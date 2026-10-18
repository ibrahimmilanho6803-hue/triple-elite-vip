import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import config
import dashboard
import license_manager
from generation_service import FAILURE_COOLDOWN_SECONDS, GenerationService
from helpers import join_jobs
from pipeline import GenerationError, NoMatchToday
from site_fakes import EMAIL, KEY, FakeLicenses, make_pipeline, sample_combos, sample_history

AJAX = {"X-Requested-With": "fetch"}


class Env:
    pass


@pytest.fixture()
def env(tmp_path):
    e = Env()
    e.licenses = FakeLicenses()
    e.pipeline = make_pipeline(delay=0.0)
    e.service = GenerationService(pipeline=e.pipeline, cache_dir=str(tmp_path / "cache"), results_dir=str(tmp_path / "results"))
    e.history = sample_history()
    e.history_error = None
    e.history_viewers = []                  # pour qui l'historique a été demandé

    def load_history(viewer=None):
        e.history_viewers.append(viewer)
        if e.history_error:
            raise e.history_error
        return e.history

    e.app = dashboard.create_app(lm=e.licenses, service=e.service, history_loader=load_history)
    e.app.config["TESTING"] = True
    e.client = e.app.test_client()
    e.parts = e.app.extensions["tev"]
    return e


def login(client, email=EMAIL, key=KEY, **kwargs):
    return client.post("/login", data={"email": email, "license_key": key}, **kwargs)


def wait_for_job(env):
    join_jobs(env.service, 10)


# --------------------------------------------------------------------------
# Pages publiques, en-têtes
# --------------------------------------------------------------------------

def test_pages_publiques(env):
    for path in ("/", "/login", "/conditions", "/resultats", "/gratuit"):
        response = env.client.get(path)
        assert response.status_code == 200 and "text/html" in response.content_type, path
    health = env.client.get("/health")
    assert health.status_code == 200 and health.get_json()["status"] == "ok"
    robots = env.client.get("/robots.txt").get_data(as_text=True)
    assert "Disallow: /app" in robots and "Disallow: /api/" in robots and "Sitemap: " in robots
    favicon = env.client.get("/favicon.ico")
    assert favicon.status_code == 301 and favicon.headers["Location"].endswith("/static/favicon.svg")
    assert env.client.head("/").status_code == 200            # sonde de disponibilité (UptimeRobot)


def test_prix_et_textes_de_la_page_d_accueil(env):
    html = env.client.get("/").get_data(as_text=True)
    page = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.replace("&nbsp;", " ")))       # le texte lu par le visiteur
    # Le FCFA d'abord (la monnaie des clients), l'euro seulement à titre indicatif.
    assert "2 000 FCFA" in page and "6 000 FCFA" in page and "39 400 FCFA" in page
    assert "≈ 3 €" in page and "≈ 9 €" in page and "≈ 60 €" in page
    assert "Pass 7 jours" in page and all(f"plan={plan}" in html for plan in ("weekly", "monthly", "yearly"))
    assert "30 €" not in page and "19 700" not in page       # les anciens prix ont disparu
    assert "Telegram" not in page                            # aucun canal de support inexistant
    assert "Cote totale" in page and "Équipe A" in page      # exemple clairement fictif
    assert "fictives" in page
    assert config.SELLER_EMAIL in env.client.get("/conditions").get_data(as_text=True)


def test_l_accueil_n_annonce_que_les_moyens_de_paiement_reellement_disponibles(env):
    page = env.client.get("/").get_data(as_text=True).replace("&nbsp;", " ")
    for country in config.PAYMENT_COUNTRIES:
        assert page.count(country) >= 2                              # sous le bouton « S'abonner » et dans la FAQ
    assert "ou carte bancaire" not in page and "et carte bancaire" not in page
    assert "carte bancaire n’est pas encore disponible" in page and "nous te préviendrons" in page


def test_accueil_cartes_activees_les_mentions_de_carte_reviennent(env, monkeypatch):
    monkeypatch.setattr(config, "CARDS_ENABLED", True)
    page = env.client.get("/").get_data(as_text=True).replace("&nbsp;", " ")
    assert "Wave ou carte bancaire." in page and "Wave et carte bancaire, via la plateforme" in page
    assert "pas encore disponible" not in page and "nous te préviendrons" not in page


PUBLIC_PAGES = ("/", "/resultats", "/gratuit", "/conditions")          # identiques pour tous : le navigateur peut les garder un instant


def test_en_tetes_de_securite(env):
    for path in ("/", "/login", "/resultats", "/gratuit", "/conditions", "/api/generate/status", "/nope"):
        headers = env.client.get(path).headers
        csp = headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "style-src 'self'" in csp and "'unsafe-inline'" not in csp
        assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"]
        # Aucune page n'est gardée, sauf les quatre pages publiques (voir les tests de mémorisation ci-dessous).
        expected = f"private, max-age={config.PUBLIC_PAGE_CACHE_SECONDS}" if path in PUBLIC_PAGES else "no-store"
        assert headers["Cache-Control"] == expected, path
    static = env.client.get("/static/css/site.css")
    assert static.status_code == 200 and "no-store" not in static.headers.get("Cache-Control", "")
    assert env.client.get("/static/favicon.svg").status_code == 200


# --------------------------------------------------------------------------
# Pages publiques gardées une minute par le navigateur (barre de navigation rapide)
# --------------------------------------------------------------------------

