import requests
import sqlite3
from datetime import datetime, timedelta
import time

class DataCollector:
    def __init__(self):
    self.base_url = "https://www.thesportsdb.com/api/v1/json/0531916234"
    self.leagues = {"Premier League": "4328", "La Liga": "4335", "Bundesliga": "4332"}
    self.init_database()

    def init_database(self):
        conn = sqlite3.connect('triple_elite.db')
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY, name TEXT UNIQUE, league TEXT, elo_rating REAL DEFAULT 1500)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY, date TEXT, home_team TEXT, away_team TEXT,
            home_score INTEGER, away_score INTEGER, league TEXT, season TEXT, status TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS team_stats (
            id INTEGER PRIMARY KEY, team_name TEXT UNIQUE, matches_played INTEGER,
            wins INTEGER, draws INTEGER, losses INTEGER, goals_for REAL, goals_against REAL,
            btts_yes INTEGER, btts_no INTEGER, home_wins INTEGER, home_draws INTEGER,
            home_losses INTEGER, away_wins INTEGER, away_draws INTEGER, away_losses INTEGER,
            last_updated TEXT)''')
        conn.commit()
        conn.close()

    def collect_all_data(self):
        print("  Collecte des donnees...")
        for league_name, league_id in self.leagues.items():
            url = f"{self.base_url}/eventspastleague.php?id={league_id}"
            try:
                response = requests.get(url)
                events = response.json().get("events", [])
                print(f"  {league_name}: {len(events)} matchs")
                for event in events:
                    self.save_match(event, league_name)
                time.sleep(2)
            except Exception as e:
                print(f"  Erreur {league_name}: {e}")
        print("  Collecte terminee !")

    def save_match(self, event, league_name):
        conn = sqlite3.connect('triple_elite.db')
        cursor = conn.cursor()
        try:
            hs = int(event.get("intHomeScore", 0)) if event.get("intHomeScore") else None
            aws = int(event.get("intAwayScore", 0)) if event.get("intAwayScore") else None
            cursor.execute('''INSERT OR IGNORE INTO matches 
                (id, date, home_team, away_team, home_score, away_score, league, season, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (
                event.get("idEvent"),
                event.get("dateEvent", ""),
                event.get("strHomeTeam", ""),
                event.get("strAwayTeam", ""),
                hs, aws,
                league_name,
                event.get("strSeason", ""),
                event.get("strStatus", "")
            ))
        except Exception:
            pass
        conn.commit()
        conn.close()

    def get_upcoming_matches(self):
        upcoming = []
        for league_name, league_id in self.leagues.items():
            url = f"{self.base_url}/eventsnextleague.php?id={league_id}"
            try:
                response = requests.get(url)
                events = response.json().get("events", [])
                for event in events:
                    upcoming.append({
                        "id": event.get("idEvent"),
                        "date": event.get("dateEvent", "") + " " + event.get("strTime", "15:00"),
                        "home_team": event.get("strHomeTeam", ""),
                        "away_team": event.get("strAwayTeam", ""),
                        "league": league_name
                    })
                time.sleep(1)
            except Exception as e:
                print(f"  Erreur {league_name}: {e}")
        return upcoming[:20]