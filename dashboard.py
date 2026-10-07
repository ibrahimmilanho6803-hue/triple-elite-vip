"""Site client Triple Elite VIP : page d'accueil, connexion, espace abonné.

Lancement : `gunicorn dashboard:app` (voir gunicorn.conf.py) ou `python dashboard.py` en local.
"""
import logging
import os
import secrets
from functools import wraps
from types import SimpleNamespace
from urllib.parse import quote

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for

import combo_history
import config
import pwa
import web_common as web
from generation_service import GenerationService
from license_manager import LicenseManager, normalize_email

log = logging.getLogger("dashboard")

ROBOTS_TXT = "User-agent: *\nAllow: /\nDisallow: /app\nDisallow: /api/\nDisallow: /login\n"

# Raisons de retour à la page de connexion (?raison=...) : message affiché, lien de renouvellement.
LOGIN_REASONS = {
    "session": ("Ta session a expiré. Reconnecte-toi pour continuer.", False),
    "expire": ("Ton abonnement a expiré. Renouvelle-le pour retrouver l'accès.", True),
    "desactive": (f"Ta licence a été désactivée. Contacte-nous : {config.SELLER_EMAIL}", False),
    "deconnecte": ("Tu es déconnecté. À bientôt !", False),
}

MSG_UNAVAILABLE = "Service momentanément indisponible. Réessaie dans un instant."
MSG_HISTORY_ERROR = "Impossible de charger l'historique pour le moment. Réessaie dans un instant."


def renew_link(email=None):
    """Adresse de la page de paiement ; l'e-mail du client y est prérempli pour qu'un renouvellement
    ne parte pas, par faute de frappe, sur une autre adresse (donc une autre licence)."""
    url = f"{config.PAIEMENT_URL}/paiement"
    return f"{url}?email={quote(email, safe='')}" if email else url


def create_app(lm=None, service=None, history_loader=None):
    """Fabrique de l'application. Les paramètres servent aux tests (faux services)."""
    web.configure_logging()
    app = Flask(__name__)

    secret = os.environ.get("SECRET_KEY")
    if not secret:
        log.warning("SECRET_KEY absente : clé aléatoire, les sessions seront perdues à chaque redémarrage")
    app.secret_key = secret or secrets.token_hex(32)

    web.install_security(app, csp=web.DASHBOARD_CSP)
    web.install_templating(app, home_url="/", login_url="/login", conditions_url="/conditions", pwa=True)
    web.register_error_pages(app)
    web.install_health_and_robots(app, robots_txt=ROBOTS_TXT)
    pwa.install_pwa(app)

    lm = lm or LicenseManager()
    gate = web.LicenseGate(lm)
    service = service or GenerationService()
    load_history = history_loader or (lambda: combo_history.load_history(config.RESULTS_DIR))
    throttle = web.RateLimiter(config.LOGIN_MAX_ATTEMPTS, config.LOGIN_WINDOW_SECONDS)
    app.extensions["tev"] = SimpleNamespace(lm=lm, gate=gate, service=service, throttle=throttle)

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

    @app.get("/")
    def accueil():
        return render_template("accueil.html")

    @app.get("/conditions")
    def conditions():
        return render_template("conditions.html")

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
                               renew_url=renew_link(session.get("email")))

    @app.post("/api/generate")
    @login_required
    @ajax_only
    def api_generate():
        state = service.request_generation()
        log.info("génération demandée par %s : %s", web.mask_email(session.get("email")), state.get("state"))
        return jsonify(state)

    @app.get("/api/generate/status")
    @login_required
    def api_generate_status():
        return jsonify(service.status())

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
