"""Outils en ligne de commande : affichage des combinés, gestion manuelle des licences."""
import datetime
import json
import re

import pytest

import dashboard
import email_sender
import generate_keys
import license_manager
import main
from fakes import insert_license, sqlite_license_manager
from generation_service import GenerationService
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


class MailBox:
    """Faux envoi d'e-mails : garde les messages au lieu de les expédier."""
    name = "test"

    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


class BrokenMailBox:
    name = "test"

    def send(self, message):
        raise email_sender.EmailError("refusé par le serveur", transient=False)


def run_menu(monkeypatch, lm, *answers, transport=None):
    """Lance le menu des licences avec des réponses scriptées. Renvoie (code de sortie, questions posées,
    réponses non utilisées). `transport` : l'envoi d'e-mails disponible (None = non configuré)."""
    monkeypatch.setenv("DATABASE_URL", "sqlite")
    monkeypatch.setattr(generate_keys, "LicenseManager", lambda: lm)
    monkeypatch.setattr(generate_keys.email_sender, "default_transport", lambda: transport)
    queue, prompts = iter(answers), []

    def fake_input(prompt=""):
        prompts.append(prompt)
        return next(queue)

    monkeypatch.setattr("builtins.input", fake_input)
    code = generate_keys.main()
    return code, prompts, list(queue)


def test_menu_de_gestion_des_licences(monkeypatch, capsys, tmp_path):
    lm = sqlite_license_manager(str(tmp_path / "l.db"))
    code, _, left = run_menu(monkeypatch, lm,
                             "1", "Client@Exemple.com", "12",          # création, un an
                             "1", "pas-un-email",                       # e-mail refusé
                             "1", "client@exemple.com", "abc",          # durée refusée
                             "1", "client@exemple.com", "1",            # prolongation (la clé ne change pas)
                             "2", "3", "client@exemple.com", "2", "5")
    assert code == 0 and left == []
    out = capsys.readouterr().out
    assert "(base ?, 0 licence(s))" in out                           # repère non secret de la base en tête de menu
    assert "Licence créée." in out and "Licence prolongée." in out
    assert "Adresse e-mail invalide." in out and "Durée invalide" in out
    assert "Licence désactivée." in out and "DÉSACTIVÉE" in out and "active, 394 j restants" in out
    assert len(lm.list_licenses()) == 1


def test_menu_propose_d_envoyer_la_cle_par_e_mail(monkeypatch, capsys, tmp_path):
    lm = sqlite_license_manager(str(tmp_path / "l.db"))
    box = MailBox()
    code, prompts, left = run_menu(monkeypatch, lm, "1", "Client@Exemple.com", "1", "o",
                                   "1", "client@exemple.com", "1", "OUI", "5", transport=box)
    assert code == 0 and left == []
    out = capsys.readouterr().out
    assert out.count("E-mail envoyé à client@exemple.com.") == 2
    assert sum("par e-mail" in p for p in prompts) == 2
    key = lm.list_licenses()[0]["key"]
    created, extended = box.sent
    assert created["To"] == extended["To"] == "client@exemple.com"
    assert created["Subject"] == "Ta clé d’accès Triple Elite VIP"
    assert extended["Subject"] == "Ton abonnement Triple Elite VIP est prolongé"        # la clé, elle, ne change pas
    for message in box.sent:
        text = message.get_body(("plain",)).get_content()
        assert key in text and "rétractation" not in text          # accès offert à la main : aucun achat


def test_menu_n_envoie_rien_sans_un_oui_explicite(monkeypatch, capsys, tmp_path):
    lm = sqlite_license_manager(str(tmp_path / "l.db"))
    box = MailBox()
    answers = ["1", "client@exemple.com", "1"]
    code, _, left = run_menu(monkeypatch, lm, *answers, "", *answers, "n", *answers, "peut-être", "5", transport=box)
    assert code == 0 and left == [] and box.sent == []
    out = capsys.readouterr().out
    assert out.count("Aucun e-mail envoyé") == 3 and "E-mail envoyé à" not in out
    assert len(lm.list_licenses()) == 1


