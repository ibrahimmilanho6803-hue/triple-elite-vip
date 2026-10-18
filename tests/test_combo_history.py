import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import pytest
import requests

import combo_history
import config
from combo_history import load_history, refresh_pending_results, summarize
from data_collector import DataCollector


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    combo_history._next_check.clear()
    collector = DataCollector()
    results = tmp_path / "results"
    results.mkdir()

    class Env:
        pass

    e = Env()
    e.collector, e.results, e.db = collector, str(results), str(tmp_path / "t.db")
    return e


def final(e, match_id, home, away, hs, aws, status="FT"):
    e.collector.save_match({"idEvent": str(match_id), "strHomeTeam": home, "strAwayTeam": away,
                            "intHomeScore": str(hs), "intAwayScore": str(aws), "dateEvent": "2026-10-03",
                            "strStatus": status}, "Serie A")


def leg(match_id, code, home=None, away=None, **extra):
    base = {"match_id": str(match_id), "home_team": home or f"H{match_id}", "away_team": away or f"A{match_id}",
            "league": "Serie A", "type": code, "type_name": "ancien libellé", "estimated_odds": 1.4,
            "confidence": 70, "kickoff": "2026-10-03T14:00:00+00:00"}
    base.update(extra)
    return base


def write(e, stamp, combos, owner=None):
    """Écrit un fichier de l'historique. `owner` : l'identifiant opaque du client qui l'a généré (rien : génération automatique)."""
    name = f"combo_{stamp}.json" if owner is None else f"combo_{stamp}_{owner}.json"
    with open(os.path.join(e.results, name), "w", encoding="utf-8") as f:
        json.dump(combos, f)


def combo(*legs, odds=2.7):
    return {"predictions": list(legs), "total_odds": odds, "avg_confidence": 70.0, "success_probability": 34.0}


def no_network(monkeypatch):
    monkeypatch.setattr(combo_history.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("réseau interdit")))


def test_verdicts_libelles_et_champs(env, monkeypatch):
    no_network(monkeypatch)
    final(env, 1, "Arsenal", "Chelsea", 2, 1)
    final(env, 2, "Roma", "Lazio", 0, 0)
    final(env, 3, "Milan", "Inter", 1, 3)
    write(env, "20261003_100000", [
        combo(leg(1, "V1", "Arsenal", "Chelsea"), leg(2, "TOTAL_0.5-", "Roma", "Lazio"), leg(3, "V2", "Milan", "Inter")),
        combo(leg(1, "V1", "Arsenal", "Chelsea"), leg(2, "BTTS_OUI", "Roma", "Lazio"), leg(3, "V2", "Milan", "Inter")),
    ])
    data = load_history(env.results, db_path=env.db, refresh=False)
    won, lost = data["combos"][0], data["combos"][1]
    first, second = sorted(data["combos"], key=lambda c: c["status"])
    assert {c["status"] for c in data["combos"]} == {"won", "lost"}
    winner = [c for c in data["combos"] if c["status"] == "won"][0]
    assert [l["outcome"] for l in winner["predictions"]] == ["won", "won", "won"]
    assert winner["predictions"][0]["type_name"] == "Victoire Arsenal"        # libellé régénéré
    assert winner["predictions"][1]["type_name"] == "Moins de 0,5 but dans le match"
    assert winner["predictions"][0]["score"] == "2 - 1"
    assert winner["predictions"][0]["kickoff"] == "2026-10-03T14:00:00+00:00"
    assert winner["generated_at"] == "2026-10-03T10:00:00+00:00"
    assert winner["success_probability"] == 34.0 and winner["total_odds"] == 2.7
    loser = [c for c in data["combos"] if c["status"] == "lost"][0]
    assert [l["outcome"] for l in loser["predictions"]] == ["won", "lost", "won"]


def test_en_cours_tant_qu_un_match_n_est_pas_joue(env, monkeypatch):
    no_network(monkeypatch)
    final(env, 1, "A", "B", 1, 0)
    write(env, "20261003_100000", [combo(leg(1, "V1", "A", "B"), leg(2, "V1"), leg(3, "V1"))])
    data = load_history(env.results, db_path=env.db, refresh=False)
    assert data["combos"][0]["status"] == "pending"
    assert [l["outcome"] for l in data["combos"][0]["predictions"]] == ["won", None, None]


