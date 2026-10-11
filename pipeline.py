"""Chaîne complète de génération : matchs du jour -> collecte -> analyse IA ->
pronostics -> combinés. Utilisée par le dashboard (en arrière-plan) et par main.py.

Les combinés ne portent que sur les matchs du JOUR (jour UTC) qui n'ont pas encore commencé. La première étape est donc de
les chercher : un jour sans match (ou avec trop peu de matchs), la chaîne s'arrête là, avant toute collecte et tout appel
payant à l'IA, avec NoMatchToday."""
import logging
import time

import config
from combo_generator import ComboGenerator
from data_collector import DataCollector

log = logging.getLogger(__name__)

MIN_MATCHES = 3

# Pourquoi aucun combiné n'est possible aujourd'hui -> (titre, explication). « none » : aucun match de nos cinq championnats
# aujourd'hui ; son titre est le message demandé, mot pour mot.
NO_MATCH_TEXTS = {
    "none": ("Pas de championnat aujourd'hui",
             "Les combinés se composent les jours où des matchs de nos cinq championnats ont lieu. Reviens demain."),
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


def run_pipeline(progress=None, collector=None, generator=None):
    """Renvoie {"combos": [...], "meta": {...}} ou lève GenerationError.

    `progress(etape, pourcentage, message)` est appelé au fil de l'avancement.
    `collector` et `generator` peuvent être injectés (tests).
    """
    progress = progress or _noop
    started = time.time()
    collector = collector or DataCollector()

    # D'abord les matchs du jour : sans eux, ni collecte des résultats ni appel à l'IA.
    progress("matchs", 5, "Recherche des matchs du jour…")
    matchday = collector.get_matchday()
    upcoming = matchday["matches"]
    t_upcoming = time.time()
    if len(upcoming) < MIN_MATCHES:
        if not matchday.get("complete", True):
            # Un championnat n'a pas répondu : on ne peut pas affirmer qu'il n'y a pas de match, simple panne à réessayer.
            raise GenerationError("Les matchs du jour sont momentanément indisponibles. Réessaie dans quelques minutes.")
        raise NoMatchToday(matchday.get("total", 0), len(upcoming), matchday.get("day"))

    progress("collecte", 12, "Collecte des derniers résultats…")
    collector.collect_all_data()
    t_collect = time.time()

    generator = generator or ComboGenerator()
    try:
        progress("analyse", 20, "Analyse des matchs par l'IA…")
        analyzer = generator.analyzer

        def on_batch(done, total):
            progress("analyse", 20 + int(60 * done / total),
                     f"Analyse des matchs par l'IA ({done}/{total})…")

        ia_items = analyzer.analyze_multiple_matches(upcoming, progress=on_batch)
        t_ia = time.time()
        if not ia_items:
            raise GenerationError("L'analyse IA est momentanément indisponible. Réessaie dans quelques minutes.",
                                  level=logging.ERROR)
        matched = analyzer.match_analyses(upcoming, ia_items)

        progress("pronostics", 82, "Calcul des pronostics et des cotes…")
        all_preds = []
        for match in upcoming:
            try:
                ia = matched.get(match["id"])
                if ia is not None:
                    analysis = analyzer.build_analysis_from_ia(match["home_team"], match["away_team"], ia)
                else:
                    # Pas d'analyse fiable pour CE match : on l'exclut plutôt que de lui
                    # attribuer celle d'un autre.
                    analysis = analyzer.analyze_match(match["home_team"], match["away_team"])
                real_odds = generator.get_real_odds(match["home_team"], match["away_team"], match["league"])
                all_preds.extend(generator.get_predictions_from_analysis(match, analysis, real_odds))
            except Exception:
                # Une donnée inattendue sur UN match ne doit pas faire échouer les autres.
                log.exception("match ignoré (%s vs %s)", match.get("home_team"), match.get("away_team"))

        progress("composition", 92, "Composition des combinés…")
        combos = generator.build_combos(all_preds)
        t_done = time.time()
        log.info("génération : %d matchs du jour, %d analysés, %d pronostics, %d combinés — matchs %.1fs, "
                 "collecte %.1fs, IA %.1fs, calculs %.1fs, total %.1fs",
                 len(upcoming), len(matched), len(all_preds), len(combos), t_upcoming - started,
                 t_collect - t_upcoming, t_ia - t_collect, t_done - t_ia, t_done - started)
        if not combos:
            if len(upcoming) <= MIN_MATCHES:
                raise GenerationError(
                    "Trop peu de matchs aujourd'hui pour composer un combiné à 2,50+ fiable. Réessaie demain.")
            raise GenerationError("Aucun combiné assez fiable avec les matchs d'aujourd'hui. Réessaie demain.")

        legs = [leg for combo in combos for leg in combo["predictions"]]
        meta = {
            "day": matchday.get("day"),
            "day_total": matchday.get("total", len(upcoming)),     # matchs du jour vus, commencés ou non
            "matches_total": len(upcoming),
            "matches_analyzed": len(matched),
            "leagues": sorted({m["league"] for m in upcoming}),
            "real_odds_legs": sum(1 for leg in legs if leg["odds_source"] == "bookmakers"),
            "total_legs": len(legs),
            "duration_seconds": round(t_done - started, 1),
        }
        return {"combos": combos, "meta": meta}
    finally:
        generator.close()
