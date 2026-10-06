import json
import random
from collections import Counter

import pytest

import combo_generator
import config
from combo_generator import ComboGenerator
from helpers import FakeAnalyzer, make_all_preds, make_analysis, make_match
from probabilities import reconcile


@pytest.fixture()
def gen():
    return ComboGenerator(analyzer=FakeAnalyzer())


# --------------------------------------------------------------------------
# Pronostics d'un match
# --------------------------------------------------------------------------

def test_seuil_de_confiance_et_plafond(gen):
    match = make_match(1)
    base = reconcile({"v1": 70, "v2": 10, "over_0_5": 95, "over_1_5": 80, "over_2_5": 55, "over_3_5": 25})
    legs = gen.get_predictions_from_analysis(match, {"base": base, "cap": 90})
    assert legs and all(leg["confidence"] >= config.MIN_CONFIDENCE for leg in legs)
    by_type = {leg["type"]: leg for leg in legs}
    assert by_type["V1"]["confidence"] == 70
    assert "TOTAL_2.5+" not in by_type                      # 55 % < seuil
    # Le plafond lié à la quantité de données s'applique à tout pari.
    capped = gen.get_predictions_from_analysis(match, {"base": base, "cap": 72})
    assert max(leg["confidence"] for leg in capped) <= 72
    assert gen.get_predictions_from_analysis(match, {"base": base, "cap": 60}) == []


def test_match_non_analyse_exclu(gen):
    assert gen.get_predictions_from_analysis(make_match(1), {"base": {}, "cap": 0, "fallback": True}) == []
    assert gen.get_predictions_from_analysis(make_match(1), {}) == []


def test_pari_compose_jamais_plus_confiant_que_ses_conditions(gen):
    """Le défaut corrigé : « victoire ET plus de N buts » ne peut pas valoir plus que la victoire."""
    for seed in range(30):
        rng = random.Random(seed)
        legs = gen.get_predictions_from_analysis(make_match(0), make_analysis(rng, cap=90, noise=8))
        by_type = {leg["type"]: leg["probability"] for leg in legs}
        for code, p in by_type.items():
            for prefix, simple in (("V1_", "V1"), ("V2_", "V2"), ("1X_", "1X"), ("2X_", "2X")):
                if code.startswith(prefix) and simple in by_type:
                    assert p <= by_type[simple] + 1e-6, (code, simple)


def test_champs_du_pronostic(gen):
    match = make_match(3, kickoff="2026-10-17T14:00:00+00:00")
    legs = gen.get_predictions_from_analysis(match, make_analysis(random.Random(1)))
    leg = legs[0]
    for field in ("match_id", "home_team", "away_team", "league", "kickoff", "type", "type_name",
                  "category", "confidence", "probability", "estimated_odds", "odds_source"):
        assert field in leg
    assert leg["kickoff"] == "2026-10-17T14:00:00+00:00"
    assert leg["odds_source"] == "estimee"
    assert leg["estimated_odds"] > 1.0


def test_cotes_estimees_suivent_la_confiance(gen):
    legs = gen.get_predictions_from_analysis(make_match(0), make_analysis(random.Random(4)))
    ordered = sorted(legs, key=lambda leg: leg["probability"])
    odds = [leg["estimated_odds"] for leg in ordered]
    assert odds == sorted(odds, reverse=True)               # plus c'est probable, plus la cote est basse


# --------------------------------------------------------------------------
# Vraies cotes (the-odds-api)
# --------------------------------------------------------------------------

def odds_event(home="Arsenal", away="Chelsea"):
    def book(h, d, a, over, under):
        return {"markets": [
            {"key": "h2h", "outcomes": [{"name": home, "price": h}, {"name": "Draw", "price": d},
                                          {"name": away, "price": a}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": over, "point": 2.5},
                                             {"name": "Under", "price": under, "point": 2.5}]}]}
    return {"home_team": home, "away_team": away,
            "bookmakers": [book(1.50, 4.0, 6.0, 1.80, 2.00), book(1.60, 4.2, 5.5, 1.90, 1.90),
                           book(1.70, 3.8, 5.0, 2.00, 1.80)]}


