import json
import logging
import random
from collections import Counter

import pytest

import combo_generator
import config
import markets
from combo_generator import ComboGenerator
from helpers import FakeAnalyzer, make_all_preds, make_analysis, make_match
from probabilities import implied_odds, market_probability, reconcile


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
# Cote minimale d'un pari
# --------------------------------------------------------------------------

# Un match où l'IA est très sûre de plusieurs paris : sans plancher, ils sortent à 1,01–1,15.
SAFE_ANALYSIS = {"base": reconcile({"v1": 72, "v2": 8, "1x": 92, "2x": 28, "over_2_5": 70, "under_2_5": 30,
                                    "over_0_5": 96, "over_1_5": 85, "over_3_5": 40}), "cap": 90}


def test_cote_minimale_par_defaut(gen):
    assert gen.min_leg_odds == config.MIN_LEG_ODDS == 1.25


def test_aucun_pari_sous_la_cote_minimale(gen):
    """Le défaut corrigé : un pari presque sûr à 1,03 servait de « bouche-trou » dans un combiné (il fait monter la chance
    annoncée sans rien ajouter à la cote), et la cote n'était pas jouable chez le bookmaker."""
    match = make_match(0)
    gen.min_leg_odds = 1.0
    sans_plancher = gen.get_predictions_from_analysis(match, SAFE_ANALYSIS)
    assert min(leg["estimated_odds"] for leg in sans_plancher) < 1.15            # la preuve que le plancher agit
    gen.min_leg_odds = config.MIN_LEG_ODDS
    legs = gen.get_predictions_from_analysis(match, SAFE_ANALYSIS)
    assert legs and min(leg["estimated_odds"] for leg in legs) >= config.MIN_LEG_ODDS
    gardes = {leg["type"] for leg in legs}
    assert gardes < {leg["type"] for leg in sans_plancher}                         # les paris restants sont une partie des autres
    assert "V1" in gardes and "1X" not in gardes and "TOTAL_0.5+" not in gardes    # 72 % reste, 92 % et 96 % sont écartés


def test_le_plafond_de_confiance_ne_gonfle_pas_les_cotes(gen):
    """Avec peu de matchs connus la confiance affichée est plafonnée (72 %), mais le pari n'en devient pas plus payant : la cote
    estimée reste celle de la chance réellement estimée. Un pari à 92 % ne devient pas « un pari à 72 % payé 1,29 » (que les
    bookmakers paieraient 1,05) : il est écarté, et la cote totale affichée reste une cote que l'on peut parier."""
    match = make_match(0)
    analysis = {**SAFE_ANALYSIS, "cap": 72}
    estimate = market_probability("1X", SAFE_ANALYSIS["base"])
    assert estimate > 90
    gen.min_leg_odds = 1.0
    legs = {leg["type"]: leg for leg in gen.get_predictions_from_analysis(match, analysis)}
    assert legs["1X"]["confidence"] == 72 and legs["1X"]["probability"] == 72      # l'affichage reste plafonné
    assert legs["1X"]["estimated_odds"] == implied_odds(estimate, config.ODDS_MARGIN) < 1.1
    gen.min_leg_odds = config.MIN_LEG_ODDS
    gardes = {leg["type"]: leg for leg in gen.get_predictions_from_analysis(match, analysis)}
    assert "1X" not in gardes and "TOTAL_0.5+" not in gardes
    assert gardes["V1"]["estimated_odds"] == implied_odds(72, config.ODDS_MARGIN)       # un pari à 72 % : confiance et cote cohérentes


def with_reference(odds):
    """Les cotes d'un match telles que get_real_odds les rend quand le bookmaker de référence (1xBet) cote les mêmes paris, au même prix."""
    return {**odds, "reference": dict(odds)}


def test_la_cote_minimale_vaut_aussi_pour_les_vraies_cotes(gen):
    """Un favori « gagne » à 1,04 chez les bookmakers n'est pas proposé ; les marchés mieux payés du même match le restent."""
    match = make_match(0)
    real = with_reference({"home": 1.04, "draw": 12.0, "away": 30.0, "over_2.5": 1.9, "under_2.5": 1.9})
    legs = {leg["type"]: leg for leg in gen.get_predictions_from_analysis(match, SAFE_ANALYSIS, real)}
    assert "V1" not in legs and legs["TOTAL_2.5+"]["estimated_odds"] == 1.9 and legs["TOTAL_2.5+"]["odds_source"] == "bookmakers"
    gen.min_leg_odds = 1.0
    assert {leg["type"]: leg for leg in gen.get_predictions_from_analysis(match, SAFE_ANALYSIS, real)}["V1"]["estimated_odds"] == 1.04


def test_la_cote_minimale_est_atteinte_exactement(gen):
    """Un pari qui paie pile la cote minimale reste proposé."""
    match = make_match(0)
    real = {"home": 1.25, "draw": 7.0, "away": 15.0}
    gen.min_leg_odds = 1.25
    assert any(leg["type"] == "V1" and leg["estimated_odds"] == 1.25 for leg in gen.get_predictions_from_analysis(match, SAFE_ANALYSIS, real))
    real["home"] = 1.24
    assert not any(leg["type"] == "V1" for leg in gen.get_predictions_from_analysis(match, SAFE_ANALYSIS, real))


