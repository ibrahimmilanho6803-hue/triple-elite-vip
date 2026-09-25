import os
import requests
from apify_client import ApifyClient

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")

def find_1xbet_match_url(home_team, away_team):
    """Cherche l'URL du match sur 1xBet"""
    search_url = "https://1xbet.com/api/search"
    params = {"q": f"{home_team} {away_team}"}
    try:
        response = requests.get(search_url, params=params, timeout=10)
        data = response.json()
        if data and len(data) > 0:
            return data[0].get("url")
    except Exception as e:
        print(f"Erreur recherche 1xBet: {e}")
    return None

def get_1xbet_odds(match_url):
    """Récupère les cotes d'un match 1xBet via Apify"""
    if not APIFY_TOKEN:
        print("APIFY_TOKEN manquant")
        return None
    
    client = ApifyClient(APIFY_TOKEN)
    run_input = {
        "startUrls": [{"url": match_url}],
        "maxMatches": 1,
        "includeOutcomes": True,
        "proxyConfiguration": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"]
        }
    }
    try:
        run = client.actor("mrdoe/1xbet-odds-scraper").call(
            run_input=run_input, timeout_secs=120
        )
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        if not items:
            return None
        odds = items[0].get("odds", {})
        return {
            "home": odds.get("home"),
            "draw": odds.get("draw"),
            "away": odds.get("away"),
            "over_2_5": odds.get("over_2_5"),
            "under_2_5": odds.get("under_2_5"),
            "btts_yes": odds.get("btts_yes"),
            "btts_no": odds.get("btts_no")
        }
    except Exception as e:
        print(f"Erreur Apify: {e}")
        return None
