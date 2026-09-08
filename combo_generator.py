import requests
from data_collector import DataCollector
from analyzer import MatchAnalyzer

ODDS_API_KEY = "d3ac58acb0852fe1dcda7fc30aecadc7"

class ComboGenerator:
    def __init__(self):
        self.collector = DataCollector()
        self.analyzer = MatchAnalyzer()
        self.min_confidence = 50
        
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
            "V1_ET_0.5+": "V1 et plus de 0.5 but",
            "V1_ET_1.5+": "V1 et plus de 1.5 buts",
            "V1_ET_2.5+": "V1 et plus de 2.5 buts",
            "V1_ET_3.5+": "V1 et plus de 3.5 buts",
            "V1_ET_1.5-": "V1 et moins de 1.5 buts",
            "V1_ET_2.5-": "V1 et moins de 2.5 buts",
            "V1_ET_3.5-": "V1 et moins de 3.5 buts",
            "1X_ET_1.5+": "1X et plus de 1.5 buts",
            "1X_ET_2.5+": "1X et plus de 2.5 buts",
            "1X_ET_3.5+": "1X et plus de 3.5 buts",
            "1X_ET_1.5-": "1X et moins de 1.5 buts",
            "1X_ET_2.5-": "1X et moins de 2.5 buts",
            "1X_ET_3.5-": "1X et moins de 3.5 buts",
            "V2_ET_0.5+": "V2 et plus de 0.5 but",
            "V2_ET_1.5+": "V2 et plus de 1.5 buts",
            "V2_ET_2.5+": "V2 et plus de 2.5 buts",
            "V2_ET_3.5+": "V2 et plus de 3.5 buts",
            "V2_ET_1.5-": "V2 et moins de 1.5 buts",
            "V2_ET_2.5-": "V2 et moins de 2.5 buts",
            "V2_ET_3.5-": "V2 et moins de 3.5 buts",
            "2X_ET_1.5+": "2X et plus de 1.5 buts",
            "2X_ET_2.5+": "2X et plus de 2.5 buts",
            "2X_ET_3.5+": "2X et plus de 3.5 buts",
            "2X_ET_1.5-": "2X et moins de 1.5 buts",
            "2X_ET_2.5-": "2X et moins de 2.5 buts",
            "2X_ET_3.5-": "2X et moins de 3.5 buts",
            "BTTS_OUI": "Les deux equipes marquent",
            "BTTS_NON": "Une equipe ne marque pas"
        }

    def get_real_odds(self, home_team, away_team):
        try:
            url = "https://api.the-odds-api.com/v4/sports/soccer/odds"
            params = {"apiKey": ODDS_API_KEY, "regions": "eu", "markets": "h2h,totals,btts"}
            response = requests.get(url, params=params)
            data = response.json()
            if isinstance(data, dict):
                return None
            for match in data:
                if home_team.lower() in match.get("home_team", "").lower():
                    odds = {}
                    for bookmaker in match.get("bookmakers", []):
                        for market in bookmaker.get("markets", []):
                            if market["key"] == "h2h":
                                for outcome in market["outcomes"]:
                                    if outcome["name"] == home_team:
                                        odds["home"] = outcome["price"]
                                    elif outcome["name"] == away_team:
                                        odds["away"] = outcome["price"]
                                    elif outcome["name"] == "Draw":
                                        odds["draw"] = outcome["price"]
                            elif market["key"] == "totals":
                                for outcome in market["outcomes"]:
                                    if outcome["name"] == "Over" and outcome.get("point") == 2.5:
                                        odds["over_2_5"] = outcome["price"]
                                    elif outcome["name"] == "Under" and outcome.get("point") == 2.5:
                                        odds["under_2_5"] = outcome["price"]
                            elif market["key"] == "btts":
                                for outcome in market["outcomes"]:
                                    if outcome["name"] == "Yes":
                                        odds["btts_yes"] = outcome["price"]
                                    elif outcome["name"] == "No":
                                        odds["btts_no"] = outcome["price"]
                        break
                    return odds
            return None
        except Exception as e:
            print(f"Erreur odds: {e}")
            return None

    def get_match_predictions(self, match):
        analysis = self.analyzer.analyze_match(match["home_team"], match["away_team"])
        real_odds = self.get_real_odds(match["home_team"], match["away_team"])
        valid = []
        
        for ptype, label in self.prediction_types.items():
            confidence = 50  # Base
            estimated = self.get_fallback_odds(ptype)
            
            # Utiliser l'analyse IA et les cotes reelles
            if ptype == "V1":
                confidence = analysis["predictions"].get("1", {}).get("confidence", 50)
                if real_odds and "home" in real_odds:
                    estimated = real_odds["home"]
            elif ptype == "V2":
                confidence = analysis["predictions"].get("2", {}).get("confidence", 50)
                if real_odds and "away" in real_odds:
                    estimated = real_odds["away"]
            elif ptype == "1X":
                confidence = analysis["predictions"].get("1X", {}).get("confidence", 50)
            elif ptype == "2X":
                confidence = 50
            elif ptype == "BTTS_OUI":
                confidence = analysis["predictions"].get("BTTS_YES", {}).get("confidence", 50)
                if real_odds and "btts_yes" in real_odds:
                    estimated = real_odds["btts_yes"]
            elif ptype == "BTTS_NON":
                confidence = analysis["predictions"].get("BTTS_NO", {}).get("confidence", 50)
                if real_odds and "btts_no" in real_odds:
                    estimated = real_odds["btts_no"]
            
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

    def get_fallback_odds(self, ptype):
        if "V1" in ptype:
            return 1.80
        elif "V2" in ptype:
            return 3.50
        elif "1X" in ptype:
            return 1.25
        elif "2X" in ptype:
            return 1.35
        elif "0.5+" in ptype:
            return 1.10
        elif "1.5+" in ptype:
            return 1.30
        elif "2.5+" in ptype:
            return 1.70
        elif "3.5+" in ptype:
            return 2.50
        elif "0.5-" in ptype:
            return 4.00
        elif "1.5-" in ptype:
            return 2.20
        elif "2.5-" in ptype:
            return 1.55
        elif "BTTS_OUI" in ptype:
            return 1.65
        elif "BTTS_NON" in ptype:
            return 1.60
        return 1.50

    def close(self):
        self.analyzer.close()
