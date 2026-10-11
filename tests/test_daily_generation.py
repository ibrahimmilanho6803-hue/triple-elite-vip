"""Génération automatique quotidienne (daily_generation.py) : facultative, et jamais plus d'appels payants que prévu."""
import logging
import threading
from datetime import datetime, timezone

import pytest

import config
import daily_generation
from daily_generation import DailyGeneration
from generation_service import (FAILURE_COOLDOWN_SECONDS, MATCHDAY_CHECK_EVERY, MATCHDAY_RETRY_AFTER,
                                GenerationService)
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
    """Chaîne de génération simulée : instantanée, compte ses appels (chacun serait un appel payant). Comme la vraie, elle
    note le jour des matchs avec lesquels elle compose les combinés (build() lui donne l'horloge du test)."""

    def __init__(self, error=None, gate=None):
        self.calls = 0
        self.error = error
        self.gate = gate
        self.clock = None

    def __call__(self, progress=None):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        day = datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime("%Y-%m-%d")
        return {"combos": sample_combos(), "meta": {"day": day, "day_total": 9, "matches_total": 9}}


class Matchday:
    """Source des matchs du jour simulée : compte ses appels (chacun serait cinq appels réseau, jamais un appel payant)."""

    def __init__(self, upcoming=9, total=None, complete=True, error=None):
        self.upcoming, self.total, self.complete, self.error = upcoming, total, complete, error
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        total = self.upcoming if self.total is None else self.total
        return {"day": "2026-10-08", "matches": [{"id": str(n)} for n in range(self.upcoming)], "total": total,
                "complete": self.complete}


def build(tmp_path, clock, pipeline=None, hour=6, matchday=None, **kwargs):
    pipeline = pipeline or Pipeline()
    pipeline.clock = clock
    service = GenerationService(pipeline=pipeline, cache_dir=str(tmp_path / "cache"),
                                results_dir=str(tmp_path / "results"), clock=clock,
                                matchday_source=matchday or Matchday())
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
# Les matchs du jour : pas de championnat, pas d'appel payant
# --------------------------------------------------------------------------

def test_une_generation_d_avant_les_matchs_du_jour_seulement_ne_compte_pas(tmp_path):
    """Ses combinés portaient sur les prochains jours : elle ne dispense pas de composer ceux d'aujourd'hui."""
    clock = Clock(ts(8, 12, 0))
    daily, service, pipeline = build(tmp_path, clock, hour=6)
    made = ts(8, 8, 0)                                                 # ce matin, mais sans « day » dans ses métadonnées
    service._write("last_generation.json", {"generated_ts": made, "generated_at": "x", "combos": sample_combos(),
                                            "meta": {"matches_total": 9}})
    assert daily.tick() == "lancee"
    settle(service)
    assert pipeline.calls == 1
    assert daily.tick() == "faite"                                     # la nouvelle porte le jour d'aujourd'hui


def test_une_generation_composee_hier_ne_compte_pas_non_plus(tmp_path):
    clock = Clock(ts(7, 23, 0))
    daily, service, pipeline = build(tmp_path, clock, hour=0)
    assert daily.tick() == "lancee"
    settle(service)
    assert daily._generated_on(datetime(2026, 10, 7).date()) is True
    assert daily._generated_on(datetime(2026, 10, 8).date()) is False


def test_le_constat_des_matchs_du_jour_est_fait_des_minuit_sans_attendre_l_heure_de_generation(tmp_path):
    clock = Clock(ts(8, 0, 5))
    daily, service, pipeline = build(tmp_path, clock, hour=6, matchday=Matchday(upcoming=0))
    assert daily.tick() == "attente"
    verdict = service.no_match_today()
    assert verdict["reason"] == "none" and verdict["title"] == "Pas de championnat aujourd'hui"
    assert pipeline.calls == 0 and service.generations_today() == 0


