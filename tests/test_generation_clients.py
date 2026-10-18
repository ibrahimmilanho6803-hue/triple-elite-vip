"""Un travail et un résultat PAR CLIENT (generation_service.py) : les combinés d'un client ne sont jamais ceux d'un autre,
chacun a son travail en cours, sa pause après un échec, son clic rendu ; la génération automatique reste à part ; chaque
génération sait ce que le client (et les autres) ont déjà reçu aujourd'hui."""
import os
import threading
import time
from datetime import datetime, timezone

import pytest

import config
import generation_service
from generation_service import HOUSE, GenerationService
from helpers import FakeClock, join_jobs
from pipeline import GenerationError, NoMatchToday
from privacy import client_scope
from variety import Variety

ALICE, BOB, CAROL = "alice@exemple.com", "bob@exemple.com", "carol@exemple.com"
START = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)


def combo_of(*match_ids):
    legs = [{"match_id": str(m), "home_team": f"H{m}", "away_team": f"A{m}", "league": "Serie A", "type": "V1",
             "type_name": f"Victoire H{m}", "confidence": 70, "estimated_odds": 1.4, "kickoff": None,
             "odds_source": "estimee", "category": "RESULTAT", "probability": 70.0} for m in match_ids]
    return {"predictions": legs, "total_odds": 2.7, "avg_confidence": 70.0, "success_probability": 34.0,
            "leagues": ["Serie A"], "categories": ["RESULTAT"]}


class Pipeline:
    """Chaîne simulée : à chaque appel, un combiné de trois NOUVEAUX matchs (1-3, puis 4-6...), sauf `same=True` (toujours 1-3).
    Note la variété reçue. `ai_matches` : nombre de matchs payés à l'IA (un entier, ou une liste : une valeur par appel)."""

    def __init__(self, error=None, ai_matches=5, same=False):
        self.error, self.ai_matches, self.same = error, ai_matches, same
        self.calls, self.varieties = 0, []
        self.gate = threading.Event()
        self.gate.set()
        self._lock = threading.Lock()

    def __call__(self, progress=None, variety=None):
        with self._lock:
            n = self.calls
            self.calls += 1
            self.varieties.append(variety)
        if progress:
            progress("analyse", 40, "Analyse…")
        self.gate.wait(5)
        if self.error:
            raise self.error
        first = 1 if self.same else 3 * n + 1
        meta = {"matches_total": 9}
        paid = self.ai_matches[n] if isinstance(self.ai_matches, list) else self.ai_matches
        if paid is not None:
            meta["ai_matches"] = paid
        return {"combos": [combo_of(first, first + 1, first + 2)], "meta": meta}


class Refunds:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1


@pytest.fixture()
def clock():
    return FakeClock(START)


def make(tmp_path, pipeline, clock):
    return GenerationService(pipeline=pipeline, cache_dir=str(tmp_path / "cache"), results_dir=str(tmp_path / "results"),
                             clock=clock)


def wait(service):
    join_jobs(service, 5)


def ids_of(state):
    return {leg["match_id"] for combo in state["combos"] for leg in combo["predictions"]}


def wait_for_calls(pipeline, count):
    for _ in range(100):
        if pipeline.calls >= count:
            return
        time.sleep(0.02)
    raise AssertionError(f"{pipeline.calls} appel(s) seulement au lieu de {count}")


# --------------------------------------------------------------------------
# Qui est qui
# --------------------------------------------------------------------------

def test_la_portee_d_un_client_est_opaque_stable_et_propre_a_lui():
    scope = GenerationService.scope_of(ALICE)
    assert scope == GenerationService.scope_of("  Alice@Exemple.COM ") == client_scope(ALICE)
    assert len(scope) == 16 and set(scope) <= set("0123456789abcdef") and "alice" not in scope
    assert scope != GenerationService.scope_of(BOB)
    assert GenerationService.scope_of(None) is None


