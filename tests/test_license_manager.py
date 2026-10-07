import datetime
import logging
import threading

import pytest

import config
import license_manager
from fakes import insert_license, raw, sqlite_license_manager
from license_manager import LicenseManager, normalize_email


@pytest.fixture()
def path(tmp_path):
    return str(tmp_path / "licenses.db")


@pytest.fixture()
def lm(path):
    return sqlite_license_manager(path)


def set_expiry(path, email, delta_days):
    when = license_manager._utcnow() + datetime.timedelta(days=delta_days)
    raw(path, "UPDATE licenses SET expires = ? WHERE email = ?", (str(when), email))
    return when


def test_creation_et_connexion(lm):
    info = lm.issue_license("Client@Exemple.com ", 1)
    assert info["renewed"] is False and len(info["key"]) == 16
    delta = info["expires"] - license_manager._utcnow()
    assert datetime.timedelta(days=29, hours=23) < delta <= datetime.timedelta(days=30)
    assert lm.verify_license("client@exemple.com", info["key"]) == (True, "Licence valide")
    # E-mail et clé tolèrent majuscules et espaces (claviers de téléphone).
    assert lm.verify_license("  CLIENT@exemple.COM ", f" {info['key'].upper()} ")[0] is True
    assert lm.is_license_active("Client@exemple.com") is True


def test_messages_ne_revelent_rien_sans_la_bonne_cle(lm, path):
    info = lm.issue_license("a@b.com", 1)
    wrong = lm.verify_license("a@b.com", "0000000000000000")
    unknown = lm.verify_license("inconnu@b.com", info["key"])
    empty = lm.verify_license("a@b.com", "")
    assert wrong == unknown == empty == (False, "E-mail ou clé de licence incorrect.")
    # Même une licence expirée ou désactivée ne le révèle qu'avec la bonne clé.
    set_expiry(path, "a@b.com", -3)
    assert lm.verify_license("a@b.com", "0000000000000000") == wrong
    valid, message = lm.verify_license("a@b.com", info["key"])
    assert valid is False and message.startswith("Ton abonnement a expiré le ")
    lm.issue_license("c@d.com", 1)
    key = lm.list_licenses()
    assert lm.deactivate_license("C@D.com") is True
    c_key = [l for l in lm.list_licenses() if l["email"] == "c@d.com"][0]["key"]
    assert lm.verify_license("c@d.com", c_key) == (False, f"Licence désactivée. Contacte-nous : {config.SELLER_EMAIL}")
    assert lm.deactivate_license("zz@zz.com") is False


def test_acces_coupe_a_l_expiration(lm, path):
    lm.issue_license("a@b.com", 1)
    assert lm.get_status("a@b.com")["state"] == "active"
    set_expiry(path, "a@b.com", -0.001)
    assert lm.get_status("a@b.com")["state"] == "expired"
    assert lm.is_license_active("a@b.com") is False
    assert lm.get_status("nobody@b.com")["state"] == "unknown"
    assert lm.get_status("")["state"] == "unknown"
    lm.issue_license("x@y.com", 12)
    lm.deactivate_license("x@y.com")
    assert lm.get_status("x@y.com")["state"] == "inactive"


def test_renouvellement_cumule_le_temps_restant_et_garde_la_cle(lm, path):
    first = lm.issue_license("a@b.com", 1)
    end = set_expiry(path, "a@b.com", 10)                      # il reste 10 jours
    renewed = lm.issue_license("a@b.com", 1)
    assert renewed["renewed"] is True and renewed["key"] == first["key"]
    assert renewed["expires"] == end + datetime.timedelta(days=30)
    year = lm.issue_license("A@B.com", 12)                     # même e-mail, autre casse
    assert year["renewed"] is True and year["expires"] == renewed["expires"] + datetime.timedelta(days=365)
    assert len(lm.list_licenses()) == 1