def test_un_pari_perdu_suffit_meme_si_d_autres_matchs_restent_a_jouer(env, monkeypatch):
    no_network(monkeypatch)
    final(env, 1, "A", "B", 0, 1)
    write(env, "20261003_100000", [combo(leg(1, "V1", "A", "B"), leg(2, "V1"), leg(3, "V1"))])
    assert load_history(env.results, db_path=env.db, refresh=False)["combos"][0]["status"] == "lost"


def test_doublons_ordre_et_bilan(env, monkeypatch):
    no_network(monkeypatch)
    for i, (hs, aws) in enumerate([(2, 0), (1, 0), (0, 1), (3, 1), (2, 2), (1, 1)], start=1):
        final(env, i, f"H{i}", f"A{i}", hs, aws)
    same = combo(leg(1, "V1"), leg(2, "V1"), leg(3, "V1"))                    # gagné ? V1 sur 2-0, 1-0, 0-1 : perdu
    write(env, "20261001_090000", [same])
    write(env, "20261002_090000", [same, combo(leg(1, "V1"), leg(2, "V1"), leg(4, "V1"))])   # 2e : gagné ; 1er : doublon
    write(env, "20261003_090000", [combo(leg(5, "1X"), leg(6, "1X"), leg(7, "V1"))])         # en cours (match 7 inconnu)
    data = load_history(env.results, db_path=env.db, refresh=False)
    assert [c["generated_at"][:10] for c in data["combos"]] == ["2026-10-03", "2026-10-02", "2026-10-01"]
    assert [c["status"] for c in data["combos"]] == ["pending", "won", "lost"]
    summary = data["summary"]
    assert (summary["won"], summary["lost"], summary["pending"], summary["void"]) == (1, 1, 1, 0)
    assert summary["combo_win_rate"] == 50 and summary["combos_settled"] == 2
    # 9 paris réglés : combo 3 (V1 2-0, V1 1-0, V1 0-1) = 2 gagnés 1 perdu ; combo 2 = 3 gagnés ; combo 1 pending = 2 réglés gagnés
    assert summary["legs_won"] + summary["legs_lost"] == 3 + 3 + 2
    assert summary["leg_win_rate"] == round(100 * summary["legs_won"] / (summary["legs_won"] + summary["legs_lost"]))


def test_bilan_sur_tout_l_historique_mais_affichage_limite(env, monkeypatch):
    no_network(monkeypatch)
    final(env, 1, "A", "B", 1, 0)
    for day in range(1, 8):
        write(env, f"2026100{day}_090000", [combo(leg(1, "V1"), leg(1000 + day, "V1"), leg(2000 + day, "V1"))])
    data = load_history(env.results, db_path=env.db, max_combos=3, refresh=False)
    assert len(data["combos"]) == 3 and sum(data["summary"][k] for k in ("won", "lost", "pending", "void")) == 7


def test_resultats_recuperes_aupres_de_thesportsdb(env, monkeypatch):
    calls = []

    class Response:
        def __init__(self, event):
            self.event = event

        def json(self):
            return {"events": [self.event]}

    def fake_get(url, params=None, timeout=None):
        calls.append(params["id"])
        return Response({"idEvent": params["id"], "strHomeTeam": "H1", "strAwayTeam": "A1", "intHomeScore": "3",
                         "intAwayScore": "1", "dateEvent": "2026-10-03", "strStatus": "FT"})

    monkeypatch.setattr(combo_history.requests, "get", fake_get)
    write(env, "20261003_100000", [combo(leg(1, "V1"), leg(1, "V1"), leg(1, "V1"))])
    data = load_history(env.results, db_path=env.db)
    assert calls == [1] and data["combos"][0]["predictions"][0]["score"] == "3 - 1"
    assert data["combos"][0]["status"] == "won"
    load_history(env.results, db_path=env.db)                                  # score en base : plus aucun appel
    assert calls == [1]


