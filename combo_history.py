"""Historique des combinés passés.

Lit les combinés sauvegardés à chaque génération (results/combo_*.json), les
rapproche des scores réels connus en base (table 'matches') et donne un verdict
gagné/perdu par pronostic puis par combiné, plus un bilan global. Aucune dépendance
à Flask : dashboard.py n'a qu'à renvoyer le résultat de load_history() en JSON.
"""
import glob
import json
import logging
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

import config
from data_collector import CALLED_OFF_STATUSES, DataCollector, event_start, is_final_status, parse_score
from markets import combo_status, describe_prediction, evaluate_prediction  # noqa: F401 (réexportés)

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Scores réels : lecture en base + rafraîchissement ciblé
# --------------------------------------------------------------------------


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_results(db_path, match_ids):
    """({id_match: (buts_domicile, buts_extérieur)} des matchs terminés,
    {id_match} des matchs reportés ou annulés)."""
    ids = sorted({i for i in match_ids if i is not None})
    scores, called_off = {}, set()
    if not ids or not os.path.exists(db_path):
        return scores, called_off
    conn = sqlite3.connect(db_path)
    try:
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT id, home_score, away_score, status FROM matches WHERE id IN ({marks})", chunk).fetchall()
            for match_id, home, away, status in rows:
                if home is not None and away is not None and is_final_status(status):
                    scores[int(match_id)] = (int(home), int(away))
                elif (status or "").strip().upper() in CALLED_OFF_STATUSES:
                    called_off.add(int(match_id))
    except sqlite3.Error as e:
        log.error("lecture des scores de l'historique impossible : %s", e)
    finally:
        conn.close()
    return scores, called_off


# État en mémoire : id_match -> instant (epoch) avant lequel on ne réinterroge pas
# TheSportsDB. Évite un appel par match à chaque ouverture de l'historique : un
# match pas encore joué n'est revérifié qu'après son coup d'envoi + sa durée, un
# match en cours / sans score toutes les 30 minutes.
_next_check = {}
_CHECK_EVERY = 30 * 60
_CALLED_OFF_EVERY = 12 * 3600       # un match reporté peut être rejoué plus tard : on revérifie, mais rarement
_MATCH_DURATION = 110 * 60
_MAX_LOOKUPS = 12
_LOOKUP_TIMEOUT = 6


def _lookup_event(match_id):
    base = f"https://www.thesportsdb.com/api/v1/json/{config.SPORTSDB_API_KEY}/lookupevent.php"
    try:
        response = requests.get(base, params={"id": match_id}, timeout=_LOOKUP_TIMEOUT)
        events = response.json().get("events") or []
        return events[0] if events else None
    except Exception as e:
        log.warning("lookup du match %s impossible : %s", match_id, e)
        return None


def refresh_pending_results(pending, now=None, called_off=()):
    """Va chercher chez TheSportsDB (lookupevent) le score final des matchs de
    l'historique dont on n'a pas encore le résultat en base.

    pending : {id_match: nom_du_championnat}. `called_off` : ceux déjà connus comme
    reportés/annulés (revérifiés rarement). Borné : au plus _MAX_LOOKUPS appels (en
    parallèle) par ouverture de l'historique, et jamais deux fois le même match avant
    son prochain moment utile. Renvoie True si au moins un résultat (score final,
    report, annulation) a été enregistré.
    """
    now = time.time() if now is None else now
    todo = []
    for match_id in pending:
        if len(todo) >= _MAX_LOOKUPS:
            break
        if _next_check.get(match_id, 0) > now:
            continue
        # Fixé AVANT l'appel : même en cas d'échec réseau on ne martèle pas l'API.
        _next_check[match_id] = now + (_CALLED_OFF_EVERY if match_id in called_off else _CHECK_EVERY)
        todo.append(match_id)
    if not todo:
        return False
    with ThreadPoolExecutor(max_workers=min(8, len(todo)), thread_name_prefix="lookup") as pool:
        events = list(pool.map(_lookup_event, todo))
    updated, collector = False, None
    for match_id, event in zip(todo, events):
        if not event:
            continue
        status = (event.get("strStatus") or "").strip().upper()
        has_scores = (parse_score(event.get("intHomeScore")) is not None
                      and parse_score(event.get("intAwayScore")) is not None)
        if (has_scores and is_final_status(status)) or (status in CALLED_OFF_STATUSES and match_id not in called_off):
            if collector is None:
                collector = DataCollector()
            collector.save_match(event, pending[match_id])
            updated = True
        elif status in CALLED_OFF_STATUSES:
            continue                                   # toujours reporté : rien à enregistrer
        else:
            start = event_start(event)
            if start is not None and start.timestamp() + _MATCH_DURATION > now:
                _next_check[match_id] = max(_next_check[match_id], start.timestamp() + _MATCH_DURATION)
    log.info("historique : %d consultation(s), résultats enregistrés : %s", len(todo), updated)
    return updated


# --------------------------------------------------------------------------
# Lecture de l'historique
# --------------------------------------------------------------------------

_FILE_RE = re.compile(r"combo_(\d{8})_(\d{6})\.json$")


