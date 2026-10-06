"""Éléments communs aux deux sites Flask (tableau de bord et paiement).

Journalisation, en-têtes de sécurité (CSP stricte, cookies), protection des formulaires,
limitation des essais, cache court des vérifications de licence, pages d'erreur.
"""
import hashlib
import logging
import os
import re
import sys
import threading
import time
from datetime import timedelta
from urllib.parse import urlparse

from flask import jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

import config
import license_manager
from privacy import mask_email  # noqa: F401  (réexporté : web.mask_email)

log = logging.getLogger(__name__)

NBSP = "\u00a0"


# --------------------------------------------------------------------------
# Journalisation
# --------------------------------------------------------------------------

def configure_logging():
    """Journaux lisibles dans les « Logs » de Render (sortie standard)."""
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# --------------------------------------------------------------------------
# Typographie et formats français
# --------------------------------------------------------------------------

_APOSTROPHE = re.compile(r"([A-Za-zÀ-ÿ])'(?=[A-Za-zÀ-ÿ])")


def typo(text):
    """Apostrophes typographiques (l'équipe -> l’équipe) dans les messages affichés."""
    return _APOSTROPHE.sub("\\1\u2019", "" if text is None else str(text))


def format_eur(amount):
    amount = float(amount)
    text = f"{amount:.0f}" if amount == int(amount) else f"{amount:.2f}".replace(".", ",")
    return f"{text}{NBSP}€"


def format_fcfa(amount):
    return f"{int(amount):,}".replace(",", NBSP) + f"{NBSP}FCFA"


def describe_remaining(expires, now=None):
    """('dans 23 jours', 23.4) : temps restant d'un abonnement, en français courant."""
    now = now or license_manager._utcnow()
    days = max(0.0, (expires - now).total_seconds() / 86400)
    whole = int(days)
    if whole < 1:
        label = "dans moins de 24" + NBSP + "heures"
    elif whole == 1:
        label = "dans 1" + NBSP + "jour"
    else:
        label = f"dans {whole}{NBSP}jours"
    return label, days


# --------------------------------------------------------------------------
# Limitation d'essais (en mémoire, par processus)
# --------------------------------------------------------------------------

class RateLimiter:
    """Compte des événements par clé sur une fenêtre glissante."""

    def __init__(self, max_events, window_seconds, clock=time.time, max_keys=20000):
        self.max_events = max_events
        self.window = window_seconds
        self.clock = clock
        self.max_keys = max_keys
        self._events = {}
        self._lock = threading.Lock()

    def _recent(self, key, now):
        events = [t for t in self._events.get(key, ()) if now - t < self.window]
        if events:
            self._events[key] = events
        else:
            self._events.pop(key, None)
        return events

    def hit(self, key):
        now = self.clock()
        with self._lock:
            if len(self._events) >= self.max_keys:           # mémoire bornée
                for k in list(self._events):
                    self._recent(k, now)
                if len(self._events) >= self.max_keys:
                    self._events.clear()
            events = self._recent(key, now)
            events.append(now)
            self._events[key] = events

    def count(self, key):
        with self._lock:
            return len(self._recent(key, self.clock()))

    def retry_after(self, key):
        """0 si la clé peut agir, sinon le nombre de secondes à attendre."""
        now = self.clock()
        with self._lock:
            events = self._recent(key, now)
            if len(events) < self.max_events:
                return 0
            return max(1, int(self.window - (now - events[0])) + 1)

    def reset(self, key):
        with self._lock:
            self._events.pop(key, None)


# --------------------------------------------------------------------------
# Vérification de licence à chaque requête, avec cache court
# --------------------------------------------------------------------------

