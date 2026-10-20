"""Dossier d'un match pour l'IA : tout ce que la base sait des deux équipes, calculé sans appel réseau ni appel à l'IA.

L'IA ne vaut que par ce qu'on lui montre. Avant, elle recevait le bilan de la saison et cinq résultats bruts par équipe.
Le dossier y ajoute ce qu'un analyste regarde avant de se prononcer :

  - le bilan de la saison, et surtout les résultats À DOMICILE pour l'équipe qui reçoit, À L'EXTÉRIEUR pour l'autre ;
  - les huit derniers matchs, chacun avec la force de l'adversaire (un 3-0 contre le dernier ne vaut pas un 1-0 contre le
    premier), la tendance des buts sur cinq matchs, les matchs sans encaisser ou sans marquer ;
  - le repos (jours depuis le dernier match, toutes compétitions suivies confondues) ;
  - le contexte du championnat (buts par match, avantage du terrain, part de victoires à domicile, de nuls, de matchs à
    plus de 2,5 buts), pour que l'IA parte des bonnes moyennes et pas de ses a priori ;
  - les confrontations directes ;
  - un REPÈRE STATISTIQUE : un modèle de Poisson qui estime les buts attendus de chaque équipe d'après les buts marqués et
    encaissés de TOUTES les équipes du championnat (la force des adversaires rencontrés est donc prise en compte), tirés
    vers la moyenne tant que l'échantillon est petit. L'IA le voit comme un point de départ chiffré : elle l'ajuste avec sa
    lecture du dossier, elle ne le recopie pas ;
  - si demandé, l'avis du marché (cotes de plusieurs bookmakers, marge retirée), qui intègre déjà les compositions, les
    blessés et les suspendus que nous n'avons pas.

Ni compositions, ni blessés : la source de données (TheSportsDB) n'en fournit pas, même pour un match déjà joué. Le dossier
le dit à l'IA, pour qu'elle n'en invente aucun.

Rien ici n'est secret ni payant. Les données sont lues UNE fois (`DossierBuilder`), puis chaque match s'en sert.
"""
import math
from collections import defaultdict, namedtuple
from datetime import date, datetime, timezone

import config

Game = namedtuple("Game", "day home away hg ag league season")

# Moyennes de buts d'un championnat quand on n'en sait presque rien (domicile, extérieur) et poids de ces a priori, en matchs.
PRIOR_HOME_GOALS = 1.45
PRIOR_AWAY_GOALS = 1.15
PRIOR_LEAGUE_WEIGHT = 20
# Force d'une équipe avant de l'avoir vue jouer = la moyenne du championnat, avec ce poids (en buts : 9 buts = ~7 matchs).
# Une équipe qui a joué 8 matchs est donc jugée pour moitié sur ses résultats, une qui en a joué 2 à peine plus de 15 %.
# Réglé sur 14 000 matchs de la Premier League, de la Liga, de la Bundesliga et de la Serie A (10 saisons) : de 6 à 9 le modèle
# prévoit aussi bien les résultats ; à 9 il prévoit moins mal les buts (à 2, il se trompait nettement plus).
PRIOR_STRENGTH_GOALS = 9.0
RATING_ITERATIONS = 30
# Les buts d'un match (plus de 1,5 / 2,5 / 3,5 et « les deux équipes marquent ») se prévoient bien moins bien que le résultat :
# sur ces mêmes 14 000 matchs le modèle brut annonçait 74 % pour des « plus de 2,5 » qui arrivaient 65 % du temps. On ramène donc
# ces quatre chiffres à moitié vers la moyenne du championnat (annoncé 73 % -> observé 70 % ; le résultat, lui, était déjà juste).
GOAL_MARKET_SHRINK = 0.5
GOAL_MARKETS = ("o15", "o25", "o35", "btts")
# Moyennes de ces quatre chiffres dans les grands championnats, utilisées tant que la saison compte peu de matchs.
PRIOR_GOAL_RATES = {"o15": 0.77, "o25": 0.53, "o35": 0.28, "btts": 0.52}
MIN_LAMBDA, MAX_LAMBDA = 0.25, 4.0
GRID_SIZE = 11                # scores de 0 à 10 buts par équipe : au-delà, la probabilité est négligeable

