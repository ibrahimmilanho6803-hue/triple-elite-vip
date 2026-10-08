"""Site de paiement : parcours d'achat complet avec un faux PayDunya et une vraie base de licences (SQLite)."""
import datetime
import re
import threading
from types import SimpleNamespace

import pytest
from flask import Flask

import config
import license_manager
import web_common
from fakes import raw, sqlite_license_manager
from pay_fakes import FakeMailer, FakePayDunya
from paiement import PLANS, create_app
from paydunya import PayDunyaError

EMAIL = "client@exemple.com"
OWNER = "proprietaire@exemple.com"


def build_env(tmp_path, *, test_mode=False, test_emails=None):
    path = str(tmp_path / "licences.db")
    lm = sqlite_license_manager(path)
    pd, mailer = FakePayDunya(), FakeMailer()
    pd.test_mode = test_mode
    # Liste de test explicite (jamais celle de l'environnement) : les tests ne dépendent pas de la machine.
    app = create_app(lm=lm, paydunya=pd, send_license=mailer, test_emails=test_emails or [])
    app.testing = True
    return SimpleNamespace(app=app, client=app.test_client(), lm=lm, pd=pd, mailer=mailer, path=path)


@pytest.fixture()
def env(tmp_path):
    return build_env(tmp_path)


@pytest.fixture()
def env_test(tmp_path):
    """Site en mode test PayDunya (clés de test) : seule l'adresse du propriétaire peut commander."""
    return build_env(tmp_path, test_mode=True, test_emails=[f"  {OWNER.upper()} ", ""])


def order_row(env, token):
    return raw(env.path, "SELECT email, plan, duree, processed, license_key, renewed FROM pending_orders WHERE token = ?",
               (token,))


def order_days(env, token):
    """Durée en jours enregistrée avec la commande (None : commande d'avant le pass de 7 jours)."""
    return raw(env.path, "SELECT jours FROM pending_orders WHERE token = ?", (token,))[0][0]


def start(env, email=EMAIL, plan="monthly", accept="1", **kwargs):
    """Le client choisit une offre et coche la case des conditions (accept=None : il ne la coche pas) :
    renvoie (réponse, jeton de la facture créée)."""
    data = {"email": email, "plan": plan}
    if accept is not None:
        data["accept"] = accept
    response = env.client.post("/payer", data=data, **kwargs)
    token = env.pd.created[-1]["token"] if response.status_code == 303 else None
    return response, token


def text(response):
    return response.get_data(as_text=True)


# --------------------------------------------------------------------------
# Page d'offres
# --------------------------------------------------------------------------

def test_page_d_offres_prix_et_formulaire(env):
    response = env.client.get("/paiement")
    page = text(response)
    flat = page.replace("&nbsp;", " ").replace("\u00a0", " ")
    assert response.status_code == 200
    # Le FCFA d'abord (la monnaie des clients et de PayDunya) ; l'euro n'est qu'un ordre de grandeur.
    assert "2 000 FCFA" in flat and "6 000 FCFA" in flat and "39 400 FCFA" in flat
    assert "≈ 3 €" in flat and "≈ 9 €" in flat and "≈ 60 €" in flat
    assert "19 700" not in flat and "30 €" not in flat                          # les anciens prix ont disparu
    assert page.count('name="plan"') == 3 and 'action="/payer"' in page and 'method="post"' in page
    assert re.findall(r'name="plan" value="(\w+)"', page) == ["weekly", "monthly", "yearly"]      # du plus petit au plus grand
    assert 'value="monthly" checked' in page and page.count(" checked") == 1                       # le mensuel reste proposé d'abord
    assert "7 jours d’accès" in flat and "30 jours d’accès" in flat and "365 jours d’accès" in flat
    assert "Pass 7 jours" in flat and "soit 3 283 FCFA par mois" in flat
    assert "Support Telegram" not in page and "style=" not in page and "onclick" not in page


def test_la_page_demande_d_accepter_les_conditions(env):
    page = text(env.client.get("/paiement"))
    assert '<input type="checkbox" id="accept" name="accept" value="1" required>' in page      # jamais cochée d'avance
    assert "droit de rétractation" in page and "accès immédiat" in page and "18&nbsp;ans ou plus" in page
    assert f'href="{config.SITE_URL}/conditions"' in page


def test_offre_et_e_mail_preselectionnes_par_l_adresse(env):
    page = text(env.client.get("/paiement?plan=yearly&email=Client%40Exemple.com"))
    assert 'value="yearly" checked' in page and 'value="client@exemple.com"' in page
    assert 'value="monthly" checked' not in page
    page = text(env.client.get("/paiement?plan=inconnu&email=%22%3E%3Cscript%3E"))
    assert 'value="monthly" checked' in page and "<script>alert" not in page and 'value=""' in page


def test_annulation_chez_paydunya_est_expliquee(env):
    assert "Paiement annulé" in text(env.client.get("/paiement?annule=1&plan=monthly"))
    assert "Paiement annulé" not in text(env.client.get("/paiement"))


