"""Chaîne de génération (pipeline.py) : les combinés ne portent que sur les matchs du JOUR, et un jour sans match la chaîne
s'arrête avant toute collecte et tout appel payant à l'IA."""
import logging
import threading
from datetime import datetime, timezone

import pytest

import config
import pipeline
from helpers import FakeClock
from pipeline import (MIN_MATCHES, NO_MATCH_TEXTS, GenerationError, NoMatchToday, no_match_reason, run_pipeline)
from shared_data import SharedData
from variety import Variety

DAY = "2026-10-10"
MORNING = datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc)


def matchday(upcoming=6, total=None, complete=True):
    matches = [{"id": str(n), "home_team": f"H{n}", "away_team": f"A{n}", "league": "Premier League" if n % 2 else "La Liga",
                "kickoff": f"{DAY}T{14 + n % 6:02d}:00:00+00:00", "date": f"{DAY} 14:00:00"} for n in range(upcoming)]
    return {"day": DAY, "matches": matches, "total": upcoming if total is None else total, "complete": complete}


class Collector:
    def __init__(self, day):
        self.day, self.collected, self.matchday_calls = day, 0, 0

    def get_matchday(self):
        self.matchday_calls += 1
        return self.day

    def collect_all_data(self):
        self.collected += 1


class Analyzer:
    def __init__(self, answers=True):
        self.answers = answers
        self.asked = []                       # les id des matchs envoyés à l'IA, un appel après l'autre

    def analyze_multiple_matches(self, matches, progress=None):
        self.asked.append([m["id"] for m in matches])
        if progress:
            progress(len(matches), len(matches))
        return [{"id": m["id"]} for m in matches] if self.answers else []

    def match_analyses(self, matches, items):
        return {m["id"]: {"id": m["id"]} for m in matches}

    def build_analysis_from_ia(self, home, away, ia):
        return {"home": home, "away": away}

    def analyze_match(self, home, away):
        return {"home": home, "away": away}


class Generator:
    """Générateur de combinés simulé : un pronostic par match, et `combos` en réponse (ou aucun)."""

    def __init__(self, combos=None, answers=True):
        self.analyzer = Analyzer(answers)
        self.combos = combos if combos is not None else []
        self.closed = False
        self.built_from = None
        self.variety = None

    def get_real_odds(self, home, away, league):
        return None

    def get_predictions_from_analysis(self, match, analysis, real_odds):
        return [{"match_id": match["id"], "odds_source": "bookmakers" if match["id"] == "0" else "estimee"}]

    def build_combos(self, predictions, variety=None):
        self.built_from = predictions
        self.variety = variety
        return self.combos

    def close(self):
        self.closed = True


class Untouchable:
    """Ce que la chaîne ne doit pas toucher un jour sans match : tout accès est un défaut (donc un appel payant)."""

    def __getattr__(self, name):
        raise AssertionError(f"la chaîne ne devait pas utiliser le générateur ({name}) un jour sans match")


def combo(*match_ids):
    return {"predictions": [{"match_id": m, "odds_source": "bookmakers" if m == "0" else "estimee"} for m in match_ids],
            "total_odds": 2.7}


# --------------------------------------------------------------------------
# Pourquoi aucun combiné n'est possible
# --------------------------------------------------------------------------

def test_les_raisons_d_un_jour_sans_combine():
    assert MIN_MATCHES == 3
    assert no_match_reason(0, 0) == "none"                    # rien du tout aujourd'hui
    assert no_match_reason(5, 0) == "started"                 # des matchs, mais tous commencés
    assert no_match_reason(5, 1) == "few" and no_match_reason(5, 2) == "few"
    assert no_match_reason(5, 3) is None and no_match_reason(0, 9) is None


def test_le_message_d_un_jour_sans_championnat_est_celui_demande():
    title, text = NO_MATCH_TEXTS["none"]
    assert title == "Pas de championnat aujourd'hui"
    assert "Reviens demain" in text
    for reason, (title, text) in NO_MATCH_TEXTS.items():
        assert title and "demain" in text, reason


# --------------------------------------------------------------------------
# Un jour sans match : arrêt immédiat, rien de payant
# --------------------------------------------------------------------------

@pytest.mark.parametrize("day, reason", [(matchday(0, 0), "none"), (matchday(0, 4), "started"), (matchday(2, 5), "few")])
def test_un_jour_sans_assez_de_matchs_la_chaine_s_arrete_avant_la_collecte_et_l_ia(day, reason):
    collector, steps = Collector(day), []
    with pytest.raises(NoMatchToday) as raised:
        run_pipeline(progress=lambda *step: steps.append(step), collector=collector, generator=Untouchable())
    error = raised.value
    assert error.reason == reason and error.total == day["total"] and error.upcoming == len(day["matches"])
    assert error.day == DAY and error.level == logging.INFO          # pas une panne : rien à signaler en rouge
    assert error.user_message == NO_MATCH_TEXTS[reason][0] and isinstance(error, GenerationError)
    assert collector.collected == 0                                  # ni collecte des résultats...
    assert [step[0] for step in steps] == ["matchs"]                 # ...ni analyse : seule la recherche des matchs a eu lieu