def test_menu_sans_envoi_configure_l_explique_sans_rien_demander(monkeypatch, capsys, tmp_path):
    lm = sqlite_license_manager(str(tmp_path / "l.db"))
    code, prompts, left = run_menu(monkeypatch, lm, "1", "client@exemple.com", "1", "5", transport=None)
    assert code == 0 and left == []
    out = capsys.readouterr().out
    assert "Envoi d'e-mail non configuré" in out and "GMAIL_MDP" in out and "Clé    : " in out
    assert not any("par e-mail" in p for p in prompts)


def test_menu_un_envoi_en_echec_n_efface_pas_la_cle(monkeypatch, capsys, tmp_path):
    lm = sqlite_license_manager(str(tmp_path / "l.db"))
    code, _, left = run_menu(monkeypatch, lm, "1", "client@exemple.com", "1", "o", "5", transport=BrokenMailBox())
    assert code == 0 and left == []
    out = capsys.readouterr().out
    key = lm.list_licenses()[0]["key"]
    assert key in out and "n'a pas pu partir" in out and "E-mail envoyé à" not in out
    assert lm.check_login("client@exemple.com", key)["ok"] is True


def site_client(lm, tmp_path):
    """Le site client branché sur ce gestionnaire de licences (donc sur la même base que le script)."""
    service = GenerationService(pipeline=make_pipeline(delay=0), cache_dir=str(tmp_path / "cache"),
                                results_dir=str(tmp_path / "results"))
    app = dashboard.create_app(lm=lm, service=service, history_loader=lambda: [])
    app.config["TESTING"] = True
    client = app.test_client()
    return lambda email, typed: client.post("/login", data={"email": email, "license_key": typed}).status_code


def test_licence_creee_en_ligne_de_commande_ouvre_la_connexion_au_site(monkeypatch, capsys, tmp_path):
    """Le scénario réel : clé créée dans le Shell, puis connexion sur le site (même base de licences)."""
    lm = sqlite_license_manager(str(tmp_path / "l.db"))
    code, _, _ = run_menu(monkeypatch, lm, "1", "Proprietaire@Exemple.com", "1", "5")
    assert code == 0
    key = re.search(r"Clé\s*: (\S+)", capsys.readouterr().out).group(1)
    login = site_client(lm, tmp_path)
    assert login("proprietaire@exemple.com", key) == 303
    assert login(" PROPRIETAIRE@exemple.com ", f" {key[:8]} {key[8:].upper()} ") == 303
    assert login("proprietaire@exemple.com", "clé-é-é-é-é-é-é-é") == 401          # un accent ne fait plus planter (500)
    assert login("autre@exemple.com", key) == 401


STALE_KEY, GOOD_KEY = "f093e5942aa3a971", "577a41809f7b6bdd"


def duplicated_database(tmp_path):
    """La base du propriétaire telle qu'elle était en production : « Ibrahim@… » désactivée et « ibrahim@… » active."""
    path = str(tmp_path / "l.db")
    lm = sqlite_license_manager(path)
    insert_license(path, "Proprietaire@Exemple.com", STALE_KEY, False)
    insert_license(path, "proprietaire@exemple.com", GOOD_KEY, True)
    return lm


def test_la_base_en_double_n_empeche_plus_la_connexion_au_site(tmp_path):
    lm = duplicated_database(tmp_path)
    login = site_client(lm, tmp_path)
    assert login("proprietaire@exemple.com", GOOD_KEY) == 303          # avant le correctif : refusée au hasard des lignes
    assert login("Proprietaire@Exemple.com", GOOD_KEY.upper()) == 303
    assert login("proprietaire@exemple.com", STALE_KEY) == 403         # l'ancienne clé reste désactivée
    assert login("proprietaire@exemple.com", "0000000000000000") == 401


def test_menu_signale_les_doublons_en_tete_et_dans_la_liste(monkeypatch, capsys, tmp_path):
    lm = duplicated_database(tmp_path)
    code, _, left = run_menu(monkeypatch, lm, "2", "5")
    assert code == 0 and left == []
    out = capsys.readouterr().out
    assert "(base ?, 2 licence(s) pour 1 e-mail(s) : DOUBLONS à supprimer)" in out
    assert out.count("<= DOUBLON") == 2 and "option 4" in out
    assert "DÉSACTIVÉE" in out and "active, 1093 j restants" in out


def test_menu_sans_doublon_n_en_parle_pas(monkeypatch, capsys, tmp_path):
    lm = sqlite_license_manager(str(tmp_path / "l.db"))
    lm.issue_license("a@b.com", 1)
    lm.issue_license("c@d.com", 1)
    run_menu(monkeypatch, lm, "2", "5")
    assert "DOUBLON" not in capsys.readouterr().out