def test_cotes_reelles_mediane_des_bookmakers():
    odds = ComboGenerator._extract_odds(odds_event())
    assert odds["home"] == 1.6 and odds["draw"] == 4.0 and odds["away"] == 5.5
    assert odds["over_2.5"] == 1.9 and odds["under_2.5"] == 1.9


def test_cotes_reelles_ignore_les_valeurs_invalides():
    event = odds_event()
    event["bookmakers"].append({"markets": [{"key": "h2h", "outcomes": [
        {"name": "Arsenal", "price": 0}, {"name": "Draw", "price": None}, {"name": "Chelsea", "price": "x"}]}]})
    odds = ComboGenerator._extract_odds(event)
    assert odds["home"] == 1.6                                # la cote nulle n'a pas planté ni faussé


def test_prix_des_marches_cotes():
    odds = ComboGenerator._extract_odds(odds_event())
    price = ComboGenerator._market_price
    assert price("V1", odds) == 1.6 and price("V2", odds) == 5.5
    assert price("1X", odds) == round(1 / (1 / 1.6 + 1 / 4.0), 2)
    assert price("2X", odds) == round(1 / (1 / 5.5 + 1 / 4.0), 2)
    assert price("TOTAL_2.5+", odds) == 1.9 and price("TOTAL_2.5-", odds) == 1.9
    assert price("TOTAL_1.5+", odds) is None                # ligne non cotée
    assert price("BTTS_OUI", odds) is None and price("V1_ET_2.5+", odds) is None
    assert price("V1", None) is None


def test_double_chance_avec_cote_nulle():
    assert ComboGenerator._combine_odds(0, 3.0) is None
    assert ComboGenerator._combine_odds(1.5, None) is None
    assert ComboGenerator._combine_odds(1.5, 4.0) == round(1 / (1 / 1.5 + 1 / 4.0), 2) == 1.09


def test_same_team():
    same = ComboGenerator._same_team
    assert same("Manchester City", "Manchester City FC") and same("Arsenal", "Arsenal FC")
    assert same("PSG", "PSG") and not same("Inter", "Atalanta")
    assert not same("", "Arsenal") and not same("Rom", "Roma")
    # Limite connue : deux écritures très différentes ne se reconnaissent pas ; le pari
    # garde alors simplement une cote estimée.
    assert not same("Manchester City", "Man City")


def test_vraies_cotes_utilisees_quand_disponibles(gen, monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "cle-de-test")
    calls = []

    def fake_load(self, sport):
        calls.append(sport)
        return [odds_event("Domicile 0", "Extérieur 0")]

    monkeypatch.setattr(ComboGenerator, "_load_events", fake_load)
    match = make_match(0, league="Premier League")
    real = gen.get_real_odds(match["home_team"], match["away_team"], match["league"])
    assert real and real["home"] == 1.6
    gen.get_real_odds(match["home_team"], match["away_team"], match["league"])
    assert calls == ["soccer_epl"]                          # une seule requête par championnat
    rng = random.Random(5)
    analysis = {"base": reconcile({"v1": 72, "v2": 8, "1x": 92, "2x": 28, "over_2_5": 70, "under_2_5": 30,
                                   "over_0_5": 96, "over_1_5": 85, "over_3_5": 40}), "cap": 90}
    legs = {leg["type"]: leg for leg in gen.get_predictions_from_analysis(match, analysis, real)}
    assert legs["V1"]["estimated_odds"] == 1.6 and legs["V1"]["odds_source"] == "bookmakers"
    assert legs["TOTAL_2.5+"]["odds_source"] == "bookmakers"
    assert legs["TOTAL_1.5+"]["odds_source"] == "estimee"


