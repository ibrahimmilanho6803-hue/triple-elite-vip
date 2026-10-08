"""Vitrine publique du site : résultats réels (/resultats) et combiné gratuit du jour (/gratuit).

La règle qui commande tout ce module : une page publique ne montre JAMAIS un combiné à venir, sauf le seul « combiné
gratuit ». Les combinés d'une génération sont le produit payant.

  - Résultats : uniquement des combinés dont TOUS les matchs sont joués, gagnés comme perdus (rien n'est trié). Un
    combiné déjà perdu mais dont certains matchs restent à jouer compte tout de suite dans le bilan (une défaite connue
    ne se cache pas) ; il n'est affiché qu'une fois tous ses matchs joués, pour ne pas révéler un pronostic à venir.
  - Combiné gratuit : UN seul combiné par génération, celui dont la chance estimée est la plus élevée, tant qu'aucun de
    ses matchs n'a commencé. Il est mémorisé (free_pick.json) : il reste le même si une nouvelle génération sort
    entre-temps (le lien partagé le matin montre le même coupon le soir), et quand il a commencé, les autres combinés de
    la même génération ne prennent pas sa place : il faut une nouvelle génération pour un nouveau combiné gratuit.

Rien de ce que fait un visiteur ne coûte quoi que ce soit : jamais d'appel à l'IA (on lit seulement la dernière génération,
GenerationService.latest) ni d'appel réseau dans la requête. Les scores manquants sont récupérés en arrière-plan, au plus
une fois tous les quarts d'heure (combo_history borne en plus chaque série d'appels).

Chaque dictionnaire rendu aux gabarits est construit champ par champ (liste blanche) : un champ ajouté plus tard aux
combinés enregistrés ne devient jamais public par accident.
"""
import json
import logging
import os
import threading
import time
from urllib.parse import quote

import combo_history
import config
import web_common as web

log = logging.getLogger(__name__)

# Un pourcentage de réussite n'est affiché qu'à partir de ces effectifs : en dessous, il ne dirait rien (2 combinés sur 3
# gagnés ne font pas « 67 % »). Les chiffres bruts, eux, sont toujours affichés.
MIN_COMBOS_FOR_RATE = 10
MIN_LEGS_FOR_RATE = 30
RECENT_COUNT = 12              # combinés détaillés sur la page « Résultats »
HISTORY_FILES = 1000           # générations relues : fenêtre glissante (la page dit « depuis le … »)
RESULTS_TTL = 300              # secondes pendant lesquelles le bilan calculé est réutilisé
REFRESH_EVERY = 900            # secondes mini entre deux récupérations de scores en arrière-plan
PICK_TTL = 10                  # secondes pendant lesquelles le combiné gratuit calculé est réutilisé

# Champs rendus aux gabarits (liste blanche).
_DONE_LEG_FIELDS = ("home_team", "away_team", "league", "kickoff", "type_name", "estimated_odds", "odds_source",
                    "confidence", "score", "outcome")
_UPCOMING_LEG_FIELDS = ("home_team", "away_team", "league", "kickoff", "type_name", "estimated_odds", "odds_source",
                        "confidence")


# --------------------------------------------------------------------------
# Résultats : bilan public
# --------------------------------------------------------------------------

def _is_complete(combo):
    """Tous les matchs sont joués (ou reportés/annulés) : le combiné peut être montré sans rien révéler à venir."""
    return all(leg.get("outcome") in ("won", "lost", "void") for leg in combo["predictions"])


def _done_view(combo):
    return {
        "generated_at": combo.get("generated_at"),
        "total_odds": combo.get("total_odds"),
        "success_probability": combo.get("success_probability"),
        "status": combo["status"],
        "predictions": [{key: leg.get(key) for key in _DONE_LEG_FIELDS} for leg in combo["predictions"]],
    }


