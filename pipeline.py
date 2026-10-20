"""Chaîne complète de génération : matchs du jour -> choix des matchs -> collecte -> analyse IA ->
pronostics -> combinés. Utilisée par le dashboard (en arrière-plan) et par main.py.

Les combinés ne portent que sur les matchs du JOUR (jour UTC) qui n'ont pas encore commencé. La première étape est donc de
les chercher : un jour sans match (ou avec trop peu de matchs), la chaîne s'arrête là, avant toute collecte et tout appel
payant à l'IA, avec NoMatchToday.

Parmi ces matchs, la génération en retient quelques-uns (variety.choose_pool) : de préférence ceux que ce client n'a pas encore
reçus aujourd'hui, et dans tous les championnats, pas toujours les mêmes. Avec `shared` (shared_data.SharedData, fourni par le
site), l'estimation de l'IA pour un match est faite une seule fois et réutilisée par toutes les générations : ce que coûte l'IA
suit le nombre de matchs du jour, pas le nombre de clients. Sans `shared` (main.py, tests), tout est refait à chaque appel."""
import logging
import time
from collections import Counter
from contextlib import contextmanager

import config
import markets
from combo_generator import ComboGenerator
from data_collector import DataCollector
from variety import Variety, choose_pool

log = logging.getLogger(__name__)

MIN_MATCHES = 3

# Pourquoi aucun combiné n'est possible aujourd'hui -> (titre, explication). « none » : aucun match de nos championnats
# aujourd'hui ; son titre est le message demandé, mot pour mot.
NO_MATCH_TEXTS = {
    "none": ("Pas de championnat aujourd'hui",
             "Les combinés se composent les jours où des matchs de nos championnats ont lieu. Reviens demain."),
    "started": ("Les matchs d'aujourd'hui ont déjà commencé",
                "Un combiné ne porte que sur des matchs à venir. Reviens demain pour de nouveaux combinés."),
    "few": ("Trop peu de matchs aujourd'hui",
            "Il reste moins de trois matchs à venir aujourd'hui : pas assez pour composer un combiné de trois matchs. "
            "Reviens demain."),
}


def no_match_reason(total, upcoming):
    """Pourquoi aucun combiné n'est possible : « none » (aucun match du jour), « started » (ils ont tous commencé) ou « few »
    (il en reste moins de MIN_MATCHES). None quand il en reste assez. total : matchs du jour vus, commencés ou non ;
    upcoming : ceux qui n'ont pas commencé."""
    if upcoming >= MIN_MATCHES:
        return None
    if upcoming > 0:
        return "few"
    return "started" if total > 0 else "none"


class GenerationError(Exception):
    """Échec « normal » de la génération, avec un message lisible par le client."""

    def __init__(self, user_message, level=logging.WARNING):
        super().__init__(user_message)
        self.user_message = user_message
        self.level = level


class NoMatchToday(GenerationError):
    """Pas assez de matchs aujourd'hui pour composer un combiné : ce n'est pas une panne, rien n'a été facturé par l'IA.
    `reason` : voir no_match_reason ; `total` et `upcoming` : matchs du jour vus et matchs encore à venir."""

    def __init__(self, total=0, upcoming=0, day=None):
        self.total, self.upcoming, self.day = total, upcoming, day
        self.reason = no_match_reason(total, upcoming) or "few"
        super().__init__(NO_MATCH_TEXTS[self.reason][0], level=logging.INFO)


def _noop(*_args, **_kwargs):
    return None


MSG_AI_DOWN = "L'analyse IA est momentanément indisponible. Réessaie dans quelques minutes."
MSG_BUSY = "Beaucoup d'analyses sont en cours. Réessaie dans quelques minutes."


@contextmanager
def _analysis_section(shared):
    """Section où une seule génération à la fois collecte les résultats et interroge l'IA (voir shared_data) : celle qui attend
    trouve ensuite les analyses déjà faites. Sans `shared`, rien à attendre."""
    if shared is None:
        yield
        return
    if not shared.analysis_lock.acquire(timeout=config.JOB_MAX_SECONDS * 0.8):
        raise GenerationError(MSG_BUSY, level=logging.ERROR)
    try:
        yield
    finally:
        shared.analysis_lock.release()


