"""Le dossier que l'IA lit pour chaque match (dossier.py) : lecture de la base, présentation, modèle statistique."""
import math
import random
import sqlite3
from datetime import date

import pytest

import config
from dossier import (DossierBuilder, GOAL_MARKET_SHRINK, LOW_SAMPLE, fit_ratings, market_view, poisson_view, score_grid)

SEASON = "2026-2027"


def make_conn(rows):
    """Base de test au format du site. rows : (date, domicile, extérieur, buts dom., buts ext., championnat, saison)."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE matches (id INTEGER PRIMARY KEY, date TEXT, home_team TEXT, away_team TEXT, home_score INTEGER, "
                 "away_score INTEGER, league TEXT, season TEXT, status TEXT)")
    conn.executemany("INSERT INTO matches (date, home_team, away_team, home_score, away_score, league, season, status) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, 'FT')", rows)
    return conn


def poisson_sample(rng, lam):
    """Tirage d'une loi de Poisson (algorithme de Knuth) : le test n'a besoin de rien d'autre que la bibliothèque standard."""
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def synthetic_league(rng, teams=10, rounds=3, league="Serie A", season=SEASON, start=date(2026, 8, 20)):
    """Un championnat dont on connaît les vraies forces : renvoie (matchs, attaque, défense). Les matchs sont des tuples de
    make_conn, datés un par jour de la ronde."""
    names = [f"T{i}" for i in range(teams)]
    attack = {t: math.exp(rng.gauss(0, 0.28)) for t in names}
    defence = {t: math.exp(rng.gauss(0, 0.28)) for t in names}
    rows, day = [], 0
    for _ in range(rounds):
        for home in names:
            for away in names:
                if home == away:
                    continue
                hg = poisson_sample(rng, 1.45 * attack[home] * defence[away])
                ag = poisson_sample(rng, 1.15 * attack[away] * defence[home])
                rows.append((date.fromordinal(start.toordinal() + day // 5).isoformat(), home, away, hg, ag, league, season))
                day += 1
    return rows, attack, defence


def correlation(xs, ys):
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))


# --------------------------------------------------------------------------
# Le modèle de Poisson
# --------------------------------------------------------------------------

def test_les_probabilites_de_poisson_sont_coherentes():
    grid = score_grid(1.4, 1.1)
    assert abs(sum(grid.values()) - 1) < 1e-12
    view = poisson_view(1.4, 1.1)
    assert abs(view["v1"] + view["x"] + view["v2"] - 1) < 1e-9
    assert view["o15"] > view["o25"] > view["o35"]
    assert view["btts"] <= min(view["e1"], view["e2"])
    assert abs(view["e1"] - (1 - math.exp(-1.4))) < 1e-3 and abs(view["e2"] - (1 - math.exp(-1.1))) < 1e-3
    even = poisson_view(1.2, 1.2)
    assert abs(even["v1"] - even["v2"]) < 1e-12 and even["x"] > 0.25
    assert poisson_view(2.2, 0.7)["v1"] > 0.6 > poisson_view(0.7, 2.2)["v1"]


def test_les_forces_des_equipes_sont_retrouvees():
    rows, attack, defence = synthetic_league(random.Random(5), teams=12, rounds=3)
    mu_home, mu_away, fitted_attack, fitted_defence = fit_ratings([(r[1], r[2], r[3], r[4]) for r in rows])
    teams = sorted(attack)
    assert 1.2 < mu_home < 1.8 and 0.9 < mu_away < 1.4 and mu_home > mu_away          # l'avantage du terrain
    assert correlation([attack[t] for t in teams], [fitted_attack[t] for t in teams]) > 0.7
    assert correlation([defence[t] for t in teams], [fitted_defence[t] for t in teams]) > 0.7
    assert 0.85 < sum(fitted_attack.values()) / len(teams) < 1.15


def test_peu_de_matchs_tire_vers_la_moyenne():
    league = [("A", "B", 1, 1), ("C", "D", 1, 1), ("B", "C", 1, 1), ("D", "A", 1, 1)]
    games = league + [("E", "A", 5, 0)]                      # E a joué UN match et l'a gagné 5-0
    _, _, attack_prudent, _ = fit_ratings(games, prior_goals=9.0)
    _, _, attack_libre, _ = fit_ratings(games, prior_goals=0.5)
    assert 1.0 < attack_prudent["E"] < attack_libre["E"]
    assert attack_prudent["E"] < 1.4                          # un match ne fait pas une superpuissance


def test_les_matchs_sans_score_sont_ignores_par_le_modele():
    mu_home, mu_away, attack, defence = fit_ratings([("A", "B", 2, 1), ("A", "C", None, None), ("B", "C", 1, None)])
    assert set(attack) == {"A", "B"} and mu_home > 0


def test_avis_du_marche_sans_la_marge():
    view = market_view({"home": 2.0, "draw": 4.0, "away": 4.0, "over_2.5": 1.8, "under_2.5": 2.0, "over_1.5": 1.2})
    assert abs(view["v1"] - 0.5) < 1e-9 and abs(view["x"] - 0.25) < 1e-9 and abs(view["v2"] - 0.25) < 1e-9
    assert abs(view["o25"] - (1 / 1.8) / (1 / 1.8 + 1 / 2.0)) < 1e-9
    assert "o15" not in view                                  # il manque le « moins de 1,5 » : on ne devine pas
    margin = market_view({"home": 1.9, "draw": 3.8, "away": 3.8})
    assert abs(margin["v1"] + margin["x"] + margin["v2"] - 1) < 1e-9 and margin["v1"] == pytest.approx(0.5)
    assert market_view(None) is None and market_view({}) is None
    assert market_view({"home": 0.5, "draw": "x", "away": True, "over_2.5": None, "under_2.5": 2.0}) is None


# --------------------------------------------------------------------------
# Lecture de la base : ce que sait le dossier d'une équipe
# --------------------------------------------------------------------------

def small_league():
    """Trois équipes de Serie A + un adversaire d'avant-saison."""
    return [
        ("2026-08-23", "Alpha", "Beta", 3, 0, "Serie A", SEASON),
        ("2026-08-30", "Gamma", "Alpha", 1, 0, "Serie A", SEASON),
        ("2026-09-13", "Alpha", "Gamma", 1, 1, "Serie A", SEASON),
        ("2026-09-20", "Beta", "Gamma", 0, 2, "Serie A", SEASON),
    ]


def test_bilan_domicile_exterieur_et_repos_d_une_equipe():
    builder = DossierBuilder(make_conn(small_league()))
    facts = builder.team_facts("Alpha", before=date(2026, 9, 26))
    season = facts["season"]
    assert (season["n"], season["w"], season["d"], season["l"]) == (3, 1, 1, 1) and season["ppg"] == pytest.approx(4 / 3)
    assert (facts["home"]["n"], facts["home"]["w"], facts["home"]["d"]) == (2, 1, 1) and facts["home"]["gf"] == 2.0
    assert (facts["away"]["n"], facts["away"]["l"]) == (1, 1) and facts["away"]["ga"] == 1.0
    assert facts["last_day"] == date(2026, 9, 13) and [r["res"] for r in facts["recent"]] == ["N", "D", "V"]   # le plus récent d'abord
    text = builder.render({"id": "7", "home_team": "Alpha", "away_team": "Gamma", "league": "Serie A",
                           "kickoff": "2026-09-26T18:45:00+00:00"})
    assert "=== MATCH id=7 | Alpha (domicile) vs Gamma (extérieur) | Serie A ===" in text
    assert "Alpha (DOMICILE) — saison : 3 matchs, 1V 1N 1D (1,33 pt/match), buts marqués/encaissés par match 1,33/0,67" in text
    assert "à domicile : 2 matchs, 1V 1N 0D, buts 2,00/0,50 par match" in text
    assert "Gamma (EXTÉRIEUR) — saison : 3 matchs, 2V 1N 0D" in text
    assert "à l'extérieur : 2 matchs, 1V 1N 0D" in text
    assert "repos : 13 jour(s) depuis son dernier match" in text
    assert "ÉCHANTILLON FAIBLE" in text and LOW_SAMPLE > 3


def test_les_derniers_matchs_donnent_le_lieu_et_la_force_de_l_adversaire():
    builder = DossierBuilder(make_conn(small_league()))
    text = builder.render({"id": "7", "home_team": "Alpha", "away_team": "Gamma", "league": "Serie A", "kickoff": "2026-09-26T18:45:00+00:00"})
    line = next(l for l in text.splitlines() if l.strip().startswith("derniers matchs") and "Beta" in l and "Gamma" in l)
    assert line.index("N (D) 1-1 Gamma") < line.index("D (E) 0-1 Gamma") < line.index("V (D) 3-0 Beta")
    assert "N (D) 1-1 Gamma [2,3 pt/m, 13/09]" in line               # Gamma : 7 points en 3 matchs
    assert "V (D) 3-0 Beta [0,0 pt/m, 23/08]" in line               # Beta n'a pris aucun point


def test_un_match_du_jour_ou_plus_tard_n_est_pas_connu_du_dossier():
    rows = small_league() + [("2026-09-26", "Alpha", "Beta", 9, 9, "Serie A", SEASON), ("2026-10-03", "Beta", "Alpha", 9, 9, "Serie A", SEASON)]
    builder = DossierBuilder(make_conn(rows))
    facts = builder.team_facts("Alpha", before=date(2026, 9, 26))
    assert facts["season"]["n"] == 3 and facts["last_day"] == date(2026, 9, 13)
    assert "9-9" not in builder.render({"id": "7", "home_team": "Alpha", "away_team": "Beta", "league": "Serie A",
                                        "kickoff": "2026-09-26T18:45:00+00:00"}).split("CONFRONTATIONS")[0]


def test_la_saison_precedente_compte_pour_les_confrontations_pas_pour_la_forme():
    old = [("2026-03-01", "Alpha", "Beta", 4, 4, "Serie A", "2025-2026"), ("2026-04-01", "Alpha", "Zeta", 5, 0, "Serie A", "2025-2026")]
    builder = DossierBuilder(make_conn(small_league() + old))
    assert builder.team_facts("Alpha", before=date(2026, 9, 26))["season"]["n"] == 3        # l'an dernier ne juge pas l'équipe
    text = builder.render({"id": "7", "home_team": "Alpha", "away_team": "Beta", "league": "Serie A", "kickoff": "2026-09-26T18:45:00+00:00"})
    assert "CONFRONTATIONS DIRECTES : 23/08/2026 Alpha 3-0 Beta ; 01/03/2026 Alpha 4-4 Beta" in text
    assert "Zeta" not in text.split("CONFRONTATIONS")[0]


def test_equipe_sans_match_et_championnat_sans_donnee():
    builder = DossierBuilder(make_conn(small_league()))
    text = builder.render({"id": "9", "home_team": "Inconnue", "away_team": "Alpha", "league": "La Liga", "kickoff": "2026-09-26T18:45:00+00:00"})
    assert "Inconnue (DOMICILE) : aucun match connu cette saison" in text
    assert "CHAMPIONNAT La Liga : aucune donnée de la saison" in text
    assert "aucune confrontation connue" in text and "REPÈRE STATISTIQUE : indisponible" not in text.split("CONFRONTATIONS")[0]
    empty = DossierBuilder(make_conn([]))
    assert "aucun match connu" in empty.render({"id": "1", "home_team": "A", "away_team": "B", "league": "Serie A"})
    assert empty.baseline({"home_team": "A", "away_team": "B", "league": "Serie A"}) is None


def test_les_lignes_inutilisables_de_la_base_sont_ignorees():
    conn = make_conn(small_league() + [("n'importe quoi", "Alpha", "Beta", 1, 1, "Serie A", SEASON),
                                       ("2026-09-02", "Alpha", "Beta", None, None, "Serie A", SEASON),
                                       ("", "Beta", "Alpha", 2, 2, "Serie A", SEASON)])
    builder = DossierBuilder(conn)
    assert builder.team_facts("Alpha", before=date(2026, 9, 26))["season"]["n"] == 3


def test_repos_inconnu_sans_heure_de_match():
    builder = DossierBuilder(make_conn(small_league()))
    text = builder.render({"id": "7", "home_team": "Alpha", "away_team": "Gamma", "league": "Serie A"})
    assert "repos :" not in text and "Alpha (DOMICILE)" in text
    dated = builder.render({"id": "7", "home_team": "Alpha", "away_team": "Gamma", "league": "Serie A", "date": "2026-09-16 18:45:00"})
    assert "repos : 3 jour(s) depuis son dernier match" in dated


def test_moyennes_du_championnat():
    builder = DossierBuilder(make_conn(small_league()))
    facts = builder.league_facts("Serie A")
    assert facts["n"] == 4 and facts["goals"] == pytest.approx(2.0) and facts["hw"] == pytest.approx(0.5) and facts["d"] == pytest.approx(0.25)
    assert facts["o25"] == pytest.approx(0.25) and facts["btts"] == pytest.approx(0.25)
    assert "ÉCHANTILLON FAIBLE" in builder._league_line("Serie A")           # 4 matchs : trop peu
    assert builder.league_facts("Autre") is None


# --------------------------------------------------------------------------
# Repère statistique, niveau et classement
# --------------------------------------------------------------------------

def test_le_repere_prefere_le_plus_fort():
    rows, attack, defence = synthetic_league(random.Random(8), teams=10, rounds=2)
    builder = DossierBuilder(make_conn(rows))
    teams = sorted(attack, key=lambda t: attack[t] / defence[t])
    weak, strong = teams[0], teams[-1]
    for_strong = builder.baseline({"home_team": strong, "away_team": weak, "league": "Serie A"})
    for_weak = builder.baseline({"home_team": weak, "away_team": strong, "league": "Serie A"})
    assert for_strong["v1"] > for_strong["v2"] and for_weak["v2"] > for_weak["v1"]
    assert for_strong["lam_home"] > for_weak["lam_home"] and 0.25 <= for_strong["lam_away"] <= 4.0
    assert abs(for_strong["v1"] + for_strong["x"] + for_strong["v2"] - 1) < 1e-9
    unknown = builder.baseline({"home_team": "Nouvelle", "away_team": weak, "league": "Serie A"})
    assert unknown is not None and unknown["lam_home"] > 0              # une équipe inconnue vaut la moyenne


def test_les_chiffres_de_buts_sont_ramenes_vers_la_moyenne_du_championnat():
    rows, attack, defence = synthetic_league(random.Random(3), teams=10, rounds=2)
    builder = DossierBuilder(make_conn(rows))
    teams = sorted(attack, key=lambda t: attack[t] * defence[t])
    match = {"home_team": teams[-1], "away_team": teams[-2], "league": "Serie A"}      # deux équipes très ouvertes
    shown = builder.baseline(match)
    raw = poisson_view(shown["lam_home"], shown["lam_away"])
    rates = builder._goal_rates("Serie A")
    for key in ("o15", "o25", "o35", "btts"):
        assert shown[key] == pytest.approx(rates[key] + GOAL_MARKET_SHRINK * (raw[key] - rates[key]))
        assert abs(shown[key] - rates[key]) <= abs(raw[key] - rates[key]) + 1e-12
    for key in ("v1", "x", "v2", "e1", "e2"):                                           # le résultat, lui, est déjà juste : inchangé
        assert shown[key] == pytest.approx(raw[key])


def test_moyennes_de_buts_tirees_vers_les_grands_championnats_en_debut_de_saison():
    builder = DossierBuilder(make_conn(small_league()))                    # 4 matchs seulement
    rates = builder._goal_rates("Serie A")
    assert rates["o25"] == pytest.approx((0.25 * 4 + 20 * 0.53) / 24)     # 4 matchs (dont un seul à plus de 2,5 buts) pèsent peu
    assert rates["o15"] > 0.6 and 0.45 < rates["btts"] < 0.55
    assert builder._goal_rates("Inconnu")["o25"] == pytest.approx(0.53)


def test_classement_et_forces_dans_le_dossier():
    rows, attack, defence = synthetic_league(random.Random(11), teams=8, rounds=2)
    builder = DossierBuilder(make_conn(rows))
    ranks = {team: builder.level(team)["rank"] for team in attack}
    assert sorted(ranks.values()) == list(range(1, 9))
    best = next(t for t, r in ranks.items() if r == 1)
    level = builder.level(best)
    assert level["of"] == 8 and level["league"] == "Serie A" and level["attack"] > 0 and level["defence"] > 0
    worst = next(t for t, r in ranks.items() if r == 8)
    text = builder.render({"id": "1", "home_team": best, "away_team": worst, "league": "Serie A", "kickoff": "2027-01-01T15:00:00+00:00"})
    assert "niveau : 1er sur 8 de Serie A aux points par match ; attaque" in text and "niveau : 8e sur 8 de Serie A" in text


def test_pas_de_classement_avec_trop_peu_d_equipes():
    builder = DossierBuilder(make_conn(small_league()))                    # 3 équipes
    level = builder.level("Alpha")
    assert "rank" not in level and level["attack"] > 0
    text = builder.render({"id": "1", "home_team": "Alpha", "away_team": "Gamma", "league": "Serie A", "kickoff": "2026-09-26T18:45:00+00:00"})
    assert "niveau : attaque" in text and "e sur" not in text.split("CONFRONTATIONS")[0].split("derniers")[0]
    assert builder.level("Inconnue") is None


def test_competition_entre_championnats_sans_repere_mais_avec_le_niveau_de_chaque_equipe():
    assert "Ligue des champions" in config.INTERNATIONAL_COMPETITIONS
    rows, attack, defence = synthetic_league(random.Random(2), teams=8, rounds=2)
    rows += [("2026-09-16", "T0", "Etranger FC", 2, 1, "Ligue des champions", SEASON)]
    builder = DossierBuilder(make_conn(rows))
    match = {"id": "3", "home_team": "T0", "away_team": "Etranger FC", "league": "Ligue des champions", "kickoff": "2026-10-20T19:00:00+00:00"}
    assert builder.baseline(match) is None
    text = builder.render(match)
    assert "REPÈRE STATISTIQUE : indisponible pour cette compétition" in text
    assert "niveau : " in text.split("Etranger FC (EXTÉRIEUR)")[0]          # T0 est jugée sur son championnat
    assert "V (D) 2-1 Etranger FC" in text                                    # et ses matchs européens comptent dans sa forme
    assert builder.level("T0")["league"] == "Serie A" and builder.level("Etranger FC") is None


def test_avis_des_bookmakers_dans_le_dossier():
    builder = DossierBuilder(make_conn(small_league()))
    match = {"id": "7", "home_team": "Alpha", "away_team": "Gamma", "league": "Serie A", "kickoff": "2026-09-26T18:45:00+00:00"}
    assert "MARCHÉ" not in builder.render(match) and "MARCHÉ" not in builder.render(match, {})
    text = builder.render(match, {"home": 2.0, "draw": 4.0, "away": 4.0, "over_2.5": 1.8, "under_2.5": 2.0})
    market = next(line for line in text.splitlines() if line.startswith("MARCHÉ"))
    assert "victoire domicile 50 %, nul 25 %, victoire extérieur 25 %" in market and "plus de 2,5 53 %" in market
    assert "compositions" in market and "plus de 1,5" not in market


def test_un_dossier_reste_court():
    """Le coût de l'IA suit la taille du dossier : un match d'un championnat à 20 équipes tient en ~1 000 jetons (~3 000 signes)."""
    rows, attack, defence = synthetic_league(random.Random(4), teams=20, rounds=1)
    builder = DossierBuilder(make_conn(rows))
    text = builder.render({"id": "1", "home_team": "T1", "away_team": "T2", "league": "Serie A", "kickoff": "2027-01-01T15:00:00+00:00"},
                          {"home": 2.0, "draw": 3.4, "away": 3.8, "over_2.5": 1.9, "under_2.5": 1.9})
    assert 1500 < len(text) < 4200
    assert text.count("=== MATCH") == 1 and text.endswith("\n")