RECENT_SHOWN = 8              # matchs listés pour chaque équipe
RECENT_AGGREGATE = 5          # matchs pour les moyennes « récentes »
H2H_SHOWN = 6
LOW_SAMPLE = 5                # en dessous, le dossier signale lui-même que les chiffres sont fragiles
MIN_LEAGUE_GAMES = 10         # en dessous, les moyennes du championnat sont signalées comme peu fiables


# --------------------------------------------------------------------------
# Petits utilitaires
# --------------------------------------------------------------------------

def _num(value, digits=2):
    """1.5 -> « 1,50 » (virgule décimale, comme tout ce que lit le client)."""
    return f"{value:.{digits}f}".replace(".", ",")


def _pct(value):
    """0.4567 -> « 46 % »."""
    return f"{int(math.floor(value * 100 + 0.5))} %"


def _parse_day(text):
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def _match_day(match):
    """Jour (UTC) du match à analyser : son coup d'envoi, à défaut sa date ; None si l'on ne sait pas."""
    kickoff = match.get("kickoff")
    if kickoff:
        try:
            moment = datetime.fromisoformat(kickoff)
            if moment.tzinfo is not None:
                moment = moment.astimezone(timezone.utc)
            return moment.date()
        except ValueError:
            pass
    return _parse_day(match.get("date"))


def _result(own, other):
    return "V" if own > other else ("N" if own == other else "D")


# --------------------------------------------------------------------------
# Modèle de Poisson : buts attendus et probabilités
# --------------------------------------------------------------------------

def fit_ratings(games, prior_goals=PRIOR_STRENGTH_GOALS, iterations=RATING_ITERATIONS):
    """Force offensive et défensive de chaque équipe d'un championnat.

    `games` : [(équipe à domicile, équipe à l'extérieur, buts domicile, buts extérieur)].
    Modèle : buts de l'équipe qui reçoit ~ Poisson(mu_dom x attaque[dom] x défense[ext]), buts de l'autre
    ~ Poisson(mu_ext x attaque[ext] x défense[dom]). 1,0 = la moyenne du championnat : une défense à 0,8 encaisse 20 % de
    moins que la moyenne. Les forces se corrigent l'une l'autre (marquer trois fois contre une défense poreuse compte moins),
    et l'a priori « équipe moyenne » pèse `prior_goals` buts, ce qui évite de conclure trop vite sur peu de matchs.

    Renvoie (mu_dom, mu_ext, attaque, défense) ; attaque et défense sont des dicts {équipe: force}."""
    games = [g for g in games if g[2] is not None and g[3] is not None]
    n = len(games)
    home_goals = sum(g[2] for g in games)
    away_goals = sum(g[3] for g in games)
    mu_home = (home_goals + PRIOR_LEAGUE_WEIGHT * PRIOR_HOME_GOALS) / (n + PRIOR_LEAGUE_WEIGHT)
    mu_away = (away_goals + PRIOR_LEAGUE_WEIGHT * PRIOR_AWAY_GOALS) / (n + PRIOR_LEAGUE_WEIGHT)
    teams = {g[0] for g in games} | {g[1] for g in games}
    attack = {team: 1.0 for team in teams}
    defence = {team: 1.0 for team in teams}
    for _ in range(iterations):
        num = {team: prior_goals for team in teams}
        den = {team: prior_goals for team in teams}
        for home, away, hg, ag in games:
            num[home] += hg
            den[home] += mu_home * defence[away]
            num[away] += ag
            den[away] += mu_away * defence[home]
        attack = {team: num[team] / den[team] for team in teams}
        num = {team: prior_goals for team in teams}
        den = {team: prior_goals for team in teams}
        for home, away, hg, ag in games:
            num[home] += ag                          # ce que l'équipe qui reçoit encaisse = ce que l'autre marque
            den[home] += mu_away * attack[away]
            num[away] += hg
            den[away] += mu_home * attack[home]
        defence = {team: num[team] / den[team] for team in teams}
    return mu_home, mu_away, attack, defence


