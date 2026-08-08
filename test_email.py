import sys
sys.path.insert(0, r"C:\Users\HP\Desktop\Triple_Elite_VIP")
from email_sender import envoyer_licence

print("Début du test d'envoi d'email...")
resultat = envoyer_licence("ibrahimmilanho6803@gmail.com", "TEST-CLE-1234", "Mensuel (30J)")
print("Résultat:", "Succès" if resultat else "Échec")