def test_la_page_n_annonce_que_les_moyens_de_paiement_reellement_disponibles(env):
    """Tant que PayDunya n'a pas activé les cartes, la page ne les promet pas, dit où l'on peut payer et à qui écrire."""
    page = text(env.client.get("/paiement")).replace("&nbsp;", " ")
    assert 'id="pay-methods"' in page and "Pour le moment, paiement par Mobile Money uniquement" in page
    for country in config.PAYMENT_COUNTRIES:
        assert country in page
    assert "carte bancaire n’est pas encore disponible" in page
    assert f'href="mailto:{config.SELLER_EMAIL}"' in page and "nous te préviendrons" in page
    assert ">Payer avec Mobile Money<" in page and "ou carte" not in page                  # bouton sans carte
    assert "et carte bancaire" not in page and "ou carte bancaire" not in page


def test_cartes_activees_les_mentions_de_carte_reviennent_et_l_avis_disparait(env, monkeypatch):
    monkeypatch.setattr(config, "CARDS_ENABLED", True)
    page = text(env.client.get("/paiement")).replace("&nbsp;", " ")
    assert ">Payer avec Mobile Money ou carte<" in page and "Orange Money, MTN, Moov, Wave et carte bancaire" in page
    assert 'id="pay-methods"' not in page and "pas encore disponible" not in page


def test_l_avis_de_paiement_reste_visible_avec_le_message_d_annulation_et_les_erreurs(env):
    assert 'id="pay-methods"' in text(env.client.get("/paiement?annule=1&plan=monthly"))
    response, _ = start(env, email="pas-un-mail")
    assert response.status_code == 400 and 'id="pay-methods"' in text(response)


def test_etiquettes_de_prix_fcfa_d_abord_euro_indicatif():
    prices = web_common.price_labels()
    assert prices["weekly"]["fcfa"] == "2\u00a0000\u00a0FCFA" and prices["weekly"]["amount"] == "2\u00a0000"
    assert prices["monthly"]["fcfa"] == "6\u00a0000\u00a0FCFA" and prices["yearly"]["amount"] == "39\u00a0400"
    assert [prices[key]["eur"] for key in ("weekly", "monthly", "yearly")] == [
        "≈\u00a03\u00a0€", "≈\u00a09\u00a0€", "≈\u00a060\u00a0€"]
    assert prices["yearly"]["per_month"] == "3\u00a0283\u00a0FCFA"
    # L'annuel est annoncé « au prix de 6,6 mois » et « 45 % » d'économie : arrondis dans le sens qui ne promet pas plus.
    assert prices["yearly"]["months_price"] == "6,6" and prices["yearly"]["saving"] == 45


def test_economie_de_l_annuel_ne_promet_jamais_plus_que_la_realite(monkeypatch):
    monkeypatch.setattr(config, "PRICE_MONTHLY_FCFA", 6000)
    monkeypatch.setattr(config, "PRICE_YEARLY_FCFA", 36000)                          # exactement 6 mois : 50 %
    yearly = web_common.price_labels()["yearly"]
    assert yearly["months_price"] == "6" and yearly["saving"] == 50
    monkeypatch.setattr(config, "PRICE_YEARLY_FCFA", 36001)                          # un FCFA de plus : 49 %, 6,1 mois
    yearly = web_common.price_labels()["yearly"]
    assert yearly["months_price"] == "6,1" and yearly["saving"] == 49
    for flat in (72000, 71999, 80000):                                               # l'annuel ne ferait rien gagner (ou moins de 1 %)
        monkeypatch.setattr(config, "PRICE_YEARLY_FCFA", flat)
        yearly = web_common.price_labels()["yearly"]
        assert "saving" not in yearly and "months_price" not in yearly, flat           # aucune promesse affichée


def test_le_tarif_de_lancement_s_annonce_jusqu_a_sa_date_puis_disparait(monkeypatch):
    monkeypatch.setattr(config, "LAUNCH_PRICE_UNTIL", datetime.date(2026, 12, 31))
    assert web_common.launch_until_label(datetime.date(2026, 10, 8)) == "31 décembre 2026"
    assert web_common.launch_until_label(datetime.date(2026, 12, 31)) == "31 décembre 2026"      # jusqu'au dernier jour inclus
    assert web_common.launch_until_label(datetime.date(2027, 1, 1)) is None                     # plus aucune mention périmée
    monkeypatch.setattr(config, "LAUNCH_PRICE_UNTIL", None)
    assert web_common.launch_until_label(datetime.date(2026, 10, 8)) is None
    monkeypatch.setattr(config, "LAUNCH_PRICE_UNTIL", datetime.date(2027, 1, 1))
    assert web_common.launch_until_label(datetime.date(2026, 10, 8)) == "1er janvier 2027"


def test_la_page_annonce_le_tarif_de_lancement_tant_qu_il_court(env, monkeypatch):
    today = datetime.datetime.now(datetime.timezone.utc).date()
    monkeypatch.setattr(config, "LAUNCH_PRICE_UNTIL", today + datetime.timedelta(days=30))
    page = text(env.client.get("/paiement")).replace("&nbsp;", " ")
    assert page.count("Tarif de lancement jusqu’au ") == 1                                       # sur le mensuel seulement
    monkeypatch.setattr(config, "LAUNCH_PRICE_UNTIL", today - datetime.timedelta(days=1))
    assert "Tarif de lancement" not in text(env.client.get("/paiement"))
    monkeypatch.setattr(config, "LAUNCH_PRICE_UNTIL", None)
    assert "Tarif de lancement" not in text(env.client.get("/paiement"))