def test_un_jour_sans_match_rien_ne_part_et_le_constat_n_est_pas_redemande_toutes_les_minutes(tmp_path):
    clock = Clock(ts(8, 6, 0))
    daily, service, pipeline = build(tmp_path, clock, hour=6, matchday=Matchday(upcoming=0))
    outcomes = []
    for _ in range(25):                                                # 25 minutes de vérifications
        outcomes.append(daily.tick())
        clock.now += 60
    assert set(outcomes) == {"sans_match"}
    assert pipeline.calls == 0 and service.generations_today() == 0     # jamais d'appel à l'IA
    assert service.matchday_source.calls == 1                           # un seul constat dans la demi-heure


def test_un_calendrier_qui_se_complete_dans_la_journee_fait_partir_la_generation(tmp_path):
    clock = Clock(ts(8, 6, 0))
    source = Matchday(upcoming=0)
    daily, service, pipeline = build(tmp_path, clock, hour=6, matchday=source)
    assert daily.tick() == "sans_match"
    source.upcoming = 6                                                # TheSportsDB a complété les matchs du jour
    clock.now += MATCHDAY_CHECK_EVERY + 1
    assert daily.tick() == "lancee"
    settle(service)
    assert pipeline.calls == 1 and service.no_match_today() is None


def test_un_jour_avec_des_matchs_le_constat_n_est_pas_refait(tmp_path):
    clock = Clock(ts(8, 0, 10))
    daily, service, pipeline = build(tmp_path, clock, hour=6, matchday=Matchday(upcoming=7))
    assert daily.tick() == "attente"
    for hour in (3, 5):
        clock.now = ts(8, hour, 30)
        assert daily.tick() == "attente"
    assert service.matchday_source.calls == 1 and service.no_match_today() is None


def test_une_reponse_incomplete_ne_fait_pas_dire_pas_de_championnat(tmp_path):
    """Un championnat n'a pas répondu : on ne sait pas, on ne conclut rien et on réessaie un peu plus tard."""
    clock = Clock(ts(8, 0, 10))
    source = Matchday(upcoming=0, complete=False)
    daily, service, pipeline = build(tmp_path, clock, hour=6, matchday=source)
    assert daily.tick() == "attente"
    assert service.no_match_today() is None and source.calls == 1
    clock.now += 60
    daily.tick()
    assert source.calls == 1                                           # pas de nouvel essai à chaque minute
    clock.now += MATCHDAY_RETRY_AFTER
    daily.tick()
    assert source.calls == 2 and service.no_match_today() is None


def test_un_championnat_muet_n_empeche_pas_de_constater_qu_il_y_a_des_matchs(tmp_path):
    """Des matchs aujourd'hui ailleurs suffisent : un championnat de plus ne ferait qu'en ajouter."""
    clock = Clock(ts(8, 0, 10))
    daily, service, _ = build(tmp_path, clock, hour=6, matchday=Matchday(upcoming=5, complete=False))
    daily.tick()
    assert service.no_match_today() is None
    assert service._matchday()["upcoming"] == 5


def test_la_panne_de_thesportsdb_ne_casse_pas_la_boucle(tmp_path, caplog):
    clock = Clock(ts(8, 7, 0))
    daily, service, pipeline = build(tmp_path, clock, hour=6, matchday=Matchday(error=RuntimeError("réseau coupé")))
    with caplog.at_level(logging.WARNING, logger="generation_service"):
        assert daily.tick() == "lancee"                                # le constat échoue, la génération suit son cours
    settle(service)
    assert pipeline.calls == 1 and "vérification impossible" in caplog.text


def test_le_constat_inattendu_qui_leve_ne_casse_pas_le_tick(tmp_path, monkeypatch, caplog):
    daily, service, pipeline = build(tmp_path, Clock(ts(8, 7, 0)), hour=6)

    def broken():
        raise ValueError("bug")

    monkeypatch.setattr(service, "refresh_matchday", broken)
    with caplog.at_level(logging.ERROR, logger="daily_generation"):
        assert daily.tick() == "lancee"
    settle(service)
    assert "constat des matchs du jour en échec" in caplog.text


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
