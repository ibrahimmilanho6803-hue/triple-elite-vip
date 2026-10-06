# Configuration Triple Elite VIP
#
# Ce fichier centralise les réglages NON secrets utilisés par les autres modules.
# Les vrais secrets (clés API, mots de passe) ne sont JAMAIS ici : ils vivent
# uniquement dans les variables d'environnement (voir .env.example et render.yaml).

import os


def load_env_file(path=".env"):
    """Lit un fichier .env (CLE=valeur, une par ligne) et définit les variables d'environnement
    absentes. Pratique en local ; sur Render, les variables viennent du tableau de bord et ce
    fichier n'existe pas. Une variable déjà définie n'est jamais écrasée. Renvoie le nombre
    de variables chargées."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return 0
    loaded = 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if name and value and name not in os.environ:       # une valeur vide (« CLE= ») ne définit rien
            os.environ[name] = value
            loaded += 1
    return loaded


if not os.environ.get("TEV_NO_DOTENV"):
    load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# --- Championnats suivis (nom -> id TheSportsDB) ---
# 5 championnats : quand une trêve internationale (ou un calendrier incomplet chez
# TheSportsDB) met un championnat en pause, les autres compensent.
LEAGUES = {
    "Premier League": "4328",
    "La Liga": "4335",
    "Bundesliga": "4331",
    "Ligue 1": "4334",
    "Serie A": "4332",
}

# --- Stockage local (historique des matchs, combinés générés, cache) ---
# Sans disque persistant sur Render, DATA_DIR vaut "." (dossier de l'appli) : tout
# est perdu à chaque redéploiement. Sur Render, attache un disque persistant et
# définis DATA_DIR sur son chemin (ex. "/var/data").
DATA_DIR = os.environ.get("DATA_DIR") or "."
DB_PATH = os.path.join(DATA_DIR, "triple_elite.db")
RESULTS_DIR = os.path.join(DATA_DIR, "results")   # un fichier JSON par génération
CACHE_DIR = os.path.join(DATA_DIR, "cache")       # dernière génération, état du travail, quota

# --- TheSportsDB ---
# "3" est la clé de test publique et partagée de TheSportsDB (limitée). Définis
# SPORTSDB_API_KEY sur Render avec ta propre clé pour un accès stable.
SPORTSDB_API_KEY = os.environ.get("SPORTSDB_API_KEY") or "3"

# --- Génération des combinés ---
TARGET_ODDS = 2.50          # cote totale minimale d'un combiné
MIN_CONFIDENCE = 65         # confiance minimale (%) pour qu'un pronostic soit retenu
MAX_COMBOS_RETOURNES = 3    # nombre de combinés renvoyés au client
MATCHS_PAR_CHAMPIONNAT = 3  # nombre de matchs à venir analysés par championnat

# Nombre de pronostics conservés par match au moment de composer les combinés
# (les plus cotés parmi ceux qui atteignent la confiance minimale). Avec 15 matchs,
# 10 laisse assez de choix sans faire exploser le nombre de combinaisons.
MAX_PREDICTIONS_PAR_MATCH = 10

# Durée (minutes) pendant laquelle les combinés générés sont réutilisés, pour tous
# les clients. Évite de refaire collecte + analyse IA à chaque clic (lent et payant)
# et garde des combinés stables dans la journée.
CACHE_MINUTES = 180

# Plafond de générations réelles (donc d'appels à l'IA payante) par jour. Au-delà,
# les derniers combinés disponibles restent servis.
MAX_GENERATIONS_PAR_JOUR = 10

# Une génération « en cours » depuis plus longtemps est considérée comme perdue
# (redémarrage du serveur...) et peut être relancée.
JOB_MAX_SECONDS = 300

# --- Cotes ---
# Quand aucune vraie cote n'est disponible (ODDS_API_KEY absente, ou pari non coté
# par the-odds-api), la cote est ESTIMÉE à partir de la probabilité : (1 - marge) / p.
# La marge imite celle d'un bookmaker (les cotes réelles sont plus basses que 1/p).
ODDS_MARGIN = 0.07

# --- IA ---
IA_MODEL = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-5-5"
# Modèles de repli, essayés dans l'ordre si l'API répond « modèle introuvable » (nom erroné, modèle retiré) :
# mieux vaut un modèle un peu plus ancien qu'un site qui ne génère plus rien. Le premier qui répond est gardé.
IA_FALLBACK_MODELS = ("claude-sonnet-5", "claude-sonnet-4-5")
IA_BATCH_SIZE = 3           # matchs par appel (appels faits en parallèle)
IA_MAX_PARALLEL = 5         # appels simultanés maximum
IA_MAX_ATTEMPTS = 2         # tentatives par lot
IA_TIMEOUT = 45.0           # secondes par appel
IA_MAX_TOKENS = 2500

# --- Sécurité des comptes clients ---
LOGIN_MAX_ATTEMPTS = 8      # échecs de connexion tolérés par e-mail...
LOGIN_WINDOW_SECONDS = 900  # ...sur cette durée (puis blocage temporaire)
SESSION_DAYS = 7            # durée de la session du navigateur

# La licence est revérifiée en base à chaque requête protégée, mais le verdict est
# gardé quelques secondes en mémoire (le navigateur interroge l'avancement toutes les
# secondes pendant une génération : inutile d'ouvrir autant de connexions à la base).
LICENSE_CACHE_SECONDS = 30
# Si la base de données est momentanément injoignable, un client dont la licence vient
# d'être vérifiée « active » garde l'accès pendant cette durée au lieu d'être éjecté.
LICENSE_GRACE_SECONDS = 900

# --- Paiement ---
# Limites anti-abus de la page de paiement (en mémoire, par processus) : chaque tentative de
# créer une facture compte pour l'e-mail saisi, l'adresse du visiteur et l'ensemble du site.
PAYMENT_MAX_PER_EMAIL = 5
PAYMENT_MAX_PER_IP = 15
PAYMENT_MAX_GLOBAL = 200
PAYMENT_WINDOW_SECONDS = 600
PAYDUNYA_TIMEOUT = 15              # secondes d'attente maximum d'une réponse PayDunya
# Page de confirmation : tant que PayDunya ne confirme pas, elle se recharge toute seule
# (toutes les SUCCESS_REFRESH_SECONDS, au plus SUCCESS_REFRESH_MAX fois), puis invite à revenir.
SUCCESS_REFRESH_SECONDS = 4
SUCCESS_REFRESH_MAX = 15

# --- E-mail de licence ---
# Gmail (SMTP) par défaut. ATTENTION : Render bloque les ports SMTP (25, 465, 587) sur les
# services de l'offre gratuite ; voir README (BREVO_API_KEY propose une alternative par HTTPS).
EMAIL_FROM_NAME = "Triple Elite VIP"
SMTP_HOST = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
SMTP_PORT = int(os.environ.get("SMTP_PORT") or "587")
EMAIL_TIMEOUT = 20                 # secondes par tentative d'envoi
EMAIL_ATTEMPTS = 3                 # tentatives avant d'abandonner (attente croissante entre deux)

# --- Adresses publiques (modifiables par variables d'environnement) ---
SITE_URL = (os.environ.get("SITE_URL") or "https://triple-elite-vip.com").rstrip("/")
PAIEMENT_URL = (os.environ.get("PAIEMENT_BASE_URL") or "https://triple-elite-vip-paiement.onrender.com").rstrip("/")

# --- Produit / tarifs ---
PRODUCT_NAME = "Triple Elite VIP"
VERSION = "2.1"

# Prix affichés au client sur les pages de vente et de paiement.
DEVISE = "€"
PRICE_MONTHLY = 30   # € / mois (affichage)
PRICE_YEARLY = 60    # € / an (affichage)

# PayDunya (Orange Money, MTN, Moov, Wave, carte) ne facture qu'en FCFA (XOF) : leur
# API ne propose aucune option pour créer une facture en euros. Le FCFA étant arrimé
# à l'euro à taux fixe (1 EUR = 655,957 FCFA), les montants ci-dessous sont
# l'équivalent réel de PRICE_MONTHLY / PRICE_YEARLY et sont ce qui est RÉELLEMENT
# transmis à PayDunya (voir paiement.py), même si la page affiche « 30 € » / « 60 € ».
PRICE_MONTHLY_FACTURE_FCFA = 19700   # ~30 EUR
PRICE_YEARLY_FACTURE_FCFA = 39400    # ~60 EUR

# --- Contact vendeur ---
SELLER_EMAIL = "tripleelitevip@gmail.com"
