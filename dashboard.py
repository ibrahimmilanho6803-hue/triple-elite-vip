"""Site client Triple Elite VIP : page d'accueil, connexion, espace abonné.

Lancement : `gunicorn dashboard:app` (voir gunicorn.conf.py) ou `python dashboard.py` en local.
"""
import logging
import os
import secrets
from functools import wraps
from types import SimpleNamespace
from urllib.parse import quote
from xml.sax.saxutils import escape

from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, session, url_for

import combo_history
import config
import pwa
import web_common as web
from daily_generation import DailyGeneration
from generation_service import GenerationService
from license_manager import LicenseManager, normalize_email
from showcase import Showcase, share_links

log = logging.getLogger("dashboard")


def build_robots():
    """Tout est public sauf l'espace client, l'API et la connexion ; le plan du site aide les moteurs de recherche."""
    return ("User-agent: *\nAllow: /\nDisallow: /app\nDisallow: /api/\nDisallow: /login\n"
            f"Sitemap: {config.SITE_URL}/sitemap.xml\n")


# Raisons de retour à la page de connexion (?raison=...) : message affiché, lien de renouvellement.
LOGIN_REASONS = {
    "session": ("Ta session a expiré. Reconnecte-toi pour continuer.", False),
    "expire": ("Ton abonnement a expiré. Renouvelle-le pour retrouver l'accès.", True),
    "desactive": (f"Ta licence a été désactivée. Contacte-nous : {config.SELLER_EMAIL}", False),
    "deconnecte": ("Tu es déconnecté. À bientôt !", False),
}

MSG_UNAVAILABLE = "Service momentanément indisponible. Réessaie dans un instant."
MSG_RESULTS_UNAVAILABLE = "Les résultats sont momentanément indisponibles. Réessaie dans quelques minutes."
MSG_HISTORY_ERROR = "Impossible de charger l'historique pour le moment. Réessaie dans un instant."


def quota_message(limit):
    """Ce que voit un client qui a utilisé toutes ses générations du jour."""
    if limit == 1:
        return "Tu as utilisé ta génération d'aujourd'hui. Reviens demain pour en lancer une nouvelle."
    return f"Tu as utilisé tes {limit} générations d'aujourd'hui. Reviens demain pour en lancer de nouvelles."


def quota_view(used, limit):
    """Les générations d'un client aujourd'hui, telles que le navigateur les reçoit : {"limit", "used", "left"}."""
    used = max(0, int(used))
    return {"limit": limit, "used": used, "left": max(0, limit - used)}


def quota_state(used, limit):
    """Réponse « plus de génération aujourd'hui » : le navigateur affiche le message à la place du bouton."""
    return {"state": "quota", "message": quota_message(limit), "quota": quota_view(used, limit)}


def renew_link(email=None):
    """Adresse de la page de paiement ; l'e-mail du client y est prérempli pour qu'un renouvellement
    ne parte pas, par faute de frappe, sur une autre adresse (donc une autre licence)."""
    url = f"{config.PAIEMENT_URL}/paiement"
    return f"{url}?email={quote(email, safe='')}" if email else url


