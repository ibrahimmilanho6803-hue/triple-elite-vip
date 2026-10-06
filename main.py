"""Génération des combinés en ligne de commande (essais et vérifications, sans passer par le site).

    python main.py            # génère et affiche les combinés
    python main.py --save     # les ajoute aussi à l'historique visible par les clients
    python main.py --json     # affiche le résultat brut (JSON)

Utilise exactement la même chaîne que le site (pipeline.run_pipeline). Variables nécessaires :
ANTHROPIC_API_KEY (analyse IA) ; facultatives : SPORTSDB_API_KEY, ODDS_API_KEY, DATA_DIR.
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import config
from pipeline import GenerationError, run_pipeline


def format_combo(number, combo):
    lines = [f"COMBINÉ {number} : cote {combo['total_odds']:.2f}, chance estimée {combo['success_probability']:.0f} %, "
             f"confiance moyenne {combo['avg_confidence']:.0f} %"]
    for leg in combo["predictions"]:
        approx = "" if leg.get("odds_source") == "bookmakers" else "≈"
        lines.append(f"  - {leg['home_team']} - {leg['away_team']} ({leg['league']})")
        lines.append(f"      {leg['type_name']} | cote {approx}{leg['estimated_odds']:.2f} | confiance {leg['confidence']} %")
    return "\n".join(lines)


def save_to_history(combos):
    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(config.RESULTS_DIR, f"combo_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(combos, f, indent=2, ensure_ascii=False, default=str)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Génère les combinés Triple Elite VIP.")
    parser.add_argument("--save", action="store_true", help="ajoute les combinés à l'historique des clients")
    parser.add_argument("--json", action="store_true", help="affiche le résultat brut au format JSON")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY n'est pas définie : l'analyse IA est impossible.", file=sys.stderr)
        return 2

    def progress(_step, percent, message):
        print(f"[{percent:3d} %] {message}", file=sys.stderr)

    try:
        result = run_pipeline(progress=progress)
    except GenerationError as exc:
        print(exc.user_message, file=sys.stderr)
        return 1

    combos = result["combos"]
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"\nTRIPLE ELITE VIP : {len(combos)} combinés générés le {datetime.now().strftime('%d/%m/%Y à %H:%M')}\n")
        print("\n\n".join(format_combo(n, combo) for n, combo in enumerate(combos, 1)))
        meta = result["meta"]
        print(f"\n{meta['matches_analyzed']}/{meta['matches_total']} matchs analysés, "
              f"{meta['real_odds_legs']}/{meta['total_legs']} cotes réelles (≈ = cote estimée).")
    if args.save:
        print(f"Ajouté à l'historique : {save_to_history(combos)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