def test_les_pages_publiques_sont_gardees_une_minute_par_le_navigateur_et_par_lui_seul(env):
    assert config.PUBLIC_PAGE_CACHE_SECONDS == 60
    for path in PUBLIC_PAGES:
        response = env.client.get(path)
        assert response.status_code == 200, path
        # « private » : ni Cloudflare ni aucun intermédiaire ne la garde ; seul le navigateur du visiteur la réutilise.
        assert response.headers["Cache-Control"] == "private, max-age=60", path
        # Rien qui varie selon le visiteur : ni cookie posé, ni « Vary: Cookie » (qui rendrait la copie inutilisable).
        assert "Set-Cookie" not in response.headers and "Vary" not in response.headers, path


def test_une_page_publique_est_identique_pour_un_visiteur_et_pour_un_client_connecte(env):
    # C'est ce qui rend sa mémorisation sans risque : aucune donnée de la personne ne s'y trouve.
    visitor = {path: env.client.get(path).get_data() for path in PUBLIC_PAGES}
    assert login(env.client).status_code == 303
    for path, html in visitor.items():
        assert env.client.get(path).get_data() == html, path


def test_les_autres_pages_et_les_erreurs_ne_sont_jamais_gardees(env):
    assert env.client.get("/login").headers["Cache-Control"] == "no-store"          # (visiteur : la page de connexion s'affiche)
    assert env.client.get("/nope").headers["Cache-Control"] == "no-store"
    assert env.client.get("/health").headers["Cache-Control"] == "no-store"
    login(env.client)
    for path in ("/app", "/debut", "/api/history", "/api/generate/status", "/logout", "/login", "/hors-ligne"):
        response = env.client.get(path)
        assert response.headers["Cache-Control"] == "no-store", (path, response.status_code)


def test_une_page_publique_en_panne_n_est_jamais_gardee(env, monkeypatch):
    env.history_error = RuntimeError("historique illisible")
    failed = env.client.get("/resultats")
    assert failed.status_code == 503 and failed.headers["Retry-After"] == "300"
    assert failed.headers["Cache-Control"] == "no-store"                              # la panne ne reste pas dans le navigateur
    monkeypatch.setattr(config, "FREE_PICK_ENABLED", False)
    closed = env.client.get("/gratuit")
    assert closed.status_code == 404 and closed.headers["Cache-Control"] == "no-store"