def score_grid(lam_home, lam_away, size=GRID_SIZE):
    """{(buts domicile, buts extérieur): probabilité} pour deux lois de Poisson indépendantes."""
    def poisson(lam):
        terms = [math.exp(-lam)]
        for k in range(1, size):
            terms.append(terms[-1] * lam / k)
        return terms
    ph, pa = poisson(lam_home), poisson(lam_away)
    grid = {(i, j): ph[i] * pa[j] for i in range(size) for j in range(size)}
    total = sum(grid.values())
    return {cell: value / total for cell, value in grid.items()}


def poisson_view(lam_home, lam_away):
    """Probabilités (0 à 1) tirées de deux espérances de buts : issue du match, lignes de buts, les deux marquent, chaque
    équipe marque."""
    grid = score_grid(lam_home, lam_away)

    def prob(condition):
        return sum(p for (i, j), p in grid.items() if condition(i, j))

    return {
        "v1": prob(lambda i, j: i > j), "x": prob(lambda i, j: i == j), "v2": prob(lambda i, j: j > i),
        "o15": prob(lambda i, j: i + j > 1), "o25": prob(lambda i, j: i + j > 2), "o35": prob(lambda i, j: i + j > 3),
        "btts": prob(lambda i, j: i > 0 and j > 0), "e1": prob(lambda i, j: i > 0), "e2": prob(lambda i, j: j > 0),
    }


