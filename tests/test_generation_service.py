import json
import logging
import os
import threading
import time

import pytest

import config
import generation_service
from generation_service import GenerationService
from helpers import join_jobs
from pipeline import GenerationError, NoMatchToday


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
        self.varieties = []                  # la variété transmise à chaque appel
        self.gate = threading.Event()
        self.gate.set()

    def __call__(self, progress=None, variety=None):
        self.calls += 1
        self.varieties.append(variety)
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
    join_jobs(service, 5)


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


def test_travail_perdu_apres_redemarrage(dirs, caplog):
    clock = Clock()
    service = make(dirs, Pipeline(), clock)
    os.makedirs(dirs[0])
    # Travail « en cours » lancé par un processus disparu.
    with open(os.path.join(dirs[0], "job.json"), "w") as f:
        json.dump({"state": "running", "pid": 2 ** 22 + 12345, "started_ts": clock.now, "percent": 30,
                   "message": "Collecte des derniers résultats…"}, f)
    with caplog.at_level(logging.ERROR):
        state = service.status()
    assert state["state"] == "error" and state["error"] == generation_service.MSG_GENERIC_ERROR
    assert "génération perdue (le processus qui la menait a disparu)" in caplog.text      # dit dans les journaux, avec l'étape
    assert "Collecte des derniers résultats…" in caplog.text and "30 %" in caplog.text
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


def test_des_ecritures_simultanees_du_meme_fichier_ne_se_marchent_pas_dessus(dirs, caplog):
    """Plusieurs fils (navigateurs qui interrogent, analyse, boucle quotidienne) écrivent parfois le même fichier d'état."""
    service = make(dirs, Pipeline())

    def hammer(n):
        for i in range(80):
            service._write("job.json", {"state": "running", "n": n, "i": i})

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
    with caplog.at_level(logging.ERROR):
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert "impossible" not in caplog.text
    assert service._read("job.json")["state"] == "running"
    assert not [name for name in os.listdir(dirs[0]) if name.endswith(".tmp")]


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


# --------------------------------------------------------------------------
# Les matchs du jour : « Pas de championnat aujourd'hui »
# --------------------------------------------------------------------------

NO_MATCH_TITLE = "Pas de championnat aujourd'hui"


class Refunds:
    """Le « clic rendu » d'un client : compte les appels et note l'état du travail au moment où il est rendu."""

    def __init__(self, service):
        self.service, self.calls, self.states = service, 0, []

    def __call__(self):
        self.calls += 1
        self.states.append((self.service._read("job.json") or {}).get("state"))


def test_la_reponse_porte_started_seulement_pour_le_clic_qui_lance_l_analyse(dirs):
    pipeline = Pipeline()
    pipeline.gate.clear()
    service = make(dirs, pipeline)
    first = service.request_generation()
    assert first["state"] == "running" and first["started"] is True
    joined = service.request_generation()
    assert joined["state"] == "running" and "started" not in joined
    pipeline.gate.set()
    wait(service)
    assert "started" not in service.request_generation()                     # combinés frais : rien de lancé


def test_le_clic_n_est_pas_rendu_quand_l_analyse_aboutit(dirs):
    service = make(dirs, Pipeline())
    refunds = Refunds(service)
    service.request_generation(refund=refunds)
    wait(service)
    assert service.status()["state"] == "done" and refunds.calls == 0


@pytest.mark.parametrize("error", [GenerationError("L'IA est en panne."), RuntimeError("bug")])
def test_le_clic_est_rendu_quand_l_analyse_n_aboutit_a_rien(dirs, error):
    service = make(dirs, Pipeline(error=error))
    refunds = Refunds(service)
    service.request_generation(refund=refunds)
    wait(service)
    assert refunds.calls == 1
    assert refunds.states == ["running"]          # rendu AVANT que l'état final ne soit visible du navigateur
    assert service.status()["state"] == "error"
    assert service.generations_today() == 1       # l'analyse payante a bien eu lieu : elle reste dans le plafond du jour


def test_un_jour_sans_match_le_clic_est_rendu_et_le_plafond_du_jour_aussi(dirs):
    pipeline = Pipeline(error=NoMatchToday(0, 0, "2026-10-08"))
    service = make(dirs, pipeline)
    refunds = Refunds(service)
    service.request_generation(refund=refunds)
    wait(service)
    assert refunds.calls == 1 and refunds.states == ["running"]
    assert service.generations_today() == 0       # arrêtée avant l'IA : rien de payé, rien de compté
    state = service.status()
    assert state["state"] == "no_match" and state["reason"] == "none" and state["title"] == NO_MATCH_TITLE
    assert "Reviens demain" in state["message"]