def _timestamp_from_filename(path):
    """Date de génération (ISO 8601, UTC) déduite du nom combo_AAAAMMJJ_HHMMSS.json
    (écrit en UTC ; les serveurs Render sont en UTC)."""
    m = _FILE_RE.search(os.path.basename(path))
    if not m:
        return None
    try:
        moment = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc).isoformat()


def _read_entries(results_dir, max_files):
    """[(date_génération, combiné)] du plus récent au plus ancien, sans doublon.

    Un même combiné (mêmes matchs, mêmes pronostics) régénéré plus tard n'apporte
    rien : seule sa première apparition est gardée.
    """
    files = sorted(glob.glob(os.path.join(results_dir, "combo_*.json")))[-max_files:]
    seen, entries = set(), []
    for path in files:  # du plus ancien au plus récent
        generated_at = _timestamp_from_filename(path)
        if not generated_at:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                combos = json.load(f)
        except (OSError, ValueError) as e:
            log.warning("historique : fichier ignoré (%s) : %s", os.path.basename(path), e)
            continue
        if not isinstance(combos, list):
            continue
        for combo in combos:
            preds = combo.get("predictions") if isinstance(combo, dict) else None
            if not isinstance(preds, list) or not preds or not all(isinstance(p, dict) for p in preds):
                continue
            signature = tuple(sorted((str(p.get("match_id")), str(p.get("type"))) for p in preds))
            if signature in seen:
                continue
            seen.add(signature)
            entries.append((generated_at, combo))
    entries.sort(key=lambda e: e[0], reverse=True)
    return entries


def _status(outcomes, voids):
    """won / lost / pending / void pour un combiné (void = au moins un match
    reporté ou annulé, sans pari perdu ni résultat encore attendu)."""
    if any(o is False for o in outcomes):
        return "lost"
    if any(voids):
        waiting = any(o is None and not v for o, v in zip(outcomes, voids))
        return "pending" if waiting else "void"
    return combo_status(outcomes)


def summarize(history):
    """Bilan global : combinés gagnés/perdus, et réussite des paris individuels."""
    counts = {"won": 0, "lost": 0, "pending": 0, "void": 0}
    legs_won = legs_lost = 0
    for combo in history:
        counts[combo["status"]] = counts.get(combo["status"], 0) + 1
        for leg in combo["predictions"]:
            if leg["outcome"] == "won":
                legs_won += 1
            elif leg["outcome"] == "lost":
                legs_lost += 1
    settled = counts["won"] + counts["lost"]
    legs_settled = legs_won + legs_lost
    return {
        **counts,
        "combos_settled": settled,
        "combo_win_rate": round(100 * counts["won"] / settled) if settled else None,
        "legs_won": legs_won, "legs_lost": legs_lost,
        "leg_win_rate": round(100 * legs_won / legs_settled) if legs_settled else None,
    }


def load_history(results_dir, db_path=None, max_combos=30, max_files=400, refresh=True):
    """{"combos": [...les plus récents...], "summary": {...bilan sur tout l'historique...}}."""
    db_path = db_path or config.DB_PATH
    entries = _read_entries(results_dir, max_files)

    ids = {_to_int(p.get("match_id")) for _, combo in entries for p in combo["predictions"]}
    scores, called_off = _read_results(db_path, ids)

    if refresh:
        pending = {}
        for _, combo in entries:                       # du plus récent au plus ancien
            for p in combo["predictions"]:
                match_id = _to_int(p.get("match_id"))
                if match_id is not None and match_id not in scores and match_id not in pending:
                    pending[match_id] = p.get("league", "")
        if pending and refresh_pending_results(pending, called_off=called_off):
            scores, called_off = _read_results(db_path, ids)

    history = []
    for generated_at, combo in entries:
        legs, outcomes, voids = [], [], []
        for p in combo["predictions"]:
            match_id = _to_int(p.get("match_id"))
            score = scores.get(match_id)
            void = score is None and match_id in called_off
            outcome = evaluate_prediction(p.get("type"), score[0], score[1]) if score else None
            outcomes.append(outcome)
            voids.append(void)
            home, away = p.get("home_team", ""), p.get("away_team", "")
            legs.append({
                "home_team": home,
                "away_team": away,
                "league": p.get("league", ""),
                "kickoff": p.get("kickoff"),
                # Libellé régénéré (noms d'équipes, orthographe) : les anciens
                # combinés enregistrés profitent aussi du libellé à jour.
                "type_name": describe_prediction(p.get("type"), home, away) if p.get("type") else p.get("type_name", ""),
                "estimated_odds": p.get("estimated_odds"),
                "odds_source": p.get("odds_source"),
                "confidence": p.get("confidence"),
                "score": f"{score[0]} - {score[1]}" if score else None,
                "outcome": "won" if outcome is True else "lost" if outcome is False else ("void" if void else None),
            })
        history.append({
            "generated_at": generated_at,
            "total_odds": combo.get("total_odds"),
            "avg_confidence": combo.get("avg_confidence"),
            "success_probability": combo.get("success_probability"),
            "status": _status(outcomes, voids),
            "predictions": legs,
        })
    return {"combos": history[:max_combos], "summary": summarize(history)}
