"""Chaîne de génération (pipeline.py) : les combinés ne portent que sur les matchs du JOUR, et un jour sans match la chaîne
s'arrête avant toute collecte et tout appel payant à l'IA."""
import logging

import pytest

import pipeline
from pipeline import (MIN_MATCHES, NO_MATCH_TEXTS, GenerationError, NoMatchToday, no_match_reason, run_pipeline)

DAY = "2026-10-10"


def matchday(upcoming=6, total=None, complete=True):
    matches = [{"id": str(n), "home_team": f"H{n}", "away_team": f"A{n}", "league": "Premier League" if n % 2 else "La Liga",
                "kickoff": f"{DAY}T{14 + n % 6:02d}:00:00+00:00", "date": f"{DAY} 14:00:00"} for n in range(upcoming)]
    return {"day": DAY, "matches": matches, "total": upcoming if total is None else total, "complete": complete}


class Collector:
    def __init__(self, day):
        self.day, self.collected = day, 0

    def get_matchday(self):
        return self.day

    def collect_all_data(self):
        self.collected += 1


class Analyzer:
    def __init__(self, answers=True):
        self.answers = answers

    def analyze_multiple_matches(self, matches, progress=None):
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

    def get_real_odds(self, home, away, league):
        return None

    def get_predictions_from_analysis(self, match, analysis, real_odds):
        return [{"match_id": match["id"], "odds_source": "bookmakers" if match["id"] == "0" else "estimee"}]

    def build_combos(self, predictions):
        self.built_from = predictions
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