def test_un_jour_sans_match_personne_ne_relance_rien(dirs):
    pipeline = Pipeline(error=NoMatchToday(0, 0, "2026-10-08"))
    service = make(dirs, pipeline)
    service.request_generation()
    wait(service)
    for _ in range(5):                            # cinq clics de plus, de n'importe quels clients
        answer = service.request_generation()
        assert answer["state"] == "no_match" and "started" not in answer
    assert pipeline.calls == 1 and service.generations_today() == 0


def test_le_constat_est_celui_de_la_journee_et_repart_le_lendemain(dirs):
    clock = Clock()
    service = make(dirs, Pipeline(error=NoMatchToday(0, 0, "x")), clock)
    service.request_generation()
    wait(service)
    assert service.no_match_today()["reason"] == "none"
    clock.now += 3 * 3600
    assert service.status()["state"] == "no_match"            # toujours le même jour
    clock.now += 24 * 3600
    assert service.no_match_today() is None and service.status() == {"state": "idle"}


def test_des_matchs_vus_plus_tot_font_dire_qu_ils_ont_commence_et_non_qu_il_n_y_en_a_pas(dirs):
    """TheSportsDB ne liste plus les matchs commencés : un total à 0 en soirée ne doit pas effacer ceux de l'après-midi."""
    service = make(dirs, Pipeline(error=NoMatchToday(6, 0, "x")))
    service.request_generation()
    wait(service)
    assert service.no_match_today()["reason"] == "started"
    service._note_matchday(0, 0)                                # une vérification plus tardive ne voit plus rien
    verdict = service.no_match_today()
    assert verdict["reason"] == "started" and verdict["title"] == "Les matchs d'aujourd'hui ont déjà commencé"


def test_trop_peu_de_matchs_a_venir_a_son_propre_message(dirs):
    service = make(dirs, Pipeline(error=NoMatchToday(5, 2, "x")))
    service.request_generation()
    wait(service)
    state = service.status()
    assert state["state"] == "no_match" and state["reason"] == "few" and state["title"] == "Trop peu de matchs aujourd'hui"


def test_une_generation_reussie_note_qu_il_y_a_de_quoi_composer_des_combines(dirs):
    service = make(dirs, Pipeline())
    service.request_generation()
    wait(service)
    assert service.no_match_today() is None
    assert service._matchday()["upcoming"] == 15 and service._matchday()["reason"] is None


def test_une_generation_qui_aboutit_corrige_un_constat_negatif_arrive_pendant_qu_elle_tournait(dirs):
    pipeline = Pipeline()
    pipeline.gate.clear()
    service = make(dirs, pipeline)
    service.request_generation()
    service._note_matchday(0, 0)                                # la boucle quotidienne constate « rien » pendant ce temps
    assert service.no_match_today()["reason"] == "none"
    pipeline.gate.set()
    wait(service)
    assert service.no_match_today() is None and service.status()["state"] == "done"


def test_des_combines_frais_priment_sur_un_constat_negatif(dirs):
    service = make(dirs, Pipeline())
    service.request_generation()
    wait(service)
    service._note_matchday(9, 1)                                # plus que 1 match à venir : pas de NOUVEAU combiné
    state = service.status()
    assert state["state"] == "done" and len(state["combos"]) == 1


def test_le_constat_illisible_ou_inconnu_est_ignore(dirs):
    service = make(dirs, Pipeline())
    assert service.no_match_today() is None
    service._write("matchday.json", {"day": service.today(), "reason": "n'importe quoi"})
    assert service.no_match_today() is None                     # raison inconnue : pas de message inventé
    service._write("matchday.json", {"day": service.today(), "reason": None})
    assert service.no_match_today() is None
    service._write("matchday.json", {"reason": "none"})        # sans jour : celui d'un autre jour
    assert service.no_match_today() is None


def test_un_refund_qui_leve_ne_casse_rien(dirs, caplog):
    service = make(dirs, Pipeline(error=GenerationError("panne")))

    def broken():
        raise RuntimeError("base injoignable")

    with caplog.at_level(logging.ERROR):
        service.request_generation(refund=broken)
        wait(service)
    assert service.status()["state"] == "error" and "génération du client non rendue" in caplog.text


