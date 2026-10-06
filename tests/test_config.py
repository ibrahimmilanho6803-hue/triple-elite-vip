"""Lecture du fichier .env (développement local)."""
import os

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
