"""Ce que toutes les générations partagent, clients et génération automatique confondus.

Chaque client a SES combinés, mais il ne faut pas que chaque clic refasse tout : relire les matchs du jour (douze appels à
TheSportsDB), recollecter les derniers résultats (vingt-quatre appels) et surtout refaire payer l'IA pour des matchs qu'elle a
déjà analysés. Trois choses sont donc gardées en commun pour un temps limité (config.py) :

  - les matchs du jour (quelques minutes) : les générations lancées à peu d'écart voient le même programme ;
  - la collecte des derniers résultats, qui alimente les statistiques des équipes (une heure) ;
  - l'estimation de l'IA pour chaque match (plusieurs heures, AnalysisCache) : un match du jour est analysé UNE fois, pas une
    fois par client. C'est ce qui fait que la dépense d'IA suit le nombre de matchs du jour et non le nombre de clients.

Une seule génération à la fois passe par « collecte + appel à l'IA » (analysis_lock) : si deux clients ont besoin du même match
au même moment, le second attend le premier puis trouve l'analyse déjà faite, au lieu de la payer deux fois.

Rien de ce qui est gardé ici n'est propre à un client : ce sont des données de matchs, jamais des combinés.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import config

log = logging.getLogger(__name__)

ANALYSES_FILE = "analyses.json"


def _norm(name):
    return " ".join((name or "").lower().split())


class AnalysisCache:
    """Estimations de l'IA gardées par match : {id du match: sa réponse}. Un fichier JSON du dossier de cache (il survit aux
    redémarrages), avec une copie en mémoire si le disque est inutilisable. Une entrée est refusée si elle est trop ancienne,
    datée du futur, ou si ses équipes ne sont pas celles du match demandé."""

    def __init__(self, directory, ttl_seconds, clock=time.time):
        self.directory = directory
        self.ttl = ttl_seconds
        self.clock = clock
        self._lock = threading.Lock()
        self._mem = {}

    def _path(self):
        return os.path.join(self.directory, ANALYSES_FILE)

    def _load(self):
        try:
            with open(self._path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("entries") if isinstance(data, dict) else None
            if isinstance(entries, dict):
                return entries
        except (OSError, ValueError):
            pass
        return dict(self._mem)

    def _save(self, entries):
        self._mem = dict(entries)
        try:
            os.makedirs(self.directory, exist_ok=True)
            tmp = self._path() + f".{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"entries": entries}, f, ensure_ascii=False)
            os.replace(tmp, self._path())
        except OSError as e:
            log.error("écriture de %s impossible : %s", ANALYSES_FILE, e)

    def _recent(self, entry, now):
        """L'entrée a un âge lisible, ni périmé ni daté du futur."""
        try:
            age = now - float(entry["ts"])
        except (KeyError, TypeError, ValueError):
            return False
        return -60 <= age < self.ttl

    def _usable(self, entry, match, now):
        if not isinstance(entry, dict) or not isinstance(entry.get("ia"), dict) or not self._recent(entry, now):
            return False
        return (_norm(entry.get("home")) == _norm(match.get("home_team"))
                and _norm(entry.get("away")) == _norm(match.get("away_team")))

    def lookup(self, matches):
        """{id du match: estimation de l'IA} pour ceux des `matches` dont l'estimation est gardée et encore valable."""
        with self._lock:
            entries = self._load()
        now = self.clock()
        found = {}
        for match in matches:
            entry = entries.get(str(match["id"]))
            if self._usable(entry, match, now):
                found[match["id"]] = entry["ia"]
        return found

    def store(self, matches, estimates):
        """Garde l'estimation de chaque match de `matches` présent dans `estimates` ({id: réponse de l'IA}) ; oublie au passage
        les entrées expirées."""
        now = self.clock()
        with self._lock:
            entries = {key: entry for key, entry in self._load().items() if isinstance(entry, dict) and self._recent(entry, now)}
            for match in matches:
                ia = estimates.get(match["id"])
                if isinstance(ia, dict):
                    entries[str(match["id"])] = {"ts": now, "home": match.get("home_team"), "away": match.get("away_team"),
                                                 "ia": ia}
            self._save(entries)


def _started(match, now):
    """Le match a commencé (son heure de coup d'envoi est passée). Une heure inconnue : pas commencé."""
    kickoff = match.get("kickoff")
    if not kickoff:
        return False
    try:
        return datetime.fromisoformat(kickoff).timestamp() <= now
    except ValueError:
        return False


class SharedData:
    """Matchs du jour, collecte et estimations de l'IA communs à toutes les générations (voir le début du module).

    `directory` : dossier de cache (analyses.json). `clock` : horloge (secondes, comme time.time)."""

    def __init__(self, directory, clock=time.time):
        self.clock = clock
        self.analyses = AnalysisCache(directory, config.ANALYSIS_CACHE_MINUTES * 60, clock=lambda: self.clock())
        self.analysis_lock = threading.Lock()          # une seule collecte + appel à l'IA à la fois
        self._matchday_lock = threading.Lock()
        self._matchday = None                          # (instant, réponse complète de get_matchday)
        self._collected_at = None
        self._ai_failed_at = None

    # ------------------------------------------------------------------ matchs du jour

    def matchday(self, collector):
        """Les matchs du jour, comme DataCollector.get_matchday, mais relus chez TheSportsDB au plus toutes les
        MATCHDAY_CACHE_SECONDS. Les matchs qui ont commencé depuis sont retirés de la liste ; le total du jour, lui, ne change
        pas. Une réponse incomplète (un championnat n'a pas répondu) n'est jamais gardée : la génération suivante réessaie."""
        with self._matchday_lock:
            now = self.clock()
            today = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
            snapshot = self._matchday
            if snapshot is None or now - snapshot[0] >= config.MATCHDAY_CACHE_SECONDS or snapshot[1].get("day") != today:
                data = collector.get_matchday()
                self._matchday = (now, data) if data.get("complete", True) else None
            else:
                data = snapshot[1]
            return {**data, "matches": [dict(match) for match in data["matches"] if not _started(match, now)]}

    # ------------------------------------------------------------------ collecte des derniers résultats

    def collect(self, collector):
        """Collecte les derniers résultats des championnats (statistiques des équipes), sauf si c'est déjà fait depuis moins de
        COLLECT_CACHE_MINUTES. À appeler sous analysis_lock. Renvoie True quand une collecte a eu lieu."""
        now = self.clock()
        if self._collected_at is not None and now - self._collected_at < config.COLLECT_CACHE_MINUTES * 60:
            return False
        summary = collector.collect_all_data()
        # Aucune ligne lue nulle part : TheSportsDB n'a pas répondu, ce n'est pas une collecte réussie, on réessaiera.
        if not isinstance(summary, dict) or any(summary.values()):
            self._collected_at = self.clock()
        return True

    # ------------------------------------------------------------------ pannes de l'IA

    def note_ai_failure(self):
        self._ai_failed_at = self.clock()

    def note_ai_success(self):
        self._ai_failed_at = None

    def ai_cooling_down(self):
        """L'IA n'a pas répondu il y a moins de AI_RETRY_AFTER_SECONDS : une autre génération n'insiste pas, au lieu d'attendre
        à son tour la fin d'un appel qui ne revient pas."""
        failed = self._ai_failed_at
        return failed is not None and self.clock() - failed < config.AI_RETRY_AFTER_SECONDS
