"""Site de paiement Triple Elite VIP : choix de l'offre, paiement PayDunya, remise de la licence.

Lancement : `gunicorn paiement:app` (voir gunicorn.conf.py) ou `python paiement.py` en local.

Parcours d'un achat :
  1. /paiement  : le client choisit une offre et saisit son e-mail ;
  2. /payer     : la facture est créée chez PayDunya, la commande est enregistrée, puis redirection ;
  3. PayDunya   : le client paie (Orange Money, MTN, Moov, Wave, carte) ;
  4. /succes    : au retour, le paiement est VÉRIFIÉ auprès de PayDunya, la licence est délivrée et
                  affichée ; en parallèle, /ipn reçoit la notification de PayDunya et fait la même
                  chose, pour que la licence soit délivrée même si le client a fermé son navigateur.
Les deux chemins passent par settle() : la licence n'est délivrée qu'une seule fois par commande.
"""
import logging
import os
import re
from types import SimpleNamespace

from flask import Flask, redirect, render_template, request, url_for

import config
import license_manager
import web_common as web
from email_sender import envoyer_licence_async
from license_manager import LicenseManager, normalize_email
from paydunya import PayDunya, PayDunyaError, clean_token, is_test_token, parse_notification

log = logging.getLogger("paiement")

ROBOTS_TXT = ("User-agent: *\nAllow: /paiement\nDisallow: /payer\nDisallow: /succes\nDisallow: /ipn\n"
              "Disallow: /ipn-paydunya\n")

EMAIL_RE = re.compile(r"^[^@\s,;<>()\[\]\\\"]+@[^@\s,;<>()\[\]\\\"]+\.[^@\s,;<>()\[\]\\\"]{2,}$")

MSG_PLAN = "Choisis une offre pour continuer."
MSG_EMAIL = "Cette adresse e-mail ne semble pas valide. Vérifie-la : c'est avec elle que tu te connecteras."
MSG_ACCEPT = ("Coche la case pour confirmer que tu as lu les conditions et que tu demandes l'accès immédiat "
              "à ton abonnement.")
MSG_UNAVAILABLE = ("Le paiement est momentanément indisponible. Réessaie dans quelques minutes, "
                   f"ou écris-nous : {config.SELLER_EMAIL}")
MSG_TEST_ONLY = ("Les paiements ne sont pas encore ouverts : le site est en phase de test. "
                 f"Réessaie bientôt, ou écris-nous : {config.SELLER_EMAIL}")


def _build_plans():
    """Les offres, dans l'ordre où elles s'affichent. `days` est la durée réellement accordée ; `months` est la même
    durée arrondie en mois entiers, gardée dans la colonne « duree » pour les versions du code qui ne lisent pas encore
    `jours` (un pass de 7 jours y compte pour un mois : mieux vaut un client un peu mieux servi que lésé)."""
    days = license_manager.duration_days
    prices = web.price_labels()
    plans = {
        "weekly": {"key": "weekly", "label": "Pass 7 jours", "title": "Pass 7 jours", "months": 1, "days": 7,
                   "note": "Pour tester le service"},
        "monthly": {"key": "monthly", "label": "Mensuel", "title": "Abonnement mensuel", "months": 1, "days": days(1),
                    "note": ""},
        "yearly": {"key": "yearly", "label": "Annuel", "title": "Abonnement annuel", "months": 12, "days": days(12),
                   "note": f"soit {prices['yearly']['per_month']} par mois"},
    }
    for key, plan in plans.items():
        price = prices[key]
        plan["fcfa"] = price["value"]                 # le montant transmis à PayDunya : toujours celui de config.py
        plan["fcfa_label"] = price["fcfa"]
        plan["amount_label"] = price["amount"]
        plan["eur_label"] = price["eur"]
        plan["access"] = f"{plan['days']}{web.NBSP}jours d’accès"
    return plans


PLANS = _build_plans()
DEFAULT_PLAN = "monthly"


