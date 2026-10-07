"""Application installable (PWA) du site client : manifeste, icônes, service worker, page « hors connexion » et
invitation à installer. Le comportement dans un vrai navigateur (installation, mode hors connexion, bouton) est testé
dans tests/e2e/test_ui.py, classe TestApplication."""
import json
import re
import struct
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

import config
import dashboard
import license_manager
import pwa
from generation_service import GenerationService
from site_fakes import EMAIL, KEY, FakeLicenses, make_pipeline, sample_history

PAGES = ("/", "/login", "/conditions")          # pages publiques qui portent l'invitation et la déclaration de l'application
THEME = re.compile(r'<meta name="theme-color" content="([^"]+)">')
ASSET_LINKS = "/.well-known/assetlinks.json"
FINGERPRINT = re.compile(r"[0-9A-F]{2}(:[0-9A-F]{2}){31}")      # SHA-256 : 32 octets en hexadécimal séparés par « : »
ANDROID_PACKAGE = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")


@pytest.fixture()
def env(tmp_path):
    e = SimpleNamespace(licenses=FakeLicenses())
    service = GenerationService(pipeline=make_pipeline(delay=0.0), cache_dir=str(tmp_path / "cache"),
                                results_dir=str(tmp_path / "results"))
    e.app = dashboard.create_app(lm=e.licenses, service=service, history_loader=sample_history)
    e.app.config["TESTING"] = True
    e.client = e.app.test_client()
    return e


def text(response):
    return response.get_data(as_text=True)


def manifest(env):
    response = env.client.get("/manifest.webmanifest")
    assert response.status_code == 200
    return json.loads(text(response))


def member(env):
    """Client connecté à l'espace abonné (un autre appareil que env.client, qui reste anonyme)."""
    client = env.app.test_client()
    assert client.post("/login", data={"email": EMAIL, "license_key": KEY}).status_code == 303
    return client


