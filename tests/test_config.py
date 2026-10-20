"""Lecture du fichier .env (développement local) et adresses publiques par défaut."""
import os
import subprocess
import sys
from pathlib import Path

import config


def test_chargement_du_fichier_env(tmp_path, monkeypatch):
    for name in ("TEV_A", "TEV_B", "TEV_C", "TEV_D", "TEV_E", "TEV_DEJA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TEV_DEJA", "valeur-existante")
    path = tmp_path / ".env"
    path.write_text("\n".join([
        "# commentaire", "", "TEV_A=simple", 'TEV_B="avec des espaces"', "TEV_C='guillemets simples'",
        "export TEV_D=exporte  ", "TEV_E=valeur # commentaire en fin de ligne", "TEV_DEJA=ecrase", "ligne sans egal",
        "=sans_nom", "TEV_VIDE="]), encoding="utf-8")
    assert config.load_env_file(str(path)) == 5
    assert os.environ["TEV_A"] == "simple" and os.environ["TEV_B"] == "avec des espaces"
    assert os.environ["TEV_C"] == "guillemets simples" and os.environ["TEV_D"] == "exporte"
    assert os.environ["TEV_E"] == "valeur"
    assert os.environ["TEV_DEJA"] == "valeur-existante"            # une variable déjà définie n'est jamais écrasée
    assert "TEV_VIDE" not in os.environ                             # « CLE= » ne définit rien (ex. SPORTSDB_API_KEY garde sa valeur par défaut)
    for name in ("TEV_A", "TEV_B", "TEV_C", "TEV_D", "TEV_E"):
        monkeypatch.delenv(name)


def test_fichier_env_absent(tmp_path):
    assert config.load_env_file(str(tmp_path / "inexistant.env")) == 0


def test_moyens_de_paiement_annonces_sont_bien_formes():
    countries = config.PAYMENT_COUNTRIES
    assert isinstance(config.CARDS_ENABLED, bool)
    assert countries and len(set(countries)) == len(countries)
    assert all(name and name == name.strip() and "'" not in name for name in countries)    # apostrophe typographique


def test_les_prix_sont_bien_formes():
    """Montants entiers et positifs en FCFA (PayDunya ne facture pas de centimes), un taux de change utilisable, et une date
    de fin de tarif de lancement qui est une vraie date (ou rien) : sinon les pages ne pourraient plus s'afficher."""
    import datetime
    for price in (config.PRICE_WEEKLY_FCFA, config.PRICE_MONTHLY_FCFA, config.PRICE_YEARLY_FCFA):
        assert isinstance(price, int) and not isinstance(price, bool) and price >= 100, price
    assert config.PRICE_WEEKLY_FCFA < config.PRICE_MONTHLY_FCFA < config.PRICE_YEARLY_FCFA
    assert 650 < config.FCFA_PER_EUR < 660                                     # taux fixe 1 € = 655,957 FCFA
    assert config.LAUNCH_PRICE_UNTIL is None or isinstance(config.LAUNCH_PRICE_UNTIL, datetime.date)


def test_une_date_de_lancement_avec_heure_ne_fait_pas_planter_les_pages(monkeypatch):
    import datetime
    import web_common
    monkeypatch.setattr(config, "LAUNCH_PRICE_UNTIL", datetime.datetime(2026, 12, 31, 18, 30))
    assert web_common.launch_until_label(datetime.date(2026, 12, 31)) == "31 décembre 2026"
    assert web_common.launch_until_label(datetime.date(2027, 1, 1)) is None


def test_lecture_d_un_nombre_decimal_de_l_environnement():
    parse = config.parse_float
    assert parse("1.30", 1.25, 1.0, 2.0) == 1.3 and parse(" 1,4 ", 1.25, 1.0, 2.0) == 1.4 and parse("2", 1.25, 1.0, 2.0) == 2.0
    # Une faute de frappe ne doit jamais fausser les cotes : valeur illisible ou hors bornes -> la valeur par défaut.
    for raw in (None, "", "abc", "1.2.3", "0.5", "0", "-3", "9", "nan", "inf"):
        assert parse(raw, 1.25, 1.0, 2.0) == 1.25, raw


def test_cote_minimale_par_pari():
    assert config.MIN_LEG_ODDS == 1.25                      # rien dans l'environnement des tests
    assert 1.0 <= config.MIN_LEG_ODDS <= 2.0


def test_penalite_de_repetition_des_genres_de_pari():
    assert config.GENRE_REPEAT_PENALTY == 8.0               # rien dans l'environnement des tests
    assert read_config("config.GENRE_REPEAT_PENALTY") == "8.0"
    assert read_config("config.GENRE_REPEAT_PENALTY", GENRE_REPEAT_PENALTY="5") == "5.0"
    assert read_config("config.GENRE_REPEAT_PENALTY", GENRE_REPEAT_PENALTY="0") == "0.0"        # 0 : aucune variété
    assert read_config("config.GENRE_REPEAT_PENALTY", GENRE_REPEAT_PENALTY="12,5") == "12.5"    # virgule française
    for raw in ("", "abc", "-1", "999", "nan"):                                                  # illisible ou hors bornes : le défaut
        assert read_config("config.GENRE_REPEAT_PENALTY", GENRE_REPEAT_PENALTY=raw) == "8.0", raw


