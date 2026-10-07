"""Faux services et données de démonstration partagés par les tests du site (Flask et navigateur)."""
import time
from datetime import datetime, timedelta, timezone

import license_manager
from markets import describe_prediction

EMAIL = "client@exemple.com"
KEY = "a1b2c3d4e5f60718"


class FakeLicenses:
    """Même interface que LicenseManager pour ce que le site utilise."""

    def __init__(self):
        self.records = {EMAIL: {"key": KEY, "expires": license_manager._utcnow() + timedelta(days=23, hours=5), "active": True}}
        self.down = False
        self.calls = 0

    def _record(self, email):
        return self.records.get(license_manager.normalize_email(email))

    def get_status(self, email):
        self.calls += 1
        if self.down:
            return {"state": "error", "expires": None}
        rec = self._record(email)
        if not rec:
            return {"state": "unknown", "expires": None}
        if not rec["active"]:
            return {"state": "inactive", "expires": rec["expires"]}
        if rec["expires"] < license_manager._utcnow():
            return {"state": "expired", "expires": rec["expires"]}
        return {"state": "active", "expires": rec["expires"]}

    @staticmethod
    def _invalid(detail):
        return {"ok": False, "reason": "invalid", "expires": None, "detail": detail,
                "message": "E-mail ou clé de licence incorrect."}

    def check_login(self, email, key):
        if self.down:
            return {"ok": False, "reason": "unavailable", "expires": None, "message": "Service momentanément indisponible. Réessaie dans un instant."}
        rec = self._record(email)
        if not rec:
            return self._invalid("e-mail inconnu")
        if (key or "").strip().lower() != rec["key"]:
            return self._invalid("clé différente (essai)")
        if not rec["active"]:
            return {"ok": False, "reason": "inactive", "expires": rec["expires"], "message": "Licence désactivée. Contacte-nous : tripleelitevip@gmail.com"}
        if rec["expires"] < license_manager._utcnow():
            return {"ok": False, "reason": "expired", "expires": rec["expires"],
                    "message": f"Ton abonnement a expiré le {rec['expires'].strftime('%d/%m/%Y')}. Renouvelle-le pour retrouver l'accès."}
        return {"ok": True, "reason": "ok", "message": "Licence valide", "expires": rec["expires"]}


# --------------------------------------------------------------------------
# Données de démonstration
# --------------------------------------------------------------------------

def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def leg(home, away, league, code, odds, conf, kickoff, source="estimee", match_id=1):
    return {"match_id": str(match_id), "home_team": home, "away_team": away, "league": league, "kickoff": _iso(kickoff),
            "type": code, "type_name": describe_prediction(code, home, away), "category": "X", "confidence": conf,
            "probability": float(conf), "estimated_odds": odds, "odds_source": source}


def sample_combos(now=None):
    now = now or datetime.now(timezone.utc)
    sat = now + timedelta(days=2, hours=3)
    sun = now + timedelta(days=3, hours=1)
    combos = [
        {"predictions": [
            leg("Arsenal", "Brighton", "Premier League", "1X", 1.33, 70, sat, match_id=1),
            leg("Inter", "Torino", "Serie A", "TOTAL_1.5+", 1.36, 68, sat + timedelta(hours=2), match_id=2),
            leg("Real Sociedad", "Getafe", "La Liga", "EQ1_0.5+", 1.41, 66, sun, match_id=3)],
         "total_odds": 2.55, "avg_confidence": 68.0, "success_probability": 31.4, "leagues": [], "categories": []},
        {"predictions": [
            leg("Bayern Munich", "Mainz", "Bundesliga", "TOTAL_2.5+", 1.38, 69, sat, "bookmakers", 4),
            leg("Lille", "Nantes", "Ligue 1", "1X", 1.35, 67, sun, "bookmakers", 5),
            leg("Chelsea", "Burnley", "Premier League", "V1", 1.45, 66, sun + timedelta(hours=2), "estimee", 6)],
         "total_odds": 2.7, "avg_confidence": 67.3, "success_probability": 30.4, "leagues": [], "categories": []},
        {"predictions": [
            leg("Atlético Madrid", "Osasuna", "La Liga", "V1_ET_1.5+", 1.62, 66, sat + timedelta(hours=5), match_id=7),
            leg("Napoli", "Hellas Vérone", "Serie A", "BTTS_NON", 1.5, 65, sat + timedelta(hours=1), match_id=8),
            leg("Borussia Dortmund", "Union Berlin", "Bundesliga", "TOTAL_3.5-", 1.38, 70, sun, match_id=9)],
         "total_odds": 3.35, "avg_confidence": 67.0, "success_probability": 30.1, "leagues": [], "categories": []},
    ]
    return combos