def png_size(data):
    """(largeur, hauteur) lues dans l'en-tête d'un fichier PNG."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "ce n'est pas un fichier PNG"
    return struct.unpack(">II", data[16:24])


def cached_files(env):
    """Adresses que le service worker met en cache, lues dans le fichier réellement servi."""
    source = text(env.client.get("/sw.js"))
    return json.loads(re.search(r"^var FILES = (\[.*\]);$", source, re.MULTILINE).group(1))


# --------------------------------------------------------------------------
# Manifeste et icônes
# --------------------------------------------------------------------------

def test_le_manifeste_decrit_une_application_installable(env):
    response = env.client.get("/manifest.webmanifest")
    assert response.status_code == 200 and response.content_type.startswith("application/manifest+json")
    data = json.loads(text(response))
    assert data["name"] == config.PRODUCT_NAME and data["lang"] == "fr"
    assert data["short_name"] == "Triple Elite" and len(data["short_name"]) <= 12      # sous l'icône : Android coupe au-delà
    assert data["display"] == "standalone"                                              # plein écran, sans barre d'adresse
    assert data["scope"] == "/" and data["id"] == "/"
    assert data["start_url"] == pwa.START_URL == "/debut" and data["start_url"].startswith(data["scope"])
    assert data["description"] and data["categories"] == ["sports"]


def destination(response):
    """Chemin vers lequel une réponse redirige (302)."""
    assert response.status_code == 302, response.status_code
    return urlparse(response.headers["Location"]).path


# L'adresse de lancement est écrite dans l'APK Android : elle ne doit plus bouger (un autre lancement = un autre APK).
# Ce qu'elle fait ensuite se règle côté serveur, et c'est ce que ces tests figent.

def test_le_lancement_de_l_application_mene_a_l_accueil_quand_on_n_est_pas_connecte(env):
    # Un visiteur qui vient d'installer l'application voit les offres, pas un formulaire qu'il ne peut pas remplir ;
    # et jamais « Ta session a expiré », que montrerait /app à un nouvel installateur.
    start = env.client.get(manifest(env)["start_url"])
    assert destination(start) == "/"
    assert env.client.get("/").status_code == 200                      # l'accueil répond : pas de redirection en boucle


def test_le_lancement_de_l_application_mene_a_l_espace_client_quand_on_est_connecte(env):
    start = member(env).get(manifest(env)["start_url"])
    assert start.status_code == 302 and start.headers["Location"].endswith("/app")


def test_au_lancement_un_abonnement_expire_est_traite_par_l_espace_client(env):
    client = member(env)
    env.licenses.records[EMAIL]["expires"] = license_manager._utcnow() - timedelta(minutes=1)
    env.app.extensions["tev"].gate.forget(EMAIL)                        # (le verdict de licence n'est gardé que 30 s)
    assert destination(client.get(pwa.START_URL)) == "/app"            # le lancement ne juge pas la licence...
    refused = client.get("/app")                                        # ...l'espace le fait, et explique pourquoi
    assert refused.status_code == 302 and refused.headers["Location"].endswith("/login?raison=expire")
    page = text(client.get("/login?raison=expire"))
    assert "Ton abonnement a expiré" in page and "Renouveler mon abonnement" in page


def test_l_adresse_de_lancement_ne_se_garde_pas_en_memoire(env):
    # La destination dépend de la personne qui ouvre l'application : ni le navigateur ni un intermédiaire ne la retient.
    assert env.client.get(pwa.START_URL).headers["Cache-Control"] == "no-store"
    assert member(env).get(pwa.START_URL).headers["Cache-Control"] == "no-store"


def test_les_couleurs_du_manifeste_sont_celles_du_site(env):
    data = manifest(env)
    for path in PAGES:
        assert THEME.search(text(env.client.get(path))).group(1) == data["theme_color"] == data["background_color"], path
    assert data["theme_color"] == pwa.THEME_COLOR
    assert pwa.THEME_COLOR.lower() in env.client.get("/static/css/site.css").get_data(as_text=True).lower()   # fond du site


def test_les_icones_du_manifeste_existent_aux_tailles_annoncees(env):
    icons = manifest(env)["icons"]
    assert {(icon["sizes"], icon["purpose"]) for icon in icons} >= {("192x192", "any"), ("512x512", "any"),
                                                                    ("512x512", "maskable")}
    for icon in icons:
        response = env.client.get(icon["src"])
        assert response.status_code == 200 and response.content_type == "image/png", icon
        side = int(icon["sizes"].split("x")[0])
        assert png_size(response.data) == (side, side), icon
        assert icon["type"] == "image/png" and icon["purpose"] in ("any", "maskable")   # jamais « any maskable » en un seul
        assert "no-store" not in response.headers.get("Cache-Control", "")             # fichier statique : se garde


def test_l_icone_d_iphone_est_declaree_et_existe(env):
    for path in PAGES:
        href = re.search(r'<link rel="apple-touch-icon" href="([^"]+)">', text(env.client.get(path))).group(1)
        response = env.client.get(href)
        assert response.status_code == 200 and png_size(response.data) == (180, 180), path


# --------------------------------------------------------------------------
# Service worker
# --------------------------------------------------------------------------

def test_le_service_worker_est_servi_a_la_racine_et_verifie_a_chaque_visite(env):
    response = env.client.get("/sw.js")
    assert response.status_code == 200 and response.content_type.startswith("text/javascript")
    assert response.headers["Cache-Control"] == "no-cache"           # une correction arrive chez les clients sans attendre
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_le_service_worker_ne_met_en_cache_que_la_page_hors_connexion_et_ses_fichiers(env):
    files = cached_files(env)
    assert files[0] == pwa.OFFLINE_PATH == "/hors-ligne"
    assert all(path.startswith("/static/") for path in files[1:]), files
    for path in files:
        # Pas de page de l'espace client, pas d'API, pas de formulaire : des données privées sur un téléphone partagé.
        assert not path.startswith(("/app", "/api/", "/login", "/logout", pwa.START_URL)), path
        # cache.addAll échoue en bloc si UN fichier ne répond pas 200 : le service worker ne s'installerait jamais.
        response = env.client.get(path)
        assert response.status_code == 200, path
    assert len(files) == len(set(files))


def test_le_service_worker_n_ajoute_rien_au_cache_apres_l_installation(env):
    source = text(env.client.get("/sw.js"))
    assert source.count("addAll(") == 1                              # seule écriture : à l'installation
    for forbidden in (".put(", ".add(", "clone()"):
        assert forbidden not in source, forbidden
    assert "'navigate'" in source and "Response.error()" in source   # pages : réseau d'abord, repli propre sinon
    assert "caches.delete" in source and "'tev-'" in source          # l'ancien cache est supprimé à l'activation


def test_le_nom_du_cache_change_quand_les_fichiers_mis_en_cache_changent(env, monkeypatch):
    def cache_name():
        return re.search(r'^var CACHE = "([^"]+)";$', text(env.client.get("/sw.js")), re.MULTILINE).group(1)

    before = cache_name()
    assert re.fullmatch(r"tev-[0-9a-f]{10}", before) and cache_name() == before        # stable d'une visite à l'autre
    monkeypatch.setattr(pwa, "OFFLINE_ASSETS", pwa.OFFLINE_ASSETS + ("js/pwa.js",))
    assert cache_name() != before                                                       # nouveau contenu : nouveau cache


def test_les_polices_gardees_sont_celles_que_demande_la_feuille_de_style(env):
    css = text(env.client.get("/static/css/site.css"))
    files = cached_files(env)
    for font in pwa.OFFLINE_FONTS:
        assert f'url("../{font}")' in css, font                      # même adresse : sinon la police ne vient pas du cache
        assert f"/static/{font}" in files
    # Les polices « latin-ext » ne servent qu'à d'autres alphabets que le français (unicode-range) : non gardées.
    assert all("latin-ext" not in path for path in files)
    assert any(f"/static/{asset}" in path for asset in pwa.OFFLINE_ASSETS for path in files)   # style et icône du site


# --------------------------------------------------------------------------
# Page « hors connexion »
# --------------------------------------------------------------------------

def test_la_page_hors_connexion_explique_et_propose_de_reessayer(env):
    response = env.client.get("/hors-ligne")
    page = text(response)
    assert response.status_code == 200 and "text/html" in response.content_type
    assert "Pas de connexion" in page and "internet" in page
    assert re.search(r'<a class="btn btn--or btn--grand" href="/app">Réessayer</a>', page)
    assert "data-install" not in page                               # pas d'invitation à installer sur cette page


def test_la_page_hors_connexion_n_a_aucun_script_et_est_la_meme_pour_tout_le_monde(env):
    anonymous = text(env.client.get("/hors-ligne"))
    assert "<script" not in anonymous                               # sans réseau, aucun script ne serait chargé
    # Le service worker en garde une seule copie, pour tous les clients de l'appareil : rien de personnel dedans.
    assert text(member(env).get("/hors-ligne")) == anonymous
    assert EMAIL not in anonymous


def test_les_routes_de_l_application_ont_les_en_tetes_de_securite(env):
    for path in ("/manifest.webmanifest", "/sw.js", "/hors-ligne", ASSET_LINKS):
        headers = env.client.get(path).headers
        assert "script-src 'self'" in headers["Content-Security-Policy"] and "'unsafe-inline'" not in headers["Content-Security-Policy"]
        assert headers["X-Content-Type-Options"] == "nosniff", path
    assert env.client.get("/hors-ligne").headers["Cache-Control"] == "no-store"      # le navigateur ne la garde pas : le service worker, si
    assert env.client.get("/manifest.webmanifest").headers["Cache-Control"] == "public, max-age=3600"
    assert env.client.get(ASSET_LINKS).headers["Cache-Control"] == "public, max-age=300"


# --------------------------------------------------------------------------
# Déclaration dans les pages, invitation à installer
# --------------------------------------------------------------------------

def pages_with_install(env):
    """Les pages publiques (vues par un visiteur) et l'espace client (vu par un abonné)."""
    return [(path, text(env.client.get(path))) for path in PAGES] + [("/app", text(member(env).get("/app")))]


