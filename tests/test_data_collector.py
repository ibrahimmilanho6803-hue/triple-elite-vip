import sqlite3
from datetime import datetime, timezone

import pytest

import config
import data_collector
from data_collector import DataCollector, event_start, is_final_status, parse_score


@pytest.fixture()
def db(monkeypatch, tmp_path):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    return path


def event(i, home="A", away="B", hs="2", aws="1", status="FT", day="2026-10-03", season="2026-2027", **extra):
    base = {"idEvent": str(i), "strHomeTeam": home, "strAwayTeam": away, "intHomeScore": hs,
            "intAwayScore": aws, "dateEvent": day, "strStatus": status, "strSeason": season}
    base.update(extra)
    return base


class FakeWeb:
    """Remplace requests.get : {fragment d'URL: évènements}."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, timeout=None, **kwargs):
        self.calls.append(url)
        for fragment, events in self.routes.items():
            if fragment in url:
                if isinstance(events, Exception):
                    raise events
                payload = {"events": events}

                class Response:
                    def json(self_inner):
                        return payload
                return Response()
        raise AssertionError(f"URL inattendue : {url}")


def test_parse_score_et_statuts():
    assert parse_score("0") == 0 and parse_score(3) == 3
    assert parse_score(None) is None and parse_score("") is None and parse_score("abc") is None
    assert is_final_status("FT") and is_final_status("") and is_final_status(None) and is_final_status("AET")
    assert not is_final_status("NS") and not is_final_status("1h") and not is_final_status("PST")


def test_event_start():
    assert event_start({"strTimestamp": "2026-10-17T14:00:00"}) == datetime(2026, 10, 17, 14, tzinfo=timezone.utc)
    assert event_start({"strTimestamp": "2026-10-17T16:00:00+02:00"}) == datetime(2026, 10, 17, 14, tzinfo=timezone.utc)
    assert event_start({"dateEvent": "2026-10-17", "strTime": "14:00:00"}) == datetime(2026, 10, 17, 14, tzinfo=timezone.utc)
    assert event_start({"dateEvent": "2026-10-17"}) is None
    assert event_start({"strTimestamp": "n'importe quoi"}) is None
    assert event_start({}) is None


def test_collecte_scores_et_statistiques(db, monkeypatch):
    web = FakeWeb({
        "eventspastleague.php?id=4328": [event(1, "Arsenal", "Chelsea", "2", "1"),
                                         event(2, "Chelsea", "Arsenal", "0", "0", day="2026-09-20")],
        "eventsseason.php?id=4328": [event(1, "Arsenal", "Chelsea", "2", "1"),
                                     event(3, "Arsenal", "Everton", "3", "0", day="2026-09-10"),
                                     event(4, "Everton", "Chelsea", None, None, status="NS", day="2026-12-01"),
                                     event(5, "Everton", "Arsenal", "", "", status="", day="2026-12-08")],
    })
    monkeypatch.setattr(data_collector.requests, "get", web)
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328"})
    collector = DataCollector()
    summary = collector.collect_all_data()
    assert summary == {"Premier League": 4}                  # 2 (passés) + 2 (saison avec score)
    conn = sqlite3.connect(db)
    ids = {r[0] for r in conn.execute("SELECT id FROM matches")}
    assert ids == {1, 2, 3}                                  # les matchs sans score ne sont jamais enregistrés
    arsenal = conn.execute("SELECT matches_played, wins, draws, losses, goals_for, goals_against, btts_yes, btts_no "
                           "FROM team_stats WHERE team_name = 'Arsenal'").fetchone()
    assert arsenal == (3, 2, 1, 0, 1.67, 0.33, 1, 2)         # 2-1 (D), 0-0 (E), 3-0 (D)
    # Une équipe n'ayant joué qu'à l'extérieur est bien prise en compte (UNION domicile/extérieur).
    assert conn.execute("SELECT matches_played FROM team_stats WHERE team_name = 'Everton'").fetchone()[0] == 1
    conn.close()
    # Ces 5 appels réseau n'ont duré qu'un aller-retour par URL (parallèle) : 1 championnat x 2 types.
    assert len(web.calls) == 2


def test_un_score_connu_n_est_jamais_efface(db, monkeypatch):
    collector = DataCollector()
    collector.save_match(event(9, "A", "B", "2", "1"), "Serie A")
    collector.save_match(event(9, "A", "B", None, None, status="FT"), "Serie A")
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT home_score, away_score FROM matches WHERE id = 9").fetchone() == (2, 1)
    conn.close()


def test_score_en_direct_non_enregistre(db):
    collector = DataCollector()
    collector.save_match(event(10, "A", "B", "1", "0", status="1H"), "Serie A")
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT home_score, away_score, status FROM matches WHERE id = 10").fetchone() == (None, None, "1H")
    conn.close()


def test_une_erreur_reseau_ne_bloque_pas_les_autres_championnats(db, monkeypatch):
    web = FakeWeb({"id=4328": OSError("réseau"), "id=4335": [event(1, "Real", "Barca", "1", "1")]})
    monkeypatch.setattr(data_collector.requests, "get", web)
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328", "La Liga": "4335"})
    summary = DataCollector().collect_all_data()
    assert summary["Premier League"] == 0 and summary["La Liga"] >= 1


def test_matchs_a_venir(db, monkeypatch):
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    future = [event(i, f"H{i}", f"A{i}", None, None, status="NS", strTimestamp=f"2026-10-{11 + i}T14:00:00",
                    dateEvent=f"2026-10-{11 + i}", strTime="14:00:00") for i in range(1, 6)]
    future.insert(1, event(90, "Postponed", "FC", None, None, status="PST", strTimestamp="2026-10-12T14:00:00"))
    future.insert(0, event(91, "Already", "Started", None, None, status="NS", strTimestamp="2026-10-10T11:00:00"))
    monkeypatch.setattr(data_collector.requests, "get", FakeWeb({"eventsnextleague": future}))
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328"})
    matches = DataCollector().get_upcoming_matches(now=now)
    assert [m["id"] for m in matches] == ["1", "2", "3"]       # 3 par championnat, sans reporté ni déjà commencé
    assert matches[0]["kickoff"] == "2026-10-12T14:00:00+00:00"
    assert matches[0]["league"] == "Premier League" and matches[0]["home_team"] == "H1"
    assert matches[0]["date"] == "2026-10-12 14:00:00"
