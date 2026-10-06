"""Catalogue des pronostics proposés, leur verdict (gagné/perdu) et leur libellé.

Un pronostic est identifié par un code texte (ex. "V1", "TOTAL_2.5+",
"1X_ET_1.5+"). Ce module est la source unique de la signification de ces codes :
- parse_market()        : décode un code en une structure Market ;
- evaluate_prediction() : verdict d'un code à partir du score final ;
- describe_prediction() : libellé français montré au client (avec les noms des équipes) ;
- category_of()         : famille du pronostic (sert à varier les combinés).

Convention des lignes : « Plus de N » = strictement plus de N buts, « Moins de N »
= strictement moins de N buts. Les codes générés n'utilisent que des lignes en
« .5 » (aucune ambiguïté en cas de score pile égal à la ligne) ; les lignes
entières des anciens combinés enregistrés restent évaluées correctement.
"""
import re
from dataclasses import dataclass
from typing import Optional

RESULT_MARKETS = ("V1", "V2", "1X", "2X")

TOTAL_LINES = ("0.5", "1.5", "2.5", "3.5")
TEAM_LINES = ("0.5", "1.5", "2.5")
COMBINED_LINES = ("1.5", "2.5", "3.5")
AT_LEAST_LINES = ("1.5", "2.5", "3.5")


def _build_catalog():
    codes = list(RESULT_MARKETS)
    codes += [f"TOTAL_{line}+" for line in TOTAL_LINES]
    codes += [f"TOTAL_{line}-" for line in TOTAL_LINES]
    codes += [f"EQ{team}_{line}+" for team in (1, 2) for line in TEAM_LINES]
    # Résultat ET total de buts du match. (« V1 et plus de 0.5 but » n'existe
    # pas : toute victoire implique au moins un but, c'est le même pari que V1.)
    codes += [f"{result}_ET_{line}+" for result in RESULT_MARKETS for line in COMBINED_LINES]
    codes += [f"AU_MOINS_{line}" for line in AT_LEAST_LINES]
    # Résultat ET nombre de buts de l'équipe concernée. (« V1 et l'équipe 1 marque
    # plus de 1.5 » est exactement « V1 et plus de 1.5 buts » : une victoire à 1 but
    # de l'équipe 1 ne peut pas totaliser 2 buts. Ce doublon n'est donc pas proposé.)
    codes += [f"V1_T1_{line}+" for line in ("2.5", "3.5")]
    codes += [f"1X_T1_{line}+" for line in TOTAL_LINES]
    codes += [f"V2_T2_{line}+" for line in ("2.5", "3.5")]
    codes += [f"2X_T2_{line}+" for line in TOTAL_LINES]
    codes += ["BTTS_OUI", "BTTS_NON"]
    return tuple(codes)


# Pronostics générés pour chaque match, dans un ordre stable.
PREDICTION_CODES = _build_catalog()

_NUM = r"(\d+(?:\.\d+)?)"
_TOTAL_RE = re.compile(rf"TOTAL_{_NUM}([+-])")
_TEAM_RE = re.compile(rf"EQ([12])_{_NUM}\+")
_RESULT_TOTAL_RE = re.compile(rf"(V1|V2|1X|2X)_ET_{_NUM}\+")
_AT_LEAST_RE = re.compile(rf"AU_MOINS_{_NUM}")
_RESULT_TEAM1_RE = re.compile(rf"(V1|1X)_T1_{_NUM}\+")
_RESULT_TEAM2_RE = re.compile(rf"(V2|2X)_T2_{_NUM}\+")


@dataclass(frozen=True)
class Market:
    kind: str                       # result | btts | total | team | result_total | at_least | result_team
    result: Optional[str] = None    # V1 / V2 / 1X / 2X
    line: Optional[float] = None    # ligne de buts
    side: Optional[str] = None      # "+" / "-" (total) ; "1" / "2" (équipe concernée)
    yes: Optional[bool] = None      # BTTS oui / non