def test_la_duree_de_garde_se_regle_et_zero_la_coupe_avec_le_prechargement(env, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_PAGE_CACHE_SECONDS", 120)
    assert all(env.client.get(path).headers["Cache-Control"] == "private, max-age=120" for path in PUBLIC_PAGES)
    monkeypatch.setattr(config, "PUBLIC_PAGE_CACHE_SECONDS", 0)
    for path in PUBLIC_PAGES:
        response = env.client.get(path)
        assert response.headers["Cache-Control"] == "no-store", path
        assert "data-prefetch" not in response.get_data(as_text=True), path           # rien à précharger si rien ne se garde


def tab_links(html):
    """Les cinq boutons de la barre : {clé: balise <a> entière}."""
    return {re.search(r'data-tab="(\w+)"', tag).group(1): tag for tag in re.findall(r'<a class="tabbar__link"[^>]*>', html)}


def test_seules_les_pages_publiques_du_site_sont_marquees_pour_le_prechargement(env):
    links = tab_links(env.client.get("/").get_data(as_text=True))
    assert list(links) == ["accueil", "gratuit", "resultats", "vip", "abonnement"]
    assert [key for key, tag in links.items() if "data-prefetch" in tag] == ["accueil", "gratuit", "resultats"]
    # Chaque bouton marqué mène à une page que le serveur laisse effectivement garder : rien n'est préchargé pour rien.
    for key in ("accueil", "gratuit", "resultats"):
        path = re.search(r'href="([^"]+)"', links[key]).group(1)
        assert env.client.get(path).headers["Cache-Control"].startswith("private, max-age="), path
    # La connexion / l'espace client (page personnelle) et le site de paiement (autre adresse) ne le sont jamais.
    assert "data-prefetch" not in links["vip"] and "data-prefetch" not in links["abonnement"]


def test_la_barre_charge_son_script_sur_toutes_les_pages_qui_la_portent(env):
    pages = {path: env.client.get(path).get_data(as_text=True)
             for path in ("/", "/gratuit", "/resultats", "/conditions", "/login", "/nope")}
    login(env.client)
    pages["/app"] = env.client.get("/app").get_data(as_text=True)
    for path, html in pages.items():
        assert re.search(r'<script src="/static/js/tabbar\.js\?v=\w+" defer></script>', html), path
    assert 'src="/static/js/tabbar.js' not in env.client.get("/hors-ligne").get_data(as_text=True)    # la page hors connexion n'a pas de script
    script = env.client.get("/static/js/tabbar.js")
    assert script.status_code == 200 and "javascript" in script.content_type


def test_le_site_client_prepare_la_connexion_vers_le_site_de_paiement(env):
    for path in ("/", "/login", "/gratuit", "/nope"):
        html = env.client.get(path).get_data(as_text=True)
        assert f'<link rel="preconnect" href="{config.PAIEMENT_URL}">' in html, path
        assert html.count('rel="preconnect"') == 1, path
    assert 'rel="preconnect"' not in env.client.get("/hors-ligne").get_data(as_text=True)    # sans réseau, inutile


def test_https_derriere_le_proxy_active_hsts(env):
    response = env.client.get("/", headers={"X-Forwarded-Proto": "https"})
    assert "max-age" in response.headers["Strict-Transport-Security"]
    assert "Strict-Transport-Security" not in env.client.get("/").headers


def test_aucun_script_ni_style_en_ligne_dans_les_pages(env):
    login(env.client)
    pages = {path: env.client.get(path).get_data(as_text=True)
             for path in ("/", "/login", "/conditions", "/resultats", "/gratuit", "/app", "/hors-ligne", "/nope")}
    for path, html in pages.items():
        assert "<style" not in html, path
        assert not re.search(r"<script(?![^>]*\bsrc=)", html), path
        assert not re.search(r"\sstyle=", html), path
        assert not re.search(r"\son[a-z]+=", html), path
        assert "javascript:" not in html, path


def test_l_espace_client_a_la_barre_du_bas_sur_acces_vip_et_garde_la_deconnexion(env):
    login(env.client)
    page = env.client.get("/app").get_data(as_text=True)
    nav = re.search(r'<nav class="tabbar".*?</nav>', page, re.S).group(0)
    assert re.findall(r'<a class="tabbar__link" href="([^"]+)" data-tab="\w+"( aria-current="page")?(?: data-prefetch)?>', nav) == [
        ("/", ""), ("/gratuit", ""), ("/resultats", ""), ("/login", ' aria-current="page"'),
        (f"{config.PAIEMENT_URL}/paiement", "")]
    header = re.search(r'<header class="topbar">.*?</header>', page, re.S).group(0)
    assert 'action="/logout"' in header and "Déconnexion" in header
    # « Accès VIP » mène à /login : un client déjà connecté est renvoyé tout de suite à son espace.
    assert env.client.get("/login").headers["Location"].endswith("/app")


def test_cookie_de_session(env):
    response = login(env.client)
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Expires=" in cookie    # session durable


# --------------------------------------------------------------------------
# Connexion
# --------------------------------------------------------------------------

def test_connexion_reussie(env):
    response = login(env.client)
    assert response.status_code == 303 and response.headers["Location"].endswith("/app")
    page = env.client.get("/app")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Abonnement actif jusqu" in html and "dans 23" in html.replace(" ", " ")
    assert env.client.get("/login").status_code == 302                  # déjà connecté : direction l'espace
    assert env.client.get("/api/generate/status").status_code == 200


def test_connexion_insensible_a_la_casse_et_aux_espaces(env):
    response = login(env.client, email="  CLIENT@Exemple.COM ", key=f"  {KEY.upper()} ")
    assert response.status_code == 303


def test_connexion_refusee_message_unique(env):
    wrong_key = login(env.client, key="0000000000000000")
    wrong_mail = login(env.client, email="inconnu@exemple.com")
    assert wrong_key.status_code == wrong_mail.status_code == 401
    for response in (wrong_key, wrong_mail):
        assert "E-mail ou clé de licence incorrect." in response.get_data(as_text=True)
    assert env.client.get("/app").status_code == 302                    # toujours pas connecté
    assert login(env.client, email="", key="").status_code == 400


def test_le_motif_du_refus_n_est_ecrit_que_dans_les_journaux(env, caplog):
    """Pour comprendre une connexion refusée (e-mail inconnu ? clé différente ?), sans rien révéler au visiteur."""
    caplog.set_level(logging.INFO, logger="dashboard")
    wrong_key = login(env.client, key="0000000000000000")
    unknown = login(env.client, email="inconnu@exemple.com")
    assert "clé différente" in caplog.text and "e-mail inconnu" in caplog.text
    assert "client@exemple.com" not in caplog.text and "inconnu@exemple.com" not in caplog.text      # adresses masquées
    assert KEY not in caplog.text and "0000000000000000" not in caplog.text                          # jamais une clé
    for response in (wrong_key, unknown):
        page = response.get_data(as_text=True)
        assert "E-mail ou clé de licence incorrect." in page
        assert "clé différente" not in page and "e-mail inconnu" not in page


def test_le_message_d_erreur_est_echappe(env):
    response = login(env.client, email='"><script>alert(1)</script>@x.com', key="x")
    html = response.get_data(as_text=True)
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html


def test_blocage_apres_trop_d_essais(env):
    now = [1_800_000_000.0]
    env.parts.throttle.clock = lambda: now[0]
    for _ in range(config.LOGIN_MAX_ATTEMPTS):
        assert login(env.client, key="0000000000000000").status_code == 401
    blocked = login(env.client)                                         # même avec la bonne clé
    assert blocked.status_code == 429 and "Trop d’essais" in blocked.get_data(as_text=True)
    assert login(env.client, email="autre@exemple.com", key="x").status_code == 401   # autre compte non touché
    now[0] += config.LOGIN_WINDOW_SECONDS + 1
    assert login(env.client).status_code == 303


def test_succes_remet_le_compteur_a_zero(env):
    for _ in range(config.LOGIN_MAX_ATTEMPTS - 1):
        login(env.client, key="0000000000000000")
    assert login(env.client).status_code == 303
    assert env.parts.throttle.count(EMAIL) == 0


def test_licence_expiree_a_la_connexion(env):
    env.licenses.records[EMAIL]["expires"] = license_manager._utcnow() - timedelta(days=2)
    response = login(env.client)
    html = response.get_data(as_text=True)
    assert response.status_code == 403 and "a expiré le" in html and "Renouveler mon abonnement" in html
    assert env.parts.throttle.count(EMAIL) == 0                         # bonne clé : pas une tentative d'intrusion


def test_licence_desactivee_a_la_connexion(env):
    env.licenses.records[EMAIL]["active"] = False
    response = login(env.client)
    assert response.status_code == 403 and config.SELLER_EMAIL in response.get_data(as_text=True)


def test_base_de_licences_en_panne_a_la_connexion(env):
    env.licenses.down = True
    response = login(env.client)
    assert response.status_code == 503 and "momentanément indisponible" in response.get_data(as_text=True)
    assert env.parts.throttle.count(EMAIL) == 0


# --------------------------------------------------------------------------
# Accès protégé : la licence est revérifiée à chaque requête
# --------------------------------------------------------------------------

def test_sans_session_les_pages_redirigent_et_l_api_refuse(env):
    page = env.client.get("/app")
    assert page.status_code == 302 and page.headers["Location"].endswith("/login?raison=session")
    for method, path in (("get", "/api/generate/status"), ("get", "/api/history"), ("post", "/api/generate")):
        response = getattr(env.client, method)(path, headers=AJAX)
        assert response.status_code == 401 and response.get_json()["raison"] == "session", path


def test_expiration_en_cours_de_session_coupe_l_acces(env):
    login(env.client)
    assert env.client.get("/api/generate/status").status_code == 200
    env.licenses.records[EMAIL]["expires"] = license_manager._utcnow() - timedelta(minutes=1)
    env.parts.gate.forget(EMAIL)                                        # (le verdict n'est gardé que 30 s)
    response = env.client.get("/api/generate/status")
    assert response.status_code == 401 and response.get_json()["raison"] == "expire"
    assert "expiré" in response.get_json()["error"]
    assert env.client.get("/app").status_code == 302                    # la session a été vidée


def test_expiration_en_cours_de_session_preremplit_le_renouvellement(env):
    login(env.client)
    env.licenses.records[EMAIL]["expires"] = license_manager._utcnow() - timedelta(minutes=1)
    env.parts.gate.forget(EMAIL)
    assert env.client.get("/api/history").status_code == 401
    page = env.client.get("/login?raison=expire").get_data(as_text=True)
    assert f"{config.PAIEMENT_URL}/paiement?email=client%40exemple.com" in page
    assert 'value="client@exemple.com"' in page
    # Rien n'est gardé au-delà : ni après une connexion réussie, ni pour un autre motif de retour.
    assert 'value="client@exemple.com"' not in env.client.get("/login?raison=session").get_data(as_text=True)
    env.licenses.records[EMAIL]["expires"] = license_manager._utcnow() + timedelta(days=5)
    login(env.client)
    with env.client.session_transaction() as session:
        assert "renew_email" not in session


def test_desactivation_en_cours_de_session(env):
    login(env.client)
    env.licenses.records[EMAIL]["active"] = False
    env.parts.gate.forget(EMAIL)
    response = env.client.get("/api/history")
    assert response.status_code == 401 and response.get_json()["raison"] == "desactive"


def test_le_verdict_de_licence_est_garde_quelques_secondes(env):
    login(env.client)
    env.licenses.calls = 0
    for _ in range(10):
        assert env.client.get("/api/generate/status").status_code == 200
    assert env.licenses.calls <= 1
    now = [time.time() + config.LICENSE_CACHE_SECONDS + 1]
    env.parts.gate.clock = lambda: now[0]
    env.client.get("/api/generate/status")
    assert env.licenses.calls == 2                                      # le délai écoulé, la base est reconsultée


def test_panne_de_base_tolerance_pour_un_client_deja_verifie(env):
    login(env.client)
    env.client.get("/api/generate/status")                              # verdict « actif » en mémoire
    env.licenses.down = True
    now = [time.time() + config.LICENSE_CACHE_SECONDS + 5]
    env.parts.gate.clock = lambda: now[0]
    assert env.client.get("/api/generate/status").status_code == 200    # la panne ne l'éjecte pas
    now[0] += config.LICENSE_GRACE_SECONDS + 1
    response = env.client.get("/api/generate/status")
    assert response.status_code == 503 and "indisponible" in response.get_json()["error"]
    assert env.client.get("/app").status_code == 503                    # pas de déconnexion silencieuse
    assert env.client.get("/app").get_data(as_text=True).count("Service momentanément indisponible") >= 1


def test_deconnexion(env):
    login(env.client)
    response = env.client.post("/logout")
    assert response.status_code == 303 and response.headers["Location"].endswith("/login?raison=deconnecte")
    assert env.client.get("/api/generate/status").status_code == 401
    page = env.client.get("/login?raison=deconnecte").get_data(as_text=True)
    assert "Tu es déconnecté" in page
    login(env.client)
    assert env.client.get("/logout").status_code == 303                 # le lien direct fonctionne aussi
    assert env.client.get("/api/history").status_code == 401


def test_message_de_retour_sur_la_page_de_connexion(env):
    page = env.client.get("/login?raison=expire").get_data(as_text=True)
    assert "Ton abonnement a expiré" in page and "Renouveler mon abonnement" in page
    unknown = env.client.get("/login?raison=<b>x</b>").get_data(as_text=True)
    assert "<b>x</b>" not in unknown


# --------------------------------------------------------------------------
# Protection des requêtes
# --------------------------------------------------------------------------

def test_requete_venant_d_un_autre_site_refusee(env):
    response = login(env.client, headers={"Origin": "https://pirate.example"})
    assert response.status_code == 403
    assert login(env.client, headers={"Origin": "null"}).status_code == 403
    assert login(env.client, headers={"Referer": "https://pirate.example/formulaire"}).status_code == 403
    assert login(env.client, headers={"Origin": "http://localhost"}).status_code == 303
    assert login(env.client, headers={"Origin": config.SITE_URL}).status_code == 303


def test_actions_de_l_interface_exigent_l_en_tete(env):
    login(env.client)
    response = env.client.post("/api/generate")
    assert response.status_code == 400
    assert env.pipeline.state["calls"] == 0
    assert env.client.post("/api/generate", headers={**AJAX, "Origin": "https://pirate.example"}).status_code == 403


def test_anciennes_routes_supprimees(env):
    login(env.client)
    assert env.client.post("/api/clear-history", headers=AJAX).status_code == 404
    assert env.client.get("/api/download/combo_20260101_000000.json").status_code == 404
    assert env.client.get("/api/generate", headers=AJAX).status_code == 405       # l'ancien appel GET n'existe plus


# --------------------------------------------------------------------------
# Génération et historique
# --------------------------------------------------------------------------

def test_generation_avec_suivi_puis_resultat_personnel(env):
    login(env.client)
    assert env.client.get("/api/generate/status").get_json() == {"state": "idle", "quota": {"limit": 2, "used": 0, "left": 2}}
    started = env.client.post("/api/generate", headers=AJAX).get_json()
    assert started["state"] in ("running", "done")
    wait_for_job(env)
    done = env.client.get("/api/generate/status").get_json()
    assert done["state"] == "done" and len(done["combos"]) == 3 and done["meta"]["matches_total"] == 15
    leg = done["combos"][0]["predictions"][0]
    assert {"type_name", "estimated_odds", "odds_source", "confidence", "kickoff", "home_team"} <= set(leg)
    # Un autre abonné ne voit RIEN de ces combinés, seulement le bouton : les siens viendront de sa propre génération.
    other = dashboard_client(env, "autre@exemple.com")
    assert other.get("/api/generate/status").get_json() == {"state": "idle", "quota": {"limit": 2, "used": 0, "left": 2}}
    assert env.pipeline.state["calls"] == 1
    assert other.post("/api/generate", headers=AJAX).get_json()["started"] is True
    wait_for_job(env)
    assert env.pipeline.state["calls"] == 2                                           # son propre travail, pas celui du premier
    mine = other.get("/api/generate/status").get_json()
    assert mine["state"] == "done" and len(mine["combos"]) == 3
    assert env.client.get("/api/generate/status").get_json()["combos"] == done["combos"]    # et le premier garde les siens


def dashboard_client(env, email):
    env.licenses.records[email] = {"key": "fedcba9876543210", "active": True,
                                   "expires": license_manager._utcnow() + timedelta(days=5)}
    client = env.app.test_client()
    assert login(client, email=email, key="fedcba9876543210").status_code == 303
    return client


def test_echec_de_generation_message_lisible(env):
    from pipeline import GenerationError
    env.pipeline = make_pipeline(delay=0.0, error=GenerationError("Aucun combiné assez fiable pour le moment. Réessaie plus tard."))
    env.service.pipeline = env.pipeline
    login(env.client)
    env.client.post("/api/generate", headers=AJAX)
    wait_for_job(env)
    state = env.client.get("/api/generate/status").get_json()
    assert state == {"state": "error", "error": "Aucun combiné assez fiable pour le moment. Réessaie plus tard.",
                     "quota": {"limit": 2, "used": 0, "left": 2}}                  # l'analyse n'a rien donné : clic rendu


def test_historique(env):
    login(env.client)
    data = env.client.get("/api/history").get_json()
    assert data == json.loads(json.dumps(env.history))
    assert data["summary"]["combos_settled"] == 5 and len(data["combos"]) == 4


def test_l_historique_est_demande_pour_le_client_connecte(env):
    login(env.client)
    other = dashboard_client(env, "autre@exemple.com")
    assert env.client.get("/api/history").status_code == 200 and other.get("/api/history").status_code == 200
    assert env.history_viewers == [GenerationService.scope_of(EMAIL), GenerationService.scope_of("autre@exemple.com")]
    assert env.history_viewers[0] != env.history_viewers[1]


def renumbered(combos, offset):
    for combo in combos:
        for item in combo["predictions"]:
            item["match_id"] = str(int(item["match_id"]) + offset)
    return combos


def test_l_historique_reel_ne_montre_que_les_combines_du_client_connecte(env, monkeypatch, tmp_path):
    """Le vrai chargeur d'historique sur de vrais fichiers : les combinés à venir qu'un autre abonné a générés n'y sont pas."""
    import combo_history
    monkeypatch.setattr(config, "RESULTS_DIR", env.service.results_dir)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "scores.db"))
    monkeypatch.setattr(combo_history, "refresh_pending_results", lambda *args, **kwargs: False)       # jamais de réseau
    app = dashboard.create_app(lm=env.licenses, service=env.service)
    app.config["TESTING"] = True
    alice, bob = app.test_client(), app.test_client()
    login(alice)
    env.licenses.records["bob@exemple.com"] = {"key": "fedcba9876543210", "active": True,
                                               "expires": license_manager._utcnow() + timedelta(days=5)}
    login(bob, email="bob@exemple.com", key="fedcba9876543210")
    assert alice.get("/api/history").get_json()["combos"] == []
    alice.post("/api/generate", headers=AJAX)
    wait_for_job(env)
    env.service.pipeline = make_pipeline(delay=0.0, combos=renumbered(sample_combos(), 100))       # d'autres matchs pour Bob
    bob.post("/api/generate", headers=AJAX)
    wait_for_job(env)
    for client in (alice, bob):
        history = client.get("/api/history").get_json()
        assert len(history["combos"]) == 3 and history["summary"]["pending"] == 3, "ni plus, ni les combinés de l'autre"
    anonymous = app.test_client().get("/api/history")
    assert anonymous.status_code in (401, 403)


