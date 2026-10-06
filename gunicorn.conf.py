"""Réglages de gunicorn pour les deux sites (tableau de bord et paiement).

Ce fichier est lu automatiquement (gunicorn.conf.py du dossier de lancement). Ce qui est écrit dans
la « Start Command » de Render (--bind, --timeout...) reste prioritaire : rien à changer côté Render.

Pourquoi des threads ? Les pages restent réactives pendant qu'une génération de combinés (plusieurs
dizaines de secondes, en arrière-plan) ou un appel PayDunya est en cours. Un seul processus : la
génération n'est lancée qu'une fois pour tous les clients, et l'état partagé (limites d'essais,
cache des licences) reste cohérent.
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
worker_class = "gthread"
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = 120
graceful_timeout = 30
keepalive = 5
accesslog = None            # les pings de supervision (toutes les 5 minutes) noieraient les journaux
errorlog = "-"
