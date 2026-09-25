import requests
from data_collector import DataCollector
from analyzerv2 import MatchAnalyzer

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

    def get_real_odds(self, home_team, away_team):
        # Chercher le match sur 1xBet via Apify
        # Note : il faut d'abord trouver l'URL du match sur 1xBet
        # Pour l'instant, on utilise The-Odds-API en fallback
        
        # Essayer Apify (nécessite l'URL du match)
        # match_url = self.find_1xbet_url(home_team, away_team)
        # if match_url:
        #     odds = get_1xbet_odds(match_url)
        #     if odds:
        #         return odds
        
        # Fallback : The-Odds-API
        return self.get_the_odds_api(home_team, away_team)
    
    def get_real_odds(self, home_team, away_team):
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
                    home_api = match.get("home_team", "").lower()
                    away_api = match.get("away_team", "").lower()
                    if (home_team.lower() in home_api or home_api in home_team.lower()) and (away_team.lower() in away_api or away_api in away_team.lower()):
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
                            print(f"ODDS TROUVEES pour {home_team} vs {away_team}: {odds}")
                            return odds
            print(f"Pas de cotes trouvees pour {home_team} vs {away_team}")
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
        valid = []
        for ptype, label in self.prediction_types.items():
            confidence = self.get_confidence(ptype, analysis)
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

    def close(self):
        self.analyzer.close()
