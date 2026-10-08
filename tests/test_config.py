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
    env = {k: v for k, v in os.environ.items() if k not in ("FREE_PICK_ENABLED", "AUTO_GENERATE_HOUR")}
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