def create_app(lm=None, service=None, history_loader=None, showcase=None, daily=None):
    """Fabrique de l'application. Les paramètres servent aux tests (faux services). `showcase` : pages publiques
    (showcase.py) ; sans lui, elles lisent le même historique que history_loader. `daily` : génération automatique
    quotidienne (daily_generation.py), créée seulement si AUTO_GENERATE_HOUR est définie."""
    web.configure_logging()
    app = Flask(__name__)

    secret = os.environ.get("SECRET_KEY")
    if not secret:
        log.warning("SECRET_KEY absente : clé aléatoire, les sessions seront perdues à chaque redémarrage")
    app.secret_key = secret or secrets.token_hex(32)

    web.install_security(app, csp=web.DASHBOARD_CSP)
    web.install_templating(app, home_url="/", login_url="/login", conditions_url="/conditions", pwa=True)
    web.register_error_pages(app)
    web.install_health_and_robots(app, robots_txt=build_robots())
    pwa.install_pwa(app)

    lm = lm or LicenseManager()
    gate = web.LicenseGate(lm)
    service = service or GenerationService()
    load_history = history_loader or (lambda: combo_history.load_history(config.RESULTS_DIR))
    throttle = web.RateLimiter(config.LOGIN_MAX_ATTEMPTS, config.LOGIN_WINDOW_SECONDS)
    if showcase is None:
        showcase = Showcase(service, loader=(lambda refresh: history_loader()) if history_loader else None)
    if daily is None and config.AUTO_GENERATE_HOUR is not None:
        daily = DailyGeneration(service, config.AUTO_GENERATE_HOUR)
    if daily is not None:
        daily.start()
    app.extensions["tev"] = SimpleNamespace(lm=lm, gate=gate, service=service, throttle=throttle, showcase=showcase,
                                            daily=daily)

    log.info("Triple Elite VIP %s : données dans %s, IA %s, clé Anthropic %s, clé cotes %s",
             config.VERSION, os.path.abspath(config.DATA_DIR), config.IA_MODEL,
             "présente" if os.environ.get("ANTHROPIC_API_KEY") else "ABSENTE",
             "présente" if os.environ.get("ODDS_API_KEY") else "absente")

    # ------------------------------------------------------------------
    # Accès protégé
    # ------------------------------------------------------------------

    def deny(reason):
        """Accès refusé : JSON pour les appels de l'interface, redirection pour les pages."""
        message = LOGIN_REASONS.get(reason, LOGIN_REASONS["session"])[0]
        if web.wants_json():
            return jsonify({"error": message, "raison": reason}), 401
        return redirect(url_for("login", raison=reason))

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            email = session.get("email")
            if not session.get("authenticated") or not email:
                return deny("session")
            # La licence est revérifiée à CHAQUE requête (verdict gardé quelques secondes) :
            # l'accès est coupé dès qu'elle expire ou est désactivée, même avec un cookie valide.
            status = gate.status(email)
            if status["state"] == "active":
                g.license = status
                return view(*args, **kwargs)
            if status["state"] == "error":
                return web.error_response(503, message=MSG_UNAVAILABLE)
            session.clear()
            if status["state"] == "expired":
                # Seul but : préremplir le lien « Renouveler » de la page de connexion (pas de licence créée par erreur
                # sur une adresse mal saisie). Effacé à la prochaine connexion ou déconnexion.
                session["renew_email"] = email
            return deny({"expired": "expire", "inactive": "desactive"}.get(status["state"], "session"))
        return wrapped

    def ajax_only(view):
        """Les actions de l'interface portent un en-tête que les formulaires d'autres sites ne
        peuvent pas ajouter (protection supplémentaire contre les requêtes forgées)."""
        @wraps(view)
        def wrapped(*args, **kwargs):
            if request.headers.get("X-Requested-With") != "fetch":
                return jsonify({"error": "Requête invalide."}), 400
            return view(*args, **kwargs)
        return wrapped

    # ------------------------------------------------------------------
    # Pages publiques
    # ------------------------------------------------------------------

    def public_results():
        """Bilan public, ou None s'il est indisponible : une panne de l'historique ne doit jamais casser les pages publiques."""
        try:
            return showcase.results()
        except Exception:
            log.exception("résultats publics indisponibles")
            return None

    def kept_briefly(page):
        """Page publique réussie : la même pour tout le monde (aucun cookie, aucune donnée du visiteur), donc le navigateur peut
        la réutiliser PUBLIC_PAGE_CACHE_SECONDS secondes. « private » : jamais gardée par un intermédiaire. Les barres de bouton
        la préchargent (static/js/tabbar.js) : un bouton touché dans la minute s'ouvre sans attendre le réseau. Toute autre
        page, et toute erreur, reste « no-store » (web_common.install_security)."""
        response = app.make_response(page)
        seconds = config.PUBLIC_PAGE_CACHE_SECONDS
        if seconds > 0 and response.status_code == 200:
            response.headers["Cache-Control"] = f"private, max-age={seconds}"
        return response

    @app.get("/")
    def accueil():
        return kept_briefly(render_template("accueil.html", proof=public_results()))

    @app.get("/resultats")
    def resultats():
        results = public_results()
        if results is None:
            response = app.make_response(web.error_response(503, message=MSG_RESULTS_UNAVAILABLE))
            response.headers["Retry-After"] = "300"
            return response
        return kept_briefly(render_template("resultats.html", r=results))

    @app.get("/gratuit")
    def gratuit():
        if not config.FREE_PICK_ENABLED:
            abort(404)
        try:
            pick = showcase.free_pick()
        except Exception:
            log.exception("combiné gratuit indisponible")
            pick = None
        links = share_links(pick, f"{config.SITE_URL}/gratuit") if pick else None
        # Sans combiné : pas de championnat aujourd'hui (ou trop peu de matchs) ou simplement pas encore d'analyse.
        no_match = None if pick else showcase.no_match_today()
        return kept_briefly(render_template("gratuit.html", pick=pick, share=links, no_match=no_match))

    @app.get("/sitemap.xml")
    def sitemap():
        paths = ["/", "/resultats"] + (["/gratuit"] if config.FREE_PICK_ENABLED else []) + ["/conditions"]
        urls = "".join(f"<url><loc>{escape(config.SITE_URL + path)}</loc></url>" for path in paths)
        body = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"{urls}</urlset>\n")
        return Response(body, mimetype="application/xml")

    @app.get("/conditions")
    def conditions():
        return kept_briefly(render_template("conditions.html"))

    @app.get(pwa.START_URL)
    def lancement():
        """Ouverture de l'application installée (manifeste, APK Android). Un client connecté arrive dans son espace,
        un visiteur sur l'accueil. Un abonnement expiré ou désactivé n'est pas jugé ici : l'espace le fait et renvoie
        vers la connexion avec son message et le lien « Renouveler »."""
        if session.get("authenticated") and session.get("email"):
            return redirect(url_for("espace"))
        return redirect(url_for("accueil"))

    # ------------------------------------------------------------------
    # Connexion / déconnexion
    # ------------------------------------------------------------------

    def render_login(email="", error=None, info=None, show_renew=False, status=200):
        page = render_template("login.html", email=email, error=error, info=info, show_renew=show_renew,
                               renew_url=renew_link(email if show_renew else None))
        return page, status

    @app.get("/login")
    def login():
        email = session.get("email")
        if session.get("authenticated") and email:
            if gate.status(email)["state"] == "active":
                return redirect(url_for("espace"))
            session.clear()
        info, show_renew = LOGIN_REASONS.get(request.args.get("raison", ""), (None, False))
        return render_login(email=(session.get("renew_email") or "") if show_renew else "", info=info,
                            show_renew=show_renew)

    @app.post("/login")
    def login_post():
        email = normalize_email(request.form.get("email"))[:254]
        key = (request.form.get("license_key") or "").strip()[:64]
        if not email or not key:
            return render_login(email=email, error="Renseigne ton e-mail et ta clé de licence.", status=400)

        wait = throttle.retry_after(email)
        if wait:
            log.warning("connexion bloquée (trop d'essais) : %s", web.mask_email(email))
            minutes = max(1, -(-wait // 60))
            unit = "minute" if minutes == 1 else "minutes"
            return render_login(email=email, status=429,
                                error=f"Trop d'essais. Réessaie dans {minutes} {unit}.")

        result = lm.check_login(email, key)
        if result["ok"]:
            throttle.reset(email)
            session.clear()
            session.permanent = True
            session["authenticated"] = True
            session["email"] = email
            gate.forget(email)
            log.info("connexion : %s", web.mask_email(email))
            return redirect(url_for("espace"), code=303)

        reason = result["reason"]
        if reason == "unavailable":
            return render_login(email=email, error=result["message"], status=503)
        if reason == "invalid":
            throttle.hit(email)
            # Le motif exact (e-mail inconnu ou clé différente) reste dans les journaux : le visiteur, lui,
            # voit toujours le même message, pour ne rien révéler sur les comptes existants.
            log.info("connexion refusée : %s (%s)", web.mask_email(email), result.get("detail") or "motif non précisé")
            return render_login(email=email, error=result["message"], status=401)
        # Bonne clé, mais abonnement expiré ou désactivé : ce n'est pas une tentative d'intrusion.
        return render_login(email=email, error=result["message"], show_renew=(reason == "expired"), status=403)

    @app.route("/logout", methods=["GET", "POST"])
    def logout():
        if session.get("email"):
            log.info("déconnexion : %s", web.mask_email(session.get("email")))
        session.clear()
        return redirect(url_for("login", raison="deconnecte"), code=303)

    # ------------------------------------------------------------------
    # Espace abonné
    # ------------------------------------------------------------------

    @app.get("/app")
    @login_required
    def espace():
        expires = g.license["expires"]
        days_left, days = web.describe_remaining(expires)
        return render_template("dashboard.html", expires_label=expires.strftime("%d/%m/%Y"),
                               days_left=days_left, renew_soon=days <= 7,
                               renew_url=renew_link(session.get("email")),
                               generations_per_day=config.CLIENT_GENERATIONS_PER_DAY)

    # ------------------------------------------------------------------
    # Générations : chaque client en a CLIENT_GENERATIONS_PER_DAY par jour (jour UTC). Seul le clic qui démarre vraiment
    # une analyse en consomme une ; afficher des combinés déjà prêts, rejoindre un travail en cours, un jour sans match
    # ou une analyse qui n'aboutit pas ne coûtent rien au client.
    # ------------------------------------------------------------------

    def quota_for(email):
        """Les générations du client aujourd'hui ({"limit", "used", "left"}), ou None si le compteur est illisible : on ne
        bloque alors personne (le plafond du jour, commun à tous, borne de toute façon la dépense)."""
        try:
            used = lm.generations_used(email, service.today())
        except Exception:
            log.exception("compteur de générations illisible")
            return None
        return quota_view(used, config.CLIENT_GENERATIONS_PER_DAY)

    def with_quota(state, email):
        """Ajoute le compteur du client aux états où il sert à l'affichage (le bouton est visible)."""
        if state.get("state") in ("idle", "error"):
            quota = quota_for(email)
            if quota is not None:
                return {**state, "quota": quota}
        return state

    @app.post("/api/generate")
    @login_required
    @ajax_only
    def api_generate():
        email = session.get("email")
        who = web.mask_email(email)
        current = service.status()
        if current["state"] != "idle":
            # Combinés prêts, travail en cours, pause après un échec, pas de championnat aujourd'hui : rien à compter.
            log.info("génération demandée par %s : %s", who, current["state"])
            return jsonify(with_quota(current, email))

        day, limit = service.today(), config.CLIENT_GENERATIONS_PER_DAY
        reserved, used = False, None
        try:
            allowed, used = lm.reserve_generation(email, day, limit)
            reserved = allowed
        except Exception:
            log.exception("compteur de générations indisponible : analyse autorisée sans être comptée")
            allowed = True
        if not allowed:
            current = service.status()                # entre-temps, un autre clic a peut-être déjà tout réglé
            if current["state"] != "idle":
                return jsonify(with_quota(current, email))
            log.info("génération refusée à %s : %d sur %d utilisées aujourd'hui", who, used, limit)
            return jsonify(quota_state(used, limit))

        # `refund` : rend ce clic si l'analyse qu'il lance n'aboutit à aucun combiné (panne, pas de match aujourd'hui).
        refund = (lambda: lm.release_generation(email, day)) if reserved else None
        response = service.request_generation(refund=refund)
        if reserved and not response.get("started"):
            lm.release_generation(email, day)         # ce clic n'a rien démarré (travail déjà lancé par un autre, plafond...)
            used -= 1
        if used is not None and response.get("state") != "done":
            response["quota"] = quota_view(used, limit)
        log.info("génération demandée par %s : %s%s", who, response.get("state"), " (lancée)" if response.get("started") else "")
        return jsonify(response)

    @app.get("/api/generate/status")
    @login_required
    def api_generate_status():
        state = service.status()
        if state["state"] in ("idle", "error"):
            quota = quota_for(session.get("email"))
            if quota is not None:
                if state["state"] == "idle" and quota["left"] <= 0:
                    return jsonify(quota_state(quota["used"], quota["limit"]))
                state = {**state, "quota": quota}
        return jsonify(state)

    @app.get("/api/history")
    @login_required
    def api_history():
        try:
            return jsonify(load_history())
        except Exception:
            log.exception("historique : chargement impossible")
            return jsonify({"error": MSG_HISTORY_ERROR}), 500

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