class LicenseGate:
    """Revérifie la licence en base à chaque requête protégée, sans ouvrir une connexion
    à la base pour CHAQUE appel : le verdict est gardé LICENSE_CACHE_SECONDS. Si la base
    est momentanément injoignable, un client dont la licence vient d'être vérifiée
    « active » garde l'accès LICENSE_GRACE_SECONDS plutôt que d'être éjecté."""

    def __init__(self, manager, clock=time.time, ttl=None, grace=None):
        self.manager = manager
        self.clock = clock
        self.ttl = config.LICENSE_CACHE_SECONDS if ttl is None else ttl
        self.grace = config.LICENSE_GRACE_SECONDS if grace is None else grace
        self._cache = {}
        self._lock = threading.Lock()

    @staticmethod
    def _still_valid(status):
        return status["state"] == "active" and status["expires"] is not None \
            and status["expires"] > license_manager._utcnow()

    def status(self, email):
        key = license_manager.normalize_email(email)
        if not key:
            return {"state": "unknown", "expires": None}
        now = self.clock()
        with self._lock:
            cached = self._cache.get(key)
        if cached and now - cached[0] < self.ttl:
            if self._still_valid(cached[1]) or cached[1]["state"] != "active":
                return cached[1]
        status = self.manager.get_status(email)
        if status["state"] == "error":
            if cached and self._still_valid(cached[1]) and now - cached[0] < self.grace:
                return cached[1]
            return status
        with self._lock:
            if len(self._cache) > 5000:
                self._cache.clear()
            self._cache[key] = (now, status)
        return status

    def forget(self, email):
        with self._lock:
            self._cache.pop(license_manager.normalize_email(email), None)


# --------------------------------------------------------------------------
# Sécurité HTTP
# --------------------------------------------------------------------------

DASHBOARD_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                 "font-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; "
                 "frame-ancestors 'none'; object-src 'none'")

# Le formulaire de paiement redirige vers PayDunya : pas de restriction form-action ici
# (Chrome l'applique aussi aux redirections qui suivent l'envoi du formulaire).
PAYMENT_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
               "font-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'")


def _host_of(url):
    return urlparse(url).netloc.lower()


def _allowed_hosts():
    hosts = {request.host.lower(), _host_of(config.SITE_URL), _host_of(config.PAIEMENT_URL)}
    forwarded = request.headers.get("X-Forwarded-Host", "").split(",")[0].strip().lower()
    if forwarded:
        hosts.add(forwarded)
    hosts.discard("")
    return hosts


def origin_is_allowed():
    """Les requêtes qui modifient quelque chose doivent venir de NOS pages (anti-CSRF),
    en plus du cookie SameSite=Lax. Sans en-tête Origin ni Referer, on ne peut rien dire."""
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source:
        return True
    if source == "null":
        return False
    return _host_of(source) in _allowed_hosts()


def wants_json():
    return request.path.startswith("/api/") or request.headers.get("X-Requested-With") == "fetch"


def install_security(app, *, csp, referrer_policy="strict-origin-when-cross-origin", origin_exempt=()):
    """En-têtes de sécurité, cookies et contrôle d'origine des requêtes qui modifient quelque chose.
    origin_exempt : chemins appelés par un serveur et non par un navigateur (ex. /ipn de PayDunya)."""
    secure_cookies = bool(os.environ.get("RENDER")) or os.environ.get("COOKIE_SECURE") == "1"
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=secure_cookies,
        PERMANENT_SESSION_LIFETIME=timedelta(days=config.SESSION_DAYS),
        SEND_FILE_MAX_AGE_DEFAULT=timedelta(days=7),
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    app.json.sort_keys = False
    # Derrière le proxy de Render : adresse du client et protocole (https) réels.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    @app.before_request
    def _refuse_foreign_posts():
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path not in origin_exempt \
                and not origin_is_allowed():
            log.warning("requête refusée (origine inattendue) : %s %s", request.method, request.path)
            return error_response(403, "Requête refusée", "Cette requête ne provient pas de notre site.")
        return None

    @app.after_request
    def _security_headers(response):
        headers = response.headers
        headers.setdefault("Content-Security-Policy", csp)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", referrer_policy)
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        if request.is_secure:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if not request.path.startswith("/static/"):
            headers.setdefault("Cache-Control", "no-store")
        return response


# --------------------------------------------------------------------------
# Gabarits : adresses, prix, fichiers statiques versionnés
# --------------------------------------------------------------------------

