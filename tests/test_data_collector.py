import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests

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


def kickoff_event(i, day, hour, minute=0, status="NS", **extra):
    """Un match à venir de TheSportsDB (coup d'envoi à `hour` h UTC, le `day`)."""
    return event(i, f"H{i}", f"A{i}", None, None, status=status, day=day,
                 strTimestamp=f"{day}T{hour:02d}:{minute:02d}:00", strTime=f"{hour:02d}:{minute:02d}:00", **extra)


NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def test_matchs_du_jour_seulement(db, monkeypatch):
    events = [kickoff_event(91, "2026-10-10", 11),                                            # a commencé : compté, pas proposé
              kickoff_event(90, "2026-10-10", 15, status="PST"),                              # reporté : ni compté ni proposé
              kickoff_event(92, "2026-10-10", 14), kickoff_event(93, "2026-10-10", 16, 30),
              kickoff_event(94, "2026-10-10", 18, 30), kickoff_event(95, "2026-10-10", 20, 45),
              kickoff_event(96, "2026-10-11", 14), kickoff_event(97, "2026-10-09", 20),       # demain et hier : jamais proposés
              kickoff_event(92, "2026-10-10", 14)]                                            # le même match deux fois
    monkeypatch.setattr(data_collector.requests, "get", FakeWeb({"eventsnextleague": events}))
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328"})
    matchday = DataCollector().get_matchday(now=NOW)
    assert matchday["day"] == "2026-10-10" and matchday["complete"] is True
    assert [m["id"] for m in matchday["matches"]] == ["92", "93", "94", "95"]      # tous ceux à venir, sans plafond
    assert matchday["total"] == 5                                            # 91, 92, 93, 94, 95 : les matchs du jour vus
    first = matchday["matches"][0]
    assert first["kickoff"] == "2026-10-10T14:00:00+00:00" and first["date"] == "2026-10-10 14:00:00"
    assert first["league"] == "Premier League" and first["home_team"] == "H92" and first["away_team"] == "A92"


def test_aucun_plafond_par_championnat_et_matchs_classes_par_heure_de_coup_d_envoi(db, monkeypatch):
    """Le choix des matchs à analyser se fait ensuite (variety.choose_pool) : la liste du jour est complète, dans l'ordre des
    coups d'envoi pour chaque championnat, quel que soit l'ordre dans lequel TheSportsDB les donne."""
    hours = [20, 14, 18, 16, 21, 13, 19, 15, 17, 22, 23, 12]                                  # douze matchs, en désordre
    events = [kickoff_event(100 + n, "2026-10-10", hour) for n, hour in enumerate(hours)]
    monkeypatch.setattr(data_collector.requests, "get", FakeWeb({"eventsnextleague": events}))
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328"})
    matchday = DataCollector().get_matchday(now=datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc))
    kickoffs = [m["kickoff"] for m in matchday["matches"]]
    assert len(kickoffs) == 12 and kickoffs == sorted(kickoffs) and matchday["total"] == 12


def test_get_upcoming_matches_ne_rend_que_les_matchs_du_jour_a_venir(db, monkeypatch):
    events = [kickoff_event(1, "2026-10-10", 11), kickoff_event(2, "2026-10-10", 14), kickoff_event(3, "2026-10-12", 14)]
    monkeypatch.setattr(data_collector.requests, "get", FakeWeb({"eventsnextleague": events}))
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328"})
    assert [m["id"] for m in DataCollector().get_upcoming_matches(now=NOW)] == ["2"]


def test_un_jour_sans_match_rien_ne_vient_d_un_autre_jour(db, monkeypatch):
    """Les prochains matchs sont dans trois jours (trêve internationale...) : aucun n'est proposé aujourd'hui."""
    events = [kickoff_event(i, f"2026-10-{12 + i}", 14) for i in range(1, 6)]
    events.insert(1, kickoff_event(90, "2026-10-13", 14, status="PST"))
    monkeypatch.setattr(data_collector.requests, "get", FakeWeb({"eventsnextleague": events}))
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328"})
    matchday = DataCollector().get_matchday(now=NOW)
    assert matchday == {"day": "2026-10-10", "matches": [], "total": 0, "complete": True}


