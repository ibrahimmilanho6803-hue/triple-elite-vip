"""Données communes à toutes les générations (shared_data.py) : estimations de l'IA gardées par match, matchs du jour, collecte
des résultats, pause après une panne de l'IA. Rien de tout cela n'est propre à un client."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

import config
from helpers import FakeClock
from shared_data import ANALYSES_FILE, AnalysisCache, SharedData

MORNING = datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc)
TTL = config.ANALYSIS_CACHE_MINUTES * 60


def match(n, home=None, away=None):
    return {"id": str(n), "home_team": home or f"H{n}", "away_team": away or f"A{n}", "league": "La Liga",
            "kickoff": f"2026-10-10T{14 + n % 6:02d}:00:00+00:00"}


# --------------------------------------------------------------------------
# AnalysisCache : l'estimation de l'IA pour chaque match
# --------------------------------------------------------------------------

@pytest.fixture()
def cache(tmp_path):
    clock = FakeClock(MORNING)
    cache = AnalysisCache(str(tmp_path / "cache"), TTL, clock=clock)
    cache.clock_for_test = clock
    return cache


def test_une_estimation_gardee_est_rendue_pour_ce_match_seulement(cache):
    matches = [match(1), match(2), match(3)]
    cache.store(matches[:2], {"1": {"v1": 50}, "2": {"v1": 60}, "3": {"v1": 99}})
    assert cache.lookup(matches) == {"1": {"v1": 50}, "2": {"v1": 60}}               # le 3 n'était pas dans ce qui a été enregistré
    assert cache.lookup([match(4)]) == {}


def test_l_estimation_survit_au_redemarrage_car_elle_est_sur_le_disque(cache, tmp_path):
    cache.store([match(1)], {"1": {"v1": 50}})
    again = AnalysisCache(str(tmp_path / "cache"), TTL, clock=cache.clock_for_test)             # un autre objet, même dossier
    assert again.lookup([match(1)]) == {"1": {"v1": 50}}
    with open(tmp_path / "cache" / ANALYSES_FILE, encoding="utf-8") as f:
        assert json.load(f)["entries"]["1"]["home"] == "H1"


def test_une_estimation_trop_ancienne_n_est_plus_rendue(cache):
    cache.store([match(1)], {"1": {"v1": 50}})
    cache.clock_for_test.advance(seconds=TTL - 1)
    assert cache.lookup([match(1)]) == {"1": {"v1": 50}}
    cache.clock_for_test.advance(seconds=1)
    assert cache.lookup([match(1)]) == {}


def test_une_estimation_datee_du_futur_est_refusee(cache):
    cache.store([match(1)], {"1": {"v1": 50}})
    cache.clock_for_test.advance(seconds=-30)                                          # petit décalage d'horloge : toléré
    assert cache.lookup([match(1)]) == {"1": {"v1": 50}}
    cache.clock_for_test.advance(seconds=-3600)                                        # une heure d'avance : refusé
    assert cache.lookup([match(1)]) == {}


def test_l_estimation_d_un_autre_match_qui_a_le_meme_id_est_refusee(cache):
    cache.store([match(1, "Arsenal", "Chelsea")], {"1": {"v1": 50}})
    assert cache.lookup([match(1, "Arsenal", "Chelsea")]) == {"1": {"v1": 50}}
    assert cache.lookup([match(1, "Arsenal", "Brighton")]) == {}                       # l'id est le même, pas les équipes
    assert cache.lookup([match(1, "  arsenal ", "CHELSEA")]) == {"1": {"v1": 50}}     # casse et espaces sans importance


def test_seules_les_reponses_valides_sont_gardees(cache):
    cache.store([match(1), match(2), match(3)], {"1": {"v1": 50}, "2": None, "3": "texte"})
    assert cache.lookup([match(1), match(2), match(3)]) == {"1": {"v1": 50}}


def test_un_nouvel_enregistrement_oublie_les_entrees_expirees_mais_garde_les_autres(cache, tmp_path):
    cache.store([match(1)], {"1": {"v1": 50}})
    cache.clock_for_test.advance(seconds=TTL - 10)
    cache.store([match(2)], {"2": {"v1": 60}})
    cache.clock_for_test.advance(seconds=20)                                           # 1 a expiré, 2 est encore bon
    cache.store([match(3)], {"3": {"v1": 70}})
    with open(tmp_path / "cache" / ANALYSES_FILE, encoding="utf-8") as f:
        assert sorted(json.load(f)["entries"]) == ["2", "3"]


def test_un_fichier_illisible_ne_bloque_rien(cache, tmp_path):
    os.makedirs(tmp_path / "cache", exist_ok=True)
    (tmp_path / "cache" / ANALYSES_FILE).write_text("{pas du json", encoding="utf-8")
    assert cache.lookup([match(1)]) == {}
    cache.store([match(1)], {"1": {"v1": 50}})                                         # et l'écriture suivante remet tout d'équerre
    assert cache.lookup([match(1)]) == {"1": {"v1": 50}}


def test_un_disque_inutilisable_garde_les_estimations_en_memoire(tmp_path):
    clock = FakeClock(MORNING)
    not_a_folder = tmp_path / "fichier"
    not_a_folder.write_text("je ne suis pas un dossier", encoding="utf-8")
    cache = AnalysisCache(str(not_a_folder), TTL, clock=clock)
    cache.store([match(1)], {"1": {"v1": 50}})
    assert cache.lookup([match(1)]) == {"1": {"v1": 50}}


# --------------------------------------------------------------------------
# SharedData.matchday : les matchs du jour, relus de temps en temps
# --------------------------------------------------------------------------

class Collector:
    def __init__(self, matches, total=None, complete=True, day="2026-10-10", summary=None):
        self.matches, self.total, self.complete, self.day = matches, total, complete, day
        self.matchday_calls = self.collect_calls = 0
        self.summary = {"La Liga": 4} if summary is None else summary

    def get_matchday(self):
        self.matchday_calls += 1
        return {"day": self.day, "matches": [dict(m) for m in self.matches],
                "total": len(self.matches) if self.total is None else self.total, "complete": self.complete}

    def collect_all_data(self):
        self.collect_calls += 1
        return self.summary


@pytest.fixture()
def shared(tmp_path):
    clock = FakeClock(MORNING)
    data = SharedData(str(tmp_path / "cache"), clock=clock)
    data.test_clock = clock
    return data


def test_les_matchs_du_jour_ne_sont_relus_qu_apres_le_delai(shared):
    collector = Collector([match(n) for n in range(5)])
    first = shared.matchday(collector)
    shared.test_clock.advance(seconds=config.MATCHDAY_CACHE_SECONDS - 1)
    second = shared.matchday(collector)
    assert collector.matchday_calls == 1 and first == second
    shared.test_clock.advance(seconds=1)
    shared.matchday(collector)
    assert collector.matchday_calls == 2


def test_les_matchs_commences_sont_retires_mais_le_total_du_jour_ne_change_pas(shared):
    collector = Collector([match(n) for n in range(5)])                                # coups d'envoi : 14 h, 15 h, ..., 18 h
    assert len(shared.matchday(collector)["matches"]) == 5
    shared.test_clock.advance(hours=9, minutes=30)                                     # 15 h 30 UTC : deux ont commencé
    data = shared.matchday(collector)
    assert [m["id"] for m in data["matches"]] == ["2", "3", "4"] and data["total"] == 5
    assert collector.matchday_calls == 2                                               # le délai de relecture était passé aussi


def test_la_liste_rendue_est_une_copie(shared):
    collector = Collector([match(n) for n in range(4)])
    shared.matchday(collector)["matches"][0]["home_team"] = "modifié"
    assert shared.matchday(collector)["matches"][0]["home_team"] == "H0"


def test_une_reponse_incomplete_n_est_jamais_gardee(shared):
    collector = Collector([match(n) for n in range(4)], complete=False)
    assert shared.matchday(collector)["complete"] is False
    shared.matchday(collector)
    assert collector.matchday_calls == 2                                               # la génération suivante réessaie aussitôt
    collector.complete = True
    shared.matchday(collector)
    shared.matchday(collector)
    assert collector.matchday_calls == 3


def test_un_nouveau_jour_force_la_relecture_meme_avant_la_fin_du_delai(shared):
    shared.test_clock.now = datetime(2026, 10, 10, 23, 59, 30, tzinfo=timezone.utc).timestamp()
    collector = Collector([match(n) for n in range(4)])
    shared.matchday(collector)
    shared.test_clock.advance(seconds=60)                                              # minuit est passé, le délai ne l'est pas
    collector.day = "2026-10-11"
    assert shared.matchday(collector)["day"] == "2026-10-11" and collector.matchday_calls == 2


# --------------------------------------------------------------------------
# SharedData.collect : les derniers résultats
# --------------------------------------------------------------------------

def test_les_derniers_resultats_ne_sont_collectes_qu_une_fois_par_periode(shared):
    collector = Collector([])
    assert shared.collect(collector) is True and collector.collect_calls == 1
    shared.test_clock.advance(minutes=config.COLLECT_CACHE_MINUTES - 1)
    assert shared.collect(collector) is False and collector.collect_calls == 1
    shared.test_clock.advance(minutes=1)
    assert shared.collect(collector) is True and collector.collect_calls == 2


def test_une_collecte_qui_n_a_rien_lu_n_est_pas_comptee_comme_faite(shared):
    collector = Collector([], summary={"La Liga": 0, "Serie A": 0})                    # TheSportsDB n'a répondu nulle part
    assert shared.collect(collector) is True
    assert shared.collect(collector) is True and collector.collect_calls == 2          # on réessaie à la génération suivante
    collector.summary = {"La Liga": 3, "Serie A": 0}
    shared.collect(collector)
    assert shared.collect(collector) is False and collector.collect_calls == 3


def test_une_collecte_sans_bilan_lisible_est_comptee_comme_faite(shared):
    collector = Collector([], summary=False)
    collector.summary = None
    collector.collect_all_data = lambda: None
    assert shared.collect(collector) is True and shared.collect(collector) is False


# --------------------------------------------------------------------------
# Pannes de l'IA
# --------------------------------------------------------------------------

def test_apres_une_panne_de_l_ia_une_courte_pause_puis_on_reessaie(shared):
    assert shared.ai_cooling_down() is False
    shared.note_ai_failure()
    assert shared.ai_cooling_down() is True
    shared.test_clock.advance(seconds=config.AI_RETRY_AFTER_SECONDS - 1)
    assert shared.ai_cooling_down() is True
    shared.test_clock.advance(seconds=1)
    assert shared.ai_cooling_down() is False


def test_une_reponse_de_l_ia_met_fin_a_la_pause(shared):
    shared.note_ai_failure()
    shared.note_ai_success()
    assert shared.ai_cooling_down() is False
