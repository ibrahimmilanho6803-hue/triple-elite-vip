import os
import requests

SHARPAPI_KEY = os.environ.get("SHARPAPI_KEY", "VOTRE_CLE_SHARPAPI")

def get_real_odds(home_team, away_team):
    """
    Récupère les cotes réelles de 1xBet via SharpAPI.
    """
    try:
        url = "https://api.sharpapi.io/api/v1/odds"
        params = {
            "sport": "football",
            "market": "moneyline,total,btts"
        }
        headers = {"X-API-Key": SHARPAPI_KEY}
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"Erreur SharpAPI: {response.status_code}")
            return None
        
        data = response.json()
        odds_list = data.get("data", [])
        
        # Chercher le match
        for odd in odds_list:
            home_api = odd.get("home_team", "").lower()
            away_api = odd.get("away_team", "").lower()
            
            if (home_team.lower() in home_api or home_api in home_team.lower()) and \
               (away_team.lower() in away_api or away_api in away_team.lower()):
                
                if odd.get("sportsbook") == "1xbet":
                    return {
                        "bookmaker": "1xbet",
                        "home": odd.get("odds_decimal") if odd.get("selection_type") == "home" else None,
                        "draw": odd.get("odds_decimal") if odd.get("selection_type") == "draw" else None,
                        "away": odd.get("odds_decimal") if odd.get("selection_type") == "away" else None,
                    }
        
        return None
    except Exception as e:
        print(f"Erreur SharpAPI: {e}")
        return None