@pytest.mark.parametrize("empty", ["", "   "])
def test_un_client_sans_adresse_n_est_jamais_confondu_avec_la_generation_automatique(tmp_path, clock, empty):
    service = make(tmp_path, Pipeline(), clock)
    service.request_generation()                                   # la génération automatique a des combinés prêts
    wait(service)
    assert service.latest() is not None
    for call in (service.status, service.request_generation, GenerationService.scope_of):
        with pytest.raises(ValueError):
            call(empty)


# --------------------------------------------------------------------------
# Les combinés d'un client ne sont jamais ceux d'un autre
# --------------------------------------------------------------------------

def test_les_combines_d_un_client_ne_s_affichent_jamais_chez_un_autre(tmp_path, clock):
    pipeline = Pipeline()
    service = make(tmp_path, pipeline, clock)
    assert service.status(ALICE) == {"state": "idle"} and service.status(BOB) == {"state": "idle"}
    assert service.request_generation(ALICE)["started"] is True
    wait(service)
    mine = service.status(ALICE)
    assert mine["state"] == "done" and mine["cached"] is True and ids_of(mine) == {"1", "2", "3"}
    assert service.status(BOB) == {"state": "idle"}                  # rien d'autre que le bouton
    assert service.status() == {"state": "idle"} and service.latest() is None
    assert service.request_generation(BOB)["started"] is True        # Bob doit lancer SA génération
    wait(service)
    assert ids_of(service.status(BOB)) == {"4", "5", "6"} and ids_of(service.status(ALICE)) == {"1", "2", "3"}
    assert pipeline.calls == 2


def test_un_client_retrouve_ses_combines_tant_qu_ils_sont_frais_puis_plus_rien(tmp_path, clock):
    pipeline = Pipeline()
    service = make(tmp_path, pipeline, clock)
    service.request_generation(ALICE)
    wait(service)
    clock.advance(minutes=config.CACHE_MINUTES - 1)
    assert service.request_generation(ALICE)["state"] == "done" and pipeline.calls == 1
    clock.advance(minutes=2)
    assert service.status(ALICE) == {"state": "idle"}


def test_la_generation_automatique_reste_a_part(tmp_path, clock):
    service = make(tmp_path, Pipeline(), clock)
    service.request_generation(ALICE)
    wait(service)
    assert service.latest() is None                                  # un client ne fournit pas le combiné gratuit
    service.request_generation()
    wait(service)
    legs = [leg["match_id"] for combo in service.latest()["combos"] for leg in combo["predictions"]]
    assert sorted(legs) == ["4", "5", "6"]
    assert ids_of(service.status(ALICE)) == {"1", "2", "3"}          # la sienne n'a pas bougé
    assert service.status(BOB) == {"state": "idle"}                  # et Bob ne reçoit pas celle de la maison non plus


def test_les_fichiers_d_un_client_portent_son_identifiant_et_jamais_son_adresse(tmp_path, clock):
    service = make(tmp_path, Pipeline(), clock)
    scope = GenerationService.scope_of(ALICE)
    service.request_generation(ALICE)
    wait(service)
    cache = set(os.listdir(tmp_path / "cache"))
    assert {f"job_{scope}.json", f"last_generation_{scope}.json"} <= cache
    assert "job.json" not in cache and "last_generation.json" not in cache
    (history,) = os.listdir(tmp_path / "results")
    assert history.startswith("combo_") and history.endswith(f"_{scope}.json")
    service.request_generation()
    wait(service)
    assert {"job.json", "last_generation.json"} <= set(os.listdir(tmp_path / "cache"))
    assert sorted(len(name) for name in os.listdir(tmp_path / "results")) == [len("combo_20261010_080000.json"),
                                                                              len(history)]
    for folder in ("cache", "results"):
        for name in os.listdir(tmp_path / folder):
            assert "alice" not in name
            with open(tmp_path / folder / name, encoding="utf-8") as f:
                assert "alice" not in f.read()