def test_historique_en_erreur_ne_revele_rien(env):
    env.history_error = RuntimeError("mot de passe interne = hunter2")
    login(env.client)
    response = env.client.get("/api/history")
    assert response.status_code == 500
    assert "hunter2" not in response.get_data(as_text=True)
    assert "Impossible de charger l’historique" in response.get_json()["error"] or "Impossible de charger l'historique" in response.get_json()["error"]


# --------------------------------------------------------------------------
# Générations des clients : CLIENT_GENERATIONS_PER_DAY par jour
# --------------------------------------------------------------------------

NOW = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
TODAY = "2026-10-08"
NO_MATCH_TITLE = "Pas de championnat aujourd'hui"


class Clock:
    def __init__(self, now=NOW):
        self.now = now.timestamp()

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta).total_seconds()


@pytest.fixture()
def timed(env):
    """Le même site, avec une horloge que le test fait avancer (un jour de championnat, 10 h UTC)."""
    env.clock = Clock()
    env.service.clock = env.clock
    env.pipeline = make_pipeline(delay=0.0, combos=sample_combos(NOW))
    env.service.pipeline = env.pipeline
    return env


def post_generate(env, client=None):
    response = (client or env.client).post("/api/generate", headers=AJAX)
    assert response.status_code == 200
    return response.get_json()