def test_pas_de_nouvel_appel_avant_le_prochain_moment_utile(env, monkeypatch):
    calls = []
    now = time.time()
    start = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now + 7200))

    class Response:
        def json(self):
            return {"events": [{"idEvent": "5", "strStatus": "NS", "strTimestamp": start}]}

    monkeypatch.setattr(combo_history.requests, "get", lambda *a, **k: calls.append(1) or Response())
    assert refresh_pending_results({5: "Serie A"}, now=now) is False
    assert refresh_pending_results({5: "Serie A"}, now=now + 3600) is False       # match dans 1 h : inutile
    assert refresh_pending_results({5: "Serie A"}, now=now + 7200 + 110 * 60 - 60) is False
    assert len(calls) == 1
    assert refresh_pending_results({5: "Serie A"}, now=now + 7200 + 110 * 60 + 60) is False
    assert len(calls) == 2                                                          # après le match : on regarde


def test_limite_d_appels_par_ouverture(env, monkeypatch):
    calls = []

    class Response:
        def json(self):
            return {"events": []}

    monkeypatch.setattr(combo_history.requests, "get", lambda *a, **k: calls.append(1) or Response())
    pending = {i: "Serie A" for i in range(1, 40)}
    refresh_pending_results(pending, now=1000.0)
    assert len(calls) == combo_history._MAX_LOOKUPS
    refresh_pending_results(pending, now=1001.0)
    assert len(calls) == 2 * combo_history._MAX_LOOKUPS                           # la suite à l'ouverture suivante


def test_panne_reseau_ne_plante_pas(env, monkeypatch):
    monkeypatch.setattr(combo_history.requests, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("réseau")))
    write(env, "20261003_100000", [combo(leg(1, "V1"), leg(2, "V1"), leg(3, "V1"))])
    data = load_history(env.results, db_path=env.db)
    assert data["combos"][0]["status"] == "pending"


def test_la_cle_d_api_n_apparait_pas_dans_le_journal_d_une_consultation_en_echec(env, monkeypatch, caplog):
    secret = "CLE-PAYANTE-123456"
    monkeypatch.setattr(config, "SPORTSDB_API_KEY", secret)

    def failing(url, params=None, timeout=None):
        raise requests.exceptions.ConnectionError(
            f"HTTPSConnectionPool(host='www.thesportsdb.com', port=443): Max retries exceeded with url: "
            f"/api/v1/json/{secret}/lookupevent.php?id={params['id']} (Caused by NameResolutionError)")

    monkeypatch.setattr(combo_history.requests, "get", failing)
    with caplog.at_level(logging.WARNING):
        assert refresh_pending_results({1: "Serie A"}, now=1000.0) is False
    assert "lookup du match 1 impossible" in caplog.text and "json/***/lookupevent.php" in caplog.text
    assert secret not in caplog.text


def test_une_consultation_qui_ne_revient_jamais_ne_fige_pas_le_rafraichissement(env, monkeypatch, caplog):
    """Les pages publiques ne relancent rien tant que le rafraîchissement précédent n'est pas fini (showcase.py) : une
    consultation sans réponse ne doit donc pas le faire durer pour toujours."""
    release = threading.Event()

    def stuck(url, params=None, timeout=None):
        release.wait(10)
        raise OSError("trop tard")

    monkeypatch.setattr(combo_history.requests, "get", stuck)
    monkeypatch.setattr(combo_history, "_LOOKUP_DEADLINE", 0.3)
    started = time.monotonic()
    with caplog.at_level(logging.ERROR):
        assert refresh_pending_results({1: "Serie A", 2: "Serie A"}, now=1000.0) is False
    assert time.monotonic() - started < 3
    assert "lookup : 2 appel(s) sans réponse" in caplog.text and "match 1" in caplog.text
    release.set()


