"""Probabilités des pronostics, calculées à partir des estimations de l'IA.

Principes (documentés aussi dans le README) :

1. Les estimations brutes de l'IA sont d'abord rendues cohérentes entre elles :
   quand deux chiffres décrivent le même événement (over 2.5 / under 2.5, victoire
   de l'extérieur / « 1X »...), on prend leur moyenne ; les probabilités sont
   forcées à respecter la logique du football (un « over » diminue quand la ligne
   monte, une victoire implique au moins un but, etc.).
2. Un pari simple reprend directement la probabilité de son événement.
3. Un pari composé (ex. « victoire ET plus de 1,5 but ») n'est gagnant que si TOUTES
   ses conditions le sont. Sa probabilité est le produit de celles de ses conditions
   (hypothèse d'indépendance, volontairement prudente), sauf quand une condition en
   implique une autre. Elle ne dépasse donc jamais celle de sa condition la plus faible.
4. Le nombre de buts d'une équipe suit une loi de Poisson dont le paramètre est déduit
   de la probabilité que cette équipe marque au moins un but.
5. Un plafond selon la quantité de données disponibles est appliqué par l'appelant.

Toutes les probabilités de ce module sont des pourcentages (0 à 100).
"""
import math

from markets import parse_market

MAX_POISSON_PCT = 99.0


