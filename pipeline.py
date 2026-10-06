"""Chaîne complète de génération : collecte -> matchs à venir -> analyse IA ->
pronostics -> combinés. Utilisée par le dashboard (en arrière-plan) et par main.py."""
import logging
import time

import config
from combo_generator import ComboGenerator
from data_collector import DataCollector

log = logging.getLogger(__name__)

MIN_MATCHES = 3


class GenerationError(Exception):
    """Échec « normal » de la génération, avec un message lisible par le client."""

    def __init__(self, user_message, level=logging.WARNING):
        super().__init__(user_message)
        self.user_message = user_message
        self.level = level


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

    progress("collecte", 5, "Collecte des derniers résultats…")
    collector.collect_all_data()
    t_collect = time.time()

    progress("matchs", 15, "Recherche des prochains matchs…")
    upcoming = collector.get_upcoming_matches()
    t_upcoming = time.time()
    if len(upcoming) < MIN_MATCHES:
        raise GenerationError("Pas assez de matchs à venir pour le moment. Réessaie plus tard.")

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
        log.info("génération : %d matchs, %d analysés, %d pronostics, %d combinés — collecte %.1fs, "
                 "matchs %.1fs, IA %.1fs, calculs %.1fs, total %.1fs",
                 len(upcoming), len(matched), len(all_preds), len(combos), t_collect - started,
                 t_upcoming - t_collect, t_ia - t_upcoming, t_done - t_ia, t_done - started)
        if not combos:
            if len(upcoming) <= MIN_MATCHES:
                raise GenerationError(
                    "Trop peu de matchs à venir dans les prochains jours (trêve internationale probable) "
                    "pour composer un combiné à 2,50+ fiable. Réessaie dans quelques jours.")
            raise GenerationError("Aucun combiné assez fiable pour le moment. Réessaie plus tard.")

        legs = [leg for combo in combos for leg in combo["predictions"]]
        meta = {
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
