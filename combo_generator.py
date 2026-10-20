"""Composition des combinés : probabilités, cotes et sélection.

- get_predictions_from_analysis() : transforme l'analyse d'un match en pronostics
  (probabilité, confiance affichée, cote) ;
- build_combos() : choisit les meilleurs combinés de 3 matchs différents, en évitant de redonner à un client les matchs
  qu'il a déjà reçus aujourd'hui (voir variety.py).

Sur les cotes : si ODDS_API_KEY est définie, les cotes des marchés couverts par
the-odds-api (résultat 1/N/2 et totaux de buts) sont les vraies cotes des bookmakers :
celles du bookmaker de référence (config.ODDS_BOOKMAKER, 1xBet) quand il cote le pari,
sinon la médiane de tous les bookmakers, affichée avec un « ≈ » (origine « mediane »).
Sinon, et pour tous les autres paris, la cote est
ESTIMÉE à partir de la probabilité, avec une marge de bookmaker (voir
probabilities.implied_odds) : une cote cohérente avec la confiance affichée, et non
plus une valeur fixe identique pour tous les matchs. Dans les deux cas, un pronostic dont la
cote est inférieure à config.MIN_LEG_ODDS n'est jamais proposé (pas de pari « trop sûr » à 1,03).
"""
import json
import logging
import os
import re
import statistics
import time
import unicodedata
from collections import Counter, defaultdict, namedtuple
from functools import lru_cache
from itertools import combinations, product

import requests

import config
import markets
from analyzerv2 import MatchAnalyzer
from data_collector import hide_keys
from probabilities import implied_odds, market_probability

log = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports"
ODDS_CACHE_MINUTES = int(os.environ.get("ODDS_CACHE_MINUTES", "360"))
ODDS_CACHE_DIR = os.path.join(config.DATA_DIR, "cache")

# Poids de la sélection : on classe les combinés selon leur probabilité de réussite (en %), avec un petit bonus de variété
# (championnats, familles de paris). La variété des PRONOSTICS est une pénalité (config.GENRE_REPEAT_PENALTY) : dans l'ensemble
# des combinés d'une génération, chaque pronostic dont le genre de pari (voir markets.genre_of) figure déjà retire ce nombre de
# points à la note totale. Les combinés sont choisis ENSEMBLE (voir _select), pas l'un après l'autre : choisir d'abord le meilleur
# combiné lui faisait prendre les genres les plus courants et laissait les suivants sans variété possible.
LEAGUE_BONUS = 2.0
CATEGORY_BONUS = 1.0
SERVED_COUNTED_MAX = 3       # au plus ce nombre d'autres clients comptent dans la pénalité des matchs déjà distribués
SEARCH_STARTS = 12           # combien de combinés de tête essayés comme premier combiné de la recherche (voir _select)
# Nombre maximal de combinaisons examinées (trois matchs, un pronostic de chacun). Un jour ordinaire en compte de 10 000 à 50 000
# (15 matchs, 3 pronostics retenus par match en moyenne) ; au-delà, la composition retient moins de pronostics par match (les plus
# cotés, voir _shortlist) pour que le temps et la mémoire d'une génération restent bornés.
MAX_RAW_COMBINATIONS = 100_000

_genre = lru_cache(maxsize=None)(markets.genre_of)       # le genre d'un pari, calculé une fois par code

# Un combiné possible : sa note (probabilité de réussite en % + bonus - pénalité des matchs déjà reçus), les matchs qu'il utilise
# (un bit par match), les genres de ses trois pronostics, les pronostics, la cote totale, la probabilité de réussite, ses familles
# de paris et ses championnats.
Candidate = namedtuple("Candidate", "score mask genres legs total_odds joint categories leagues")

