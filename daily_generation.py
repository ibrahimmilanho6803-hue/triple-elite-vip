"""Génération automatique quotidienne (facultative).

Sans elle, une génération n'existe que si un abonné clique sur « Générer » : les jours calmes, la page « Combiné gratuit »
reste vide et l'historique public n'avance pas. Avec elle, une génération part chaque jour à l'heure choisie (variable
AUTO_GENERATE_HOUR, heure UTC), si personne n'en a déjà fait une ce jour-là, c'est-à-dire composée avec les matchs de CE
jour. Un jour sans match (ou avec trop peu de matchs), elle le constate sans appeler l'IA et n'insiste pas : « Pas de
championnat aujourd'hui », sur la page « Combiné gratuit » comme chez les abonnés.

La boucle fait aussi, dès le début de chaque journée et AVANT l'heure de génération, le constat des matchs du jour
(GenerationService.refresh_matchday : gratuit, aucun appel à l'IA). C'est lui qui permet d'afficher « Pas de championnat
aujourd'hui » dès minuit, sans attendre la génération ni le clic d'un abonné.

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
        """Une génération réussie existe-t-elle déjà pour ce jour (UTC) ? Il faut qu'elle ait été composée avec les matchs
        de CE jour (« day » dans ses métadonnées) : une génération d'avant le passage aux matchs du jour seulement, qui
        portait sur les prochains jours, ne compte pas."""
        entry = self.service.latest()
        meta = entry.get("meta") if isinstance(entry, dict) else None
        return isinstance(meta, dict) and meta.get("day") == day.isoformat()

    def _watch_matchday(self):
        """Le constat des matchs du jour : gratuit, et fait même avant l'heure de génération. Ne lève jamais."""
        try:
            self.service.refresh_matchday()
        except Exception:
            log.exception("génération automatique : constat des matchs du jour en échec")

    def tick(self):
        """Une vérification. Renvoie ce qui s'est passé (pour les journaux et les tests) :
        'attente' (trop tôt), 'faite' (déjà une génération aujourd'hui), 'sans_match' (pas de match aujourd'hui, ou trop
        peu : rien à générer, et on ne redemande pas toutes les minutes), 'abandon' (trop de tentatives),
        'lancee' (un travail a démarré), ou l'état renvoyé par le service ('running', 'done', 'error')."""
        now = datetime.fromtimestamp(self.clock(), tz=timezone.utc)
        if self._day != now.date():
            self._day, self._attempts = now.date(), 0
        self._watch_matchday()
        if now.hour < self.hour:
            return "attente"
        if self._generated_on(now.date()):
            return "faite"
        if self.service.no_match_today():
            return "sans_match"
        if self._attempts >= self.max_attempts:
            return "abandon"
        response = self.service.request_generation()
        if response.get("started"):                          # un nouveau travail a vraiment démarré (et non rejoint)
            self._attempts += 1
            log.info("génération automatique lancée (tentative %d/%d)", self._attempts, self.max_attempts)
            return "lancee"
        return response.get("state")

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