def test_un_match_sans_heure_compte_pour_son_jour(db, monkeypatch):
    today = event(1, "H1", "A1", None, None, status="NS", day="2026-10-10")                  # ni horodatage ni heure
    tomorrow = event(2, "H2", "A2", None, None, status="NS", day="2026-10-11")
    monkeypatch.setattr(data_collector.requests, "get", FakeWeb({"eventsnextleague": [today, tomorrow]}))
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328"})
    matchday = DataCollector().get_matchday(now=NOW)
    assert [m["id"] for m in matchday["matches"]] == ["1"] and matchday["matches"][0]["kickoff"] is None
    assert matchday["total"] == 1
    for broken in ("", "demain", None):                                                      # date illisible : jamais aujourd'hui
        odd = event(3, "H3", "A3", None, None, status="NS", day=broken)
        monkeypatch.setattr(data_collector.requests, "get", FakeWeb({"eventsnextleague": [odd]}))
        assert DataCollector().get_matchday(now=NOW)["total"] == 0


def test_le_jour_est_celui_d_utc_meme_si_l_horloge_donnee_ne_l_est_pas(db, monkeypatch):
    late = kickoff_event(1, "2026-10-10", 23, 30)
    after_midnight = kickoff_event(2, "2026-10-11", 0, 30)
    monkeypatch.setattr(data_collector.requests, "get", FakeWeb({"eventsnextleague": [late, after_midnight]}))
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328"})
    paris_after_midnight = datetime(2026, 10, 11, 0, 30, tzinfo=timezone(timedelta(hours=2)))   # 22 h 30 UTC, le 10
    matchday = DataCollector().get_matchday(now=paris_after_midnight)
    assert matchday["day"] == "2026-10-10" and [m["id"] for m in matchday["matches"]] == ["1"]


def test_sans_horloge_le_jour_est_celui_d_aujourd_hui_en_utc(db, monkeypatch):
    monkeypatch.setattr(data_collector.requests, "get", FakeWeb({"eventsnextleague": []}))
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328"})
    before = datetime.now(timezone.utc).date().isoformat()
    day = DataCollector().get_matchday()["day"]
    assert day in (before, datetime.now(timezone.utc).date().isoformat())


def test_un_championnat_muet_rend_le_constat_incomplet_sans_perdre_les_autres(db, monkeypatch):
    web = FakeWeb({"id=4328": OSError("réseau"), "id=4335": [kickoff_event(1, "2026-10-10", 14)]})
    monkeypatch.setattr(data_collector.requests, "get", web)
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328", "La Liga": "4335"})
    matchday = DataCollector().get_matchday(now=NOW)
    assert matchday["complete"] is False                                     # « aucun match » ne prouverait alors rien
    assert [m["id"] for m in matchday["matches"]] == ["1"] and matchday["matches"][0]["league"] == "La Liga"


def test_un_seul_appel_par_championnat(db, monkeypatch):
    web = FakeWeb({"eventsnextleague": [kickoff_event(1, "2026-10-10", 14)]})
    monkeypatch.setattr(data_collector.requests, "get", web)
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328", "La Liga": "4335", "Serie A": "4332"})
    DataCollector().get_matchday(now=NOW)
    assert len(web.calls) == 3 and all("eventsnextleague.php?id=" in url for url in web.calls)


class Stuck:
    """Remplace requests.get : un championnat ne répond JAMAIS (jusqu'à ce que le test le libère), les autres répondent."""

    def __init__(self, answers, stuck="id=4328"):
        self.answers, self.stuck, self.release, self.calls = answers, stuck, threading.Event(), []

    def __call__(self, url, timeout=None, **kwargs):
        if self.stuck in url:
            self.calls.append(url)
            self.release.wait(10)
            raise OSError("trop tard")
        return self.answers(url, timeout=timeout, **kwargs)


