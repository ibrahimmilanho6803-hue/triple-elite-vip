"""Analyse des matchs par l'IA (Claude).

Pour chaque match, l'IA reçoit un DOSSIER (voir dossier.py) : niveau des deux équipes, résultats à domicile pour l'une et à
l'extérieur pour l'autre, derniers matchs pesés par la force de l'adversaire, repos, confrontations directes, moyennes du
championnat et un repère statistique (modèle de Poisson). Elle doit ÉCRIRE son analyse (clé "analyse") avant de donner ses
chiffres : c'est cette lecture, faite avant les nombres, qui les rend plus réfléchis et plus cohérents.

Les matchs sont envoyés à l'IA par petits lots, EN PARALLÈLE : une analyse de 15 matchs en un seul appel prenait 35 à 95
secondes (et perdait tout si la réponse était tronquée) ; avec des lots de 3 matchs, un lot en échec n'empêche pas les autres.

L'IA ne sert qu'à ESTIMER des probabilités à partir des données fournies ; tout le reste (cohérence, paris composés, cotes,
sélection) est calculé de façon déterministe, voir probabilities.py et combo_generator.py.
"""
import json
import logging
import os
import random
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

import config
from dossier import DossierBuilder, poisson_view
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

def _poisson_reference():
    """Deux repères de calcul pour l'IA, tirés du même modèle que le repère statistique du dossier (les lire ici évite qu'elle
    fasse mentalement des calculs de Poisson, où elle se trompe) : plus de 1,5 / 2,5 / 3,5 buts selon le total attendu, et chance
    qu'une équipe marque selon ses buts attendus."""
    totals = []
    for total in (2.0, 2.5, 3.0, 3.5):
        view = poisson_view(total / 2, total / 2)
        totals.append(f"{str(total).replace('.', ',')} → {round(100 * view['o15'])} / {round(100 * view['o25'])} / "
                      f"{round(100 * view['o35'])} %")
    scores = []
    for goals in (0.6, 0.8, 1.0, 1.3, 1.6, 2.0):
        scores.append(f"{str(goals).replace('.', ',')} → {round(100 * (1 - 2.718281828 ** -goals))} %")
    return " ; ".join(totals), " ; ".join(scores)


_TOTALS_TABLE, _SCORES_TABLE = _poisson_reference()

