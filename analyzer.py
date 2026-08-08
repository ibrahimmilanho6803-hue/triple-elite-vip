import sqlite3
import math
from datetime import datetime, timedelta

class MatchAnalyzer:
    def __init__(self):
        self.db = 'triple_elite.db'
        self.conn = sqlite3.connect(self.db)
        self.cursor = self.conn.cursor()
    
    def get_team_elo(self, team_name):
        """Récupère le classement Elo d'une équipe"""
        self.cursor.execute(
            "SELECT elo_rating FROM teams WHERE name = ?", 
            (team_name,)
        )
        result = self.cursor.fetchone()
        return result[0] if result else 1500
    
    def get_team_stats(self, team_name):
        """Récupère toutes les statistiques d'une équipe"""
        self.cursor.execute(
            "SELECT * FROM team_stats WHERE team_name = ?",
            (team_name,)
        )
        result = self.cursor.fetchone()
        
        if not result:
            return None
        
        return {
            "matches_played": result[1],
            "wins": result[2],
            "draws": result[3],
            "losses": result[4],
            "goals_for_avg": result[5],
            "goals_against_avg": result[6],
            "btts_yes": result[7],
            "btts_no": result[8],
            "home_wins": result[9],
            "home_draws": result[10],
            "home_losses": result[11],
            "away_wins": result[12],
            "away_draws": result[13],
            "away_losses": result[14]
        }
    
    def get_recent_form(self, team_name, matches_count=5):
        """Analyse la forme récente d'une équipe"""
        self.cursor.execute('''
            SELECT home_team, away_team, home_score, away_score, date
            FROM matches
            WHERE (home_team = ? OR away_team = ?)
            AND home_score IS NOT NULL
            ORDER BY date DESC
            LIMIT ?
        ''', (team_name, team_name, matches_count))
        
        matches = self.cursor.fetchall()
        
        form = {
            "points": 0,
            "goals_scored": 0,
            "goals_conceded": 0,
            "results": [],
            "avg_goals_per_match": 0
        }
        
        for match in matches:
            home, away, home_score, away_score, date = match
            
            if team_name == home:
                scored = home_score or 0
                conceded = away_score or 0
                
                if home_score > away_score:
                    form["points"] += 3
                    form["results"].append("V")
                elif home_score == away_score:
                    form["points"] += 1
                    form["results"].append("N")
                else:
                    form["results"].append("D")
            else:
                scored = away_score or 0
                conceded = home_score or 0
                
                if away_score > home_score:
                    form["points"] += 3
                    form["results"].append("V")
                elif away_score == home_score:
                    form["points"] += 1
                    form["results"].append("N")
                else:
                    form["results"].append("D")
            
            form["goals_scored"] += scored
            form["goals_conceded"] += conceded
        
        if matches_count > 0 and len(matches) > 0:
            form["avg_goals_per_match"] = round(
                (form["goals_scored"] + form["goals_conceded"]) / len(matches), 2
            )
        
        return form
    
    def get_h2h_stats(self, team1, team2, limit=5):
        """Statistiques des confrontations directes"""
        self.cursor.execute('''
            SELECT home_team, away_team, home_score, away_score, date
            FROM matches
            WHERE (home_team = ? AND away_team = ?)
            OR (home_team = ? AND away_team = ?)
            AND home_score IS NOT NULL
            ORDER BY date DESC
            LIMIT ?
        ''', (team1, team2, team2, team1, limit))
        
        matches = self.cursor.fetchall()
        
        if not matches:
            return None
        
        stats = {
            "matches": len(matches),
            "team1_wins": 0,
            "team2_wins": 0,
            "draws": 0,
            "avg_goals": 0,
            "btts_count": 0
        }
        
        total_goals = 0
        for match in matches:
            home, away, home_score, away_score, date = match
            total_goals += (home_score or 0) + (away_score or 0)
            
            if home == team1:
                if home_score > away_score:
                    stats["team1_wins"] += 1
                elif home_score == away_score:
                    stats["draws"] += 1
                else:
                    stats["team2_wins"] += 1
            else:
                if away_score > home_score:
                    stats["team1_wins"] += 1
                elif away_score == home_score:
                    stats["draws"] += 1
                else:
                    stats["team2_wins"] += 1
            
            if home_score and away_score and home_score > 0 and away_score > 0:
                stats["btts_count"] += 1
        
        stats["avg_goals"] = round(total_goals / len(matches), 2)
        stats["btts_percentage"] = round((stats["btts_count"] / len(matches)) * 100, 1)
        
        return stats
    
    def calculate_poisson_probability(self, team_goals_avg, opponent_goals_avg, target_goals):
        """Calcule la probabilité de Poisson pour les buts"""
        lambda_value = (team_goals_avg + opponent_goals_avg) / 2
        
        probability = (math.exp(-lambda_value) * (lambda_value ** target_goals)) / math.factorial(target_goals)
        return round(probability * 100, 2)
    
    def predict_btts_probability(self, home_team, away_team):
        """Prédit la probabilité que les deux équipes marquent"""
        home_stats = self.get_team_stats(home_team)
        away_stats = self.get_team_stats(away_team)
        
        if not home_stats or not away_stats:
            return 50  # Valeur par défaut
        
        home_attack = home_stats["goals_for_avg"] or 1.0
        home_defense = home_stats["goals_against_avg"] or 1.0
        away_attack = away_stats["goals_for_avg"] or 1.0
        away_defense = away_stats["goals_against_avg"] or 1.0
        
        # Calcul simplifié du BTTS
        home_score_prob = home_attack / (away_defense + 0.5)
        away_score_prob = away_attack / (home_defense + 0.5)
        
        btts_prob = min(home_score_prob * away_score_prob * 35, 90)
        
        return round(btts_prob, 1)
    
    def predict_total_goals(self, home_team, away_team):
        """Prédit le nombre total de buts attendus"""
        home_stats = self.get_team_stats(home_team)
        away_stats = self.get_team_stats(away_team)
        home_form = self.get_recent_form(home_team)
        away_form = self.get_recent_form(away_team)
        
        if not home_stats or not away_stats:
            return 2.5  # Valeur par défaut
        
        goals_avg = (
            home_stats["goals_for_avg"] +
            home_stats["goals_against_avg"] +
            away_stats["goals_for_avg"] +
            away_stats["goals_against_avg"]
        ) / 2
        
        # Ajustement avec la forme récente
        form_adjustment = (home_form["avg_goals_per_match"] + away_form["avg_goals_per_match"]) / 2
        
        predicted_goals = (goals_avg + form_adjustment) / 2
        
        return round(predicted_goals, 2)
    
    def calculate_confidence_score(self, home_team, away_team, prediction_type):
        """
        Calcule un score de confiance pour un pronostic
        Retourne un score de 0 à 100
        """
        score = 0
        details = {}
        
        # 1. Écart Elo (25 points max)
        home_elo = self.get_team_elo(home_team)
        away_elo = self.get_team_elo(away_team)
        elo_diff = home_elo - away_elo
        
        if elo_diff > 200:
            score += 25
        elif elo_diff > 150:
            score += 20
        elif elo_diff > 100:
            score += 15
        elif elo_diff > 50:
            score += 10
        else:
            score += 5
        
        details["elo_diff"] = elo_diff
        details["elo_score"] = min(25, max(5, elo_diff // 8))
        
        # 2. Forme récente à domicile (20 points max)
        home_form = self.get_recent_form(home_team)
        home_form_points = home_form["points"]
        
        if home_form_points >= 13:
            score += 20
        elif home_form_points >= 10:
            score += 15
        elif home_form_points >= 7:
            score += 10
        elif home_form_points >= 4:
            score += 5
        
        details["home_form_points"] = home_form_points
        details["home_form_score"] = min(20, home_form_points * 1.5)
        
        # 3. Forme visiteur à l'extérieur (15 points max)
        away_form = self.get_recent_form(away_team)
        away_form_points = away_form["points"]
        
        if away_form_points <= 2:
            score += 15
        elif away_form_points <= 5:
            score += 10
        elif away_form_points <= 8:
            score += 5
        
        details["away_form_points"] = away_form_points
        
        # 4. Confrontations directes (15 points max)
        h2h = self.get_h2h_stats(home_team, away_team)
        if h2h:
            home_win_rate = (h2h["team1_wins"] / h2h["matches"]) * 100
            if home_win_rate > 70:
                score += 15
            elif home_win_rate > 50:
                score += 10
            elif home_win_rate > 30:
                score += 5
            
            details["h2h_home_win_rate"] = home_win_rate
        
        # 5. Adaptation au type de pronostic (25 points max)
        if prediction_type == "1":
            # Victoire simple
            home_stats = self.get_team_stats(home_team)
            if home_stats:
                home_win_rate = (home_stats["home_wins"] / max(1, home_stats["home_wins"] + home_stats["home_draws"] + home_stats["home_losses"])) * 100
                if home_win_rate > 70:
                    score += 25
                elif home_win_rate > 55:
                    score += 18
                elif home_win_rate > 40:
                    score += 12
                
                details["home_win_rate"] = round(home_win_rate, 1)
        
        elif prediction_type == "+2.5":
            # Plus de 2.5 buts
            predicted_goals = self.predict_total_goals(home_team, away_team)
            if predicted_goals > 3.5:
                score += 25
            elif predicted_goals > 3.0:
                score += 20
            elif predicted_goals > 2.8:
                score += 15
            elif predicted_goals > 2.5:
                score += 10
            
            details["predicted_goals"] = predicted_goals
        
        elif prediction_type == "BTTS_YES":
            # Les deux équipes marquent OUI
            btts_prob = self.predict_btts_probability(home_team, away_team)
            if btts_prob > 75:
                score += 25
            elif btts_prob > 65:
                score += 20
            elif btts_prob > 55:
                score += 15
            
            details["btts_probability"] = btts_prob
        
        elif prediction_type == "BTTS_NO":
            # Les deux équipes marquent NON
            btts_prob = self.predict_btts_probability(home_team, away_team)
            if btts_prob < 30:
                score += 25
            elif btts_prob < 40:
                score += 20
            elif btts_prob < 50:
                score += 15
            
            details["btts_probability"] = btts_prob
        
        elif prediction_type == "1X":
            # Double chance
            score += 20  # Plus sûr, donc bon score de base
            details["note"] = "Double chance - risque réduit"
        
        return min(100, score), details
    
    def analyze_match(self, home_team, away_team):
        """Analyse complète d'un match"""
        analysis = {
            "home_team": home_team,
            "away_team": away_team,
            "predictions": {}
        }
        
        # Calculer tous les types de pronostics
        for pred_type in ["1", "1X", "+2.5", "BTTS_YES", "BTTS_NO"]:
            confidence, details = self.calculate_confidence_score(
                home_team, away_team, pred_type
            )
            
            analysis["predictions"][pred_type] = {
                "confidence": confidence,
                "details": details
            }
        
        # Ajouter les stats globales
        analysis["elo_diff"] = self.get_team_elo(home_team) - self.get_team_elo(away_team)
        analysis["predicted_goals"] = self.predict_total_goals(home_team, away_team)
        analysis["btts_probability"] = self.predict_btts_probability(home_team, away_team)
        
        return analysis
    
    def close(self):
        """Ferme la connexion à la base de données"""
        self.conn.close()

# Test
if __name__ == "__main__":
    analyzer = MatchAnalyzer()
    
    # Test d'analyse
    result = analyzer.analyze_match("Manchester City", "Bournemouth")
    
    print(f"\n📊 ANALYSE : {result['home_team']} vs {result['away_team']}")
    print(f"   Écart Elo : {result['elo_diff']}")
    print(f"   Buts prédits : {result['predicted_goals']}")
    print(f"   Probabilité BTTS : {result['btts_probability']}%")
    print("\n   PRONOSTICS :")
    
    for pred_type, data in result["predictions"].items():
        stars = "⭐" * (data["confidence"] // 20)
        print(f"   {pred_type:10} → Confiance : {data['confidence']}% {stars}")
    
    analyzer.close()