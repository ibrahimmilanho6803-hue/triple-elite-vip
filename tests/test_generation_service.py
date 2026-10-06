import json
import logging
import os
import threading
import time

import pytest

import config
import generation_service
from generation_service import GenerationService
from pipeline import GenerationError


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def leg(match_id, kickoff=None):
    return {"match_id": match_id, "home_team": "A", "away_team": "B", "league": "Serie A", "type": "V1",
            "type_name": "Victoire A", "confidence": 70, "estimated_odds": 1.3, "kickoff": kickoff,
            "odds_source": "estimee", "category": "RESULTAT", "probability": 70.0}


def combo(kickoff=None):
    return {"predictions": [leg("1", kickoff), leg("2", kickoff), leg("3", kickoff)], "total_odds": 2.6,
            "avg_confidence": 70.0, "success_probability": 34.0, "leagues": ["Serie A"], "categories": ["RESULTAT"]}


class Pipeline:
    def __init__(self, kickoff=None, error=None, delay=0.0):
        self.calls, self.kickoff, self.error, self.delay = 0, kickoff, error, delay
        self.gate = threading.Event()
        self.gate.set()

    def __call__(self, progress=None):
        self.calls += 1
        if progress:
            progress("analyse", 40, "Analyse…")
        self.gate.wait(5)
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return {"combos": [combo(self.kickoff)], "meta": {"matches_total": 15}}


@pytest.fixture()
def dirs(tmp_path):
    return str(tmp_path / "cache"), str(tmp_path / "results")


def make(dirs, pipeline, clock=None):
    return GenerationService(pipeline=pipeline, cache_dir=dirs[0], results_dir=dirs[1], clock=clock or Clock())


def wait(service):
    if service._thread:
        service._thread.join(5)


def test_demarrage_progression_puis_resultat(dirs):
    pipeline = Pipeline()
    pipeline.gate.clear()
    service = make(dirs, pipeline)
    assert service.status() == {"state": "idle"}
    started = service.request_generation()
    assert started["state"] == "running"
    time.sleep(0.1)
    running = service.status()
    assert running["state"] == "running" and running["progress"]["percent"] == 40
    pipeline.gate.set()
    wait(service)
    done = service.status()
    assert done["state"] == "done" and done["cached"] is True
    assert len(done["combos"]) == 1 and done["meta"]["matches_total"] == 15
    assert done["generated_at"].endswith("+00:00")
    files = os.listdir(dirs[1])
    assert len(files) == 1 and files[0].startswith("combo_") and files[0].endswith(".json")
    assert json.load(open(os.path.join(dirs[1], files[0]), encoding="utf-8"))[0]["total_odds"] == 2.6


def test_le_cache_frais_evite_une_nouvelle_generation(dirs):
    pipeline = Pipeline()
    service = make(dirs, pipeline)
    service.request_generation()
    wait(service)
    for _ in range(5):
        assert service.request_generation()["state"] == "done"
    assert pipeline.calls == 1


