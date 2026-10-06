"""Outils en ligne de commande : affichage des combinés, gestion manuelle des licences."""
import datetime
import json

import pytest

import generate_keys
import license_manager
import main
from fakes import sqlite_license_manager
from site_fakes import make_pipeline, sample_combos


def test_affichage_d_un_combine():
    combo = sample_combos()[1]                                     # contient des cotes réelles et une cote estimée
    text = main.format_combo(2, combo)
    assert text.startswith("COMBINÉ 2 : cote 2.70, chance estimée 30 %")
    assert "Bayern Munich - Mainz (Bundesliga)" in text
    assert "cote 1.38" in text and "cote ≈1.45" in text            # « ≈ » seulement devant une cote estimée


def test_generation_en_ligne_de_commande(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")
    monkeypatch.setattr(main, "run_pipeline", make_pipeline(delay=0))
    monkeypatch.setattr(main.config, "RESULTS_DIR", str(tmp_path / "results"))
    assert main.main(["--save"]) == 0
    out, err = capsys.readouterr()
    assert "3 combinés générés" in out and "COMBINÉ 3" in out and "15/15 matchs analysés" in out
    saved = list((tmp_path / "results").glob("combo_*.json"))
    assert len(saved) == 1 and len(json.loads(saved[0].read_text(encoding="utf-8"))) == 3
    assert main.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["meta"]["matches_total"] == 15


def test_generation_sans_cle_ia_ou_en_echec(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main.main([]) == 2 and "ANTHROPIC_API_KEY" in capsys.readouterr().err
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")

    def failing(progress=None):
        raise main.GenerationError("Pas assez de matchs à venir pour le moment. Réessaie plus tard.")

    monkeypatch.setattr(main, "run_pipeline", failing)
    assert main.main([]) == 1 and "Pas assez de matchs" in capsys.readouterr().err


def test_description_d_une_licence():
    soon = license_manager._utcnow() + datetime.timedelta(days=12, hours=2)
    line = generate_keys.describe({"email": "a@b.com", "key": "k" * 16, "expires": str(soon), "active": True})
    assert "a@b.com" in line and "active, 12 j restants" in line
    old = license_manager._utcnow() - datetime.timedelta(days=3, hours=1)
    assert "expirée depuis 4 j" in generate_keys.describe({"email": "a@b.com", "key": "k", "expires": str(old), "active": True})
    assert "DÉSACTIVÉE" in generate_keys.describe({"email": "a@b.com", "key": "k", "expires": str(soon), "active": False})


def test_menu_de_gestion_des_licences(monkeypatch, capsys, tmp_path):
    lm = sqlite_license_manager(str(tmp_path / "l.db"))
    monkeypatch.setenv("DATABASE_URL", "sqlite")
    monkeypatch.setattr(generate_keys, "LicenseManager", lambda: lm)
    answers = iter(["1", "Client@Exemple.com", "12",          # création, un an
                    "1", "pas-un-email",                       # e-mail refusé
                    "1", "client@exemple.com", "abc",          # durée refusée
                    "1", "client@exemple.com", "1",            # prolongation (la clé ne change pas)
                    "2", "3", "client@exemple.com", "2", "4"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert generate_keys.main() == 0
    out = capsys.readouterr().out
    assert "Licence créée." in out and "Licence prolongée." in out
    assert "Adresse e-mail invalide." in out and "Durée invalide" in out
    assert "Licence désactivée." in out and "DÉSACTIVÉE" in out and "active, 394 j restants" in out
    assert len(lm.list_licenses()) == 1


def test_menu_sans_base_de_donnees(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert generate_keys.main() == 2 and "DATABASE_URL" in capsys.readouterr().out