def test_sans_cle_pas_de_requete(gen, monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setattr(ComboGenerator, "_load_events",
                        lambda self, sport: pytest.fail("aucune requête attendue sans clé"))
    assert gen.get_real_odds("A", "B", "Serie A") is None


def test_cache_disque_des_cotes(monkeypatch, tmp_path):
    monkeypatch.setenv("ODDS_API_KEY", "cle-de-test")
    monkeypatch.setattr(combo_generator, "ODDS_CACHE_DIR", str(tmp_path))
    hits = []

    class Response:
        status_code = 200

        def json(self):
            return [odds_event()]

    monkeypatch.setattr(combo_generator.requests, "get", lambda *a, **k: hits.append(k) or Response())
    gen = ComboGenerator(analyzer=FakeAnalyzer())
    assert len(gen._load_events("soccer_epl")) == 1
    assert len(ComboGenerator(analyzer=FakeAnalyzer())._load_events("soccer_epl")) == 1
    assert len(hits) == 1                                    # le 2e appel vient du cache disque
    assert hits[0]["params"]["markets"] == "h2h,totals"


def test_echec_api_cotes_retombe_sur_le_cache_perime(monkeypatch, tmp_path):
    monkeypatch.setenv("ODDS_API_KEY", "cle-de-test")
    monkeypatch.setattr(combo_generator, "ODDS_CACHE_DIR", str(tmp_path))
    (tmp_path / "odds_soccer_epl.json").write_text(
        json.dumps({"fetched_at": 1, "events": [odds_event()]}), encoding="utf-8")

    class Quota:
        status_code = 401

    monkeypatch.setattr(combo_generator.requests, "get", lambda *a, **k: Quota())
    events = ComboGenerator(analyzer=FakeAnalyzer())._load_events("soccer_epl")
    assert len(events) == 1
    monkeypatch.setattr(combo_generator.requests, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("réseau")))
    assert len(ComboGenerator(analyzer=FakeAnalyzer())._load_events("soccer_epl")) == 1


# --------------------------------------------------------------------------
# Sélection des combinés
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(12))
def test_combines_respectent_les_regles(gen, seed):
    preds = make_all_preds(gen, seed=seed, cap=90)
    combos = gen.build_combos(preds)
    assert 1 <= len(combos) <= config.MAX_COMBOS_RETOURNES
    seen_matches = set()
    for combo in combos:
        legs = combo["predictions"]
        assert len(legs) == 3
        ids = [leg["match_id"] for leg in legs]
        assert len(set(ids)) == 3                              # 3 matchs différents
        assert not (set(ids) & seen_matches)                   # aucun match déjà utilisé
        seen_matches.update(ids)
        assert combo["total_odds"] >= config.TARGET_ODDS
        product = 1.0
        for leg in legs:
            product *= leg["estimated_odds"]
        assert combo["total_odds"] == pytest.approx(product, abs=0.01)
        assert len({leg["category"] for leg in legs}) >= 2     # pas 3 paris du même genre
        assert all(leg["confidence"] >= config.MIN_CONFIDENCE for leg in legs)
        assert combo["avg_confidence"] == pytest.approx(sum(l["confidence"] for l in legs) / 3, abs=0.06)
        expected = legs[0]["probability"] * legs[1]["probability"] * legs[2]["probability"] / 10000
        assert combo["success_probability"] == pytest.approx(expected, abs=0.06)
        # Honnêteté : une cote à 2,50+ ne peut pas avoir plus de ~43 % de réussite estimée.
        assert combo["success_probability"] < 100 / config.TARGET_ODDS + 1
    probabilities = [c["success_probability"] for c in combos]
    assert probabilities == sorted(probabilities, reverse=True)


def test_combines_varies(gen):
    combos = gen.build_combos(make_all_preds(gen, seed=3))
    assert len(combos) == 3
    types = Counter(leg["type"] for c in combos for leg in c["predictions"])
    assert max(types.values()) <= 3                            # pas le même pari partout
    assert len({league for c in combos for league in c["leagues"]}) >= 3


def test_raccourci_garde_les_paris_les_plus_cotes(gen):
    legs = [{"estimated_odds": 1.1 + i / 100, "probability": 90 - i} for i in range(30)]
    short = ComboGenerator._shortlist(legs, 10)
    assert len(short) == 10
    assert min(leg["estimated_odds"] for leg in short) >= 1.1 + 20 / 100


def test_aucun_combine_si_pas_assez_de_matchs(gen):
    preds = make_all_preds(gen, seed=1, matches=2)
    assert gen.build_combos(preds) == []
    assert gen.build_combos([]) == []


def test_aucun_combine_si_cote_inatteignable(gen):
    preds = make_all_preds(gen, seed=1)
    assert gen.build_combos(preds, target_odds=50) == []


def test_un_seul_combine_possible_avec_trois_matchs(gen):
    preds = make_all_preds(gen, seed=2, matches=3)
    combos = gen.build_combos(preds, target_odds=1.5)
    assert len(combos) == 1