def test_menu_supprime_les_lignes_d_un_e_mail_apres_confirmation_tapee(monkeypatch, capsys, tmp_path):
    lm = duplicated_database(tmp_path)
    lm.issue_license("autre@exemple.com", 1)
    code, prompts, left = run_menu(monkeypatch, lm,
                                   "4", "proprietaire@exemple.com", "peut-être",       # autre mot : rien n'est effacé
                                   "4", "proprietaire@exemple.com", "",                # réponse vide : rien non plus
                                   "4", "PROPRIETAIRE@exemple.com", " Supprimer ",     # le mot demandé (casse tolérée)
                                   "2", "5")
    assert code == 0 and left == []
    out = capsys.readouterr().out
    assert out.count("2 ligne(s) seront effacées") == 3 and out.count("Annulé : rien n'a été supprimé.") == 2
    assert out.count("2 ligne(s) supprimée(s).") == 1 and "option 1" in out
    assert sum("tape le mot « supprimer »" in p for p in prompts) == 3
    assert [lic["email"] for lic in lm.list_licenses()] == ["autre@exemple.com"]          # l'autre client est intact
    assert "1 licence(s)" in out and "DOUBLON" not in out.split("licence(s) au")[-1]      # la liste finale est propre


def test_menu_ne_supprime_rien_sans_le_mot_exact(monkeypatch, capsys, tmp_path):
    lm = duplicated_database(tmp_path)
    for word in ("o", "oui", "supprime", "supprimer tout", "y"):
        run_menu(monkeypatch, lm, "4", "proprietaire@exemple.com", word, "5")
    assert len(lm.list_licenses()) == 2
    assert capsys.readouterr().out.count("supprimée(s)") == 0


def test_menu_suppression_d_un_e_mail_inconnu_ou_invalide_ne_demande_rien(monkeypatch, capsys, tmp_path):
    lm = sqlite_license_manager(str(tmp_path / "l.db"))
    lm.issue_license("a@b.com", 1)
    code, prompts, left = run_menu(monkeypatch, lm, "4", "inconnu@exemple.com", "4", "pas-un-email", "5")
    assert code == 0 and left == []
    out = capsys.readouterr().out
    assert "Licence introuvable." in out and "Adresse e-mail invalide." in out
    assert not any("supprimer" in p for p in prompts) and len(lm.list_licenses()) == 1


def test_menu_une_suppression_en_echec_n_est_pas_presentee_comme_reussie(monkeypatch, capsys, tmp_path):
    lm = duplicated_database(tmp_path)

    def broken(email):
        raise OSError("base injoignable")

    monkeypatch.setattr(lm, "delete_license", broken)
    code, _, left = run_menu(monkeypatch, lm, "4", "proprietaire@exemple.com", "supprimer", "5")
    assert code == 0 and left == []
    out = capsys.readouterr().out
    assert "Échec, rien n'a été supprimé : base injoignable" in out and "supprimée(s)" not in out
    assert len(lm.list_licenses()) == 2


def test_repartir_de_zero_supprimer_puis_recreer_ouvre_la_connexion_au_site(monkeypatch, capsys, tmp_path):
    """Ce que le propriétaire a demandé : tout effacer pour son e-mail, puis créer une licence neuve."""
    lm = duplicated_database(tmp_path)
    code, _, left = run_menu(monkeypatch, lm, "4", "proprietaire@exemple.com", "supprimer",
                             "1", "proprietaire@exemple.com", "12", "2", "5")
    assert code == 0 and left == []
    out = capsys.readouterr().out
    key = re.search(r"Clé\s*: (\S+)", out).group(1)
    assert "Licence créée." in out and key not in (STALE_KEY, GOOD_KEY)
    assert "DOUBLON" not in out.split("Licence créée.")[-1]
    login = site_client(lm, tmp_path)
    assert login("proprietaire@exemple.com", key) == 303
    assert login("proprietaire@exemple.com", GOOD_KEY) == 401 and login("proprietaire@exemple.com", STALE_KEY) == 401


def test_menu_sans_base_de_donnees(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert generate_keys.main() == 2 and "DATABASE_URL" in capsys.readouterr().out