def test_aucun_combine_ne_contient_un_pari_sous_la_cote_minimale(gen):
    """De vraies cotes très basses (grands favoris) sur la moitié des matchs : avant le plancher, beaucoup de combinés en
    contenaient un. Désormais aucun, et les trois combinés sont presque toujours là. (Ce cas extrême, où la moitié des matchs n'offre
    presque rien, laisse rarement deux combinés seulement depuis que « moins de 3,5 buts » n'est plus proposé.)"""
    rng = random.Random(11)
    real = {"home": 1.03, "draw": 14.0, "away": 40.0, "over_2.5": 1.12, "under_2.5": 5.0, "over_1.5": 1.02, "under_1.5": 12.0}
    sizes = []
    for round_number in range(8):
        preds = []
        for n in range(12):
            analysis = make_analysis(rng, cap=90, noise=5.0)
            preds += gen.get_predictions_from_analysis(make_match(n), analysis, dict(real) if n % 2 == 0 else None)
        combos = gen.build_combos(preds)
        assert len(combos) >= 2, round_number
        sizes.append(len(combos))
        for combo in combos:
            assert combo["total_odds"] >= config.TARGET_ODDS
            assert all(leg["estimated_odds"] >= config.MIN_LEG_ODDS for leg in combo["predictions"])
    assert sizes.count(3) >= 6, sizes


# --------------------------------------------------------------------------
# Vraies cotes (the-odds-api)
# --------------------------------------------------------------------------

def odds_book(key, home, away, h=None, d=None, a=None, over=None, under=None, line=2.5):
    """Un bookmaker de the-odds-api (sa clé, son résultat 1/N/2 et ses totaux de buts), avec seulement ce qu'il cote."""
    markets = []
    outcomes = [{"name": name, "price": price} for name, price in ((home, h), ("Draw", d), (away, a)) if price is not None]
    if outcomes:
        markets.append({"key": "h2h", "outcomes": outcomes})
    totals = [{"name": name, "price": price, "point": line} for name, price in (("Over", over), ("Under", under))
              if price is not None]
    if totals:
        markets.append({"key": "totals", "outcomes": totals})
    return {"key": key, "markets": markets}


def odds_event(home="Arsenal", away="Chelsea"):
    """Trois bookmakers. Médianes : 1,60 / 4,00 / 5,50, plus de 2,5 à 1,90, moins de 2,5 à 1,90. 1xBet (le deuxième) est à la
    médiane partout sauf pour le match nul, qu'il paie 4,20."""
    def book(key, h, d, a, over, under):
        return odds_book(key, home, away, h, d, a, over, under)
    return {"home_team": home, "away_team": away,
            "bookmakers": [book("pinnacle", 1.50, 4.0, 6.0, 1.80, 2.00), book("onexbet", 1.60, 4.2, 5.5, 1.90, 1.90),
                           book("bet365", 1.70, 3.8, 5.0, 2.00, 1.80)]}


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


def test_same_team_ignore_les_accents_et_la_ponctuation():
    """TheSportsDB et the-odds-api n'écrivent pas les noms pareil : le défaut corrigé laissait une cote « ≈ » à des paris
    pourtant cotés par les bookmakers (« İstanbul Başakşehir gagne ou match nul » en est un exemple réel)."""
    same = ComboGenerator._same_team
    assert same("İstanbul Başakşehir", "Istanbul Basaksehir") and same("İstanbul Başakşehir", "Istanbul Basaksehir FK")
    assert same("Paris Saint-Germain", "Paris Saint Germain") and same("St. Pauli", "FC St Pauli")
    assert same("Atlético Madrid", "Atletico Madrid") and same("Borussia Mönchengladbach", "Borussia Monchengladbach")
    assert same("Deportivo Alavés", "Alaves") and same("1. FC Köln", "FC Koln") and same("Málaga", "MALAGA")
    assert same("Fenerbahçe", "Fenerbahce") and same("Gençlerbirliği", "Genclerbirligi")      # le « ı » turc, sans point
    assert same("Brøndby", "Brondby") and same("Zürich", "Zurich") and same("Łódź", "Lodz")
    assert same("FC Nordsjælland", "FC Nordsjaelland") and same("SønderjyskE", "Sonderjyske")    # les clubs danois (Superliga)
    assert same("Ростов", "Ростов") and not same("Ростов", "Зенит")                          # autres alphabets : gardés tels quels
    # Rien de plus permissif pour autant : des équipes différentes restent différentes.
    assert not same("Real Madrid", "Real Sociedad") and not same("Sporting CP", "Sporting Braga")
    assert not same("Inter", "Atalanta") and not same("Rom", "Roma")
    assert not same(None, "Arsenal") and not same("Arsenal", None) and not same("-", "Arsenal") and not same("", "")


