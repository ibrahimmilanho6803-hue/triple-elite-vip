"""Gestion manuelle des licences (cadeau, geste commercial, dépannage), sans passer par le paiement.

    python generate_keys.py

Nécessite DATABASE_URL (l'adresse de la base PostgreSQL, comme sur Render). Une licence créée ici est
exactement la même que celle d'un achat : même e-mail, même clé, même durée. Si le client a déjà un
abonnement en cours, la durée s'ajoute au temps restant et sa clé ne change pas.

Après chaque création ou prolongation, le script propose d'envoyer la clé par e-mail (même envoi que le
site : GMAIL_MDP ou BREVO_API_KEY), plutôt que de la recopier à la main. Rien ne part sans un « o » explicite.

« Supprimer définitivement » efface toutes les lignes d'un e-mail, pour repartir de zéro (puis option 1). Rien n'est
effacé sans avoir tapé le mot « supprimer » : le script montre d'abord exactement ce qui va disparaître.
"""
import logging
import os
import re
import sys
from collections import Counter
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


def duplicated(licences):
    """E-mails présents sur plusieurs lignes (à la casse près) : un reste d'anciennes versions, à nettoyer."""
    counts = Counter(license_manager.normalize_email(lic["email"]) for lic in licences)
    return {email for email, n in counts.items() if n > 1}


def delete_flow(lm, email):
    """Suppression définitive des licences d'un e-mail, après avoir montré ce qui va disparaître et obtenu le mot
    « supprimer » tapé en toutes lettres. Une base injoignable n'est jamais présentée comme une suppression réussie."""
    rows = [lic for lic in lm.list_licenses() if license_manager.normalize_email(lic["email"]) == email]
    if not rows:
        print("Licence introuvable.")
        return
    print(f"\n{len(rows)} ligne(s) seront effacées pour cet e-mail, sans retour possible :")
    for lic in rows:
        print("   " + describe(lic))
    answer = input("Pour confirmer, tape le mot « supprimer » (autre chose = annuler) : ").strip().lower()
    if answer != "supprimer":
        print("Annulé : rien n'a été supprimé.")
        return
    try:
        deleted = lm.delete_license(email)
    except Exception as exc:
        print(f"Échec, rien n'a été supprimé : {exc}")
        return
    print(f"{deleted} ligne(s) supprimée(s). Pour repartir de zéro : option 1 avec cet e-mail." if deleted
          else "Licence introuvable.")


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
        print("4. Supprimer définitivement une licence")
        print("5. Quitter")
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
                doubles = duplicated(licences)
                print(f"\n{len(licences)} licence(s) au {datetime.now(timezone.utc).strftime('%d/%m/%Y')} :")
                for lic in licences:
                    mark = "  <= DOUBLON" if license_manager.normalize_email(lic["email"]) in doubles else ""
                    print("   " + describe(lic) + mark)
                if doubles:
                    print("\n   DOUBLON : plusieurs lignes pour un même e-mail (majuscules différentes), reste d'anciennes"
                          "\n   versions. La connexion choisit la bonne ligne, mais mieux vaut nettoyer : option 4"
                          "\n   (supprimer cet e-mail), puis option 1 (le recréer).")

        elif choice == "3":
            email = ask_email("E-mail à désactiver : ")
            if email:
                print("Licence désactivée." if lm.deactivate_license(email) else "Licence introuvable.")

        elif choice == "4":
            email = ask_email("E-mail dont la licence doit être SUPPRIMÉE : ")
            if email:
                delete_flow(lm, email)

        elif choice == "5":
            return 0


if __name__ == "__main__":
    sys.exit(main())
