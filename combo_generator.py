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
            "BTTS_OUI": "Les deux equipes marquent OUI",
            "BTTS_NON": "Les deux equipes marquent NON",
            "AU_MOINS_0.5": "Au moins une equipe marque plus de 0.5 but",
            "AU_MOINS_1.5": "Au moins une equipe marque plus de 1.5 buts",
            "AU_MOINS_2.5": "Au moins une equipe marque plus de 2.5 buts",
            "AU_MOINS_3.5": "Au moins une equipe marque plus de 3.5 buts"
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
                home_api = match.get("home_team", "").lower()
                away_api = match.get("away_team", "").lower()
                home_search = home_team.lower()
                away_search = away_team.lower()
                if (home_search in home_api or home_api in home_search) and (away_search in away_api or away_api in away_search):
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
                    return odds
            return None
        except Exception as e:
            print(f"Erreur odds: {e}")
            return None

    def get_match_predictions(self, match):
        real_odds = self.get_real_odds(match["home_team"], match["away_team"])
        analysis = self.analyzer.analyze_match(match["home_team"], match["away_team"], real_odds)
        valid = []
        for ptype, label in self.prediction_types.items():
            confidence = self.get_confidence(ptype, analysis)
            estimated = self.get_odds_for_type(ptype, real_odds)
            if estimated is None:
                estimated = self.get_fallback_odds(ptype)
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

    def get_confidence(self, ptype, analysis):
        preds = analysis.get("predictions", {})
        if ptype == "V1":
            return preds.get("1", {}).get("confidence", 50)
        elif ptype == "V2":
            return preds.get("2", {}).get("confidence", 50)
        elif ptype == "1X":
            return preds.get("1X", {}).get("confidence", 50)
        elif ptype == "2X":
            return preds.get("2X", {}).get("confidence", 50)
        elif ptype.startswith("BTTS"):
            key = "BTTS_YES" if ptype == "BTTS_OUI" else "BTTS_NO"
            return preds.get(key, {}).get("confidence", 50)
        elif "0.5+" in ptype or "1.5+" in ptype or "2.5+" in ptype or "3.5+" in ptype:
            return preds.get("+2.5", {}).get("confidence", 50)
        elif "0.5-" in ptype or "1.5-" in ptype or "2.5-" in ptype or "3.5-" in ptype:
            return preds.get("-2.5", {}).get("confidence", 50)
        return 50

    def get_odds_for_type(self, ptype, real_odds):
        if not real_odds:
            return None
        if ptype == "V1" and "home" in real_odds:
            return real_odds["home"]
        if ptype == "V2" and "away" in real_odds:
            return real_odds["away"]
        if ptype == "1X" and "home" in real_odds and "draw" in real_odds:
            return round(1 / (1/real_odds["home"] + 1/real_odds["draw"]), 2)
        if ptype == "2X" and "away" in real_odds and "draw" in real_odds:
            return round(1 / (1/real_odds["away"] + 1/real_odds["draw"]), 2)
        if ptype == "BTTS_OUI" and "btts_yes" in real_odds:
            return real_odds["btts_yes"]
        if ptype == "BTTS_NON" and "btts_no" in real_odds:
            return real_odds["btts_no"]
        if "0.5+" in ptype and "over_0.5" in real_odds:
            return real_odds["over_0.5"]
        if "1.5+" in ptype and "over_1.5" in real_odds:
            return real_odds["over_1.5"]
        if "2.5+" in ptype and "over_2.5" in real_odds:
            return real_odds["over_2.5"]
        if "3.5+" in ptype and "over_3.5" in real_odds:
            return real_odds["over_3.5"]
        if "0.5-" in ptype and "under_0.5" in real_odds:
            return real_odds["under_0.5"]
        if "1.5-" in ptype and "under_1.5" in real_odds:
            return real_odds["under_1.5"]
        if "2.5-" in ptype and "under_2.5" in real_odds:
            return real_odds["under_2.5"]
        if "3.5-" in ptype and "under_3.5" in real_odds:
            return real_odds["under_3.5"]
        return None

    def get_fallback_odds(self, ptype):
        if ptype == "V1":
            return 1.80
        elif ptype == "V2":
            return 3.50
        elif ptype == "1X":
            return 1.25
        elif ptype == "2X":
            return 1.35
        elif "0.5+" in ptype:
            return 1.10
        elif "1+" in ptype or "1.5+" in ptype:
            return 1.30
        elif "2+" in ptype or "2.5+" in ptype:
            return 1.70
        elif "3+" in ptype or "3.5+" in ptype:
            return 2.50
        elif "0.5-" in ptype:
            return 4.00
        elif "1.5-" in ptype:
            return 2.20
        elif "2.5-" in ptype:
            return 1.55
        elif "3.5-" in ptype:
            return 1.35
        elif ptype == "BTTS_OUI":
            return 1.65
        elif ptype == "BTTS_NON":
            return 1.60
        elif "AU_MOINS" in ptype:
            return 1.40
        return 1.50

        def get_predictions_from_analysis(self, match, analysis, real_odds=None):
        valid = []
        for ptype, label in self.prediction_types.items():
            confidence = 50
            estimated = self.get_fallback_odds(ptype)
            
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
                confidence = analysis["predictions"].get("2X", {}).get("confidence", 50)
            elif ptype == "BTTS_OUI":
                confidence = analysis["predictions"].get("BTTS_YES", {}).get("confidence", 50)
            elif ptype == "BTTS_NON":
                confidence = analysis["predictions"].get("BTTS_NO", {}).get("confidence", 50)
            elif "2.5+" in ptype:
                confidence = analysis["predictions"].get("+2.5", {}).get("confidence", 50)
            
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