def test_chaque_etape_de_l_analyse_est_consignee(dirs, caplog):
    service = make(dirs, Pipeline())
    with caplog.at_level(logging.INFO):
        service.request_generation()
        wait(service)
    assert "génération : Analyse… (40 %)" in caplog.text


# --------------------------------------------------------------------------
# Une analyse qui ne finit jamais (appel réseau resté sans réponse) : déclarée perdue, clic rendu UNE fois, blocage consigné
# --------------------------------------------------------------------------

def stuck(dirs, clock, error=None):
    """Une analyse démarrée par un client, puis restée bloquée à l'étape « Analyse… » (le temps du test)."""
    pipeline = Pipeline(error=error)
    pipeline.gate.clear()
    service = make(dirs, pipeline, clock)
    refunds = Refunds(service)
    assert service.request_generation(refund=refunds)["started"] is True
    time.sleep(0.1)                                          # le temps qu'elle atteigne son étape
    assert service.status()["state"] == "running"
    return service, pipeline, refunds


def test_une_analyse_bloquee_est_declaree_perdue_le_clic_est_rendu_et_l_endroit_du_blocage_consigne(dirs, caplog):
    clock = Clock()
    service, pipeline, refunds = stuck(dirs, clock)
    clock.now += config.JOB_MAX_SECONDS - 1
    assert service.status()["state"] == "running" and refunds.calls == 0              # pas encore trop long
    clock.now += 2
    with caplog.at_level(logging.ERROR):
        state = service.status()
    assert state == {"state": "error", "error": generation_service.MSG_GENERIC_ERROR}
    assert refunds.calls == 1 and refunds.states == ["running"]          # rendu AVANT que l'erreur ne soit visible du navigateur
    assert "génération perdue (elle dure trop longtemps)" in caplog.text
    assert "Analyse…" in caplog.text and "40 %" in caplog.text                         # l'étape où elle en était
    assert "Son fil en est là" in caplog.text and "self.gate.wait(5)" in caplog.text  # et ce que faisait son fil
    pipeline.gate.set()
    wait(service)


def test_le_clic_d_une_analyse_perdue_n_est_rendu_qu_une_fois(dirs):
    clock = Clock()
    service, pipeline, refunds = stuck(dirs, clock, error=RuntimeError("enfin"))
    clock.now += config.JOB_MAX_SECONDS + 1
    assert [service.status()["state"] for _ in range(3)] == ["error"] * 3
    assert refunds.calls == 1
    pipeline.gate.set()                                      # l'analyse finit par échouer à son tour : rien de plus à rendre
    wait(service)
    assert refunds.calls == 1


def test_des_navigateurs_qui_interrogent_en_meme_temps_ne_font_declarer_la_perte_qu_une_fois(dirs, caplog):
    clock = Clock()
    service, pipeline, refunds = stuck(dirs, clock)
    clock.now += config.JOB_MAX_SECONDS + 1
    answers = []
    threads = [threading.Thread(target=lambda: answers.append(service.status()["state"])) for _ in range(10)]
    with caplog.at_level(logging.ERROR):
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert answers == ["error"] * 10
    assert refunds.calls == 1 and caplog.text.count("génération perdue") == 1
    pipeline.gate.set()
    wait(service)


def test_une_analyse_perdue_qui_se_termine_enfin_ne_touche_ni_au_clic_ni_a_l_etat_de_la_suivante(dirs):
    clock = Clock()
    service, pipeline, first = stuck(dirs, clock, error=RuntimeError("tard"))
    first_thread = service._slot(None).thread
    clock.now += config.JOB_MAX_SECONDS + 1
    assert service.status()["state"] == "error" and first.calls == 1
    clock.now += generation_service.FAILURE_COOLDOWN_SECONDS + 1
    next_pipeline = Pipeline()
    next_pipeline.gate.clear()
    service.pipeline = next_pipeline
    second = Refunds(service)
    assert service.request_generation(refund=second)["started"] is True      # un autre client relance
    pipeline.gate.set()                                                      # la première analyse échoue enfin...
    first_thread.join(5)
    assert not first_thread.is_alive()
    assert first.calls == 1 and second.calls == 0                            # ... sans rendre le clic de l'autre client
    assert service.status()["state"] == "running"                            # ... ni écraser l'état de son analyse
    next_pipeline.gate.set()
    wait(service)
    assert service.status()["state"] == "done" and second.calls == 0