def status_of(env, client=None):
    return (client or env.client).get("/api/generate/status").get_json()


def used(env, email=EMAIL, day=TODAY):
    return env.licenses.usage.get((email, day), 0)


def test_chaque_client_a_deux_generations_par_jour_par_defaut():
    assert config.CLIENT_GENERATIONS_PER_DAY == 2


def test_le_clic_qui_demarre_une_analyse_consomme_une_generation(timed):
    login(timed.client)
    started = post_generate(timed)
    assert started["state"] == "running" and started["started"] is True
    assert started["quota"] == {"limit": 2, "used": 1, "left": 1}
    wait_for_job(timed)
    assert used(timed) == 1                                            # l'analyse a abouti : le clic est bien consommé
    done = status_of(timed)
    assert done["state"] == "done" and "quota" not in done              # les combinés s'affichent, plus de bouton


def test_afficher_ses_combines_deja_prets_ou_rejoindre_son_travail_ne_coute_rien(timed):
    gate = threading.Event()

    def slow(progress=None, variety=None):
        gate.wait(5)
        return {"combos": sample_combos(NOW), "meta": {"day": TODAY, "day_total": 9, "matches_total": 9}}

    timed.service.pipeline = slow
    login(timed.client)
    assert post_generate(timed)["started"] is True                      # le client lance l'analyse
    rejoint = post_generate(timed)                                      # un second clic (autre onglet) la rejoint : rien à compter
    assert rejoint["state"] == "running" and "started" not in rejoint
    assert used(timed) == 1
    gate.set()
    wait_for_job(timed)
    pret = post_generate(timed)                                         # puis il retrouve ses combinés, toujours sans rien compter
    assert pret["state"] == "done" and used(timed) == 1


