import os
import requests
from data_collector import DataCollector
from analyzerv2 import MatchAnalyzer
import config

# SECURITE : cette cle the-odds-api.com etait codee en dur ici et poussee sur
# un depot public. Definis ODDS_API_KEY sur Render avec une cle regeneree.
# Sans variable definie, les cotes reelles ne sont simplement pas recuperees
# et l'appli retombe sur les cotes estimees (get_fallback_odds) : pas de
# plantage, juste une precision moindre.
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")


class ComboGenerator:
    def __init__(self):
        self.collector = DataCollector()
        self.analyzer = MatchAnalyzer()
        self.min_confidence = config.MIN_CONFIDENCE
        self.prediction_types = {
            "V1": "Victoire equipe 1",
            "V2": "Victoire equipe 2",
            "1X": "Victoire ou nul equipe 1",
            "2X": "Victoire ou nul equipe 2",
            "TOTAL_0.5+": "Plus de 0.5 but",
            "TOTAL_1+": "Plus de 1 but",
            "TOTAL_1.5+": "Plus de 1.5 buts",
            "TOTAL_2+": "Plus de 2 buts",
            "TOTAL_2.5+": "Plus de 2.5 buts",
            "TOTAL_3+": "Plus de 3 buts",
            "TOTAL_0.5-": "Moins de 0.5 but",
            "TOTAL_1-": "Moins de 1 but",
            "TOTAL_1.5-": "Moins de 1.5 buts",
            "TOTAL_2-": "Moins de 2 buts",
            "TOTAL_2.5-": "Moins de 2.5 buts",
            "TOTAL_3-": "Moins de 3 buts",
            "EQ1_0.5+": "Equipe 1 plus de 0.5 but",
            "EQ1_1.5+": "Equipe 1 plus de 1.5 buts",
            "EQ1_2.5+": "Equipe 1 plus de 2.5 buts",
            "EQ2_0.5+": "Equipe 2 plus de 0.5 but",
            "EQ2_1.5+": "Equipe 2 plus de 1.5 buts",
            "EQ2_2.5+": "Equipe 2 plus de 2.5 buts",
            "V1_ET_0.5+": "V1 et plus de 0.5 but",
            "V1_ET_1.5+": "V1 et plus de 1.5 buts",
            "V1_ET_2.5+": "V1 et plus de 2.5 buts",
            "V1_ET_3.5+": "V1 et plus de 3.5 buts",
            "1X_ET_1.5+": "1X et plus de 1.5 buts",
            "1X_ET_2.5+": "1X et plus de 2.5 buts",
            "1X_ET_3.5+": "1X et plus de 3.5 buts",
            "V2_ET_0.5+": "V2 et plus de 0.5 but",
            "V2_ET_1.5+": "V2 et plus de 1.5 buts",
            "V2_ET_2.5+": "V2 et plus de 2.5 buts",
            "V2_ET_3.5+": "V2 et plus de 3.5 buts",
            "2X_ET_1.5+": "2X et plus de 1.5 buts",
            "2X_ET_2.5+": "2X et plus de 2.5 buts",
            "2X_ET_3.5+": "2X et plus de 3.5 buts",
            "AU_MOINS_1.5": "Au moins une equipe marque plus de 1.5 buts",
            "AU_MOINS_2.5": "Au moins une equipe marque plus de 2.5 buts",
            "AU_MOINS_3.5": "Au moins une equipe marque plus de 3.5 buts",
            "V1_T1_0.5+": "V1 et Total 1 plus de 0.5 but",
            "V1_T1_1.5+": "V1 et Total 1 plus de 1.5 buts",
            "V1_T1_2.5+": "V1 et Total 1 plus de 2.5 buts",
            "V1_T1_3.5+": "V1 et Total 1 plus de 3.5 buts",
            "1X_T1_0.5+": "1X et Total 1 plus de 0.5 but",
            "1X_T1_1.5+": "1X et Total 1 plus de 1.5 buts",
            "1X_T1_2.5+": "1X et Total 1 plus de 2.5 buts",
            "1X_T1_3.5+": "1X et Total 1 plus de 3.5 buts",
            "V2_T2_0.5+": "V2 et Total 2 plus de 0.5 but",
            "V2_T2_1.5+": "V2 et Total 2 plus de 1.5 buts",
            "V2_T2_2.5+": "V2 et Total 2 plus de 2.5 buts",
            "V2_T2_3.5+": "V2 et Total 2 plus de 3.5 buts",
            "2X_T2_0.5+": "2X et Total 2 plus de 0.5 but",
            "2X_T2_1.5+": "2X et Total 2 plus de 1.5 buts",
            "2X_T2_2.5+": "2X et Total 2 plus de 2.5 buts",
            "2X_T2_3.5+": "2X et Total 2 plus de 3.5 buts",
            "BTTS_OUI": "Les deux equipes marquent OUI",
            "BTTS_NON": "Les deux equipes marquent NON"
        }

    @staticmethod
    def _same_team(name_a, name_b):
        """Comparaison tolerante mais moins permissive que la version
        d'origine (une trop courte sous-chaine commune faisait matcher des
        equipes differentes)."""
        a, b = name_a.lower().strip(), name_b.lower().strip()
        if not a or not b:
            return False
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        if len(shorter) < 4:
            return a == b
        return shorter in longer

    def get_real_odds(self, home_team, away_team):
        if not ODDS_API_KEY:
            return None
        try:
            sports = ["soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga"]
            for sport in sports:
                url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
                params = {"apiKey": ODDS_API_KEY, "regions": "eu", "markets": "h2h,totals"}
                response = requests.get(url, params=params, timeout=10)
                if response.status_code != 200:
                    continue
                data = response.json()
                if not isinstance(data, list):
                    continue
                for match in data:
                    home_api = match.get("home_team", "")
                    away_api = match.get("away_team", "")
                    if self._same_team(home_team, home_api) and self._same_team(away_team, away_api):
                        odds = {}
                        for bookmaker in match.get("bookmakers", []):
                            for market in bookmaker.get("markets", []):
                                if market["key"] == "h2h":
                                    for outcome in market["outcomes"]:
                                        if outcome["name"] == match["home_team"]:
                                            odds["home"] = outcome["price"]
                                        elif outcome["name"] == match["away_team"]:
                                            odds["away"] = outcome["price"]
                                        elif outcome["name"] == "Draw":
                                            odds["draw"] = outcome["price"]
                                elif market["key"] == "totals":
                                    for outcome in market["outcomes"]:
                                        pt = outcome.get("point")
                                        if outcome["name"] == "Over":
                                            odds[f"over_{pt}"] = outcome["price"]
                                        elif outcome["name"] == "Under":
                                            odds[f"under_{pt}"] = outcome["price"]
                                elif market["key"] == "btts":
                                    for outcome in market["outcomes"]:
                                        if outcome["name"] == "Yes":
                                            odds["btts_yes"] = outcome["price"]
                                        elif outcome["name"] == "No":
                                            odds["btts_no"] = outcome["price"]
                            break
                        if odds:
                            return odds
            return None
        except Exception as e:
            print(f"Erreur odds: {e}")
            return None

    def get_confidence(self, ptype, analysis):
        preds = analysis.get("predictions", {})
        mapping = {
            "V1": "1", "V2": "2", "1X": "1X", "2X": "2X",
            "TOTAL_0.5+": "+0.5", "TOTAL_1+": "+1", "TOTAL_1.5+": "+1.5",
            "TOTAL_2+": "+2", "TOTAL_2.5+": "+2.5", "TOTAL_3+": "+3",
            "TOTAL_0.5-": "-0.5", "TOTAL_1-": "-1", "TOTAL_1.5-": "-1.5",
            "TOTAL_2-": "-2", "TOTAL_2.5-": "-2.5", "TOTAL_3-": "-3",
            "EQ1_0.5+": "+0.5", "EQ1_1.5+": "+1.5", "EQ1_2.5+": "+2.5",
            "EQ2_0.5+": "+0.5", "EQ2_1.5+": "+1.5", "EQ2_2.5+": "+2.5",
            "V1_ET_0.5+": "+0.5", "V1_ET_1.5+": "+1.5", "V1_ET_2.5+": "+2.5", "V1_ET_3.5+": "+3",
            "1X_ET_1.5+": "+1.5", "1X_ET_2.5+": "+2.5", "1X_ET_3.5+": "+3",
            "V2_ET_0.5+": "+0.5", "V2_ET_1.5+": "+1.5", "V2_ET_2.5+": "+2.5", "V2_ET_3.5+": "+3",
            "2X_ET_1.5+": "+1.5", "2X_ET_2.5+": "+2.5", "2X_ET_3.5+": "+3",
            "AU_MOINS_0.5": "AU_MOINS_0.5", "AU_MOINS_1.5": "AU_MOINS_1.5",
            "AU_MOINS_2.5": "AU_MOINS_2.5", "AU_MOINS_3.5": "AU_MOINS_3.5",
            "V1_T1_0.5+": "+0.5", "V1_T1_1.5+": "+1.5", "V1_T1_2.5+": "+2.5", "V1_T1_3.5+": "+3",
            "1X_T1_0.5+": "+0.5", "1X_T1_1.5+": "+1.5", "1X_T1_2.5+": "+2.5", "1X_T1_3.5+": "+3",
            "V2_T2_0.5+": "+0.5", "V2_T2_1.5+": "+1.5", "V2_T2_2.5+": "+2.5", "V2_T2_3.5+": "+3",
            "2X_T2_0.5+": "+0.5", "2X_T2_1.5+": "+1.5", "2X_T2_2.5+": "+2.5", "2X_T2_3.5+": "+3",
            "BTTS_OUI": "BTTS_YES", "BTTS_NON": "BTTS_NO"
        }
        key = mapping.get(ptype, "1")
        return preds.get(key, {}).get("confidence", 50)

    def get_fallback_odds(self, ptype):
        if ptype == "V1": return 1.80
        elif ptype == "V2": return 3.50
        elif ptype == "1X": return 1.25
        elif ptype == "2X": return 1.35
        elif "0.5+" in ptype: return 1.10
        elif "1+" in ptype or "1.5+" in ptype: return 1.30
        elif "2+" in ptype or "2.5+" in ptype: return 1.70
        elif "3+" in ptype or "3.5+" in ptype: return 2.50
        elif "0.5-" in ptype: return 4.00
        elif "1.5-" in ptype: return 2.20
        elif "2.5-" in ptype: return 1.55
        elif "3.5-" in ptype: return 1.35
        elif ptype == "BTTS_OUI": return 1.65
        elif ptype == "BTTS_NON": return 1.60
        elif "AU_MOINS" in ptype: return 1.40
        return 1.50

    def get_predictions_from_analysis(self, match, analysis, real_odds=None):
        # Un match dont l'IA n'a pas pu s'occuper (fallback neutre) est
        # exclu plutot que presente avec de faux chiffres personnalises.
        if analysis.get("fallback"):
            return []
        valid = []
        for ptype, label in self.prediction_types.items():
            confidence = self.get_confidence(ptype, analysis)
            estimated = self.get_fallback_odds(ptype)

            if real_odds:
                if ptype == "V1" and "home" in real_odds:
                    estimated = real_odds["home"]
                elif ptype == "V2" and "away" in real_odds:
                    estimated = real_odds["away"]
                elif ptype == "1X" and "home" in real_odds and "draw" in real_odds:
                    estimated = round(1 / (1/real_odds["home"] + 1/real_odds["draw"]), 2)
                elif ptype == "2X" and "away" in real_odds and "draw" in real_odds:
                    estimated = round(1 / (1/real_odds["away"] + 1/real_odds["draw"]), 2)
                elif ptype in ["TOTAL_2.5+", "TOTAL_3+"] and "over_2.5" in real_odds:
                    estimated = real_odds["over_2.5"]
                elif ptype in ["TOTAL_0.5-", "TOTAL_1-", "TOTAL_1.5-", "TOTAL_2-", "TOTAL_2.5-"] and "under_2.5" in real_odds:
                    estimated = real_odds["under_2.5"]
                elif ptype == "TOTAL_1.5+" and "over_2.5" in real_odds:
                    estimated = round(real_odds["over_2.5"] * 0.7, 2)
                elif ptype == "TOTAL_0.5+" and "over_2.5" in real_odds:
                    estimated = round(real_odds["over_2.5"] * 0.5, 2)
                elif ptype.startswith("V1_ET_") and "home" in real_odds:
                    estimated = round(real_odds["home"] * 1.2, 2)
                elif ptype.startswith("V2_ET_") and "away" in real_odds:
                    estimated = round(real_odds["away"] * 1.2, 2)
                elif ptype.startswith("1X_ET_") and "home" in real_odds and "draw" in real_odds:
                    estimated = round((1 / (1/real_odds["home"] + 1/real_odds["draw"])) * 1.3, 2)
                elif ptype.startswith("2X_ET_") and "away" in real_odds and "draw" in real_odds:
                    estimated = round((1 / (1/real_odds["away"] + 1/real_odds["draw"])) * 1.3, 2)
                elif ptype.startswith("EQ1_") and "home" in real_odds:
                    estimated = round(real_odds["home"] * 0.9, 2)
                elif ptype.startswith("EQ2_") and "away" in real_odds:
                    estimated = round(real_odds["away"] * 0.9, 2)

            if confidence >= self.min_confidence:
                valid.append({
                    "match_id": match["id"],
                    "home_team": match["home_team"],
                    "away_team": match["away_team"],
                    "league": match["league"],
                    "type": ptype,
                    "type_name": label,
                    "confidence": confidence,
                    "estimated_odds": estimated
                })
        return valid

    def close(self):
        self.analyzer.close()