def test_renouvellement_apres_expiration_nouvelle_cle(lm, path):
    first = lm.issue_license("a@b.com", 1)
    set_expiry(path, "a@b.com", -5)
    again = lm.issue_license("a@b.com", 1)
    assert again["renewed"] is False and again["key"] != first["key"]
    assert lm.verify_license("a@b.com", first["key"])[0] is False         # l'ancienne clé ne marche plus
    assert lm.verify_license("a@b.com", again["key"])[0] is True
    delta = again["expires"] - license_manager._utcnow()
    assert datetime.timedelta(days=29, hours=23) < delta <= datetime.timedelta(days=30)


def test_reactivation_d_une_licence_desactivee(lm):
    first = lm.issue_license("a@b.com", 1)
    lm.deactivate_license("a@b.com")
    again = lm.issue_license("a@b.com", 1)
    assert lm.is_license_active("a@b.com") is True and again["key"] != first["key"]


def test_generate_license_renvoie_la_cle(lm):
    key = lm.generate_license("a@b.com", 3)
    assert lm.verify_license("a@b.com", key)[0] is True


def test_licence_existante_avec_email_en_majuscules(lm, path):
    raw(path, "INSERT INTO licenses (email, key, created, expires, active) VALUES (?, ?, ?, ?, ?)",
        ("Ancien@Client.com", "abcd1234abcd1234", "2026-01-01 00:00:00",
         str(license_manager._utcnow() + datetime.timedelta(days=5)), 1))
    assert lm.verify_license("ancien@client.com", "abcd1234abcd1234")[0] is True
    renewed = lm.issue_license("ANCIEN@client.com", 1)
    assert renewed["renewed"] and renewed["key"] == "abcd1234abcd1234"
    assert len(raw(path, "SELECT * FROM licenses")) == 1


