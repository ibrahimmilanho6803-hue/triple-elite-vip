"""Fabriques de données de test partagées."""
import math
import random

import config
from probabilities import reconcile


class FakeAnalyzer:
    def close(self):
        pass


def poisson_ai(rng, noise=0.0):
    """Estimations d'IA plausibles tirées d'une loi de Poisson (avec du bruit)."""
    lam_h, lam_a = rng.uniform(0.8, 2.2), rng.uniform(0.6, 1.8)

    def pois(lam, k):
        return math.exp(-lam) * lam ** k / math.factorial(k)

    grid = {(i, j): pois(lam_h, i) * pois(lam_a, j) for i in range(10) for j in range(10)}
    total = sum(grid.values())

    def pct(cond):
        value = 100 * sum(v for (i, j), v in grid.items() if cond(i, j)) / total
        return max(1, min(99, round(value + (rng.gauss(0, noise) if noise else 0))))

    return {
        "v1": pct(lambda i, j: i > j), "v2": pct(lambda i, j: j > i),
        "1x": pct(lambda i, j: i >= j), "2x": pct(lambda i, j: j >= i),
        "over_0_5": pct(lambda i, j: i + j > 0), "over_1_5": pct(lambda i, j: i + j > 1),
        "over_2_5": pct(lambda i, j: i + j > 2), "over_3_5": pct(lambda i, j: i + j > 3),
        "under_0_5": pct(lambda i, j: i + j < 1), "under_1_5": pct(lambda i, j: i + j < 2),
        "under_2_5": pct(lambda i, j: i + j < 3), "under_3_5": pct(lambda i, j: i + j < 4),
        "eq1_over_0_5": pct(lambda i, j: i > 0), "eq2_over_0_5": pct(lambda i, j: j > 0),
        "au_moins_1_marque_1_5": pct(lambda i, j: max(i, j) > 1),
        "au_moins_1_marque_2_5": pct(lambda i, j: max(i, j) > 2),
        "au_moins_1_marque_3_5": pct(lambda i, j: max(i, j) > 3),
        "btts_oui": pct(lambda i, j: i > 0 and j > 0),
        "btts_non": pct(lambda i, j: not (i > 0 and j > 0)),
    }


def make_match(n, league=None, kickoff=None):
    leagues = list(config.LEAGUES)
    return {"id": str(1000 + n), "home_team": f"Domicile {n}", "away_team": f"Extérieur {n}",
            "league": league or leagues[n % len(leagues)], "kickoff": kickoff}


def make_analysis(rng, cap=90, noise=5.0):
    return {"home_team": "x", "away_team": "y", "base": reconcile(poisson_ai(rng, noise)), "cap": cap}


def make_all_preds(generator, seed=0, matches=15, cap=90):
    rng = random.Random(seed)
    preds = []
    for n in range(matches):
        match = make_match(n)
        preds += generator.get_predictions_from_analysis(match, make_analysis(rng, cap))
    return preds
