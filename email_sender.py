import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading

EMAIL_EXPEDITEUR = os.environ.get("GMAIL_EMAIL", "tripleelitevip@gmail.com")
MOT_DE_PASSE_APP = os.environ.get("GMAIL_MDP", "tuvx qsar slfy epnj")

def envoyer_licence_async(email_destinataire, cle_licence, plan):
    thread = threading.Thread(target=envoyer_licence, args=(email_destinataire, cle_licence, plan))
    thread.daemon = True
    thread.start()
    print(f"Envoi email en arriere-plan a {email_destinataire}...")

def envoyer_licence(email_destinataire, cle_licence, plan):
    msg = MIMEMultipart('alternative')
    msg['From'] = f"Triple Elite VIP <{EMAIL_EXPEDITEUR}>"
    msg['To'] = email_destinataire
    msg['Subject'] = "Votre acces Triple Elite VIP"
    msg['X-Priority'] = '3'
    msg['X-MSMail-Priority'] = 'Normal'
    msg['Importance'] = 'Normal'
    msg['Reply-To'] = EMAIL_EXPEDITEUR

    corps_html = f"""
<html>
<body style="font-family: Arial, sans-serif; background: #0a0e27; padding: 20px;">
<div style="max-width: 500px; margin: auto; background: #1a1f3a; padding: 30px; border-radius: 10px; color: #fff;">
<h1 style="color: #ffd700; text-align: center;">Triple Elite VIP</h1>
<p>Bonjour,</p>
<p>Merci pour votre abonnement ! Voici vos acces :</p>
<div style="background: #0d1137; padding: 15px; border-radius: 5px; margin: 20px 0;">
<p><strong>Email :</strong> {email_destinataire}</p>
<p><strong>Cle :</strong> <span style="color: #ffd700;">{cle_licence}</span></p>
<p><strong>Plan :</strong> {plan}</p>
</div>
<p>Connectez-vous sur : <a href="https://triple-elite-vip.com/login" style="color: #ffd700;">Dashboard VIP</a></p>
<p style="color: #aaa; font-size: 0.8em; margin-top: 30px; text-align: center;">Triple Elite VIP - Predictions Football</p>
</div>
</body>
</html>
"""

    msg.attach(MIMEText(corps_html, 'html'))

    try:
        serveur = smtplib.SMTP('smtp.gmail.com', 587)
        serveur.starttls()
        serveur.login(EMAIL_EXPEDITEUR, MOT_DE_PASSE_APP)
        serveur.send_message(msg)
        serveur.quit()
        print(f"Email envoye a {email_destinataire}")
        return True
    except Exception as e:
        print(f"Erreur envoi email : {e}")
        return False