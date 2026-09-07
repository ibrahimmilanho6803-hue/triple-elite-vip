import requests
from data_collector import DataCollector
from analyzer import MatchAnalyzer

ODDS_API_KEY = "d3ac58acb0852fe1dcda7fc30aecadc7"

class ComboGenerator:
    def __init__(self):
        self.collector = DataCollector()
        self.analyzer = MatchAnalyzer()
        self.min_confidence = 50

    def get_real_odds(self, home_team, away_team):
        try:
            url = "https://api.the-odds-api.com/v4/sports/soccer/odds"
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": "eu",
                "markets": "h2h,totals,btts"
            }
            response = requests.get(url, params=params)
            data = response.json()
            
            for match in data:
                if home_team.lower() in match["home_team"].lower() and away_team.lower() in match["away_team"].lower():
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
        for ptype, data in analysis["predictions"].items():
            if data["confidence"] >= self.min_confidence:
                estimated = None
                
                # Utiliser les cotes réelles si disponibles
                if real_odds:
                    if ptype == "1" and "home" in real_odds:
                        estimated = real_odds["home"]
                    elif ptype == "1X" and "home" in real_odds and "draw" in real_odds:
                        estimated = round(1 / (1/real_odds["home"] + 1/real_odds["draw"]), 2)
                    elif ptype == "+2.5" and "over_2_5" in real_odds:
                        estimated = real_odds["over_2_5"]
                    elif ptype == "BTTS_YES" and "btts_yes" in real_odds:
                        estimated = real_odds["btts_yes"]
                    elif ptype == "BTTS_NO" and "btts_no" in real_odds:
                        estimated = real_odds["btts_no"]
                
                # Fallback si pas de cote réelle
                if estimated is None:
                    if ptype == "1":
                        estimated = 1.80
                    elif ptype == "1X":
                        estimated = 1.25
                    elif ptype == "+2.5":
                        estimated = 1.70
                    elif ptype == "BTTS_YES":
                        estimated = 1.65
                    elif ptype == "BTTS_NO":
                        estimated = 1.60
                    else:
                        estimated = 1.50
                
                valid.append({
                    "match_id": match["id"],
                    "home_team": match["home_team"],
                    "away_team": match["away_team"],
                    "league": match["league"],
                    "type": ptype,
                    "type_name": self.get_prediction_name(ptype),
                    "confidence": data["confidence"],
                    "estimated_odds": estimated
                })
        return valid

    def get_prediction_name(self, ptype):
        names = {
            "1": "Victoire a domicile",
            "1X": "Double chance domicile",
            "+2.5": "Plus de 2.5 buts",
            "BTTS_YES": "Les 2 equipes marquent OUI",
            "BTTS_NO": "Les 2 equipes marquent NON"
        }
        return names.get(ptype, ptype)

    def close(self):
        self.analyzer.close()