# Les lettres que la décomposition Unicode ne ramène pas à une lettre de base (« ı » turc sans point, « ø », « æ »...).
_LETTER_FOLD = str.maketrans({"ı": "i", "ø": "o", "æ": "ae", "œ": "oe", "ł": "l", "đ": "d", "ð": "d", "þ": "th"})


def _odds_api_key():
    return os.environ.get("ODDS_API_KEY")


def _valid_price(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 1.01


def _fold_name(name):
    """Nom d'équipe tel qu'on le compare d'une source de données à l'autre : minuscules, sans accents, tirets, points et
    apostrophes ramenés à une espace. « İstanbul Başakşehir » et « Istanbul Basaksehir », « Paris Saint-Germain » et
    « Paris Saint Germain », « Atlético Madrid » et « Atletico Madrid » donnent le même texte. Les lettres d'autres alphabets
    sont gardées telles quelles."""
    text = unicodedata.normalize("NFKD", str(name or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char)).translate(_LETTER_FOLD)
    return " ".join(re.sub(r"[\W_]+", " ", text).split())


def _repeats(genres, counts):
    """Combien des pronostics de `genres` répètent un genre déjà compté dans `counts`, ou déjà vu plus tôt dans `genres`."""
    repeated, earlier = 0, []
    for genre in genres:
        if counts.get(genre) or genre in earlier:
            repeated += 1
        earlier.append(genre)
    return repeated


def _tally(chosen):
    """(matchs utilisés sous forme de bits, nombre de pronostics par genre) des combinés retenus."""
    mask, counts = 0, Counter()
    for candidate in chosen:
        mask |= candidate.mask
        counts.update(candidate.genres)
    return mask, counts


def _best_addition(candidates, mask, counts, penalty):
    """Le combiné (sans match de `mask`) qui ajoute le plus à ceux déjà retenus : sa note moins `penalty` par pronostic dont le
    genre est déjà pris. (combiné, valeur), ou (None, None). `candidates` est trié par note décroissante : la valeur ne dépasse
    jamais la note, donc la recherche s'arrête dès que la note ne peut plus battre le meilleur trouvé."""
    best, best_value = None, None
    for candidate in candidates:
        if best_value is not None and candidate.score <= best_value:
            break
        if candidate.mask & mask:
            continue
        value = candidate.score - penalty * _repeats(candidate.genres, counts)
        if best_value is None or value > best_value:
            best, best_value = candidate, value
    return best, best_value


def _value(chosen, penalty):
    """Valeur d'un ensemble de combinés : la somme de leurs notes, moins `penalty` par pronostic qui répète un genre."""
    genres = [genre for candidate in chosen for genre in candidate.genres]
    return sum(candidate.score for candidate in chosen) - penalty * (len(genres) - len(set(genres)))


def _grow(candidates, chosen, max_combos, penalty):
    """Complète `chosen` combiné après combiné, en prenant à chaque fois celui qui ajoute le plus."""
    chosen = list(chosen)
    while len(chosen) < max_combos:
        mask, counts = _tally(chosen)
        best, _ = _best_addition(candidates, mask, counts, penalty)
        if best is None:
            break
        chosen.append(best)
    return chosen


def _refine(candidates, chosen, max_combos, penalty):
    """Remplace un combiné à la fois par un meilleur (compte tenu des autres), et en ajoute un si la place s'est libérée, tant que
    l'ensemble gagne. Chaque changement fait monter strictement la valeur : la recherche se termine ; le plafond de tours n'est
    qu'une ceinture."""
    chosen = list(chosen)
    for _ in range(30):
        changed = False
        for position in range(len(chosen)):
            mask, counts = _tally(chosen[:position] + chosen[position + 1:])
            best, best_value = _best_addition(candidates, mask, counts, penalty)
            if best is None or best is chosen[position]:
                continue
            current = chosen[position].score - penalty * _repeats(chosen[position].genres, counts)
            if best_value > current + 1e-9:
                chosen[position] = best
                changed = True
        grown = _grow(candidates, chosen, max_combos, penalty)
        if len(grown) > len(chosen):
            chosen, changed = grown, True
        if not changed:
            break
    return chosen


def _combination_count(legs_by_match):
    """Nombre de combinaisons « trois matchs différents, un pronostic de chacun » (polynôme symétrique élémentaire d'ordre 3 du
    nombre de pronostics de chaque match)."""
    e1 = e2 = e3 = 0
    for legs in legs_by_match.values():
        n = len(legs)
        e3 += e2 * n
        e2 += e1 * n
        e1 += n
    return e3


class ComboGenerator:
    # Correspondance championnat (clé de config.LEAGUES) -> sport the-odds-api.
    LEAGUE_TO_ODDS_SPORT = {
        "Premier League": "soccer_epl",
        "La Liga": "soccer_spain_la_liga",
        "Bundesliga": "soccer_germany_bundesliga",
        "Ligue 1": "soccer_france_ligue_one",
        "Serie A": "soccer_italy_serie_a",
        "Ligue des champions": "soccer_uefa_champs_league",
        "Premier-Liga (Russie)": "soccer_russia_premier_league",
        "Eredivisie (Pays-Bas)": "soccer_netherlands_eredivisie",
        "Primeira Liga (Portugal)": "soccer_portugal_primeira_liga",
        "Pro League (Belgique)": "soccer_belgium_first_div",
        "Süper Lig (Turquie)": "soccer_turkey_super_league",
        "Superliga (Danemark)": "soccer_denmark_superliga",
    }

    def __init__(self, analyzer=None):
        self.analyzer = analyzer if analyzer is not None else MatchAnalyzer()
        self.min_confidence = config.MIN_CONFIDENCE
        self.min_leg_odds = config.MIN_LEG_ODDS
        # Évènements the-odds-api déjà téléchargés pendant cette génération
        # (une requête par championnat, pas une par match).
        self._league_events = {}

    # ------------------------------------------------------------------
    # Cotes réelles (optionnel : nécessite ODDS_API_KEY)
    # ------------------------------------------------------------------

    @staticmethod
    def _same_team(name_a, name_b):
        """Comparaison tolérante d'un nom d'équipe d'une source à l'autre : sans accents ni ponctuation (voir _fold_name) et
        à une partie du nom près (« Brighton » ~ « Brighton and Hove Albion »), mais une sous-chaîne trop courte ne suffit pas
        à confondre deux équipes."""
        a, b = _fold_name(name_a), _fold_name(name_b)
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
    def _collect_prices(event, bookmaker_key=None):
        """{clé: [cotes, une par bookmaker]} d'un évènement the-odds-api : home / draw / away, over_<ligne> / under_<ligne>.
        `bookmaker_key` : ne lire que ce bookmaker (sa clé chez the-odds-api, par exemple « onexbet »)."""
        home_api, away_api = event.get("home_team"), event.get("away_team")
        prices = defaultdict(list)
        for bookmaker in event.get("bookmakers") or []:
            if bookmaker_key and str(bookmaker.get("key") or "").strip().lower() != bookmaker_key:
                continue
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
        return prices

    @classmethod
    def _extract_odds(cls, event):
        """Cotes médianes (sur tous les bookmakers) d'un évènement the-odds-api :
        home / draw / away, over_<ligne> / under_<ligne>."""
        return {k: round(statistics.median(v), 2) for k, v in cls._collect_prices(event).items() if v}

    @classmethod
    def _bookmaker_odds(cls, event, bookmaker_key):
        """Cotes d'UN bookmaker (sa clé chez the-odds-api) pour un évènement, de la même forme que _extract_odds mais
        seulement pour ce qu'il cote ; {} s'il ne cote rien de ce match. Le résultat 1/N/2 n'est gardé que s'il cote les trois
        issues : une double chance calculée avec deux issues de deux bookmakers différents ne serait chez aucun des deux."""
        if not bookmaker_key:
            return {}
        odds = {k: round(statistics.median(v), 2) for k, v in cls._collect_prices(event, bookmaker_key).items() if v}
        if not all(key in odds for key in ("home", "draw", "away")):
            for key in ("home", "draw", "away"):
                odds.pop(key, None)
        return odds

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
            log.warning("the-odds-api %s : %s", sport, hide_keys(e))        # le message contient l'adresse, donc la clé
            return (cached or {}).get("events") or []
        try:
            os.makedirs(ODDS_CACHE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"fetched_at": time.time(), "events": events}, f)
        except OSError as e:
            log.warning("cache cotes non écrit : %s", e)
        return events

    def get_real_odds(self, home_team, away_team, league=None):
        """Vraies cotes du match, ou None : la médiane des bookmakers (home / draw / away, over_<ligne> / under_<ligne>), et, sous
        la clé "reference", celles du bookmaker de référence (config.ODDS_BOOKMAKER, 1xBet) pour ce qu'il cote. La médiane sert de
        repli, et d'avis du marché à l'IA (config.AI_SEES_MARKET)."""
        if not _odds_api_key():
            return None
        sport = self.LEAGUE_TO_ODDS_SPORT.get(league)
        sports = [sport] if sport else list(self.LEAGUE_TO_ODDS_SPORT.values())
        searched = 0
        for sport_key in sports:
            if sport_key not in self._league_events:
                self._league_events[sport_key] = self._load_events(sport_key)
            events = self._league_events[sport_key]
            searched += len(events)
            for event in events:
                if (self._same_team(home_team, event.get("home_team"))
                        and self._same_team(away_team, event.get("away_team"))):
                    odds = self._extract_odds(event)
                    if odds:
                        if config.ODDS_BOOKMAKER:
                            reference = self._bookmaker_odds(event, config.ODDS_BOOKMAKER)
                            if reference:
                                odds["reference"] = reference
                            else:
                                log.info("cotes %s : %s – %s (%s) absent chez ce bookmaker : médiane des autres, cotes marquées ≈",
                                         config.ODDS_BOOKMAKER, home_team, away_team, league)
                        return odds
        if searched:
            # Pas de réponse de l'API (clé, quota) : déjà signalé par _load_events. Ici elle a répondu, mais sans ce match : il
            # n'est pas encore coté, ou son nom s'écrit autrement chez elle. Ses paris gardent une cote estimée.
            log.info("cotes réelles : %s – %s (%s) introuvable parmi les %d match(s) cotés par the-odds-api : cotes estimées",
                     home_team, away_team, league, searched)
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

    @classmethod
    def _priced(cls, code, real_odds):
        """(cote, origine) d'un pronostic coté par the-odds-api, ou (None, None) :
        - « bookmakers » : la cote du bookmaker de référence (config.ODDS_BOOKMAKER) ; quand aucun n'est choisi, la médiane de
          tous les bookmakers, comme avant la version 2.4.1 ;
        - « mediane » : la médiane des bookmakers, faute de la cote du bookmaker de référence. Elle est affichée avec un « ≈ » :
          chez lui, le client peut trouver un autre prix."""
        if not real_odds:
            return None, None
        price = cls._market_price(code, real_odds.get("reference"))
        if price is not None:
            return price, "bookmakers"
        price = cls._market_price(code, real_odds)
        if price is None:
            return None, None
        return price, ("mediane" if config.ODDS_BOOKMAKER else "bookmakers")

    # ------------------------------------------------------------------
    # Pronostics d'un match
    # ------------------------------------------------------------------

    def get_predictions_from_analysis(self, match, analysis, real_odds=None):
        """Pronostics retenus pour un match : ceux dont la confiance atteint le seuil minimal ET dont la cote atteint la cote
        minimale (config.MIN_LEG_ODDS). Un match que l'IA n'a pas pu analyser est exclu."""
        if analysis.get("fallback"):
            return []
        base = analysis.get("base") or {}
        cap = analysis.get("cap", 100)
        legs = []
        for code in markets.PREDICTION_CODES:
            estimate = market_probability(code, base)
            if estimate is None:
                continue
            # Le plafond (peu de matchs connus) limite la confiance AFFICHÉE ; il ne rend pas un pari plus payant. Une cote
            # estimée vient donc de la chance réellement estimée : plafonner d'abord affichait 1,29 pour un pari que les
            # bookmakers paient 1,05, et ce faux 1,29 le faisait choisir (mesuré : la cote totale affichée dépassait de
            # ~0,2 la cote réellement pariable ; elle la suit à ~0,04 près).
            probability = min(estimate, cap)
            confidence = int(round(probability))
            if confidence < self.min_confidence:
                continue
            odds, source = self._priced(code, real_odds)
            if odds is None:
                odds, source = implied_odds(estimate, config.ODDS_MARGIN), "estimee"
            if odds < self.min_leg_odds:
                continue                    # trop peu payé pour être proposé, même s'il est presque sûr (voir MIN_LEG_ODDS)
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
    def _candidates(legs_by_match, target_odds, penalties=None):
        """Tous les combinés possibles de 3 matchs, du mieux noté au moins bien noté : cote totale d'au moins `target_odds`, au
        moins deux familles de paris. `penalties` : {id du match: pénalité de variété par pronostic sur ce match}, retirée de la
        note du combiné (voir _variety_penalties). Deux combinés des mêmes matchs et des mêmes genres de pari ne diffèrent que
        par leur note : seul le mieux noté est gardé."""
        penalties = penalties or {}
        keys = [k for k, legs in legs_by_match.items() if legs]
        bit = {k: 1 << n for n, k in enumerate(keys)}
        best = {}
        for ka, kb, kc in combinations(keys, 3):
            variety_cost = penalties.get(ka, 0.0) + penalties.get(kb, 0.0) + penalties.get(kc, 0.0)
            mask = bit[ka] | bit[kb] | bit[kc]
            for la, lb, lc in product(legs_by_match[ka], legs_by_match[kb], legs_by_match[kc]):
                total_odds = round(la["estimated_odds"] * lb["estimated_odds"] * lc["estimated_odds"], 2)
                if total_odds < target_odds:
                    continue
                categories = {la["category"], lb["category"], lc["category"]}
                if len(categories) < 2:
                    continue
                leagues = {la["league"], lb["league"], lc["league"]}
                joint = la["probability"] * lb["probability"] * lc["probability"] / 10000.0
                score = (joint + LEAGUE_BONUS * (len(leagues) - 1) + CATEGORY_BONUS * (len(categories) - 1) - variety_cost)
                genres = (_genre(la["type"]), _genre(lb["type"]), _genre(lc["type"]))
                signature = (mask, tuple(sorted(genres)))
                known = best.get(signature)
                if known is None or score > known.score:
                    best[signature] = Candidate(score, mask, genres, (la, lb, lc), total_odds, joint, categories, leagues)
        return sorted(best.values(), key=lambda candidate: candidate.score, reverse=True)

    @staticmethod
    def _select(candidates, max_combos, penalty):
        """Les combinés d'une génération, choisis ensemble : jusqu'à `max_combos` combinés sans match en commun qui maximisent la
        somme de leurs notes, moins `penalty` points pour chaque pronostic dont le genre de pari figure déjà dans la génération
        (dans un autre combiné, ou plus tôt dans le même). Plus de combinés avant tout : une variété ne coûte jamais un combiné.

        Recherche locale à plusieurs départs (le problème exact se résout par programmation en nombres entiers ; mesuré sur des
        journées simulées, cette recherche en reste à environ un pour cent de l'optimum, en quelques centièmes de seconde) :
        chaque départ complète un combiné de tête puis remplace un combiné à la fois par un meilleur tant que l'ensemble gagne.
        `penalty` 0 : aucune variété, les combinés les mieux notés sans match en commun."""
        if not candidates or max_combos < 1:
            return []
        candidates = sorted(candidates, key=lambda candidate: candidate.score, reverse=True)       # déjà trié par _candidates
        starts = [_grow(candidates, [], max_combos, penalty), _grow(candidates, [], max_combos, 0.0)]
        tried = set()
        for first in candidates:
            if first.mask not in tried:                     # des départs sur des matchs différents explorent plus de choix
                tried.add(first.mask)
                starts.append(_grow(candidates, [first], max_combos, penalty))
                if len(tried) >= SEARCH_STARTS:
                    break
        best, best_key = [], None
        for chosen in starts:
            chosen = _refine(candidates, chosen, max_combos, penalty)
            key = (len(chosen), round(_value(chosen, penalty), 9))
            if best_key is None or key > best_key:
                best, best_key = chosen, key
        return best

    @staticmethod
    def _variety_penalties(match_ids, variety):
        """{id du match: pénalité} : très forte pour un match déjà donné à CE client aujourd'hui, modérée pour chaque autre
        client qui l'a reçu (voir config.VARIETY_* et variety.py). Vide sans `variety`."""
        if variety is None:
            return {}
        penalties = {}
        for match_id in match_ids:
            penalty = config.VARIETY_SERVED_PENALTY * min(variety.times_served(match_id), SERVED_COUNTED_MAX)
            if variety.is_seen(match_id):
                penalty += config.VARIETY_SEEN_PENALTY
            if penalty:
                penalties[match_id] = penalty
        return penalties

    def build_combos(self, all_preds, target_odds=None, max_combos=None, variety=None):
        """Jusqu'à `max_combos` combinés de 3 matchs, sans match en commun entre combinés, chacun d'une cote totale >=
        `target_odds`, variés (championnats, familles de paris, et genres de pronostics : voir _select et
        config.GENRE_REPEAT_PENALTY) et classés par probabilité de réussite. `variety` : ce que le client a déjà reçu aujourd'hui
        (variety.Variety), pour ne pas lui redonner les mêmes matchs."""
        target_odds = config.TARGET_ODDS if target_odds is None else target_odds
        max_combos = config.MAX_COMBOS_RETOURNES if max_combos is None else max_combos

        grouped = defaultdict(list)
        for leg in all_preds:
            grouped[leg["match_id"]].append(leg)
        size = config.MAX_PREDICTIONS_PAR_MATCH
        legs_by_match = {k: self._shortlist(v, size) for k, v in grouped.items()}
        while size > 3 and _combination_count(legs_by_match) > MAX_RAW_COMBINATIONS:
            size -= 1
            legs_by_match = {k: self._shortlist(v, size) for k, v in grouped.items()}
        if size < config.MAX_PREDICTIONS_PAR_MATCH:
            log.info("composition : %d pronostics par match au plus (beaucoup de pronostics éligibles, voir MAX_RAW_COMBINATIONS)",
                     size)
        penalties = self._variety_penalties(legs_by_match, variety)

        candidates = self._candidates(legs_by_match, target_odds, penalties)
        selected = []
        for candidate in self._select(candidates, max_combos, config.GENRE_REPEAT_PENALTY):
            legs = list(candidate.legs)
            selected.append({
                "predictions": legs,
                "total_odds": candidate.total_odds,
                "avg_confidence": round(sum(leg["confidence"] for leg in legs) / 3, 1),
                "success_probability": round(candidate.joint, 1),
                "leagues": sorted(candidate.leagues),
                "categories": sorted(candidate.categories),
            })
        selected.sort(key=lambda c: c["success_probability"], reverse=True)
        return selected

    def close(self):
        close = getattr(self.analyzer, "close", None)
        if close:
            close()
