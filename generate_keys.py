import sys
sys.path.insert(0, r"C:\Users\HP\Desktop\Triple_Elite_VIP")
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
        print(f"\n✅ Licence creee !")
        print(f"   Email : {email}")
        print(f"   Cle   : {key}")
        print(f"   Duree : {duree} mois")
        
    elif choix == "2":
        try:
            import json
            with open("licenses.json", "r") as f:
                licences = json.load(f)
            print("\n📋 LICENCES ACTIVES :")
            for lic in licences:
                statut = "✅ Active" if lic["active"] else "❌ Desactivee"
                print(f"   {lic['email']} | {lic['key']} | Expire: {lic['expires'][:10]} | {statut}")
        except:
            print("Aucune licence trouvee")
            
    elif choix == "3":
        email = input("Email a desactiver : ")
        try:
            import json
            with open("licenses.json", "r") as f:
                licences = json.load(f)
            for lic in licences:
                if lic["email"] == email:
                    lic["active"] = False
            with open("licenses.json", "w") as f:
                json.dump(licences, f, indent=4)
            print(f"✅ Licence de {email} desactivee")
        except:
            print("Erreur")
            
    elif choix == "4":
        break