def test_un_championnat_qui_ne_repond_jamais_est_abandonne_apres_le_delai(db, monkeypatch, caplog):
    """Le 11 octobre, un appel resté sans réponse (même pas une erreur) a figé « Générer les combinés » pendant 5 minutes."""
    web = Stuck(FakeWeb({"id=4335": [kickoff_event(1, "2026-10-10", 14)]}))
    monkeypatch.setattr(data_collector.requests, "get", web)
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328", "La Liga": "4335"})
    monkeypatch.setattr(DataCollector, "FETCH_DEADLINE", 0.4)
    monkeypatch.setattr(DataCollector, "RETRY_DEADLINE", 0.4)
    started = time.monotonic()
    with caplog.at_level(logging.WARNING):
        matchday = DataCollector().get_matchday(now=NOW)
    assert time.monotonic() - started < 4                                   # 0,4 s + 0,4 s de deuxième essai, et non 10 s
    assert matchday["complete"] is False                                    # rien n'est conclu sur un championnat muet
    assert [m["id"] for m in matchday["matches"]] == ["1"] and matchday["matches"][0]["league"] == "La Liga"
    assert len(web.calls) == 2                                              # le premier essai, puis un seul de plus
    assert "sans réponse" in caplog.text and "eventsnextleague.php?id=4328" in caplog.text
    web.release.set()


def test_la_collecte_continue_sans_le_championnat_qui_ne_repond_pas(db, monkeypatch):
    answers = FakeWeb({"id=4335": [event(1, "Real", "Barca", "1", "1")]})
    web = Stuck(answers)
    monkeypatch.setattr(data_collector.requests, "get", web)
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328", "La Liga": "4335"})
    monkeypatch.setattr(DataCollector, "FETCH_DEADLINE", 0.4)
    started = time.monotonic()
    summary = DataCollector().collect_all_data()
    assert time.monotonic() - started < 4
    assert summary["Premier League"] == 0 and summary["La Liga"] >= 1
    web.release.set()


def test_un_championnat_qui_echoue_une_fois_a_une_deuxieme_chance(db, monkeypatch):
    good = FakeWeb({"eventsnextleague": [kickoff_event(1, "2026-10-10", 14)]})
    attempts = []

    def flaky(url, timeout=None, **kwargs):
        if "id=4328" in url:
            attempts.append(url)
            if len(attempts) == 1:
                raise OSError("coupure passagère")
        return good(url, timeout=timeout, **kwargs)

    monkeypatch.setattr(data_collector.requests, "get", flaky)
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328", "La Liga": "4335"})
    matchday = DataCollector().get_matchday(now=NOW)
    assert matchday["complete"] is True and len(attempts) == 2
    assert matchday["total"] == 1 and [m["id"] for m in matchday["matches"]] == ["1"]


def test_un_championnat_en_panne_n_a_qu_un_seul_nouvel_essai(db, monkeypatch):
    web = FakeWeb({"id=4328": OSError("réseau"), "id=4335": [kickoff_event(1, "2026-10-10", 14)]})
    monkeypatch.setattr(data_collector.requests, "get", web)
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328", "La Liga": "4335"})
    matchday = DataCollector().get_matchday(now=NOW)
    assert matchday["complete"] is False
    assert [url for url in web.calls if "id=4328" in url] != [] and len([u for u in web.calls if "id=4328" in u]) == 2
    assert len([u for u in web.calls if "id=4335" in u]) == 1                 # celui qui a répondu n'est pas redemandé