def sample_history(now=None):
    now = now or datetime.now(timezone.utc)

    def hleg(home, away, league, code, odds, conf, kickoff, score, outcome, source="estimee"):
        item = leg(home, away, league, code, odds, conf, kickoff, source)
        for k in ("match_id", "category", "probability", "type"):
            item.pop(k, None)
        item.update({"score": score, "outcome": outcome})
        return item

    day = now - timedelta(days=3)
    return {
        "summary": {"won": 2, "lost": 3, "pending": 1, "void": 1, "combos_settled": 5, "combo_win_rate": 40,
                    "legs_won": 12, "legs_lost": 6, "leg_win_rate": 67},
        "combos": [
            {"generated_at": _iso(now - timedelta(hours=1)), "total_odds": 2.61, "avg_confidence": 68.0, "success_probability": 31.0,
             "status": "pending", "predictions": [
                 hleg("Arsenal", "Brighton", "Premier League", "1X", 1.33, 70, now + timedelta(days=1), None, None),
                 hleg("Inter", "Torino", "Serie A", "TOTAL_1.5+", 1.36, 68, now + timedelta(days=1), None, None),
                 hleg("Real Sociedad", "Getafe", "La Liga", "EQ1_0.5+", 1.41, 66, now + timedelta(days=2), None, None)]},
            {"generated_at": _iso(day), "total_odds": 2.7, "avg_confidence": 68.0, "success_probability": 31.0,
             "status": "won", "predictions": [
                 hleg("Bayern Munich", "Mainz", "Bundesliga", "TOTAL_2.5+", 1.38, 69, day + timedelta(hours=20), "3 - 1", "won"),
                 hleg("Lille", "Nantes", "Ligue 1", "1X", 1.35, 67, day + timedelta(hours=22), "1 - 1", "won"),
                 hleg("Chelsea", "Burnley", "Premier League", "V1", 1.45, 66, day + timedelta(hours=24), "2 - 0", "won", "bookmakers")]},
            {"generated_at": _iso(day - timedelta(days=4)), "total_odds": 2.58, "avg_confidence": 68.0, "success_probability": 31.0,
             "status": "lost", "predictions": [
                 hleg("Atlético Madrid", "Osasuna", "La Liga", "V1_ET_1.5+", 1.62, 66, day - timedelta(days=3), "1 - 0", "lost"),
                 hleg("Napoli", "Hellas Vérone", "Serie A", "BTTS_NON", 1.5, 65, day - timedelta(days=3), "1 - 0", "won"),
                 hleg("Borussia Dortmund", "Union Berlin", "Bundesliga", "TOTAL_3.5-", 1.38, 70, day - timedelta(days=2), "2 - 1", "won")]},
            {"generated_at": _iso(day - timedelta(days=9)), "total_odds": 2.52, "avg_confidence": 67.0, "success_probability": 30.0,
             "status": "void", "predictions": [
                 hleg("Lyon", "Monaco", "Ligue 1", "1X", 1.3, 70, day - timedelta(days=8), None, "void"),
                 hleg("Roma", "Lazio", "Serie A", "TOTAL_1.5+", 1.35, 68, day - timedelta(days=8), "1 - 1", "won"),
                 hleg("Betis", "Valence", "La Liga", "2X", 1.45, 66, day - timedelta(days=7), "0 - 0", "won")]},
        ],
    }


def make_pipeline(delay=0.25, combos=None, error=None):
    """Chaîne de génération simulée : quelques étapes avec progression, puis les combinés."""
    state = {"calls": 0}

    def pipeline(progress=None):
        state["calls"] += 1
        steps = [("collecte", 5, "Collecte des derniers résultats…"), ("matchs", 15, "Recherche des prochains matchs…"),
                 ("analyse", 40, "Analyse des matchs par l'IA (1/5)…"), ("analyse", 80, "Analyse des matchs par l'IA (5/5)…"),
                 ("pronostics", 82, "Calcul des pronostics et des cotes…"), ("composition", 92, "Composition des combinés…")]
        for step in steps:
            if progress:
                progress(*step)
            time.sleep(delay)
        if error:
            raise error
        return {"combos": combos if combos is not None else sample_combos(),
                "meta": {"matches_total": 15, "matches_analyzed": 15, "leagues": ["a", "b", "c", "d", "e"],
                         "real_odds_legs": 3, "total_legs": 9, "duration_seconds": 18.2}}

    pipeline.state = state
    return pipeline