def test_les_pages_declarent_l_application_et_chargent_son_script(env):
    for path, page in pages_with_install(env):
        assert '<link rel="manifest" href="/manifest.webmanifest">' in page, path
        assert re.search(r'<script src="/static/js/pwa\.js\?v=\w+" defer></script>', page), path
        assert '<meta name="apple-mobile-web-app-capable" content="yes">' in page, path
        assert '<meta name="mobile-web-app-capable" content="yes">' in page, path
        assert '<meta name="apple-mobile-web-app-title" content="Triple Elite">' in page, path


def test_l_invitation_a_installer_est_masquee_au_depart_et_unique_sur_chaque_page(env):
    for path, page in pages_with_install(env):
        assert len(re.findall(r"\sdata-install[\s>]", page)) == 1, path           # une seule invitation par page
        assert '<div class="install" data-install hidden>' in page, path
        for part in ("data-install-android", "data-install-ios", "data-install-button"):
            assert re.search(rf"\s{part}\s+hidden", page), (path, part)           # rien ne clignote avant le script
        assert "Plus tard" in page and "Installer l’application" in page, path


def test_dans_l_espace_client_l_invitation_est_en_haut_et_ailleurs_dans_le_pied_de_page(env):
    app_page = text(member(env).get("/app"))
    assert app_page.index("data-install ") < app_page.index('class="tabs"') < app_page.index("<footer")
    for path in PAGES:
        page = text(env.client.get(path))
        assert page.index("<footer") < page.index("data-install "), path