def test_bookmaker_de_reference_lu_dans_un_texte():
    parse = config.parse_bookmaker
    assert parse(None, "onexbet") == "onexbet"                                       # absent : le défaut
    assert parse("", "onexbet") == "" and parse("   ", "onexbet") == ""              # vide : aucun bookmaker de référence
    assert parse("1xBet", "onexbet") == parse(" 1XBET ", "onexbet") == parse("OneXBet", "onexbet") == "onexbet"
    assert parse("Pinnacle", "onexbet") == "pinnacle"                                # une autre clé the-odds-api est gardée telle quelle


def test_le_bookmaker_de_reference_est_1xbet_par_defaut_et_se_change_par_variable_d_environnement():
    assert config.ODDS_BOOKMAKER == "onexbet" and config.ODDS_BOOKMAKER_LABEL == "1xBet"      # rien dans l'environnement des tests
    assert read_config("config.ODDS_BOOKMAKER") == "'onexbet'"
    assert read_config("config.ODDS_BOOKMAKER", ODDS_BOOKMAKER="1xBet") == "'onexbet'"
    assert read_config("config.ODDS_BOOKMAKER", ODDS_BOOKMAKER="") == "''"                   # vide : aucun, la médiane fait foi
    assert read_config("config.ODDS_BOOKMAKER_LABEL", ODDS_BOOKMAKER="pinnacle") == "'pinnacle'"


def test_liste_d_adresses_de_test():
    assert config.parse_email_list(" A@x.com, b@y.com;C@Z.com  d@w.org ,, ") == {"a@x.com", "b@y.com", "c@z.com", "d@w.org"}
    assert config.parse_email_list("") == frozenset() and config.parse_email_list(None) == frozenset()
    assert config.PAYDUNYA_TEST_EMAILS == frozenset()               # rien dans l'environnement des tests


def default_urls(**environment):
    """(SITE_URL, PAIEMENT_URL) lus par un Python neuf : config lit l'environnement au chargement."""
    env = {k: v for k, v in os.environ.items() if k not in ("SITE_URL", "PAIEMENT_BASE_URL")}
    env.update(TEV_NO_DOTENV="1", **environment)
    code = "import sys; sys.path.insert(0, '.'); import config; print(config.SITE_URL, config.PAIEMENT_URL)"
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parent.parent, env=env,
                            capture_output=True, text=True, check=True)
    return tuple(result.stdout.split())


def test_les_deux_sites_ont_une_adresse_du_domaine_de_la_marque_par_defaut():
    # Le client voit « paiement.triple-elite-vip.com », pas l'adresse technique du service chez Render.
    assert default_urls() == ("https://triple-elite-vip.com", "https://paiement.triple-elite-vip.com")


def test_les_adresses_publiques_peuvent_etre_changees_par_variable_d_environnement():
    urls = default_urls(PAIEMENT_BASE_URL="https://autre.exemple.org/", SITE_URL="https://site.exemple.org//")
    assert urls == ("https://site.exemple.org", "https://autre.exemple.org")        # sans « / » final


