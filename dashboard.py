import sys
import os
sys.path.insert(0, r"C:\Users\HP\Desktop\Triple_Elite_VIP")

from flask import Flask, render_template_string, jsonify, request
from data_collector import DataCollector
from combo_generator import ComboGenerator
from license_manager import LicenseManager
from datetime import datetime
import json
import glob

app = Flask(__name__)
lm = LicenseManager()

PAGE_ACCUEIL = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Triple Elite VIP - Accueil</title>
    <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: 'Segoe UI', sans-serif; background: #0a0e27; color: #fff; text-align: center; }
    .hero { padding: 80px 20px; background: linear-gradient(135deg, #1a1a3e, #0d1137); }
    .hero h1 { color: #ffd700; font-size: 3em; margin-bottom: 10px; }
    .hero p { color: #aaa; font-size: 1.2em; max-width: 600px; margin: 0 auto; }
    .features { display: flex; justify-content: center; gap: 30px; padding: 50px 20px; flex-wrap: wrap; }
    .feature { background: #1a1f3a; padding: 30px; border-radius: 10px; width: 280px; }
    .feature h3 { color: #ffd700; margin-bottom: 10px; }
    .feature p { color: #aaa; }
    .pricing { padding: 50px 20px; }
    .pricing h2 { color: #ffd700; margin-bottom: 30px; }
    .price-cards { display: flex; justify-content: center; gap: 20px; flex-wrap: wrap; }
    .price-card { background: #1a1f3a; padding: 40px 30px; border-radius: 10px; width: 250px; border: 2px solid #333; }
    .price-card.premium { border-color: #ffd700; }
    .price { font-size: 2.5em; color: #ffd700; font-weight: bold; }
    .price span { font-size: 0.4em; color: #aaa; }
    .btn { background: #ffd700; color: #0a0e27; padding: 15px 40px; font-weight: bold; border-radius: 5px; text-decoration: none; display: inline-block; margin: 20px 10px; }
    .btn:hover { background: #ffed4a; }
    .btn-green { background: #4caf50; color: #fff; }
    .btn-green:hover { background: #66bb6a; }
    @media (max-width: 768px) {
        .hero { padding: 40px 15px; }
        .hero h1 { font-size: 2em; }
        .hero p { font-size: 1em; }
        .features { gap: 15px; padding: 30px 10px; }
        .feature { width: 100%; max-width: 300px; padding: 20px; }
        .pricing { padding: 30px 10px; }
        .pricing h2 { font-size: 1.8em; }
        .price-cards { gap: 15px; }
        .price-card { width: 100%; max-width: 300px; padding: 25px 20px; }
        .price { font-size: 2em; }
        .btn { padding: 12px 25px; font-size: 0.95em; margin: 10px 5px; }
    }
    @media (max-width: 480px) {
        .hero h1 { font-size: 1.6em; }
        .hero p { font-size: 0.9em; }
        .btn { display: block; width: 80%; margin: 10px auto; }
        .price { font-size: 1.8em; }
    }
</style>
</head>
<body>
    <div class="hero">
        <h1>Triple Elite VIP</h1>
        <p>Le logiciel qui analyse 3 championnats et génère 3 combinés optimisers a 2.50+ chaque semaine</p>
        <a href="https://triple-elite-vip-paiement.onrender.com" class="btn btn-green">S'abonner maintenant</a>
        <a href="/login" class="btn">Accès Client VIP</a>
    </div>
    <div class="features">
        <div class="feature">
            <h3>Premier League</h3>
            <p>Analyse complète du championnat Anglais</p>
        </div>
        <div class="feature">
            <h3>La Liga</h3>
            <p>Analyse complète du championnat Espagnol</p>
        </div>
        <div class="feature">
            <h3>Bundesliga</h3>
            <p>Analyse complète du championnat Allemand</p>
        </div>
    </div>
    <div class="pricing">
        <h2>Offres VIP</h2>
        <div class="price-cards">
            <div class="price-card">
                <h3>Mensuel</h3>
                <div class="price">30€<span>/ 1mois</span></div>
                <p>Accès complet</p>
                <p>3 combinés/semaine</p>
                <p>Support Télégram</p>
            </div>
            <div class="price-card premium">
                <h3>Annuel</h3>
                <div class="price">60€<span>/ 1ans</span></div>
                <p>Accès complet</p>
                <p>3 combinés/semaine</p>
                <p>Support prioritaire</p>
            </div>
        </div>
        <p style="color:#aaa; margin-top:20px;">Contact : tripleelitevip@gmail.com</p>
    </div>
</body>
</html>
"""

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Triple Elite VIP - Dashboard</title>
    <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0a0e27; color: #fff; }
    .header { background: linear-gradient(135deg, #1a1a3e, #0d1137); padding: 20px; text-align: center; border-bottom: 2px solid #ffd700; }
    .header h1 { color: #ffd700; font-size: 2em; }
    .header p { color: #aaa; margin-top: 5px; }
    .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
    .combo-card { background: #1a1f3a; border-radius: 10px; padding: 20px; margin: 20px 0; border-left: 4px solid #ffd700; }
    .combo-card h2 { color: #ffd700; margin-bottom: 10px; }
    .combo-stats { display: flex; gap: 20px; margin-bottom: 15px; flex-wrap: wrap; }
    .stat { background: #0d1137; padding: 10px 15px; border-radius: 5px; }
    .stat-label { color: #aaa; font-size: 0.8em; }
    .stat-value { color: #ffd700; font-size: 1.2em; font-weight: bold; }
    .match-row { display: flex; justify-content: space-between; align-items: center; padding: 12px; margin: 8px 0; background: #0d1137; border-radius: 5px; flex-wrap: wrap; gap: 8px; }
    .match-teams { font-size: 1.1em; }
    .match-league { color: #aaa; font-size: 0.8em; }
    .match-prediction { color: #4caf50; font-weight: bold; }
    .match-odds { color: #ffd700; font-weight: bold; }
    .match-confidence { color: #2196f3; }
    .btn { background: #ffd700; color: #0a0e27; border: none; padding: 12px 30px; font-size: 1em; font-weight: bold; border-radius: 5px; cursor: pointer; margin: 10px; }
    .btn:hover { background: #ffed4a; }
    .btn-green { background: #4caf50; color: #fff; }
    .btn-green:hover { background: #66bb6a; }
    .login-box { max-width: 400px; margin: 100px auto; background: #1a1f3a; padding: 30px; border-radius: 10px; text-align: center; }
    .login-box input { width: 100%; padding: 10px; margin: 10px 0; background: #0d1137; border: 1px solid #333; color: #fff; border-radius: 5px; }
    .error { color: #f44336; margin: 10px 0; }
    .loading { text-align: center; padding: 50px; color: #ffd700; font-size: 1.2em; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Triple Elite VIP</h1>
        <p>Predictions Football - 3 Championnats - Combinés 2.50+</p>
    </div>
    <div class="container">
        {% if not authenticated %}
        <div class="login-box">
            <h2>Connexion VIP</h2>
            <form method="POST" action="/login">
                <input type="email" name="email" placeholder="Email" required>
                <input type="text" name="license_key" placeholder="Cle de licence" required>
                <button type="submit" class="btn">Se connecter</button>
            </form>
            {% if error %}
            <p class="error">{{ error }}</p>
            {% endif %}
            <p style="margin-top:20px;"><a href="/" style="color:#ffd700;">Retour a l'accueil</a></p>
        </div>
        {% else %}
        <div style="text-align: center; padding: 20px;">
            <button onclick="generateCombos()" class="btn">Générer les combinés</button>
            <button onclick="window.location.href='https://triple-elite-vip.com'" class="btn btn-green">Renouveler</button>
            <button onclick="showHistory()" class="btn">Historique</button>
            <a href="/logout"><button class="btn" style="background:#f44336;color:#fff;">Deconnexion</button></a>
        </div>
        <div id="combos-container">
            <div class="loading" id="loading" style="display:none;">Analyse en cours...</div>
            <div id="results"></div>
        </div>
        {% endif %}
    </div>
    <script>
    function generateCombos() {
    document.getElementById('loading').style.display = 'block';
    document.getElementById('results').innerHTML = '';
    fetch('/api/generate')
        .then(function(response) { return response.json(); })
        .then(function(data) {
            document.getElementById('loading').style.display = 'none';
            if (data.error) {
                document.getElementById('results').innerHTML = '<p style="color:#ff9800; text-align:center; padding:20px; font-size:1.1em;">' + data.error + '</p>';
                return;
            }
            if (data.combos.length === 0) {
                document.getElementById('results').innerHTML = '<p style="color:#ff9800; text-align:center; padding:20px; font-size:1.1em;">Aucun combiné trouver</p>';
                return;
            }
            var html = '';
            data.combos.forEach(function(combo, index) {
                html += '<div class="combo-card">';
                html += '<h2>COMBINE #' + (index + 1) + '</h2>';
                html += '<div class="combo-stats">';
                html += '<div class="stat"><div class="stat-label">Cote totale</div><div class="stat-value">' + combo.total_odds + '</div></div>';
                html += '<div class="stat"><div class="stat-label">Confiance</div><div class="stat-value">' + combo.avg_confidence + '%</div></div>';
                html += '<div class="stat"><div class="stat-label">Score</div><div class="stat-value">' + combo.score + '/100</div></div>';
                html += '</div>';
                combo.predictions.forEach(function(p) {
                    html += '<div class="match-row">';
                    html += '<div><div class="match-teams">' + p.home_team + ' vs ' + p.away_team + '</div>';
                    html += '<div class="match-league">' + p.league + '</div></div>';
                    html += '<div class="match-prediction">' + p.type_name + '</div>';
                    html += '<div class="match-odds">Cote: ' + p.estimated_odds + '</div>';
                    html += '<div class="match-confidence">' + p.confidence + '%</div>';
                    html += '</div>';
                });
                html += '</div>';
            });
            document.getElementById('results').innerHTML = html;
        })
        .catch(function(error) {
            document.getElementById('loading').style.display = 'none';
            document.getElementById('results').innerHTML = '<p class="error">Erreur de connexion</p>';
        });
}
    function showHistory() {
        fetch('/api/history')
            .then(function(response) { return response.json(); })
            .then(function(data) {
                var html = '<h2>Historique des générations</h2>';
                if (data.length === 0) {
                    html += '<p>Aucun historique</p>';
                } else {
                    data.forEach(function(file) {
                        html += '<p>' + file + ' <a href="/api/download/' + file + '" style="color:#ffd700;">Telecharger</a></p>';
                    });
                }
                document.getElementById('results').innerHTML = html;
            });
    }
    </script>
</body>
</html>
"""
@app.route('/')
def accueil():
    return PAGE_ACCUEIL

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template_string(HTML_TEMPLATE, authenticated=False, error=None)
    email = request.form.get('email')
    license_key = request.form.get('license_key')
    valid, message = lm.verify_license(email, license_key)
    if valid:
        return render_template_string(HTML_TEMPLATE, authenticated=True, error=None)
    else:
        return render_template_string(HTML_TEMPLATE, authenticated=False, error=message)

@app.route('/logout')
def logout():
    return render_template_string(HTML_TEMPLATE, authenticated=False, error=None)

@app.route('/api/generate')
def api_generate():
    try:
        collector = DataCollector()
        generator = ComboGenerator()
        collector.collect_all_data()
        upcoming = collector.get_upcoming_matches()
        
        if len(upcoming) < 3:
            return jsonify({"error": "Pas assez de matchs (minimum 3 requis)"})
        
        all_preds = []
        for match in upcoming:
            preds = generator.get_match_predictions(match)
            all_preds.extend(preds)
        
        from itertools import combinations, product
        preds_by_match = {}
        for pred in all_preds:
            key = f"{pred['home_team']} vs {pred['away_team']}"
            if key not in preds_by_match:
                preds_by_match[key] = []
            preds_by_match[key].append(pred)
        
        all_combos = []
        for m1, m2, m3 in combinations(preds_by_match.keys(), 3):
            for p1, p2, p3 in product(preds_by_match[m1], preds_by_match[m2], preds_by_match[m3]):
                combo = [p1, p2, p3]
                total_odds = round(p1["estimated_odds"] * p2["estimated_odds"] * p3["estimated_odds"], 2)
                if total_odds >= 2.50:
                    avg_conf = sum(p["confidence"] for p in combo) / 3
                    score = round(avg_conf * 0.6 + len(set(p["type"] for p in combo)) * 5 + len(set(p["league"] for p in combo)) * 5, 1)
                    all_combos.append({
                        "predictions": combo,
                        "total_odds": total_odds,
                        "avg_confidence": round(avg_conf, 1),
                        "score": score
                    })

print(f"Matchs a venir: {len(upcoming)}")
print(f"Pronostics valides: {len(all_preds)}")
print(f"Combinaisons cote >= 2.50: {len(all_combos)}")
        
        all_combos.sort(key=lambda x: x["score"], reverse=True)
        top3 = all_combos[:3]
        
        generator.close()
        return jsonify({"combos": top3})
    except Exception as e:
        print(f"ERREUR: {e}")
        return jsonify({"error": str(e)})

@app.route('/api/history')
def api_history():
    if not os.path.exists("results"):
        return jsonify([])
    files = [f for f in os.listdir("results") if f.endswith('.json')]
    files.sort(reverse=True)
    return jsonify(files[:20])

@app.route('/api/download/<filename>')
def api_download(filename):
    if not os.path.exists(f"results/{filename}"):
        return "Fichier introuvable", 404
    with open(f"results/{filename}", 'r', encoding='utf-8') as f:
        content = f.read()
    return app.response_class(content, mimetype='application/json', headers={'Content-Disposition': f'attachment;filename={filename}'})

@app.route('/api/clear-history')
def api_clear_history():
    try:
        files = glob.glob("results/combo_*.json")
        for f in files:
            os.remove(f)
        return jsonify({"success": True, "deleted": len(files)})
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == '__main__':
    print("\nDashboard Triple Elite VIP")
    print("Page d'accueil : http://localhost:5000")
    print("Connexion VIP : http://localhost:5000/login")
    app.run(host='0.0.0.0', port=5000, debug=True)