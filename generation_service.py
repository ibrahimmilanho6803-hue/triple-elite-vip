"""Génération des combinés en arrière-plan, partagée entre tous les clients.

Pourquoi : la génération (collecte + analyse IA) dure plusieurs secondes à
plusieurs dizaines de secondes. Faite dans la requête HTTP, elle bloquait le
serveur (et provoquait des « Erreur de connexion » quand le délai était dépassé).
Ici le clic du client DÉMARRE un travail en arrière-plan (un seul à la fois, pour
tous les clients) et le navigateur interroge ensuite son avancement.

- cache : les combinés générés servent tous les clients pendant CACHE_MINUTES (et
  tant qu'aucun de leurs matchs n'a commencé) ;
- un seul travail à la fois : des clics répétés ne multiplient pas les appels payants ;
- plafond quotidien (MAX_GENERATIONS_PAR_JOUR) : au-delà, les derniers combinés
  restent servis ;
- après un échec, une courte pause évite de relancer aussitôt toute la chaîne.

L'état est écrit dans CACHE_DIR (fichiers JSON) pour rester cohérent même si le
serveur lance plusieurs processus.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import config
from pipeline import GenerationError, run_pipeline

log = logging.getLogger(__name__)

FAILURE_COOLDOWN_SECONDS = 120

MSG_DAILY_LIMIT = ("La limite d'analyses du jour est atteinte. Voici les derniers combinés disponibles ; "
                   "de nouveaux seront générés demain.")
MSG_DAILY_LIMIT_EMPTY = ("La limite d'analyses du jour est atteinte et aucun combiné récent n'est disponible. "
                         "Réessaie demain.")
MSG_GENERIC_ERROR = "Une erreur est survenue pendant la génération. Merci de réessayer dans quelques minutes."


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


class GenerationService:
    def __init__(self, pipeline=run_pipeline, cache_dir=None, results_dir=None, clock=time.time):
        self.pipeline = pipeline
        self.cache_dir = cache_dir or config.CACHE_DIR
        self.results_dir = results_dir or config.RESULTS_DIR
        self.clock = clock
        self._lock = threading.Lock()
        self._thread = None
        self._progress = {"step": "", "percent": 0, "message": ""}
        # Copie en mémoire de ce que ce processus a écrit : sert de secours si le
        # disque est plein ou inaccessible (le service continue alors de fonctionner).
        self._mem = {}

    # ------------------------------------------------------------------
    # Fichiers d'état
    # ------------------------------------------------------------------

    def _path(self, name):
        return os.path.join(self.cache_dir, name)

    def _read(self, name):
        try:
            with open(self._path(name), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return self._mem.get(name)

    def _write(self, name, data):
        self._mem[name] = data
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = self._path(name) + f".{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=str)
            os.replace(tmp, self._path(name))
        except OSError as e:
            log.error("écriture de %s impossible : %s", name, e)

    # ------------------------------------------------------------------
    # Cache des combinés
    # ------------------------------------------------------------------

    def _cached(self):
        """(entrée du cache, est_frais). Frais = assez récent ET aucun match commencé."""
        entry = self._read("last_generation.json")
        if not entry or not isinstance(entry.get("combos"), list) or not entry["combos"]:
            return None, False
        now = self.clock()
        try:
            age = now - float(entry["generated_ts"])
        except (KeyError, TypeError, ValueError):
            return entry, False
        fresh = age < config.CACHE_MINUTES * 60
        if fresh:
            for combo in entry["combos"]:
                for leg in combo.get("predictions", []):
                    kickoff = leg.get("kickoff")
                    if not kickoff:
                        continue
                    try:
                        if datetime.fromisoformat(kickoff).timestamp() <= now:
                            fresh = False
                    except ValueError:
                        continue
        return entry, fresh

    def latest(self):
        """Dernière génération réussie, même ancienne : {"generated_ts", "generated_at", "combos", "meta"}, ou None.
        Ne démarre jamais rien (lecture seule) : c'est ce qu'utilisent les pages publiques."""
        entry, _fresh = self._cached()
        return entry

    def _save_history(self, combos):
        try:
            os.makedirs(self.results_dir, exist_ok=True)
            stamp = datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
            with open(os.path.join(self.results_dir, f"combo_{stamp}.json"), "w", encoding="utf-8") as f:
                json.dump(combos, f, indent=2, ensure_ascii=False, default=str)
        except OSError as e:
            log.error("sauvegarde de l'historique impossible : %s", e)

    # ------------------------------------------------------------------
    # Quota quotidien
    # ------------------------------------------------------------------

    def _today(self):
        return datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime("%Y-%m-%d")

    def generations_today(self):
        usage = self._read("usage.json") or {}
        return int(usage.get("count", 0)) if usage.get("date") == self._today() else 0

    def _count_generation(self):
        self._write("usage.json", {"date": self._today(), "count": self.generations_today() + 1})

    # ------------------------------------------------------------------
    # Travail en arrière-plan
    # ------------------------------------------------------------------

    def _job(self):
        job = self._read("job.json")
        if not job or job.get("state") != "running":
            return job
        # Un travail « en cours » dont le processus a disparu (redémarrage du
        # serveur...) ou qui dure trop longtemps est considéré comme perdu.
        if job.get("pid") == os.getpid():
            alive = self._thread is not None and self._thread.is_alive()
        else:
            alive = _pid_alive(job.get("pid"))
        if alive and self.clock() - float(job.get("started_ts", 0)) < config.JOB_MAX_SECONDS:
            return job
        lost = {"state": "error", "message": MSG_GENERIC_ERROR, "finished_ts": self.clock()}
        self._write("job.json", lost)
        return lost

    def _set_progress(self, step, percent, message):
        self._progress = {"step": step, "percent": int(percent), "message": message}
        job = self._read("job.json")
        if job and job.get("state") == "running" and job.get("pid") == os.getpid():
            job.update(self._progress)
            self._write("job.json", job)

    def _run(self):
        try:
            result = self.pipeline(progress=self._set_progress)
            combos = result["combos"]
            now = self.clock()
            self._save_history(combos)
            self._write("last_generation.json", {
                "generated_ts": now, "generated_at": _iso(now),
                "combos": combos, "meta": result.get("meta", {}),
            })
            self._write("job.json", {"state": "done", "finished_ts": now})
        except GenerationError as e:
            log.log(e.level, "génération sans résultat : %s", e.user_message)
            self._write("job.json", {"state": "error", "message": e.user_message,
                                     "finished_ts": self.clock()})
        except Exception:
            log.exception("génération en échec")
            self._write("job.json", {"state": "error", "message": MSG_GENERIC_ERROR,
                                     "finished_ts": self.clock()})

    def _start(self):
        self._progress = {"step": "demarrage", "percent": 1, "message": "Démarrage de l'analyse…"}
        self._write("job.json", {"state": "running", "pid": os.getpid(), "started_ts": self.clock(),
                                 **self._progress})
        self._count_generation()
        self._thread = threading.Thread(target=self._run, name="generation", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Réponses pour l'API
    # ------------------------------------------------------------------

    def _result(self, state, entry=None, **extra):
        data = {"state": state}
        if entry:
            data.update({"combos": entry["combos"], "generated_at": entry.get("generated_at"),
                         "meta": entry.get("meta", {})})
        data.update(extra)
        return data

    def status(self):
        """État courant, sans jamais démarrer de travail."""
        entry, fresh = self._cached()
        job = self._job()
        if job and job.get("state") == "running":
            return {"state": "running", "progress": {
                "step": job.get("step", ""), "percent": job.get("percent", 0), "message": job.get("message", "")}}
        if fresh:
            return self._result("done", entry, cached=True)
        if job and job.get("state") == "error" \
                and self.clock() - float(job.get("finished_ts", 0)) < FAILURE_COOLDOWN_SECONDS:
            return {"state": "error", "error": job.get("message", MSG_GENERIC_ERROR)}
        return {"state": "idle"}

    def request_generation(self):
        """Clic du client : sert le cache s'il est frais, sinon démarre (ou rejoint) le travail."""
        with self._lock:
            current = self.status()
            if current["state"] != "idle":
                return current
            if self.generations_today() >= config.MAX_GENERATIONS_PAR_JOUR:
                entry, _fresh = self._cached()
                if entry:
                    return self._result("done", entry, cached=True, stale=True, notice=MSG_DAILY_LIMIT)
                return {"state": "error", "error": MSG_DAILY_LIMIT_EMPTY}
            self._start()
            return {"state": "running", "progress": dict(self._progress)}