def public_results(combos):
    """Bilan public à partir des combinés de combo_history.load_history (tous, du plus récent au plus ancien).

    Compte les combinés gagnés et perdus (jamais les « en attente » ni les « annulés »), les pronostics distincts
    gagnés et perdus (un même pronostic présent dans plusieurs combinés compte une fois), la chance moyenne annoncée,
    et détaille les RECENT_COUNT derniers combinés entièrement joués."""
    settled = [c for c in combos if c.get("status") in ("won", "lost")]
    won = sum(1 for c in settled if c["status"] == "won")
    complete = [c for c in settled if _is_complete(c)]

    legs = {}
    for combo in settled:
        for leg in combo["predictions"]:
            if leg.get("outcome") in ("won", "lost"):
                legs[(leg.get("home_team"), leg.get("away_team"), leg.get("kickoff"), leg.get("type_name"))] = leg["outcome"]
    legs_won = sum(1 for outcome in legs.values() if outcome == "won")

    chances = [web.as_number(c.get("success_probability")) for c in settled]
    chances = [chance for chance in chances if chance is not None]
    enough_combos = len(settled) >= MIN_COMBOS_FOR_RATE
    return {
        "combos_settled": len(settled),
        "won": won,
        "lost": len(settled) - won,
        "pending": sum(1 for c in combos if c.get("status") == "pending"),
        # Combinés perdus dont des matchs restent à jouer : comptés ci-dessus, pas encore détaillés ci-dessous.
        "lost_unfinished": len(settled) - len(complete),
        "enough": enough_combos,
        "min_combos": MIN_COMBOS_FOR_RATE,
        "min_legs": MIN_LEGS_FOR_RATE,
        "combo_win_rate": round(100 * won / len(settled)) if enough_combos else None,
        "legs_settled": len(legs),
        "legs_won": legs_won,
        "legs_lost": len(legs) - legs_won,
        "leg_win_rate": round(100 * legs_won / len(legs)) if len(legs) >= MIN_LEGS_FOR_RATE else None,
        # Chance moyenne annoncée sur les coupons, à comparer à la réussite constatée : seulement avec assez de combinés.
        "avg_chance": round(sum(chances) / len(chances)) if enough_combos and chances else None,
        "since": min((c["generated_at"] for c in settled if c.get("generated_at")), default=None),
        "recent": [_done_view(c) for c in complete[:RECENT_COUNT]],
        "recent_total": len(complete),
    }


def default_loader(refresh):
    """Tout l'historique (fenêtre glissante de HISTORY_FILES générations) ; refresh=True va chercher les scores manquants."""
    return combo_history.load_history(config.RESULTS_DIR, max_combos=10 ** 6, max_files=HISTORY_FILES, refresh=refresh)


# --------------------------------------------------------------------------
# Combiné gratuit
# --------------------------------------------------------------------------

def is_upcoming(combo, now, margin):
    """Tous les matchs du combiné commencent dans plus de `margin` secondes (et ont une heure de coup d'envoi lisible)."""
    legs = combo.get("predictions") if isinstance(combo, dict) else None
    if not isinstance(legs, list) or not legs:
        return False
    for leg in legs:
        moment = web.parse_iso(leg.get("kickoff")) if isinstance(leg, dict) else None
        if moment is None or moment.timestamp() - now < margin:
            return False
    return True


def _upcoming_view(combo):
    return {
        "total_odds": combo.get("total_odds"),
        "success_probability": combo.get("success_probability"),
        "predictions": [{key: leg.get(key) for key in _UPCOMING_LEG_FIELDS} for leg in combo["predictions"]],
    }


def _wrap_pick(view):
    first = min((leg["kickoff"] for leg in view["predictions"]), key=lambda iso: web.parse_iso(iso))
    return {"combo": view, "first_kickoff": first}