PROMPT_HEADER = f"""Tu es un analyste football expert, rigoureux et prudent. Pour CHAQUE match ci-dessous, tu reçois un DOSSIER
construit à partir des résultats réels de la saison en cours : tout ce que tu sais du match est dans ce dossier. Étudie-le comme
un analyste professionnel AVANT de chiffrer quoi que ce soit, puis estime des probabilités (des entiers de 0 à 100).

COMMENT ÉTUDIER UN DOSSIER (dans cet ordre) :
1. NIVEAU : points par match, buts marqués et encaissés, forces offensive et défensive (1,00 = la moyenne du championnat ; une
   défense à 0,80 encaisse 20 % de buts de moins que la moyenne). Compare les deux équipes entre elles.
2. LIEU : l'équipe qui reçoit se juge d'abord sur ses résultats À DOMICILE, l'autre sur ses résultats À L'EXTÉRIEUR ; le bilan
   global passe après. L'avantage du terrain propre au championnat est donné.
3. FORME : les derniers matchs, du plus récent au plus ancien. Pèse chaque résultat par la force de l'adversaire (« pt/m » = ses
   points par match cette saison) : un 3-0 contre le dernier du classement vaut moins qu'un 1-0 contre un prétendant au titre.
   Repère les séries et la tendance (défense qui s'effondre, attaque en panne...).
4. REPOS : moins de 4 jours depuis le dernier match = fatigue et rotations possibles ; 7 jours ou plus = aucun souci.
5. CONFRONTATIONS DIRECTES : un indice parmi d'autres, d'autant moins fiable que les matchs sont peu nombreux ou anciens.
6. REPÈRE STATISTIQUE : buts attendus et probabilités d'un modèle de Poisson calculé sur TOUS les matchs du championnat (la
   force des adversaires rencontrés est prise en compte). C'est ton point de départ chiffré, PAS la réponse : corrige-le avec ce
   que tu as lu aux points 1 à 5, sans le recopier. Si tu t'en éloignes nettement (plus de 8 points sur une victoire ou sur le
   nul, plus d'un demi-but attendu), dis pourquoi dans "analyse".
7. FIABILITÉ : un échantillon faible (signalé dans le dossier), des signaux contradictoires ou une compétition entre équipes de
   championnats différents doivent te rapprocher des moyennes du championnat, jamais de valeurs extrêmes.

CE QUE TU N'AS PAS : les compositions, les blessés et suspendus, l'enjeu du match (titre, maintien, derby, match de coupe...), la
météo. N'en invente JAMAIS et ne cite aucun joueur ni aucune absence qui ne figure pas dans le dossier ; cette part d'inconnu est
une raison de plus de rester mesuré. Si le dossier contient une ligne MARCHÉ, elle donne les probabilités tirées des cotes de
plusieurs bookmakers, qui, eux, connaissent compositions et absences : pars-en de préférence pour ce qu'elle couvre, et ne t'en
éloigne que pour une raison précise lue dans le dossier.

CALIBRATION (essentiel) : tes pourcentages sont de vraies probabilités, jugées sur des centaines de matchs : quand tu écris 70,
cela doit arriver environ 7 fois sur 10.
- Le football est très incertain : un grand favori à domicile gagne rarement plus de 4 fois sur 5. Ne dépasse pas 85 pour une
  victoire (v1 ou v2) ni 93 pour « victoire ou nul » (1x ou 2x) ; ne mets rien sous 3 ni au-dessus de 97.
- Les buts d'un match (plus de X buts, les deux équipes marquent) sont bien moins prévisibles que le résultat : reste proche des
  moyennes du championnat, et n'avance une valeur éloignée que si le dossier l'impose vraiment.
- Décide d'abord les buts attendus de chaque équipe (nombres décimaux, de 0,3 à 3,5), puis déduis-en tes pourcentages pour que tout
  soit cohérent. Repères d'un modèle de Poisson : total de buts attendus dans le match → plus de 1,5 / 2,5 / 3,5 buts : {_TOTALS_TABLE}.
  Buts attendus d'une équipe → chance qu'elle marque au moins un but : {_SCORES_TABLE}. « Les deux équipes marquent » vaut à peu
  près le produit des chances de marquer des deux équipes, un peu plus quand les deux défenses sont poreuses.

Définitions (temps réglementaire, prolongations exclues) :
- v1 : l'équipe à domicile gagne. v2 : l'équipe à l'extérieur gagne.
- 1x : le domicile gagne OU match nul. 2x : l'extérieur gagne OU match nul.
- over_X_Y : le nombre total de buts du match est STRICTEMENT supérieur à X,Y (over_2_5 = au moins 3 buts).
- under_X_Y : le nombre total de buts du match est STRICTEMENT inférieur à X,Y.
- eq1_over_0_5 / eq2_over_0_5 : l'équipe à domicile / à l'extérieur marque au moins 1 but.
- au_moins_1_marque_X_Y : au moins une des deux équipes marque STRICTEMENT plus de X,Y buts
  (au_moins_1_marque_1_5 = au moins une équipe marque 2 buts ou plus).
- btts_oui : les deux équipes marquent. btts_non : au moins une équipe ne marque pas.

Cohérence attendue : v1 + nul + v2 = 100 (1x = v1 + nul ; 2x = v2 + nul) ; over_X_Y + under_X_Y = 100 ; btts_oui + btts_non = 100 ;
les probabilités « over » baissent quand la ligne monte.

RÉPONSE : pour chaque match, un objet JSON dont les clés sont écrites DANS CET ORDRE (l'analyse vient AVANT les chiffres : écris-la
d'abord, puis laisse-la guider tes nombres) :
1. "id", "home_team", "away_team" : recopiés tels qu'ils sont donnés ;
2. "analyse" : 5 à 7 phrases courtes en français (110 mots au plus), qui suivent les points 1 à 7 et citent des chiffres du
   dossier (pas de généralités) : niveau comparé, lieu, forme avec la force des adversaires, repos, face-à-face, écart avec le
   repère statistique et sa raison, puis le principal facteur d'incertitude. Une seule ligne, sans guillemets doubles, sans
   crochets ni accolades ;
3. "buts_attendus_domicile" et "buts_attendus_exterieur" : nombres décimaux avec un point (1.4) ;
4. les probabilités (entiers de 0 à 100) : v1, v2, 1x, 2x, over_0_5, over_1_5, over_2_5, over_3_5, under_0_5, under_1_5,
   under_2_5, under_3_5, eq1_over_0_5, eq2_over_0_5, au_moins_1_marque_1_5, au_moins_1_marque_2_5, au_moins_1_marque_3_5,
   btts_oui, btts_non.

EXEMPLE DE FORME (équipes et valeurs inventées : ne recopie rien de cet exemple, seulement sa structure) :
[{{"id": "000", "home_team": "Equipe A", "away_team": "Equipe B", "analyse": "A, 2e du championnat (attaque 1,25, défense 0,80), reçoit B, 11e (attaque 0,95, défense 1,10). A a gagné 4 de ses 5 matchs à domicile avec 2,2 buts marqués par match ; B n'a pris que 3 points sur 12 à l'extérieur. A vient de battre deux équipes du haut de tableau (1,9 et 1,7 pt/m), B reste sur deux défaites contre des équipes plus faibles. Repos : 6 jours pour A, 3 pour B. Face-à-face : 4 victoires de A sur 6, indice mineur. Le repère donne 58 % à A ; je monte un peu à cause de la fatigue de B. Incertitude : compositions et absences inconnues.", "buts_attendus_domicile": 1.8, "buts_attendus_exterieur": 0.9, "v1": 60, "v2": 17, "1x": 83, "2x": 40, "over_0_5": 93, "over_1_5": 75, "over_2_5": 51, "over_3_5": 29, "under_0_5": 7, "under_1_5": 25, "under_2_5": 49, "under_3_5": 71, "eq1_over_0_5": 83, "eq2_over_0_5": 59, "au_moins_1_marque_1_5": 64, "au_moins_1_marque_2_5": 32, "au_moins_1_marque_3_5": 12, "btts_oui": 50, "btts_non": 50}}]
"""

