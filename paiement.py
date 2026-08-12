import sys
import os
sys.path.insert(0, r"C:\Users\HP\Desktop\Triple_Elite_VIP")

from flask import Flask, render_template_string, request, redirect, jsonify
from license_manager import LicenseManager
from email_sender import envoyer_licence_async
import paydunya

app = Flask(__name__)
lm = LicenseManager()

# Configuration PayDunya
paydunya.api_keys = {
    "PAYDUNYA-MASTER-KEY": "**************************",
    "PAYDUNYA-PRIVATE-KEY": "**************************",
    "PAYDUNYA-TOKEN": "**************************"
}

# Mode test (True) ou production (False)
paydunya.mode = "test"  # Passez à "live" pour la production

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
        .btn-pay { background: #0070ba; color: #fff; }
        .btn-pay:hover { background: #005ea6; }
        .back-link { color: #ffd700; margin-top: 20px; display: inline-block; }
        .info { color: #aaa; font-size: 0.9em; margin: 15px 0; }
        .success-box { background: #1a3a1a; border: 2px solid #4caf50; padding: 20px; border-radius: 10px; margin-top: 20px; }
        .success-box h2 { color: #4caf50; }
        .license-key { background: #0d1137; padding: 12px; font-size: 1em; color: #ffd700; font-family: monospace; border-radius: 5px; margin: 10px 0; word-break: break-all; }
        .error { color: #f44336; margin: 10px 0; }
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
                <li>Support Telegram</li>
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
        
        <button id="pay-btn" class="btn btn-pay" onclick="payer()">
            Payer avec Mobile Money / Carte
        </button>
        
        <p class="info">Orange Money, MTN, Moov, Wave et Carte Bancaire acceptes.</p>
        <p class="info">Apres paiement, votre licence sera envoyee par email.</p>
        
        <div id="error-message" class="error"></div>
        
        <a href="https://triple-elite-vip.com" class="back-link">Retour a l'accueil</a>
    </div>
    
    <script>
        var selectedPlan = 'monthly';
        
        function selectPlan(plan) {
            selectedPlan = plan;
            document.querySelectorAll('.plan').forEach(function(p) { p.classList.remove('selected'); });
            document.getElementById('plan-' + plan).classList.add('selected');
        }
        
        function payer() {
            var email = document.getElementById('email').value;
            if (!email) {
                document.getElementById('error-message').textContent = 'Veuillez entrer votre email';
                return;
            }
            
            document.getElementById('pay-btn').disabled = true;
            document.getElementById('pay-btn').textContent = 'Redirection vers PayDunya...';
            
            fetch('/payer', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: email, plan: selectedPlan })
            })
            .then(function(response) { return response.json(); })
            .then(function(data) {
                if (data.url) {
                    window.location.href = data.url;
                } else {
                    document.getElementById('error-message').textContent = data.error || 'Erreur';
                    document.getElementById('pay-btn').disabled = false;
                    document.getElementById('pay-btn').textContent = 'Payer avec Mobile Money / Carte';
                }
            });
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

@app.route('/payer', methods=['POST'])
def payer():
    try:
        data = request.json
        email = data.get('email')
        plan = data.get('plan')
        
        if plan == 'monthly':
            amount = 30000  # 30€ en FCFA (environ 19680 FCFA, ajustez)
            plan_nom = "Mensuel"
            duree = 1
        else:
            amount = 60000  # 60€
            plan_nom = "Annuel"
            duree = 12
        
        # Créer la facture PayDunya
        invoice = paydunya.Invoice()
        invoice.add_item("Triple Elite VIP", amount)
invoice.total_amount = amount
        invoice.description = "Logiciel de predictions football"
        
        # URLs de retour
        invoice.return_url = "https://triple-elite-vip-paiement.onrender.com/succes?email=" + email + "&plan=" + plan_nom + "&duree=" + str(duree)
        invoice.cancel_url = "https://triple-elite-vip-paiement.onrender.com/paiement"
        
        if invoice.create():
            return jsonify({'url': invoice.invoice_url})
        else:
            return jsonify({'error': 'Erreur creation facture'})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/succes')
def succes():
    email = request.args.get('email')
    plan = request.args.get('plan', 'Mensuel')
    duree = int(request.args.get('duree', 1))
    
    if email:
        license_key = lm.generate_license(email, duree)
        envoyer_licence_async(email, license_key, plan)
        print(f"Licence envoyee a {email}")
        
        return f"""
        <!DOCTYPE html>
        <html lang="fr">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Paiement reussi</title>
            <style>
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                body {{ font-family: 'Segoe UI', sans-serif; background: #0a0e27; color: #fff; text-align: center; }}
                .container {{ max-width: 500px; margin: 50px auto; padding: 30px; background: #1a1f3a; border-radius: 15px; }}
                h1 {{ color: #4caf50; margin-bottom: 20px; }}
                .key {{ background: #0d1137; padding: 15px; font-size: 1.2em; color: #ffd700; font-family: monospace; border-radius: 5px; margin: 20px 0; }}
                .btn {{ background: #ffd700; color: #0a0e27; padding: 15px 40px; font-weight: bold; border-radius: 5px; text-decoration: none; display: inline-block; margin: 10px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Paiement reussi !</h1>
                <p>Votre cle de licence :</p>
                <div class="key">{license_key}</div>
                <p style="color:#aaa;">Elle a aussi ete envoyee par email.</p>
                <a href="https://triple-elite-vip.com/login" class="btn">Se connecter</a>
            </div>
        </body>
        </html>
        """
    
    return redirect('/paiement')

@app.route('/ipn-paydunya', methods=['POST'])
def ipn_paydunya():
    data = request.json
    print("IPN PayDunya:", data)
    return "OK", 200

if __name__ == '__main__':
    print("Page de paiement : http://localhost:5001/paiement")
    app.run(host='0.0.0.0', port=5001, debug=True)