def test_chaque_client_a_son_propre_travail_en_cours(timed):
    """Pendant qu'un client attend ses combinés, le clic d'un autre ne rejoint PAS ce travail : il lance le sien, qui lui coûte
    sa propre génération. Aucun ne reçoit les combinés de l'autre."""
    gate = threading.Event()
    calls = []

    def slow(progress=None, variety=None):
        calls.append(variety)
        gate.wait(5)
        return {"combos": sample_combos(NOW), "meta": {"day": TODAY, "day_total": 9, "matches_total": 9}}

    timed.service.pipeline = slow
    autre = dashboard_client(timed, "autre@exemple.com")
    login(timed.client)
    assert post_generate(timed)["started"] is True
    second = post_generate(timed, autre)
    assert second["state"] == "running" and second["started"] is True       # son propre travail
    assert used(timed) == 1 and used(timed, "autre@exemple.com") == 1
    assert status_of(timed)["state"] == "running" and status_of(timed, autre)["state"] == "running"
    gate.set()
    wait_for_job(timed)
    assert len(calls) == 2
    assert status_of(timed)["state"] == "done" and status_of(timed, autre)["state"] == "done"


def test_deux_analyses_par_jour_puis_le_message_et_aucun_appel_payant(timed):
    login(timed.client)
    for expected in (1, 2):
        assert post_generate(timed)["started"] is True
        wait_for_job(timed)
        assert used(timed) == expected
        timed.clock.advance(hours=4)                                   # les combinés ne sont plus frais : on peut relancer
    calls = timed.pipeline.state["calls"]
    assert calls == 2
    # Plus de génération : l'état de l'espace le dit d'emblée, et le clic ne lance rien.
    blocked = status_of(timed)
    assert blocked["state"] == "quota" and blocked["quota"] == {"limit": 2, "used": 2, "left": 0}
    assert blocked["message"] == ("Tu as utilisé tes 2 générations d'aujourd'hui. "
                                  "Reviens demain pour en lancer de nouvelles.")
    refused = post_generate(timed)
    assert refused == blocked
    assert timed.pipeline.state["calls"] == calls and used(timed) == 2
    # Le lendemain, le compteur repart de zéro.
    timed.clock.advance(hours=20)
    assert status_of(timed) == {"state": "idle", "quota": {"limit": 2, "used": 0, "left": 2}}
    assert post_generate(timed)["started"] is True
    wait_for_job(timed)


