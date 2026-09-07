import sqlite3
import math
import anthropic
from datetime import datetime

class MatchAnalyzer:
    def __init__(self):
        self.db = 'triple_elite.db'
        self.conn = sqlite3.connect(self.db)
        self.cursor = self.conn.cursor()
        self.client = anthropic.Anthropic(api_key="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")

    def get_team_stats(self, team_name):
        self.cursor.execute("SELECT * FROM team_stats WHERE team_name = ?", (team_name,))
        result = self.cursor.fetchone()
        if not result:
            return None
        return {
            "matches_played": int(result[2] or 0),
            "wins": int(result[3] or 0),
            "draws": int(result[4] or 0),
            "losses": int(result[5] or 0),
            "goals_for_avg": float(result[6] or 0),
            "goals_against_avg": float(result[7] or 0),
            "btts_yes": int(result[8] or 0),
            "btts_no": int(result[9] or 0),
            "home_wins": int(result[10] or 0),
            "home_draws": int(result[11] or 0),
            "home_losses": int(result[12] or 0),
            "away_wins": int(result[13] or 0),
            "away_draws": int(result[14] or 0),
            "away_losses": int(result[15] or 0)
        }

    def analyze_match(self, home_team, away_team):
        analysis = {"home_team": home_team, "away_team": away_team, "predictions": {}}
        
        # Appel IA
        try:
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=200,
                messages=[{
                    "role": "user",
                    "content": f"Match de football : {home_team} vs {away_team}. Reponds UNIQUEMENT avec : 1 (victoire domicile), N (nul), ou 2 (victoire exterieur). Pas de phrase."
                }]
            )
            ia_result = response.content[0].text.strip()
            print(f"IA reponse: {ia_result}")
        except Exception as e:
            print(f"IA erreur: {e}")
            ia_result = None
        
        # Si IA dit victoire domicile
        if ia_result == "1":
            analysis["predictions"]["1"] = {"confidence": 75, "details": {"ia": True}}
            analysis["predictions"]["1X"] = {"confidence": 85, "details": {"ia": True}}
            analysis["predictions"]["+2.5"] = {"confidence": 55, "details": {}}
            analysis["predictions"]["BTTS_YES"] = {"confidence": 50, "details": {}}
            analysis["predictions"]["BTTS_NO"] = {"confidence": 50, "details": {}}
        elif ia_result == "N":
            analysis["predictions"]["1"] = {"confidence": 35, "details": {}}
            analysis["predictions"]["1X"] = {"confidence": 60, "details": {"ia": True}}
            analysis["predictions"]["+2.5"] = {"confidence": 50, "details": {}}
            analysis["predictions"]["BTTS_YES"] = {"confidence": 50, "details": {}}
            analysis["predictions"]["BTTS_NO"] = {"confidence": 50, "details": {}}
        elif ia_result == "2":
            analysis["predictions"]["1"] = {"confidence": 30, "details": {}}
            analysis["predictions"]["1X"] = {"confidence": 45, "details": {}}
            analysis["predictions"]["+2.5"] = {"confidence": 50, "details": {}}
            analysis["predictions"]["BTTS_YES"] = {"confidence": 50, "details": {}}
            analysis["predictions"]["BTTS_NO"] = {"confidence": 50, "details": {}}
        else:
            # Fallback statistique
            analysis["predictions"]["1"] = {"confidence": 60, "details": {}}
            analysis["predictions"]["1X"] = {"confidence": 70, "details": {}}
            analysis["predictions"]["+2.5"] = {"confidence": 55, "details": {}}
            analysis["predictions"]["BTTS_YES"] = {"confidence": 50, "details": {}}
            analysis["predictions"]["BTTS_NO"] = {"confidence": 50, "details": {}}
        
        return analysis

    def close(self):
        self.conn.close()
