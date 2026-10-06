"""Collecte des données de matchs (TheSportsDB) et statistiques des équipes."""
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

import config

log = logging.getLogger(__name__)

# Statuts TheSportsDB d'un match PAS (ou pas encore) terminé ("NS" = pas commencé,
# "1H"/"2H"/"HT" = en cours...). Le score d'un tel match ne doit jamais être
# enregistré comme résultat : un score en direct figé en base fausserait les stats
# des équipes ET le verdict gagné/perdu de l'historique. Un statut vide/inconnu est
# considéré comme terminé (anciens évènements).
NOT_FINAL_STATUSES = {
    "NS", "NOT STARTED", "TBD", "1H", "2H", "HT", "ET", "BT", "P", "LIVE",
    "IN PLAY", "INT", "SUSP", "PST", "POSTPONED", "CANC", "CANCELLED",
    "ABD", "ABANDONED", "SUSPENDED", "INTERRUPTED",
}

# Matchs qui n'auront pas lieu à la date prévue : jamais proposés dans un combiné.
CALLED_OFF_STATUSES = {"PST", "POSTPONED", "CANC", "CANCELLED", "ABD", "ABANDONED"}


def is_final_status(status):
    return (status or "").strip().upper() not in NOT_FINAL_STATUSES


def parse_score(raw):
    """Score entier, ou None s'il est absent/invalide ("0" reste bien 0)."""
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def event_start(event):
    """Coup d'envoi (datetime UTC) d'un évènement TheSportsDB, ou None."""
    stamp = event.get("strTimestamp")
    try:
        if stamp:
            moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        else:
            day, hour = event.get("dateEvent"), (event.get("strTime") or "")[:8]
            if not day or not hour:
                return None
            moment = datetime.strptime(f"{day} {hour}", "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


class DataCollector:
    REQUEST_TIMEOUT = 10  # secondes

    def __init__(self):
        # Sans variable SPORTSDB_API_KEY, la clé de test publique "3" est utilisée
        # (fonctionnelle mais partagée et limitée).
        self.api_key = config.SPORTSDB_API_KEY
        self.base_url = f"https://www.thesportsdb.com/api/v1/json/{self.api_key}"
        self.leagues = config.LEAGUES
        self.init_database()

    def init_database(self):
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS teams (id INTEGER PRIMARY KEY, name TEXT UNIQUE, league TEXT, elo_rating REAL DEFAULT 1500)')
        cursor.execute('CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY, date TEXT, home_team TEXT, away_team TEXT, home_score INTEGER, away_score INTEGER, league TEXT, season TEXT, status TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS team_stats (id INTEGER PRIMARY KEY, team_name TEXT UNIQUE, matches_played INTEGER, wins INTEGER, draws INTEGER, losses INTEGER, goals_for REAL, goals_against REAL, btts_yes INTEGER, btts_no INTEGER, home_wins INTEGER, home_draws INTEGER, home_losses INTEGER, away_wins INTEGER, away_draws INTEGER, away_losses INTEGER, last_updated TEXT)')
        conn.commit()
        conn.close()

    @staticmethod
    def _current_season():
        """Saison TheSportsDB en cours, format 'AAAA-AAAA' (ex: '2026-2027').
        Les 5 championnats suivis démarrent autour de juillet/août."""
        now = datetime.now()
        if now.month >= 7:
            return f"{now.year}-{now.year + 1}"
        return f"{now.year - 1}-{now.year}"

    # ------------------------------------------------------------------
    # Téléchargement (en parallèle : un appel par championnat et par type)
    # ------------------------------------------------------------------

    def _fetch_events(self, url):
        """Liste d'évènements d'une URL TheSportsDB, ou None en cas d'échec."""
        try:
            response = requests.get(url, timeout=self.REQUEST_TIMEOUT)
            return response.json().get("events") or []
        except Exception as e:
            log.warning("TheSportsDB indisponible (%s) : %s", url.split("/json/")[-1].split("?")[0], e)
            return None

    def _fetch_many(self, urls):
        """{clé: évènements ou None} pour un dict {clé: url}, téléchargés en parallèle."""
        if not urls:
            return {}
        with ThreadPoolExecutor(max_workers=min(8, len(urls)), thread_name_prefix="sportsdb") as pool:
            futures = {key: pool.submit(self._fetch_events, url) for key, url in urls.items()}
            return {key: future.result() for key, future in futures.items()}

    # ------------------------------------------------------------------
    # Enregistrement
    # ------------------------------------------------------------------

    @staticmethod
    def _row(event, league_name):
        status = event.get("strStatus", "") or ""
        hs = parse_score(event.get("intHomeScore"))
        aws = parse_score(event.get("intAwayScore"))
        if not is_final_status(status):
            hs = aws = None
        return (event.get("idEvent"), event.get("dateEvent", ""), event.get("strHomeTeam", ""),
                event.get("strAwayTeam", ""), hs, aws, league_name, event.get("strSeason", ""), status)

    @staticmethod
    def _upsert(conn, rows):
        # Upsert (et non INSERT OR IGNORE) : le dernier état connu gagne, mais un
        # score déjà connu n'est jamais effacé par un champ vide.
        conn.executemany(
            'INSERT INTO matches (id, date, home_team, away_team, home_score, away_score, league, season, status) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) '
            'ON CONFLICT(id) DO UPDATE SET '
            'date = excluded.date, home_team = excluded.home_team, away_team = excluded.away_team, '
            'home_score = COALESCE(excluded.home_score, matches.home_score), '
            'away_score = COALESCE(excluded.away_score, matches.away_score), '
            'league = excluded.league, season = excluded.season, status = excluded.status',
            rows)

    def save_match(self, event, league_name):
        if event.get("idEvent") in (None, ""):
            return
        conn = sqlite3.connect(config.DB_PATH)
        try:
            self._upsert(conn, [self._row(event, league_name)])
            conn.commit()
        except sqlite3.Error as e:
            log.error("sauvegarde match impossible : %s", e)
        finally:
            conn.close()

    def collect_all_data(self):
        """Télécharge les derniers résultats de chaque championnat, puis recalcule
        les statistiques des équipes. Renvoie {championnat: nombre de matchs lus}."""
        season = self._current_season()
        urls = {}
        for league_name, league_id in self.leagues.items():
            urls[(league_name, "past")] = f"{self.base_url}/eventspastleague.php?id={league_id}"
            # eventspastleague ne renvoie qu'une poignée des tout derniers résultats.
            # Sur une base fraîchement réinitialisée, ça laisse presque toutes les
            # équipes sous 4 matchs connus (confiance plafonnée à 60 % : aucun
            # pronostic ne passe). On récupère donc aussi tous les matchs déjà joués
            # de la saison en cours, en un seul appel par championnat.
            urls[(league_name, "season")] = f"{self.base_url}/eventsseason.php?id={league_id}&s={season}"
        fetched = self._fetch_many(urls)

        summary = {name: 0 for name in self.leagues}
        conn = sqlite3.connect(config.DB_PATH)
        try:
            for (league_name, kind), events in fetched.items():
                rows = []
                for event in events or []:
                    if event.get("idEvent") in (None, ""):
                        continue
                    # eventsseason renvoie aussi les matchs pas encore joués : on ne
                    # garde que ceux avec un score.
                    if kind == "season" and (parse_score(event.get("intHomeScore")) is None
                                             or parse_score(event.get("intAwayScore")) is None):
                        continue
                    rows.append(self._row(event, league_name))
                if rows:
                    self._upsert(conn, rows)
                    summary[league_name] += len(rows)
            conn.commit()
            # UNION des deux colonnes : une équipe qui n'a été que « extérieur » dans
            # les données stockées serait sinon oubliée et jamais mise à jour.
            teams = [row[0] for row in conn.execute(
                "SELECT DISTINCT home_team FROM matches UNION SELECT DISTINCT away_team FROM matches")]
            for team_name in teams:
                self.update_team_stats(team_name, conn=conn)
            conn.commit()
        finally:
            conn.close()
        log.info("Collecte terminée : %s", ", ".join(f"{k} {v}" for k, v in summary.items()))
        return summary

    def update_team_stats(self, team_name, conn=None):
        own_conn = conn is None
        if own_conn:
            conn = sqlite3.connect(config.DB_PATH)
        try:
            matches = conn.execute(
                'SELECT home_team, away_team, home_score, away_score FROM matches '
                'WHERE (home_team = ? OR away_team = ?) AND home_score IS NOT NULL AND away_score IS NOT NULL',
                (team_name, team_name)).fetchall()
            if not matches:
                return
            stats = {"matches_played": 0, "wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0,
                     "btts_yes": 0, "btts_no": 0, "home_wins": 0, "home_draws": 0, "home_losses": 0,
                     "away_wins": 0, "away_draws": 0, "away_losses": 0}
            for home, away, hs, aws in matches:
                stats["matches_played"] += 1
                at_home = team_name == home
                own, other = (hs, aws) if at_home else (aws, hs)
                side = "home" if at_home else "away"
                stats["goals_for"] += own
                stats["goals_against"] += other
                if own > other:
                    stats["wins"] += 1
                    stats[f"{side}_wins"] += 1
                elif own == other:
                    stats["draws"] += 1
                    stats[f"{side}_draws"] += 1
                else:
                    stats["losses"] += 1
                    stats[f"{side}_losses"] += 1
                if hs > 0 and aws > 0:
                    stats["btts_yes"] += 1
                else:
                    stats["btts_no"] += 1
            stats["goals_for"] = round(stats["goals_for"] / stats["matches_played"], 2)
            stats["goals_against"] = round(stats["goals_against"] / stats["matches_played"], 2)
            conn.execute(
                'INSERT OR REPLACE INTO team_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (None, team_name, stats["matches_played"], stats["wins"], stats["draws"], stats["losses"],
                 stats["goals_for"], stats["goals_against"], stats["btts_yes"], stats["btts_no"],
                 stats["home_wins"], stats["home_draws"], stats["home_losses"],
                 stats["away_wins"], stats["away_draws"], stats["away_losses"],
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            if own_conn:
                conn.commit()
        finally:
            if own_conn:
                conn.close()

    # ------------------------------------------------------------------
    # Matchs à venir
    # ------------------------------------------------------------------

    def get_upcoming_matches(self, now=None):
        """Les prochains matchs de chaque championnat (MATCHS_PAR_CHAMPIONNAT par
        championnat), hors matchs reportés/annulés et hors matchs déjà commencés."""
        now = now or datetime.now(timezone.utc)
        urls = {name: f"{self.base_url}/eventsnextleague.php?id={league_id}"
                for name, league_id in self.leagues.items()}
        fetched = self._fetch_many(urls)
        upcoming, seen = [], set()
        for league_name in self.leagues:                 # ordre stable des championnats
            count = 0
            for event in fetched.get(league_name) or []:
                if count >= config.MATCHS_PAR_CHAMPIONNAT:
                    break
                match_id = event.get("idEvent")
                if match_id in (None, "") or match_id in seen:
                    continue
                if (event.get("strStatus") or "").strip().upper() in CALLED_OFF_STATUSES:
                    continue
                start = event_start(event)
                if start is not None and start <= now:
                    continue
                seen.add(match_id)
                upcoming.append({
                    "id": match_id,
                    "date": f"{event.get('dateEvent', '')} {event.get('strTime') or ''}".strip(),
                    "kickoff": start.isoformat() if start else None,
                    "home_team": event.get("strHomeTeam", ""),
                    "away_team": event.get("strAwayTeam", ""),
                    "league": league_name,
                })
                count += 1
        return upcoming