def test_enumeration_a_la_francaise():
    assert web_common.join_fr([]) == "" and web_common.join_fr(["A"]) == "A"
    assert web_common.join_fr(["A", "B"]) == "A et B" and web_common.join_fr(["A", "B", "C"]) == "A, B et C"
    assert web_common.join_fr(["A", "", None, "B"]) == "A et B"


def test_en_tetes_de_securite_de_la_page_de_paiement(env):
    response = env.client.get("/paiement")
    assert response.headers["Content-Security-Policy"] == web_common.PAYMENT_CSP
    # L'adresse /succes contient le jeton : jamais envoyée à un autre site. Pas « no-referrer » : les navigateurs
    # enverraient « Origin: null » sur nos propres formulaires.
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_accueil_robots_et_sante(env):
    assert env.client.get("/").status_code == 302 and env.client.get("/").headers["Location"].endswith("/paiement")
    assert "Disallow: /succes" in text(env.client.get("/robots.txt"))
    assert env.client.get("/health").get_json()["status"] == "ok"
    assert env.client.get("/payer").status_code == 405
    assert env.client.get("/introuvable").status_code == 404


def test_le_pied_de_page_renvoie_vers_les_pages_publiques_du_site_client(env, monkeypatch):
    """Le paiement vit sur une autre adresse que le site client : ses liens vers les résultats et le combiné gratuit
    sont des adresses complètes, et ces pages n'existent pas ici."""
    page = text(env.client.get("/paiement"))
    assert f'href="{config.SITE_URL}/resultats"' in page and f'href="{config.SITE_URL}/gratuit"' in page
    for path in ("/resultats", "/gratuit", "/sitemap.xml"):
        assert env.client.get(path).status_code == 404, path
    monkeypatch.setattr(config, "FREE_PICK_ENABLED", False)
    page = text(env.client.get("/paiement"))
    assert f"{config.SITE_URL}/gratuit" not in page and f'href="{config.SITE_URL}/resultats"' in page


def test_le_site_de_paiement_n_est_pas_une_application_installable(env):
    """Seul le site client est installable (pwa.py) : la page de paiement s'ouvre dans le navigateur, sans manifeste
    ni service worker (qui, sur cette adresse, n'aurait aucune raison de s'occuper des paiements)."""
    page = text(env.client.get("/paiement"))
    for marker in ('rel="manifest"', "apple-touch-icon", "pwa.js", "data-install", "apple-mobile-web-app"):
        assert marker not in page, marker
    for path in ("/manifest.webmanifest", "/sw.js", "/hors-ligne", "/.well-known/assetlinks.json", "/debut"):
        assert env.client.get(path).status_code == 404, path


def test_le_site_de_paiement_a_la_meme_barre_du_bas_avec_des_adresses_completes(env):
    """Même menu que sur le site client ; ici les adresses portent le nom du site (les pages sont sur un autre domaine)."""
    expected = [("Accueil", config.SITE_URL), ("Combiné gratuit", f"{config.SITE_URL}/gratuit"),
                ("Résultats combinés", f"{config.SITE_URL}/resultats"), ("Accès VIP", f"{config.SITE_URL}/login"),
                ("Abonnement VIP", f"{config.PAIEMENT_URL}/paiement")]
    for path in ("/paiement", "/succes", "/page-inconnue"):
        page = text(env.client.get(path))
        nav = re.search(r'<nav class="tabbar".*?</nav>', page, re.S).group(0)
        buttons = re.findall(r'<a class="tabbar__link" href="([^"]+)"( aria-current="page")?>.*?'
                             r'<span class="tabbar__label">([^<]+)</span>', nav, re.S)
        assert [(label, href) for href, _, label in buttons] == expected, path
        assert [label for _, here, label in buttons if here] == (["Abonnement VIP"] if path != "/page-inconnue" else []), path
        header = re.search(r'<header class="topbar">.*?</header>', page, re.S).group(0)
        assert header.count("<a ") == 1 and "<nav" not in header, path


# --------------------------------------------------------------------------
# Création de la facture
# --------------------------------------------------------------------------

def test_achat_mensuel_redirige_vers_paydunya_et_enregistre_la_commande(env):
    response, token = start(env, email="  Client@Exemple.com ")
    assert response.status_code == 303 and response.headers["Location"] == f"https://paydunya.com/checkout/invoice/{token}"
    sent = env.pd.created[0]
    assert sent["amount"] == config.PRICE_MONTHLY_FCFA == 6000
    assert sent["return_url"] == f"{config.PAIEMENT_URL}/succes" and sent["callback_url"] == f"{config.PAIEMENT_URL}/ipn"
    assert sent["cancel_url"].startswith(f"{config.PAIEMENT_URL}/paiement?annule=1")
    assert sent["custom_data"] == {"plan": "monthly", "email": EMAIL}
    assert order_row(env, token) == [(EMAIL, "Mensuel", 1, 0, None, None)]
    assert order_days(env, token) == 30