def test_match_reporte(env, monkeypatch):
    no_network(monkeypatch)
    final(env, 1, "A", "B", 1, 0)
    final(env, 2, "C", "D", 2, 0)
    env.collector.save_match({"idEvent": "3", "strHomeTeam": "E", "strAwayTeam": "F", "intHomeScore": None,
                              "intAwayScore": None, "dateEvent": "2026-10-03", "strStatus": "PST"}, "Serie A")
    write(env, "20261003_100000", [
        combo(leg(1, "V1"), leg(2, "V1"), leg(3, "V1")),                 # reporté, autres gagnés -> annulé
        combo(leg(1, "V2"), leg(2, "V1"), leg(3, "V1")),                 # un perdu -> perdu
        combo(leg(1, "V1"), leg(4, "V1"), leg(3, "V1")),                 # reporté mais un match à venir -> en cours
    ])
    data = load_history(env.results, db_path=env.db, refresh=False)
    by_status = sorted(c["status"] for c in data["combos"])
    assert by_status == ["lost", "pending", "void"]
    voided = [c for c in data["combos"] if c["status"] == "void"][0]
    assert [l["outcome"] for l in voided["predictions"]] == ["won", "won", "void"]
    assert data["summary"]["void"] == 1 and data["summary"]["combos_settled"] == 1


def test_fichiers_invalides_ignores(env, monkeypatch):
    no_network(monkeypatch)
    with open(os.path.join(env.results, "combo_20261001_000000.json"), "w") as f:
        f.write("{pas du json")
    with open(os.path.join(env.results, "combo_20261002_000000.json"), "w") as f:
        json.dump({"pas": "une liste"}, f)
    with open(os.path.join(env.results, "combo_invalide.json"), "w") as f:
        json.dump([combo(leg(1, "V1"))], f)
    write(env, "20261003_000000", [{"predictions": []}, "texte", {"predictions": ["x"]}])
    data = load_history(env.results, db_path=env.db, refresh=False)
    assert data["combos"] == [] and data["summary"]["combo_win_rate"] is None


def test_dossier_et_base_absents(tmp_path, monkeypatch):
    no_network(monkeypatch)
    data = load_history(str(tmp_path / "rien"), db_path=str(tmp_path / "absente.db"), refresh=False)
    assert data["combos"] == [] and data["summary"]["won"] == 0
    assert summarize([])["leg_win_rate"] is None


# --------------------------------------------------------------------------
# Chaque client voit SES combinés : ceux des autres ne s'affichent qu'une fois tous leurs matchs commencés
# --------------------------------------------------------------------------

ALICE, BOB, CAROL = "a1b2c3d4e5f60718", "0123456789abcdef", "fedcba9876543210"
NOON = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc).timestamp()


def at(hour, day="2026-10-10"):
    return f"{day}T{hour:02d}:00:00+00:00"


def trio(first, kickoff=at(20), types=("V1", "V1", "V1")):
    """Un combiné de trois matchs consécutifs à partir de `first`, tous au même coup d'envoi."""
    return combo(*[leg(first + n, code, kickoff=kickoff) for n, code in enumerate(types)])


def history_for(env, viewer):
    return load_history(env.results, db_path=env.db, refresh=False, viewer=viewer, now=NOON)


def teams(data):
    return sorted(l["home_team"] for c in data["combos"] for l in c["predictions"])


def test_un_client_voit_ses_combines_a_venir_et_pas_ceux_des_autres(env, monkeypatch):
    no_network(monkeypatch)
    write(env, "20261010_080000", [trio(1)], owner=ALICE)
    write(env, "20261010_081000", [trio(4)], owner=BOB)
    write(env, "20261010_060000", [trio(7)])                                      # génération automatique de 6 h
    assert teams(history_for(env, ALICE)) == ["H1", "H2", "H3"]
    assert teams(history_for(env, BOB)) == ["H4", "H5", "H6"]
    assert teams(history_for(env, CAROL)) == []                                    # ni les combinés des autres, ni ceux de la maison
    assert len(history_for(env, None)["combos"]) == 3                              # sans identifiant (pages publiques) : tout