# --------------------------------------------------------------------------
# Un travail par client
# --------------------------------------------------------------------------

def test_chaque_client_a_son_propre_travail_en_cours(tmp_path, clock):
    pipeline = Pipeline()
    pipeline.gate.clear()
    service = make(tmp_path, pipeline, clock)
    first = service.request_generation(ALICE)
    assert first["state"] == "running" and first["started"] is True
    again = service.request_generation(ALICE)                        # même client : il rejoint son travail
    assert again["state"] == "running" and "started" not in again
    other = service.request_generation(BOB)                          # autre client : son propre travail
    assert other["state"] == "running" and other["started"] is True
    wait_for_calls(pipeline, 2)
    assert service.status(CAROL) == {"state": "idle"}                # qui n'a rien demandé
    pipeline.gate.set()
    wait(service)
    assert pipeline.calls == 2
    assert service.status(ALICE)["state"] == "done" and service.status(BOB)["state"] == "done"


def test_des_clics_simultanes_de_deux_clients_lancent_exactement_deux_travaux(tmp_path, clock):
    pipeline = Pipeline()
    service = make(tmp_path, pipeline, clock)
    threads = [threading.Thread(target=service.request_generation, args=(client,)) for client in (ALICE, BOB) * 12]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wait(service)
    assert pipeline.calls == 2 and service.generations_today() == 2


def test_l_echec_d_un_client_ne_bloque_pas_les_autres(tmp_path, clock):
    pipeline = Pipeline(error=GenerationError("Aucun combiné assez fiable. Réessaie."))
    service = make(tmp_path, pipeline, clock)
    service.request_generation(ALICE)
    wait(service)
    assert service.status(ALICE) == {"state": "error", "error": "Aucun combiné assez fiable. Réessaie."}
    assert service.status(BOB) == {"state": "idle"}                  # la pause d'Alice n'est pas celle de Bob
    pipeline.error = None
    assert service.request_generation(BOB)["started"] is True
    wait(service)
    assert service.status(BOB)["state"] == "done" and service.status(ALICE)["state"] == "error"
    assert service.request_generation(ALICE)["state"] == "error"     # elle, elle attend la fin de sa pause
    clock.advance(seconds=generation_service.FAILURE_COOLDOWN_SECONDS + 1)
    assert service.request_generation(ALICE)["started"] is True
    wait(service)


@pytest.mark.parametrize("error", [GenerationError("L'IA est en panne."), RuntimeError("panne")])
def test_le_clic_rendu_est_celui_du_client_dont_l_analyse_echoue(tmp_path, clock, error):
    pipeline = Pipeline(error=error)
    service = make(tmp_path, pipeline, clock)
    alice, bob = Refunds(), Refunds()
    service.request_generation(ALICE, refund=alice)
    wait(service)
    assert alice.calls == 1 and bob.calls == 0
    pipeline.error = None
    service.request_generation(BOB, refund=bob)
    wait(service)
    assert alice.calls == 1 and bob.calls == 0                       # celle de Bob a abouti : rien à rendre


def test_un_jour_sans_match_le_clic_est_rendu_et_tous_les_clients_recoivent_le_meme_constat(tmp_path, clock):
    service = make(tmp_path, Pipeline(error=NoMatchToday(0, 0, "2026-10-10")), clock)
    refund = Refunds()
    service.request_generation(ALICE, refund=refund)
    wait(service)
    assert refund.calls == 1 and service.generations_today() == 0
    for client in (ALICE, BOB, None):
        state = service.status(client)
        assert state["state"] == "no_match" and state["reason"] == "none", client
    assert service.request_generation(BOB)["state"] == "no_match"