def test_achat_annuel(env):
    response, token = start(env, plan="yearly")
    assert response.status_code == 303
    assert env.pd.created[0]["amount"] == 39400 and "Annuel" in env.pd.created[0]["name"]
    assert order_row(env, token) == [(EMAIL, "Annuel", 12, 0, None, None)]
    assert order_days(env, token) == 365


def test_achat_du_pass_de_7_jours(env):
    response, token = start(env, plan="weekly")
    assert response.status_code == 303
    sent = env.pd.created[0]
    assert sent["amount"] == config.PRICE_WEEKLY_FCFA == 2000                     # le montant vient du serveur, en FCFA
    assert sent["name"] == "Triple Elite VIP - Pass 7 jours" and sent["custom_data"] == {"plan": "weekly", "email": EMAIL}
    # La commande garde la durée exacte (7 jours) ; « duree » (mois) n'est qu'un repli arrondi vers le haut.
    assert order_row(env, token) == [(EMAIL, "Pass 7 jours", 1, 0, None, None)]
    assert order_days(env, token) == 7


def test_les_prix_sont_ceux_de_la_configuration():
    assert list(PLANS) == ["weekly", "monthly", "yearly"]                          # ordre d'affichage : du plus court au plus long
    assert [PLANS[key]["fcfa"] for key in PLANS] == [config.PRICE_WEEKLY_FCFA, config.PRICE_MONTHLY_FCFA,
                                                     config.PRICE_YEARLY_FCFA] == [2000, 6000, 39400]
    assert [PLANS[key]["days"] for key in PLANS] == [7, 30, 365]
    assert [PLANS[key]["months"] for key in PLANS] == [1, 1, 12]
    # Les mois gardés pour les anciennes versions ne donnent jamais MOINS que la durée vraiment accordée.
    for plan in PLANS.values():
        assert license_manager.duration_days(plan["months"]) >= plan["days"]


def test_plus_on_s_engage_longtemps_moins_le_jour_coute():
    """Garde-fou de la grille de prix : si une modification de config.py rendait le pass moins cher au jour que le
    mensuel (ou le mensuel moins cher que l'annuel), les clients auraient intérêt à acheter la mauvaise offre."""
    per_day = [PLANS[key]["fcfa"] / PLANS[key]["days"] for key in ("weekly", "monthly", "yearly")]
    assert per_day[0] > per_day[1] > per_day[2]


@pytest.mark.parametrize("email", ["", "pas-un-email", "a@b", "a b@c.com", "a@@c.com", "a@c.com, b@c.com",
                                   "<x>@c.com", "x" * 250 + "@c.com"])
def test_e_mail_invalide_refuse_sans_toucher_paydunya(env, email):
    response, token = start(env, email=email)
    assert response.status_code == 400 and token is None
    assert "adresse e-mail ne semble pas valide" in text(response)
    assert env.pd.created == []


def test_sans_la_case_des_conditions_aucune_facture(env):
    for accept in (None, "", "0", "non"):
        response, token = start(env, accept=accept)
        assert response.status_code == 400 and token is None
        assert "Coche la case" in text(response) and "required checked" not in text(response)
    assert env.pd.created == [] and raw(env.path, "SELECT * FROM pending_orders") == []


def test_refuser_les_conditions_n_use_pas_les_tentatives_autorisees(env):
    for _ in range(config.PAYMENT_MAX_PER_EMAIL + 2):
        assert start(env, accept=None)[0].status_code == 400
    assert start(env)[0].status_code == 303                                       # la limite n'a pas été entamée


def test_apres_une_erreur_la_case_cochee_reste_cochee(env):
    page = text(start(env, email="pas-un-email")[0])
    assert "adresse e-mail ne semble pas valide" in page and 'value="1" required checked>' in page
    page = text(start(env, email="pas-un-email", accept=None)[0])
    assert 'value="1" required>' in page                                          # pas cochée : elle ne l'a jamais été
    env.pd.configured = False
    assert 'value="1" required checked>' in text(start(env)[0])


def test_offre_invalide_refusee(env):
    for plan in ("", "gratuit", "yearly; DROP TABLE"):
        response, _ = start(env, plan=plan)
        assert response.status_code == 400 and "Choisis une offre" in text(response)
    assert env.pd.created == []


def test_paydunya_non_configure_ou_en_panne_n_envoie_pas_payer(env):
    env.pd.configured = False
    response, token = start(env)
    assert response.status_code == 503 and token is None and "momentanément indisponible" in text(response)
    env.pd.configured = True
    env.pd.fail_create = PayDunyaError("refusé")
    response, token = start(env)
    assert response.status_code == 503 and raw(env.path, "SELECT * FROM pending_orders") == []
    # La page redonne le formulaire avec l'e-mail déjà saisi et l'offre choisie.
    assert f'value="{EMAIL}"' in text(response)


def test_commande_non_enregistree_le_client_n_est_pas_envoye_payer(env, monkeypatch):
    monkeypatch.setattr(env.lm, "create_pending_order", lambda *a, **k: False)
    response, token = start(env)
    assert response.status_code == 503 and token is None and "momentanément indisponible" in text(response)


