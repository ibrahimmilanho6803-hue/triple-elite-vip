import sqlite3
import math
from datetime import datetime

class MatchAnalyzer:
    def __init__(self):
        self.db = 'triple_elite.db'
        self.conn = sqlite3.connect(self.db)
        self.cursor = self.conn.cursor()

    def get_team_elo(self, team_name):
        self.cursor.execute("SELECT elo_rating FROM teams WHERE name = ?", (team_name,))
        result = self.cursor.fetchone()
        return result[0] if result else 1500

    def get_team_stats(self, team_name):
        self.cursor.execute("SELECT * FROM team_stats WHERE team_name = ?", (team_name,))
        result = self.cursor.fetchone()
        if not result:
            return None
        return {
            "matches_played": result[1], "wins": result[2], "draws": result[3],
            "losses": result[4], "goals_for_avg": result[5], "goals_against_avg": result[6],
            "btts_yes": result[7], "btts_no": result[8], "home_wins": result[9],
            "home_draws": result[10], "home_losses": result[11],
            "away_wins": result[12], "away_draws": result[13], "away_losses": result[14]
        }

    def analyze_match(self, home_team, away_team):
        analysis = {
            "home_team": home_team,
            "away_team": away_team,
            "predictions": {}
        }

        # Calculer la confiance pour chaque type de pronostic
        # Toujours donner au moins 50% de confiance de base
        base_confidence = 50

        # Victoire à domicile (1)
        home_stats = self.get_team_stats(home_team)
        away_stats = self.get_team_stats(away_team)
        
        if home_stats and away_stats:
            home_win_rate = (home_stats["wins"] / max(1, home_stats["matches_played"])) * 100
            away_loss_rate = (away_stats["losses"] / max(1, away_stats["matches_played"])) * 100
            confidence_1 = base_confidence + (home_win_rate - away_loss_rate) / 2
        else:
            confidence_1 = base_confidence

        analysis["predictions"]["1"] = {
            "confidence": max(35, min(95, round(confidence_1))),
            "details": {}
        }

        # Double chance (1X)
        analysis["predictions"]["1X"] = {
            "confidence": min(95, max(40, round(confidence_1) + 15)),
            "details": {}
        }

        # Plus de 2.5 buts
        if home_stats and away_stats:
            total_goals = home_stats["goals_for_avg"] + away_stats["goals_for_avg"]
            confidence_over = base_confidence + (total_goals - 2.0) * 15
        else:
            confidence_over = base_confidence
        analysis["predictions"]["+2.5"] = {
            "confidence": max(35, min(90, round(confidence_over))),
            "details": {}
        }

        # BTTS OUI
        if home_stats and away_stats:
            btts_rate = ((home_stats["btts_yes"] / max(1, home_stats["matches_played"])) + 
                         (away_stats["btts_yes"] / max(1, away_stats["matches_played"]))) / 2 * 100
            confidence_btts_yes = base_confidence + (btts_rate - 50) / 2
        else:
            confidence_btts_yes = base_confidence
        analysis["predictions"]["BTTS_YES"] = {
            "confidence": max(35, min(90, round(confidence_btts_yes))),
            "details": {}
        }

        # BTTS NON
        analysis["predictions"]["BTTS_NO"] = {
            "confidence": max(35, min(90, round(100 - confidence_btts_yes))),
            "details": {}
        }

        analysis["elo_diff"] = self.get_team_elo(home_team) - self.get_team_elo(away_team)
        analysis["predicted_goals"] = (home_stats["goals_for_avg"] + away_stats["goals_against_avg"]) / 2 if home_stats and away_stats else 2.5
        analysis["btts_probability"] = round(confidence_btts_yes, 1)

        return analysis

    def close(self):
        self.conn.close()