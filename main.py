import os
from data_collector import DataCollector
from combo_generator import ComboGenerator
from datetime import datetime
from itertools import combinations, product
import json
import config


class TripleEliteVIP:
    def __init__(self):
        self.collector = DataCollector()
        self.generator = ComboGenerator()
        print("\nTRIPLE ELITE VIP - Demarrage...\n")

    def full_auto_generation(self):
        print("=" * 50)
        print(f"ANALYSE AUTOMATIQUE - {datetime.now().strftime('%d/%m/%Y %H:%M')}")
        print("=" * 50)
        print("\n1/4 Collecte des donnees...")
        self.collector.collect_all_data()
        print("\n2/4 Recherche des matchs a venir...")
        upcoming = self.collector.get_upcoming_matches()
        print(f"  {len(upcoming)} matchs trouves")
        if len(upcoming) < 3:
            print("  Pas assez de matchs (minimum 3 requis)")
            return

        print("\n3/4 Analyse IA...")
        analyses_ia = self.generator.analyzer.analyze_multiple_matches(upcoming)
        analyses_par_match = {}
        for ia in analyses_ia:
            if ia.get("home_team") and ia.get("away_team"):
                analyses_par_match[f"{ia['home_team']} vs {ia['away_team']}"] = ia

        all_preds = []
        for match in upcoming:
            key = f"{match['home_team']} vs {match['away_team']}"
            if key in analyses_par_match:
                analysis = self.generator.analyzer.build_analysis_from_ia(
                    match["home_team"], match["away_team"], analyses_par_match[key]
                )
            else:
                analysis = self.generator.analyzer.analyze_match(match["home_team"], match["away_team"])
            real_odds = self.generator.get_real_odds(match["home_team"], match["away_team"])
            preds = self.generator.get_predictions_from_analysis(match, analysis, real_odds)
            all_preds.extend(preds)
            print(f"  {match['home_team']} vs {match['away_team']} -> {len(preds)} pronostics")
        print(f"\n  {len(all_preds)} pronostics valides")

        print("\n4/4 Generation des combines...")
        preds_by_match = {}
        for pred in all_preds:
            key = f"{pred['home_team']} vs {pred['away_team']}"
            preds_by_match.setdefault(key, []).append(pred)

        all_combos = []
        match_keys = list(preds_by_match.keys())
        for m1, m2, m3 in combinations(match_keys, 3):
            for p1, p2, p3 in product(preds_by_match[m1], preds_by_match[m2], preds_by_match[m3]):
                combo = [p1, p2, p3]
                total_odds = round(p1["estimated_odds"] * p2["estimated_odds"] * p3["estimated_odds"], 2)
                if total_odds >= config.TARGET_ODDS:
                    avg_conf = sum(p["confidence"] for p in combo) / 3
                    score = round(avg_conf * 0.6 + len(set(p["type"] for p in combo)) * 5 + len(set(p["league"] for p in combo)) * 5, 1)
                    all_combos.append({
                        "predictions": combo,
                        "total_odds": total_odds,
                        "avg_confidence": round(avg_conf, 1),
                        "score": score
                    })
        all_combos.sort(key=lambda x: x["score"], reverse=True)
        print(f"  {len(all_combos)} combines generes")
        top3 = all_combos[:3]
        if not top3:
            print(f"  Aucun combine trouve avec cote >= {config.TARGET_ODDS}")
            return

        print("\n" + "=" * 50)
        print("TOP COMBINES")
        print("=" * 50)
        for i, combo in enumerate(top3, 1):
            print(f"\nCOMBINE #{i} | Cote: {combo['total_odds']} | Confiance: {combo['avg_confidence']}%  | Score: {combo['score']}/100")
            for j, pred in enumerate(combo["predictions"], 1):
                print(f"  Match {j}: {pred['home_team']} vs {pred['away_team']} ({pred['league']})")
                print(f"  -> {pred['type_name']} | Cote: {pred['estimated_odds']} | Confiance: {pred['confidence']}%")

        if not os.path.exists("results"):
            os.makedirs("results")
        filename = f"results/combo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(top3, f, indent=4, ensure_ascii=False, default=str)
        print(f"\nResultats sauvegardes dans {filename}")
        return top3

    def close(self):
        self.generator.close()


if __name__ == "__main__":
    app = TripleEliteVIP()
    print("1. Generation unique maintenant")
    print("2. Quitter")
    choix = input("Votre choix (1 ou 2) : ")
    if choix == "1":
        app.full_auto_generation()
    app.close()
    print("\nFin du programme.")