def test_le_generateur_n_est_meme_pas_cree_un_jour_sans_match(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("ComboGenerator() construit un jour sans match")

    monkeypatch.setattr(pipeline, "ComboGenerator", forbidden)
    with pytest.raises(NoMatchToday):
        run_pipeline(collector=Collector(matchday(0, 0)))


def test_un_championnat_muet_est_une_panne_et_non_un_jour_sans_match():
    """Si un championnat n'a pas répondu, « aucun match » ne prouve rien : message de panne, à réessayer."""
    with pytest.raises(GenerationError) as raised:
        run_pipeline(collector=Collector(matchday(0, 0, complete=False)), generator=Untouchable())
    assert not isinstance(raised.value, NoMatchToday)
    assert "momentanément indisponibles" in raised.value.user_message and "Réessaie" in raised.value.user_message
    assert raised.value.level == logging.WARNING


# --------------------------------------------------------------------------
# Un jour de championnat : la chaîne complète
# --------------------------------------------------------------------------

def test_un_jour_de_championnat_la_chaine_donne_les_combines_du_jour():
    collector, steps = Collector(matchday(6)), []
    generator = Generator([combo("0", "1", "2"), combo("3", "4", "5")])
    result = run_pipeline(progress=lambda *step: steps.append(step), collector=collector, generator=generator)
    assert len(result["combos"]) == 2
    assert [step[0] for step in steps][:3] == ["matchs", "collecte", "analyse"]           # les matchs d'abord
    assert steps[0] == ("matchs", 5, "Recherche des matchs du jour…") and steps[1][1] == 12
    assert [step[0] for step in steps][-2:] == ["pronostics", "composition"]
    assert collector.collected == 1 and generator.closed is True
    assert len(generator.built_from) == 6                                                  # un pronostic par match du jour
    meta = result["meta"]
    assert meta["day"] == DAY and meta["day_total"] == 6 and meta["matches_total"] == 6
    assert meta["matches_analyzed"] == 6 and meta["leagues"] == ["La Liga", "Premier League"]
    assert meta["total_legs"] == 6 and meta["real_odds_legs"] == 1 and meta["duration_seconds"] >= 0


def test_le_total_du_jour_compte_aussi_les_matchs_deja_commences():
    day = matchday(4, total=9)                                       # 9 matchs aujourd'hui, 5 ont commencé, 4 restent
    result = run_pipeline(collector=Collector(day), generator=Generator([combo("0", "1", "2")]))
    assert result["meta"]["day_total"] == 9 and result["meta"]["matches_total"] == 4


def test_trois_matchs_exactement_suffisent_pour_un_combine():
    result = run_pipeline(collector=Collector(matchday(MIN_MATCHES)), generator=Generator([combo("0", "1", "2")]))
    assert len(result["combos"]) == 1


def test_le_generateur_est_toujours_ferme(monkeypatch):
    generator = Generator([])
    with pytest.raises(GenerationError):
        run_pipeline(collector=Collector(matchday(6)), generator=generator)
    assert generator.closed is True


# --------------------------------------------------------------------------
# Les échecs « normaux », en français
# --------------------------------------------------------------------------

def test_aucun_combine_assez_fiable_avec_trois_matchs_seulement():
    with pytest.raises(GenerationError) as raised:
        run_pipeline(collector=Collector(matchday(3)), generator=Generator([]))
    assert raised.value.user_message == "Trop peu de matchs aujourd'hui pour composer un combiné à 2,50+ fiable. Réessaie demain."
    assert not isinstance(raised.value, NoMatchToday)


def test_aucun_combine_assez_fiable_avec_plus_de_matchs():
    with pytest.raises(GenerationError) as raised:
        run_pipeline(collector=Collector(matchday(8)), generator=Generator([]))
    assert raised.value.user_message == "Aucun combiné assez fiable avec les matchs d'aujourd'hui. Réessaie demain."


def test_l_ia_indisponible_est_une_erreur_a_signaler():
    with pytest.raises(GenerationError) as raised:
        run_pipeline(collector=Collector(matchday(6)), generator=Generator([], answers=False))
    assert "L'analyse IA est momentanément indisponible" in raised.value.user_message
    assert raised.value.level == logging.ERROR


# --------------------------------------------------------------------------
# Quels matchs sont analysés : au plus MATCHS_PAR_GENERATION, de préférence ceux que le client n'a pas encore reçus
# --------------------------------------------------------------------------

def test_un_jour_charge_la_chaine_n_en_garde_que_dix_huit():
    generator = Generator([combo("0", "1", "2")])
    result = run_pipeline(collector=Collector(matchday(40)), generator=generator)
    meta = result["meta"]
    assert config.MATCHS_PAR_GENERATION == 18
    assert meta["matches_available"] == 40 and meta["day_total"] == 40 and meta["matches_total"] == 18
    assert len(generator.analyzer.asked[0]) == 18 and len(generator.built_from) == 18


def test_la_chaine_prefere_les_matchs_que_le_client_n_a_pas_encore_recus():
    generator = Generator([combo("0", "1", "2")])
    seen = frozenset(str(n) for n in range(20))                       # 20 matchs déjà reçus, 20 nouveaux
    run_pipeline(collector=Collector(matchday(40)), generator=generator, variety=Variety(seen=seen))
    asked = generator.analyzer.asked[0]
    assert len(asked) == 18 and not (set(asked) & seen)


def test_un_jour_pauvre_les_matchs_deja_recus_sont_repris_plutot_que_d_echouer():
    generator = Generator([combo("0", "1", "2")])
    everything = Variety(seen=frozenset(str(n) for n in range(5)))
    result = run_pipeline(collector=Collector(matchday(5)), generator=generator, variety=everything)
    assert len(result["combos"]) == 1 and result["meta"]["matches_total"] == 5


def test_la_variete_est_transmise_a_la_composition_des_combines():
    variety = Variety(seen=frozenset({"1"}), served={"2": 2})
    generator = Generator([combo("0", "1", "2")])
    run_pipeline(collector=Collector(matchday(6)), generator=generator, variety=variety)
    assert generator.variety is variety
    other = Generator([combo("0", "1", "2")])
    run_pipeline(collector=Collector(matchday(6)), generator=other)
    assert other.variety == Variety()


# --------------------------------------------------------------------------
# Analyses de l'IA communes à toutes les générations
# --------------------------------------------------------------------------

@pytest.fixture()
def shared(tmp_path):
    return SharedData(str(tmp_path / "cache"), clock=FakeClock(MORNING))


def test_sans_donnees_communes_chaque_generation_paie_tous_ses_matchs():
    collector, generator = Collector(matchday(6)), Generator([combo("0", "1", "2")])
    first = run_pipeline(collector=collector, generator=generator)
    second = run_pipeline(collector=collector, generator=generator)
    assert first["meta"]["ai_matches"] == second["meta"]["ai_matches"] == 6
    assert len(generator.analyzer.asked) == 2 and collector.collected == 2 and collector.matchday_calls == 2


def test_une_deuxieme_generation_reutilise_les_analyses_de_l_ia(shared):
    collector, generator = Collector(matchday(6)), Generator([combo("0", "1", "2")])
    first = run_pipeline(collector=collector, generator=generator, shared=shared)
    assert first["meta"]["ai_matches"] == 6 and generator.analyzer.asked == [["0", "1", "2", "3", "4", "5"]]
    second = run_pipeline(collector=collector, generator=generator, shared=shared)
    assert second["meta"]["ai_matches"] == 0 and second["meta"]["matches_analyzed"] == 6
    assert len(generator.analyzer.asked) == 1                         # l'IA n'est pas rappelée...
    assert collector.collected == 1 and collector.matchday_calls == 1  # ...ni la collecte, ni la relecture des matchs du jour
    assert second["combos"] == first["combos"]


def test_seuls_les_matchs_pas_encore_analyses_sont_envoyes_a_l_ia(shared):
    collector, generator = Collector(matchday(30)), Generator([combo("0", "1", "2")])
    run_pipeline(collector=collector, generator=generator, shared=shared)
    first_pool = set(generator.analyzer.asked[0])
    assert len(first_pool) == 18
    second = run_pipeline(collector=collector, generator=generator, shared=shared, variety=Variety(seen=frozenset(first_pool)))
    asked = generator.analyzer.asked[1]
    assert len(asked) == 12 and not (set(asked) & first_pool)         # les 12 matchs encore jamais vus, pas les 6 déjà analysés
    assert second["meta"]["ai_matches"] == 12 and second["meta"]["matches_analyzed"] == 18


def test_l_estimation_gardee_pour_un_match_n_est_pas_utilisee_pour_un_autre_match(shared):
    collector, generator = Collector(matchday(6)), Generator([combo("0", "1", "2")])
    run_pipeline(collector=collector, generator=generator, shared=shared)
    day = matchday(6)
    for m in day["matches"]:
        m["home_team"] = "Équipe changée " + m["id"]                  # les mêmes id, mais d'autres équipes (id réutilisés)
    shared.clock.advance(seconds=config.MATCHDAY_CACHE_SECONDS)       # le programme du jour est relu ; les estimations sont encore valables
    run_pipeline(collector=Collector(day), generator=generator, shared=shared)
    assert len(generator.analyzer.asked) == 2 and len(generator.analyzer.asked[1]) == 6


def test_une_estimation_ancienne_est_refaite(tmp_path):
    early = SharedData(str(tmp_path / "cache"), clock=FakeClock(datetime(2026, 10, 10, 0, 5, tzinfo=timezone.utc)))
    collector, generator = Collector(matchday(6)), Generator([combo("0", "1", "2")])
    run_pipeline(collector=collector, generator=generator, shared=early)
    early.clock.advance(minutes=config.ANALYSIS_CACHE_MINUTES - 1)       # encore avant les premiers coups d'envoi (14 h)
    assert run_pipeline(collector=collector, generator=generator, shared=early)["meta"]["ai_matches"] == 0
    early.clock.advance(minutes=2)
    result = run_pipeline(collector=collector, generator=generator, shared=early)
    assert result["meta"]["ai_matches"] == 6 and len(generator.analyzer.asked) == 2


def test_une_panne_de_l_ia_n_empeche_pas_de_composer_avec_les_analyses_deja_gardees(shared):
    collector = Collector(matchday(30))
    good = Generator([combo("0", "1", "2")])
    run_pipeline(collector=collector, generator=good, shared=shared)
    first_pool = set(good.analyzer.asked[0])
    down = Generator([combo("0", "1", "2")], answers=False)
    result = run_pipeline(collector=collector, generator=down, shared=shared, variety=Variety(seen=frozenset(first_pool)))
    assert result["meta"]["ai_matches"] == 0 and result["meta"]["matches_analyzed"] == 6    # les 6 déjà gardés dans la liste
    assert len(result["combos"]) == 1 and shared.ai_cooling_down() is True


def test_ia_indisponible_sans_analyse_gardee_est_une_erreur_et_la_suivante_n_insiste_pas(shared):
    collector, down = Collector(matchday(6)), Generator([], answers=False)
    with pytest.raises(GenerationError) as raised:
        run_pipeline(collector=collector, generator=down, shared=shared)
    assert raised.value.user_message == pipeline.MSG_AI_DOWN and raised.value.level == logging.ERROR
    assert shared.ai_cooling_down() is True and len(down.analyzer.asked) == 1
    with pytest.raises(GenerationError) as again:                       # moins d'une minute plus tard : l'IA n'est pas rappelée
        run_pipeline(collector=collector, generator=down, shared=shared)
    assert again.value.user_message == pipeline.MSG_AI_DOWN and len(down.analyzer.asked) == 1
    shared.clock.advance(seconds=config.AI_RETRY_AFTER_SECONDS)
    with pytest.raises(GenerationError):
        run_pipeline(collector=collector, generator=down, shared=shared)
    assert len(down.analyzer.asked) == 2                                # la pause est finie : on réessaie
    shared.clock.advance(seconds=config.AI_RETRY_AFTER_SECONDS)
    good = Generator([combo("0", "1", "2")])
    assert run_pipeline(collector=collector, generator=good, shared=shared)["meta"]["ai_matches"] == 6
    assert shared.ai_cooling_down() is False                            # et une réponse de l'IA efface la panne


def test_trop_d_attente_derriere_une_autre_analyse_donne_un_message_clair(shared, monkeypatch):
    monkeypatch.setattr(config, "JOB_MAX_SECONDS", 0.5)
    generator = Generator([combo("0", "1", "2")])
    with shared.analysis_lock:                                          # une autre génération est en train de payer l'IA
        with pytest.raises(GenerationError) as raised:
            run_pipeline(collector=Collector(matchday(6)), generator=generator, shared=shared)
    assert raised.value.user_message == pipeline.MSG_BUSY and generator.closed is True
    assert generator.analyzer.asked == []


def test_deux_generations_en_meme_temps_ne_font_payer_l_ia_qu_une_seule_fois(shared):
    gate, entered = threading.Event(), threading.Event()

    class Slow(Analyzer):
        def analyze_multiple_matches(self, matches, progress=None):
            entered.set()
            gate.wait(5)
            return super().analyze_multiple_matches(matches, progress)

    analyzer, results = Slow(), []

    def make():
        generator = Generator([combo("0", "1", "2")])
        generator.analyzer = analyzer
        return generator

    def work():
        results.append(run_pipeline(collector=Collector(matchday(6)), generator=make(), shared=shared))

    first = threading.Thread(target=work)
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=work)
    second.start()
    gate.set()
    first.join(10)
    second.join(10)
    assert len(results) == 2 and len(analyzer.asked) == 1               # le second a attendu, puis trouvé l'analyse déjà faite
    assert sorted(r["meta"]["ai_matches"] for r in results) == [0, 6]