class _JsonState:
    """Petit fichier d'état JSON dans le dossier de cache, avec copie en mémoire si le disque est inutilisable."""

    def __init__(self, directory):
        self._directory = directory
        self._mem = {}

    def read(self, name):
        try:
            with open(os.path.join(self._directory(), name), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return self._mem.get(name)

    def write(self, name, data):
        self._mem[name] = data
        try:
            directory = self._directory()
            os.makedirs(directory, exist_ok=True)
            tmp = os.path.join(directory, f"{name}.{os.getpid()}.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, os.path.join(directory, name))
        except OSError as e:
            log.error("écriture de %s impossible : %s", name, e)


# --------------------------------------------------------------------------
# Partage
# --------------------------------------------------------------------------

def share_links(pick, page_url):
    """Adresses de partage WhatsApp et Telegram du combiné gratuit (de simples liens : ils marchent sans JavaScript)."""
    combo = pick["combo"]
    bits = [f"{len(combo['predictions'])} matchs"]
    odds = web.fr_odds(combo.get("total_odds"))
    if odds != "–":
        bits.append(f"cote {odds}")
    chance = web.fr_pct(combo.get("success_probability"))
    if chance:
        bits.append(f"chance estimée {chance}")
    text = f"⚽ Le combiné gratuit du jour sur Triple Elite VIP : {', '.join(bits)}.".replace(web.NBSP, " ")
    return {
        "url": page_url,
        "text": text,
        "whatsapp": "https://wa.me/?text=" + quote(f"{text} {page_url}", safe=""),
        "telegram": "https://t.me/share/url?url=" + quote(page_url, safe="") + "&text=" + quote(text, safe=""),
    }


# --------------------------------------------------------------------------
# Le service
# --------------------------------------------------------------------------

class Showcase:
    """Ce que les pages publiques ont le droit de montrer : bilan des combinés joués, combiné gratuit.

    service : GenerationService (on n'y lit que la dernière génération). loader(refresh) : historique complet au format
    de combo_history.load_history ; background_refresh : récupérer les scores manquants en arrière-plan (par défaut
    seulement avec le vrai chargeur : un chargeur de test ne fait aucun appel réseau)."""

    def __init__(self, service, loader=None, clock=time.time, results_ttl=RESULTS_TTL, refresh_every=REFRESH_EVERY,
                 pick_ttl=PICK_TTL, background_refresh=None):
        self.service = service
        self.loader = loader or default_loader
        self.clock = clock
        self.results_ttl = results_ttl
        self.refresh_every = refresh_every
        self.pick_ttl = pick_ttl
        self.background_refresh = loader is None if background_refresh is None else background_refresh
        self._lock = threading.Lock()              # drapeaux et combiné gratuit en mémoire
        self._compute_lock = threading.Lock()      # un seul calcul du bilan à la fois
        self._results = None                       # (instant, bilan)
        self._failed_at = None
        self._pick = None                          # (instant, combiné gratuit ou None)
        self._refreshing = False
        self._last_refresh = 0.0
        self._state = _JsonState(lambda: getattr(self.service, "cache_dir", None) or config.CACHE_DIR)

    # ---------------------------------------------------------- bilan

    def results(self):
        """Bilan public (voir public_results), ou None si l'historique est illisible et qu'aucun bilan n'a jamais été calculé."""
        now = self.clock()
        with self._compute_lock:
            snapshot = self._results
            if snapshot is not None and now - snapshot[0] < self.results_ttl:
                data = snapshot[1]
            elif self._failed_at is not None and now - self._failed_at < 30:
                data = snapshot[1] if snapshot is not None else None      # panne récente : pas de nouvel essai à chaque requête
            else:
                try:
                    data = public_results(self.loader(False)["combos"])
                except Exception:
                    log.exception("vitrine : bilan public impossible à calculer")
                    self._failed_at = now
                    data = snapshot[1] if snapshot is not None else None
                else:
                    self._failed_at = None
                    self._results = (now, data)
        self._refresh_in_background(now)
        return data

    def _refresh_in_background(self, now):
        if not self.background_refresh:
            return
        with self._lock:
            if self._refreshing or now - self._last_refresh < self.refresh_every:
                return
            self._refreshing = True
            self._last_refresh = now
        threading.Thread(target=self._refresh, name="showcase-refresh", daemon=True).start()

    def _refresh(self):
        try:
            history = self.loader(True)            # récupère les scores manquants (appels bornés), puis relit tout
            data = public_results(history["combos"])
            with self._compute_lock:
                self._results = (self.clock(), data)
                self._failed_at = None
        except Exception:
            log.exception("vitrine : récupération des scores impossible")
        finally:
            with self._lock:
                self._refreshing = False

    # ---------------------------------------------------------- combiné gratuit

    def free_pick(self):
        """{"combo": ..., "first_kickoff": ISO} ou None (page désactivée, aucune génération récente, tous les combinés ont commencé)."""
        if not config.FREE_PICK_ENABLED:
            return None
        now = self.clock()
        with self._lock:
            cached = self._pick
        if cached is not None and now - cached[0] < self.pick_ttl:
            return cached[1]
        try:
            pick = self._current_pick(now)
        except Exception:
            log.exception("vitrine : combiné gratuit impossible à déterminer")
            return None
        with self._lock:
            self._pick = (now, pick)
        return pick

    def _current_pick(self, now):
        max_age = config.FREE_PICK_MAX_AGE_HOURS * 3600
        margin = config.FREE_PICK_MARGIN_MINUTES * 60

        # Le combiné déjà choisi reste le combiné du jour tant qu'il est valable.
        saved = self._state.read("free_pick.json")
        if saved:
            picked_at = web.as_number(saved.get("picked_ts"))
            combo = saved.get("combo")
            if picked_at is not None and 0 <= now - picked_at <= max_age and is_upcoming(combo, now, margin):
                return _wrap_pick(combo)

        entry = self.service.latest()
        generated = web.as_number(entry.get("generated_ts")) if entry else None
        if generated is None or now - generated > max_age:
            return None
        # Un seul combiné gratuit par génération : s'il a déjà été choisi dans celle-ci, les deux autres restent payants.
        if saved and web.as_number(saved.get("generated_ts")) == generated:
            return None
        candidates = [c for c in entry["combos"] if is_upcoming(c, now, margin)]
        if not candidates:
            return None
        best = max(candidates, key=lambda c: web.as_number(c.get("success_probability")) or 0.0)
        view = _upcoming_view(best)
        self._state.write("free_pick.json", {"picked_ts": now, "generated_ts": generated, "combo": view})
        return _wrap_pick(view)