def test_expiration_sans_microsecondes(lm, path):
    lm.issue_license("a@b.com", 1)
    raw(path, "UPDATE licenses SET expires = ? WHERE email = ?",
        ((license_manager._utcnow() + datetime.timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"), "a@b.com"))
    assert lm.is_license_active("a@b.com") is True
    raw(path, "UPDATE licenses SET expires = ? WHERE email = ?", ("n'importe quoi", "a@b.com"))
    assert lm.is_license_active("a@b.com") is False                        # date illisible : accès refusé


def test_base_indisponible_ne_deconnecte_pas_et_ne_donne_pas_de_licence(path):
    def broken():
        raise OSError("connexion refusée")

    lm = LicenseManager(db_url="x", connect=broken)                       # init_db avale l'erreur
    assert lm.get_status("a@b.com")["state"] == "error"
    assert lm.is_license_active("a@b.com") is False
    valid, message = lm.verify_license("a@b.com", "x")
    assert valid is False and "indisponible" in message
    with pytest.raises(OSError):
        lm.issue_license("a@b.com", 1)                                    # jamais de fausse licence
    assert lm.list_licenses() == [] and lm.deactivate_license("a@b.com") is False
    with pytest.raises(OSError):
        lm.delete_license("a@b.com")                                      # jamais « supprimé » sans réponse de la base


# --------------------------------------------------------------------------
# Commandes de paiement
# --------------------------------------------------------------------------

def test_commande_cycle_complet(lm):
    assert lm.create_pending_order("tok1", "Client@X.com", "Mensuel", 1) is True
    assert lm.create_pending_order("tok1", "autre@x.com", "Annuel", 12) is True      # doublon ignoré
    order = lm.get_pending_order("tok1")
    assert order == {"email": "client@x.com", "plan": "Mensuel", "duree": 1, "processed": False,
                     "license_key": None, "renewed": None}
    claimed = lm.claim_order("tok1")
    assert claimed == {"email": "client@x.com", "plan": "Mensuel", "duree": 1}
    assert lm.claim_order("tok1") is None                                           # déjà prise
    assert lm.get_pending_order("tok1")["processed"] is True
    assert lm.complete_order("tok1", "cle123") is True
    done = lm.get_pending_order("tok1")
    assert done["license_key"] == "cle123" and done["renewed"] is False and done["processed"] is True
    lm.complete_order("tok1", "cle123", renewed=True)
    assert lm.get_pending_order("tok1")["renewed"] is True
    assert lm.get_pending_order("inconnu") is None and lm.claim_order("inconnu") is None


def test_liberation_d_une_commande(lm):
    lm.create_pending_order("tok", "a@b.com", "Mensuel", 1)
    assert lm.claim_order("tok") is not None
    lm.release_order("tok")
    assert lm.get_pending_order("tok")["processed"] is False
    assert lm.claim_order("tok") is not None


def test_commandes_base_indisponible_leve_une_erreur_au_lieu_de_dire_inconnue(path):
    def broken():
        raise OSError("connexion refusée")

    lm = LicenseManager(db_url="x", connect=broken)
    # Une base injoignable ne doit jamais passer pour « commande introuvable » (le client a peut-être payé).
    with pytest.raises(OSError):
        lm.get_pending_order("tok")
    with pytest.raises(OSError):
        lm.claim_order("tok")
    assert lm.create_pending_order("tok", "a@b.com", "Mensuel", 1) is False
    lm.release_order("tok")                                                # sans effet, sans plantage
    assert lm.complete_order("tok", "cle") is False


def test_anciennes_commandes_sans_colonnes_recentes(path):
    # Base créée par une version antérieure : sans les colonnes license_key et renewed.
    raw(path, "CREATE TABLE pending_orders (token TEXT PRIMARY KEY, email TEXT NOT NULL, plan TEXT NOT NULL, "
              "duree INTEGER NOT NULL, created TEXT, processed BOOLEAN DEFAULT FALSE)")
    raw(path, "INSERT INTO pending_orders VALUES ('vieux', 'a@b.com', 'Mensuel', 1, '2026-01-01 00:00:00', 1)")
    lm = sqlite_license_manager(path)                                      # init_db ajoute les colonnes
    assert lm.get_pending_order("vieux") == {"email": "a@b.com", "plan": "Mensuel", "duree": 1, "processed": True,
                                             "license_key": None, "renewed": None}


def test_une_seule_reservation_en_cas_d_appels_simultanes(lm):
    lm.create_pending_order("tok", "a@b.com", "Mensuel", 1)
    winners = []

    def attempt():
        if lm.claim_order("tok"):
            winners.append(1)

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(winners) == 1


def test_init_db_idempotent(path):
    first = sqlite_license_manager(path)
    first.issue_license("a@b.com", 1)
    second = sqlite_license_manager(path)                                  # recrée les tables sans rien casser
    assert len(second.list_licenses()) == 1


def test_description_non_secrete_de_la_base(lm):
    assert lm.describe_db() == "base ?, 0 licence(s)"              # SQLite de test : pas de nom de base PostgreSQL
    lm.issue_license("a@b.com", 1)
    lm.issue_license("c@d.com", 1)
    lm.db_url = "postgres://utilisateur:motdepasse-secret@dpg-abc123-a.frankfurt-postgres.render.com/triple_elite_db"
    description = lm.describe_db()
    assert description == "base triple_elite_db, 2 licence(s)"
    assert "motdepasse" not in description and "dpg-abc123" not in description and "utilisateur" not in description


def test_le_demarrage_journalise_la_base_utilisee(path, caplog):
    sqlite_license_manager(path).issue_license("a@b.com", 1)
    caplog.set_level("INFO", logger="license_manager")
    sqlite_license_manager(path)                                   # un nouveau démarrage
    assert "Base de données des licences initialisée (base ?, 1 licence(s))" in caplog.text


def test_description_de_la_base_injoignable_ne_plante_pas():
    def broken():
        raise OSError("connexion refusée")

    lm = LicenseManager(db_url="postgres://u:p@h/ma_base", connect=broken)
    assert lm.describe_db() == "base ma_base, nombre de licences illisible"


def test_normalize_email():
    assert normalize_email("  A@B.Com ") == "a@b.com" and normalize_email(None) == ""


def test_durees_en_jours():
    assert license_manager.duration_days(1) == 30
    assert license_manager.duration_days(12) == 365        # une vraie année, pas 12 x 30 jours
    assert license_manager.duration_days(3) == 91
    assert license_manager.duration_days(0) == 0


def test_check_login_detaille_la_raison(lm, path):
    info = lm.issue_license("a@b.com", 1)
    assert lm.check_login("a@b.com", info["key"])["reason"] == "ok"
    assert lm.check_login("a@b.com", "0000000000000000")["reason"] == "invalid"
    assert lm.check_login("inconnu@b.com", info["key"])["reason"] == "invalid"
    set_expiry(path, "a@b.com", -2)
    expired = lm.check_login("a@b.com", info["key"])
    assert expired["reason"] == "expired" and expired["ok"] is False and expired["expires"] is not None
    assert lm.check_login("a@b.com", "0000000000000000")["reason"] == "invalid"     # sans la bonne clé : rien de révélé
    other = lm.issue_license("c@d.com", 1)
    lm.deactivate_license("c@d.com")
    assert lm.check_login("c@d.com", other["key"])["reason"] == "inactive"


def test_cle_recopiee_avec_des_espaces_ou_en_majuscules(lm):
    info = lm.issue_license("a@b.com", 1)
    grouped = " ".join(info["key"][i:i + 4] for i in range(0, 16, 4)).upper()
    assert lm.check_login("a@b.com", grouped)["ok"] is True
    assert lm.check_login("a@b.com", f"\n {info['key']}\t")["ok"] is True
    assert lm.check_login("a@b.com", info["key"][:-1])["ok"] is False


def test_check_login_donne_le_motif_exact_pour_les_journaux_seulement(lm):
    info = lm.issue_license("a@b.com", 1)
    unknown = lm.check_login("inconnu@b.com", info["key"])
    short = lm.check_login("a@b.com", info["key"][:-1])
    other = lm.check_login("a@b.com", "0000000000000000")
    blank = lm.check_login("a@b.com", " ​ ")
    assert unknown["detail"] == "e-mail inconnu" and blank["detail"] == "clé vide"
    assert short["detail"] == "clé différente (15 caractères saisis, 16 attendus)"
    assert other["detail"] == "clé différente (16 caractères saisis, 16 attendus)"
    # Le visiteur lit toujours le même message, et le motif ne contient jamais la clé.
    results = (unknown, short, other, blank)
    assert {r["reason"] for r in results} == {"invalid"}
    assert {r["message"] for r in results} == {"E-mail ou clé de licence incorrect."}
    assert all(info["key"] not in r["detail"] for r in results)


def test_cle_avec_caracteres_invisibles_acceptee(lm):
    """Une copie depuis un e-mail ou une messagerie glisse parfois des caractères invisibles dans la clé."""
    key = lm.issue_license("a@b.com", 1)["key"]
    for typed in (f"{key[:8]}​{key[8:]}",               # espace de largeur nulle
                  f"﻿{key}‎",                       # marque d'ordre des octets, marque de sens d'écriture
                  f"{key[:8]} {key[8:]}",                # espace insécable
                  f"{key[:4]}­{key[4:]}"):               # trait d'union conditionnel
        assert lm.check_login("a@b.com", typed)["ok"] is True, repr(typed)


def test_cle_avec_accents_ou_caracteres_exotiques_est_refusee_sans_planter(lm):
    """hmac.compare_digest refuse les textes non ASCII : une clé accentuée donnait une erreur 500 au lieu d'un refus."""
    lm.issue_license("a@b.com", 1)
    for typed in ("clé-accentuée-é", "ÀÉÎÔÛ" * 4, "😀" * 8, "\ud800x", "\x00" * 16):
        result = lm.check_login("a@b.com", typed)
        assert result["ok"] is False and result["reason"] == "invalid", repr(typed)


def test_cle_lue_avec_des_lettres_qui_ressemblent_a_des_chiffres(lm, path):
    """La lettre O pour un zéro, I ou L pour un un : confusions courantes en recopiant une clé à la main."""
    soon = str(license_manager._utcnow() + datetime.timedelta(days=5))
    raw(path, "INSERT INTO licenses (email, key, created, expires, active) VALUES (?, ?, ?, ?, ?)",
        ("a@b.com", "0a1b2c3d4e5f6071", "2026-01-01 00:00:00", soon, 1))
    for typed in ("0a1b2c3d4e5f6071", "Oa1b2c3d4e5f6071", "0alb2c3d4e5f6O7I", "oa1b2c3d4e5f607L"):
        assert lm.check_login("a@b.com", typed)["ok"] is True, typed
    for typed in ("0a1b2c3d4e5f6072", "0a1b2c3d4e5f6O7x", "0a1b2c3d4e5f607"):
        assert lm.check_login("a@b.com", typed)["ok"] is False, typed


def test_lettres_ressemblantes_ignorees_pour_une_cle_ancienne_non_hexadecimale(lm, path):
    soon = str(license_manager._utcnow() + datetime.timedelta(days=5))
    raw(path, "INSERT INTO licenses (email, key, created, expires, active) VALUES (?, ?, ?, ?, ?)",
        ("vieux@b.com", "Olive-Ancienne-Cle", "2026-01-01 00:00:00", soon, 1))
    assert lm.check_login("vieux@b.com", " olive-ancienne-cle ")["ok"] is True
    assert lm.check_login("vieux@b.com", "0live-ancienne-cle")["ok"] is False        # pas de lecture « magique » ici


def test_cles_tolerantes_ne_rendent_pas_les_essais_gratuits(lm):
    """Une clé presque juste (un caractère de trop ou de moins, un chiffre faux) reste refusée."""
    key = lm.issue_license("a@b.com", 1)["key"]
    wrong_digit = key[:-1] + ("0" if key[-1] != "0" else "2")
    for typed in (key + "0", key[:-1], wrong_digit, key[::-1] if key[::-1] != key else key + "x"):
        assert lm.check_login("a@b.com", typed)["ok"] is False, typed


# --------------------------------------------------------------------------
# Plusieurs lignes pour un même e-mail (casse différente) : doublons hérités d'anciennes versions
# --------------------------------------------------------------------------

STALE_EMAIL, STALE_KEY = "Client@Exemple.com", "f093e5942aa3a971"          # ligne désactivée, ancienne
GOOD_EMAIL, GOOD_KEY = "client@exemple.com", "577a41809f7b6bdd"            # ligne active, la bonne


def with_duplicates(path, stale_first):
    """Les deux lignes, dans les deux ordres physiques possibles (la base ne garantit aucun ordre)."""
    stale = (STALE_EMAIL, STALE_KEY, False)
    good = (GOOD_EMAIL, GOOD_KEY, True)
    for row in ((stale, good) if stale_first else (good, stale)):
        insert_license(path, *row)


@pytest.mark.parametrize("stale_first", [True, False])
def test_doublon_la_ligne_desactivee_ne_bloque_pas_la_ligne_active(lm, path, stale_first):
    with_duplicates(path, stale_first)
    assert lm.check_login("client@exemple.com", GOOD_KEY)["ok"] is True
    assert lm.check_login("  CLIENT@exemple.com ", GOOD_KEY.upper())["ok"] is True
    assert lm.get_status("client@exemple.com")["state"] == "active"              # la session reste valable
    assert lm.is_license_active("Client@Exemple.com") is True
    assert lm.verify_license("client@exemple.com", GOOD_KEY) == (True, "Licence valide")


@pytest.mark.parametrize("stale_first", [True, False])
def test_doublon_l_ancienne_cle_reste_refusee_comme_desactivee(lm, path, stale_first):
    with_duplicates(path, stale_first)
    assert lm.check_login("client@exemple.com", STALE_KEY)["reason"] == "inactive"
    wrong = lm.check_login("client@exemple.com", "0000000000000000")
    assert wrong["reason"] == "invalid" and "2 lignes pour cet e-mail" in wrong["detail"]


def test_doublon_la_meilleure_ligne_decide_de_l_etat(lm, path):
    insert_license(path, STALE_EMAIL, STALE_KEY, False, days=500)           # désactivée
    insert_license(path, GOOD_EMAIL, GOOD_KEY, True, days=-3)               # active mais expirée
    assert lm.get_status("client@exemple.com")["state"] == "expired"        # plus parlant que « désactivée »
    raw(path, "UPDATE licenses SET active = 0")
    assert lm.get_status("client@exemple.com")["state"] == "inactive"
    assert lm.is_license_active("client@exemple.com") is False


def test_doublon_deux_lignes_actives_chaque_cle_ouvre_sa_ligne(lm, path):
    insert_license(path, STALE_EMAIL, STALE_KEY, True, days=10)
    insert_license(path, GOOD_EMAIL, GOOD_KEY, True, days=400)
    assert lm.check_login("client@exemple.com", STALE_KEY)["ok"] is True
    assert lm.check_login("client@exemple.com", GOOD_KEY)["ok"] is True
    longest = lm.get_status("client@exemple.com")["expires"]
    assert longest > license_manager._utcnow() + datetime.timedelta(days=399)       # la plus longue est retenue


def test_doublon_une_cle_expiree_reste_refusee_meme_si_une_autre_ligne_est_active(lm, path):
    insert_license(path, STALE_EMAIL, STALE_KEY, True, days=-4)
    insert_license(path, GOOD_EMAIL, GOOD_KEY, True, days=300)
    expired = lm.check_login("client@exemple.com", STALE_KEY)
    assert expired["reason"] == "expired" and expired["ok"] is False
    assert lm.check_login("client@exemple.com", GOOD_KEY)["ok"] is True


@pytest.mark.parametrize("stale_first", [True, False])
def test_doublon_le_renouvellement_prolonge_la_ligne_utilisable_et_garde_sa_cle(lm, path, stale_first):
    with_duplicates(path, stale_first)
    before = {r["key"]: r for r in lm.list_licenses()}
    info = lm.issue_license("Client@Exemple.com", 1)
    assert info["renewed"] is True and info["key"] == GOOD_KEY
    after = {r["key"]: r for r in lm.list_licenses()}
    assert after[GOOD_KEY]["expires"] != before[GOOD_KEY]["expires"]                 # prolongée de 30 jours
    assert after[STALE_KEY] == before[STALE_KEY]                                     # l'autre ligne n'est pas touchée
    assert lm.check_login("client@exemple.com", GOOD_KEY)["ok"] is True


def test_doublon_sans_ligne_utilisable_la_plus_utile_recoit_la_nouvelle_cle(lm, path):
    insert_license(path, STALE_EMAIL, STALE_KEY, False)                              # désactivée
    insert_license(path, GOOD_EMAIL, GOOD_KEY, True, days=-5)                        # active mais expirée : c'est elle qu'on relance
    info = lm.issue_license("client@exemple.com", 1)
    assert info["renewed"] is False and info["key"] not in (STALE_KEY, GOOD_KEY)
    assert lm.check_login("client@exemple.com", info["key"])["ok"] is True
    assert lm.get_status("client@exemple.com")["state"] == "active"
    assert lm.check_login("client@exemple.com", STALE_KEY)["reason"] == "inactive"   # l'ancienne clé désactivée reste refusée
    assert lm.check_login("client@exemple.com", GOOD_KEY)["detail"].startswith("clé différente")    # remplacée
    assert raw(path, "SELECT COUNT(*) FROM licenses WHERE active = 1")[0][0] == 1


@pytest.mark.parametrize("stale_first", [True, False])
def test_doublon_toutes_lignes_desactivees_une_seule_est_relancee(lm, path, stale_first):
    rows = [(STALE_EMAIL, STALE_KEY), (GOOD_EMAIL, GOOD_KEY)]
    for email, key in (rows if stale_first else rows[::-1]):
        insert_license(path, email, key, False)
    info = lm.issue_license("client@exemple.com", 1)
    assert lm.check_login("client@exemple.com", info["key"])["ok"] is True
    assert lm.get_status("client@exemple.com")["state"] == "active"
    assert raw(path, "SELECT COUNT(*) FROM licenses WHERE active = 1")[0][0] == 1


def test_suppression_efface_toutes_les_lignes_de_l_e_mail_et_seulement_elles(lm, path):
    with_duplicates(path, True)
    lm.issue_license("autre@exemple.com", 1)
    assert lm.delete_license(" CLIENT@exemple.com ") == 2
    assert [lic["email"] for lic in lm.list_licenses()] == ["autre@exemple.com"]
    assert lm.check_login("client@exemple.com", GOOD_KEY)["detail"] == "e-mail inconnu"
    assert lm.get_status("client@exemple.com")["state"] == "unknown"
    assert lm.delete_license("client@exemple.com") == 0 and lm.delete_license("") == 0 and lm.delete_license(None) == 0
    assert len(lm.list_licenses()) == 1                                              # l'autre client est intact
    fresh = lm.issue_license("client@exemple.com", 1)                               # on repart de zéro
    assert fresh["renewed"] is False and lm.check_login("client@exemple.com", fresh["key"])["ok"] is True
    assert lm.check_login("client@exemple.com", STALE_KEY)["detail"].startswith("clé différente")


def test_suppression_ne_touche_pas_aux_commandes_de_paiement(lm):
    lm.create_pending_order("tok1", "client@exemple.com", "Mensuel", 1)
    lm.issue_license("client@exemple.com", 1)
    assert lm.delete_license("client@exemple.com") == 1
    assert lm.get_pending_order("tok1")["email"] == "client@exemple.com"


def test_suppression_signale_une_base_injoignable():
    def broken():
        raise OSError("connexion refusée")

    lm = LicenseManager(db_url="x", connect=broken)
    with pytest.raises(OSError):
        lm.delete_license("a@b.com")                     # jamais « supprimé » quand la base ne répond pas


def test_la_suppression_est_journalisee_sans_adresse_en_clair(lm, caplog):
    lm.issue_license("client@exemple.com", 1)
    caplog.set_level("INFO", logger="license_manager")
    lm.delete_license("client@exemple.com")
    assert "Licence supprimée pour c***@exemple.com (1 ligne(s))" in caplog.text
    assert "client@exemple.com" not in caplog.text


def test_les_doublons_sont_signales_a_la_description_et_au_demarrage(lm, path, caplog):
    assert lm.duplicate_count() == 0
    with_duplicates(path, True)
    insert_license(path, "autre@exemple.com", "0123456789abcdef", True)
    assert lm.duplicate_count() == 1
    assert lm.describe_db() == "base ?, 3 licence(s) pour 2 e-mail(s) : DOUBLONS à supprimer"
    caplog.set_level("INFO", logger="license_manager")
    sqlite_license_manager(path)                                                     # redémarrage du service
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "1 ligne(s) de licence en double" in warnings[0].getMessage()
    assert "generate_keys.py" in warnings[0].getMessage() and "exemple.com" not in caplog.text
    lm.delete_license("client@exemple.com")
    assert lm.duplicate_count() == 0 and lm.describe_db() == "base ?, 1 licence(s)"


def test_le_renouvellement_previent_dans_les_journaux_quand_il_y_a_des_doublons(lm, path, caplog):
    with_duplicates(path, True)
    caplog.set_level("WARNING", logger="license_manager")
    lm.issue_license("client@exemple.com", 1)
    assert "2 lignes de licence pour c***@exemple.com" in caplog.text and "client@exemple.com" not in caplog.text