def test_le_fichier_robots_ne_bloque_rien_de_ce_qu_il_faut_pour_installer_l_application(env):
    rules = [line.split(":", 1)[1].strip() for line in text(env.client.get("/robots.txt")).splitlines()
             if line.lower().startswith("disallow:")]
    assert rules                                                      # l'espace client et l'API restent bloqués
    for path in ("/manifest.webmanifest", "/sw.js", "/hors-ligne", "/static/icons/icon-512.png", ASSET_LINKS,
                 pwa.START_URL):
        assert not any(path.startswith(rule) for rule in rules if rule), path


# --------------------------------------------------------------------------
# Application Android : fichier de liaison avec le site (Digital Asset Links)
# --------------------------------------------------------------------------

def asset_links(env):
    response = env.client.get(ASSET_LINKS)
    assert response.status_code == 200, "Android lit ce fichier sans redirection : il doit répondre 200 à cette adresse"
    return json.loads(text(response))


def test_le_fichier_de_liaison_android_declare_l_application_et_sa_cle(env):
    response = env.client.get(ASSET_LINKS)
    assert response.status_code == 200 and response.content_type == "application/json"
    statements = asset_links(env)
    assert len(statements) == 1
    statement = statements[0]
    assert statement["relation"] == ["delegate_permission/common.handle_all_urls"]
    target = statement["target"]
    assert target["namespace"] == "android_app" and target["package_name"] == config.ANDROID_PACKAGE
    assert target["sha256_cert_fingerprints"] == list(config.ANDROID_CERT_FINGERPRINTS)
    assert set(statement) == {"relation", "target"} and set(target) == {"namespace", "package_name", "sha256_cert_fingerprints"}


def test_la_configuration_android_est_bien_formee(env):
    # Une faute de frappe ici ne se verrait pas : Android ne reconnaîtrait pas l'application et afficherait une barre d'adresse.
    assert ANDROID_PACKAGE.fullmatch(config.ANDROID_PACKAGE) and config.ANDROID_PACKAGE == "com.tripleelitevip.app"
    assert config.ANDROID_CERT_FINGERPRINTS, "aucune empreinte : l'application s'ouvrirait avec une barre d'adresse"
    assert len(set(config.ANDROID_CERT_FINGERPRINTS)) == len(config.ANDROID_CERT_FINGERPRINTS)
    for fingerprint in config.ANDROID_CERT_FINGERPRINTS:
        assert FINGERPRINT.fullmatch(fingerprint), fingerprint


def test_le_fichier_de_liaison_range_les_empreintes_en_majuscules(env, monkeypatch):
    monkeypatch.setattr(config, "ANDROID_CERT_FINGERPRINTS", ("9a:70:ff:f3:" + "01:" * 25 + "ac:b2:13", "AB:" * 31 + "CD"))
    fingerprints = asset_links(env)[0]["target"]["sha256_cert_fingerprints"]
    assert fingerprints == ["9A:70:FF:F3:" + "01:" * 25 + "AC:B2:13", "AB:" * 31 + "CD"]
    assert all(FINGERPRINT.fullmatch(fingerprint) for fingerprint in fingerprints)


def test_sans_empreinte_le_fichier_de_liaison_est_une_liste_vide_valide(env, monkeypatch):
    monkeypatch.setattr(config, "ANDROID_CERT_FINGERPRINTS", ())
    assert asset_links(env) == []                  # pas de déclaration : jamais une déclaration avec une clé vide


def test_le_fichier_de_liaison_ne_depend_pas_du_visiteur(env):
    anonymous = text(env.client.get(ASSET_LINKS))
    assert text(member(env).get(ASSET_LINKS)) == anonymous
    assert EMAIL not in anonymous and KEY not in anonymous