def test_chaque_client_a_ses_propres_generations(timed):
    autre = dashboard_client(timed, "autre@exemple.com")
    login(timed.client)
    for _ in range(2):
        assert post_generate(timed)["started"] is True
        wait_for_job(timed)
        timed.clock.advance(hours=4)
    assert status_of(timed)["state"] == "quota"
    deux = status_of(timed, autre)
    assert deux["state"] == "idle" and deux["quota"] == {"limit": 2, "used": 0, "left": 2}   # l'autre n'a rien utilisé
    assert post_generate(timed, autre)["started"] is True
    wait_for_job(timed)


def test_une_analyse_en_echec_rend_le_clic(timed):
    timed.service.pipeline = make_pipeline(delay=0.0, error=GenerationError("Aucun combiné assez fiable. Réessaie."))
    login(timed.client)
    started = post_generate(timed)
    assert started["started"] is True and started["quota"]["used"] == 1
    wait_for_job(timed)
    assert used(timed) == 0                                            # rien n'a été livré : le clic est rendu
    failed = status_of(timed)
    assert failed["state"] == "error" and failed["quota"] == {"limit": 2, "used": 0, "left": 2}
    # Pendant la pause qui suit un échec, le clic ne relance rien et ne coûte rien.
    again = post_generate(timed)
    assert again["state"] == "error" and used(timed) == 0 and timed.service.generations_today() == 1
    timed.clock.advance(seconds=FAILURE_COOLDOWN_SECONDS + 1)
    assert status_of(timed)["state"] == "idle"


def test_une_panne_inattendue_rend_aussi_le_clic(timed):
    def boom(progress=None, variety=None):
        raise RuntimeError("détail interne")

    timed.service.pipeline = boom
    login(timed.client)
    assert post_generate(timed)["started"] is True
    wait_for_job(timed)
    assert used(timed) == 0 and status_of(timed)["state"] == "error"


def test_un_jour_sans_championnat_le_clic_est_rendu_et_le_message_est_donne(timed):
    timed.service.pipeline = make_pipeline(delay=0.0, error=NoMatchToday(0, 0, TODAY))
    login(timed.client)
    assert post_generate(timed)["started"] is True                     # seul moyen de le savoir : une première analyse
    wait_for_job(timed)
    verdict = status_of(timed)
    assert verdict["state"] == "no_match" and verdict["reason"] == "none" and verdict["title"] == NO_MATCH_TITLE
    assert "quota" not in verdict
    assert used(timed) == 0 and timed.service.generations_today() == 0   # ni le client ni le plafond du jour n'ont payé
    # Ensuite, plus rien ne part, pour personne, et ce n'est pas une erreur.
    autre = dashboard_client(timed, "autre@exemple.com")
    for client in (timed.client, autre):
        response = post_generate(timed, client)
        assert response["state"] == "no_match" and response["title"] == NO_MATCH_TITLE
    assert used(timed) == 0 and used(timed, "autre@exemple.com") == 0
    assert timed.pipeline.state["calls"] == 0 and timed.service.pipeline.state["calls"] == 1    # une seule analyse
    timed.clock.advance(days=1)                                        # le lendemain, tout repart
    assert status_of(timed)["state"] == "idle"


def test_les_matchs_deja_commences_ou_trop_peu_nombreux_ont_leur_propre_message(timed):
    for total, upcoming, reason, title in ((6, 0, "started", "Les matchs d'aujourd'hui ont déjà commencé"),
                                           (6, 2, "few", "Trop peu de matchs aujourd'hui")):
        timed.service._write("matchday.json", {"day": TODAY, "total": total, "upcoming": upcoming, "reason": reason,
                                               "checked_ts": timed.clock()})
        login(timed.client)
        state = status_of(timed)
        assert state["state"] == "no_match" and state["reason"] == reason and state["title"] == title
        assert post_generate(timed)["state"] == "no_match" and used(timed) == 0


def test_un_clic_qui_ne_demarre_rien_est_rendu(timed):
    """Plafond du jour de tous les clients atteint : l'analyse ne part pas, donc le client ne perd rien."""
    for _ in range(config.MAX_GENERATIONS_PAR_JOUR):
        timed.service._count_generation()
    login(timed.client)
    response = post_generate(timed)
    assert response["state"] == "error" and "started" not in response
    assert response["quota"] == {"limit": 2, "used": 0, "left": 2} and used(timed) == 0
    assert timed.pipeline.state["calls"] == 0


