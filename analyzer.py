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
        
        prompt = f"""Analyse ces matchs. Pour CHACUN donne toutes ces probabilites (0-100) :
prediction (1/N/2), confidence, total_0_5_plus, total_1_5_plus, total_2_5_plus, total_3_5_plus,
total_0_5_moins, total_1_5_moins, total_2_5_moins, total_3_5_moins, btts_oui, btts_non,
eq1_0_5_plus, eq2_0_5_plus, au_moins_une_marque_0_5, au_moins_une_marque_1_5, au_moins_une_marque_2_5, au_moins_une_marque_3_5

MATCHS :
{chr(10).join(match_list)}

JSON uniquement : {{"analyses": [{{"prediction":"1","confidence":70,"total_0_5_plus":95,"total_1_5_plus":80,"total_2_5_plus":60,"total_3_5_plus":35,"total_0_5_moins":5,"total_1_5_moins":20,"total_2_5_moins":40,"total_3_5_moins":65,"btts_oui":55,"btts_non":45,"eq1_0_5_plus":85,"eq2_0_5_plus":75,"au_moins_une_marque_0_5":92,"au_moins_une_marque_1_5":78,"au_moins_une_marque_2_5":58,"au_moins_une_marque_3_5":38}}, ...]}}"""

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
        
        analysis["predictions"]["1"] = {"confidence": conf if pred == "1" else 30, "details": {}}
        analysis["predictions"]["2"] = {"confidence": conf if pred == "2" else 30, "details": {}}
        analysis["predictions"]["1X"] = {"confidence": conf if pred in ["1", "N"] else 45, "details": {}}
        analysis["predictions"]["2X"] = {"confidence": conf if pred in ["2", "N"] else 45, "details": {}}
        
        analysis["predictions"]["+0.5"] = {"confidence": int(ia_data.get("total_0_5_plus", 90)), "details": {}}
        analysis["predictions"]["+1"] = {"confidence": int(ia_data.get("total_1_5_plus", 70)), "details": {}}
        analysis["predictions"]["+1.5"] = {"confidence": int(ia_data.get("total_1_5_plus", 70)), "details": {}}
        analysis["predictions"]["+2"] = {"confidence": int(ia_data.get("total_2_5_plus", 55)), "details": {}}
        analysis["predictions"]["+2.5"] = {"confidence": int(ia_data.get("total_2_5_plus", 55)), "details": {}}
        analysis["predictions"]["+3"] = {"confidence": int(ia_data.get("total_3_5_plus", 35)), "details": {}}
        
        analysis["predictions"]["-0.5"] = {"confidence": int(ia_data.get("total_0_5_moins", 10)), "details": {}}
        analysis["predictions"]["-1"] = {"confidence": int(ia_data.get("total_1_5_moins", 30)), "details": {}}
        analysis["predictions"]["-1.5"] = {"confidence": int(ia_data.get("total_1_5_moins", 30)), "details": {}}
        analysis["predictions"]["-2"] = {"confidence": int(ia_data.get("total_2_5_moins", 45)), "details": {}}
        analysis["predictions"]["-2.5"] = {"confidence": int(ia_data.get("total_2_5_moins", 45)), "details": {}}
        analysis["predictions"]["-3"] = {"confidence": int(ia_data.get("total_3_5_moins", 65)), "details": {}}
        
        analysis["predictions"]["BTTS_YES"] = {"confidence": int(ia_data.get("btts_oui", 50)), "details": {}}
        analysis["predictions"]["BTTS_NO"] = {"confidence": int(ia_data.get("btts_non", 50)), "details": {}}
        
        analysis["predictions"]["AU_MOINS_0.5"] = {"confidence": int(ia_data.get("au_moins_une_marque_0_5", 92)), "details": {}}
        analysis["predictions"]["AU_MOINS_1.5"] = {"confidence": int(ia_data.get("au_moins_une_marque_1_5", 78)), "details": {}}
        analysis["predictions"]["AU_MOINS_2.5"] = {"confidence": int(ia_data.get("au_moins_une_marque_2_5", 58)), "details": {}}
        analysis["predictions"]["AU_MOINS_3.5"] = {"confidence": int(ia_data.get("au_moins_une_marque_3_5", 38)), "details": {}}
        
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