# --------------------------------------------------------------------------
# Le constat gratuit des matchs du jour (refresh_matchday)
# --------------------------------------------------------------------------

class Matchday:
    def __init__(self, upcoming=6, total=None, complete=True, error=None):
        self.upcoming, self.total, self.complete, self.error, self.calls = upcoming, total, complete, error, 0

    def __call__(self):
        self.calls += 1
        if self.error:
            raise self.error
        return {"day": "x", "matches": [{"id": n} for n in range(self.upcoming)],
                "total": self.upcoming if self.total is None else self.total, "complete": self.complete}


def make_checking(dirs, source, clock=None):
    return GenerationService(pipeline=Pipeline(), cache_dir=dirs[0], results_dir=dirs[1], clock=clock or Clock(),
                             matchday_source=source)


def test_le_constat_enregistre_un_jour_sans_match_sans_aucune_generation(dirs):
    source = Matchday(upcoming=0)
    service = make_checking(dirs, source)
    assert service.refresh_matchday() is True
    assert service.no_match_today()["title"] == NO_MATCH_TITLE
    assert service.status()["state"] == "no_match"
    assert service.generations_today() == 0 and service.pipeline.calls == 0         # aucun appel payant, aucun travail
    assert service.request_generation()["state"] == "no_match" and service.pipeline.calls == 0


def test_le_constat_d_un_jour_de_match_est_garde_sans_etre_refait(dirs):
    source = Matchday(upcoming=7, total=7)
    service = make_checking(dirs, source)
    assert service.refresh_matchday() is True
    assert service.no_match_today() is None and service._matchday()["upcoming"] == 7
    for _ in range(5):
        assert service.refresh_matchday() is False
    assert source.calls == 1


def test_un_constat_negatif_est_refait_toutes_les_demi_heures(dirs):
    clock, source = Clock(), Matchday(upcoming=0)
    service = make_checking(dirs, source, clock)
    service.refresh_matchday()
    clock.now += generation_service.MATCHDAY_CHECK_EVERY - 1
    assert service.refresh_matchday() is False and source.calls == 1
    clock.now += 2
    source.upcoming = 4                                         # le calendrier s'est complété
    assert service.refresh_matchday() is True and source.calls == 2
    assert service.no_match_today() is None


def test_une_reponse_incomplete_ou_en_panne_ne_conclut_rien_et_reessaie_plus_tard(dirs):
    clock = Clock()
    for source in (Matchday(upcoming=0, complete=False), Matchday(error=OSError("réseau"))):
        service = make_checking((dirs[0] + str(id(source)), dirs[1]), source, clock)
        assert service.refresh_matchday() is False
        assert service.no_match_today() is None and service._read("matchday.json") is None
        assert service.refresh_matchday() is False and source.calls == 1        # pas de nouvel essai aussitôt
        clock.now += generation_service.MATCHDAY_RETRY_AFTER
        service.refresh_matchday()
        assert source.calls == 2


def test_une_reponse_incomplete_mais_avec_assez_de_matchs_suffit(dirs):
    service = make_checking(dirs, Matchday(upcoming=5, complete=False))
    assert service.refresh_matchday() is True and service.no_match_today() is None


def test_sans_source_injectee_le_service_utilise_le_collecteur_reel_en_dernier_recours(dirs, monkeypatch):
    """(L'installation de tests interdit le réseau : la source réelle y est remplacée par un refus.)"""
    service = GenerationService(pipeline=Pipeline(), cache_dir=dirs[0], results_dir=dirs[1], clock=Clock())
    assert service.matchday_source == service._real_matchday
    assert service.refresh_matchday() is False and service.no_match_today() is None


def test_le_vrai_collecteur_est_appele_sans_argument(dirs, monkeypatch):
    import data_collector
    monkeypatch.undo()                                          # rend sa vraie source au service, le temps de ce test
    calls = []

    class Collector:
        def get_matchday(self):
            calls.append(1)
            return {"day": "x", "matches": [], "total": 0, "complete": True}

    monkeypatch.setattr(data_collector, "DataCollector", Collector)
    assert GenerationService._real_matchday() == {"day": "x", "matches": [], "total": 0, "complete": True}
    assert calls == [1]