def market_view(odds):
    """Probabilités du marché (marge des bookmakers retirée) tirées des cotes médianes de ComboGenerator.get_real_odds :
    {"v1", "x", "v2", "o15", "o25", "o35"} (0 à 1), seulement ce qui est coté. None si rien n'est utilisable."""
    if not isinstance(odds, dict):
        return None

    def valid(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 1.01

    view = {}
    home, draw, away = odds.get("home"), odds.get("draw"), odds.get("away")
    if valid(home) and valid(draw) and valid(away):
        inverse = [1 / home, 1 / draw, 1 / away]
        total = sum(inverse)
        view["v1"], view["x"], view["v2"] = (x / total for x in inverse)
    for key, line in (("o15", "1.5"), ("o25", "2.5"), ("o35", "3.5")):
        over, under = odds.get(f"over_{line}"), odds.get(f"under_{line}")
        if valid(over) and valid(under):
            view[key] = (1 / over) / (1 / over + 1 / under)
    return view or None


# --------------------------------------------------------------------------
# Lecture de la base
# --------------------------------------------------------------------------

class DossierBuilder:
    """Construit le dossier de chaque match à partir d'UNE lecture des matchs terminés de la base (`conn` : connexion sqlite3)."""

    def __init__(self, conn):
        rows = conn.execute(
            "SELECT date, home_team, away_team, home_score, away_score, league, season FROM matches "
            "WHERE home_score IS NOT NULL AND away_score IS NOT NULL ORDER BY date, id").fetchall()
        everything = []
        for day, home, away, hg, ag, league, season in rows:
            parsed = _parse_day(day)
            if parsed is None:
                continue
            everything.append(Game(parsed, home, away, int(hg), int(ag), league or "", season or ""))
        self.all_games = everything                                    # toutes saisons : pour les confrontations directes
        current = max((g.season for g in everything if g.season), default="")
        # La saison en cours seulement (une équipe ne se juge pas sur l'an dernier) ; une ligne sans saison est gardée.
        self.games = [g for g in everything if not g.season or g.season == current]
        self.by_team = defaultdict(list)
        domestic = defaultdict(lambda: defaultdict(int))              # équipe -> {championnat: matchs joués}
        for g in self.games:
            self.by_team[g.home].append(g)
            self.by_team[g.away].append(g)
            if g.league and g.league not in config.INTERNATIONAL_COMPETITIONS:
                domestic[g.home][g.league] += 1
                domestic[g.away][g.league] += 1
        # Le championnat « d'origine » d'une équipe : celui où elle a le plus joué (une équipe de Ligue des champions y joue
        # aussi dans son championnat, et c'est là que sa force se mesure).
        self.home_league = {team: max(counts, key=counts.get) for team, counts in domestic.items()}
        self._ratings = {}
        self._ppg = {}
        self._league = {}
        self._tables = {}

    # ------------------------------------------------------------------ calculs par équipe

    def points_per_game(self, team):
        """Points par match pris cette saison (toutes compétitions suivies), ou None sans match."""
        if team not in self._ppg:
            games = self.by_team.get(team, [])
            if not games:
                self._ppg[team] = None
            else:
                points = 0
                for g in games:
                    own, other = (g.hg, g.ag) if g.home == team else (g.ag, g.hg)
                    points += 3 if own > other else (1 if own == other else 0)
                self._ppg[team] = points / len(games)
        return self._ppg[team]

    def team_facts(self, team, before=None):
        """Chiffres d'une équipe cette saison, ou None sans match. `before` : jour du match à analyser (les matchs de ce jour
        ou plus tard ne comptent pas)."""
        games = [g for g in self.by_team.get(team, []) if before is None or g.day < before]
        if not games:
            return None
        rows = []
        for g in games:
            at_home = g.home == team
            own, other = (g.hg, g.ag) if at_home else (g.ag, g.hg)
            opponent = g.away if at_home else g.home
            rows.append({"game": g, "home": at_home, "gf": own, "ga": other, "opp": opponent, "res": _result(own, other)})

        def tally(subset):
            n = len(subset)
            return {
                "n": n, "w": sum(1 for r in subset if r["res"] == "V"), "d": sum(1 for r in subset if r["res"] == "N"),
                "l": sum(1 for r in subset if r["res"] == "D"),
                "gf": sum(r["gf"] for r in subset) / n if n else 0.0, "ga": sum(r["ga"] for r in subset) / n if n else 0.0,
                "clean": sum(1 for r in subset if r["ga"] == 0), "blank": sum(1 for r in subset if r["gf"] == 0),
                "btts": sum(1 for r in subset if r["gf"] > 0 and r["ga"] > 0),
                "o15": sum(1 for r in subset if r["gf"] + r["ga"] > 1), "o25": sum(1 for r in subset if r["gf"] + r["ga"] > 2),
                "o35": sum(1 for r in subset if r["gf"] + r["ga"] > 3),
            }

        season = tally(rows)
        season["ppg"] = (3 * season["w"] + season["d"]) / season["n"]
        return {"season": season, "home": tally([r for r in rows if r["home"]]), "away": tally([r for r in rows if not r["home"]]),
                "recent": list(reversed(rows))[:RECENT_SHOWN], "last": tally(list(reversed(rows))[:RECENT_AGGREGATE]),
                "last_day": rows[-1]["game"].day}

    # ------------------------------------------------------------------ championnat

    def league_facts(self, league):
        """Moyennes du championnat cette saison, ou None sans match."""
        if league not in self._league:
            games = [g for g in self.games if g.league == league]
            n = len(games)
            if not n:
                self._league[league] = None
            else:
                self._league[league] = {
                    "n": n, "goals": sum(g.hg + g.ag for g in games) / n, "home": sum(g.hg for g in games) / n,
                    "away": sum(g.ag for g in games) / n,
                    "hw": sum(1 for g in games if g.hg > g.ag) / n, "d": sum(1 for g in games if g.hg == g.ag) / n,
                    "aw": sum(1 for g in games if g.hg < g.ag) / n,
                    "btts": sum(1 for g in games if g.hg > 0 and g.ag > 0) / n,
                    "o15": sum(1 for g in games if g.hg + g.ag > 1) / n, "o25": sum(1 for g in games if g.hg + g.ag > 2) / n,
                    "o35": sum(1 for g in games if g.hg + g.ag > 3) / n,
                }
        return self._league[league]

    def _fitted(self, league):
        """Forces des équipes d'un championnat (voir fit_ratings), calculées une seule fois ; None sans match."""
        if league not in self._ratings:
            games = [(g.home, g.away, g.hg, g.ag) for g in self.games if g.league == league]
            self._ratings[league] = fit_ratings(games) if games else None
        return self._ratings[league]

    def _goal_rates(self, league):
        """Moyennes du championnat pour les quatre chiffres de buts (GOAL_MARKETS), tirées vers les moyennes des grands
        championnats tant que la saison compte peu de matchs."""
        facts = self.league_facts(league)
        n = facts["n"] if facts else 0
        return {key: ((facts[key] * n if facts else 0.0) + PRIOR_LEAGUE_WEIGHT * PRIOR_GOAL_RATES[key]) / (n + PRIOR_LEAGUE_WEIGHT)
                for key in GOAL_MARKETS}

    def baseline(self, match):
        """Repère statistique du match : {"lam_home", "lam_away", + poisson_view}, ou None quand il n'a pas de sens (compétition
        entre équipes de championnats différents) ou pas de données. Les chiffres de buts sont ramenés vers la moyenne du
        championnat (GOAL_MARKET_SHRINK) : le modèle brut y était trop sûr de lui."""
        league = match.get("league")
        if league in config.INTERNATIONAL_COMPETITIONS:
            return None
        fitted = self._fitted(league)
        if fitted is None:
            return None
        mu_home, mu_away, attack, defence = fitted
        home, away = match["home_team"], match["away_team"]
        lam_home = mu_home * attack.get(home, 1.0) * defence.get(away, 1.0)
        lam_away = mu_away * attack.get(away, 1.0) * defence.get(home, 1.0)
        lam_home = min(max(lam_home, MIN_LAMBDA), MAX_LAMBDA)
        lam_away = min(max(lam_away, MIN_LAMBDA), MAX_LAMBDA)
        view = poisson_view(lam_home, lam_away)
        rates = self._goal_rates(league)
        for key in GOAL_MARKETS:
            view[key] = rates[key] + GOAL_MARKET_SHRINK * (view[key] - rates[key])
        view.update({"lam_home": lam_home, "lam_away": lam_away})
        return view

    # ------------------------------------------------------------------ niveau d'une équipe dans son championnat

    def _table(self, league):
        """{équipe: (rang, nombre d'équipes classées)} d'un championnat, au nombre de points par match puis à la différence de
        buts par match. Seules comptent les équipes qui ont joué au moins 3 matchs ; {} s'il y en a moins de 6."""
        if league not in self._tables:
            stats = defaultdict(lambda: [0, 0, 0])                    # équipe -> [matchs, points, différence de buts]
            for g in self.games:
                if g.league != league:
                    continue
                for team, own, other in ((g.home, g.hg, g.ag), (g.away, g.ag, g.hg)):
                    row = stats[team]
                    row[0] += 1
                    row[1] += 3 if own > other else (1 if own == other else 0)
                    row[2] += own - other
            ranked = sorted((team for team, row in stats.items() if row[0] >= 3),
                            key=lambda team: (-stats[team][1] / stats[team][0], -stats[team][2] / stats[team][0], team))
            self._tables[league] = ({team: (i + 1, len(ranked)) for i, team in enumerate(ranked)} if len(ranked) >= 6 else {})
        return self._tables[league]

    def level(self, team):
        """Niveau d'une équipe dans son championnat : {"league", "rank", "of", "attack", "defence"} (les clés dont on ne sait
        rien sont absentes), ou None. attack et defence valent 1,0 pour la moyenne du championnat ; défense plus basse =
        meilleure défense."""
        league = self.home_league.get(team)
        if league is None:
            return None
        info = {"league": league}
        rank = self._table(league).get(team)
        if rank:
            info["rank"], info["of"] = rank
        fitted = self._fitted(league)
        if fitted and team in fitted[2]:
            info["attack"], info["defence"] = fitted[2][team], fitted[3][team]
        return info

    def head_to_head(self, home, away):
        """Derniers face-à-face (toutes saisons), du plus récent au plus ancien."""
        meetings = [g for g in self.all_games if {g.home, g.away} == {home, away} and home != away]
        return list(reversed(meetings))[:H2H_SHOWN]

    # ------------------------------------------------------------------ rendu

    def _team_block(self, team, role, match_day):
        """Le paragraphe d'une équipe. `role` : « domicile » ou « extérieur » (c'est le lieu de CE match qui compte)."""
        facts = self.team_facts(team, before=match_day)
        label = f"{team} ({role.upper()})"
        if facts is None:
            return (f"{label} : aucun match connu cette saison. Reste proche des moyennes du championnat et n'accorde "
                    f"aucune confiance à un avis sur cette équipe.")
        s = facts["season"]
        lines = [f"{label} — saison : {s['n']} match{'s' if s['n'] > 1 else ''}, {s['w']}V {s['d']}N {s['l']}D "
                 f"({_num(s['ppg'])} pt/match), buts marqués/encaissés par match {_num(s['gf'])}/{_num(s['ga'])}"
                 + (" — ÉCHANTILLON FAIBLE" if s["n"] < LOW_SAMPLE else "")]
        level = self.level(team)
        if level and "attack" in level:
            standing = (f"{'1er' if level['rank'] == 1 else str(level['rank']) + 'e'} sur {level['of']} de {level['league']} "
                        f"aux points par match ; ") if "rank" in level else ""
            lines.append(f"  niveau : {standing}attaque {_num(level['attack'])}, défense {_num(level['defence'])} "
                         f"(1,00 = moyenne du championnat ; défense basse = bonne défense)")
        venue = facts["home" if role == "domicile" else "away"]
        where = "à domicile" if role == "domicile" else "à l'extérieur"
        if venue["n"]:
            lines.append(f"  {where} : {venue['n']} match{'s' if venue['n'] > 1 else ''}, {venue['w']}V {venue['d']}N {venue['l']}D, "
                         f"buts {_num(venue['gf'])}/{_num(venue['ga'])} par match")
        else:
            lines.append(f"  {where} : aucun match joué")
        lines.append(f"  sur la saison : plus de 1,5 buts dans {s['o15']}/{s['n']} matchs, plus de 2,5 dans {s['o25']}/{s['n']}, "
                     f"plus de 3,5 dans {s['o35']}/{s['n']} ; les deux équipes ont marqué dans {s['btts']}/{s['n']} ; "
                     f"{s['clean']} sans encaisser, {s['blank']} sans marquer")
        last = facts["last"]
        lines.append(f"  {last['n']} derniers matchs : {_num(last['gf'], 1)} marqué(s) et {_num(last['ga'], 1)} encaissé(s) par match, "
                     f"{last['clean']} sans encaisser, {last['blank']} sans marquer, les deux équipes ont marqué {last['btts']} fois")
        if match_day is not None:
            lines.append(f"  repos : {max(0, (match_day - facts['last_day']).days)} jour(s) depuis son dernier match")
        shown = []
        for r in facts["recent"]:
            ppg = self.points_per_game(r["opp"])
            rating = f"{_num(ppg, 1)} pt/m" if ppg is not None else "?"
            shown.append(f"{r['res']} ({'D' if r['home'] else 'E'}) {r['gf']}-{r['ga']} {r['opp']} [{rating}, "
                         f"{r['game'].day.strftime('%d/%m')}]")
        lines.append("  derniers matchs (du plus récent) : " + " ; ".join(shown))
        return "\n".join(lines)

    def _league_line(self, league):
        facts = self.league_facts(league)
        if facts is None:
            return f"CHAMPIONNAT {league} : aucune donnée de la saison."
        warning = " — ÉCHANTILLON FAIBLE" if facts["n"] < MIN_LEAGUE_GAMES else ""
        return (f"CHAMPIONNAT {league} ({facts['n']} matchs cette saison{warning}) : {_num(facts['goals'])} buts par match "
                f"({_num(facts['home'])} pour l'équipe qui reçoit, {_num(facts['away'])} pour l'autre) ; victoire à domicile "
                f"{_pct(facts['hw'])}, nul {_pct(facts['d'])}, victoire à l'extérieur {_pct(facts['aw'])} ; les deux équipes "
                f"marquent {_pct(facts['btts'])} ; plus de 1,5 buts {_pct(facts['o15'])}, plus de 2,5 {_pct(facts['o25'])}, "
                f"plus de 3,5 {_pct(facts['o35'])}")

    def _baseline_line(self, match):
        base = self.baseline(match)
        if base is None:
            return ("REPÈRE STATISTIQUE : indisponible pour cette compétition (équipes de championnats différents) — "
                    "appuie-toi sur les moyennes et le niveau de chaque équipe dans son championnat.")
        home, away = match["home_team"], match["away_team"]
        return (f"REPÈRE STATISTIQUE (modèle de Poisson sur les buts de tout le championnat, force des adversaires incluse ; sans "
                f"blessés ni compositions) : buts attendus {_num(base['lam_home'])} pour {home}, {_num(base['lam_away'])} pour "
                f"{away} ; victoire {home} {_pct(base['v1'])}, nul {_pct(base['x'])}, victoire {away} {_pct(base['v2'])} ; "
                f"plus de 1,5 buts {_pct(base['o15'])}, plus de 2,5 {_pct(base['o25'])}, plus de 3,5 {_pct(base['o35'])} ; les deux "
                f"équipes marquent {_pct(base['btts'])} ; {home} marque au moins un but {_pct(base['e1'])}, {away} {_pct(base['e2'])}")

    @staticmethod
    def _market_line(odds):
        view = market_view(odds)
        if view is None:
            return None
        parts = []
        if "v1" in view:
            parts.append(f"victoire domicile {_pct(view['v1'])}, nul {_pct(view['x'])}, victoire extérieur {_pct(view['v2'])}")
        for key, label in (("o15", "plus de 1,5 buts"), ("o25", "plus de 2,5"), ("o35", "plus de 3,5")):
            if key in view:
                parts.append(f"{label} {_pct(view[key])}")
        return ("MARCHÉ (cotes médianes de plusieurs bookmakers, marge retirée ; elles intègrent déjà les compositions, les blessés "
                "et les suspendus que tu n'as pas) : " + " ; ".join(parts))

    def render(self, match, market_odds=None):
        """Le dossier du match, prêt à être collé dans le message envoyé à l'IA."""
        home, away, league = match["home_team"], match["away_team"], match.get("league", "")
        match_day = _match_day(match)
        header = f"=== MATCH id={match['id']} | {home} (domicile) vs {away} (extérieur) | {league} ==="
        meetings = self.head_to_head(home, away)
        h2h = (" ; ".join(f"{g.day.strftime('%d/%m/%Y')} {g.home} {g.hg}-{g.ag} {g.away}" for g in meetings)
               if meetings else "aucune confrontation connue")
        lines = [header, self._league_line(league),
                 self._team_block(home, "domicile", match_day),
                 self._team_block(away, "extérieur", match_day),
                 f"CONFRONTATIONS DIRECTES : {h2h}", self._baseline_line(match)]
        market = self._market_line(market_odds)
        if market:
            lines.append(market)
        return "\n".join(lines) + "\n"