def read_config(expression, **environment):
    """Valeur d'une expression de config lue par un Python neuf (config lit l'environnement au chargement)."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("FREE_PICK_ENABLED", "AUTO_GENERATE_HOUR", "PUBLIC_PAGE_CACHE_SECONDS")}
    env.update(TEV_NO_DOTENV="1", **environment)
    code = f"import sys; sys.path.insert(0, '.'); import config; print(repr({expression}))"
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parent.parent, env=env,
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def test_heure_de_generation_automatique_lue_dans_un_texte():
    for raw, expected in ((None, None), ("", None), ("  ", None), ("abc", None), ("-1", None), ("24", None),
                          ("6.5", None), ("0", 0), ("6", 6), (" 7 ", 7), ("23", 23)):
        assert config.parse_hour(raw) == expected, raw


def test_la_generation_automatique_est_desactivee_par_defaut():
    # Chaque génération est un appel payant à l'IA : elle ne part jamais sans que le propriétaire l'ait demandé.
    assert config.AUTO_GENERATE_HOUR is None
    assert read_config("config.AUTO_GENERATE_HOUR") == "None"
    assert read_config("config.AUTO_GENERATE_HOUR", AUTO_GENERATE_HOUR="6") == "6"
    assert read_config("config.AUTO_GENERATE_HOUR", AUTO_GENERATE_HOUR="plus tard") == "None"
    assert 1 <= config.AUTO_GENERATE_MAX_ATTEMPTS <= 5


def test_le_combine_gratuit_est_actif_par_defaut_et_se_coupe_par_variable_d_environnement():
    assert read_config("config.FREE_PICK_ENABLED") == "True"
    for off in ("0", "false", "NON", "Off", "no"):
        assert read_config("config.FREE_PICK_ENABLED", FREE_PICK_ENABLED=off) == "False", off
    for on in ("1", "oui", "true"):
        assert read_config("config.FREE_PICK_ENABLED", FREE_PICK_ENABLED=on) == "True", on


def test_duree_en_secondes_lue_dans_un_texte():
    for raw, expected in ((None, 60), ("", 60), ("  ", 60), ("abc", 60), ("-1", 60), ("601", 60), ("6.5", 60),
                          ("0", 0), ("30", 30), (" 90 ", 90), ("600", 600)):
        assert config.parse_seconds(raw, 60) == expected, raw
    assert config.parse_seconds("7", 60, maximum=5) == 60


def test_les_pages_publiques_sont_gardees_une_minute_et_cela_se_coupe_par_variable_d_environnement():
    # Sans réglage : 60 s. PUBLIC_PAGE_CACHE_SECONDS=0 (Render > Environment) retire mémorisation et préchargement.
    assert read_config("config.PUBLIC_PAGE_CACHE_SECONDS") == "60"
    assert read_config("config.PUBLIC_PAGE_CACHE_SECONDS", PUBLIC_PAGE_CACHE_SECONDS="0") == "0"
    assert read_config("config.PUBLIC_PAGE_CACHE_SECONDS", PUBLIC_PAGE_CACHE_SECONDS="120") == "120"
    assert read_config("config.PUBLIC_PAGE_CACHE_SECONDS", PUBLIC_PAGE_CACHE_SECONDS="longtemps") == "60"


# --------------------------------------------------------------------------
# Les douze compétitions suivies
# --------------------------------------------------------------------------

NEW_LEAGUES = {"Ligue des champions": "4480", "Premier-Liga (Russie)": "4355", "Eredivisie (Pays-Bas)": "4337",
               "Primeira Liga (Portugal)": "4344", "Pro League (Belgique)": "4338", "Süper Lig (Turquie)": "4339",
               "Superliga (Danemark)": "4340"}


def test_douze_competitions_les_cinq_historiques_puis_les_sept_ajoutees():
    assert len(config.LEAGUES) == 12
    assert list(config.LEAGUES)[:5] == ["Premier League", "La Liga", "Bundesliga", "Ligue 1", "Serie A"]
    for name, league_id in NEW_LEAGUES.items():
        assert config.LEAGUES[name] == league_id, name
    ids = list(config.LEAGUES.values())
    assert len(set(ids)) == len(ids) and all(i.isdigit() for i in ids)           # un identifiant TheSportsDB par compétition


def test_chaque_competition_a_sa_carte_d_accueil_et_son_marche_de_cotes():
    from combo_generator import ComboGenerator
    assert set(config.LEAGUE_CARDS) == set(config.LEAGUES)                      # aucune compétition oubliée sur la page d'accueil
    assert set(ComboGenerator.LEAGUE_TO_ODDS_SPORT) == set(config.LEAGUES)      # ni dans la recherche des vraies cotes
    sports = list(ComboGenerator.LEAGUE_TO_ODDS_SPORT.values())
    assert len(set(sports)) == len(sports) and all(sport.startswith("soccer_") for sport in sports)
    for short, country in config.LEAGUE_CARDS.values():
        assert short and country and short == short.strip() and country == country.strip()


def test_la_superliga_du_danemark():
    """Identifiant TheSportsDB (4340, « Danish Superliga ») et sport the-odds-api (« Denmark Superliga ») vérifiés en ligne."""
    from combo_generator import ComboGenerator
    assert config.LEAGUES["Superliga (Danemark)"] == "4340"
    assert config.LEAGUE_CARDS["Superliga (Danemark)"] == ("Superliga", "Danemark")
    assert ComboGenerator.LEAGUE_TO_ODDS_SPORT["Superliga (Danemark)"] == "soccer_denmark_superliga"


def test_les_reglages_de_variete_et_de_partage_sont_utilisables():
    assert config.MATCHS_PAR_GENERATION >= 3 * config.MAX_COMBOS_RETOURNES      # assez de matchs pour trois combinés de trois
    assert config.VARIETY_SEEN_PENALTY > config.VARIETY_SERVED_PENALTY >= 0
    assert config.ANALYSIS_CACHE_MINUTES * 60 > config.MATCHDAY_CACHE_SECONDS and config.AI_RETRY_AFTER_SECONDS > 0
    assert config.MAX_GENERATIONS_PAR_JOUR >= config.CLIENT_GENERATIONS_PER_DAY
    assert not hasattr(config, "MATCHS_PAR_CHAMPIONNAT")                         # remplacé par MATCHS_PAR_GENERATION


def test_la_cle_thesportsdb_par_defaut_est_la_cle_de_test_publique():
    assert read_config("config.SPORTSDB_API_KEY", SPORTSDB_API_KEY="") == "'123'"        # l'ancienne « 3 » est désactivée
    assert read_config("config.SPORTSDB_API_KEY", SPORTSDB_API_KEY="cle-de-test") == "'cle-de-test'"
