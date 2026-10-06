"""Analyse des matchs par l'IA (Claude).

Les matchs sont envoyés à l'IA par petits lots, EN PARALLÈLE : une analyse de
15 matchs en un seul appel prenait 35 à 95 secondes (et perdait tout si la réponse
était tronquée) ; avec des lots de 3 matchs, la génération complète dure ~15 à
25 secondes et un lot en échec n'empêche pas les autres.

L'IA ne sert qu'à ESTIMER des probabilités à partir des données fournies (forme,
confrontations directes, bilan de saison) ; tout le reste (cohérence, paris composés,
cotes, sélection) est calculé de façon déterministe, voir probabilities.py et
combo_generator.py.
"""
import json
import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

import config
from probabilities import reconcile

log = logging.getLogger(__name__)

class ModelChooser:
    """Modèle d'IA à utiliser : celui de la configuration, puis les modèles de repli si l'API répond
    « modèle introuvable » (404). Partagé par tous les appels : une fois un modèle valide trouvé,
    les générations suivantes l'utilisent directement."""

    def __init__(self, models):
        self.models = list(dict.fromkeys(m for m in models if m))
        self.index = 0
        self._lock = threading.Lock()

    def current(self):
        with self._lock:
            return self.models[self.index]

    def reject(self, model):
        """`model` est introuvable. Renvoie True s'il reste un autre modèle à essayer (ou si un appel
        simultané en a déjà choisi un autre)."""
        with self._lock:
            if self.models[self.index] == model:
                if self.index + 1 >= len(self.models):
                    log.error("modèle d'IA introuvable : %s (aucun modèle de repli)", model)
                    return False
                self.index += 1
                log.error("modèle d'IA introuvable : %s ; essai de %s", model, self.models[self.index])
            return True


SHARED_MODELS = ModelChooser([config.IA_MODEL, *config.IA_FALLBACK_MODELS])

PROMPT_HEADER = """Tu es un analyste football expert. Pour CHAQUE match ci-dessous, estime des probabilités
(des entiers de 0 à 100) en te basant UNIQUEMENT sur les données fournies : forme récente,
confrontations directes (H2H), bilan et moyennes de buts de la saison.

Définitions (temps réglementaire, prolongations exclues) :
- v1 : l'équipe à domicile gagne. v2 : l'équipe à l'extérieur gagne.
- 1x : le domicile gagne OU match nul. 2x : l'extérieur gagne OU match nul.
- over_X_Y : le nombre total de buts du match est STRICTEMENT supérieur à X,Y (over_2_5 = au moins 3 buts).
- under_X_Y : le nombre total de buts du match est STRICTEMENT inférieur à X,Y.
- eq1_over_0_5 / eq2_over_0_5 : l'équipe à domicile / à l'extérieur marque au moins 1 but.
- au_moins_1_marque_X_Y : au moins une des deux équipes marque STRICTEMENT plus de X,Y buts
  (au_moins_1_marque_1_5 = au moins une équipe marque 2 buts ou plus).
- btts_oui : les deux équipes marquent. btts_non : au moins une équipe ne marque pas.

Cohérence attendue : v1 + nul + v2 = 100 ; over_X_Y + under_X_Y = 100 ; btts_oui + btts_non = 100 ;
les probabilités « over » baissent quand la ligne monte.
Sois honnête sur l'incertitude : si les données manquent ou sont partagées, donne des
probabilités proches de 50 plutôt que des valeurs extrêmes injustifiées.

Clés à fournir pour chaque match :
id, home_team, away_team, v1, v2, 1x, 2x,
over_0_5, over_1_5, over_2_5, over_3_5, under_0_5, under_1_5, under_2_5, under_3_5,
eq1_over_0_5, eq2_over_0_5,
au_moins_1_marque_1_5, au_moins_1_marque_2_5, au_moins_1_marque_3_5,
btts_oui, btts_non
"""

PROMPT_FOOTER = """
Réponds UNIQUEMENT avec un tableau JSON valide (un objet par match), commençant par [ et
finissant par ]. Recopie "id", "home_team" et "away_team" tels qu'ils sont donnés. N'écris
rien avant ni après le JSON (aucune explication) et n'utilise que des guillemets doubles."""


def parse_ai_json(text):
    """Liste d'objets JSON extraite de la réponse de l'IA.

    Tolère les balises ```json, du texte autour, et une réponse TRONQUÉE : on
    récupère alors tous les objets complets reçus avant la coupure.
    """
    text = (text or "").replace("```json", "").replace("```", "").strip()
    start = text.find("[")
    if start < 0:
        return []
    end = text.rfind("]")
    if end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except ValueError:
            pass
    # Réponse invalide ou tronquée : on lit les objets un par un.
    decoder = json.JSONDecoder()
    items, pos = [], start + 1
    while pos < len(text):
        while pos < len(text) and text[pos] in " \n\r\t,":
            pos += 1
        if pos >= len(text) or text[pos] != "{":
            break
        try:
            obj, pos = decoder.raw_decode(text, pos)
        except ValueError:
            break
        if isinstance(obj, dict):
            items.append(obj)
    return items


