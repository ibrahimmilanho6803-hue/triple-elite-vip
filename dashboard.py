import os
import json
import glob
import secrets
from datetime import datetime, timedelta
from functools import wraps
from itertools import combinations, product

from flask import Flask, render_template_string, jsonify, request, session
from werkzeug.utils import secure_filename

import config
from data_collector import DataCollector
from combo_generator import ComboGenerator
from license_manager import LicenseManager

app = Flask(__name__)
# Sans SECRET_KEY defini sur Render, une valeur aleatoire est generee au
# demarrage (les sessions restent valables tant que le processus tourne).
# Definis SECRET_KEY sur Render pour des sessions stables entre redemarrages.
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)

lm = LicenseManager()

RESULTS_DIR = "results"
CACHE_DIR = "cache"
CACHE_FILE = os.path.join(CACHE_DIR, "last_generation.json")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            return jsonify({"error": "Session expiree, merci de te reconnecter."}), 401
        return f(*args, **kwargs)
    return decorated


def _read_cache():
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        generated_at = datetime.fromisoformat(data["generated_at"])
        if datetime.now() - generated_at < timedelta(minutes=config.CACHE_MINUTES):
            return data["combos"], data["generated_at"]
    except Exception:
        pass
    return None, None


def _write_cache(combos):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({"generated_at": datetime.now().isoformat(), "combos": combos}, f, ensure_ascii=False, default=str)
    except Exception as e:
        print(f"Erreur ecriture cache: {e}")


def _save_history(combos):
    try:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        filename = f"{RESULTS_DIR}/combo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(combos, f, indent=2, ensure_ascii=False, default=str)
    except Exception as e:
        print(f"Erreur sauvegarde historique: {e}")


