"""Génération automatique quotidienne (daily_generation.py) : facultative, et jamais plus d'appels payants que prévu."""
import logging
import threading
from datetime import datetime, timezone

import pytest

import config
import daily_generation
from daily_generation import DailyGeneration
from generation_service import FAILURE_COOLDOWN_SECONDS, GenerationService
from pipeline import GenerationError
from site_fakes import sample_combos


def ts(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc).timestamp()


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class Pipeline:
    """Chaîne de génération simulée : instantanée, compte ses appels (chacun serait un appel payant)."""

    def __init__(self, error=None, gate=None):
        self.calls = 0
        self.error = error
        self.gate = gate

    def __call__(self, progress=None):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        return {"combos": sample_combos(), "meta": {}}


def build(tmp_path, clock, pipeline=None, hour=6, **kwargs):
    pipeline = pipeline or Pipeline()
    service = GenerationService(pipeline=pipeline, cache_dir=str(tmp_path / "cache"),
                                results_dir=str(tmp_path / "results"), clock=clock)
    return DailyGeneration(service, hour, clock=clock, **kwargs), service, pipeline


def settle(service):
    if service._thread is not None:
        service._thread.join(5)


# --------------------------------------------------------------------------
# Quand elle part, quand elle se tait
# --------------------------------------------------------------------------

def test_rien_ne_part_avant_l_heure_choisie(tmp_path):
    clock = Clock(ts(8, 5, 59))
    daily, service, pipeline = build(tmp_path, clock, hour=6)
    assert daily.tick() == "attente"
    assert pipeline.calls == 0 and service.generations_today() == 0


def test_une_generation_part_a_l_heure_choisie_et_une_seule_par_jour(tmp_path):
    clock = Clock(ts(8, 6, 0))
    daily, service, pipeline = build(tmp_path, clock, hour=6)
    assert daily.tick() == "lancee"
    settle(service)
    assert pipeline.calls == 1 and service.latest() is not None
    for hour in (7, 12, 23):
        clock.now = ts(8, hour, 30)
        assert daily.tick() == "faite"
    assert pipeline.calls == 1                                         # personne ne repaie l'IA le même jour


def test_une_generation_faite_par_un_abonne_dans_la_journee_suffit(tmp_path):
    clock = Clock(ts(8, 9, 0))
    daily, service, pipeline = build(tmp_path, clock, hour=6)
    service.request_generation()                                       # le clic d'un abonné
    settle(service)
    assert daily.tick() == "faite"
    assert pipeline.calls == 1


def test_la_generation_de_la_veille_ne_compte_pas(tmp_path):
    clock = Clock(ts(7, 20, 0))
    daily, service, pipeline = build(tmp_path, clock, hour=6)
    service.request_generation()
    settle(service)
    clock.now = ts(8, 6, 30)                                           # le lendemain, après l'heure
    assert daily.tick() == "lancee"
    settle(service)
    assert pipeline.calls == 2


def test_un_travail_deja_en_cours_est_rejoint_sans_en_lancer_un_second(tmp_path):
    clock = Clock(ts(8, 7, 0))
    gate = threading.Event()
    daily, service, pipeline = build(tmp_path, clock, Pipeline(gate=gate), hour=6)
    service.request_generation()                                       # un abonné vient de cliquer
    assert daily.tick() == "running"
    gate.set()
    settle(service)
    assert pipeline.calls == 1
    assert service.generations_today() == 1


def test_des_combines_encore_frais_de_la_veille_au_soir_ne_declenchent_pas_d_appel_payant(tmp_path):
    clock = Clock(ts(7, 23, 30))
    daily, service, pipeline = build(tmp_path, clock, hour=0)
    service.request_generation()
    settle(service)
    clock.now = ts(8, 0, 10)                                           # 40 minutes plus tard : le cache est encore frais
    assert daily.tick() == "done"
    assert pipeline.calls == 1