def test_limite_de_tentatives_par_e_mail(env):
    for _ in range(config.PAYMENT_MAX_PER_EMAIL):
        assert start(env)[0].status_code == 303
    response, _ = start(env)
    assert response.status_code == 429 and "Trop de tentatives" in text(response)
    assert len(env.pd.created) == config.PAYMENT_MAX_PER_EMAIL
    assert start(env, email="autre@exemple.com")[0].status_code == 303                # un autre client n'est pas gêné


def test_limite_de_tentatives_par_adresse_ip(env):
    ip = {"environ_base": {"REMOTE_ADDR": "203.0.113.9"}}
    for n in range(config.PAYMENT_MAX_PER_IP):
        assert start(env, email=f"client{n}@exemple.com", **ip)[0].status_code == 303
    assert start(env, email="encore@exemple.com", **ip)[0].status_code == 429
    assert start(env, email="encore@exemple.com", environ_base={"REMOTE_ADDR": "198.51.100.7"})[0].status_code == 303


def test_les_formulaires_d_autres_sites_sont_refuses(env):
    response = env.client.post("/payer", data={"email": EMAIL, "plan": "monthly"}, headers={"Origin": "https://pirate.example"})
    assert response.status_code == 403 and env.pd.created == []
    response = env.client.post("/payer", data={"email": EMAIL, "plan": "monthly", "accept": "1"},
                               headers={"Origin": config.PAIEMENT_URL})
    assert response.status_code == 303


# --------------------------------------------------------------------------
# Retour du client : /succes
# --------------------------------------------------------------------------

def test_retour_apres_paiement_delivre_et_affiche_la_licence(env):
    _, token = start(env)
    env.pd.pay(token)
    response = env.client.get(f"/succes?token={token}")
    page = text(response)
    assert response.status_code == 200 and "Paiement confirmé" in page
    (email, plan, duree, processed, key, renewed), = order_row(env, token)
    assert email == EMAIL and processed == 1 and renewed == 0 and len(key) == 16
    assert key in page and EMAIL in page and 'id="copy-key"' in page
    # La licence marche vraiment : 30 jours, connexion possible avec cet e-mail et cette clé.
    assert env.lm.verify_license(EMAIL, key) == (True, "Licence valide")
    status = env.lm.get_status(EMAIL)
    assert datetime.timedelta(days=29, hours=23) < status["expires"] - license_manager._utcnow() <= datetime.timedelta(days=30)
    assert env.mailer.sent == [{"to": EMAIL, "key": key, "plan": "Mensuel", "expires": status["expires"], "renewed": False}]
    assert f"{config.SITE_URL}/login" in page


def test_abonnement_annuel_dure_365_jours(env):
    _, token = start(env, plan="yearly")
    env.pd.pay(token)
    assert env.client.get(f"/succes?token={token}").status_code == 200
    delta = env.lm.get_status(EMAIL)["expires"] - license_manager._utcnow()
    assert datetime.timedelta(days=364, hours=23) < delta <= datetime.timedelta(days=365)


def test_pass_de_7_jours_dure_7_jours_et_l_e_mail_le_nomme(env):
    _, token = start(env, plan="weekly")
    env.pd.pay(token)
    page = text(env.client.get(f"/succes?token={token}"))
    assert "Paiement confirmé" in page
    status = env.lm.get_status(EMAIL)
    assert datetime.timedelta(days=6, hours=23) < status["expires"] - license_manager._utcnow() <= datetime.timedelta(days=7)
    assert env.mailer.sent == [{"to": EMAIL, "key": order_row(env, token)[0][4], "plan": "Pass 7 jours",
                                "expires": status["expires"], "renewed": False}]


def test_pass_de_7_jours_s_ajoute_a_un_abonnement_en_cours(env):
    first = env.lm.issue_license(EMAIL, 1)
    before = first["expires"]
    _, token = start(env, plan="weekly")
    env.pd.pay(token)
    page = text(env.client.get(f"/succes?token={token}"))
    assert "Abonnement prolongé" in page and first["key"] not in page
    assert env.lm.get_status(EMAIL)["expires"] == before + datetime.timedelta(days=7)
    assert env.mailer.sent[0]["renewed"] is True and env.mailer.sent[0]["key"] == first["key"]


def test_commande_enregistree_avant_le_pass_est_delivree_selon_ses_mois(env):
    """Un paiement en cours pendant la mise à jour (commande sans durée en jours) donne la durée qui était annoncée."""
    _, token = start(env, plan="yearly")
    raw(env.path, "UPDATE pending_orders SET jours = NULL WHERE token = ?", (token,))
    assert order_days(env, token) is None
    env.pd.pay(token)
    assert env.client.get(f"/succes?token={token}").status_code == 200
    delta = env.lm.get_status(EMAIL)["expires"] - license_manager._utcnow()
    assert datetime.timedelta(days=364, hours=23) < delta <= datetime.timedelta(days=365)


def test_actualiser_la_page_ne_delivre_qu_une_licence_et_un_seul_e_mail(env):
    _, token = start(env)
    env.pd.pay(token)
    first = text(env.client.get(f"/succes?token={token}"))
    key = order_row(env, token)[0][4]
    for _ in range(3):
        again = text(env.client.get(f"/succes?token={token}"))
        assert key in again and "Paiement confirmé" in again
    assert len(env.mailer.sent) == 1 and len(env.lm.list_licenses()) == 1
    assert len(env.pd.confirm_calls) == 1                         # PayDunya n'est plus interrogé une fois la commande traitée
    assert key in first