PRICE_MONTHLY_TXT = f"{config.PRICE_MONTHLY:,} {config.DEVISE}".replace(",", " ")
PRICE_YEARLY_TXT = f"{config.PRICE_YEARLY:,} {config.DEVISE}".replace(",", " ")

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
    .hero { padding: 50px 20px; background: linear-gradient(135deg, #1a1a3e, #0d1137); }
    .hero h1 { color: #ffd700; font-size: 2.2em; margin-bottom: 10px; }
    .hero p { color: #aaa; font-size: 0.95em; max-width: 600px; margin: 0 auto; }
    .features { display: flex; justify-content: center; gap: 15px; padding: 30px 20px; flex-wrap: wrap; }
    .feature { background: #1a1f3a; padding: 20px; border-radius: 10px; width: 220px; }
    .feature h3 { color: #ffd700; font-size: 1em; margin-bottom: 8px; }
    .feature p { color: #aaa; font-size: 0.85em; }
    .pricing { padding: 30px 20px; }
    .pricing h2 { color: #ffd700; font-size: 1.5em; margin-bottom: 20px; }
    .price-cards { display: flex; justify-content: center; gap: 15px; flex-wrap: wrap; }
    .price-card { background: #1a1f3a; padding: 25px 20px; border-radius: 10px; width: 200px; border: 2px solid #333; }
    .price-card.premium { border-color: #ffd700; }
    .price { font-size: 1.6em; color: #ffd700; font-weight: bold; }
    .price span { font-size: 0.4em; color: #aaa; }
    .btn { background: #ffd700; color: #0a0e27; padding: 10px 20px; font-weight: bold; border-radius: 5px; text-decoration: none; display: inline-block; margin: 8px 5px; font-size: 0.9em; }
    .btn:hover { background: #ffed4a; }
    .btn-green { background: #4caf50; color: #fff; }
    .btn-green:hover { background: #66bb6a; }
    .disclaimer { color: #888; font-size: 0.78em; max-width: 640px; margin: 20px auto 0; line-height: 1.5; }
        @media (max-width: 768px) {
        .hero h1 { font-size: 2em; }
        .hero h1 { font-size: 1.8em !important; }
        .hero p { font-size: 0.85em; }
        .features { gap: 10px; padding: 20px 10px; }
        .feature { width: 100%; max-width: 300px; }
        .price-cards { gap: 10px; }
        .price-card { width: 100%; max-width: 280px; }
        .btn { padding: 10px 15px; font-size: 0.85em; display: block; width: 90%; margin: 8px auto; }
    }
    @media (max-width: 480px) {
        .hero h1 { font-size: 1.3em; }
        .price { font-size: 1.3em; }
    }
    </style>
</head>
<body>
    <div class="hero">
        <h1>Triple Elite VIP</h1>
        <p>Le logiciel qui analyse 3 championnats et genere des combines optimises a 2.50+ chaque semaine</p>
        <a href="https://triple-elite-vip-paiement.onrender.com" class="btn btn-green">S'abonner maintenant</a>
        <a href="/login" class="btn">Acces Client VIP</a>
    </div>
    <div class="features">
        <div class="feature">
            <h3>Premier League</h3>
            <p>Analyse complete du championnat Anglais</p>
        </div>
        <div class="feature">
            <h3>La Liga</h3>
            <p>Analyse complete du championnat Espagnol</p>
        </div>
        <div class="feature">
            <h3>Bundesliga</h3>
            <p>Analyse complete du championnat Allemand</p>
        </div>
    </div>
    <div class="pricing">
        <h2>Offres VIP</h2>
        <div class="price-cards">
            <div class="price-card">
                <h3>Mensuel</h3>
                <div class="price">""" + PRICE_MONTHLY_TXT + """<span>/ 1mois</span></div>
                <p>Acces complet</p>
                <p>Combines chaque semaine</p>
                <p>Support Telegram</p>
            </div>
            <div class="price-card premium">
                <h3>Annuel</h3>
                <div class="price">""" + PRICE_YEARLY_TXT + """<span>/ 1an</span></div>
                <p>Acces complet</p>
                <p>Combines chaque semaine</p>
                <p>Support prioritaire</p>
            </div>
        </div>
        <p class="disclaimer">Les pronostics sont generes par une analyse statistique et une IA a partir des
        donnees disponibles (forme recente, confrontations directes, stats de saison). Il s'agit d'estimations,
        pas d'une garantie de resultat : parie de maniere responsable.</p>
        <p style="color:#aaa; margin-top:10px;">Contact : """ + config.SELLER_EMAIL + """</p>
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
    .header { background: linear-gradient(135deg, #1a1a3e, #0d1137); padding: 15px; text-align: center; border-bottom: 2px solid #ffd700; }
    .header h1 { color: #ffd700; font-size: 1.8em; }
    .header p { color: #aaa; margin-top: 5px; font-size: 0.85em; }
    .container { max-width: 1200px; margin: 0 auto; padding: 15px; }
    .combo-card { background: #1a1f3a; border-radius: 10px; padding: 15px; margin: 15px 0; border-left: 4px solid #ffd700; }
    .combo-card h2 { color: #ffd700; font-size: 1.1em; margin-bottom: 8px; }
    .combo-stats { display: flex; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
    .stat { background: #0d1137; padding: 8px 12px; border-radius: 5px; }
    .stat-label { color: #aaa; font-size: 0.7em; }
    .stat-value { color: #ffd700; font-size: 1em; font-weight: bold; }
    .match-row { display: flex; justify-content: space-between; align-items: center; padding: 10px; margin: 6px 0; background: #0d1137; border-radius: 5px; flex-wrap: wrap; gap: 8px; }
    .match-teams { font-size: 0.9em; }
    .match-league { color: #aaa; font-size: 0.7em; }
    .match-prediction { color: #4caf50; font-weight: bold; font-size: 0.85em; }
    .match-odds { color: #ffd700; font-weight: bold; font-size: 0.9em; }
    .match-confidence { color: #2196f3; font-size: 0.85em; }
    .btn { background: #ffd700; color: #0a0e27; border: none; padding: 10px 20px; font-size: 0.9em; font-weight: bold; border-radius: 5px; cursor: pointer; margin: 5px; }
    .btn:hover { background: #ffed4a; }
    .btn-green { background: #4caf50; color: #fff; }
    .login-box { max-width: 400px; margin: 50px auto; background: #1a1f3a; padding: 25px; border-radius: 10px; text-align: center; }
    .login-box input { width: 100%; padding: 10px; margin: 8px 0; background: #0d1137; border: 1px solid #333; color: #fff; border-radius: 5px; }
    .error { color: #f44336; margin: 10px 0; }
    .loading { text-align: center; padding: 30px; color: #ffd700; font-size: 1em; }
    .updated-at { text-align: center; color: #888; font-size: 0.78em; margin-top: -5px; padding-bottom: 10px; }
    .disclaimer { color: #888; font-size: 0.75em; text-align: center; max-width: 700px; margin: 15px auto; line-height: 1.5; }
        @media (max-width: 768px) {
        .header h1 { font-size: 1.6em; }
        .combo-stats { flex-direction: row; }
        .btn { display: inline-block; width: auto; margin: 5px; padding: 10px 20px; }
        .login-box { margin: 20px auto; padding: 20px; }
        .match-row { flex-direction: row; text-align: left; flex-wrap: wrap; gap: 10px; padding: 15px; }
        .match-teams { font-size: 1em; }
        .match-prediction, .match-odds, .match-confidence { font-size: 0.9em; }
    }
    </style>
</head>
<body>
    <div class="header">
        <h1>Triple Elite VIP</h1>
        <p>Predictions Football - 3 Championnats - Combines 2.50+</p>
    </div>
    <div class="container">
        {% if not authenticated %}
        <div class="login-box">
            <h2>Connexion VIP</h2>
            <form method="POST" action="/login">
                <input type="email" name="email" placeholder="E-mail" required>
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
            <button onclick="generateCombos()" class="btn">Generer les combines</button>
            <button onclick="window.location.href='https://triple-elite-vip-paiement.onrender.com'" class="btn btn-green">Renouveler</button>
            <button onclick="showHistory()" class="btn">Historique</button>
            <a href="/logout"><button class="btn" style="background:#f44336;color:#fff;">Deconnexion</button></a>
        </div>
        <p class="disclaimer">La confiance affichee est une estimation statistique et IA basee sur l'historique
        des equipes (forme recente, confrontations directes, stats de saison), pas une garantie de resultat.</p>
        <div id="combos-container">
            <div class="loading" id="loading" style="display:none;">Analyse en cours...</div>
            <div id="updated-at" class="updated-at"></div>
            <div id="results"></div>
        </div>
        {% endif %}
    </div>
    <script>
    function generateCombos() {
        document.getElementById('loading').style.display = 'block';
        document.getElementById('results').innerHTML = '';
        document.getElementById('updated-at').textContent = '';
        fetch('/api/generate')
            .then(function(response) {
                if (response.status === 401) { window.location.href = '/login'; return null; }
                return response.json();
            })
            .then(function(data) {
                if (!data) { return; }
                document.getElementById('loading').style.display = 'none';
                if (data.error) {
                    document.getElementById('results').innerHTML = '<p style="color:#ff9800;">' + data.error + '</p>';
                    return;
                }
                if (data.combos.length === 0) {
                    document.getElementById('results').innerHTML = '<p style="color:#ff9800;">Aucun combine trouve pour le moment</p>';
                    return;
                }
                if (data.cached) {
                    document.getElementById('updated-at').textContent = 'Combines de la derniere analyse (mis a jour regulierement)';
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
            .then(function(response) {
                if (response.status === 401) { window.location.href = '/login'; return null; }
                return response.json();
            })
            .then(function(data) {
                if (!data) { return; }
                var html = '<h2>Historique des generations</h2>';
                if (data.length === 0) { html += '<p>Aucun historique</p>'; }
                else {
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
        if session.get('authenticated'):
            return render_template_string(HTML_TEMPLATE, authenticated=True, error=None)
        return render_template_string(HTML_TEMPLATE, authenticated=False, error=None)

    email = (request.form.get('email') or '').strip()
    license_key = (request.form.get('license_key') or '').strip()
    valid, message = lm.verify_license(email, license_key)
    if valid:
        session['authenticated'] = True
        session['email'] = email
        return render_template_string(HTML_TEMPLATE, authenticated=True, error=None)
    else:
        return render_template_string(HTML_TEMPLATE, authenticated=False, error=message)


@app.route('/logout')
def logout():
    session.clear()
    return render_template_string(HTML_TEMPLATE, authenticated=False, error=None)


@app.route('/api/generate')
@login_required
def api_generate():
    cached, generated_at = _read_cache()
    if cached is not None:
        return jsonify({"combos": cached, "cached": True, "generated_at": generated_at})

    generator = None
    try:
        collector = DataCollector()
        generator = ComboGenerator()
        collector.collect_all_data()
        upcoming = collector.get_upcoming_matches()
        if len(upcoming) < 3:
            return jsonify({"error": "Pas assez de matchs a venir pour le moment, reessaie plus tard."})
        upcoming = upcoming[:6]

        analyses_ia = generator.analyzer.analyze_multiple_matches(upcoming)
        analyses_par_match = {}
        for ia in analyses_ia:
            if ia.get("home_team") and ia.get("away_team"):
                key = f"{ia['home_team']} vs {ia['away_team']}"
                analyses_par_match[key] = ia

        all_preds = []
        for match in upcoming:
            key = f"{match['home_team']} vs {match['away_team']}"
            if key in analyses_par_match:
                analysis = generator.analyzer.build_analysis_from_ia(
                    match["home_team"], match["away_team"], analyses_par_match[key]
                )
            else:
                # Pas d'analyse IA fiable pour ce match precis : on l'exclut
                # (via le fallback neutre) plutot que de lui attribuer par
                # erreur l'analyse d'un autre match.
                analysis = generator.analyzer.analyze_match(match["home_team"], match["away_team"])
            real_odds = generator.get_real_odds(match["home_team"], match["away_team"])
            preds = generator.get_predictions_from_analysis(match, analysis, real_odds)
            all_preds.extend(preds)

        preds_by_match = {}
        for pred in all_preds:
            key = f"{pred['home_team']} vs {pred['away_team']}"
            preds_by_match.setdefault(key, []).append(pred)

        all_combos = []
        match_keys = list(preds_by_match.keys())
        for m1, m2, m3 in combinations(match_keys, 3):
            for p1, p2, p3 in product(preds_by_match[m1], preds_by_match[m2], preds_by_match[m3]):
                combo = [p1, p2, p3]
                total_odds = round(p1["estimated_odds"] * p2["estimated_odds"] * p3["estimated_odds"], 2)
                if total_odds >= config.TARGET_ODDS:
                    avg_conf = sum(p["confidence"] for p in combo) / 3
                    categories = set()
                    for p in combo:
                        t = p["type"]
                        if t in ["V1", "V2", "1X", "2X"]:
                            categories.add("RESULTAT")
                        elif t.startswith("BTTS"):
                            categories.add("BTTS")
                        elif t.startswith("AU_MOINS"):
                            categories.add("AU_MOINS")
                        elif t.startswith("EQ1") or t.startswith("EQ2"):
                            categories.add("EQUIPE")
                        elif "_ET_" in t or "_T1_" in t or "_T2_" in t:
                            categories.add("COMBINE")
                        elif "TOTAL" in t or "+" in t or "-" in t:
                            categories.add("TOTAL")
                    if len(categories) < 2:
                        continue
                    score = round(avg_conf * 0.6 + len(categories) * 10, 1)
                    all_combos.append({
                        "predictions": combo,
                        "total_odds": total_odds,
                        "avg_confidence": round(avg_conf, 1),
                        "score": score,
                        "leagues": list(set(p["league"] for p in combo)),
                        "categories": list(categories)
                    })

        all_combos.sort(key=lambda x: x["score"], reverse=True)

        top = []
        matchs_utilises = set()
        for combo in all_combos:
            combo_matchs = set(f"{p['home_team']} vs {p['away_team']}" for p in combo["predictions"])
            leagues_combo = set(p["league"] for p in combo["predictions"])
            if len(combo_matchs & matchs_utilises) == 0 and len(leagues_combo) >= 2:
                top.append(combo)
                matchs_utilises.update(combo_matchs)
            if len(top) >= config.MAX_COMBOS_RETOURNES:
                break

        if len(top) < config.MAX_COMBOS_RETOURNES:
            for combo in all_combos:
                combo_matchs = set(f"{p['home_team']} vs {p['away_team']}" for p in combo["predictions"])
                if len(combo_matchs & matchs_utilises) == 0:
                    top.append(combo)
                    matchs_utilises.update(combo_matchs)
                if len(top) >= config.MAX_COMBOS_RETOURNES:
                    break

        if not top:
            return jsonify({"error": "Aucun combine assez fiable pour le moment. Reessaie plus tard."})

        _save_history(top)
        _write_cache(top)
        return jsonify({"combos": top, "cached": False})
    except Exception as e:
        print(f"ERREUR /api/generate: {e}")
        return jsonify({"error": "Une erreur est survenue pendant la generation. Merci de reessayer dans quelques minutes."}), 500
    finally:
        if generator is not None:
            generator.close()


@app.route('/api/history')
@login_required
def api_history():
    if not os.path.exists(RESULTS_DIR):
        return jsonify([])
    files = [f for f in os.listdir(RESULTS_DIR) if f.endswith('.json')]
    files.sort(reverse=True)
    return jsonify(files[:20])


@app.route('/api/download/<path:filename>')
@login_required
def api_download(filename):
    safe_name = secure_filename(filename)
    filepath = os.path.join(RESULTS_DIR, safe_name)
    if not safe_name.endswith('.json') or not os.path.exists(filepath):
        return jsonify({"error": "Fichier introuvable"}), 404
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    return app.response_class(content, mimetype='application/json', headers={'Content-Disposition': f'attachment;filename={safe_name}'})


@app.route('/api/clear-history', methods=['POST'])
@login_required
def api_clear_history():
    try:
        files = glob.glob(f"{RESULTS_DIR}/combo_*.json")
        for f in files:
            os.remove(f)
        return jsonify({"success": True, "deleted": len(files)})
    except Exception as e:
        print(f"ERREUR /api/clear-history: {e}")
        return jsonify({"error": "Une erreur est survenue."}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    print("\nDashboard Triple Elite VIP")
    app.run(host='0.0.0.0', port=port, debug=debug)
