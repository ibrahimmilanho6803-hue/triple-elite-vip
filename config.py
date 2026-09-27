# Configuration Triple Elite VIP
#
# Ce fichier centralise les reglages non-secrets utilises par les autres
# modules (dashboard.py, paiement.py, combo_generator.py, data_collector.py).
# Les vrais secrets (cles API, mots de passe) ne sont JAMAIS ici : ils vivent
# uniquement dans les variables d'environnement (voir render.yaml).

import os

# --- Championnats suivis (nom -> id TheSportsDB) ---
# 5 championnats plutot que 3 : quand une treve internationale (ou un simple
# calendrier incomplet cote TheSportsDB) met un championnat en pause, les
# autres compensent et il reste plus de matchs pour composer un combine.
LEAGUES = {
    "Premier League": "4328",
    "La Liga": "4335",
    "Bundesliga": "4331",
    "Ligue 1": "4334",
    "Serie A": "4332",
}

# --- Base de donnees locale (historique matchs/equipes) ---
# Sans disque persistant configure sur Render, DATA_DIR vaut "." (dossier de
# l'appli) : le fichier est perdu a chaque redeploiement, car le disque de
# base est ephemere. Definis DATA_DIR sur Render (ex: "/var/data") une fois
# un disque persistant attache a ce chemin pour que l'historique survive aux
# deploiements.
DATA_DIR = os.environ.get("DATA_DIR", ".")
DB_PATH = os.path.join(DATA_DIR, "triple_elite.db")

# --- TheSportsDB ---
# "3" est la cle de test publique et partagee documentee par TheSportsDB.
# Elle fonctionne sans compte mais est limitee/partagee avec tout le monde.
# Definis SPORTSDB_API_KEY sur Render avec ta propre cle (Patreon) pour un
# acces stable et plus fiable.
SPORTSDB_API_KEY = os.environ.get("SPORTSDB_API_KEY", "3")

# --- Generation des combines ---
TARGET_ODDS = 2.50          # cote totale minimale d'un combine
MIN_CONFIDENCE = 65         # confiance minimale (%) pour qu'un pronostic soit retenu
MAX_COMBOS_RETOURNES = 3    # nombre de combines renvoyes au client
MATCHS_PAR_CHAMPIONNAT = 3  # nombre de matchs a venir analyses par championnat

# Nombre max de types de pronostics conserves par match (les plus confiants)
# au moment de composer les combines. Avec 5 championnats x 3 matchs, jusqu'a
# 15 matchs entrent desormais dans le calcul (voir dashboard.py) : sans cette
# limite, le nombre de combines a evaluer (matchs x predictions au cube)
# grossirait beaucoup trop. 8 laisse largement de quoi varier les types de
# pronostics dans chaque combine.
MAX_PREDICTIONS_PAR_MATCH = 8

# Duree (en minutes) pendant laquelle un combine genere est reutilise avant
# d'etre recalcule. Evite de refaire une collecte + un appel IA + des appels
# aux cotes a chaque clic, ce qui serait lent et couteux en API.
CACHE_MINUTES = 30

# --- Produit / tarifs ---
PRODUCT_NAME = "Triple Elite VIP"
VERSION = "2.0"

# Prix affiches au client sur les pages de vente et de paiement.
DEVISE = "€"
PRICE_MONTHLY = 30   # € / mois (affichage)
PRICE_YEARLY = 60    # € / an (affichage)

# PayDunya (Orange Money, MTN, Moov, Wave, carte) ne facture qu'en FCFA (XOF) :
# leur API ne propose aucune option pour creer une facture en euros. Le FCFA
# etant arrime a l'euro a taux fixe (1 EUR = 655,957 FCFA), les montants
# ci-dessous sont l'equivalent reel de PRICE_MONTHLY/PRICE_YEARLY et sont ce
# qui est REELLEMENT transmis a PayDunya (voir paiement.py) -- meme si la
# page affiche "30 €" / "60 €" au client.
PRICE_MONTHLY_FACTURE_FCFA = 19700   # ~30 EUR
PRICE_YEARLY_FACTURE_FCFA = 39400    # ~60 EUR

# --- Contact vendeur ---
SELLER_EMAIL = "tripleelitevip@gmail.com"