def _pct(value):
    """Valeur numérique bornée à [0, 100], ou None si absente / invalide."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return max(0.0, min(100.0, number))


def _mean_of_estimates(direct, complement):
    """Moyenne de deux estimations du même événement : P(E) et 100 - P(non E)."""
    estimates = []
    d = _pct(direct)
    c = _pct(complement)
    if d is not None:
        estimates.append(d)
    if c is not None:
        estimates.append(100.0 - c)
    if not estimates:
        return None
    return sum(estimates) / len(estimates)


def reconcile(ai):
    """Rend cohérentes les estimations brutes de l'IA (dict de pourcentages).

    Renvoie un dict dont les valeurs valent None quand l'IA ne les a pas fournies :
      v1, x, v2          victoire domicile / nul / victoire extérieur
      o05..o35           P(total de buts > 0.5 / 1.5 / 2.5 / 3.5)
      btts               les deux équipes marquent
      e1, e2             l'équipe à domicile / à l'extérieur marque au moins un but
      au15, au25, au35   au moins une équipe marque plus de 1.5 / 2.5 / 3.5 buts
    """
    get = (ai or {}).get

    v1 = _mean_of_estimates(get("v1"), get("2x"))   # P(V1) = 100 - P(2X)
    v2 = _mean_of_estimates(get("v2"), get("1x"))   # P(V2) = 100 - P(1X)
    x = None
    if v1 is not None and v2 is not None:
        total = v1 + v2
        if total > 95.0:                            # il reste toujours un peu de place pour le nul
            v1, v2 = v1 * 95.0 / total, v2 * 95.0 / total
        x = 100.0 - v1 - v2

    overs = [
        _mean_of_estimates(get("over_0_5"), get("under_0_5")),
        _mean_of_estimates(get("over_1_5"), get("under_1_5")),
        _mean_of_estimates(get("over_2_5"), get("under_2_5")),
        _mean_of_estimates(get("over_3_5"), get("under_3_5")),
    ]
    e1 = _pct(get("eq1_over_0_5"))
    e2 = _pct(get("eq2_over_0_5"))

    # Une victoire (ou un but d'une équipe) implique au moins un but dans le match.
    floor_o05 = [v for v in (v1, v2, e1, e2) if v is not None]
    if overs[0] is not None and floor_o05:
        overs[0] = max(overs[0], max(floor_o05))
    # La probabilité de dépasser une ligne ne peut que baisser quand la ligne monte.
    running = None
    for i, value in enumerate(overs):
        if value is None:
            continue
        if running is not None:
            value = min(value, running)
        overs[i] = running = value
    o05, o15, o25, o35 = overs

    # Une équipe qui gagne a marqué ; et si le match compte au moins un but, l'équipe
    # à domicile a marqué sauf si l'extérieur a gagné sans qu'elle marque (donc
    # e1 >= o05 - v2, et symétriquement pour e2). Une équipe ne marque pas non plus
    # plus souvent que le match n'a de but.
    if e1 is not None:
        lower = [v for v in (v1, None if o05 is None or v2 is None else o05 - v2) if v is not None]
        e1 = max([e1] + lower)
        if o05 is not None:
            e1 = min(e1, o05)
    if e2 is not None:
        lower = [v for v in (v2, None if o05 is None or v1 is None else o05 - v1) if v is not None]
        e2 = max([e2] + lower)
        if o05 is not None:
            e2 = min(e2, o05)

    btts = _mean_of_estimates(get("btts_oui"), get("btts_non"))
    if btts is not None:
        # Les deux équipes marquent => chacune marque, et il y a au moins 2 buts.
        bounds = [v for v in (e1, e2, o15) if v is not None]
        if bounds:
            btts = min(btts, min(bounds))

    # « Une équipe marque plus de N buts » implique « le match compte plus de N buts ».
    au = [_pct(get("au_moins_1_marque_1_5")), _pct(get("au_moins_1_marque_2_5")),
          _pct(get("au_moins_1_marque_3_5"))]
    caps = [o15, o25, o35]
    running = None
    for i, value in enumerate(au):
        if value is None:
            continue
        if caps[i] is not None:
            value = min(value, caps[i])
        if running is not None:
            value = min(value, running)
        au[i] = running = value
    au15, au25, au35 = au

    return {
        "v1": v1, "x": x, "v2": v2,
        "o05": o05, "o15": o15, "o25": o25, "o35": o35,
        "btts": btts, "e1": e1, "e2": e2,
        "au15": au15, "au25": au25, "au35": au35,
    }


# --------------------------------------------------------------------------
# Buts d'une équipe (loi de Poisson)
# --------------------------------------------------------------------------

def poisson_lambda(p_scores_pct):
    """Nombre moyen de buts déduit de P(l'équipe marque au moins un but)."""
    p = min(max(p_scores_pct, 0.0), MAX_POISSON_PCT) / 100.0
    return -math.log(1.0 - p)


def poisson_tail(lam, line):
    """P(buts > line) en % pour une loi de Poisson de moyenne lam."""
    need = int(math.floor(line)) + 1            # « plus de 1.5 » => au moins 2
    cdf = 0.0
    term = math.exp(-lam)
    for k in range(need):
        cdf += term
        term *= lam / (k + 1)
    return max(0.0, min(1.0, 1.0 - cdf)) * 100.0


def _team_goals_over(base, side, line):
    e = base.get("e1" if side == "1" else "e2")
    if e is None:
        return None
    if line < 1:
        return e
    return poisson_tail(poisson_lambda(e), line)


# --------------------------------------------------------------------------
# Probabilité d'un pronostic
# --------------------------------------------------------------------------

def _both(a, b):
    """P(A et B) en supposant A et B indépendants, bornée par la logique (Fréchet)."""
    low = max(0.0, a + b - 100.0)
    high = min(a, b)
    return max(low, min(high, a * b / 100.0))


def _result_probability(result, base):
    v1, v2 = base.get("v1"), base.get("v2")
    if result == "V1":
        return v1
    if result == "V2":
        return v2
    if result == "1X":
        return None if v2 is None else 100.0 - v2
    if result == "2X":
        return None if v1 is None else 100.0 - v1
    return None


def _total_over(base, line):
    """P(total de buts > line) pour une ligne quelconque (le total est un entier)."""
    half = math.floor(line) + 0.5
    key = {0.5: "o05", 1.5: "o15", 2.5: "o25", 3.5: "o35"}.get(half)
    return base.get(key) if key else None


def _total_under(base, line):
    """P(total de buts < line) pour une ligne quelconque."""
    half = math.ceil(line) - 0.5
    key = {0.5: "o05", 1.5: "o15", 2.5: "o25", 3.5: "o35"}.get(half)
    over = base.get(key) if key else None
    return None if over is None else 100.0 - over


def market_probability(code, base):
    """Probabilité (%) du pronostic `code`, ou None si les données manquent."""
    market = parse_market(code)
    if market is None:
        return None
    kind = market.kind

    if kind == "result":
        return _result_probability(market.result, base)

    if kind == "btts":
        btts = base.get("btts")
        if btts is None:
            return None
        return btts if market.yes else 100.0 - btts

    if kind == "total":
        if market.side == "+":
            return _total_over(base, market.line)
        return _total_under(base, market.line)

    if kind == "team":
        return _team_goals_over(base, market.side, market.line)

    if kind == "at_least":
        if market.line < 1:
            return base.get("o05")
        key = {1.5: "au15", 2.5: "au25", 3.5: "au35"}.get(float(market.line))
        return base.get(key) if key else None

    if kind == "result_total":
        p_result = _result_probability(market.result, base)
        p_total = _total_over(base, market.line)
        if p_result is None or p_total is None:
            return None
        if market.line < 1:
            # Au moins un but : déjà acquis pour une victoire ; pour « ou nul »
            # il ne manque que le 0-0.
            if market.result in ("V1", "V2"):
                return p_result
            return max(0.0, p_result - (100.0 - p_total))
        return _both(p_result, p_total)

    if kind == "result_team":
        p_result = _result_probability(market.result, base)
        p_team = _team_goals_over(base, market.side, market.line)
        if p_result is None or p_team is None:
            return None
        if market.line < 1:
            o05 = base.get("o05")
            if market.result in ("V1", "V2"):
                return p_result
            if o05 is None:
                return None
            return max(0.0, p_result - (100.0 - o05))
        return _both(p_result, p_team)

    return None


def implied_odds(probability_pct, margin):
    """Cote décimale correspondant à une probabilité, avec une marge de bookmaker
    (les vrais bookmakers paient moins que la cote « juste » 1/p)."""
    p = max(float(probability_pct), 1.0) / 100.0
    return round(max(1.01, (1.0 - margin) / p), 2)