def test_renouvellement_garde_la_cle_et_ne_l_affiche_pas(env):
    first = env.lm.issue_license(EMAIL, 1)
    before = first["expires"]
    _, token = start(env, plan="yearly")
    env.pd.pay(token)
    page = text(env.client.get(f"/succes?token={token}"))
    assert "Abonnement prolongé" in page and first["key"] not in page          # la clé n'est jamais affichée à un tiers
    assert "ne change pas" in page and f"{config.SITE_URL}/app" in page
    status = env.lm.get_status(EMAIL)
    assert status["expires"] == before + datetime.timedelta(days=365)
    assert order_row(env, token)[0][4:] == (first["key"], 1)
    assert env.mailer.sent[0]["renewed"] is True and env.mailer.sent[0]["key"] == first["key"]
    assert first["key"] not in text(env.client.get(f"/succes?token={token}"))   # ni au retour suivant
    assert len(env.mailer.sent) == 1


def test_licence_expiree_recoit_une_nouvelle_cle(env):
    old = env.lm.issue_license(EMAIL, 1)
    raw(env.path, "UPDATE licenses SET expires = ?", (str(license_manager._utcnow() - datetime.timedelta(days=2)),))
    _, token = start(env)
    env.pd.pay(token)
    page = text(env.client.get(f"/succes?token={token}"))
    key = order_row(env, token)[0][4]
    assert key != old["key"] and key in page and "Paiement confirmé" in page
    assert env.lm.verify_license(EMAIL, old["key"])[0] is False and env.lm.verify_license(EMAIL, key)[0] is True


def test_paiement_en_attente_la_page_se_recharge_toute_seule(env):
    _, token = start(env)
    response = env.client.get(f"/succes?token={token}")
    page = text(response)
    assert response.status_code == 200 and "Confirmation en cours" in page
    next_page = f"/succes?token={token}&amp;n=1"
    assert f'data-refresh-url="{next_page}" data-refresh-seconds="{config.SUCCESS_REFRESH_SECONDS}"' in page   # avec JavaScript
    assert f'<noscript><meta http-equiv="refresh" content="{config.SUCCESS_REFRESH_SECONDS};url={next_page}"></noscript>' in page
    assert '<meta http-equiv="refresh"' not in page.replace('<noscript><meta http-equiv="refresh"', "")   # jamais hors <noscript>
    assert order_row(env, token)[0][3] == 0 and env.mailer.sent == [] and env.lm.list_licenses() == []
    # Le client paie : l'actualisation suivante délivre la licence.
    env.pd.pay(token)
    assert "Paiement confirmé" in text(env.client.get(f"/succes?token={token}&n=1"))


def test_paiement_en_attente_trop_long_la_page_cesse_de_se_recharger(env):
    _, token = start(env)
    page = text(env.client.get(f"/succes?token={token}&n={config.SUCCESS_REFRESH_MAX}"))
    assert "http-equiv" not in page and "data-refresh-url" not in page
    assert "prend plus de temps" in page and "Ne paie pas une seconde fois" in page
    assert "Actualiser" in page and "c***@exemple.com" in page and EMAIL not in page


@pytest.mark.parametrize("status", ["cancelled", "failed", "expired"])
def test_paiement_annule_ou_refuse_ne_delivre_rien(env, status):
    _, token = start(env)
    env.pd.cancel(token, status)
    response = env.client.get(f"/succes?token={token}")
    page = text(response)
    assert response.status_code == 200 and "Paiement non abouti" in page and token in page
    assert "/paiement" in page and env.lm.list_licenses() == [] and env.mailer.sent == []
    assert order_row(env, token)[0][3] == 0                       # le client peut encore payer cette facture


def test_paydunya_injoignable_au_retour_puis_retabli(env):
    _, token = start(env)
    env.pd.pay(token)
    env.pd.fail_confirm = PayDunyaError("injoignable", transient=True)
    response = env.client.get(f"/succes?token={token}")
    assert response.status_code == 503 and "data-refresh-url" in text(response)
    assert env.lm.list_licenses() == []                           # jamais de licence sans confirmation
    env.pd.fail_confirm = None
    assert "Paiement confirmé" in text(env.client.get(f"/succes?token={token}&n=1"))


def test_jeton_inconnu_ou_mal_forme(env):
    response = env.client.get("/succes?token=inconnu1234")
    assert response.status_code == 404 and "Commande introuvable" in text(response) and env.pd.confirm_calls == []
    response = env.client.get("/succes?token=%3Cscript%3Ealert(1)%3C/script%3E")
    assert response.status_code == 404 and "<script>alert" not in text(response)
    response = env.client.get("/succes")
    assert response.status_code == 200 and "e-mail" in text(response) and env.pd.confirm_calls == []


def test_base_en_panne_au_retour_ne_dit_pas_commande_introuvable(env, monkeypatch):
    _, token = start(env)
    env.pd.pay(token)

    def broken(*a, **k):
        raise OSError("base injoignable")

    monkeypatch.setattr(env.lm, "get_pending_order", broken)
    response = env.client.get(f"/succes?token={token}")
    assert response.status_code == 503 and "introuvable" not in text(response) and "Vérification en cours" in text(response)
    monkeypatch.undo()
    assert "Paiement confirmé" in text(env.client.get(f"/succes?token={token}"))