class MatchAnalyzer:
    # En dessous de ces tailles d'échantillon (matchs joués), on plafonne la
    # confiance affichée : on ne peut pas être « sûr à 90 % » d'un pronostic sur
    # une équipe dont on n'a presque pas d'historique. Le plafond s'applique au
    # minimum des deux équipes (le maillon le plus faible).
    CONFIDENCE_CAP_BY_SAMPLE = [
        (15, 90),  # 15 matchs ou plus -> jusqu'à 90 %
        (8, 82),
        (4, 72),
        (0, 60),   # moins de 4 matchs connus -> jamais plus de 60 %
    ]

    def __init__(self, client=None, models=None):
        self.models = models or SHARED_MODELS
        self.conn = sqlite3.connect(config.DB_PATH)
        self.cursor = self.conn.cursor()
        if client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                log.error("ANTHROPIC_API_KEY manquante : l'analyse IA est impossible")
            # max_retries=0 : le SDK retente 2 fois par défaut en interne, EN PLUS
            # de nos propres tentatives (voir _ask_ai). La durée maximale d'un
            # appel serait alors imprévisible. Un seul niveau de retry : le nôtre.
            client = anthropic.Anthropic(api_key=api_key, max_retries=0)
        self.client = client

    # ------------------------------------------------------------------
    # Données de contexte pour l'IA
    # ------------------------------------------------------------------

    def get_team_stats(self, team_name):
        self.cursor.execute(
            "SELECT matches_played, wins, draws, losses, goals_for, goals_against, btts_yes, btts_no "
            "FROM team_stats WHERE team_name = ?", (team_name,))
        row = self.cursor.fetchone()
        if not row:
            return None
        return {
            "matches_played": int(row[0] or 0),
            "wins": int(row[1] or 0),
            "draws": int(row[2] or 0),
            "losses": int(row[3] or 0),
            "goals_for_avg": float(row[4] or 0),
            "goals_against_avg": float(row[5] or 0),
            "btts_yes": int(row[6] or 0),
            "btts_no": int(row[7] or 0),
        }

    def get_recent_form(self, team_name, limit=5):
        self.cursor.execute('''
            SELECT home_team, away_team, home_score, away_score, date
            FROM matches
            WHERE (home_team = ? OR away_team = ?)
            AND home_score IS NOT NULL AND away_score IS NOT NULL
            ORDER BY date DESC
            LIMIT ?
        ''', (team_name, team_name, limit))
        form = []
        for home, away, hs, aws, _date in self.cursor.fetchall():
            own, other = (hs, aws) if team_name == home else (aws, hs)
            letter = "V" if own > other else ("N" if own == other else "D")
            form.append(f"{letter} {home} {hs}-{aws} {away}")
        return form

    def get_h2h(self, home_team, away_team, limit=5):
        self.cursor.execute('''
            SELECT home_team, away_team, home_score, away_score, date
            FROM matches
            WHERE ((home_team = ? AND away_team = ?) OR (home_team = ? AND away_team = ?))
            AND home_score IS NOT NULL AND away_score IS NOT NULL
            ORDER BY date DESC
            LIMIT ?
        ''', (home_team, away_team, away_team, home_team, limit))
        return [f"{home} {hs}-{aws} {away}" for home, away, hs, aws, _date in self.cursor.fetchall()]

    def _confidence_cap(self, home_team, away_team):
        """Plafond de confiance selon la quantité de données réellement
        disponibles pour les deux équipes (le maillon le plus faible)."""
        home_stats = self.get_team_stats(home_team)
        away_stats = self.get_team_stats(away_team)
        sample = min(
            home_stats["matches_played"] if home_stats else 0,
            away_stats["matches_played"] if away_stats else 0,
        )
        for threshold, cap in self.CONFIDENCE_CAP_BY_SAMPLE:
            if sample >= threshold:
                return cap
        return self.CONFIDENCE_CAP_BY_SAMPLE[-1][1]

    def _team_summary(self, team_name):
        stats = self.get_team_stats(team_name)
        if not stats:
            return "aucune donnée"
        return (f"{stats['matches_played']} matchs : {stats['wins']}V {stats['draws']}N {stats['losses']}D, "
                f"buts marqués/encaissés par match {stats['goals_for_avg']:.2f}/{stats['goals_against_avg']:.2f}, "
                f"les deux équipes ont marqué dans {stats['btts_yes']} de ces matchs")

    def _build_prompt(self, matches):
        blocks = []
        for m in matches:
            home, away = m["home_team"], m["away_team"]
            home_form = self.get_recent_form(home)
            away_form = self.get_recent_form(away)
            h2h = self.get_h2h(home, away)
            blocks.append(
                f"=== MATCH id={m['id']} | {home} (domicile) vs {away} (extérieur) | {m['league']} ===\n"
                f"Bilan saison {home} : {self._team_summary(home)}\n"
                f"Bilan saison {away} : {self._team_summary(away)}\n"
                f"Forme {home} (5 derniers) : {', '.join(home_form) if home_form else 'N/A'}\n"
                f"Forme {away} (5 derniers) : {', '.join(away_form) if away_form else 'N/A'}\n"
                f"H2H : {', '.join(h2h) if h2h else 'N/A'}\n")
        return f"{PROMPT_HEADER}\nMATCHS :\n{chr(10).join(blocks)}{PROMPT_FOOTER}"

    # ------------------------------------------------------------------
    # Appels à l'IA
    # ------------------------------------------------------------------

    def _ask_ai(self, prompt, expected, label):
        """Un appel à l'IA (2 tentatives). Ne lève jamais : renvoie la meilleure
        liste obtenue (éventuellement partielle, ou vide)."""
        best = []
        attempt = model_switches = 0
        while attempt < config.IA_MAX_ATTEMPTS:
            attempt += 1
            started = time.time()
            text = ""
            model = self.models.current()
            try:
                response = self.client.messages.create(
                    model=model,
                    max_tokens=config.IA_MAX_TOKENS,
                    timeout=config.IA_TIMEOUT,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = "".join(getattr(block, "text", "") for block in response.content)
                items = parse_ai_json(text)
                if len(items) > len(best):
                    best = items
                log.info("IA lot %s tentative %d/%d : %d/%d analyses en %.1fs",
                         label, attempt, config.IA_MAX_ATTEMPTS, len(items), expected, time.time() - started)
                if len(best) >= expected:
                    return best
            except Exception as e:  # réseau, quota, délai dépassé...
                if (getattr(e, "status_code", None) == 404 and model_switches < len(self.models.models)
                        and self.models.reject(model)):
                    model_switches += 1
                    attempt -= 1                     # changer de modèle ne compte pas comme une tentative ratée
                    continue
                log.warning("IA lot %s tentative %d/%d en erreur après %.1fs : %s: %s | début de réponse : %s",
                            label, attempt, config.IA_MAX_ATTEMPTS, time.time() - started,
                            type(e).__name__, e, (text[:120] or "(vide)").replace("\n", " "))
            if attempt < config.IA_MAX_ATTEMPTS:
                time.sleep(1.5 * attempt)
        return best

    def analyze_multiple_matches(self, matches, progress=None):
        """Estimations de l'IA pour tous les matchs : liste d'objets JSON (avec
        "id"). `progress(lots_termines, lots_total)` est appelé à chaque lot terminé."""
        size = max(1, config.IA_BATCH_SIZE)
        batches = [matches[i:i + size] for i in range(0, len(matches), size)]
        if not batches:
            return []
        # Les prompts sont construits ICI (la connexion SQLite n'est pas partagée
        # entre threads) ; seuls les appels réseau partent dans des threads.
        prompts = [self._build_prompt(batch) for batch in batches]
        results = []
        workers = max(1, min(config.IA_MAX_PARALLEL, len(batches)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ia") as pool:
            futures = [pool.submit(self._ask_ai, prompt, len(batch), f"{i + 1}/{len(batches)}")
                       for i, (batch, prompt) in enumerate(zip(batches, prompts))]
            for done, future in enumerate(as_completed(futures), start=1):
                results.extend(future.result())
                if progress:
                    progress(done, len(batches))
        return results

    @staticmethod
    def _norm(name):
        return " ".join((name or "").lower().split())

    def match_analyses(self, matches, ia_items):
        """{id du match: estimation de l'IA}. L'association se fait par "id" (que
        l'IA recopie) et, à défaut, par les noms exacts des équipes : jamais par
        approximation, pour ne pas attribuer l'analyse d'un match à un autre."""
        by_id, by_names = {}, {}
        for item in ia_items:
            if item.get("id") not in (None, ""):
                by_id[str(item["id"]).strip()] = item
            if item.get("home_team") and item.get("away_team"):
                by_names[(self._norm(item["home_team"]), self._norm(item["away_team"]))] = item
        matched = {}
        for m in matches:
            item = by_id.get(str(m["id"]).strip())
            if item is None:
                item = by_names.get((self._norm(m["home_team"]), self._norm(m["away_team"])))
            if item is not None:
                matched[m["id"]] = item
        return matched

    # ------------------------------------------------------------------
    # Analyse d'un match
    # ------------------------------------------------------------------

    def build_analysis_from_ia(self, home_team, away_team, ia_data):
        """Probabilités cohérentes (voir probabilities.reconcile) + plafond de
        confiance lié à la quantité de données disponibles."""
        return {
            "home_team": home_team,
            "away_team": away_team,
            "base": reconcile(ia_data),
            "cap": self._confidence_cap(home_team, away_team),
        }

    def analyze_match(self, home_team, away_team):
        """Analyse de secours SANS IA, pour un match que l'IA n'a pas pu traiter :
        il est exclu des combinés plutôt que présenté avec de faux pourcentages."""
        return {"home_team": home_team, "away_team": away_team, "base": {}, "cap": 0, "fallback": True}

    def close(self):
        self.conn.close()
