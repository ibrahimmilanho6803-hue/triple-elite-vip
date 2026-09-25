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
            "btts_no": int(result[9] or 0)
        }

    def get_recent_form(self, team_name, limit=5):
        self.cursor.execute('''
            SELECT home_team, away_team, home_score, away_score, date
            FROM matches
            WHERE (home_team = ? OR away_team = ?)
            AND home_score IS NOT NULL
            ORDER BY date DESC
            LIMIT ?
        ''', (team_name, team_name, limit))
        results = self.cursor.fetchall()
        form = []
        for home, away, hs, aws, date in results:
            if team_name == home:
                if hs > aws:
                    form.append(f"V {home} {hs}-{aws} {away}")
                elif hs == aws:
                    form.append(f"N {home} {hs}-{aws} {away}")
                else:
                    form.append(f"D {home} {hs}-{aws} {away}")
            else:
                if aws > hs:
                    form.append(f"V {away} {aws}-{hs} {home}")
                elif aws == hs:
                    form.append(f"N {away} {aws}-{hs} {home}")
                else:
                    form.append(f"D {away} {aws}-{hs} {home}")
        return form

    def get_h2h(self, home_team, away_team, limit=5):
        self.cursor.execute('''
            SELECT home_team, away_team, home_score, away_score, date
            FROM matches
            WHERE ((home_team = ? AND away_team = ?) OR (home_team = ? AND away_team = ?))
            AND home_score IS NOT NULL
            ORDER BY date DESC
            LIMIT ?
        ''', (home_team, away_team, away_team, home_team, limit))
        results = self.cursor.fetchall()
        h2h = []
        for home, away, hs, aws, date in results:
            h2h.append(f"{home} {hs}-{aws} {away}")
        return h2h

    def analyze_multiple_matches(self, matches):
        match_list = []
        for m in matches:
            home_stats = self.get_team_stats(m["home_team"])
            away_stats = self.get_team_stats(m["away_team"])
            home_form = self.get_recent_form(m["home_team"])
            away_form = self.get_recent_form(m["away_team"])
            h2h = self.get_h2h(m["home_team"], m["away_team"])
            home_txt = f"{home_stats['wins']}V{home_stats['draws']}N{home_stats['losses']}D" if home_stats else "N/A"
            away_txt = f"{away_stats['wins']}V{away_stats['draws']}N{away_stats['losses']}D" if away_stats else "N/A"
            match_txt = f"""
=== {m['home_team']} vs {m['away_team']} ({m['league']}) ===
{home_txt} (dom) vs {away_txt} (ext)
FORME {m['home_team']} : {', '.join(home_form) if home_form else 'N/A'}
FORME {m['away_team']} : {', '.join(away_form) if away_form else 'N/A'}
H2H : {', '.join(h2h) if h2h else 'N/A'}
"""
            match_list.append(match_txt)
        prompt = f"""Tu es un analyste football expert. Analyse chaque match avec attention en te basant sur :
- La forme recente (5 derniers matchs)
- Les confrontations directes (H2H)
- Les stats de la saison

Pour CHACUN donne ces probabilites (0-100) :
home_team, away_team, v1, v2, 1x, 2x,
over_0_5, over_1_5, over_2_5, over_3_5,
under_0_5, under_1_5, under_2_5, under_3_5,
eq1_over_0_5, eq2_over_0_5,
au_moins_1_marque_0_5, au_moins_1_marque_1_5, au_moins_1_marque_2_5, au_moins_1_marque_3_5,
btts_oui, btts_non

MATCHS :
{chr(10).join(match_list)}

Reponds UNIQUEMENT avec un JSON valide commencant par [ et finissant par ]"""
        try:
            response = self.client.messages.create(
                model="claude-sonnet-5",
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}]
            )
            ia_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    ia_text += block.text
            ia_text = ia_text.strip()
            ia_text = ia_text.replace("```json", "").replace("```", "").strip()
            start = ia_text.find("[")
            end = ia_text.rfind("]")
            if start >= 0 and end > start:
                ia_text = ia_text[start:end+1]
            print(f"IA nettoye: {ia_text[:500]}")
            ia_data = json.loads(ia_text)
            print(f"IA parse OK: {len(ia_data)} analyses")
            return ia_data
        except Exception as e:
            print(f"IA erreur: {e}")
            return []

    def build_analysis_from_ia(self, home_team, away_team, ia_data):
        analysis = {"home_team": home_team, "away_team": away_team, "predictions": {}}
        v1 = int(ia_data.get("v1", 50))
        v2 = int(ia_data.get("v2", 30))
        x1 = int(ia_data.get("1x", 60))
        x2 = int(ia_data.get("2x", 50))
        over05 = int(ia_data.get("over_0_5", 90))
        over15 = int(ia_data.get("over_1_5", 75))
        over25 = int(ia_data.get("over_2_5", 55))
        over35 = int(ia_data.get("over_3_5", 35))
        under05 = int(ia_data.get("under_0_5", 10))
        under15 = int(ia_data.get("under_1_5", 25))
        under25 = int(ia_data.get("under_2_5", 45))
        under35 = int(ia_data.get("under_3_5", 65))
        eq1_05 = int(ia_data.get("eq1_over_0_5", 80))
        eq2_05 = int(ia_data.get("eq2_over_0_5", 70))
        au05 = int(ia_data.get("au_moins_1_marque_0_5", 92))
        au15 = int(ia_data.get("au_moins_1_marque_1_5", 75))
        au25 = int(ia_data.get("au_moins_1_marque_2_5", 55))
        au35 = int(ia_data.get("au_moins_1_marque_3_5", 35))
        btts_oui = int(ia_data.get("btts_oui", 50))
        btts_non = int(ia_data.get("btts_non", 50))
        analysis["predictions"]["1"] = {"confidence": v1, "details": {}}
        analysis["predictions"]["2"] = {"confidence": v2, "details": {}}
        analysis["predictions"]["1X"] = {"confidence": x1, "details": {}}
        analysis["predictions"]["2X"] = {"confidence": x2, "details": {}}
        analysis["predictions"]["+0.5"] = {"confidence": over05, "details": {}}
        analysis["predictions"]["+1"] = {"confidence": over15, "details": {}}
        analysis["predictions"]["+1.5"] = {"confidence": over15, "details": {}}
        analysis["predictions"]["+2"] = {"confidence": over25, "details": {}}
        analysis["predictions"]["+2.5"] = {"confidence": over25, "details": {}}
        analysis["predictions"]["+3"] = {"confidence": over35, "details": {}}
        analysis["predictions"]["-0.5"] = {"confidence": under05, "details": {}}
        analysis["predictions"]["-1"] = {"confidence": under15, "details": {}}
        analysis["predictions"]["-1.5"] = {"confidence": under15, "details": {}}
        analysis["predictions"]["-2"] = {"confidence": under25, "details": {}}
        analysis["predictions"]["-2.5"] = {"confidence": under25, "details": {}}
        analysis["predictions"]["-3"] = {"confidence": under35, "details": {}}
        analysis["predictions"]["BTTS_YES"] = {"confidence": btts_oui, "details": {}}
        analysis["predictions"]["BTTS_NO"] = {"confidence": btts_non, "details": {}}
        analysis["predictions"]["AU_MOINS_0.5"] = {"confidence": au05, "details": {}}
        analysis["predictions"]["AU_MOINS_1.5"] = {"confidence": au15, "details": {}}
        analysis["predictions"]["AU_MOINS_2.5"] = {"confidence": au25, "details": {}}
        analysis["predictions"]["AU_MOINS_3.5"] = {"confidence": au35, "details": {}}
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