def install_templating(app, *, home_url, login_url, conditions_url):
    versions = {}

    def asset(path):
        """Adresse d'un fichier statique avec une empreinte : le navigateur recharge le
        fichier dès qu'il change et le garde en cache sinon."""
        version = None if app.debug else versions.get(path)
        if version is None:
            try:
                with open(os.path.join(app.static_folder, path), "rb") as f:
                    version = hashlib.md5(f.read()).hexdigest()[:10]
            except OSError:
                version = config.VERSION
            versions[path] = version
        return url_for("static", filename=path, v=version)

    app.jinja_env.globals["asset"] = asset
    app.jinja_env.filters["typo"] = typo
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True

    per_month = config.PRICE_YEARLY / 12

    @app.context_processor
    def _shared_context():
        return {
            "home_url": home_url,
            "login_url": login_url,
            "conditions_url": conditions_url,
            "site_url": config.SITE_URL,
            "paiement_url": config.PAIEMENT_URL,
            "seller_email": config.SELLER_EMAIL,
            "product_name": config.PRODUCT_NAME,
            "price_monthly": format_eur(config.PRICE_MONTHLY),
            "price_yearly": format_eur(config.PRICE_YEARLY),
            "price_monthly_num": f"{config.PRICE_MONTHLY:g}".replace(".", ","),
            "price_yearly_num": f"{config.PRICE_YEARLY:g}".replace(".", ","),
            "price_yearly_per_month": format_eur(round(per_month, 2)),
            "fcfa_monthly": format_fcfa(config.PRICE_MONTHLY_FACTURE_FCFA),
            "fcfa_yearly": format_fcfa(config.PRICE_YEARLY_FACTURE_FCFA),
            "target_odds": f"{config.TARGET_ODDS:.2f}".replace(".", ","),
            "min_confidence": config.MIN_CONFIDENCE,
            "cache_minutes": config.CACHE_MINUTES,
            "cache_hours": f"{config.CACHE_MINUTES / 60:g}".replace(".", ","),
        }


# --------------------------------------------------------------------------
# Pages et réponses d'erreur
# --------------------------------------------------------------------------

ERRORS = {
    400: ("Requête invalide", "Nous n’avons pas compris cette demande. Recharge la page et réessaie."),
    403: ("Accès refusé", "Cette action n’est pas autorisée."),
    404: ("Page introuvable", "Cette page n’existe pas ou a été déplacée."),
    405: ("Action non autorisée", "Cette page n’accepte pas cette action."),
    413: ("Demande trop volumineuse", "Les données envoyées sont trop volumineuses."),
    429: ("Trop de demandes", "Patiente un instant avant de réessayer."),
    500: ("Une erreur est survenue", "Réessaie dans un instant. Si le problème continue, écris-nous."),
    503: ("Service momentanément indisponible", "Réessaie dans un instant."),
}


def error_response(code, title=None, message=None):
    default_title, default_message = ERRORS.get(code) or (ERRORS[400] if code < 500 else ERRORS[500])
    title = title or default_title
    message = message or default_message
    if wants_json():
        return jsonify({"error": message, "title": title}), code
    return render_template("erreur.html", code=code, title=title, message=message), code


def register_error_pages(app):
    @app.errorhandler(HTTPException)
    def _http_error(error):
        code = error.code or 500
        if code == 404:
            log.debug("404 %s", request.path)             # robots et scanners : sans intérêt
        return error_response(code)

    @app.errorhandler(Exception)
    def _unexpected_error(error):
        log.exception("erreur inattendue sur %s %s", request.method, request.path)
        return error_response(500)


def install_health_and_robots(app, *, robots_txt):
    @app.get("/health")
    def health():
        response = jsonify({"status": "ok", "version": config.VERSION})
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/robots.txt")
    def robots():
        return app.response_class(robots_txt, mimetype="text/plain")

    @app.get("/favicon.ico")
    def favicon():
        return redirect(url_for("static", filename="favicon.svg"), code=301)
