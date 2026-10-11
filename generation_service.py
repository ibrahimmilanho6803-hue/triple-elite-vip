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
- après un échec, une courte pause évite de relancer aussitôt toute la chaîne ;
- les combinés ne portent que sur les matchs du jour : un jour sans match (ou avec
  trop peu de matchs), la chaîne s'arrête avant tout appel à l'IA, le service garde
  ce constat pour la journée (matchday.json) et répond « no_match » à tout le monde,
  sans rien compter ni dans le plafond du jour ni dans les générations d'un client.
  Le constat peut aussi être fait sans génération (refresh_matchday : cinq appels
  gratuits à TheSportsDB, jamais d'IA) : c'est ce que fait la boucle quotidienne dès le
  début de la journée, pour que « Pas de championnat aujourd'hui » s'affiche sans attendre
  le premier clic d'un client.

L'état est écrit dans CACHE_DIR (fichiers JSON) pour rester cohérent même si le
serveur lance plusieurs processus.
"""
import json
import logging
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

import config
from pipeline import NO_MATCH_TEXTS, GenerationError, NoMatchToday, no_match_reason, run_pipeline

log = logging.getLogger(__name__)

STACK_LINES = 14                 # profondeur de la pile consignée quand une analyse est déclarée perdue
FAILURE_COOLDOWN_SECONDS = 120
MATCHDAY_CHECK_EVERY = 1800      # secondes entre deux vérifications des matchs du jour, tant que le constat est négatif
MATCHDAY_RETRY_AFTER = 300       # secondes avant de réessayer quand TheSportsDB n'a pas répondu

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


def _once(refund):
    """`refund` enveloppé pour ne rendre le clic qu'UNE fois : l'analyse qui échoue et le travail déclaré perdu pendant
    qu'elle tournait encore peuvent tous deux vouloir le rendre. None reste None."""
    if refund is None:
        return None
    lock, state = threading.Lock(), {"done": False}

    def give_back_once():
        with lock:
            if state["done"]:
                return
            state["done"] = True
        refund()
    return give_back_once


class GenerationService:
    def __init__(self, pipeline=run_pipeline, cache_dir=None, results_dir=None, clock=time.time, matchday_source=None):
        """matchday_source : fonction sans argument qui renvoie les matchs du jour (DataCollector.get_matchday) ; sert à
        refresh_matchday. Par défaut, le vrai collecteur (jamais créé avant d'en avoir besoin)."""
        self.pipeline = pipeline
        self.cache_dir = cache_dir or config.CACHE_DIR
        self.results_dir = results_dir or config.RESULTS_DIR
        self.clock = clock
        self.matchday_source = matchday_source or self._real_matchday
        self._lock = threading.Lock()
        self._matchday_lock = threading.Lock()
        self._lost_lock = threading.Lock()
        self._matchday_failed_at = None
        self._thread = None
        self._refund = None              # le clic à rendre si l'analyse EN COURS dans ce processus n'aboutit à rien
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
            tmp = self._path(name) + f".{os.getpid()}.{threading.get_ident()}.tmp"      # un fichier par fil : écritures simultanées
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

    def today(self):
        """Le jour (UTC, « AAAA-MM-JJ ») selon l'horloge du service : celui des matchs, du plafond et des quotas clients."""
        return self._today()

    def generations_today(self):
        usage = self._read("usage.json") or {}
        return int(usage.get("count", 0)) if usage.get("date") == self._today() else 0

    def _count_generation(self):
        self._write("usage.json", {"date": self._today(), "count": self.generations_today() + 1})

    def _uncount_generation(self):
        """Une analyse arrêtée avant tout appel à l'IA (pas de match aujourd'hui) ne compte pas dans le plafond du jour."""
        count = self.generations_today()
        if count > 0:
            self._write("usage.json", {"date": self._today(), "count": count - 1})

    # ------------------------------------------------------------------
    # Les matchs du jour : constat gardé pour la journée
    # ------------------------------------------------------------------

    def _matchday(self):
        """Le constat enregistré pour AUJOURD'HUI ({"day", "total", "upcoming", "reason", "checked_ts"}), ou None."""
        record = self._read("matchday.json")
        if isinstance(record, dict) and record.get("day") == self._today():
            return record
        return None

    def _note_matchday(self, total, upcoming):
        """Enregistre ce que la dernière vérification a vu : `total` matchs aujourd'hui (commencés ou non), `upcoming` encore
        à venir. Le total ne baisse jamais dans la journée : TheSportsDB ne liste plus les matchs déjà commencés, et c'est ce
        total qui distingue « pas de championnat aujourd'hui » de « les matchs du jour ont déjà commencé »."""
        previous = self._matchday() or {}
        total = max(int(previous.get("total") or 0), int(total or 0), int(upcoming or 0))
        self._write("matchday.json", {"day": self._today(), "total": total, "upcoming": int(upcoming or 0),
                                      "reason": no_match_reason(total, int(upcoming or 0)), "checked_ts": self.clock()})

    def no_match_today(self):
        """{"reason", "title", "message"} quand la vérification d'aujourd'hui a conclu qu'aucun combiné n'est possible
        (voir pipeline.NO_MATCH_TEXTS), sinon None. Lecture seule : aucun appel réseau, aucun travail lancé."""
        record = self._matchday()
        reason = record.get("reason") if record else None
        if reason not in NO_MATCH_TEXTS:
            return None
        title, message = NO_MATCH_TEXTS[reason]
        return {"reason": reason, "title": title, "message": message}

    @staticmethod
    def _real_matchday():
        from data_collector import DataCollector
        return DataCollector().get_matchday()

    def refresh_matchday(self):
        """Vérifie les matchs d'aujourd'hui et garde le constat (cinq appels gratuits à TheSportsDB, jamais d'appel à l'IA).

        Appelée par la boucle quotidienne (daily_generation.py), jamais par une requête de visiteur. Sans effet quand le
        constat du jour est déjà « de quoi composer des combinés », et tant que le dernier constat négatif a moins de
        MATCHDAY_CHECK_EVERY secondes (un calendrier peut se compléter dans la journée). Ne conclut jamais « pas de match »
        sur une réponse incomplète : si un championnat n'a pas répondu, rien n'est enregistré et on réessaie dans
        MATCHDAY_RETRY_AFTER secondes. Renvoie True quand un constat vient d'être enregistré. Ne lève jamais."""
        with self._matchday_lock:
            now = self.clock()
            record = self._matchday()
            if record is not None:
                if record.get("reason") is None:
                    return False
                try:
                    if now - float(record.get("checked_ts") or 0) < MATCHDAY_CHECK_EVERY:
                        return False
                except (TypeError, ValueError):
                    pass
            if self._matchday_failed_at is not None and now - self._matchday_failed_at < MATCHDAY_RETRY_AFTER:
                return False
            try:
                matchday = self.matchday_source()
                upcoming = len(matchday["matches"])
                if no_match_reason(matchday.get("total", 0), upcoming) is not None and not matchday.get("complete", True):
                    raise RuntimeError("réponse incomplète de TheSportsDB")
            except Exception as e:
                self._matchday_failed_at = now
                log.warning("matchs du jour : vérification impossible (%s)", e)
                return False
            self._matchday_failed_at = None
            self._note_matchday(matchday.get("total", 0), upcoming)
            log.info("matchs du jour (%s) : %d à venir sur %d", self._today(), upcoming, matchday.get("total", 0))
            return True

    # ------------------------------------------------------------------
    # Travail en arrière-plan
    # ------------------------------------------------------------------

    def _lost_cause(self, job):
        """Pourquoi ce travail « en cours » est perdu (texte pour les journaux), ou None s'il suit son cours : son processus
        a disparu (redémarrage du serveur...), son fil s'est arrêté, ou il dure trop longtemps."""
        own = job.get("pid") == os.getpid()
        if own:
            alive = self._thread is not None and self._thread.is_alive()
        else:
            alive = _pid_alive(job.get("pid"))
        if not alive:
            return "son fil s'est arrêté" if own else "le processus qui la menait a disparu"
        if self.clock() - float(job.get("started_ts", 0)) >= config.JOB_MAX_SECONDS:
            return "elle dure trop longtemps"
        return None

    def _job(self):
        job = self._read("job.json")
        if not job or job.get("state") != "running" or self._lost_cause(job) is None:
            return job
        with self._lost_lock:
            job = self._read("job.json")              # un autre fil vient peut-être de le constater (voire d'en lancer un autre)
            cause = self._lost_cause(job) if job and job.get("state") == "running" else None
            if cause is None:
                return job
            return self._declare_lost(job, cause)

    def _declare_lost(self, job, cause):
        """Le travail « en cours » est perdu (voir _lost_cause). Dans cet ordre : on le dit dans les journaux (où il en était et,
        s'il tourne encore ici, ce que fait son fil à cet instant : c'est ce qui dira quel appel ne revient pas), on rend au
        client le clic qui l'avait lancé, et SEULEMENT ENSUITE le travail passe en erreur (le navigateur voit alors le message :
        son clic est déjà rendu). Un clic ne se rend que pour un travail lancé par CE processus."""
        own = job.get("pid") == os.getpid()
        stack = ""
        if own and self._thread is not None and self._thread.is_alive():
            frame = sys._current_frames().get(self._thread.ident)
            if frame is not None:
                stack = " Son fil en est là :\n" + "".join(traceback.format_stack(frame, limit=STACK_LINES)).rstrip()
        age = self.clock() - float(job.get("started_ts", 0))
        log.error("génération perdue (%s) : étape « %s » (%s %%), lancée il y a %d s.%s",
                  cause, job.get("message") or job.get("step") or "?", job.get("percent", 0), age, stack)
        if own:
            self._give_back(self._refund)
        lost = {"state": "error", "message": MSG_GENERIC_ERROR, "finished_ts": self.clock()}
        self._write("job.json", lost)
        return lost

    def _set_progress(self, step, percent, message):
        self._progress = {"step": step, "percent": int(percent), "message": message}
        log.info("génération : %s (%d %%)", message, self._progress["percent"])
        job = self._read("job.json")
        if job and job.get("state") == "running" and job.get("pid") == os.getpid():
            job.update(self._progress)
            self._write("job.json", job)

    @staticmethod
    def _give_back(refund):
        """Rend au client le clic qui avait lancé cette analyse, quand elle n'a donné aucun combiné. Ne lève jamais."""
        if refund is None:
            return
        try:
            refund()
        except Exception:
            log.exception("génération du client non rendue")

    def _finish_job(self, state):
        """Écrit l'état final du travail, sauf si un travail plus récent a pris sa place (celui-ci avait été déclaré perdu
        puis un autre lancé : il arrive trop tard et ne doit pas écraser l'état du nouveau)."""
        if self._thread is threading.current_thread():
            self._write("job.json", state)

    def _run(self, refund=None):
        # Ordre voulu pour chaque issue : constat du jour, clics rendus, et SEULEMENT ENSUITE l'état final du travail (dès
        # qu'il change, le navigateur du client peut relancer : son clic rendu doit déjà l'être).
        try:
            result = self.pipeline(progress=self._set_progress)
            combos = result["combos"]
            now = self.clock()
            self._save_history(combos)
            meta = result.get("meta", {})
            self._write("last_generation.json", {
                "generated_ts": now, "generated_at": _iso(now),
                "combos": combos, "meta": meta,
            })
            if meta.get("matches_total") is not None:
                self._note_matchday(meta.get("day_total"), meta["matches_total"])
            self._finish_job({"state": "done", "finished_ts": now})
        except NoMatchToday as e:
            log.log(e.level, "génération sans objet : %s (%d match(s) aujourd'hui, %d à venir)",
                    e.user_message, e.total, e.upcoming)
            self._note_matchday(e.total, e.upcoming)
            self._uncount_generation()
            self._give_back(refund)
            self._finish_job({"state": "no_match", "finished_ts": self.clock()})
        except GenerationError as e:
            log.log(e.level, "génération sans résultat : %s", e.user_message)
            self._give_back(refund)
            self._finish_job({"state": "error", "message": e.user_message, "finished_ts": self.clock()})
        except Exception:
            log.exception("génération en échec")
            self._give_back(refund)
            self._finish_job({"state": "error", "message": MSG_GENERIC_ERROR, "finished_ts": self.clock()})
        finally:
            if self._refund is refund:                  # ce travail est fini : plus rien à rendre pour lui
                self._refund = None

    def _start(self, refund=None):
        refund = _once(refund)
        self._refund = refund
        self._progress = {"step": "demarrage", "percent": 1, "message": "Démarrage de l'analyse…"}
        self._write("job.json", {"state": "running", "pid": os.getpid(), "started_ts": self.clock(),
                                 **self._progress})
        self._count_generation()
        self._thread = threading.Thread(target=self._run, args=(refund,), name="generation", daemon=True)
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
        verdict = self.no_match_today()
        if verdict:
            return {"state": "no_match", **verdict}
        return {"state": "idle"}

    def request_generation(self, refund=None):
        """Clic du client : sert le cache s'il est frais, sinon démarre (ou rejoint) le travail.

        Quand CE clic démarre une analyse, la réponse porte "started": True. `refund` : fonction appelée (sans argument) si
        cette analyse n'aboutit à aucun combiné (panne, pas de match aujourd'hui) : elle rend au client le clic qui l'a
        lancée. Les clics qui ne démarrent rien (combinés déjà prêts, travail déjà en cours, pause, pas de match) n'ont rien
        à rendre : l'appelant ne les compte pas."""
        with self._lock:
            current = self.status()
            if current["state"] != "idle":
                return current
            if self.generations_today() >= config.MAX_GENERATIONS_PAR_JOUR:
                entry, _fresh = self._cached()
                if entry:
                    return self._result("done", entry, cached=True, stale=True, notice=MSG_DAILY_LIMIT)
                return {"state": "error", "error": MSG_DAILY_LIMIT_EMPTY}
            self._start(refund)
            return {"state": "running", "progress": dict(self._progress), "started": True}