def test_nom_d_equipe_compare():
    fold = combo_generator._fold_name
    assert fold("İstanbul  Başakşehir") == "istanbul basaksehir" == fold("ISTANBUL-BASAKSEHIR")
    assert fold("Newell's Old Boys") == "newell s old boys"
    assert fold(None) == "" and fold("  ") == "" and fold(42) == "42"


def test_cote_reelle_d_un_match_dont_le_nom_a_des_accents(gen, monkeypatch):
    """Le match « İstanbul Başakşehir – Gaziantep » reçoit sa vraie cote même si the-odds-api l'écrit sans accents."""
    monkeypatch.setenv("ODDS_API_KEY", "cle-de-test")
    asked = []

    def fake_load(self, sport):
        asked.append(sport)
        return [odds_event("Galatasaray", "Fenerbahce"), odds_event("Istanbul Basaksehir", "Gaziantep FK")]

    monkeypatch.setattr(ComboGenerator, "_load_events", fake_load)
    real = gen.get_real_odds("İstanbul Başakşehir", "Gaziantep", "Süper Lig (Turquie)")
    assert asked == ["soccer_turkey_super_league"] and real and real["home"] == 1.6
    match = {**make_match(0, league="Süper Lig (Turquie)"), "home_team": "İstanbul Başakşehir", "away_team": "Gaziantep"}
    analysis = {"base": reconcile({"v1": 72, "v2": 8, "1x": 92, "2x": 28, "over_2_5": 70, "under_2_5": 30,
                                   "over_0_5": 96, "over_1_5": 85, "over_3_5": 40}), "cap": 90}
    gen.min_leg_odds = 1.0              # ce test porte sur la reconnaissance du match : « 1X » cote 1,14, sous la cote minimale
    legs = {leg["type"]: leg for leg in gen.get_predictions_from_analysis(match, analysis, real)}
    assert legs["1X"]["odds_source"] == "bookmakers" and legs["1X"]["estimated_odds"] == round(1 / (1 / 1.6 + 1 / 4.2), 2)


def test_match_introuvable_chez_the_odds_api_est_note_dans_les_journaux(gen, monkeypatch, caplog):
    monkeypatch.setenv("ODDS_API_KEY", "cle-de-test")
    monkeypatch.setattr(ComboGenerator, "_load_events", lambda self, sport: [odds_event("Arsenal", "Chelsea")])
    with caplog.at_level(logging.INFO, logger="combo_generator"):
        assert gen.get_real_odds("Lyon", "Nantes", "Ligue 1") is None
        assert gen.get_real_odds("Arsenal", "Chelsea", "Premier League")                    # trouvé : rien à signaler
    assert "Lyon – Nantes (Ligue 1) introuvable parmi les 1 match(s) cotés" in caplog.text
    assert caplog.text.count("introuvable") == 1 and "cle-de-test" not in caplog.text


def test_pas_de_journal_quand_l_api_n_a_rien_repondu(gen, monkeypatch, caplog):
    """Quota atteint ou panne : _load_events le signale déjà, et aucun match n'est « introuvable » pour autant."""
    monkeypatch.setenv("ODDS_API_KEY", "cle-de-test")
    monkeypatch.setattr(ComboGenerator, "_load_events", lambda self, sport: [])
    with caplog.at_level(logging.INFO, logger="combo_generator"):
        assert gen.get_real_odds("Lyon", "Nantes", "Ligue 1") is None
    assert "introuvable" not in caplog.text


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
    analysis = {"base": reconcile({"v1": 72, "v2": 8, "1x": 92, "2x": 28, "over_2_5": 70, "under_2_5": 30,
                                   "over_0_5": 96, "over_1_5": 85, "over_3_5": 40}), "cap": 90}
    gen.min_leg_odds = 1.0              # ce test porte sur l'origine des cotes, pas sur la cote minimale (testée plus bas)
    legs = {leg["type"]: leg for leg in gen.get_predictions_from_analysis(match, analysis, real)}
    assert legs["V1"]["estimated_odds"] == 1.6 and legs["V1"]["odds_source"] == "bookmakers"
    assert legs["TOTAL_2.5+"]["odds_source"] == "bookmakers"
    assert legs["TOTAL_1.5+"]["odds_source"] == "estimee"


# --------------------------------------------------------------------------
# Cotes du bookmaker de référence (1xBet)
# --------------------------------------------------------------------------

def test_le_bookmaker_de_reference_est_1xbet_par_defaut():
    assert config.ODDS_BOOKMAKER == "onexbet" and config.ODDS_BOOKMAKER_LABEL == "1xBet"


def test_cotes_d_un_seul_bookmaker():
    event = odds_event()
    onexbet = ComboGenerator._bookmaker_odds(event, "onexbet")
    assert onexbet == {"home": 1.6, "draw": 4.2, "away": 5.5, "over_2.5": 1.9, "under_2.5": 1.9}   # le nul : 4,2, pas la médiane (4,0)
    assert ComboGenerator._bookmaker_odds(event, "pinnacle")["home"] == 1.5
    assert ComboGenerator._bookmaker_odds(event, "unibet") == {}                    # ce bookmaker ne cote pas ce match
    assert ComboGenerator._bookmaker_odds(event, "") == {} and ComboGenerator._bookmaker_odds(event, None) == {}
    assert ComboGenerator._extract_odds(event)["draw"] == 4.0                      # la médiane, elle, reste celle de tous


