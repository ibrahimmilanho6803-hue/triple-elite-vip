import os
import sqlite3
import json
import anthropic

class MatchAnalyzer:
    def __init__(self):
        self.db = 'triple_elite.db'
        self.conn = sqlite3.connect(self.db)
        self.cursor = self.conn.cursor()
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

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

    def analyze_multiple_matches(self, matches):
        match_list = []
        for m in matches:
            home_stats = self.get_team_stats(m["home_team"])
            away_stats = self.get_team_stats(m["away_team"])
            home_txt = f"{home_stats['wins']}V{home_stats['draws']}N{home_stats['losses']}D" if home_stats else "N/A"
            away_txt = f"{away_stats['wins']}V{away_stats['draws']}N{away_stats['losses']}D" if away_stats else "N/A"
            match_list.append(f"{m['home_team']} (dom, {home_txt}) vs {m['away_team']} (ext, {away_txt}) [{m['league']}]")
        
        prompt = f"""Analyse ces matchs. Pour CHACUN donne : home_team, away_team, prediction (1/N/2), confidence, total_2_5_plus, btts_oui.

MATCHS :
{chr(10).join(match_list)}

JSON uniquement : {{"analyses": [{{"home_team":"Equipe1","away_team":"Equipe2","prediction":"1","confidence":70,"total_2_5_plus":55,"btts_oui":60}}, ...]}}"""

        try:
            response = self.client.messages.create(
                model="claude-sonnet-5",
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}]
            )
            ia_text = response.content[0].text.strip()
            print(f"IA reponse: {ia_text[:300]}")
            ia_text = ia_text.replace("```json", "").replace("```", "").strip()
            ia_data = json.loads(ia_text)
            return ia_data.get("analyses", [])
        except Exception as e:
            print(f"IA erreur: {e}")
            return []

    def build_analysis_from_ia(self, home_team, away_team, ia_data):
        analysis = {"home_team": home_team, "away_team": away_team, "predictions": {}}
        pred = ia_data.get("prediction", "1")
        conf = int(ia_data.get("confidence", 60))
        over25 = int(ia_data.get("total_2_5_plus", 55))
        btts = int(ia_data.get("btts_oui", 50))
        
        analysis["predictions"]["1"] = {"confidence": conf if pred == "1" else 30, "details": {}}
        analysis["predictions"]["2"] = {"confidence": conf if pred == "2" else 30, "details": {}}
        analysis["predictions"]["1X"] = {"confidence": conf if pred in ["1", "N"] else 45, "details": {}}
        analysis["predictions"]["2X"] = {"confidence": conf if pred in ["2", "N"] else 45, "details": {}}
        
        analysis["predictions"]["+0.5"] = {"confidence": 90, "details": {}}
        analysis["predictions"]["+1"] = {"confidence": min(95, over25 + 20), "details": {}}
        analysis["predictions"]["+1.5"] = {"confidence": min(95, over25 + 15), "details": {}}
        analysis["predictions"]["+2"] = {"confidence": over25, "details": {}}
        analysis["predictions"]["+2.5"] = {"confidence": over25, "details": {}}
        analysis["predictions"]["+3"] = {"confidence": max(10, over25 - 20), "details": {}}
        
        analysis["predictions"]["-0.5"] = {"confidence": 10, "details": {}}
        analysis["predictions"]["-1"] = {"confidence": max(5, 100 - over25 - 20), "details": {}}
        analysis["predictions"]["-1.5"] = {"confidence": max(5, 100 - over25 - 15), "details": {}}
        analysis["predictions"]["-2"] = {"confidence": 100 - over25, "details": {}}
        analysis["predictions"]["-2.5"] = {"confidence": 100 - over25, "details": {}}
        analysis["predictions"]["-3"] = {"confidence": min(90, 100 - over25 + 20), "details": {}}
        
        analysis["predictions"]["BTTS_YES"] = {"confidence": btts, "details": {}}
        analysis["predictions"]["BTTS_NO"] = {"confidence": 100 - btts, "details": {}}
        
        analysis["predictions"]["AU_MOINS_0.5"] = {"confidence": 92, "details": {}}
        analysis["predictions"]["AU_MOINS_1.5"] = {"confidence": min(95, over25 + 25), "details": {}}
        analysis["predictions"]["AU_MOINS_2.5"] = {"confidence": over25, "details": {}}
        analysis["predictions"]["AU_MOINS_3.5"] = {"confidence": max(10, over25 - 20), "details": {}}
        
        return analysis

    def analyze_match(self, home_team, away_team, real_odds=None):
        analysis = {"home_team": home_team, "away_team": away_team, "predictions": {}}
        analysis["predictions"]["1"] = {"confidence": 60, "details": {}}
        analysis["predictions"]["2"] = {"confidence": 40, "details": {}}
        analysis["predictions"]["1X"] = {"confidence": 70, "details": {}}
        analysis["predictions"]["2X"] = {"confidence": 50, "details": {}}
        analysis["predictions"]["+0.5"] = {"confidence": 90, "details": {}}
        analysis["predictions"]["+1"] = {"confidence": 70, "details": {}}
        analysis["predictions"]["+1.5"] = {"confidence": 70, "details": {}}
        analysis["predictions"]["+2"] = {"confidence": 55, "details": {}}
        analysis["predictions"]["+2.5"] = {"confidence": 55, "details": {}}
        analysis["predictions"]["+3"] = {"confidence": 35, "details": {}}
        analysis["predictions"]["-0.5"] = {"confidence": 10, "details": {}}
        analysis["predictions"]["-1"] = {"confidence": 30, "details": {}}
        analysis["predictions"]["-1.5"] = {"confidence": 30, "details": {}}
        analysis["predictions"]["-2"] = {"confidence": 45, "details": {}}
        analysis["predictions"]["-2.5"] = {"confidence": 45, "details": {}}
        analysis["predictions"]["-3"] = {"confidence": 65, "details": {}}
        analysis["predictions"]["BTTS_YES"] = {"confidence": 50, "details": {}}
        analysis["predictions"]["BTTS_NO"] = {"confidence": 50, "details": {}}
        analysis["predictions"]["AU_MOINS_0.5"] = {"confidence": 92, "details": {}}
        analysis["predictions"]["AU_MOINS_1.5"] = {"confidence": 78, "details": {}}
        analysis["predictions"]["AU_MOINS_2.5"] = {"confidence": 58, "details": {}}
        analysis["predictions"]["AU_MOINS_3.5"] = {"confidence": 38, "details": {}}
        return analysis

    def close(self):
        self.conn.close()
