from email_sender import envoyer_licence

print("Debut du test d'envoi d'email...")
resultat = envoyer_licence("ibrahimmilanho6803@gmail.com", "TEST-CLE-1234", "Mensuel (30J)")
print("Resultat:", "Succes" if resultat else "Echec")
