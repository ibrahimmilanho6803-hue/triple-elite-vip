import os
import sqlite3
import json
import anthropic


class MatchAnalyzer:
    # En dessous de ces tailles d'echantillon (matchs joues), on plafonne la
    # confiance affichee : on ne peut pas etre "sur a 90%" d'un pronostic sur
    # une equipe dont on n'a presque pas d'historique. Ce plafond s'applique
    # au minimum des deux equipes (le maillon le plus faible).
    CONFIDENCE_CAP_BY_SAMPLE = [
        (15, 90),  # 15 matchs ou plus -> jusqu'a 90%
        (8, 82),
        (4, 72),
        (0, 60),   # moins de 4 matchs connus -> jamais plus de 60%
    ]

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

    def _confidence_cap(self, home_team, away_team):
        """Plafond de confiance base sur la quantite de donnees reellement
        disponibles pour les deux equipes (le maillon le plus faible)."""
        home_stats = self.get_team_stats(home_team)
        away_stats = self.get_team_stats(away_team)
        sample = min(
            home_stats["matches_played"] if home_stats else 0,
            away_stats["matches_played"] if away_stats else 0,
        )
        for threshold, cap in self.CONFIDENCE_CAP_BY_SAMPLE:
            if sample >= threshold:
                return cap
        return self.CONFIDENCE_CAP_BY_SAMPLE[-1][1]

    @staticmethod
    def _clamp(value, low=1, high=99):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 50
        return max(low, min(high, value))

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

Sois honnete sur l'incertitude : si les donnees manquent ou sont partagees,
n'hesite pas a donner des probabilites proches de 50 plutot que des valeurs
extremes injustifiees.

MATCHS :
{chr(10).join(match_list)}

Reponds UNIQUEMENT avec un JSON valide commencant par [ et finissant par ]"""
        try:
            response = self.client.messages.create(
                model="claude-sonnet-5",
                max_tokens=8000,
                timeout=60.0,
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
            ia_data = json.loads(ia_text)
            print(f"IA parse OK: {len(ia_data)} analyses")
            return ia_data
        except Exception as e:
            print(f"IA erreur: {e}")
            return []

    def build_analysis_from_ia(self, home_team, away_team, ia_data):
        cap = self._confidence_cap(home_team, away_team)

        def c(key, default):
            return min(self._clamp(ia_data.get(key, default)), cap)

        analysis = {"home_team": home_team, "away_team": away_team, "predictions": {}}
        v1 = c("v1", 50)
        v2 = c("v2", 30)
        x1 = c("1x", 60)
        x2 = c("2x", 50)
        over05 = c("over_0_5", 90)
        over15 = c("over_1_5", 75)
        over25 = c("over_2_5", 55)
        over35 = c("over_3_5", 35)
        under05 = c("under_0_5", 10)
        under15 = c("under_1_5", 25)
        under25 = c("under_2_5", 45)
        under35 = c("under_3_5", 65)
        au05 = c("au_moins_1_marque_0_5", 92)
        au15 = c("au_moins_1_marque_1_5", 75)
        au25 = c("au_moins_1_marque_2_5", 55)
        au35 = c("au_moins_1_marque_3_5", 35)
        btts_oui = c("btts_oui", 50)
        btts_non = c("btts_non", 50)
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
        """Analyse de secours SANS IA. N'est utilisee que si Claude n'a pas
        pu analyser ce match du tout. Renvoie des valeurs neutres et
        deliberement peu confiantes plutot que de simuler une vraie analyse :
        mieux vaut l'exclure des combines que d'afficher un faux pourcentage
        de fiabilite a un client."""
        analysis = {"home_team": home_team, "away_team": away_team, "predictions": {}, "fallback": True}
        neutral_low = 45
        for key in ["1", "2", "1X", "2X", "+0.5", "+1", "+1.5", "+2", "+2.5", "+3",
                    "-0.5", "-1", "-1.5", "-2", "-2.5", "-3", "BTTS_YES", "BTTS_NO",
                    "AU_MOINS_0.5", "AU_MOINS_1.5", "AU_MOINS_2.5", "AU_MOINS_3.5"]:
            analysis["predictions"][key] = {"confidence": neutral_low, "details": {}}
        return analysis

    def close(self):
        self.conn.close()