def _with_market(matches, generator):
    """Copies des `matches` portant l'avis des bookmakers (clé "market" : cotes médianes, voir ComboGenerator.get_real_odds) quand
    l'IA doit le voir (config.AI_SEES_MARKET). Sans cotes pour un match, il part tel quel : l'IA l'analyse sans cet avis."""
    if not config.AI_SEES_MARKET or generator is None:
        return matches
    enriched = []
    for match in matches:
        try:
            odds = generator.get_real_odds(match["home_team"], match["away_team"], match["league"])
        except Exception:
            log.exception("cotes indisponibles pour l'IA (%s vs %s)", match.get("home_team"), match.get("away_team"))
            odds = None
        enriched.append({**match, "market": odds} if odds else match)
    return enriched


def _estimates(pool, collector, analyzer, progress, shared, generator=None):
    """({id du match: estimation de l'IA}, nombre de matchs payés à l'IA pour cette génération).

    Avec `shared`, les matchs déjà analysés (et encore valables) viennent de la mémoire commune : seuls les autres sont envoyés
    à l'IA, après une collecte des derniers résultats si elle n'a pas été faite récemment. Si l'IA ne répond pas du tout, la
    génération continue avec les analyses déjà gardées, et ne s'arrête que s'il n'y en a aucune."""
    cached = shared.analyses.lookup(pool) if shared is not None else {}
    missing = [match for match in pool if match["id"] not in cached]
    if not missing:
        return cached, 0
    if shared is not None and shared.ai_cooling_down():
        log.warning("analyse IA : sans réponse il y a moins de %d s, %d match(s) non analysé(s) cette fois-ci",
                    config.AI_RETRY_AFTER_SECONDS, len(missing))
        if not cached:
            raise GenerationError(MSG_AI_DOWN, level=logging.ERROR)
        return cached, 0

    progress("collecte", 12, "Collecte des derniers résultats…")
    if shared is not None:
        shared.collect(collector)
    else:
        collector.collect_all_data()

    progress("analyse", 20, "Analyse des matchs par l'IA…")

    def on_batch(done, total):
        progress("analyse", 20 + int(60 * done / total), f"Analyse des matchs par l'IA ({done}/{total})…")

    ia_items = analyzer.analyze_multiple_matches(_with_market(missing, generator), progress=on_batch)
    if not ia_items:
        if shared is not None:
            shared.note_ai_failure()
        if not cached:
            raise GenerationError(MSG_AI_DOWN, level=logging.ERROR)
        log.warning("analyse IA sans réponse : la génération continue avec les %d analyse(s) déjà gardées", len(cached))
        return cached, 0
    fresh = analyzer.match_analyses(missing, ia_items)
    if shared is not None:
        shared.note_ai_success()
        shared.analyses.store(missing, fresh)
    return {**cached, **fresh}, len(missing)


