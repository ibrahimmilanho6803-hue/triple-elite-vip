import json
import logging
import re
import time
from datetime import timedelta

import pytest

import config
import dashboard
import license_manager
from generation_service import GenerationService
from site_fakes import EMAIL, KEY, FakeLicenses, make_pipeline, sample_history

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

    def load_history():
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
    if env.service._thread:
        env.service._thread.join(10)


# --------------------------------------------------------------------------
# Pages publiques, en-têtes
# --------------------------------------------------------------------------

def test_pages_publiques(env):
    for path in ("/", "/login", "/conditions"):
        response = env.client.get(path)
        assert response.status_code == 200 and "text/html" in response.content_type, path
    health = env.client.get("/health")
    assert health.status_code == 200 and health.get_json()["status"] == "ok"
    robots = env.client.get("/robots.txt").get_data(as_text=True)
    assert "Disallow: /app" in robots and "Disallow: /api/" in robots
    favicon = env.client.get("/favicon.ico")
    assert favicon.status_code == 301 and favicon.headers["Location"].endswith("/static/favicon.svg")
    assert env.client.head("/").status_code == 200            # sonde de disponibilité (UptimeRobot)


def test_prix_et_textes_de_la_page_d_accueil(env):
    page = env.client.get("/").get_data(as_text=True)
    assert "30 €" in page.replace("&nbsp;", " ") or "30&nbsp;€" in page
    assert "19 700 FCFA" in page and "39 400 FCFA" in page
    assert "Telegram" not in page                            # aucun canal de support inexistant
    assert "Cote totale" in page and "Équipe A" in page      # exemple clairement fictif
    assert "fictives" in page
    assert config.SELLER_EMAIL in env.client.get("/conditions").get_data(as_text=True)


def test_en_tetes_de_securite(env):
    for path in ("/", "/login", "/api/generate/status", "/nope"):
        headers = env.client.get(path).headers
        csp = headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "style-src 'self'" in csp and "'unsafe-inline'" not in csp
        assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"]
        assert headers["Cache-Control"] == "no-store"
    static = env.client.get("/static/css/site.css")
    assert static.status_code == 200 and "no-store" not in static.headers.get("Cache-Control", "")
    assert env.client.get("/static/favicon.svg").status_code == 200


def test_https_derriere_le_proxy_active_hsts(env):
    response = env.client.get("/", headers={"X-Forwarded-Proto": "https"})
    assert "max-age" in response.headers["Strict-Transport-Security"]
    assert "Strict-Transport-Security" not in env.client.get("/").headers


def test_aucun_script_ni_style_en_ligne_dans_les_pages(env):
    login(env.client)
    pages = {path: env.client.get(path).get_data(as_text=True) for path in ("/", "/login", "/conditions", "/app", "/nope")}
    for path, html in pages.items():
        assert "<style" not in html, path
        assert not re.search(r"<script(?![^>]*\bsrc=)", html), path
        assert not re.search(r"\sstyle=", html), path
        assert not re.search(r"\son[a-z]+=", html), path
        assert "javascript:" not in html, path


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

def test_generation_avec_suivi_puis_resultat_partage(env):
    login(env.client)
    assert env.client.get("/api/generate/status").get_json() == {"state": "idle"}
    started = env.client.post("/api/generate", headers=AJAX).get_json()
    assert started["state"] in ("running", "done")
    wait_for_job(env)
    done = env.client.get("/api/generate/status").get_json()
    assert done["state"] == "done" and len(done["combos"]) == 3 and done["meta"]["matches_total"] == 15
    leg = done["combos"][0]["predictions"][0]
    assert {"type_name", "estimated_odds", "odds_source", "confidence", "kickoff", "home_team"} <= set(leg)
    # Un autre abonné ne relance rien : il reçoit les mêmes combinés.
    other = dashboard_client(env, "autre@exemple.com")
    assert other.get("/api/generate/status").get_json()["combos"] == done["combos"]
    other.post("/api/generate", headers=AJAX)
    assert env.pipeline.state["calls"] == 1


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
    assert state == {"state": "error", "error": "Aucun combiné assez fiable pour le moment. Réessaie plus tard."}


def test_historique(env):
    login(env.client)
    data = env.client.get("/api/history").get_json()
    assert data == json.loads(json.dumps(env.history))
    assert data["summary"]["combos_settled"] == 5 and len(data["combos"]) == 4


def test_historique_en_erreur_ne_revele_rien(env):
    env.history_error = RuntimeError("mot de passe interne = hunter2")
    login(env.client)
    response = env.client.get("/api/history")
    assert response.status_code == 500
    assert "hunter2" not in response.get_data(as_text=True)
    assert "Impossible de charger l’historique" in response.get_json()["error"] or "Impossible de charger l'historique" in response.get_json()["error"]


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