PROMPT_FOOTER = """
Réponds UNIQUEMENT avec un tableau JSON valide (un objet par match, dans l'ordre donné), commençant par [ et finissant par ].
N'écris rien avant ni après le JSON et n'utilise que des guillemets doubles."""


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
    # Quantité de données connues (plafond de confiance) et message envoyé à l'IA
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

    def _build_prompt(self, matches, builder=None):
        """Le message envoyé à l'IA pour un lot de matchs : la consigne, puis le dossier de chaque match (voir dossier.py).
        `builder` : DossierBuilder déjà lu (un seul pour tous les lots d'une génération) ; sinon il est lu ici. Si un match porte
        la clé "market" (cotes des bookmakers, voir config.AI_SEES_MARKET), le dossier en reprend l'avis."""
        builder = builder or DossierBuilder(self.conn)
        blocks = [builder.render(match, match.get("market")) for match in matches]
        return f"{PROMPT_HEADER}\nMATCHS :\n{chr(10).join(blocks)}{PROMPT_FOOTER}"

    # ------------------------------------------------------------------
    # Appels à l'IA
    # ------------------------------------------------------------------

    @staticmethod
    def _rate_limit_wait(error):
        """Secondes à patienter avant de réessayer quand l'API répond « trop de requêtes » (429) ou « surchargée » (529), en
        suivant l'en-tête retry-after quand il existe (au plus config.IA_RATE_LIMIT_MAX_WAIT) ; None pour toute autre erreur."""
        if getattr(error, "status_code", None) not in (429, 529):
            return None
        wait = None
        headers = getattr(getattr(error, "response", None), "headers", None)
        try:
            wait = float(headers.get("retry-after"))
        except (AttributeError, TypeError, ValueError):
            pass
        if wait is None or wait != wait:                    # absent, illisible ou « nan »
            wait = 5.0
        return min(max(wait, 1.0), float(config.IA_RATE_LIMIT_MAX_WAIT))

    def _ask_ai(self, prompt, expected, label):
        """Un appel à l'IA (2 tentatives). Ne lève jamais : renvoie la meilleure
        liste obtenue (éventuellement partielle, ou vide). Une limite de débit (429, 529) ne compte pas comme une tentative
        ratée : on patiente le temps indiqué par l'API puis on réessaie, au plus config.IA_RATE_LIMIT_RETRIES fois."""
        best = []
        attempt = model_switches = rate_waits = 0
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
                wait = self._rate_limit_wait(e)
                if wait is not None and rate_waits < config.IA_RATE_LIMIT_RETRIES:
                    rate_waits += 1
                    attempt -= 1                     # une limite de débit n'est pas non plus une tentative ratée
                    log.info("IA lot %s : limite de débit de l'API (%s), nouvel essai dans %.0f s (%d/%d)",
                             label, getattr(e, "status_code", "?"), wait, rate_waits, config.IA_RATE_LIMIT_RETRIES)
                    time.sleep(wait + random.random())      # un peu de hasard : les lots parallèles ne repartent pas ensemble
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
        builder = DossierBuilder(self.conn)                  # la base n'est lue qu'une fois pour tous les lots
        prompts = [self._build_prompt(batch, builder) for batch in batches]
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