def test_le_plafond_quotidien_du_service_est_respecte(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_GENERATIONS_PAR_JOUR", 1)
    clock = Clock(ts(8, 6, 0))
    daily, service, pipeline = build(tmp_path, clock, hour=6)
    service._count_generation()                                        # le plafond du jour est déjà atteint
    assert daily.tick() == "error"                                     # le service refuse : rien ne part
    assert pipeline.calls == 0 and service.generations_today() == 1


# --------------------------------------------------------------------------
# Pannes : pas de série d'appels payants
# --------------------------------------------------------------------------

def test_apres_un_echec_le_service_fait_une_pause_puis_on_reessaie_dans_la_limite_des_tentatives(tmp_path):
    clock = Clock(ts(8, 6, 0))
    daily, service, pipeline = build(tmp_path, clock, Pipeline(error=GenerationError("panne de l'IA")), hour=6,
                                     max_attempts=3)
    assert daily.tick() == "lancee"
    settle(service)
    clock.now += 60
    assert daily.tick() == "error"                                      # pause du service : rien de relancé
    assert pipeline.calls == 1

    outcomes = []
    for _ in range(4):
        clock.now += FAILURE_COOLDOWN_SECONDS + 1
        outcomes.append(daily.tick())
        settle(service)
    assert outcomes == ["lancee", "lancee", "abandon", "abandon"]
    assert pipeline.calls == 3                                          # 3 tentatives dans la journée, pas une de plus


def test_les_tentatives_repartent_le_lendemain(tmp_path):
    clock = Clock(ts(8, 6, 0))
    daily, service, pipeline = build(tmp_path, clock, Pipeline(error=GenerationError("panne")), hour=6, max_attempts=1)
    assert daily.tick() == "lancee"
    settle(service)
    clock.now += FAILURE_COOLDOWN_SECONDS + 1
    assert daily.tick() == "abandon"
    clock.now = ts(9, 6, 5)
    assert daily.tick() == "lancee"
    settle(service)
    assert pipeline.calls == 2


def test_une_entree_de_cache_illisible_n_empeche_ni_ne_declenche_a_tort(tmp_path):
    clock = Clock(ts(8, 7, 0))
    daily, service, pipeline = build(tmp_path, clock, hour=6)
    service._write("last_generation.json", {"combos": sample_combos(), "generated_ts": "hier"})
    assert daily.tick() == "lancee"                                    # pas de date lisible : comme s'il n'y avait rien
    settle(service)
    assert pipeline.calls == 1


# --------------------------------------------------------------------------
# La boucle de fond
# --------------------------------------------------------------------------

class StopLoop(BaseException):
    pass


def test_la_boucle_survit_a_une_verification_en_echec(tmp_path, monkeypatch, caplog):
    daily, _, _ = build(tmp_path, Clock(ts(8, 7, 0)))
    ticks = []

    def tick():
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("disque en panne")
        return "faite"

    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise StopLoop

    monkeypatch.setattr(daily, "tick", tick)
    monkeypatch.setattr(daily_generation.time, "sleep", sleep)
    with caplog.at_level(logging.ERROR, logger="daily_generation"), pytest.raises(StopLoop):
        daily._loop()
    assert len(ticks) == 3 and sleeps == [daily_generation.CHECK_EVERY] * 3
    assert "vérification en échec" in caplog.text


def test_la_surveillance_ne_demarre_qu_une_fois(tmp_path, monkeypatch):
    daily, _, _ = build(tmp_path, Clock(ts(8, 7, 0)))
    started = threading.Event()

    def loop():
        started.set()

    monkeypatch.setattr(daily, "_loop", loop)
    assert daily.start() is daily
    first = daily._thread
    assert started.wait(5)
    daily.start()
    assert daily._thread is first
    assert first.daemon is True and first.name == "daily-generation"


def test_le_plafond_de_tentatives_vient_de_la_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AUTO_GENERATE_MAX_ATTEMPTS", 2)
    daily, _, _ = build(tmp_path, Clock(ts(8, 7, 0)))
    assert daily.max_attempts == 2
    assert DailyGeneration(object(), 6, max_attempts=5).max_attempts == 5