def test_la_cle_du_bookmaker_se_lit_sans_tenir_compte_des_majuscules():
    event = {"home_team": "Arsenal", "away_team": "Chelsea",
             "bookmakers": [odds_book(" OneXBet ", "Arsenal", "Chelsea", 1.6, 4.2, 5.5, 1.9, 1.9)]}
    assert ComboGenerator._bookmaker_odds(event, "onexbet")["home"] == 1.6
    sans_cle = odds_book("", "Arsenal", "Chelsea", 1.6, 4.2, 5.5, 1.9, 1.9)
    del sans_cle["key"]
    assert ComboGenerator._bookmaker_odds({"home_team": "Arsenal", "away_team": "Chelsea", "bookmakers": [sans_cle]}, "onexbet") == {}


def test_le_resultat_d_un_bookmaker_n_est_garde_que_s_il_cote_les_trois_issues():
    """Une double chance « 1X » calculée avec le « 1 » de 1xBet et le « N » d'un autre bookmaker ne serait chez aucun des deux."""
    event = {"home_team": "Arsenal", "away_team": "Chelsea", "bookmakers": [
        odds_book("onexbet", "Arsenal", "Chelsea", h=1.6, a=5.5, over=1.9, under=1.9),          # pas de cote du match nul
        odds_book("pinnacle", "Arsenal", "Chelsea", 1.5, 4.0, 6.0, 1.8, 2.0)]}
    assert ComboGenerator._bookmaker_odds(event, "onexbet") == {"over_2.5": 1.9, "under_2.5": 1.9}   # les totaux restent
    assert ComboGenerator._bookmaker_odds(event, "pinnacle")["draw"] == 4.0
    assert ComboGenerator._extract_odds(event)["draw"] == 4.0                                   # la médiane garde, elle, le nul de Pinnacle