def test_clics_simultanes_un_seul_travail(dirs):
    pipeline = Pipeline(delay=0.3)
    service = make(dirs, pipeline)
    results = []
    threads = [threading.Thread(target=lambda: results.append(service.request_generation()["state"]))
               for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wait(service)
    assert pipeline.calls == 1
    assert set(results) <= {"running", "done"}


def test_cache_expire_apres_la_duree(dirs):
    clock, pipeline = Clock(), Pipeline()
    service = make(dirs, pipeline, clock)
    service.request_generation()
    wait(service)
    clock.now += config.CACHE_MINUTES * 60 - 1
    assert service.status()["state"] == "done"
    clock.now += 2
    assert service.status() == {"state": "idle"}
    assert service.request_generation()["state"] == "running"
    wait(service)
    assert pipeline.calls == 2


def test_cache_invalide_quand_un_match_a_commence(dirs):
    clock = Clock()
    kickoff = datetime_iso(clock.now + 3600)
    service = make(dirs, Pipeline(kickoff=kickoff), clock)
    service.request_generation()
    wait(service)
    assert service.status()["state"] == "done"
    clock.now += 3601
    assert service.status() == {"state": "idle"}


def datetime_iso(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def test_echec_normal_puis_pause_avant_nouvelle_tentative(dirs, caplog):
    clock = Clock()
    pipeline = Pipeline(error=GenerationError("Aucun combiné assez fiable pour le moment. Réessaie plus tard."))
    service = make(dirs, pipeline, clock)
    service.request_generation()
    wait(service)
    state = service.status()
    assert state == {"state": "error", "error": "Aucun combiné assez fiable pour le moment. Réessaie plus tard."}
    assert service.request_generation()["state"] == "error"     # pause : aucune relance
    assert pipeline.calls == 1
    clock.now += generation_service.FAILURE_COOLDOWN_SECONDS + 1
    assert service.request_generation()["state"] == "running"
    wait(service)
    assert pipeline.calls == 2


def test_erreur_inattendue_message_generique(dirs):
    service = make(dirs, Pipeline(error=RuntimeError("secret interne")))
    service.request_generation()
    wait(service)
    state = service.status()
    assert state["state"] == "error"
    assert "secret interne" not in json.dumps(state)
    assert state["error"] == generation_service.MSG_GENERIC_ERROR


def test_plafond_quotidien(dirs, monkeypatch):
    monkeypatch.setattr(config, "MAX_GENERATIONS_PAR_JOUR", 2)
    clock, pipeline = Clock(), Pipeline()
    service = make(dirs, pipeline, clock)
    for _ in range(2):
        assert service.request_generation()["state"] == "running"
        wait(service)
        clock.now += config.CACHE_MINUTES * 60 + 1          # le cache expire entre deux demandes
    assert service.generations_today() == 2
    stale = service.request_generation()
    assert stale["state"] == "done" and stale["stale"] is True
    assert stale["notice"] == generation_service.MSG_DAILY_LIMIT and len(stale["combos"]) == 1
    assert pipeline.calls == 2
    clock.now += 86400                                     # le lendemain, le quota est remis à zéro
    assert service.generations_today() == 0
    assert service.request_generation()["state"] == "running"
    wait(service)


def test_plafond_atteint_sans_aucun_cache(dirs, monkeypatch):
    monkeypatch.setattr(config, "MAX_GENERATIONS_PAR_JOUR", 1)
    clock = Clock()
    service = make(dirs, Pipeline(error=GenerationError("Aucun combiné")), clock)
    service.request_generation()
    wait(service)
    clock.now += generation_service.FAILURE_COOLDOWN_SECONDS + 1
    assert service.request_generation() == {"state": "error", "error": generation_service.MSG_DAILY_LIMIT_EMPTY}


def test_travail_perdu_apres_redemarrage(dirs):
    clock = Clock()
    service = make(dirs, Pipeline(), clock)
    os.makedirs(dirs[0])
    # Travail « en cours » lancé par un processus disparu.
    with open(os.path.join(dirs[0], "job.json"), "w") as f:
        json.dump({"state": "running", "pid": 2 ** 22 + 12345, "started_ts": clock.now, "percent": 30}, f)
    state = service.status()
    assert state["state"] == "error" and state["error"] == generation_service.MSG_GENERIC_ERROR
    clock.now += generation_service.FAILURE_COOLDOWN_SECONDS + 1
    assert service.request_generation()["state"] == "running"
    wait(service)


def test_travail_trop_long_considere_perdu(dirs):
    clock = Clock()
    service = make(dirs, Pipeline(), clock)
    os.makedirs(dirs[0])
    with open(os.path.join(dirs[0], "job.json"), "w") as f:
        json.dump({"state": "running", "pid": os.getppid(), "started_ts": clock.now - config.JOB_MAX_SECONDS - 5}, f)
    assert service.status()["state"] == "error"


def test_fichiers_corrompus_ne_plantent_pas(dirs):
    service = make(dirs, Pipeline())
    os.makedirs(dirs[0])
    for name in ("last_generation.json", "job.json", "usage.json"):
        with open(os.path.join(dirs[0], name), "w") as f:
            f.write("{pas du json")
    assert service.status() == {"state": "idle"}
    assert service.generations_today() == 0
    assert service.request_generation()["state"] == "running"
    wait(service)
    assert service.status()["state"] == "done"


def test_disque_inaccessible_le_service_continue_en_memoire(tmp_path):
    blocker = tmp_path / "fichier"
    blocker.write_text("x")
    service = GenerationService(pipeline=Pipeline(), cache_dir=str(blocker / "cache"),
                                results_dir=str(blocker / "results"), clock=Clock())
    assert service.request_generation()["state"] == "running"
    wait(service)
    done = service.status()
    assert done["state"] == "done" and len(done["combos"]) == 1       # résultat gardé en mémoire
    assert service.request_generation()["state"] == "done"
