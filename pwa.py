"""Application installable (PWA) du site client : manifeste, service worker et page « hors connexion ».

Ce que cela apporte aux clients : une icône sur l'écran d'accueil, une ouverture en plein écran sans barre d'adresse,
un bouton « Installer l'application » (Android) ou le geste à faire (iPhone), et une page claire quand le téléphone
n'a plus de connexion. C'est aussi la base obligatoire de l'application Android (APK installé à la main, ou version
Google Play) : voir README.

Le service worker NE met en cache AUCUNE page de l'espace client ni aucune réponse de l'API (ce sont des données
privées, et un téléphone peut être partagé) : seulement la page « hors connexion » et les fichiers dont elle a besoin.
"""
import hashlib
import json

from flask import Response, current_app, render_template, url_for

import config

OFFLINE_PATH = "/hors-ligne"
# Page ouverte au lancement de l'application : un client connecté est redirigé vers son espace, les autres arrivent
# sur le formulaire de connexion (et non sur « Ta session a expiré », que verrait un nouvel installateur sur /app).
START_URL = "/login"
THEME_COLOR = "#0A0F2C"            # même couleur que <meta name="theme-color"> (base.html) et que le fond du site
SHORT_NAME = "Triple Elite"

# Fichiers dont la page « hors connexion » a besoin, mis en cache à l'installation du service worker. Les polices sont
# demandées par la feuille de style sans empreinte : même adresse ici.
OFFLINE_ASSETS = ("css/site.css", "favicon.svg")
OFFLINE_FONTS = ("fonts/onest-latin.woff2", "fonts/big-shoulders-display-latin.woff2")

# (fichier, taille en pixels, usage) des icônes de l'application ; générées par scripts/make_icons.py.
ICONS = (("icon-192.png", 192, "any"), ("icon-512.png", 512, "any"), ("icon-maskable-512.png", 512, "maskable"))
APPLE_TOUCH_ICON = "icons/apple-touch-icon.png"


def _asset(path):
    """Adresse d'un fichier statique avec empreinte (même fonction que dans les gabarits)."""
    return current_app.jinja_env.globals["asset"](path)


def offline_files():
    """Adresses (relatives au site) mises en cache par le service worker."""
    return ([OFFLINE_PATH] + [_asset(path) for path in OFFLINE_ASSETS]
            + [url_for("static", filename=font) for font in OFFLINE_FONTS])


def build_manifest():
    return {
        "id": "/",
        "name": config.PRODUCT_NAME,
        "short_name": SHORT_NAME,
        "description": "Trois combinés de football par génération, avec la chance de réussite estimée de chacun.",
        "lang": "fr",
        "dir": "ltr",
        "start_url": START_URL,
        "scope": "/",
        "display": "standalone",
        "background_color": THEME_COLOR,
        "theme_color": THEME_COLOR,
        "categories": ["sports"],
        "icons": [{"src": _asset(f"icons/{name}"), "sizes": f"{size}x{size}", "type": "image/png", "purpose": purpose}
                  for name, size, purpose in ICONS],
    }


def build_asset_links():
    """Contenu de /.well-known/assetlinks.json (Digital Asset Links) : déclare que l'application Android du site, signée
    par l'une des clés de config.ANDROID_CERT_FINGERPRINTS, peut ouvrir ses pages sans barre d'adresse. Liste vide tant
    qu'aucune clé n'est enregistrée : l'application s'ouvre alors avec une barre d'adresse, sans rien casser."""
    if not config.ANDROID_CERT_FINGERPRINTS:
        return []
    return [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": config.ANDROID_PACKAGE,
            "sha256_cert_fingerprints": [fingerprint.upper() for fingerprint in config.ANDROID_CERT_FINGERPRINTS],
        },
    }]


def install_pwa(app):
    @app.get("/manifest.webmanifest")
    def manifest():
        response = Response(json.dumps(build_manifest(), ensure_ascii=False, indent=2),
                            mimetype="application/manifest+json")
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    @app.get("/.well-known/assetlinks.json")
    def asset_links():
        # Google (et Chrome) lisent ce fichier à cette adresse exacte, sans redirection ; il ne contient rien de secret.
        response = Response(json.dumps(build_asset_links(), indent=2), mimetype="application/json")
        response.headers["Cache-Control"] = "public, max-age=300"
        return response

    @app.get("/sw.js")
    def service_worker():
        files = offline_files()
        # Le nom du cache change dès qu'un fichier mis en cache change : l'ancien est supprimé à l'activation.
        cache_name = "tev-" + hashlib.md5(json.dumps(files).encode()).hexdigest()[:10]
        response = Response(render_template("sw.js", cache_name=cache_name, offline_url=OFFLINE_PATH, files=files),
                            mimetype="text/javascript")
        response.headers["Cache-Control"] = "no-cache"          # le navigateur vérifie la version à chaque fois
        return response

    @app.get(OFFLINE_PATH)
    def offline():
        return render_template("hors_ligne.html")
