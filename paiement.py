import sys
import os
sys.path.insert(0, r"C:\Users\HP\Desktop\Triple_Elite_VIP")

from flask import Flask, render_template_string, request, redirect
from license_manager import LicenseManager
from email_sender import envoyer_licence_async
import uuid

app = Flask(__name__)
lm = LicenseManager()

PAYPAL_LINK_MENSUEL = "https://www.paypal.com/ncp/payment/VQLWDAY9P9RYQ"
# Ajoutez un autre lien pour l'annuel quand vous l'aurez créé
PAYPAL_LINK_ANNUEL = "https://www.paypal.com/ncp/payment/VOTRE_AUTRE_LIEN"

PAGE_PAIEMENT = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Triple Elite VIP - Abonnement</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: #0a0e27; color: #fff; text-align: center; }
        .container { max-width: 500px; margin: 20px auto; padding: 20px; background: #1a1f3a; border-radius: 15px; }
        h1 { color: #ffd700; font-size: 1.5em; margin-bottom: 20px; }
        .plan { border: 2px solid #333; padding: 20px; margin: 10px 0; border-radius: 10px; cursor: pointer; transition: 0.3s; text-align: left; }
        .plan:hover { border-color: #ffd700; }
        .plan.selected { border-color: #ffd700; background: #0d1137; }
        .plan h2 { color: #ffd700; font-size: 1.2em; }
        .plan .price { font-size: 2em; color: #ffd700; font-weight: bold; margin: 10px 0; }
        .plan .price span { font-size: 0.4em; }
        .plan ul { list-style: none; color: #aaa; font-size: 0.9em; }
        .plan ul li { margin: 5px 0; }
        input[type=email] { width: 100%; padding: 12px; margin: 15px 0; background: #0d1137; border: 1px solid #333; color: #fff; border-radius: 5px; font-size: 1em; }
        .btn { background: #ffd700; color: #0a0e27; padding: 15px; font-weight: bold; border-radius: 5px; cursor: pointer; border: none; font-size: 1.1em; margin-top: 15px; width: 100%; text-decoration: none; display: block; text-align: center; }
        .btn:hover { background: #ffed4a; }
        .btn:disabled { background: #555; cursor: not-allowed; }
        .btn-paypal { background: #0070ba; color: #fff; }
        .btn-paypal:hover { background: #005ea6; }
        .back-link { color: #ffd700; margin-top: 20px; display: inline-block; }
        .info { color: #aaa; font-size: 0.9em; margin: 15px 0; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Choisissez votre abonnement</h1>
        
        <input type="email" id="email" placeholder="Votre adresse email" required>
        
        <div class="plan selected" id="plan-monthly" onclick="selectPlan('monthly')">
            <h2>Abonnement Mensuel</h2>
            <div class="price">30€<span>/ 1mois</span></div>
            <ul>
                <li>Acces complet au logiciel</li>
                <li>3 combinés optimisés par semaine</li>
                <li>Support Télégram</li>
            </ul>
        </div>
        
        <div class="plan" id="plan-yearly" onclick="selectPlan('yearly')">
            <h2>Abonnement Annuel</h2>
            <div class="price">60€<span>/ 1ans</span></div>
            <ul>
                <li>Tout l'abonnement mensuel</li>
                <li>Support prioritaire</li>
            </ul>
        </div>
        
        <a id="paypal-btn" href="PAYPAL_LINK_MENSUEL" class="btn btn-paypal" onclick="return verifierEmail()">
            Payer avec PayPal
        </a>
        
        <p class="info">Vous serez redirige vers PayPal pour finaliser le paiement.</p>
        <p class="info">Apres paiement, votre licence sera envoyee par email.</p>
        
        <a href="https://triple-elite-vip.com" class="back-link">Retour a l'accueil</a>
    </div>
    
    <script>
        var selectedPlan = 'monthly';
        var linkMensuel = '""" + PAYPAL_LINK_MENSUEL + """';
        var linkAnnuel = '""" + PAYPAL_LINK_ANNUEL + """';
        
        function selectPlan(plan) {
            selectedPlan = plan;
            document.querySelectorAll('.plan').forEach(function(p) { p.classList.remove('selected'); });
            document.getElementById('plan-' + plan).classList.add('selected');
            document.getElementById('paypal-btn').href = (plan === 'monthly') ? linkMensuel : linkAnnuel;
        }
        
        function verifierEmail() {
            var email = document.getElementById('email').value;
            if (!email) {
                alert('Veuillez entrer votre adresse email');
                return false;
            }
            // Stocker l'email pour envoi de licence
            localStorage.setItem('email_achat', email);
            return true;
        }
    </script>
</body>
</html>
"""

@app.route('/')
def accueil():
    return redirect('/paiement')

@app.route('/paiement')
def paiement():
    return PAGE_PAIEMENT

@app.route('/generer-licence', methods=['POST'])
def generer_licence():
    email = request.form.get('email')
    plan = request.form.get('plan', 'Mensuel')
    duree = 12 if 'Annuel' in plan else 1
    license_key = lm.generate_license(email, duree)
    envoyer_licence_async(email, license_key, plan)
    return f"Licence envoyee a {email}: {license_key}"

if __name__ == '__main__':
    print("Page de paiement : http://localhost:5001/paiement")
    app.run(host='0.0.0.0', port=5001, debug=True)