def test_la_cle_d_api_n_apparait_jamais_dans_les_journaux(db, monkeypatch, caplog):
    secret = "CLE-PAYANTE-123456"
    monkeypatch.setattr(config, "SPORTSDB_API_KEY", secret)

    def failing(url, timeout=None, **kwargs):
        path = url.split("thesportsdb.com", 1)[1]
        raise requests.exceptions.ConnectionError(
            f"HTTPSConnectionPool(host='www.thesportsdb.com', port=443): Max retries exceeded with url: {path} "
            f"(Caused by NameResolutionError(\"Failed to resolve 'www.thesportsdb.com'\"))")

    monkeypatch.setattr(data_collector.requests, "get", failing)
    monkeypatch.setattr(config, "LEAGUES", {"Premier League": "4328"})
    with caplog.at_level(logging.INFO):
        collector = DataCollector()
        collector.get_matchday(now=NOW)
        collector.collect_all_data()
    assert secret not in caplog.text
    assert "eventsnextleague.php?id=4328" in caplog.text and "json/***/" in caplog.text    # l'appel reste reconnaissable


def test_hide_keys_et_call_label():
    hide, label = data_collector.hide_keys, data_collector.call_label
    assert hide("url: /api/v1/json/ABC123/eventsnextleague.php?id=4328 (Caused by x)") == \
        "url: /api/v1/json/***/eventsnextleague.php?id=4328 (Caused by x)"
    assert hide("https://x.example/odds?regions=eu&apiKey=SECRET&markets=h2h") == \
        "https://x.example/odds?regions=eu&apiKey=***&markets=h2h"
    assert hide("rien à cacher : 3 matchs") == "rien à cacher : 3 matchs"
    assert hide(ValueError("clé /json/ABC/x")) == "clé /json/***/x"
    assert label("https://www.thesportsdb.com/api/v1/json/ABC123/eventsseason.php?id=4328&s=2026-2027") == \
        "eventsseason.php?id=4328&s=2026-2027"


def test_run_parallel_garde_l_ordre_borne_l_attente_et_consigne_les_exceptions(caplog):
    release = threading.Event()

    def work(n):
        if n == 1:
            release.wait(10)                  # ne revient pas
        if n == 2:
            raise ValueError("boum")
        return n * 10

    started = time.monotonic()
    with caplog.at_level(logging.ERROR):
        out = data_collector.run_parallel(work, [0, 1, 2, 3], deadline=0.3, name="essai", describe=lambda n: f"n°{n}")
    assert out == [0, None, None, 30] and time.monotonic() - started < 3
    assert "essai : 1 appel(s) sans réponse" in caplog.text and "n°1" in caplog.text and "boum" in caplog.text
    assert "Fil de « n°1 »" in caplog.text and "release.wait(10)" in caplog.text        # où l'appel abandonné est resté bloqué
    release.set()
    assert data_collector.run_parallel(work, [], deadline=1) == []


def test_run_parallel_ne_depasse_pas_le_maximum_simultane():
    state = {"live": 0, "peak": 0}
    lock = threading.Lock()

    def work(n):
        with lock:
            state["live"] += 1
            state["peak"] = max(state["peak"], state["live"])
        time.sleep(0.05)
        with lock:
            state["live"] -= 1
        return n

    assert data_collector.run_parallel(work, range(10), deadline=10, max_workers=3) == list(range(10))
    assert state["peak"] <= 3


def test_run_parallel_ne_lance_plus_rien_une_fois_le_delai_passe():
    started = []

    def work(n):
        started.append(n)
        time.sleep(0.6)
        return n

    assert data_collector.run_parallel(work, [0, 1, 2], deadline=0.3, max_workers=1) == [None, None, None]
    time.sleep(0.8)                                                           # le premier finit, personne d'autre ne démarre
    assert started == [0]


def test_event_day():
    assert data_collector.event_day({"dateEvent": "2026-10-10"}).isoformat() == "2026-10-10"
    for odd in ({}, {"dateEvent": None}, {"dateEvent": ""}, {"dateEvent": "10/10/2026"}):
        assert data_collector.event_day(odd) is None
