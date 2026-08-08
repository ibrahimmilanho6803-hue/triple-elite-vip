import sys
import os
sys.path.insert(0, r"C:\Users\HP\Desktop\Triple_Elite_VIP")

from flask import Flask, jsonify, request, redirect
from license_manager import LicenseManager
from email_sender import envoyer_licence
import stripe

app = Flask(__name__)
lm = LicenseManager()

try:
    from config_secret import STRIPE_SECRET_KEY, STRIPE_PUBLIC_KEY
except:
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_PUBLIC_KEY = os.environ.get("STRIPE_PUBLIC_KEY", "")

stripe.api_key = STRIPE_SECRET_KEY

PRICE_MONTHLY = 3000
PRICE_YEARLY = 6000

PAGE_PAIEMENT = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Triple Elite VIP - Abonnement</title>
    <script src="https://js.stripe.com/v3/"></script>
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
        .btn { background: #ffd700; color: #0a0e27; padding: 15px; font-weight: bold; border-radius: 5px; cursor: pointer; border: none; font-size: 1.1em; margin-top: 15px; width: 100%; }
        .btn:hover { background: #ffed4a; }
        .btn:disabled { background: #555; cursor: not-allowed; }
        .success-box { background: #1a3a1a; border: 2px solid #4caf50; padding: 20px; border-radius: 10px; margin-top: 20px; }
        .success-box h2 { color: #4caf50; }
        .license-key { background: #0d1137; padding: 12px; font-size: 1em; color: #ffd700; font-family: monospace; border-radius: 5px; margin: 10px 0; word-break: break-all; }
        .error { color: #f44336; margin: 10px 0; }
        .back-link { color: #ffd700; margin-top: 20px; display: inline-block; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Paiement de votre abonnement</h1>
        
        <form id="payment-form">
            <input type="email" id="email" placeholder="Votre adresse email" required>
            
            <div class="plan" onclick="selectPlan('monthly')" id="plan-monthly">
                <h2>Abonnement Mensuel</h2>
                <div class="price">30€<span>/ 1mois</span></div>
                <ul>
                    <li>Accès complet au logiciel</li>
                    <li>3 combinés optimisés par semaine</li>
                    <li>Support Télégram</li>
                </ul>
            </div>
            
            <div class="plan" onclick="selectPlan('yearly')" id="plan-yearly">
                <h2>Abonnement Annuel</h2>
                <div class="price">60€<span>/ 1ans</span></div>
                <ul>
                    <li>Tout l'abonnement mensuel</li>
                    <li>Support prioritaire</li>
                </ul>
            </div>
            
            <button type="submit" class="btn" id="submit-btn" disabled>Payer maintenant</button>
            <p class="error" id="error-message"></p>
        </form>
        
        <div id="success-box" style="display:none;">
    <div class="success-box">
        <h2>Paiement reussi !</h2>
        <p>Votre clé de licence :</p>
        <div class="license-key" id="license-key"></div>
        <p style="color:#aaa;">Conservez cette cle precieusement</p>
        <p style="color:#aaa;">Vérifiez vos SPAM si vous ne trouvez pas le message</p>
    </div>
    <a href="http://localhost:5000/login" class="btn">Se connecter</a>
</div>
        </div>
        
        <a href="/" class="back-link">Retour a l'accueil</a>
    </div>
    
    <script>
        let selectedPlan = null;
        const stripe = Stripe('pk_test_51TwMZ5Ju2VrqoyZcnVQ7lpytN500HS8GArYmLzikDKzyW4dZ8gUjz1Y1vkxQLFGHPsORnSgsw3rv6UvphFBb2UZU00ZV5Runx6');
        
        function selectPlan(plan) {
            selectedPlan = plan;
            document.querySelectorAll('.plan').forEach(p => p.classList.remove('selected'));
            document.getElementById('plan-' + plan).classList.add('selected');
            document.getElementById('submit-btn').disabled = false;
        }
        
        document.getElementById('payment-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const email = document.getElementById('email').value;
            if (!email || !selectedPlan) return;
            
            document.getElementById('submit-btn').disabled = true;
            document.getElementById('submit-btn').textContent = 'Redirection vers Stripe...';
            document.getElementById('error-message').textContent = '';
            
            try {
                const response = await fetch('/create-checkout-session', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email: email, plan: selectedPlan })
                });
                
                const session = await response.json();
                
                if (session.error) {
                    document.getElementById('error-message').textContent = session.error;
                    document.getElementById('submit-btn').disabled = false;
                    document.getElementById('submit-btn').textContent = 'Payer maintenant';
                    return;
                }
                
                window.location.href = session.url;
                
            } catch (error) {
                document.getElementById('error-message').textContent = 'Erreur de connexion';
                document.getElementById('submit-btn').disabled = false;
                document.getElementById('submit-btn').textContent = 'Payer maintenant';
            }
        });
        
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('success') === 'true') {
            document.getElementById('payment-form').style.display = 'none';
            document.getElementById('success-box').style.display = 'block';
            document.getElementById('license-key').textContent = urlParams.get('key');
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

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        data = request.json
        email = data.get('email')
        plan = data.get('plan')
        
        if plan == 'monthly':
            duration_months = 1
            amount = PRICE_MONTHLY
            name = "Triple Elite VIP - Mensuel"
            plan_nom = "Mensuel (30)"
        else:
            duration_months = 12
            amount = PRICE_YEARLY
            name = "Triple Elite VIP - Annuel"
            plan_nom = "Annuel (60)"
        
        license_key = lm.generate_license(email, duration_months)
        
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {
                        'name': name,
                        'description': 'Logiciel de predictions football - 3 combines/semaine',
                    },
                    'unit_amount': amount,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url='http://localhost:5001/succes?key=' + license_key + '&email=' + email,
            cancel_url='http://localhost:5001/paiement',
            customer_email=email,
            metadata={'license_key': license_key}
        )
        
        return jsonify({'url': session.url})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/succes')
def succes():
    license_key = request.args.get('key')
    email = request.args.get('email')
    
    if email and license_key:
        envoyer_licence(email, license_key, 'VIP')
    
    return redirect('/paiement?success=true&key=' + license_key)

@app.route('/login')
def login_page():
    return redirect('http://localhost:5000/login')

if __name__ == '__main__':
    print("Page de paiement : http://localhost:5001/paiement")
    app.run(host='0.0.0.0', port=5001, debug=True)