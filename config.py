# Configuration Triple Elite VIP
#
# Ce fichier centralise les reglages non-secrets utilises par les autres
# modules (dashboard.py, paiement.py, combo_generator.py, data_collector.py).
# Les vrais secrets (cles API, mots de passe) ne sont JAMAIS ici : ils vivent
# uniquement dans les variables d'environnement (voir render.yaml).

import os

# --- Championnats suivis (nom -> id TheSportsDB) ---
LEAGUES = {
    "Premier League": "4328",
    "La Liga": "4335",
    "Bundesliga": "4331",
}

# --- TheSportsDB ---
# "3" est la cle de test publique et partagee documentee par TheSportsDB.
# Elle fonctionne sans compte mais est limitee/partagee avec tout le monde.
# Definis SPORTSDB_API_KEY sur Render avec ta propre cle (Patreon) pour un
# acces stable et plus fiable.
SPORTSDB_API_KEY = os.environ.get("SPORTSDB_API_KEY", "3")

# --- Generation des combines ---
TARGET_ODDS = 2.50          # cote totale minimale d'un combine
MIN_CONFIDENCE = 65         # confiance minimale (%) pour qu'un pronostic soit retenu
MAX_COMBOS_RETOURNES = 2    # nombre de combines renvoyes au client
MATCHS_PAR_CHAMPIONNAT = 3  # nombre de matchs a venir analyses par championnat

# Duree (en minutes) pendant laquelle un combine genere est reutilise avant
# d'etre recalcule. Evite de refaire une collecte + un appel IA + des appels
# aux cotes a chaque clic, ce qui serait lent et couteux en API.
CACHE_MINUTES = 30

# --- Produit / tarifs ---
# Les paiements sont traites en FCFA (Orange Money, MTN, Moov, Wave, PayDunya).
PRODUCT_NAME = "Triple Elite VIP"
VERSION = "2.0"
DEVISE = "FCFA"
PRICE_MONTHLY = 30000   # FCFA / mois
PRICE_YEARLY = 60000    # FCFA / an

# --- Contact vendeur ---
SELLER_EMAIL = "tripleelitevip@gmail.com"
