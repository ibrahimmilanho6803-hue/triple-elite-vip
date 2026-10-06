import random

import pytest

import markets
from probabilities import (implied_odds, market_probability, poisson_lambda, poisson_tail,
                           reconcile)

AI_KEYS = ["v1", "v2", "1x", "2x", "over_0_5", "over_1_5", "over_2_5", "over_3_5",
           "under_0_5", "under_1_5", "under_2_5", "under_3_5", "eq1_over_0_5", "eq2_over_0_5",
           "au_moins_1_marque_1_5", "au_moins_1_marque_2_5", "au_moins_1_marque_3_5",
           "btts_oui", "btts_non"]

TYPICAL = {"v1": 55, "v2": 20, "1x": 80, "2x": 45,
           "over_0_5": 95, "over_1_5": 80, "over_2_5": 55, "over_3_5": 30,
           "under_0_5": 5, "under_1_5": 20, "under_2_5": 45, "under_3_5": 70,
           "eq1_over_0_5": 78, "eq2_over_0_5": 62,
           "au_moins_1_marque_1_5": 52, "au_moins_1_marque_2_5": 22, "au_moins_1_marque_3_5": 8,
           "btts_oui": 52, "btts_non": 48}


def random_ai(rng):
    """Estimations d'IA aléatoires, volontairement incohérentes et incomplètes."""
    data = {}
    for key in AI_KEYS:
        roll = rng.random()
        if roll < 0.1:
            continue                               # clé absente
        if roll < 0.15:
            data[key] = rng.choice([None, "abc", "", float("nan"), -20, 150, True])
        else:
            data[key] = rng.randint(0, 100)
    return data


def test_cas_du_defaut_signale():
    """Avant : « Victoire équipe 1 et plus de 0.5 but » affichait ~90 % alors que la
    victoire seule vaut 55 %. Un pari composé ne dépasse jamais sa condition la plus faible."""
    base = reconcile(TYPICAL)
    p_v1 = market_probability("V1", base)
    assert 50 <= p_v1 <= 60
    for code in ("V1_ET_1.5+", "V1_ET_2.5+", "V1_ET_3.5+", "V1_T1_2.5+", "V1_T1_3.5+"):
        assert market_probability(code, base) <= p_v1 + 1e-9, code
    assert market_probability("V1_ET_1.5+", base) < 50           # produit des deux conditions
    assert market_probability("EQ1_2.5+", base) < 25             # avant : ~55 % (confiance du total de buts)
    assert market_probability("EQ1_1.5+", base) < 45             # avant : ~80 %


def test_double_chance_et_victoire_coherentes():
    base = reconcile(TYPICAL)
    assert market_probability("1X", base) == pytest.approx(100 - market_probability("V2", base))
    assert market_probability("2X", base) == pytest.approx(100 - market_probability("V1", base))
    total = sum(market_probability(c, base) for c in ("V1", "V2")) + base["x"]
    assert total == pytest.approx(100)


def test_over_under_complementaires_et_decroissants():
    base = reconcile(TYPICAL)
    for line in ("0.5", "1.5", "2.5", "3.5"):
        assert (market_probability(f"TOTAL_{line}+", base)
                + market_probability(f"TOTAL_{line}-", base)) == pytest.approx(100)
    overs = [market_probability(f"TOTAL_{l}+", base) for l in ("0.5", "1.5", "2.5", "3.5")]
    assert overs == sorted(overs, reverse=True)


def test_deux_estimations_du_meme_evenement_sont_moyennees():
    base = reconcile({"over_2_5": 60, "under_2_5": 30})   # 60 et 100-30=70
    assert base["o25"] == pytest.approx(65)


def test_rattrapage_des_incoherences_de_l_ia():
    base = reconcile({"v1": 70, "v2": 60})                 # somme > 100 : impossible
    assert base["v1"] + base["v2"] <= 95.0 + 1e-9
    assert base["x"] >= 5.0 - 1e-9
    base = reconcile({"over_1_5": 40, "over_2_5": 70})     # over 2.5 > over 1.5 : impossible
    assert base["o25"] <= base["o15"]
    base = reconcile({"v1": 80, "over_0_5": 50})           # une victoire implique au moins un but
    assert base["o05"] >= 80
    base = reconcile({"eq1_over_0_5": 20, "v1": 60})       # l'équipe qui gagne a marqué
    assert base["e1"] >= 60


