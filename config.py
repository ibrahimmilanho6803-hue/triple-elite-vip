# Configuration Triple Elite VIP
#
# Ce fichier centralise les réglages NON secrets utilisés par les autres modules.
# Les vrais secrets (clés API, mots de passe) ne sont JAMAIS ici : ils vivent
# uniquement dans les variables d'environnement (voir .env.example et render.yaml).

import os
import re


def parse_email_list(raw):
    """Adresses séparées par des virgules, des points-virgules ou des espaces -> ensemble en minuscules."""
    return frozenset(part.lower() for part in re.split(r"[,;\s]+", raw or "") if part)


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


def parse_hour(raw):
    """Heure entière 0-23 lue dans une variable d'environnement ; None si absente ou invalide."""
    try:
        hour = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return hour if 0 <= hour <= 23 else None


# --- Pages publiques : résultats réels (/resultats) et combiné gratuit du jour (/gratuit) ---
# Le combiné gratuit est UN SEUL des combinés de la dernière génération (celui dont la chance estimée est la plus élevée).
# Les autres restent le produit payant : jamais montrés. FREE_PICK_ENABLED=0 (Render > Environment) retire la page
# /gratuit et ses liens sans nouveau déploiement.
FREE_PICK_ENABLED = os.environ.get("FREE_PICK_ENABLED", "1").strip().lower() not in ("0", "false", "non", "no", "off")
FREE_PICK_MAX_AGE_HOURS = 24      # une génération plus ancienne ne fournit plus de combiné gratuit
FREE_PICK_MARGIN_MINUTES = 15     # ni un combiné dont un match commence dans moins de ce délai

# Génération automatique quotidienne (daily_generation.py), pour que le combiné gratuit et l'historique soient alimentés
# même les jours où aucun abonné ne clique. DÉSACTIVÉE par défaut : chaque génération est un appel payant à l'IA.
# AUTO_GENERATE_HOUR = heure UTC (0 à 23) à partir de laquelle la génération du jour peut partir ; absente = désactivée.
AUTO_GENERATE_HOUR = parse_hour(os.environ.get("AUTO_GENERATE_HOUR"))
AUTO_GENERATE_MAX_ATTEMPTS = 3    # tentatives par jour (une panne de l'IA ne doit pas multiplier les appels payants)

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
# Mode test : avec les clés de TEST de PayDunya (« test_private_… »), les paiements sont fictifs. Pour qu'un visiteur ne
# puisse pas obtenir une vraie licence avec un faux paiement, seules ces adresses (variable PAYDUNYA_TEST_EMAILS, séparées
# par des virgules) peuvent alors commander. Liste vide : personne, tout achat est refusé tant que le mode test dure.
PAYDUNYA_TEST_EMAILS = parse_email_list(os.environ.get("PAYDUNYA_TEST_EMAILS"))
# Page de confirmation : tant que PayDunya ne confirme pas, elle se recharge toute seule
# (toutes les SUCCESS_REFRESH_SECONDS, au plus SUCCESS_REFRESH_MAX fois), puis invite à revenir.
SUCCESS_REFRESH_SECONDS = 4
SUCCESS_REFRESH_MAX = 15

# Moyens de paiement ANNONCÉS aux clients (accueil, FAQ, page de paiement). Ils doivent refléter ce que PayDunya propose
# RÉELLEMENT à ce compte marchand, sinon un client arrive chez PayDunya et n'y trouve aucun moyen de payer.
# Contrôlé sur la page de paiement PayDunya le 07/10/2026 : du Mobile Money dans ces six pays, aucune carte bancaire
# (ni pour les pays « Autres »). Ajouter un pays ici seulement après l'avoir vu sur cette page.
PAYMENT_COUNTRIES = ("Côte d’Ivoire", "Sénégal", "Bénin", "Togo", "Burkina Faso", "Cameroun")
# Passer à True quand PayDunya a activé les cartes internationales sur le compte ET qu'une carte apparaît sur sa page de
# paiement : les textes « carte bancaire » reviennent partout et l'avis « pas encore de carte » disparaît.
CARDS_ENABLED = False

# --- E-mail de licence ---
# Gmail (SMTP) par défaut. ATTENTION : Render bloque les ports SMTP (25, 465, 587) sur les
# services de l'offre gratuite ; voir README (BREVO_API_KEY propose une alternative par HTTPS).
EMAIL_FROM_NAME = "Triple Elite VIP"
SMTP_HOST = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
SMTP_PORT = int(os.environ.get("SMTP_PORT") or "587")
EMAIL_TIMEOUT = 20                 # secondes par tentative d'envoi
EMAIL_ATTEMPTS = 3                 # tentatives avant d'abandonner (attente croissante entre deux)

# --- Adresses publiques (modifiables par variables d'environnement) ---
# Le site de paiement est un service Render à part, mais il porte une adresse du domaine de la marque (CNAME Namecheap
# vers triple-elite-vip-paiement.onrender.com). L'ancienne adresse onrender.com répond toujours, comme secours.
SITE_URL = (os.environ.get("SITE_URL") or "https://triple-elite-vip.com").rstrip("/")
PAIEMENT_URL = (os.environ.get("PAIEMENT_BASE_URL") or "https://paiement.triple-elite-vip.com").rstrip("/")

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

# --- Application Android (APK installé à la main, plus tard Google Play) ---
# Le site publie ces informations dans /.well-known/assetlinks.json (voir pwa.py) : c'est ce fichier qui prouve à Android
# que l'application et le site sont au même propriétaire, donc que l'application peut s'ouvrir sans barre d'adresse.
# Une empreinte SHA-256 par clé de signature (la clé de l'APK distribué directement ; plus tard, celle de Google Play).
# Une empreinte n'est pas un secret et ne permet de signer rien du tout : la clé elle-même (fichier .keystore) ne doit
# jamais être dans ce dépôt.
ANDROID_PACKAGE = "com.tripleelitevip.app"
ANDROID_CERT_FINGERPRINTS = (
    "9A:70:FF:F3:93:B9:30:85:6E:56:78:69:0D:1C:D6:DC:CF:37:B3:DC:28:62:CB:E2:96:3B:B5:F1:13:AC:B2:13",   # APK 1.0 (octobre 2026)
)
