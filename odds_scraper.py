import os
from apify_client import ApifyClient

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "VOTRE_TOKEN_APIFY")

def get_1xbet_odds(match_url):
    """
    Récupère toutes les cotes d'un match 1xBet via Apify.
    match_url = URL de la page du match sur 1xBet
    Ex: https://1xbet.com/en/line/football/88637-england-premier-league/746666829-ipswich-town-liverpool
    """
    client = ApifyClient(APIFY_TOKEN)
    
    run_input = {
        "startUrls": [{"url": match_url}],
        "maxMatches": 1,
        "includeOutcomes": True,
        "useCache": False,
        "proxyConfiguration": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
            "apifyProxyCountry": "NG"  # Nigeria (1xBet y opère)
        }
    }
    
    try:
        run = client.actor("mrdoe/1xbet-odds-scraper").call(run_input=run_input, timeout_secs=120)
        
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        
        if not items:
            return None
        
        match_data = items[0]
        
        # Extraire les cotes principales
        odds = match_data.get("odds", {})
        
        return {
            "home": odds.get("home"),
            "draw": odds.get("draw"),
            "away": odds.get("away"),
            "over_2_5": odds.get("over_2_5"),
            "under_2_5": odds.get("under_2_5"),
            "btts_yes": odds.get("btts_yes"),
            "btts_no": odds.get("btts_no"),
            "raw": odds  # Toutes les cotes brutes
        }
    except Exception as e:
        print(f"Erreur Apify: {e}")
        return None