def run_pipeline(progress=None, collector=None, generator=None, variety=None, shared=None):
    """Renvoie {"combos": [...], "meta": {...}} ou lève GenerationError.

    `progress(etape, pourcentage, message)` est appelé au fil de l'avancement.
    `variety` (variety.Variety) : ce que le client a déjà reçu aujourd'hui. `shared` (shared_data.SharedData) : ce que les
    générations de tous les clients partagent. `collector` et `generator` peuvent être injectés (tests).
    """
    progress = progress or _noop
    variety = variety or Variety()
    started = time.time()
    collector = collector or DataCollector()

    # D'abord les matchs du jour : sans eux, ni collecte des résultats ni appel à l'IA.
    progress("matchs", 5, "Recherche des matchs du jour…")
    matchday = shared.matchday(collector) if shared is not None else collector.get_matchday()
    upcoming = matchday["matches"]
    t_upcoming = time.time()
    if len(upcoming) < MIN_MATCHES:
        if not matchday.get("complete", True):
            # Un championnat n'a pas répondu : on ne peut pas affirmer qu'il n'y a pas de match, simple panne à réessayer.
            raise GenerationError("Les matchs du jour sont momentanément indisponibles. Réessaie dans quelques minutes.")
        raise NoMatchToday(matchday.get("total", 0), len(upcoming), matchday.get("day"))

    pool = choose_pool(upcoming, variety)

    generator = generator or ComboGenerator()
    try:
        analyzer = generator.analyzer
        with _analysis_section(shared):
            estimates, paid = _estimates(pool, collector, analyzer, progress, shared, generator)
            t_ia = time.time()
            matched = {match["id"]: estimates[match["id"]] for match in pool if match["id"] in estimates}

            progress("pronostics", 82, "Calcul des pronostics et des cotes…")
            analyses = {}
            for match in pool:
                try:
                    ia = matched.get(match["id"])
                    if ia is not None:
                        analyses[match["id"]] = analyzer.build_analysis_from_ia(match["home_team"], match["away_team"], ia)
                    else:
                        # Pas d'analyse fiable pour CE match : on l'exclut plutôt que de lui
                        # attribuer celle d'un autre.
                        analyses[match["id"]] = analyzer.analyze_match(match["home_team"], match["away_team"])
                except Exception:
                    # Une donnée inattendue sur UN match ne doit pas faire échouer les autres.
                    log.exception("match ignoré (%s vs %s)", match.get("home_team"), match.get("away_team"))

        all_preds = []
        for match in pool:
            analysis = analyses.get(match["id"])
            if analysis is None:
                continue
            try:
                real_odds = generator.get_real_odds(match["home_team"], match["away_team"], match["league"])
                all_preds.extend(generator.get_predictions_from_analysis(match, analysis, real_odds))
            except Exception:
                log.exception("match ignoré (%s vs %s)", match.get("home_team"), match.get("away_team"))

        progress("composition", 92, "Composition des combinés…")
        combos = generator.build_combos(all_preds, variety=variety)
        t_done = time.time()
        log.info("génération : %d matchs à venir, %d retenus, %d analysés (%d par l'IA, %d déjà gardés), %d pronostics, "
                 "%d combinés — matchs %.1fs, collecte et IA %.1fs, calculs %.1fs, total %.1fs",
                 len(upcoming), len(pool), len(matched), paid, len(matched) - paid, len(all_preds), len(combos),
                 t_upcoming - started, t_ia - t_upcoming, t_done - t_ia, t_done - started)
        if not combos:
            if len(pool) <= MIN_MATCHES:
                raise GenerationError(
                    "Trop peu de matchs aujourd'hui pour composer un combiné à 2,50+ fiable. Réessaie demain.")
            raise GenerationError("Aucun combiné assez fiable avec les matchs d'aujourd'hui. Réessaie demain.")

        legs = [leg for combo in combos for leg in combo["predictions"]]
        origins = Counter(leg["odds_source"] for leg in legs)
        # Dans les journaux : de quelle origine sont les cotes proposées (voir ComboGenerator._priced) ; c'est ce qui dit combien
        # de cotes 1xBet la génération a trouvées, donc combien de « ≈ » le client voit.
        log.info("cotes des %d pronostics proposés : %d relevées chez %s, %d médiane des bookmakers (≈), %d estimées (≈)",
                 len(legs), origins["bookmakers"], config.ODDS_BOOKMAKER_LABEL or "les bookmakers",
                 origins["mediane"], origins["estimee"])
        # Et quels pronostics : c'est ce qui répond à « pourquoi toujours le même pari ? » (voir GENRE_REPEAT_PENALTY).
        types = Counter(str(leg.get("type")) for leg in legs)
        log.info("types des %d pronostics proposés : %s (%d genres de pari différents)", len(legs),
                 ", ".join(code if n == 1 else f"{code} x{n}" for code, n in types.most_common()),
                 len({markets.genre_of(code) for code in types}))
        meta = {
            "day": matchday.get("day"),
            "day_total": matchday.get("total", len(upcoming)),     # matchs du jour vus, commencés ou non
            "matches_available": len(upcoming),                    # matchs du jour pas encore commencés
            "matches_total": len(pool),                            # ceux que cette génération a retenus
            "matches_analyzed": len(matched),
            "ai_matches": paid,                                    # matchs payés à l'IA cette fois (0 : tout était déjà gardé)
            "leagues": sorted({m["league"] for m in pool}),
            "real_odds_legs": sum(1 for leg in legs if leg["odds_source"] == "bookmakers"),
            "total_legs": len(legs),
            "duration_seconds": round(t_done - started, 1),
        }
        return {"combos": combos, "meta": meta}
    finally:
        generator.close()