def test_une_analyse_bloquee_rend_le_clic_de_son_client_seulement(tmp_path, clock):
    pipeline = Pipeline()
    pipeline.gate.clear()
    service = make(tmp_path, pipeline, clock)
    alice, bob = Refunds(), Refunds()
    service.request_generation(ALICE, refund=alice)
    wait_for_calls(pipeline, 1)
    clock.advance(seconds=config.JOB_MAX_SECONDS - 10)
    service.request_generation(BOB, refund=bob)                      # lancée 10 s avant la limite d'Alice
    wait_for_calls(pipeline, 2)
    clock.advance(seconds=11)
    assert service.status(ALICE)["state"] == "error" and alice.calls == 1
    assert service.status(BOB)["state"] == "running" and bob.calls == 0
    pipeline.gate.set()
    wait(service)
    assert service.status(BOB)["state"] == "done" and bob.calls == 0 and alice.calls == 1


def test_le_travail_perdu_d_un_client_ne_touche_pas_celui_d_un_autre(tmp_path, clock):
    service = make(tmp_path, Pipeline(), clock)
    service._write(service._job_name(GenerationService.scope_of(ALICE)),
                   {"state": "running", "pid": 2 ** 22 + 12345, "started_ts": clock(), "percent": 30, "message": "Collecte…"})
    assert service.status(ALICE) == {"state": "error", "error": generation_service.MSG_GENERIC_ERROR}
    assert service.status(BOB) == {"state": "idle"} and service.status() == {"state": "idle"}


# --------------------------------------------------------------------------
# Plafond du jour
# --------------------------------------------------------------------------

def test_le_plafond_du_jour_ne_compte_que_les_analyses_qui_ont_paye_l_ia(tmp_path, clock):
    service = make(tmp_path, Pipeline(ai_matches=[5, 0, 7, None]), clock)
    for client, expected in ((ALICE, 1), (BOB, 1), (CAROL, 2), ("dave@exemple.com", 3)):
        service.request_generation(client)
        wait(service)
        assert service.generations_today() == expected, client      # 0 match payé : rien compté ; clé absente : comptée


def test_le_plafond_du_jour_est_commun_a_tous_les_clients(tmp_path, clock, monkeypatch):
    monkeypatch.setattr(config, "MAX_GENERATIONS_PAR_JOUR", 2)
    service = make(tmp_path, Pipeline(), clock)
    for client in (ALICE, BOB):
        service.request_generation(client)
        wait(service)
    assert service.request_generation(CAROL) == {"state": "error", "error": generation_service.MSG_DAILY_LIMIT_EMPTY}
    clock.advance(minutes=config.CACHE_MINUTES + 1)
    stale = service.request_generation(ALICE)
    assert stale["state"] == "done" and stale["stale"] is True and ids_of(stale) == {"1", "2", "3"}   # ses derniers, pas ceux de Bob


# --------------------------------------------------------------------------
# Variété : ce qui a déjà été donné aujourd'hui
# --------------------------------------------------------------------------

def test_chaque_generation_sait_ce_que_le_client_et_les_autres_ont_deja_recu(tmp_path, clock):
    pipeline = Pipeline()
    service = make(tmp_path, pipeline, clock)
    service.request_generation(ALICE)
    wait(service)
    assert pipeline.varieties[0] == Variety()                        # personne n'a encore rien reçu
    clock.advance(minutes=config.CACHE_MINUTES + 1)
    service.request_generation(ALICE)                                # sa deuxième génération du jour
    wait(service)
    assert pipeline.varieties[1] == Variety(seen=frozenset({"1", "2", "3"}))
    service.request_generation(BOB)
    wait(service)
    assert pipeline.varieties[2] == Variety(served={str(n): 1 for n in range(1, 7)})     # tout ce qu'Alice a reçu
    clock.advance(minutes=config.CACHE_MINUTES + 1)
    service.request_generation(ALICE)
    wait(service)
    assert pipeline.varieties[3] == Variety(seen=frozenset(str(n) for n in range(1, 7)),
                                            served={"7": 1, "8": 1, "9": 1})              # ce que Bob a reçu, pas ses propres matchs


