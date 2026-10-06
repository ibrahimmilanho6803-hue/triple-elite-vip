"""Historique des combines passes.

Lit les combines sauvegardes a chaque generation (results/combo_*.json), les
rapproche des scores reels deja connus en base (table 'matches') et donne un
verdict gagne/perdu par pronostic puis par combine. Aucune dependance a Flask :
dashboard.py n'a plus qu'a renvoyer le resultat de load_history() en JSON.
"""
import glob
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone

import requests

import config
from data_collector import DataCollector, is_final_status, parse_score

# --------------------------------------------------------------------------
# Verdict d'un pronostic a partir du score final
# --------------------------------------------------------------------------

_NUM = r"(\d+(?:\.\d+)?)"
_TOTAL_RE = re.compile(rf"TOTAL_{_NUM}([+-])")
_TEAM_RE = re.compile(rf"EQ([12])_{_NUM}\+")
_RESULT_AND_TOTAL_RE = re.compile(rf"(V1|V2|1X|2X)_ET_{_NUM}\+")
_AT_LEAST_RE = re.compile(rf"AU_MOINS_{_NUM}")
_RESULT_AND_TEAM1_RE = re.compile(rf"(V1|1X)_T1_{_NUM}\+")
_RESULT_AND_TEAM2_RE = re.compile(rf"(V2|2X)_T2_{_NUM}\+")


def _result_market(market, home, away):
    if market == "V1":
        return home > away
    if market == "V2":
        return away > home
    if market == "1X":
        return home >= away
    if market == "2X":
        return away >= home
    return None


def evaluate_prediction(ptype, home, away):
    """True (gagne), False (perdu) ou None (type de pronostic inconnu).

    Les libelles montres aux clients (voir ComboGenerator.prediction_types)
    font foi : "Plus de N buts" = strictement plus de N, "Moins de N buts" =
    strictement moins de N (avec une ligne entiere, un score pile egal a N ne
    gagne donc pas). "Total 1" / "Total 2" = nombre de buts de l'equipe 1
    (domicile) / de l'equipe 2 (exterieur).
    """
    if not isinstance(ptype, str) or not isinstance(home, int) or not isinstance(away, int):
        return None
    total = home + away

    if ptype in ("V1", "V2", "1X", "2X"):
        return _result_market(ptype, home, away)
    if ptype == "BTTS_OUI":
        return home > 0 and away > 0
    if ptype == "BTTS_NON":
        return not (home > 0 and away > 0)

    m = _TOTAL_RE.fullmatch(ptype)
    if m:
        line = float(m.group(1))
        return total > line if m.group(2) == "+" else total < line

    m = _TEAM_RE.fullmatch(ptype)
    if m:
        goals = home if m.group(1) == "1" else away
        return goals > float(m.group(2))

    m = _RESULT_AND_TOTAL_RE.fullmatch(ptype)
    if m:
        return _result_market(m.group(1), home, away) and total > float(m.group(2))

    m = _AT_LEAST_RE.fullmatch(ptype)
    if m:
        return max(home, away) > float(m.group(1))

    m = _RESULT_AND_TEAM1_RE.fullmatch(ptype)
    if m:
        return _result_market(m.group(1), home, away) and home > float(m.group(2))

    m = _RESULT_AND_TEAM2_RE.fullmatch(ptype)
    if m:
        return _result_market(m.group(1), home, away) and away > float(m.group(2))

    return None


def combo_status(outcomes):
    """'lost' des qu'un pronostic est perdu, 'won' si tous sont gagnes,
    sinon 'pending' (au moins un match pas encore joue / verifiable)."""
    if any(o is False for o in outcomes):
        return "lost"
    if outcomes and all(o is True for o in outcomes):
        return "won"
    return "pending"


# --------------------------------------------------------------------------
# Scores reels : lecture en base + rafraichissement cible
# --------------------------------------------------------------------------

def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_final_scores(db_path, match_ids):
    """{id_match: (buts_domicile, buts_exterieur)} pour les matchs termines."""
    ids = sorted({i for i in match_ids if i is not None})
    scores = {}
    if not ids or not os.path.exists(db_path):
        return scores
    conn = sqlite3.connect(db_path)
    try:
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                "SELECT id, home_score, away_score, status FROM matches "
                f"WHERE id IN ({marks}) AND home_score IS NOT NULL AND away_score IS NOT NULL",
                chunk,
            ).fetchall()
            for match_id, home, away, status in rows:
                if is_final_status(status):
                    scores[int(match_id)] = (int(home), int(away))
    except sqlite3.Error as e:
        print(f"Erreur lecture scores historique: {e}")
    finally:
        conn.close()
    return scores


# Etat en memoire : id_match -> instant (epoch) avant lequel on ne reinterroge
# pas TheSportsDB. Evite de refaire un appel par match a chaque ouverture de
# l'historique : un match pas encore joue n'est reverifie qu'apres son coup
# d'envoi + sa duree, un match en cours / sans score toutes les 30 minutes.
_next_check = {}
_CHECK_EVERY = 30 * 60
_MATCH_DURATION = 110 * 60
_MAX_LOOKUPS = 10
_LOOKUP_TIMEOUT = 6
_LOOKUP_BUDGET = 25