def parse_market(code):
    """Décode un code de pronostic ; None s'il est inconnu."""
    if not isinstance(code, str):
        return None
    if code in RESULT_MARKETS:
        return Market("result", result=code)
    if code == "BTTS_OUI":
        return Market("btts", yes=True)
    if code == "BTTS_NON":
        return Market("btts", yes=False)
    m = _TOTAL_RE.fullmatch(code)
    if m:
        return Market("total", line=float(m.group(1)), side=m.group(2))
    m = _TEAM_RE.fullmatch(code)
    if m:
        return Market("team", line=float(m.group(2)), side=m.group(1))
    m = _RESULT_TOTAL_RE.fullmatch(code)
    if m:
        return Market("result_total", result=m.group(1), line=float(m.group(2)))
    m = _AT_LEAST_RE.fullmatch(code)
    if m:
        return Market("at_least", line=float(m.group(1)))
    m = _RESULT_TEAM1_RE.fullmatch(code)
    if m:
        return Market("result_team", result=m.group(1), line=float(m.group(2)), side="1")
    m = _RESULT_TEAM2_RE.fullmatch(code)
    if m:
        return Market("result_team", result=m.group(1), line=float(m.group(2)), side="2")
    return None


def category_of(code):
    """Famille d'un pronostic : sert à ne pas empiler 3 paris du même genre."""
    market = parse_market(code)
    if market is None:
        return "AUTRE"
    return {
        "result": "RESULTAT",
        "btts": "BTTS",
        "total": "TOTAL",
        "team": "EQUIPE",
        "at_least": "AU_MOINS",
        "result_total": "COMBINE",
        "result_team": "COMBINE",
    }[market.kind]


# --------------------------------------------------------------------------
# Verdict à partir du score final
# --------------------------------------------------------------------------

def _result_won(result, home, away):
    if result == "V1":
        return home > away
    if result == "V2":
        return away > home
    if result == "1X":
        return home >= away
    if result == "2X":
        return away >= home
    return None


def evaluate_prediction(code, home, away):
    """True (gagné), False (perdu) ou None (code inconnu / score invalide)."""
    if not isinstance(home, int) or not isinstance(away, int) \
            or isinstance(home, bool) or isinstance(away, bool):
        return None
    market = parse_market(code)
    if market is None:
        return None
    total = home + away
    kind = market.kind
    if kind == "result":
        return _result_won(market.result, home, away)
    if kind == "btts":
        both = home > 0 and away > 0
        return both if market.yes else not both
    if kind == "total":
        return total > market.line if market.side == "+" else total < market.line
    if kind == "team":
        goals = home if market.side == "1" else away
        return goals > market.line
    if kind == "result_total":
        return _result_won(market.result, home, away) and total > market.line
    if kind == "at_least":
        return max(home, away) > market.line
    if kind == "result_team":
        goals = home if market.side == "1" else away
        return _result_won(market.result, home, away) and goals > market.line
    return None


def combo_status(outcomes):
    """'lost' dès qu'un pronostic est perdu, 'won' si tous sont gagnés,
    sinon 'pending' (au moins un match pas encore joué / vérifiable)."""
    if any(o is False for o in outcomes):
        return "lost"
    if outcomes and all(o is True for o in outcomes):
        return "won"
    return "pending"


# --------------------------------------------------------------------------
# Libellés pour les clients
# --------------------------------------------------------------------------

def _fr_number(line):
    text = f"{line:g}"
    return text.replace(".", ",")


def _goals(line):
    """« 1,5 but » (singulier sous 2) / « 2,5 buts »."""
    return f"{_fr_number(line)} {'but' if line < 2 else 'buts'}"


def describe_prediction(code, home, away):
    """Libellé français du pronostic, avec les noms des équipes."""
    market = parse_market(code)
    home = (home or "").strip() or "Équipe 1"
    away = (away or "").strip() or "Équipe 2"
    if market is None:
        return str(code)
    wins = {"V1": f"Victoire {home}", "V2": f"Victoire {away}",
            "1X": f"{home} gagne ou match nul", "2X": f"{away} gagne ou match nul"}
    kind = market.kind
    if kind == "result":
        return wins[market.result]
    if kind == "btts":
        return "Les deux équipes marquent : " + ("OUI" if market.yes else "NON")
    if kind == "total":
        word = "Plus" if market.side == "+" else "Moins"
        return f"{word} de {_goals(market.line)} dans le match"
    if kind == "team":
        team = home if market.side == "1" else away
        return f"{team} : plus de {_goals(market.line)}"
    if kind == "result_total":
        return f"{wins[market.result]} et plus de {_goals(market.line)} dans le match"
    if kind == "at_least":
        return f"Au moins une équipe marque plus de {_goals(market.line)}"
    if kind == "result_team":
        team = home if market.side == "1" else away
        return f"{wins[market.result]} et {team} marque plus de {_goals(market.line)}"
    return str(code)
