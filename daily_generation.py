"""Génération automatique quotidienne (facultative).

Sans elle, une génération n'existe que si un abonné clique sur « Générer » : les jours calmes, la page « Combiné gratuit »
reste vide et l'historique public n'avance pas. Avec elle, une génération part chaque jour à l'heure choisie (variable
AUTO_GENERATE_HOUR, heure UTC), si personne n'en a déjà fait une ce jour-là.

DÉSACTIVÉE par défaut : une génération est un appel payant à l'IA (de l'ordre de 0,2 $). Elle passe par
GenerationService.request_generation, donc par les mêmes garde-fous que le clic d'un abonné : un seul travail à la fois,
combinés déjà frais réutilisés, plafond quotidien (MAX_GENERATIONS_PAR_JOUR), pause après un échec. S'y ajoute un plafond de
tentatives par jour (AUTO_GENERATE_MAX_ATTEMPTS) : une panne de l'IA ne se transforme pas en série d'appels payants.
"""
import logging
import threading
import time
from datetime import datetime, timezone

import config

log = logging.getLogger(__name__)

CHECK_EVERY = 60               # secondes entre deux vérifications


class DailyGeneration:
    def __init__(self, service, hour, max_attempts=None, clock=time.time):
        self.service = service
        self.hour = hour
        self.max_attempts = config.AUTO_GENERATE_MAX_ATTEMPTS if max_attempts is None else max_attempts
        self.clock = clock
        self._day = None
        self._attempts = 0
        self._thread = None

    def _generated_on(self, day):
        """Une génération réussie existe-t-elle déjà pour ce jour (UTC) ?"""
        entry = self.service.latest()
        try:
            stamp = float(entry["generated_ts"]) if entry else None
        except (KeyError, TypeError, ValueError):
            return False
        return stamp is not None and datetime.fromtimestamp(stamp, tz=timezone.utc).date() == day

    def tick(self):
        """Une vérification. Renvoie ce qui s'est passé (pour les journaux et les tests) :
        'attente' (trop tôt), 'faite' (déjà une génération aujourd'hui), 'abandon' (trop de tentatives),
        'lancee' (un travail a démarré), ou l'état renvoyé par le service ('running', 'done', 'error')."""
        now = datetime.fromtimestamp(self.clock(), tz=timezone.utc)
        if self._day != now.date():
            self._day, self._attempts = now.date(), 0
        if now.hour < self.hour:
            return "attente"
        if self._generated_on(now.date()):
            return "faite"
        if self._attempts >= self.max_attempts:
            return "abandon"
        before = self.service.generations_today()
        state = self.service.request_generation().get("state")
        if self.service.generations_today() > before:       # un nouveau travail a vraiment démarré (et non rejoint)
            self._attempts += 1
            log.info("génération automatique lancée (tentative %d/%d)", self._attempts, self.max_attempts)
            return "lancee"
        return state

    def _loop(self):
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("génération automatique : vérification en échec")
            time.sleep(CHECK_EVERY)

    def start(self):
        """Lance la surveillance dans un fil de fond (une seule fois par processus)."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="daily-generation", daemon=True)
            self._thread.start()
            log.info("génération automatique activée : chaque jour à partir de %02d h UTC", self.hour)
        return self