def test_echec_de_l_emission_de_la_licence_libere_la_commande(env, monkeypatch):
    _, token = start(env)
    env.pd.pay(token)
    real_issue = env.lm.issue_license
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("base injoignable pendant l'écriture")
        return real_issue(*args, **kwargs)

    monkeypatch.setattr(env.lm, "issue_license", flaky)
    response = env.client.get(f"/succes?token={token}")
    assert response.status_code == 503 and env.mailer.sent == [] and env.lm.list_licenses() == []
    assert order_row(env, token)[0][3] == 0                       # libérée : le prochain passage réessaie
    assert "Paiement confirmé" in text(env.client.get(f"/succes?token={token}&n=1"))
    assert len(env.mailer.sent) == 1 and len(env.lm.list_licenses()) == 1


def test_echec_de_l_e_mail_n_empeche_pas_d_afficher_la_licence(env):
    env.mailer.error = RuntimeError("SMTP bloqué")
    _, token = start(env)
    env.pd.pay(token)
    page = text(env.client.get(f"/succes?token={token}"))
    key = order_row(env, token)[0][4]
    assert "Paiement confirmé" in page and key in page


def test_cle_non_rattachee_a_la_commande_s_affiche_quand_meme_et_l_incident_est_journalise(env, monkeypatch, caplog):
    _, token = start(env)
    env.pd.pay(token)
    monkeypatch.setattr(env.lm, "complete_order", lambda *args, **kwargs: False)    # la base lâche à ce moment précis
    with caplog.at_level("ERROR", logger="paiement"):
        page = text(env.client.get(f"/succes?token={token}"))
    assert "Paiement confirmé" in page                                   # le client voit sa clé cette fois-ci...
    assert len(env.mailer.sent) == 1 and env.mailer.sent[0]["key"] in page   # ...et la reçoit aussi par e-mail
    assert "clé non rattachée à la commande" in caplog.text               # ...et l'incident est visible dans les journaux