def test_les_combines_des_autres_apparaissent_quand_tous_leurs_matchs_ont_commence(env, monkeypatch):
    no_network(monkeypatch)
    write(env, "20261010_080000", [trio(1, kickoff=at(9))], owner=ALICE)             # commencés à 9 h
    write(env, "20261010_081000", [combo(leg(4, "V1", kickoff=at(9)), leg(5, "V1", kickoff=at(9)),
                                         leg(6, "V1", kickoff=at(20)))], owner=BOB)   # un match encore à jouer à 20 h
    write(env, "20261010_060000", [trio(7, kickoff=at(11))])
    assert teams(history_for(env, CAROL)) == ["H1", "H2", "H3", "H7", "H8", "H9"]      # le combiné de Bob reste caché
    assert teams(history_for(env, BOB)) == ["H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9"]   # le sien, il le voit toujours
    assert teams(history_for(env, ALICE)) == ["H1", "H2", "H3", "H7", "H8", "H9"]


def test_un_combine_recu_par_plusieurs_clients_est_visible_pour_chacun_une_seule_fois(env, monkeypatch):
    no_network(monkeypatch)
    write(env, "20261010_080000", [trio(1)], owner=ALICE)
    write(env, "20261010_081000", [trio(1)], owner=BOB)                              # exactement le même combiné
    for viewer in (ALICE, BOB):
        assert teams(history_for(env, viewer)) == ["H1", "H2", "H3"], viewer
    assert teams(history_for(env, CAROL)) == []
    assert len(history_for(env, None)["combos"]) == 1                                  # une seule ligne dans l'historique public


def test_un_combine_de_la_maison_regenere_par_un_client_le_reste_pour_les_deux(env, monkeypatch):
    no_network(monkeypatch)
    write(env, "20261010_060000", [trio(1)])
    write(env, "20261010_090000", [trio(1)], owner=ALICE)
    assert teams(history_for(env, ALICE)) == ["H1", "H2", "H3"] and teams(history_for(env, BOB)) == []


def test_le_bilan_ne_compte_que_les_combines_montres(env, monkeypatch):
    no_network(monkeypatch)
    for match_id in (1, 2, 3, 7, 8, 9):
        final(env, match_id, f"H{match_id}", f"A{match_id}", 2, 0)
    write(env, "20261009_080000", [trio(1, kickoff=at(20, "2026-10-09"))], owner=BOB)       # joué hier, gagné : visible pour tous
    write(env, "20261010_080000", [trio(4)], owner=ALICE)                                   # le sien, pas encore joué
    write(env, "20261010_081000", [trio(7)], owner=BOB)                                     # celui de Bob, à venir (score déjà en base)
    mine = history_for(env, ALICE)
    assert len(mine["combos"]) == 2 and (mine["summary"]["won"], mine["summary"]["pending"]) == (1, 1)
    other = history_for(env, CAROL)
    assert len(other["combos"]) == 1 and (other["summary"]["won"], other["summary"]["pending"]) == (1, 0)
    public = history_for(env, None)
    assert len(public["combos"]) == 3 and public["summary"]["won"] == 2


def test_un_match_sans_heure_est_juge_par_le_jour_de_la_generation(env, monkeypatch):
    no_network(monkeypatch)
    nohour = combo(leg(1, "V1", kickoff=None), leg(2, "V1", kickoff=None), leg(3, "V1", kickoff=None))
    write(env, "20261009_080000", [nohour], owner=ALICE)                             # généré hier : le jour est passé
    nohour_today = combo(leg(4, "V1", kickoff=None), leg(5, "V1", kickoff=None), leg(6, "V1", kickoff=None))
    write(env, "20261010_080000", [nohour_today], owner=ALICE)                       # généré aujourd'hui : pas fini
    assert teams(history_for(env, BOB)) == ["H1", "H2", "H3"]
    assert teams(history_for(env, ALICE)) == ["H1", "H2", "H3", "H4", "H5", "H6"]


def test_les_fichiers_aux_noms_inconnus_sont_ignores_sans_fausser_les_proprietaires(env, monkeypatch):
    no_network(monkeypatch)
    write(env, "20261010_080000", [trio(1)], owner="PAS-UN-IDENTIFIANT")                # nom invalide : pas un fichier de l'historique
    write(env, "20261010_081000", [trio(4)], owner=ALICE)
    assert teams(history_for(env, ALICE)) == ["H4", "H5", "H6"] and teams(history_for(env, None)) == ["H4", "H5", "H6"]
