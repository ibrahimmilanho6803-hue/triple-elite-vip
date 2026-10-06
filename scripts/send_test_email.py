"""Envoie un e-mail de licence de démonstration, pour vérifier la configuration d'envoi.

    python scripts/send_test_email.py adresse@exemple.com

Utilise le même envoi que le site : l'API Brevo si BREVO_API_KEY est définie, sinon Gmail (SMTP)
avec GMAIL_EMAIL et GMAIL_MDP. Attention : sur Render, l'offre gratuite bloque SMTP.
"""
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import email_sender  # noqa: E402


def main():
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        print(__doc__)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    transport = email_sender.default_transport()
    if transport is None:
        print("Aucun envoi configuré : définis GMAIL_MDP (et GMAIL_EMAIL) ou BREVO_API_KEY.")
        return 2
    print(f"Envoi par {transport.name} à {sys.argv[1]} ...")
    expires = (datetime.now(timezone.utc) + timedelta(days=30)).replace(tzinfo=None)
    ok = email_sender.envoyer_licence(sys.argv[1], "0123456789abcdef", "Mensuel", expires, transport=transport)
    print("E-mail envoyé." if ok else "Échec : voir le message ci-dessus.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