def short(token):
    """Début d'un jeton de facture, pour les journaux : jamais le jeton entier, qui ouvre la page de la commande
    (donc la clé de licence)."""
    keep = max(1, min(6, len(token) // 2))
    return f"{token[:keep]}…"


def valid_email(email):
    return bool(email) and len(email) <= 254 and bool(EMAIL_RE.match(email))


def create_app(lm=None, paydunya=None, send_license=None, test_emails=None):
    """Fabrique de l'application. Les paramètres servent aux tests (faux services).
    test_emails : adresses autorisées à commander en mode test (par défaut PAYDUNYA_TEST_EMAILS)."""
    web.configure_logging()
    app = Flask(__name__)

    # Pas de cookie ni de session ici ; la page de paiement redirige vers PayDunya (pas de form-action restreint)
    # et /ipn est appelée par les serveurs de PayDunya, pas par un navigateur (pas de contrôle d'origine).
    # Referrer-Policy « same-origin » et surtout pas « no-referrer » : l'adresse de /succes contient le jeton
    # de la commande et ne doit jamais partir vers un autre site, mais avec « no-referrer » les navigateurs
    # envoient « Origin: null » sur nos propres formulaires, que le contrôle anti-CSRF refuse à juste titre.
    web.install_security(app, csp=web.PAYMENT_CSP, referrer_policy="same-origin",
                         origin_exempt=("/ipn", "/ipn-paydunya"))
    web.install_templating(app, home_url=config.SITE_URL, login_url=f"{config.SITE_URL}/login",
                           conditions_url=f"{config.SITE_URL}/conditions")
    web.register_error_pages(app)

    lm = lm or LicenseManager()
    paydunya = paydunya or PayDunya()
    send_license = send_license or envoyer_licence_async
    if test_emails is None:
        allowed_test_emails = config.PAYDUNYA_TEST_EMAILS
    else:
        allowed_test_emails = frozenset(e for e in (normalize_email(x) for x in test_emails) if e)

    def paydunya_state():
        """live | test | absent : visible dans /health pour que la surveillance voie un mode inattendu."""
        if not paydunya.configured:
            return "absent"
        return "test" if paydunya.test_mode else "live"

    web.install_health_and_robots(app, robots_txt=ROBOTS_TXT, health_extra=lambda: {"paydunya": paydunya_state()})

    limits = {
        "email": web.RateLimiter(config.PAYMENT_MAX_PER_EMAIL, config.PAYMENT_WINDOW_SECONDS),
        "ip": web.RateLimiter(config.PAYMENT_MAX_PER_IP, config.PAYMENT_WINDOW_SECONDS),
        "global": web.RateLimiter(config.PAYMENT_MAX_GLOBAL, config.PAYMENT_WINDOW_SECONDS),
    }
    app.extensions["tev"] = SimpleNamespace(lm=lm, paydunya=paydunya, limits=limits, send_license=send_license)

    if not paydunya.configured:
        log.error("clés PayDunya absentes (%s) : aucun paiement ne sera possible",
                  getattr(paydunya, "expected_variables", "PAYDUNYA_MASTER_KEY, PAYDUNYA_PRIVATE_KEY, PAYDUNYA_TOKEN"))
    elif paydunya.test_mode:
        log.warning("MODE TEST PayDunya : paiements fictifs, réservés à %d adresse(s) de test (PAYDUNYA_TEST_EMAILS)",
                    len(allowed_test_emails))
    log.info("Triple Elite VIP %s : site de paiement, retour %s", config.VERSION, config.PAIEMENT_URL)

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    def render_pay(selected=DEFAULT_PLAN, email="", error=None, notice=None, status=200, accepted=False):
        page = render_template("paiement.html", plans=list(PLANS.values()), selected=selected,
                               selected_plan=PLANS[selected], email=email, error=error, notice=notice,
                               accepted=accepted, test_mode=paydunya.configured and paydunya.test_mode)
        return page, status

    def render_result(state, status=200, **context):
        return render_template("succes.html", state=state, **context), status

    @app.get("/")
    def accueil():
        return redirect(url_for("paiement"))

    @app.get("/paiement")
    def paiement():
        selected = request.args.get("plan")
        selected = selected if selected in PLANS else DEFAULT_PLAN
        email = normalize_email(request.args.get("email"))[:254]
        notice = None
        if "annule" in request.args:
            notice = "Paiement annulé. Tu peux choisir une offre et réessayer quand tu veux."
        return render_pay(selected, email if valid_email(email) else "", notice=notice)

    # ------------------------------------------------------------------
    # Création de la facture
    # ------------------------------------------------------------------

    def wait_time(email, ip):
        return max(limits["email"].retry_after(email), limits["ip"].retry_after(ip),
                   limits["global"].retry_after("*"))

    @app.post("/payer")
    def payer():
        plan_key = request.form.get("plan")
        email = normalize_email(request.form.get("email"))[:254]
        selected = plan_key if plan_key in PLANS else DEFAULT_PLAN
        # Case des conditions : le client demande l'accès immédiat et renonce à se rétracter une fois la clé
        # remise. Le navigateur la rend obligatoire (« required »), le serveur la vérifie quand même.
        accepted = request.form.get("accept") == "1"
        if plan_key not in PLANS:
            return render_pay(selected, email, error=MSG_PLAN, accepted=accepted, status=400)
        if not valid_email(email):
            return render_pay(selected, email, error=MSG_EMAIL, accepted=accepted, status=400)
        if not accepted:
            return render_pay(selected, email, error=MSG_ACCEPT, status=400)
        if not paydunya.configured:
            log.error("paiement impossible : clés PayDunya absentes")
            return render_pay(selected, email, error=MSG_UNAVAILABLE, accepted=True, status=503)
        # Clés de test : le paiement est fictif. Seules les adresses de test peuvent commander, sinon n'importe
        # quel visiteur obtiendrait une vraie licence sans rien payer.
        if paydunya.test_mode and email not in allowed_test_emails:
            log.warning("mode test : adresse non autorisée, aucune facture (%s)", web.mask_email(email))
            return render_pay(selected, email, error=MSG_TEST_ONLY, accepted=True, status=503)

        ip = request.remote_addr or "?"
        wait = wait_time(email, ip)
        if wait:
            log.warning("paiement bloqué (trop de tentatives) : %s", web.mask_email(email))
            minutes = max(1, -(-wait // 60))
            return render_pay(selected, email, status=429, accepted=True,
                              error=f"Trop de tentatives. Réessaie dans {minutes} minute{'s' if minutes > 1 else ''}.")
        for name, key in (("email", email), ("ip", ip), ("global", "*")):
            limits[name].hit(key)

        plan = PLANS[plan_key]
        base = config.PAIEMENT_URL
        try:
            invoice = paydunya.create_invoice(
                name=f"{config.PRODUCT_NAME} - {plan['label']}",
                amount=plan["fcfa"],
                description="Pronostics de football : trois combinés par génération",
                return_url=f"{base}/succes",
                cancel_url=f"{base}/paiement?annule=1&plan={plan_key}",
                callback_url=f"{base}/ipn",
                custom_data={"plan": plan_key, "email": email},
            )
        except PayDunyaError as exc:
            log.error("facture PayDunya impossible (%s) : %s", web.mask_email(email), exc)
            return render_pay(selected, email, error=MSG_UNAVAILABLE, accepted=True, status=503)

        # Même règle si PayDunya répond par une facture de test alors que le site ne se croyait pas en mode test :
        # on n'enregistre pas la commande et on n'envoie personne « payer » pour de faux.
        if is_test_token(invoice["token"]) and email not in allowed_test_emails:
            log.error("facture de TEST pour une adresse non autorisée (%s) : refusée, vérifie les clés PayDunya",
                      web.mask_email(email))
            return render_pay(selected, email, error=MSG_TEST_ONLY, accepted=True, status=503)

        # La commande est enregistrée AVANT d'envoyer le client payer : sans elle, un paiement
        # ne pourrait pas être rattaché à un e-mail. Si l'enregistrement échoue, on s'arrête là.
        if not lm.create_pending_order(invoice["token"], email, plan["label"], plan["months"], plan["days"]):
            log.error("commande non enregistrée (%s) : le client n'est pas envoyé payer", web.mask_email(email))
            return render_pay(selected, email, error=MSG_UNAVAILABLE, accepted=True, status=503)

        log.info("facture créée : %s, %s, %s FCFA, %s, conditions acceptées", plan["label"], web.mask_email(email),
                 plan["fcfa"], short(invoice["token"]))
        return redirect(invoice["url"], code=303)

    # ------------------------------------------------------------------
    # Vérification du paiement et remise de la licence
    # ------------------------------------------------------------------

    def expiry_of(email):
        return lm.get_status(email).get("expires")

    def finished(order):
        """Commande déjà traitée : licence affichable, ou en cours de préparation par une autre requête."""
        if not order["license_key"]:
            return {"state": "preparing", "order": order}
        return {"state": "done", "order": order, "key": order["license_key"],
                "renewed": bool(order["renewed"]), "expires": expiry_of(order["email"])}

    def deliver(token, order):
        """Le paiement est confirmé : réserve la commande, émet la licence, l'enregistre, l'envoie."""
        try:
            claimed = lm.claim_order(token)
        except Exception:
            log.exception("réservation de la commande impossible : %s", short(token))
            return {"state": "unavailable", "order": order}
        if claimed is None:
            # Le retour du navigateur et la notification PayDunya arrivent presque ensemble :
            # l'autre requête a gagné, on regarde où elle en est.
            try:
                current = lm.get_pending_order(token)
            except Exception:
                return {"state": "unavailable", "order": order}
            return finished(current) if current else {"state": "unknown"}
        try:
            # « jours » vaut None pour les commandes enregistrées avant le pass de 7 jours : elles se lisent en mois.
            info = lm.issue_license(claimed["email"], claimed["duree"], days=claimed.get("jours"))
        except Exception:
            log.exception("PAIEMENT CONFIRMÉ MAIS LICENCE NON DÉLIVRÉE (%s, %s) : nouvelle tentative au prochain "
                          "passage du client ou de PayDunya", web.mask_email(claimed["email"]), short(token))
            lm.release_order(token)
            return {"state": "unavailable", "order": order}
        if not lm.complete_order(token, info["key"], info["renewed"]):
            log.error("licence délivrée mais clé non rattachée à la commande %s : elle ne pourra pas être réaffichée "
                      "(le client la reçoit par e-mail)", short(token))
        log.info("paiement confirmé : %s, %s, licence %s jusqu'au %s", web.mask_email(claimed["email"]),
                 claimed["plan"], "renouvelée" if info["renewed"] else "créée", info["expires"])
        try:
            send_license(claimed["email"], info["key"], claimed["plan"], info["expires"], info["renewed"])
        except Exception:
            log.exception("e-mail de licence non lancé (%s)", web.mask_email(claimed["email"]))
        return {"state": "done", "order": {**order, "email": claimed["email"]}, "key": info["key"],
                "renewed": info["renewed"], "expires": info["expires"]}

    def settle(token):
        """Vérifie le paiement auprès de PayDunya et délivre la licence UNE seule fois, quel que soit le
        nombre de passages (retour du client, actualisations, notification IPN). Renvoie {"state": ...} :
        done | preparing | pending | failed | unavailable | unknown."""
        try:
            order = lm.get_pending_order(token)
        except Exception:
            log.exception("commande illisible (base injoignable) : %s", short(token))
            return {"state": "unavailable"}
        if order is None:
            return {"state": "unknown"}
        if order["processed"]:
            return finished(order)
        try:
            invoice = paydunya.confirm(token)
        except PayDunyaError as exc:
            log.warning("vérification PayDunya impossible (%s) : %s", short(token), exc)
            return {"state": "unavailable", "order": order}
        if invoice["status"] == "pending":
            return {"state": "pending", "order": order}
        if invoice["status"] != "completed":
            log.info("paiement non abouti (%s) : %s", short(token), invoice["status"])
            return {"state": "failed", "order": order, "status": invoice["status"]}
        return deliver(token, order)

    @app.get("/succes")
    def succes():
        raw = request.args.get("token", "")
        if not raw.strip():
            return render_result("sans_reference", reference=None)
        token = clean_token(raw)
        if not token:
            return render_result("inconnu", status=404, reference=None)
        try:
            attempt = max(0, min(int(request.args.get("n", "0")), 1000))
        except ValueError:
            attempt = 0

        outcome = settle(token)
        state = outcome["state"]
        order = outcome.get("order") or {}
        context = {"reference": token, "email": order.get("email", ""),
                   "email_masked": web.mask_email(order.get("email", "")),
                   "refresh_url": None, "refresh_seconds": config.SUCCESS_REFRESH_SECONDS, "gave_up": False,
                   "retry_url": url_for("succes", token=token)}
        if state == "done":
            expires = outcome.get("expires")
            key = outcome["key"]
            context.update(key=key, key_groups=[key[i:i + 4] for i in range(0, len(key), 4)],
                           renewed=outcome["renewed"],
                           expires_label=expires.strftime("%d/%m/%Y") if expires else None)
            return render_result("done", **context)
        if state == "unknown":
            return render_result("inconnu", status=404, **context)
        if state == "failed":
            return render_result("failed", **context)
        # En attente (pending, preparing) ou vérification momentanément impossible : la page se recharge
        # toute seule un moment, puis laisse la main au client.
        if attempt < config.SUCCESS_REFRESH_MAX:
            context["refresh_url"] = url_for("succes", token=token, n=attempt + 1)
        else:
            context["gave_up"] = True
        status = 503 if state == "unavailable" else 200
        return render_result(state, status=status, **context)

    # « /ipn-paydunya » : l'adresse enregistrée dans l'application PayDunya (celle de /ipn est envoyée avec
    # chaque facture et prime, mais les deux marchent).
    @app.post("/ipn")
    @app.post("/ipn-paydunya")
    def ipn():
        """Notification de PayDunya à chaque paiement. Son contenu n'est jamais cru : on en extrait
        seulement le jeton de la facture, puis on interroge PayDunya (settle)."""
        token, digest = parse_notification(request.form, request.get_json(silent=True))
        if not token:
            log.info("notification PayDunya sans jeton exploitable")
            return "ignored", 200
        if digest and not paydunya.hash_is_valid(digest):
            log.warning("notification PayDunya : empreinte inattendue (%s), la vérification auprès de PayDunya décide",
                        short(token))
        state = settle(token)["state"]
        log.info("notification PayDunya %s : %s", short(token), state)
        if state == "unavailable":
            return "retry later", 503
        return "ok", 200

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