def test_donnees_manquantes_donnent_none():
    base = reconcile({})
    assert all(v is None for v in base.values())
    for code in markets.PREDICTION_CODES:
        assert market_probability(code, base) is None
    base = reconcile({"over_2_5": 60})
    assert market_probability("TOTAL_2.5+", base) == pytest.approx(60)
    assert market_probability("V1", base) is None
    assert market_probability("EQ1_1.5+", base) is None
    assert market_probability("V1_ET_2.5+", base) is None


def test_poisson():
    lam = poisson_lambda(75)
    assert lam == pytest.approx(1.386, abs=0.001)
    assert poisson_tail(lam, 0.5) == pytest.approx(75)
    assert poisson_tail(lam, 1.5) == pytest.approx(40.3, abs=0.2)
    assert poisson_tail(lam, 2.5) == pytest.approx(16.3, abs=0.2)
    assert poisson_tail(lam, 3.5) < poisson_tail(lam, 2.5)
    assert poisson_lambda(100) < 10 and poisson_lambda(0) == 0


def test_cotes_estimees_coherentes_avec_la_probabilite():
    assert implied_odds(50, 0.07) == pytest.approx(1.86, abs=0.01)
    assert implied_odds(100, 0.07) == 1.01
    assert implied_odds(0, 0.07) > implied_odds(50, 0.07)
    assert implied_odds(80, 0.07) < implied_odds(60, 0.07)


@pytest.mark.parametrize("seed", range(40))
def test_proprietes_sur_estimations_aleatoires(seed):
    rng = random.Random(seed)
    base = reconcile(random_ai(rng))
    probs = {code: market_probability(code, base) for code in markets.PREDICTION_CODES}
    for code, p in probs.items():
        assert p is None or 0 <= p <= 100, (code, p)

    def get(code):
        return probs[code]

    # Une condition de plus ne peut que rendre le pari moins probable.
    pairs = [("V1_ET_1.5+", "V1"), ("V1_ET_2.5+", "V1"), ("V1_ET_2.5+", "TOTAL_2.5+"),
             ("1X_ET_1.5+", "1X"), ("1X_ET_1.5+", "TOTAL_1.5+"), ("V2_ET_2.5+", "V2"),
             ("2X_ET_3.5+", "2X"), ("2X_ET_3.5+", "TOTAL_3.5+"),
             ("V1_T1_2.5+", "V1"), ("V1_T1_2.5+", "EQ1_2.5+"), ("1X_T1_1.5+", "1X"),
             ("1X_T1_1.5+", "EQ1_1.5+"), ("V2_T2_2.5+", "V2"), ("V2_T2_2.5+", "EQ2_2.5+"),
             ("2X_T2_0.5+", "2X"), ("2X_T2_0.5+", "EQ2_0.5+"), ("1X_T1_0.5+", "1X"),
             ("1X_T1_0.5+", "EQ1_0.5+")]
    for compound, simple in pairs:
        if get(compound) is not None and get(simple) is not None:
            assert get(compound) <= get(simple) + 1e-6, (compound, get(compound), simple, get(simple))
    # Logique du football.
    if get("1X") is not None and get("V1") is not None:
        assert get("1X") >= get("V1") - 1e-6
    if get("2X") is not None and get("V2") is not None:
        assert get("2X") >= get("V2") - 1e-6
    chain = [get(f"TOTAL_{l}+") for l in ("0.5", "1.5", "2.5", "3.5")]
    known = [v for v in chain if v is not None]
    assert known == sorted(known, reverse=True)
    for line in ("1.5", "2.5", "3.5"):
        if get(f"AU_MOINS_{line}") is not None and get(f"TOTAL_{line}+") is not None:
            assert get(f"AU_MOINS_{line}") <= get(f"TOTAL_{line}+") + 1e-6
    if get("BTTS_OUI") is not None:
        assert get("BTTS_OUI") + get("BTTS_NON") == pytest.approx(100)
        for team in ("EQ1_0.5+", "EQ2_0.5+"):
            if get(team) is not None:
                assert get("BTTS_OUI") <= get(team) + 1e-6