def test_un_compteur_illisible_ne_bloque_personne(timed, caplog):
    timed.licenses.usage_down = True
    login(timed.client)
    assert "quota" not in status_of(timed) and status_of(timed)["state"] == "idle"
    with caplog.at_level(logging.ERROR):
        started = post_generate(timed)
    assert started["state"] == "running" and started["started"] is True and "quota" not in started
    wait_for_job(timed)
    assert status_of(timed)["state"] == "done"
    assert "compteur de générations indisponible" in caplog.text


def test_le_message_de_limite_s_accorde_avec_le_nombre_de_generations():
    assert dashboard.quota_message(1) == "Tu as utilisé ta génération d'aujourd'hui. Reviens demain pour en lancer une nouvelle."
    assert "tes 5 générations" in dashboard.quota_message(5)
    assert dashboard.quota_view(7, 2) == {"limit": 2, "used": 7, "left": 0}
    assert dashboard.quota_view(-1, 2) == {"limit": 2, "used": 0, "left": 2}


def test_la_limite_se_regle_dans_la_configuration(timed, monkeypatch):
    monkeypatch.setattr(config, "CLIENT_GENERATIONS_PER_DAY", 1)
    login(timed.client)
    assert post_generate(timed)["quota"] == {"limit": 1, "used": 1, "left": 0}
    wait_for_job(timed)
    timed.clock.advance(hours=4)
    blocked = status_of(timed)
    assert blocked["state"] == "quota" and blocked["message"].startswith("Tu as utilisé ta génération")


def test_l_espace_annonce_que_les_combines_sont_personnels(timed):
    login(timed.client)
    html = timed.client.get("/app").get_data(as_text=True).replace("&nbsp;", " ").replace("\u00a0", " ")
    assert "des onze compétitions" in html
    assert "Ces combinés sont les tiens : aucun autre abonné ne les voit." in html
    assert "partagés entre tous les abonnés" not in html


def test_la_legende_dit_quand_une_cote_est_reelle_ou_estimee(timed):
    """Le défaut signalé par un client : certaines cotes ont un « ≈ » et d'autres non, sans que la page l'explique."""
    login(timed.client)
    html = timed.client.get("/app").get_data(as_text=True).replace("&nbsp;", " ").replace(" ", " ")
    assert "Une cote <strong>sans</strong> « ≈ » est la cote réelle relevée chez plusieurs bookmakers." in html
    assert "estimée par notre modèle, faute de cote réelle pour ce pari" in html and "les deux équipes marquent" in html


def test_l_espace_annonce_les_generations_du_jour(timed):
    login(timed.client)
    html = timed.client.get("/app").get_data(as_text=True).replace("&nbsp;", " ")
    assert "Tu peux lancer 2 générations par jour." in html and 'id="generate-quota"' in html
    assert "matchs du jour" in html and "jusqu’à trois combinés" in html and 'id="no-match"' in html


def test_les_reponses_de_generation_ne_sont_pas_mises_en_cache(timed):
    login(timed.client)
    for response in (timed.client.get("/api/generate/status"), timed.client.post("/api/generate", headers=AJAX)):
        assert response.headers["Cache-Control"] == "no-store"
    wait_for_job(timed)


# --------------------------------------------------------------------------
# Erreurs
# --------------------------------------------------------------------------

def test_pages_d_erreur(env):
    page = env.client.get("/xmlrpc.php")
    assert page.status_code == 404 and "Page introuvable" in page.get_data(as_text=True)
    api = env.client.get("/api/inconnu")
    assert api.status_code == 404 and api.get_json()["error"]
    assert env.client.post("/").status_code == 405


def test_erreur_inattendue_page_generique(env):
    @env.app.get("/boom")
    def boom():
        raise RuntimeError("détail interne secret")

    response = env.client.get("/boom")
    assert response.status_code == 500
    assert "secret" not in response.get_data(as_text=True)
    assert "Une erreur est survenue" in response.get_data(as_text=True)


def test_espace_affiche_le_renouvellement_proche(env):
    env.licenses.records[EMAIL]["expires"] = license_manager._utcnow() + timedelta(days=3, hours=2)
    login(env.client)
    html = env.client.get("/app").get_data(as_text=True).replace(" ", " ")
    assert "Ton abonnement se termine dans 3 jours" in html
    env.licenses.records[EMAIL]["expires"] = license_manager._utcnow() + timedelta(hours=5)
    env.parts.gate.forget(EMAIL)
    assert "dans moins de 24 heures" in env.client.get("/app").get_data(as_text=True).replace(" ", " ")


def test_liens_de_renouvellement_preremplissent_l_e_mail(env):
    login(env.client)
    html = env.client.get("/app").get_data(as_text=True)
    assert f"{config.PAIEMENT_URL}/paiement?email=client%40exemple.com" in html
    env.licenses.records[EMAIL]["expires"] = license_manager._utcnow() - timedelta(days=2)
    expired = login(env.client).get_data(as_text=True)
    assert f"{config.PAIEMENT_URL}/paiement?email=client%40exemple.com" in expired
    # Sans e-mail connu (retour sur la page de connexion), le lien reste simple.
    page = env.app.test_client().get("/login?raison=expire").get_data(as_text=True)
    assert f'href="{config.PAIEMENT_URL}/paiement"' in page and "email=" not in page