def _event_start_epoch(event):
    stamp = event.get("strTimestamp")
    try:
        if stamp:
            return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc).timestamp()
        day, hour = event.get("dateEvent"), (event.get("strTime") or "00:00:00")[:8]
        return datetime.strptime(f"{day} {hour}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def refresh_pending_results(pending, now=None):
    """Va chercher chez TheSportsDB (lookupevent) le score final des matchs de
    l'historique dont on n'a pas encore le resultat en base.

    pending : {id_match: nom_du_championnat}. Borne : au plus _MAX_LOOKUPS
    appels par ouverture de l'historique, dans un budget de temps global, et
    jamais deux fois le meme match avant son prochain moment utile. Renvoie
    True si au moins un score final a ete enregistre.
    """
    now = time.time() if now is None else now
    started = time.time()
    updated = False
    lookups = 0
    collector = None
    base = f"https://www.thesportsdb.com/api/v1/json/{config.SPORTSDB_API_KEY}/lookupevent.php"
    for match_id, league in pending.items():
        if lookups >= _MAX_LOOKUPS or time.time() - started > _LOOKUP_BUDGET:
            break
        if _next_check.get(match_id, 0) > now:
            continue
        lookups += 1
        # Fixe AVANT l'appel : meme en cas d'echec reseau on ne martele pas l'API.
        _next_check[match_id] = now + _CHECK_EVERY
        try:
            response = requests.get(base, params={"id": match_id}, timeout=_LOOKUP_TIMEOUT)
            events = response.json().get("events") or []
        except Exception as e:
            print(f"Erreur lookup match {match_id}: {e}")
            continue
        if not events:
            continue
        event = events[0]
        has_scores = (parse_score(event.get("intHomeScore")) is not None
                      and parse_score(event.get("intAwayScore")) is not None)
        if has_scores and is_final_status(event.get("strStatus")):
            if collector is None:
                collector = DataCollector()
            collector.save_match(event, league)
            updated = True
        else:
            start = _event_start_epoch(event)
            if start is not None and start + _MATCH_DURATION > now:
                _next_check[match_id] = max(_next_check[match_id], start + _MATCH_DURATION)
    if lookups:
        print(f"DEBUG historique: lookups={lookups} resultats_enregistres={updated}")
    return updated


# --------------------------------------------------------------------------
# Lecture de l'historique
# --------------------------------------------------------------------------

_FILE_RE = re.compile(r"combo_(\d{8})_(\d{6})\.json$")


def _timestamp_from_filename(path):
    """Date de generation (ISO 8601 UTC) deduite du nom combo_AAAAMMJJ_HHMMSS.json."""
    m = _FILE_RE.search(os.path.basename(path))
    if not m:
        return None
    try:
        local = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    # Le nom a ete ecrit avec datetime.now() (heure du serveur) : on le
    # convertit en UTC pour que le navigateur l'affiche a l'heure du client.
    return local.astimezone(timezone.utc).isoformat()


def _read_entries(results_dir, max_files, max_combos):
    """[(date_generation, combine)] du plus recent au plus ancien, sans doublon.

    Un meme combine (memes matchs, memes types de pronostic) regenere plus
    tard n'apporte rien : seule sa premiere apparition est gardee.
    """
    files = sorted(glob.glob(os.path.join(results_dir, "combo_*.json")))[-max_files:]
    seen = set()
    entries = []
    for path in files:  # du plus ancien au plus recent
        generated_at = _timestamp_from_filename(path)
        if not generated_at:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                combos = json.load(f)
        except (OSError, ValueError) as e:
            print(f"Historique: fichier ignore ({os.path.basename(path)}): {e}")
            continue
        if not isinstance(combos, list):
            continue
        for combo in combos:
            preds = combo.get("predictions") if isinstance(combo, dict) else None
            if not isinstance(preds, list) or not preds:
                continue
            signature = tuple(sorted((str(p.get("match_id")), str(p.get("type"))) for p in preds))
            if signature in seen:
                continue
            seen.add(signature)
            entries.append((generated_at, combo))
    entries.sort(key=lambda e: e[0], reverse=True)
    return entries[:max_combos]


def load_history(results_dir, db_path=None, max_combos=30, max_files=60, refresh=True):
    db_path = db_path or config.DB_PATH
    entries = _read_entries(results_dir, max_files, max_combos)

    ids = {_to_int(p.get("match_id")) for _, combo in entries for p in combo["predictions"]}
    scores = _read_final_scores(db_path, ids)

    if refresh:
        pending = {}
        for _, combo in entries:
            for p in combo["predictions"]:
                match_id = _to_int(p.get("match_id"))
                if match_id is not None and match_id not in scores and match_id not in pending:
                    pending[match_id] = p.get("league", "")
        if pending and refresh_pending_results(pending):
            scores = _read_final_scores(db_path, ids)

    history = []
    for generated_at, combo in entries:
        legs = []
        outcomes = []
        for p in combo["predictions"]:
            score = scores.get(_to_int(p.get("match_id")))
            outcome = evaluate_prediction(p.get("type"), score[0], score[1]) if score else None
            outcomes.append(outcome)
            legs.append({
                "home_team": p.get("home_team", ""),
                "away_team": p.get("away_team", ""),
                "league": p.get("league", ""),
                "type_name": p.get("type_name", ""),
                "estimated_odds": p.get("estimated_odds"),
                "confidence": p.get("confidence"),
                "score": f"{score[0]} - {score[1]}" if score else None,
                "outcome": "won" if outcome is True else "lost" if outcome is False else None,
            })
        history.append({
            "generated_at": generated_at,
            "total_odds": combo.get("total_odds"),
            "avg_confidence": combo.get("avg_confidence"),
            "score": combo.get("score"),
            "status": combo_status(outcomes),
            "predictions": legs,
        })
    print(f"DEBUG historique: combos={len(history)} "
          f"gagnes={sum(1 for h in history if h['status'] == 'won')} "
          f"perdus={sum(1 for h in history if h['status'] == 'lost')} "
          f"en_cours={sum(1 for h in history if h['status'] == 'pending')}")
    return history