def test_retour_du_navigateur_et_notification_simultanes_une_seule_licence(env):
    _, token = start(env)
    env.pd.pay(token)
    barrier = threading.Barrier(6)
    results = []

    def visit(kind):
        client = env.app.test_client()
        barrier.wait()
        if kind == "ipn":
            results.append(client.post("/ipn", data={"data[invoice][token]": token}).status_code)
        else:
            results.append(client.get(f"/succes?token={token}").status_code)

    threads = [threading.Thread(target=visit, args=(kind,)) for kind in ("web", "ipn", "web", "ipn", "web", "web")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(code in (200, 503) for code in results)
    assert len(env.lm.list_licenses()) == 1 and len(env.mailer.sent) == 1
    final = env.client.get(f"/succes?token={token}")
    assert final.status_code == 200 and env.mailer.sent[0]["key"] in text(final)


# --------------------------------------------------------------------------
# Notification IPN de PayDunya
# --------------------------------------------------------------------------

def test_notification_delivre_la_licence_meme_si_le_client_a_ferme_son_navigateur(env):
    _, token = start(env)
    env.pd.pay(token)
    response = env.client.post("/ipn", data={"data[invoice][token]": token, "data[status]": "completed",
                                             "data[hash]": env.pd.good_hash})
    assert response.status_code == 200
    assert len(env.mailer.sent) == 1 and len(env.lm.list_licenses()) == 1
    # Le client revient plus tard : sa clé est toujours là, sans second e-mail.
    page = text(env.client.get(f"/succes?token={token}"))
    assert env.mailer.sent[0]["key"] in page and len(env.mailer.sent) == 1


def test_notification_au_format_json(env):
    _, token = start(env)
    env.pd.pay(token)
    response = env.client.post("/ipn", json={"data": {"invoice": {"token": token}, "status": "completed"}})
    assert response.status_code == 200 and len(env.mailer.sent) == 1


def test_fausse_notification_ne_donne_pas_de_licence(env):
    _, token = start(env)                                          # jamais payée chez PayDunya
    response = env.client.post("/ipn", data={"data[invoice][token]": token, "data[status]": "completed",
                                             "data[hash]": "forge"})
    assert response.status_code == 200
    assert env.lm.list_licenses() == [] and env.mailer.sent == [] and order_row(env, token)[0][3] == 0
    assert env.pd.confirm_calls == [token]                         # la décision vient de PayDunya, pas de la notification


def test_notifications_sans_interet_sont_ignorees_sans_appeler_paydunya(env):
    for kwargs in ({}, {"data": {"x": "y"}}, {"data": {"data[invoice][token]": "inconnu1234"}},
                   {"data": {"data[invoice][token]": "x y; DROP"}}):
        assert env.client.post("/ipn", **kwargs).status_code == 200
    assert env.pd.confirm_calls == [] and env.lm.list_licenses() == []


def test_notification_pendant_une_panne_demande_a_paydunya_de_reessayer(env):
    _, token = start(env)
    env.pd.pay(token)
    env.pd.fail_confirm = PayDunyaError("injoignable", transient=True)
    assert env.client.post("/ipn", data={"data[invoice][token]": token}).status_code == 503
    env.pd.fail_confirm = None
    assert env.client.post("/ipn", data={"data[invoice][token]": token}).status_code == 200
    assert len(env.mailer.sent) == 1


def test_notification_acceptee_malgre_l_absence_d_origine_navigateur(env):
    _, token = start(env)
    env.pd.pay(token)
    response = env.client.post("/ipn", data={"data[invoice][token]": token}, headers={"Origin": "https://app.paydunya.com"})
    assert response.status_code == 200 and len(env.mailer.sent) == 1


def test_journaux_sans_e_mail_en_clair(env, caplog):
    caplog.set_level("INFO")
    _, token = start(env)
    env.pd.pay(token)
    env.client.get(f"/succes?token={token}")
    assert "c***@exemple.com" in caplog.text and EMAIL not in caplog.text
    assert token not in caplog.text                                # seul le début du jeton est journalisé


# --------------------------------------------------------------------------
# Adresse de notification enregistrée chez PayDunya
# --------------------------------------------------------------------------

def test_l_adresse_enregistree_chez_paydunya_fonctionne_comme_ipn(env):
    _, token = start(env)
    env.pd.pay(token)
    response = env.client.post("/ipn-paydunya", data={"data[invoice][token]": token},
                               headers={"Origin": "https://app.paydunya.com"})
    assert response.status_code == 200 and len(env.mailer.sent) == 1 and len(env.lm.list_licenses()) == 1
    assert "Disallow: /ipn-paydunya" in text(env.client.get("/robots.txt"))


# --------------------------------------------------------------------------
# Mode test PayDunya : clés de test, paiements fictifs, réservés aux adresses de test
# --------------------------------------------------------------------------

def test_le_bandeau_mode_test_n_apparait_qu_en_mode_test(env, env_test):
    assert "Mode test" not in text(env.client.get("/paiement"))
    page = text(env_test.client.get("/paiement"))
    assert "Mode test" in page and "Aucun vrai paiement" in page


def test_en_mode_test_un_client_ne_peut_pas_commander(env_test, caplog):
    with caplog.at_level("WARNING", logger="paiement"):
        response, token = start(env_test, email=EMAIL)
    assert response.status_code == 503 and token is None
    assert "ne sont pas encore ouverts" in text(response) and "Mode test" in text(response)
    assert env_test.pd.created == [] and raw(env_test.path, "SELECT * FROM pending_orders") == []
    assert "mode test : adresse non autorisée" in caplog.text and EMAIL not in caplog.text


def test_en_mode_test_le_proprietaire_va_jusqu_a_la_licence(env_test):
    response, token = start(env_test, email=OWNER.upper())            # casse et espaces ignorés des deux côtés
    assert response.status_code == 303 and token.startswith("test_")
    assert response.headers["Location"] == f"https://paydunya.com/sandbox-checkout/invoice/{token}"
    env_test.pd.pay(token)
    assert "Paiement confirmé" in text(env_test.client.get(f"/succes?token={token}"))
    assert [mail["to"] for mail in env_test.mailer.sent] == [OWNER]
    assert env_test.lm.get_status(OWNER)["state"] == "active"


def test_en_mode_test_sans_adresse_autorisee_tout_est_refuse(tmp_path):
    site = build_env(tmp_path, test_mode=True, test_emails=[])
    response, token = start(site, email=OWNER)
    assert response.status_code == 503 and token is None and site.pd.created == []


def test_une_facture_de_test_inattendue_n_est_ni_enregistree_ni_payee(env, caplog):
    env.pd.token_prefix = "test_"          # PayDunya répond par une facture de test alors que le site se croit en production
    with caplog.at_level("ERROR", logger="paiement"):
        response, token = start(env)
    assert response.status_code == 503 and token is None and "ne sont pas encore ouverts" in text(response)
    assert raw(env.path, "SELECT * FROM pending_orders") == [] and env.mailer.sent == []
    assert "facture de TEST pour une adresse non autorisée" in caplog.text
    assert start(env, email=OWNER)[0].status_code == 503                      # même sans liste de test : refusé


def test_la_sante_indique_le_mode_paydunya(env, env_test):
    assert env.client.get("/health").get_json() == {"status": "ok", "version": config.VERSION, "paydunya": "live"}
    assert env_test.client.get("/health").get_json()["paydunya"] == "test"
    env.pd.configured = False
    assert env.client.get("/health").get_json()["paydunya"] == "absent"


def test_la_sante_repond_meme_si_le_complement_echoue():
    def broken():
        raise RuntimeError("panne")

    probe = Flask("sante")
    web_common.install_health_and_robots(probe, robots_txt="", health_extra=broken)
    response = probe.test_client().get("/health")
    assert response.status_code == 200 and response.get_json() == {"status": "ok", "version": config.VERSION}


def test_les_journaux_ne_montrent_jamais_un_jeton_entier():
    from paiement import short
    for token in ("abcdef", "abcdefghij", "test_AbCdEf1234", "x" * 80):
        shown = short(token).rstrip("…")
        assert token.startswith(shown) and len(shown) < len(token) and len(shown) <= 6
