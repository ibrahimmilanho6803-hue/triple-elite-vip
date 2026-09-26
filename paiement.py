import os
import requests
from flask import Flask, request, redirect, jsonify
from license_manager import LicenseManager
from email_sender import envoyer_licence_async
import config

app = Flask(__name__)
lm = LicenseManager()

# SECURITE : les cles PayDunya (LIVE) etaient codees en dur dans ce fichier
# et poussees sur un depot GitHub PUBLIC. Elles doivent etre regenerees dans
# le tableau de bord PayDunya, puis definies UNIQUEMENT via ces variables
# d'environnement sur Render (jamais dans le code).
PAYDUNYA_MASTER_KEY = os.environ.get("PAYDUNYA_MASTER_KEY")
PAYDUNYA_PRIVATE_KEY = os.environ.get("PAYDUNYA_PRIVATE_KEY")
PAYDUNYA_TOKEN = os.environ.get("PAYDUNYA_TOKEN")

BASE_URL = os.environ.get("PAIEMENT_BASE_URL", "https://triple-elite-vip-paiement.onrender.com")

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
        .btn:disabled { opacity: 0.6; cursor: not-allowed; }
        .btn-pay { background: #0070ba; color: #fff; }
        .btn-pay:hover { background: #005ea6; }
        .back-link { color: #ffd700; margin-top: 20px; display: inline-block; }
        .info { color: #aaa; font-size: 0.9em; margin: 15px 0; }
        .error { color: #f44336; margin: 10px 0; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Choisissez votre abonnement</h1>
        <input type="email" id="email" placeholder="Votre adresse E-mail" required>
        <div class="plan selected" id="plan-monthly" onclick="selectPlan('monthly')">
            <h2>Abonnement Mensuel</h2>
            <div class="price">{{PRICE_MONTHLY}}<span>/ 1mois</span></div>
            <ul>
                <li>Acces complet au logiciel</li>
                <li>3 combines optimises par semaine</li>
                <li>Support Telegram</li>
            </ul>
        </div>
        <div class="plan" id="plan-yearly" onclick="selectPlan('yearly')">
            <h2>Abonnement Annuel</h2>
            <div class="price">{{PRICE_YEARLY}}<span>/ 1an</span></div>
            <ul>
                <li>Tout l'abonnement mensuel</li>
                <li>Support prioritaire</li>
            </ul>
        </div>
        <button id="pay-btn" class="btn btn-pay" onclick="payer()">Payer avec Mobile Money / Carte</button>
        <p class="info">Orange Money, MTN, Moov, Wave et Carte Bancaire acceptes.</p>
        <p class="info">Apres paiement, votre licence sera envoyee par E-mail.</p>
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
            if (!email) { document.getElementById('error-message').textContent = 'Veuillez entrer votre email'; return; }
            document.getElementById('pay-btn').disabled = true;
            document.getElementById('pay-btn').textContent = 'Redirection vers PayDunya...';
            fetch('/payer', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: email, plan: selectedPlan })
            })
            .then(function(response) { return response.json(); })
            .then(function(data) {
                if (data.url) { window.location.href = data.url; }
                else {
                    document.getElementById('error-message').textContent = data.error || 'Erreur';
                    document.getElementById('pay-btn').disabled = false;
                    document.getElementById('pay-btn').textContent = 'Payer avec Mobile Money / Carte';
                }
            })
            .catch(function() {
                document.getElementById('error-message').textContent = 'Erreur de connexion';
                document.getElementById('pay-btn').disabled = false;
                document.getElementById('pay-btn').textContent = 'Payer avec Mobile Money / Carte';
            });
        }
    </script>
</body>
</html>
""".replace("{{PRICE_MONTHLY}}", f"{config.PRICE_MONTHLY:,} {config.DEVISE}".replace(",", " ")) \
   .replace("{{PRICE_YEARLY}}", f"{config.PRICE_YEARLY:,} {config.DEVISE}".replace(",", " "))


@app.route('/')
def accueil():
    return redirect('/paiement')


@app.route('/paiement')
def paiement():
    return PAGE_PAIEMENT


@app.route('/payer', methods=['POST'])
def payer():
    if not (PAYDUNYA_MASTER_KEY and PAYDUNYA_PRIVATE_KEY and PAYDUNYA_TOKEN):
        print("ERREUR: cles PAYDUNYA_MASTER_KEY/PAYDUNYA_PRIVATE_KEY/PAYDUNYA_TOKEN manquantes")
        return jsonify({'error': "Paiement momentanement indisponible, merci de reessayer plus tard."}), 503
    try:
        data = request.get_json(silent=True) or {}
        email = (data.get('email') or '').strip()
        plan = data.get('plan')
        if not email or '@' not in email:
            return jsonify({'error': 'Adresse email invalide'}), 400

        if plan == 'yearly':
            amount = config.PRICE_YEARLY
            plan_nom = "Annuel"
            duree = 12
        else:
            amount = config.PRICE_MONTHLY
            plan_nom = "Mensuel"
            duree = 1

        response = requests.post(
            "https://app.paydunya.com/api/v1/checkout-invoice/create",
            json={
                "invoice": {
                    "items": [{"name": "Triple Elite VIP - " + plan_nom, "quantity": 1, "unit_price": amount, "total_price": amount}],
                    "total_amount": amount,
                    "description": "Logiciel de predictions football"
                },
                "store": {"name": "Triple Elite VIP", "website_url": "https://triple-elite-vip.com"},
                "actions": {
                    # PayDunya ajoute automatiquement "?token=<invoice_token>"
                    # a cette URL de retour. On ne fait plus confiance a des
                    # informations passees par le client (email/plan/duree
                    # dans l'URL) : elles sont retrouvees cote serveur via ce
                    # token dans /succes, et le paiement est reverifie
                    # aupres de PayDunya avant d'emettre la licence.
                    "return_url": f"{BASE_URL}/succes",
                    "cancel_url": f"{BASE_URL}/paiement"
                }
            },
            headers={
                "PAYDUNYA-MASTER-KEY": PAYDUNYA_MASTER_KEY,
                "PAYDUNYA-PRIVATE-KEY": PAYDUNYA_PRIVATE_KEY,
                "PAYDUNYA-TOKEN": PAYDUNYA_TOKEN,
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        result = response.json()
        if result.get("response_code") == "00":
            token = result.get("token")
            lm.create_pending_order(token, email, plan_nom, duree)
            return jsonify({'url': result.get("invoice_url")})
        else:
            print(f"Erreur creation facture PayDunya: {result}")
            return jsonify({'error': result.get("response_text", "Erreur PayDunya")})
    except Exception as e:
        print(f"Erreur /payer: {e}")
        return jsonify({'error': "Une erreur est survenue, merci de reessayer."}), 500


def _confirmer_paiement_paydunya(token):
    """Verifie AUPRES DE PAYDUNYA (et non via les parametres d'URL fournis
    par le navigateur) que la facture correspondant a ce token a bien ete
    payee. Renvoie True seulement si PayDunya confirme response_code == "00"
    et status == "completed"."""
    try:
        response = requests.get(
            f"https://app.paydunya.com/api/v1/checkout-invoice/confirm/{token}",
            headers={
                "PAYDUNYA-MASTER-KEY": PAYDUNYA_MASTER_KEY,
                "PAYDUNYA-PRIVATE-KEY": PAYDUNYA_PRIVATE_KEY,
                "PAYDUNYA-TOKEN": PAYDUNYA_TOKEN,
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        result = response.json()
        return result.get("response_code") == "00" and result.get("status") == "completed"
    except Exception as e:
        print(f"Erreur confirmation PayDunya: {e}")
        return False


@app.route('/succes')
def succes():
    token = request.args.get('token')
    if not token:
        return redirect('/paiement')

    order = lm.get_pending_order(token)
    if not order:
        return "<h1 style='color:#f44336;text-align:center;padding:50px;'>Commande introuvable.</h1><p style='text-align:center;'><a href='/paiement' style='color:#ffd700;'>Retour</a></p>", 404

    if order["processed"]:
        return "<h1 style='color:green;text-align:center;padding:50px;'>Paiement deja confirme. Verifiez votre boite mail.</h1><p style='text-align:center;'><a href='https://triple-elite-vip.com/login'>Se connecter</a></p>"

    if not (PAYDUNYA_MASTER_KEY and PAYDUNYA_PRIVATE_KEY and PAYDUNYA_TOKEN):
        return "<h1 style='color:#f44336;text-align:center;padding:50px;'>Verification du paiement indisponible pour le moment.</h1>", 503

    if not _confirmer_paiement_paydunya(token):
        return ("<h1 style='color:#ff9800;text-align:center;padding:50px;'>Paiement non confirme.</h1>"
                "<p style='text-align:center;'>Si vous avez ete debite, contactez-nous a "
                f"{config.SELLER_EMAIL} avec votre reference.</p>"
                "<p style='text-align:center;'><a href='/paiement' style='color:#ffd700;'>Reessayer</a></p>")

    license_key = lm.generate_license(order["email"], order["duree"])
    lm.mark_order_processed(token)
    envoyer_licence_async(order["email"], license_key, order["plan"])
    return "<h1 style='color:green;text-align:center;padding:50px;'>Paiement reussi ! Licence envoyee par email.</h1><p style='text-align:center;'><a href='https://triple-elite-vip.com/login'>Se connecter</a></p>"


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)
