import requests
import sqlite3
from datetime import datetime
import time

import config


class DataCollector:
    REQUEST_TIMEOUT = 10  # secondes

    def __init__(self):
        # SECURITE : une cle TheSportsDB payante etait codee en dur ici et
        # poussee sur un depot public. Utilise SPORTSDB_API_KEY (Render) pour
        # ta propre cle ; sans variable definie, la cle de test publique "3"
        # est utilisee (fonctionnelle mais partagee/limitee).
        self.api_key = config.SPORTSDB_API_KEY
        self.base_url = f"https://www.thesportsdb.com/api/v1/json/{self.api_key}"
        self.leagues = config.LEAGUES
        self.init_database()

    def init_database(self):
        conn = sqlite3.connect('triple_elite.db')
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS teams (id INTEGER PRIMARY KEY, name TEXT UNIQUE, league TEXT, elo_rating REAL DEFAULT 1500)')
        cursor.execute('CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY, date TEXT, home_team TEXT, away_team TEXT, home_score INTEGER, away_score INTEGER, league TEXT, season TEXT, status TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS team_stats (id INTEGER PRIMARY KEY, team_name TEXT UNIQUE, matches_played INTEGER, wins INTEGER, draws INTEGER, losses INTEGER, goals_for REAL, goals_against REAL, btts_yes INTEGER, btts_no INTEGER, home_wins INTEGER, home_draws INTEGER, home_losses INTEGER, away_wins INTEGER, away_draws INTEGER, away_losses INTEGER, last_updated TEXT)')
        # NOTE : on ne fait plus de DROP TABLE team_stats ici. L'ancienne
        # version supprimait puis recreait la table a CHAQUE collecte, ce qui
        # pouvait la laisser vide le temps du recalcul si une autre requete
        # arrivait en meme temps. update_team_stats() fait un INSERT OR
        # REPLACE cible par equipe, donc plus besoin de tout effacer.
        conn.commit()
        conn.close()

    @staticmethod
    def _current_season():
        """Saison TheSportsDB en cours, format 'AAAA-AAAA' (ex: '2026-2027').
        Les 5 championnats suivis demarrent autour de juillet/aout."""
        now = datetime.now()
        if now.month >= 7:
            return f"{now.year}-{now.year + 1}"
        return f"{now.year - 1}-{now.year}"

    def collect_all_data(self):
        print("Collecte des donnees...")
        season = self._current_season()
        for league_name, league_id in self.leagues.items():
            url = f"{self.base_url}/eventspastleague.php?id={league_id}"
            try:
                response = requests.get(url, timeout=self.REQUEST_TIMEOUT)
                events = response.json().get("events") or []
                for event in events:
                    self.save_match(event, league_name)
                # Pause courte entre chaque championnat : simple politesse
                # envers l'API (surtout utile avec la cle de test partagee).
                # Avec plus de championnats suivis, une pause de 2s devenait
                # couteuse en temps d'attente pour le client ; reduite car ce
                # bloc ne tourne de toute facon qu'au plus une fois par
                # CACHE_MINUTES grace au cache de generation.
                time.sleep(0.5)
            except Exception as e:
                print(f"Erreur {league_name}: {e}")

            # eventspastleague.php ne renvoie qu'une poignee des tout derniers
            # resultats (verifie : 1 seul evenement recupere en test). Sur une
            # base tout juste reinitialisee (nouveau deploiement -> disque non
            # persistant sur Render, donc table 'matches' vide), ca laisse la
            # quasi-totalite des equipes avec moins de 4 matchs connus, ce qui
            # plafonne leur confiance a 60% (MatchAnalyzer.CONFIDENCE_CAP_BY_
            # SAMPLE) -- sous le seuil MIN_CONFIDENCE=65, donc AUCUN pronostic
            # ne passe, meme quand l'IA a parfaitement repondu (constate : 15
            # analyses IA valides, 0 pronostic retenu). On recupere en plus
            # tous les matchs deja joues de la saison en cours en un seul
            # appel, pour qu'une equipe ayant dispute plusieurs journees ait
            # tout de suite un historique suffisant, sans attendre des jours
            # (ou un prochain redemarrage qui remettrait tout a zero).
            season_url = f"{self.base_url}/eventsseason.php?id={league_id}&s={season}"
            try:
                response = requests.get(season_url, timeout=self.REQUEST_TIMEOUT)
                events = response.json().get("events") or []
                for event in events:
                    # eventsseason renvoie aussi les matchs pas encore joues :
                    # on ne garde que ceux avec un score, pour ne pas creer une
                    # ligne "vide" qu'un futur INSERT OR IGNORE ne pourra plus
                    # jamais completer avec le vrai resultat.
                    hs, aws = event.get("intHomeScore"), event.get("intAwayScore")
                    if hs not in (None, "") and aws not in (None, ""):
                        self.save_match(event, league_name)
                time.sleep(0.5)
            except Exception as e:
                print(f"Erreur saison {league_name}: {e}")
        conn = sqlite3.connect('triple_elite.db')
        cursor = conn.cursor()
        # UNION des deux colonnes : une equipe qui n'a encore ete que "exterieur"
        # dans les donnees stockees (frequent juste apres une reinitialisation)
        # etait sinon oubliee ici, et donc jamais mise a jour dans team_stats.
        cursor.execute("SELECT DISTINCT home_team FROM matches UNION SELECT DISTINCT away_team FROM matches")
        teams = cursor.fetchall()
        conn.close()
        for (team_name,) in teams:
            self.update_team_stats(team_name)
        print("Collecte terminee")

    def save_match(self, event, league_name):
        conn = sqlite3.connect('triple_elite.db')
        cursor = conn.cursor()
        try:
            hs_raw = event.get("intHomeScore")
            aws_raw = event.get("intAwayScore")
            try:
                hs = int(hs_raw) if hs_raw else None
            except (TypeError, ValueError):
                hs = None
            try:
                aws = int(aws_raw) if aws_raw else None
            except (TypeError, ValueError):
                aws = None
            cursor.execute(
                'INSERT OR IGNORE INTO matches (id, date, home_team, away_team, home_score, away_score, league, season, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (event.get("idEvent"), event.get("dateEvent", ""), event.get("strHomeTeam", ""), event.get("strAwayTeam", ""), hs, aws, league_name, event.get("strSeason", ""), event.get("strStatus", ""))
            )
        except Exception as e:
            print(f"Erreur sauvegarde match: {e}")
        conn.commit()
        conn.close()

    def update_team_stats(self, team_name):
        conn = sqlite3.connect('triple_elite.db')
        cursor = conn.cursor()
        cursor.execute('SELECT home_team, away_team, home_score, away_score FROM matches WHERE (home_team = ? OR away_team = ?) AND home_score IS NOT NULL', (team_name, team_name))
        matches = cursor.fetchall()
        if not matches:
            conn.close()
            return
        stats = {"matches_played": 0, "wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0, "btts_yes": 0, "btts_no": 0, "home_wins": 0, "home_draws": 0, "home_losses": 0, "away_wins": 0, "away_draws": 0, "away_losses": 0}
        for home, away, hs, aws in matches:
            stats["matches_played"] += 1
            if team_name == home:
                stats["goals_for"] += hs or 0
                stats["goals_against"] += aws or 0
                if hs is not None and aws is not None:
                    if hs > aws:
                        stats["wins"] += 1
                        stats["home_wins"] += 1
                    elif hs == aws:
                        stats["draws"] += 1
                        stats["home_draws"] += 1
                    else:
                        stats["losses"] += 1
                        stats["home_losses"] += 1
                if hs and aws and hs > 0 and aws > 0:
                    stats["btts_yes"] += 1
                else:
                    stats["btts_no"] += 1
            else:
                stats["goals_for"] += aws or 0
                stats["goals_against"] += hs or 0
                if hs is not None and aws is not None:
                    if aws > hs:
                        stats["wins"] += 1
                        stats["away_wins"] += 1
                    elif aws == hs:
                        stats["draws"] += 1
                        stats["away_draws"] += 1
                    else:
                        stats["losses"] += 1
                        stats["away_losses"] += 1
                if hs and aws and hs > 0 and aws > 0:
                    stats["btts_yes"] += 1
                else:
                    stats["btts_no"] += 1
        if stats["matches_played"] > 0:
            stats["goals_for"] = round(stats["goals_for"] / stats["matches_played"], 2)
            stats["goals_against"] = round(stats["goals_against"] / stats["matches_played"], 2)
        cursor.execute(
            'INSERT OR REPLACE INTO team_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (None, team_name, stats["matches_played"], stats["wins"], stats["draws"], stats["losses"], stats["goals_for"], stats["goals_against"], stats["btts_yes"], stats["btts_no"], stats["home_wins"], stats["home_draws"], stats["home_losses"], stats["away_wins"], stats["away_draws"], stats["away_losses"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
        conn.close()

    def get_upcoming_matches(self):
        upcoming = []
        for league_name, league_id in self.leagues.items():
            url = f"{self.base_url}/eventsnextleague.php?id={league_id}"
            try:
                response = requests.get(url, timeout=self.REQUEST_TIMEOUT)
                events = response.json().get("events") or []
                count = 0
                for event in events:
                    if count >= config.MATCHS_PAR_CHAMPIONNAT:
                        break
                    upcoming.append({
                        "id": event.get("idEvent"),
                        "date": event.get("dateEvent", "") + " " + event.get("strTime", "15:00"),
                        "home_team": event.get("strHomeTeam", ""),
                        "away_team": event.get("strAwayTeam", ""),
                        "league": league_name,
                    })
                    count += 1
                time.sleep(0.3)
            except Exception as e:
                print(f"Erreur {league_name}: {e}")
        return upcoming