def test_les_cotes_invalides_d_un_bookmaker_sont_ignorees():
    event = {"home_team": "Arsenal", "away_team": "Chelsea", "bookmakers": [
        {"key": "onexbet", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Arsenal", "price": 0}, {"name": "Draw", "price": None}, {"name": "Chelsea", "price": "x"}]}]}]}
    assert ComboGenerator._bookmaker_odds(event, "onexbet") == {}


def test_get_real_odds_ajoute_les_cotes_du_bookmaker_de_reference(gen, monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "cle-de-test")
    monkeypatch.setattr(ComboGenerator, "_load_events", lambda self, sport: [odds_event("Domicile 0", "Extérieur 0")])
    real = gen.get_real_odds("Domicile 0", "Extérieur 0", "Premier League")
    assert real["home"] == 1.6 and real["draw"] == 4.0                       # la médiane de tous les bookmakers (inchangée)
    assert real["reference"] == {"home": 1.6, "draw": 4.2, "away": 5.5, "over_2.5": 1.9, "under_2.5": 1.9}      # et 1xBet


def test_bookmaker_de_reference_absent_du_match_est_note_dans_les_journaux(gen, monkeypatch, caplog):
    """1xBet ne cote pas tous les matchs : la médiane des autres sert alors, ses cotes portent « ≈ », et le journal le dit."""
    monkeypatch.setenv("ODDS_API_KEY", "cle-de-test")
    event = {"home_team": "Arsenal", "away_team": "Chelsea", "bookmakers": [
        odds_book("pinnacle", "Arsenal", "Chelsea", 1.5, 4.0, 6.0, 1.8, 2.0),
        odds_book("bet365", "Arsenal", "Chelsea", 1.7, 3.8, 5.0, 2.0, 1.8)]}
    monkeypatch.setattr(ComboGenerator, "_load_events", lambda self, sport: [event])
    with caplog.at_level(logging.INFO, logger="combo_generator"):
        real = gen.get_real_odds("Arsenal", "Chelsea", "Premier League")
    assert real and "reference" not in real and real["home"] == 1.6
    assert "cotes onexbet : Arsenal – Chelsea (Premier League) absent chez ce bookmaker" in caplog.text
    assert "cle-de-test" not in caplog.text
    legs = {leg["type"]: leg for leg in gen.get_predictions_from_analysis(make_match(0), SAFE_ANALYSIS, real)}
    assert legs["V1"]["odds_source"] == "mediane" and legs["V1"]["estimated_odds"] == 1.6


def test_origine_des_cotes_1xbet_puis_mediane_puis_estimation():
    priced = ComboGenerator._priced
    real = {"home": 1.6, "draw": 4.0, "away": 5.5, "over_2.5": 1.9, "under_2.5": 1.9,
            "reference": {"home": 1.55, "draw": 4.1, "away": 5.8, "over_2.5": 1.85}}      # 1xBet ne cote pas « moins de 2,5 »
    assert priced("V1", real) == (1.55, "bookmakers")                                       # sa cote, pas la médiane
    assert priced("1X", real) == (round(1 / (1 / 1.55 + 1 / 4.1), 2), "bookmakers")         # calculée avec ses deux cotes
    assert priced("TOTAL_2.5+", real) == (1.85, "bookmakers")
    assert priced("TOTAL_2.5-", real) == (1.9, "mediane")                                   # il ne la cote pas : médiane, avec « ≈ »
    assert priced("TOTAL_1.5+", real) == (None, None)                                       # personne ne la cote : estimation
    assert priced("BTTS_OUI", real) == (None, None)
    assert priced("V1", None) == (None, None) and priced("V1", {}) == (None, None)
    # Sans « reference » (1xBet ne cote pas du tout ce match) : tout vient de la médiane, donc tout porte « ≈ ».
    assert priced("V1", {"home": 1.6, "draw": 4.0, "away": 5.5}) == (1.6, "mediane")


def test_sans_bookmaker_de_reference_la_mediane_fait_foi(gen, monkeypatch, caplog):
    """ODDS_BOOKMAKER vide : le comportement d'avant la version 2.4.1, la médiane de tous les bookmakers, sans « ≈ »."""
    monkeypatch.setattr(config, "ODDS_BOOKMAKER", "")
    monkeypatch.setenv("ODDS_API_KEY", "cle-de-test")
    monkeypatch.setattr(ComboGenerator, "_load_events", lambda self, sport: [odds_event("Domicile 0", "Extérieur 0")])
    with caplog.at_level(logging.INFO, logger="combo_generator"):
        real = gen.get_real_odds("Domicile 0", "Extérieur 0", "Premier League")
    assert real and "reference" not in real and "absent chez ce bookmaker" not in caplog.text
    assert ComboGenerator._priced("V1", real) == (1.6, "bookmakers")
    gen.min_leg_odds = 1.0
    legs = {leg["type"]: leg for leg in gen.get_predictions_from_analysis(make_match(0, league="Premier League"), SAFE_ANALYSIS, real)}
    assert legs["V1"]["odds_source"] == "bookmakers" and legs["V1"]["estimated_odds"] == 1.6
    assert legs["1X"]["odds_source"] == "bookmakers" and legs["1X"]["estimated_odds"] == round(1 / (1 / 1.6 + 1 / 4.0), 2)   # le nul médian
    assert legs["TOTAL_1.5+"]["odds_source"] == "estimee"


def test_la_cote_de_1xbet_decide_de_la_cote_minimale(gen):
    """Le client joue chez 1xBet : un pari à 1,40 en médiane mais à 1,20 chez lui n'est pas jouable ; et l'inverse."""
    match = make_match(0)
    chez_lui_trop_bas = {"home": 1.40, "draw": 5.0, "away": 9.0, "reference": {"home": 1.20, "draw": 5.5, "away": 10.0}}
    assert "V1" not in {leg["type"] for leg in gen.get_predictions_from_analysis(match, SAFE_ANALYSIS, chez_lui_trop_bas)}
    chez_lui_assez = {"home": 1.20, "draw": 5.5, "away": 10.0, "reference": {"home": 1.40, "draw": 5.0, "away": 9.0}}
    legs = {leg["type"]: leg for leg in gen.get_predictions_from_analysis(match, SAFE_ANALYSIS, chez_lui_assez)}
    assert legs["V1"]["estimated_odds"] == 1.40 and legs["V1"]["odds_source"] == "bookmakers"


def test_un_combine_garde_l_origine_de_chaque_cote(gen):
    """Dans un même combiné, la cote 1xBet n'a pas de « ≈ », la médiane et l'estimation en ont un : l'origine suit le pari."""
    match = make_match(0)
    real = {"home": 1.6, "draw": 4.0, "away": 5.5, "over_2.5": 1.9, "under_2.5": 1.9,
            "reference": {"over_2.5": 1.85, "under_2.5": 1.95}}                             # 1xBet ne cote que les totaux
    gen.min_leg_odds = 1.0              # ce test porte sur l'origine des cotes : « plus de 1,5 » (85 %, estimé à 1,09) doit rester visible
    legs = {leg["type"]: leg for leg in gen.get_predictions_from_analysis(match, SAFE_ANALYSIS, real)}
    assert legs["TOTAL_2.5+"]["odds_source"] == "bookmakers" and legs["TOTAL_2.5+"]["estimated_odds"] == 1.85
    assert legs["V1"]["odds_source"] == "mediane" and legs["V1"]["estimated_odds"] == 1.6
    assert legs["TOTAL_1.5+"]["odds_source"] == "estimee"
    assert {leg["odds_source"] for leg in legs.values()} == {"bookmakers", "mediane", "estimee"}


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


def test_la_cle_des_cotes_n_apparait_pas_dans_les_journaux(monkeypatch, tmp_path, caplog):
    secret = "CLE-COTES-987654"
    monkeypatch.setenv("ODDS_API_KEY", secret)
    monkeypatch.setattr(combo_generator, "ODDS_CACHE_DIR", str(tmp_path))

    def failing(url, params=None, timeout=None):
        # `requests` reprend l'adresse complète, paramètres compris, dans le message de ses exceptions.
        raise combo_generator.requests.exceptions.ConnectionError(
            f"HTTPSConnectionPool(host='api.the-odds-api.com', port=443): Max retries exceeded with url: "
            f"/v4/sports/soccer_epl/odds?apiKey={secret}&regions=eu&markets=h2h%2Ctotals (Caused by Timeout)")

    monkeypatch.setattr(combo_generator.requests, "get", failing)
    with caplog.at_level(logging.WARNING):
        assert ComboGenerator(analyzer=FakeAnalyzer())._load_events("soccer_epl") == []
    assert "the-odds-api soccer_epl" in caplog.text and "apiKey=***" in caplog.text
    assert secret not in caplog.text


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


# --------------------------------------------------------------------------
# Pari retiré : « Moins de 3,5 buts »
# --------------------------------------------------------------------------

# Un match où « moins de 3,5 buts » est à 72 % : avant son retrait, il passait la confiance minimale (65 %) et la cote minimale
# (estimée à 1,29), comme sur presque tous les matchs.
UNDER_35_ANALYSIS = {"base": reconcile({"v1": 45, "v2": 25, "over_0_5": 93, "over_1_5": 74, "over_2_5": 48,
                                        "over_3_5": 28, "under_3_5": 72}), "cap": 90}


def test_moins_de_3_5_buts_n_est_plus_propose(gen, monkeypatch):
    """Retiré à la demande du propriétaire du site (il revenait dans la plupart des générations)."""
    match = make_match(0)
    assert market_probability("TOTAL_3.5-", UNDER_35_ANALYSIS["base"]) == 72
    types = {leg["type"] for leg in gen.get_predictions_from_analysis(match, UNDER_35_ANALYSIS)}
    assert types and "TOTAL_3.5-" not in types
    # Avec une vraie cote de 1xBet, ou une IA sûre à 99 % et sans cote minimale : toujours pas proposé.
    real = with_reference({"under_3.5": 1.55, "over_3.5": 2.3})
    assert "TOTAL_3.5-" not in {leg["type"] for leg in gen.get_predictions_from_analysis(match, UNDER_35_ANALYSIS, real)}
    sure = {"base": reconcile({"over_3_5": 1, "under_3_5": 99, "over_0_5": 99, "over_1_5": 90, "over_2_5": 40}), "cap": 90}
    gen.min_leg_odds = 1.0
    assert "TOTAL_3.5-" not in {leg["type"] for leg in gen.get_predictions_from_analysis(match, sure)}
    # La preuve que c'est bien son retrait du catalogue qui l'écarte, et non un seuil : remis au catalogue, il est proposé.
    gen.min_leg_odds = config.MIN_LEG_ODDS
    monkeypatch.setattr(markets, "PREDICTION_CODES", (*markets.PREDICTION_CODES, "TOTAL_3.5-"))
    legs = {leg["type"]: leg for leg in gen.get_predictions_from_analysis(match, UNDER_35_ANALYSIS)}
    assert legs["TOTAL_3.5-"]["confidence"] == 72 and legs["TOTAL_3.5-"]["estimated_odds"] == implied_odds(72, config.ODDS_MARGIN)


def test_les_autres_totaux_restent_proposes(gen):
    """Seul « moins de 3,5 » est retiré : les autres lignes de buts (et son contraire) le restent."""
    match = make_match(0)
    analysis = {"base": reconcile({"v1": 45, "v2": 25, "over_0_5": 93, "over_1_5": 70, "over_2_5": 30, "over_3_5": 28,
                                   "under_2_5": 70, "under_3_5": 72}), "cap": 90}
    types = {leg["type"] for leg in gen.get_predictions_from_analysis(match, analysis)}
    assert {"TOTAL_1.5+", "TOTAL_2.5-"} <= types and "TOTAL_3.5-" not in types


# --------------------------------------------------------------------------
# Variété des pronostics : un même genre de pari le moins possible dans une génération
# --------------------------------------------------------------------------

LEAGUES = ("Premier League", "La Liga", "Serie A")


def fake_leg(match, code, category, probability, odds):
    """Un pronostic tel que get_predictions_from_analysis le rend, avec seulement ce que la composition lit."""
    return {"match_id": str(match), "home_team": f"D{match}", "away_team": f"E{match}", "league": LEAGUES[match % 3],
            "kickoff": None, "type": code, "type_name": code, "category": category, "confidence": int(probability),
            "probability": float(probability), "estimated_odds": odds, "odds_source": "estimee"}


def shared_genre_day():
    """9 matchs ; sur chacun, une double chance (« 1X » ou « 2X » : le même genre de pari, pour l'une ou l'autre équipe), la plus
    probable, et un pari bien à lui, un peu moins probable. Sans variété, la composition reprend la double chance partout."""
    categories = ("TOTAL", "EQUIPE", "BTTS")
    preds = []
    for m in range(9):
        preds.append(fake_leg(m, "1X" if m % 2 == 0 else "2X", "RESULTAT", 74, 1.38))
        preds.append(fake_leg(m, f"PARI_{m}", categories[m % 3], 70, 1.42))
    return preds


def genres(combos):
    return [markets.genre_of(leg["type"]) for combo in combos for leg in combo["predictions"]]


def test_la_variete_donne_des_pronostics_de_genres_differents(gen, monkeypatch):
    assert config.GENRE_REPEAT_PENALTY > 0
    found = genres(gen.build_combos(shared_genre_day()))
    assert len(found) == 9 and len(set(found)) == 9                    # neuf pronostics, neuf genres différents
    # La preuve que c'est la pénalité qui fait la variété : sans elle, la double chance (« 1X » et « 2X » confondus) revient.
    monkeypatch.setattr(config, "GENRE_REPEAT_PENALTY", 0.0)
    sans = Counter(genres(gen.build_combos(shared_genre_day())))
    assert sans[markets.genre_of("1X")] >= 4


def test_plus_la_penalite_est_forte_moins_un_genre_se_repete(gen, monkeypatch):
    distinct = []
    for penalty in (0.0, 2.0, 8.0, 50.0):
        monkeypatch.setattr(config, "GENRE_REPEAT_PENALTY", penalty)
        distinct.append(len(set(genres(gen.build_combos(shared_genre_day())))))
    assert distinct == sorted(distinct) and distinct[0] < distinct[-1] == 9


def test_un_combine_evite_deux_fois_le_meme_genre_quand_c_est_possible(gen):
    for combo in gen.build_combos(shared_genre_day()):
        assert len({markets.genre_of(leg["type"]) for leg in combo["predictions"]}) == 3


def test_la_variete_ne_fait_perdre_aucun_combine_quand_les_paris_manquent(gen, monkeypatch):
    """Deux genres seulement sur tous les matchs : trois pronostics différents sont impossibles dans un combiné. La génération
    reprend alors les genres plutôt que de rendre un combiné de moins, même avec la pénalité la plus forte."""
    preds = []
    for m in range(9):
        preds.append(fake_leg(m, "1X" if m % 2 else "2X", "RESULTAT", 74, 1.40))
        preds.append(fake_leg(m, "TOTAL_1.5+", "TOTAL", 72, 1.40))
    for penalty in (0.0, 8.0, 50.0):
        monkeypatch.setattr(config, "GENRE_REPEAT_PENALTY", penalty)
        combos = gen.build_combos(preds)
        assert len(combos) == 3, penalty
        for combo in combos:
            assert len(combo["predictions"]) == 3 and len(combo["categories"]) == 2        # les règles de base tiennent toujours
            assert combo["total_odds"] >= config.TARGET_ODDS


def candidate(score, matches, genre_names):
    """Un combiné possible écrit à la main, pour essayer la recherche sans passer par les pronostics."""
    mask = sum(1 << m for m in matches)
    return combo_generator.Candidate(score, mask, tuple(genre_names), (), 2.6, score, frozenset(), frozenset())


def picked(chosen):
    return sorted((tuple(sorted(c.genres)), c.score) for c in chosen)


def test_les_combines_sont_choisis_ensemble_et_non_l_un_apres_l_autre():
    """Le combiné le mieux noté prend les genres a, b, c ; le suivant n'a plus que des répétitions. Deux combinés un peu moins bien
    notés, mais de genres tous différents, valent plus : la recherche les trouve, une composition qui prend toujours le meilleur
    d'abord non."""
    top = candidate(40, (1, 2, 3), "abc")
    second = candidate(39, (4, 5, 6), "abc")          # avec top : 3 répétitions
    alt_a = candidate(38, (1, 2, 4), "abd")
    alt_b = candidate(37, (3, 5, 6), "efc")
    pool = [top, second, alt_a, alt_b]
    chosen = ComboGenerator._select(pool, 2, 8.0)
    assert set(chosen) == {alt_a, alt_b}              # 75 points, aucun genre répété, contre 79 - 24 = 55
    assert set(ComboGenerator._select(pool, 2, 0.0)) == {top, second}         # sans pénalité : les deux mieux notés
    assert set(ComboGenerator._select(pool, 2, 1.0)) == {top, second}         # 79 - 3 = 76 > 75 : une pénalité faible ne suffit pas


def test_la_recherche_prefere_plus_de_combines_a_plus_de_variete():
    pool = [candidate(30, (1, 2, 3), "abc"), candidate(30, (4, 5, 6), "abc"), candidate(30, (7, 8, 9), "abc")]
    for penalty in (0.0, 8.0, 50.0):
        assert len(ComboGenerator._select(pool, 3, penalty)) == 3, penalty
    assert len(ComboGenerator._select(pool, 2, 8.0)) == 2                      # jamais plus que demandé
    assert ComboGenerator._select([], 3, 8.0) == [] and ComboGenerator._select(pool, 0, 8.0) == []


def test_la_recherche_ne_prend_jamais_deux_combines_sur_le_meme_match():
    pool = [candidate(40 - n, matches, "abc") for n, matches in enumerate(((1, 2, 3), (3, 4, 5), (5, 6, 1), (6, 7, 8)))]
    for penalty in (0.0, 8.0):
        chosen = ComboGenerator._select(pool, 3, penalty)
        masks = [c.mask for c in chosen]
        assert all(a & b == 0 for i, a in enumerate(masks) for b in masks[i + 1:])


def test_la_recherche_garde_toujours_autant_de_combines_que_la_composition_la_plus_simple():
    """Un cas où le meilleur combiné de tête empêche d'en faire trois : la recherche essaie d'autres têtes."""
    blocker = candidate(45, (1, 4, 7), "abc")            # coupe chacun des trois autres combinés
    pool = [blocker, candidate(30, (1, 2, 3), "def"), candidate(30, (4, 5, 6), "ghi"), candidate(30, (7, 8, 9), "jkl")]
    chosen = ComboGenerator._select(pool, 3, 8.0)
    assert len(chosen) == 3 and blocker not in chosen


def test_le_resultat_ne_depend_que_des_pronostics(gen):
    preds = shared_genre_day()
    assert gen.build_combos(preds) == gen.build_combos(list(preds)) == gen.build_combos(preds)


def test_un_combine_deja_trouve_n_est_pas_modifie_par_la_variete(gen):
    """Les champs d'un combiné sont ceux de ses pronostics, comme avant : la variété ne change que le choix."""
    for combo in gen.build_combos(shared_genre_day()):
        legs = combo["predictions"]
        assert combo["total_odds"] == round(legs[0]["estimated_odds"] * legs[1]["estimated_odds"] * legs[2]["estimated_odds"], 2)
        assert combo["avg_confidence"] == round(sum(leg["confidence"] for leg in legs) / 3, 1)
        assert combo["leagues"] == sorted({leg["league"] for leg in legs})
        assert combo["categories"] == sorted({leg["category"] for leg in legs})


@pytest.mark.parametrize("seed", range(10))
def test_la_variete_garde_autant_de_combines_et_aucun_pari_trop_bas(gen, monkeypatch, seed):
    """Sur des journées plausibles (15 matchs, IA bruitée) : autant de combinés qu'avec une pénalité nulle, davantage de genres
    de pari différents (ou autant), et les règles de base tiennent."""
    preds = make_all_preds(gen, seed=seed, cap=90)
    avec = gen.build_combos(preds)
    monkeypatch.setattr(config, "GENRE_REPEAT_PENALTY", 0.0)
    sans = gen.build_combos(preds)
    assert len(avec) == len(sans)
    assert len(set(genres(avec))) >= len(set(genres(sans)))
    for combo in avec:
        assert combo["total_odds"] >= config.TARGET_ODDS
        assert all(leg["estimated_odds"] >= config.MIN_LEG_ODDS for leg in combo["predictions"])


def heavy_day(matches, legs_per_match):
    """Un jour où chaque match offre énormément de pronostics éligibles (une IA qui met tout à plus de 65 %)."""
    codes = list(markets.PREDICTION_CODES)
    preds = []
    for m in range(matches):
        for n in range(legs_per_match):
            code = codes[(m * 7 + n * 3) % len(codes)]
            preds.append(fake_leg(m, code, markets.category_of(code), 66 + (n % 8), round(1.30 + 0.02 * n, 2)))
    return preds


def test_un_jour_tres_charge_reste_borne_en_temps_et_en_memoire(gen, monkeypatch):
    """18 matchs x 10 pronostics font 800 000 combinaisons : la composition retient alors moins de pronostics par match (les plus
    cotés) plutôt que d'examiner tout, et rend quand même ses trois combinés."""
    seen = {}
    original = ComboGenerator._candidates

    def spy(legs_by_match, target_odds, penalties=None):
        seen["count"] = combo_generator._combination_count(legs_by_match)
        seen["size"] = max(len(legs) for legs in legs_by_match.values())
        return original(legs_by_match, target_odds, penalties)

    monkeypatch.setattr(ComboGenerator, "_candidates", staticmethod(spy))
    combos = gen.build_combos(heavy_day(18, 10))
    assert len(combos) == 3
    assert seen["count"] <= combo_generator.MAX_RAW_COMBINATIONS and seen["size"] < 10
    # Un jour ordinaire n'est pas touché : tous ses pronostics sont gardés.
    seen.clear()
    gen.build_combos(heavy_day(15, 3))
    assert seen["size"] == 3 and seen["count"] == 455 * 27


def test_nombre_de_combinaisons():
    legs = lambda n: [object()] * n                                                 # noqa: E731
    assert combo_generator._combination_count({}) == 0
    assert combo_generator._combination_count({"a": legs(2), "b": legs(3)}) == 0            # moins de trois matchs
    assert combo_generator._combination_count({"a": legs(2), "b": legs(3), "c": legs(4)}) == 24
    assert combo_generator._combination_count({"a": legs(1), "b": legs(2), "c": legs(3), "d": legs(4)}) == (
        1 * 2 * 3 + 1 * 2 * 4 + 1 * 3 * 4 + 2 * 3 * 4)
