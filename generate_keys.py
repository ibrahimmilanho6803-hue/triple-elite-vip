from license_manager import LicenseManager

lm = LicenseManager()

print("=" * 50)
print("GENERATEUR DE LICENCES - Triple Elite VIP")
print("=" * 50)

while True:
    print("\n1. Generer une nouvelle licence")
    print("2. Voir toutes les licences")
    print("3. Desactiver une licence")
    print("4. Quitter")

    choix = input("\nChoix : ")

    if choix == "1":
        email = input("Email du client : ")
        duree = int(input("Duree (mois) : "))
        key = lm.generate_license(email, duree)
        print(f"\nLicence creee !")
        print(f"   Email : {email}")
        print(f"   Cle   : {key}")
        print(f"   Duree : {duree} mois")

    elif choix == "2":
        licences = lm.list_licenses()
        if not licences:
            print("Aucune licence trouvee")
        else:
            print("\nLICENCES :")
            for lic in licences:
                statut = "Active" if lic["active"] else "Desactivee"
                expire = str(lic["expires"])[:10]
                print(f"   {lic['email']} | {lic['key']} | Expire: {expire} | {statut}")

    elif choix == "3":
        email = input("Email a desactiver : ")
        if lm.deactivate_license(email):
            print(f"Licence de {email} desactivee")
        else:
            print("Licence introuvable")

    elif choix == "4":
        break