def test_la_generation_automatique_compte_comme_un_client_pour_la_variete(tmp_path, clock):
    pipeline = Pipeline()
    service = make(tmp_path, pipeline, clock)
    service.request_generation()
    wait(service)
    assert service._served()["given"] == {HOUSE: ["1", "2", "3"]}
    service.request_generation(ALICE)
    wait(service)
    assert pipeline.varieties[1] == Variety(served={"1": 1, "2": 1, "3": 1})


def test_un_match_redonne_au_meme_client_n_est_pas_recompte(tmp_path, clock):
    service = make(tmp_path, Pipeline(same=True), clock)
    scope = GenerationService.scope_of(ALICE)
    for _ in range(3):
        service.request_generation(ALICE)
        wait(service)
        clock.advance(minutes=config.CACHE_MINUTES + 1)
    served = service._served()
    assert served["given"] == {scope: ["1", "2", "3"]} and served["counts"] == {"1": 1, "2": 1, "3": 1}


def test_la_variete_repart_de_zero_le_lendemain(tmp_path, clock):
    pipeline = Pipeline()
    service = make(tmp_path, pipeline, clock)
    service.request_generation(ALICE)
    wait(service)
    clock.advance(days=1)
    service.request_generation(ALICE)
    wait(service)
    assert pipeline.varieties[1] == Variety()
    assert service._served()["counts"] == {"4": 1, "5": 1, "6": 1}


def test_une_generation_sans_combine_ne_note_rien_comme_distribue(tmp_path, clock):
    service = make(tmp_path, Pipeline(error=GenerationError("Aucun combiné.")), clock)
    service.request_generation(ALICE)
    wait(service)
    assert service._served()["given"] == {} and service._read("served.json") is None


# --------------------------------------------------------------------------
# Nettoyage
# --------------------------------------------------------------------------

def test_les_etats_anciens_des_clients_sont_supprimes_mais_pas_ceux_de_la_maison(tmp_path, clock):
    service = make(tmp_path, Pipeline(), clock)
    cache = tmp_path / "cache"
    cache.mkdir()
    old = time.time() - generation_service.PRUNE_AFTER - 3600
    names = {"job_aaaaaaaaaaaaaaaa.json": True, "last_generation_aaaaaaaaaaaaaaaa.json": True,         # anciens, d'un client
             "job_bbbbbbbbbbbbbbbb.json": False,                                                         # récent
             "job.json": False, "last_generation.json": False, "served.json": False}                    # de la maison : jamais
    for name, stale in names.items():
        path = cache / name
        path.write_text("{}", encoding="utf-8")
        if stale or name in ("job.json", "last_generation.json", "served.json"):
            os.utime(path, (old, old))
    service.request_generation(ALICE)
    wait(service)
    left = set(os.listdir(cache))
    assert "job_aaaaaaaaaaaaaaaa.json" not in left and "last_generation_aaaaaaaaaaaaaaaa.json" not in left
    assert {"job_bbbbbbbbbbbbbbbb.json", "served.json"} <= left
    assert {"job.json", "last_generation.json"} <= left


def test_le_nettoyage_n_a_lieu_qu_une_fois_par_periode(tmp_path, clock):
    service = make(tmp_path, Pipeline(), clock)
    cache = tmp_path / "cache"
    cache.mkdir()
    old = time.time() - generation_service.PRUNE_AFTER - 3600
    service.request_generation(ALICE)
    wait(service)
    stale = cache / "job_cccccccccccccccc.json"
    stale.write_text("{}", encoding="utf-8")
    os.utime(stale, (old, old))
    service.request_generation(BOB)                                  # moins d'une heure plus tard : pas de nouveau nettoyage
    wait(service)
    assert stale.exists()
    clock.advance(seconds=generation_service.PRUNE_EVERY + 1)
    service.request_generation(CAROL)
    wait(service)
    assert not stale.exists()
