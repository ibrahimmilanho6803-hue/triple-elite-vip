from data_collector import DataCollector
from analyzer import MatchAnalyzer

class ComboGenerator:
    def __init__(self):
        self.collector = DataCollector()
        self.analyzer = MatchAnalyzer()
        self.min_confidence = 65
        self.base_odds = {
            "1": {"min": 1.15, "max": 1.80},
            "1X": {"min": 1.05, "max": 1.25},
            "+2.5": {"min": 1.40, "max": 2.00},
            "BTTS_YES": {"min": 1.55, "max": 2.10},
            "BTTS_NO": {"min": 1.50, "max": 2.00}
        }

    def estimate_odds(self, ptype, conf):
        base = self.base_odds.get(ptype, {"min": 1.50, "max": 1.80})
        if conf >= 85:
            return base["min"]
        elif conf >= 75:
            return round(base["min"] + (base["max"] - base["min"]) * 0.25, 2)
        elif conf >= 65:
            return round(base["min"] + (base["max"] - base["min"]) * 0.5, 2)
        else:
            return round(base["max"], 2)

    def get_prediction_name(self, ptype):
        names = {
            "1": "Victoire a domicile",
            "1X": "Double chance domicile",
            "+2.5": "Plus de 2.5 buts",
            "BTTS_YES": "Les 2 equipes marquent OUI",
            "BTTS_NO": "Les 2 equipes marquent NON"
        }
        return names.get(ptype, ptype)

    def get_match_predictions(self, match):
        analysis = self.analyzer.analyze_match(match["home_team"], match["away_team"])
        valid = []
        for ptype, data in analysis["predictions"].items():
            if data["confidence"] >= self.min_confidence:
                valid.append({
                    "match_id": match["id"],
                    "home_team": match["home_team"],
                    "away_team": match["away_team"],
                    "league": match["league"],
                    "type": ptype,
                    "type_name": self.get_prediction_name(ptype),
                    "confidence": data["confidence"],
                    "estimated_odds": self.estimate_odds(ptype, data["confidence"])
                })
        return valid

    def close(self):
        self.analyzer.close()