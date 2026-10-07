"""Gestion manuelle des licences (cadeau, geste commercial, dépannage), sans passer par le paiement.

    python generate_keys.py

Nécessite DATABASE_URL (l'adresse de la base PostgreSQL, comme sur Render). Une licence créée ici est
exactement la même que celle d'un achat : même e-mail, même clé, même durée. Si le client a déjà un
abonnement en cours, la durée s'ajoute au temps restant et sa clé ne change pas.

Après chaque création ou prolongation, le script propose d'envoyer la clé par e-mail (même envoi que le
site : GMAIL_MDP ou BREVO_API_KEY), plutôt que de la recopier à la main. Rien ne part sans un « o » explicite.
"""
import logging
import os
import re
import sys
from datetime import datetime, timezone

import email_sender
import license_manager
from license_manager import LicenseManager, duration_days

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
YES = {"o", "oui", "y", "yes"}


def ask_email(prompt):
    email = license_manager.normalize_email(input(prompt))
    if not EMAIL_RE.match(email):
        print("Adresse e-mail invalide.")
        return None
    return email


def ask_months():
    raw = input("Durée en mois (1 = 30 jours, 12 = un an) : ").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 36:
        print("Durée invalide : entre un nombre de mois entre 1 et 36.")
        return None
    return int(raw)


def describe(lic):
    expires = license_manager.LicenseManager._parse_expires(lic["expires"])
    left = (expires - license_manager._utcnow()).days
    if not lic["active"]:
        state = "DÉSACTIVÉE"
    elif left < 0:
        state = f"expirée depuis {-left} j"
    else:
        state = f"active, {left} j restants"
    return f"{lic['email']} | {lic['key']} | jusqu'au {expires.strftime('%d/%m/%Y')} | {state}"


def offer_email(email, info):
    """Propose d'envoyer la clé au client par e-mail. Jamais sans accord explicite ; en cas d'échec, la clé
    reste affichée au-dessus et rien n'est perdu."""
    transport = email_sender.default_transport()
    if transport is None:
        print("   (Envoi d'e-mail non configuré ici : GMAIL_MDP et BREVO_API_KEY sont absents."
              " Transmets la clé au client toi-même.)")
        return
    answer = input(f"\nEnvoyer la clé par e-mail à {email} ? [o/N] : ").strip().lower()
    if answer not in YES:
        print("   (Aucun e-mail envoyé : pense à transmettre la clé au client.)")
        return
    sent = email_sender.envoyer_licence(email, info["key"], "", info["expires"], renewed=info["renewed"],
                                        transport=transport, granted=True)
    if sent:
        print(f"   E-mail envoyé à {email}.")
    else:
        print("   L'e-mail n'a pas pu partir (détail ci-dessus). Transmets la clé au client toi-même.")


def main():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL n'est pas définie : impossible de joindre la base des licences.")
        return 2
    # Les avertissements et erreurs (base ou envoi d'e-mail) s'affichent tels quels ; le reste reste silencieux.
    logging.basicConfig(level=logging.WARNING, format="   %(levelname)s %(message)s")
    lm = LicenseManager()

    print("=" * 50)
    print("LICENCES - Triple Elite VIP")
    print(f"({lm.describe_db()})")       # le site client et le paiement doivent afficher la même base
    print("=" * 50)
    while True:
        print("\n1. Créer ou prolonger une licence")
        print("2. Voir toutes les licences")
        print("3. Désactiver une licence")
        print("4. Quitter")
        choice = input("\nChoix : ").strip()

        if choice == "1":
            email, months = ask_email("E-mail du client : "), None
            if email:
                months = ask_months()
            if email and months:
                try:
                    info = lm.issue_license(email, months)
                except Exception as exc:                     # base injoignable : ne jamais faire croire que c'est fait
                    print(f"Échec, rien n'a été enregistré : {exc}")
                    continue
                print("\nLicence prolongée." if info["renewed"] else "\nLicence créée.")
                print(f"   E-mail : {email}")
                print(f"   Clé    : {info['key']}")
                print(f"   Durée  : {months} mois ({duration_days(months)} jours)")
                print(f"   Valable jusqu'au {info['expires'].strftime('%d/%m/%Y')}")
                offer_email(email, info)

        elif choice == "2":
            licences = lm.list_licenses()
            if not licences:
                print("Aucune licence trouvée.")
            else:
                print(f"\n{len(licences)} licence(s) au {datetime.now(timezone.utc).strftime('%d/%m/%Y')} :")
                for lic in licences:
                    print("   " + describe(lic))

        elif choice == "3":
            email = ask_email("E-mail à désactiver : ")
            if email:
                print("Licence désactivée." if lm.deactivate_license(email) else "Licence introuvable.")

        elif choice == "4":
            return 0


if __name__ == "__main__":
    sys.exit(main())
