"""Composition des combinés : probabilités, cotes et sélection.

- get_predictions_from_analysis() : transforme l'analyse d'un match en pronostics
  (probabilité, confiance affichée, cote) ;
- build_combos() : choisit les meilleurs combinés de 3 matchs différents.

Sur les cotes : si ODDS_API_KEY est définie, les cotes des marchés couverts par
the-odds-api (résultat 1/N/2 et totaux de buts) sont les vraies cotes des bookmakers
(médiane de tous les bookmakers). Sinon, et pour tous les autres paris, la cote est
ESTIMÉE à partir de la probabilité, avec une marge de bookmaker (voir
probabilities.implied_odds) : une cote cohérente avec la confiance affichée, et non
plus une valeur fixe identique pour tous les matchs.
"""
import json
import logging
import os
import statistics
import time
from collections import Counter, defaultdict
from itertools import combinations, product

import requests

import config
import markets
from analyzerv2 import MatchAnalyzer
from probabilities import implied_odds, market_probability

log = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports"
ODDS_CACHE_MINUTES = int(os.environ.get("ODDS_CACHE_MINUTES", "360"))
ODDS_CACHE_DIR = os.path.join(config.DATA_DIR, "cache")

# Poids de la sélection : on classe les combinés selon leur probabilité de réussite
# (en %), avec un petit bonus de variété (championnats, types de paris) et une
# pénalité quand un type de pari a déjà été utilisé par un combiné retenu.
LEAGUE_BONUS = 2.0
CATEGORY_BONUS = 1.0
REPEAT_TYPE_PENALTY = 3.0


def _odds_api_key():
    return os.environ.get("ODDS_API_KEY")


