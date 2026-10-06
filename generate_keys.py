"""Gestion manuelle des licences (cadeau, geste commercial, dépannage), sans passer par le paiement.

    python generate_keys.py

Nécessite DATABASE_URL (l'adresse de la base PostgreSQL, comme sur Render). Une licence créée ici est
exactement la même que celle d'un achat : même e-mail, même clé, même durée. Si le client a déjà un
abonnement en cours, la durée s'ajoute au temps restant et sa clé ne change pas.
"""
import os
import re
import sys
from datetime import datetime, timezone

import license_manager
from license_manager import LicenseManager, duration_days

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


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


def main():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL n'est pas définie : impossible de joindre la base des licences.")
        return 2
    lm = LicenseManager()

    print("=" * 50)
    print("LICENCES - Triple Elite VIP")
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
                print("   (Pense à envoyer cette clé au client : aucun e-mail n'est envoyé d'ici.)")

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