def _valid_price(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 1.01


class ComboGenerator:
    # Correspondance championnat (clé de config.LEAGUES) -> sport the-odds-api.
    LEAGUE_TO_ODDS_SPORT = {
        "Premier League": "soccer_epl",
        "La Liga": "soccer_spain_la_liga",
        "Bundesliga": "soccer_germany_bundesliga",
        "Ligue 1": "soccer_france_ligue_one",
        "Serie A": "soccer_italy_serie_a",
    }

    def __init__(self, analyzer=None):
        self.analyzer = analyzer if analyzer is not None else MatchAnalyzer()
        self.min_confidence = config.MIN_CONFIDENCE
        # Évènements the-odds-api déjà téléchargés pendant cette génération
        # (une requête par championnat, pas une par match).
        self._league_events = {}

    # ------------------------------------------------------------------
    # Cotes réelles (optionnel : nécessite ODDS_API_KEY)
    # ------------------------------------------------------------------

    @staticmethod
    def _same_team(name_a, name_b):
        """Comparaison tolérante (« Man City » ~ « Manchester City »), mais une
        sous-chaîne trop courte ne suffit pas à confondre deux équipes."""
        a, b = (name_a or "").lower().strip(), (name_b or "").lower().strip()
        if not a or not b:
            return False
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        if len(shorter) < 4:
            return a == b
        return shorter in longer

    @staticmethod
    def _combine_odds(odds_a, odds_b):
        """Cote d'un pari « A ou B » (double chance) à partir des cotes de A et B.
        None si une des cotes manque ou est nulle (marché suspendu...)."""
        if not _valid_price(odds_a) or not _valid_price(odds_b):
            return None
        return round(1 / (1 / odds_a + 1 / odds_b), 2)

    @staticmethod
    def _extract_odds(event):
        """Cotes médianes (sur tous les bookmakers) d'un évènement the-odds-api :
        home / draw / away, over_<ligne> / under_<ligne>."""
        home_api, away_api = event.get("home_team"), event.get("away_team")
        prices = defaultdict(list)
        for bookmaker in event.get("bookmakers") or []:
            for market in bookmaker.get("markets") or []:
                key = market.get("key")
                for outcome in market.get("outcomes") or []:
                    price, name = outcome.get("price"), outcome.get("name")
                    if not _valid_price(price):
                        continue
                    if key == "h2h":
                        if name == home_api:
                            prices["home"].append(price)
                        elif name == away_api:
                            prices["away"].append(price)
                        elif name == "Draw":
                            prices["draw"].append(price)
                    elif key == "totals":
                        point = outcome.get("point")
                        if not isinstance(point, (int, float)) or isinstance(point, bool):
                            continue
                        if name in ("Over", "Under"):
                            prices[f"{name.lower()}_{float(point):g}"].append(price)
        return {k: round(statistics.median(v), 2) for k, v in prices.items() if v}

    def _cache_path(self, sport):
        return os.path.join(ODDS_CACHE_DIR, f"odds_{sport}.json")

    def _load_events(self, sport):
        """Évènements d'un sport : cache disque (pour économiser le quota de
        the-odds-api), sinon une requête. En cas d'échec on retombe sur un cache
        même périmé."""
        path = self._cache_path(sport)
        cached = None
        try:
            with open(path, "r", encoding="utf-8") as f:
                cached = json.load(f)
        except (OSError, ValueError):
            cached = None
        if cached and time.time() - cached.get("fetched_at", 0) < ODDS_CACHE_MINUTES * 60:
            return cached.get("events") or []
        try:
            response = requests.get(
                f"{ODDS_API_BASE}/{sport}/odds",
                params={"apiKey": _odds_api_key(), "regions": "eu", "markets": "h2h,totals"},
                timeout=10,
            )
            if response.status_code != 200:
                log.warning("the-odds-api %s : HTTP %s (clé invalide ou quota atteint ?)",
                            sport, response.status_code)
                return (cached or {}).get("events") or []
            events = response.json()
            if not isinstance(events, list):
                return (cached or {}).get("events") or []
        except Exception as e:
            log.warning("the-odds-api %s : %s", sport, e)
            return (cached or {}).get("events") or []
        try:
            os.makedirs(ODDS_CACHE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"fetched_at": time.time(), "events": events}, f)
        except OSError as e:
            log.warning("cache cotes non écrit : %s", e)
        return events

    def get_real_odds(self, home_team, away_team, league=None):
        """Vraies cotes (médiane des bookmakers) du match, ou None."""
        if not _odds_api_key():
            return None
        sport = self.LEAGUE_TO_ODDS_SPORT.get(league)
        sports = [sport] if sport else list(self.LEAGUE_TO_ODDS_SPORT.values())
        for sport_key in sports:
            if sport_key not in self._league_events:
                self._league_events[sport_key] = self._load_events(sport_key)
            for event in self._league_events[sport_key]:
                if (self._same_team(home_team, event.get("home_team"))
                        and self._same_team(away_team, event.get("away_team"))):
                    odds = self._extract_odds(event)
                    if odds:
                        return odds
        return None

    @classmethod
    def _market_price(cls, code, real_odds):
        """Vraie cote d'un pronostic quand son marché est coté par the-odds-api."""
        if not real_odds:
            return None
        market = markets.parse_market(code)
        if market is None:
            return None
        if market.kind == "result":
            if code == "V1":
                price = real_odds.get("home")
            elif code == "V2":
                price = real_odds.get("away")
            elif code == "1X":
                price = cls._combine_odds(real_odds.get("home"), real_odds.get("draw"))
            else:
                price = cls._combine_odds(real_odds.get("away"), real_odds.get("draw"))
            return price if _valid_price(price) else None
        if market.kind == "total":
            side = "over" if market.side == "+" else "under"
            price = real_odds.get(f"{side}_{market.line:g}")
            return price if _valid_price(price) else None
        return None

    # ------------------------------------------------------------------
    # Pronostics d'un match
    # ------------------------------------------------------------------

    def get_predictions_from_analysis(self, match, analysis, real_odds=None):
        """Pronostics retenus pour un match : ceux dont la confiance atteint le
        seuil minimal. Un match que l'IA n'a pas pu analyser est exclu."""
        if analysis.get("fallback"):
            return []
        base = analysis.get("base") or {}
        cap = analysis.get("cap", 100)
        legs = []
        for code in markets.PREDICTION_CODES:
            probability = market_probability(code, base)
            if probability is None:
                continue
            probability = min(probability, cap)
            confidence = int(round(probability))
            if confidence < self.min_confidence:
                continue
            real_price = self._market_price(code, real_odds)
            if real_price is not None:
                odds, source = real_price, "bookmakers"
            else:
                odds, source = implied_odds(probability, config.ODDS_MARGIN), "estimee"
            legs.append({
                "match_id": match["id"],
                "home_team": match["home_team"],
                "away_team": match["away_team"],
                "league": match["league"],
                "kickoff": match.get("kickoff"),
                "type": code,
                "type_name": markets.describe_prediction(code, match["home_team"], match["away_team"]),
                "category": markets.category_of(code),
                "confidence": confidence,
                "probability": round(probability, 2),
                "estimated_odds": odds,
                "odds_source": source,
            })
        return legs

    # ------------------------------------------------------------------
    # Sélection des combinés
    # ------------------------------------------------------------------

    @staticmethod
    def _shortlist(legs, size):
        """Les `size` pronostics les plus cotés d'un match. Garder les plus
        « confiants » serait une erreur : ce sont les moins cotés, et aucun
        combiné à 2,50+ ne peut être composé uniquement de paris très sûrs."""
        ranked = sorted(legs, key=lambda leg: (-leg["estimated_odds"], -leg["probability"]))
        return ranked[:size]

    @staticmethod
    def _best_combo(legs_by_match, excluded_matches, used_types, target_odds):
        """Meilleur combiné de 3 matchs (hors matchs déjà utilisés), ou None."""
        keys = [k for k in legs_by_match if k not in excluded_matches]
        best, best_score = None, None
        for ka, kb, kc in combinations(keys, 3):
            for la, lb, lc in product(legs_by_match[ka], legs_by_match[kb], legs_by_match[kc]):
                total_odds = round(la["estimated_odds"] * lb["estimated_odds"] * lc["estimated_odds"], 2)
                if total_odds < target_odds:
                    continue
                categories = {la["category"], lb["category"], lc["category"]}
                if len(categories) < 2:
                    continue
                leagues = {la["league"], lb["league"], lc["league"]}
                joint = la["probability"] * lb["probability"] * lc["probability"] / 10000.0
                score = (joint + LEAGUE_BONUS * (len(leagues) - 1) + CATEGORY_BONUS * (len(categories) - 1)
                         - REPEAT_TYPE_PENALTY * sum(1 for leg in (la, lb, lc) if used_types[leg["type"]]))
                if best_score is None or score > best_score:
                    best_score = score
                    best = (la, lb, lc, total_odds, joint, categories, leagues)
        return best

    def build_combos(self, all_preds, target_odds=None, max_combos=None):
        """Jusqu'à `max_combos` combinés de 3 matchs, sans match en commun entre
        combinés, chacun d'une cote totale >= `target_odds`, variés (championnats,
        types de pronostics) et classés par probabilité de réussite."""
        target_odds = config.TARGET_ODDS if target_odds is None else target_odds
        max_combos = config.MAX_COMBOS_RETOURNES if max_combos is None else max_combos

        grouped = defaultdict(list)
        for leg in all_preds:
            grouped[leg["match_id"]].append(leg)
        legs_by_match = {k: self._shortlist(v, config.MAX_PREDICTIONS_PAR_MATCH) for k, v in grouped.items()}

        selected = []
        used_matches = set()
        used_types = Counter()
        while len(selected) < max_combos:
            best = self._best_combo(legs_by_match, used_matches, used_types, target_odds)
            if best is None:
                break
            la, lb, lc, total_odds, joint, categories, leagues = best
            legs = [la, lb, lc]
            selected.append({
                "predictions": legs,
                "total_odds": total_odds,
                "avg_confidence": round(sum(leg["confidence"] for leg in legs) / 3, 1),
                "success_probability": round(joint, 1),
                "leagues": sorted(leagues),
                "categories": sorted(categories),
            })
            used_matches.update(leg["match_id"] for leg in legs)
            used_types.update(leg["type"] for leg in legs)
        selected.sort(key=lambda c: c["success_probability"], reverse=True)
        return selected

    def close(self):
        close = getattr(self.analyzer, "close", None)
        if close:
            close()
