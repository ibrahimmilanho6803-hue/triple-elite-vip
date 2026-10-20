import json
import threading
import time

import pytest

import analyzerv2
import config
from analyzerv2 import MatchAnalyzer, parse_ai_json
from data_collector import DataCollector
from helpers import poisson_ai
from probabilities import reconcile
import random


class Block:
    def __init__(self, text):
        self.text = text


class Response:
    def __init__(self, text):
        self.content = [Block(text)]


class FakeClient:
    """Faux client Anthropic : `responder(prompt, call_number)` renvoie le texte ou lève."""
    def __init__(self, responder):
        self.responder, self.calls, self.lock = responder, [], threading.Lock()
        self.messages = self

    def create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        with self.lock:
            self.calls.append(kwargs)
            number = len(self.calls)
        return Response(self.responder(prompt, number))


def ids_in(prompt):
    import re
    return re.findall(r"MATCH id=(\w+)", prompt)


def answer_for(prompt, seed=0):
    rng = random.Random(seed)
    items = []
    for match_id in ids_in(prompt):
        item = poisson_ai(rng)
        item.update({"id": match_id, "home_team": f"H{match_id}", "away_team": f"A{match_id}"})
        items.append(item)
    return json.dumps(items)


@pytest.fixture()
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(analyzerv2.time, "sleep", lambda s: None)
    DataCollector()      # crée les tables
    return str(tmp_path / "t.db")


def matches(n):
    return [{"id": str(100 + i), "home_team": f"H{100 + i}", "away_team": f"A{100 + i}", "league": "Serie A"}
            for i in range(n)]


# --------------------------------------------------------------------------
# Lecture de la réponse de l'IA
# --------------------------------------------------------------------------

def test_parse_json_normal_et_balises():
    assert parse_ai_json('[{"id": "1", "v1": 50}]') == [{"id": "1", "v1": 50}]
    assert parse_ai_json('```json\n[{"id": "1"}]\n```') == [{"id": "1"}]
    assert parse_ai_json('Voici le résultat :\n[{"id": "1"}]\nBonne chance') == [{"id": "1"}]
    assert parse_ai_json("[]") == []


def test_parse_json_tronque_recupere_les_objets_complets():
    text = '[{"id": "1", "v1": 50}, {"id": "2", "v1": 60}, {"id": "3", "v1": 7'
    assert parse_ai_json(text) == [{"id": "1", "v1": 50}, {"id": "2", "v1": 60}]
    assert parse_ai_json('[{"id": "1"}, {"id": "2", "v1": "abc') == [{"id": "1"}]


def test_parse_json_invalide():
    assert parse_ai_json("") == [] and parse_ai_json(None) == []
    assert parse_ai_json("pas de json ici") == []
    assert parse_ai_json('{"id": "1"}') == []
    assert parse_ai_json('[1, 2, 3]') == []
    assert parse_ai_json('[{"id": "1"} {"id": "2"}]') == [{"id": "1"}, {"id": "2"}]   # virgule oubliée : tolérée


# --------------------------------------------------------------------------
# Appels par lots, en parallèle
# --------------------------------------------------------------------------

def test_lots_de_trois_matchs_en_parallele(db):
    started = []

    def responder(prompt, number):
        started.append(time.time())
        time.sleep(0.2)
        return answer_for(prompt)

    client = FakeClient(responder)
    analyzer = MatchAnalyzer(client=client)
    progress = []
    t0 = time.time()
    result = analyzer.analyze_multiple_matches(matches(15), progress=lambda done, total: progress.append((done, total)))
    elapsed = time.time() - t0
    assert len(client.calls) == 5                         # 15 matchs / lots de 3
    assert len(result) == 15
    assert elapsed < 0.8                                  # 5 x 0,2 s en parallèle, pas en série
    assert sorted(progress) == [(i, 5) for i in range(1, 6)]
    assert all(call["max_tokens"] == config.IA_MAX_TOKENS and call["timeout"] == config.IA_TIMEOUT
               for call in client.calls)
    assert {call["model"] for call in client.calls} == {config.IA_MODEL}
    analyzer.close()


def test_un_lot_en_echec_n_empeche_pas_les_autres(db):
    def responder(prompt, number):
        if "id=100" in prompt:
            raise RuntimeError("API surchargée")
        return answer_for(prompt)

    analyzer = MatchAnalyzer(client=FakeClient(responder))
    result = analyzer.analyze_multiple_matches(matches(9))
    assert len(result) == 6                               # 2 lots sur 3 ; le lot en échec est perdu seul
    analyzer.close()


def test_nouvelle_tentative_apres_json_invalide(db):
    def responder(prompt, number):
        return "désolé, pas de JSON" if number == 1 else answer_for(prompt)

    client = FakeClient(responder)
    analyzer = MatchAnalyzer(client=client)
    assert len(analyzer.analyze_multiple_matches(matches(3))) == 3
    assert len(client.calls) == 2


def test_reponse_tronquee_puis_complete(db):
    def responder(prompt, number):
        full = answer_for(prompt)
        return full[: len(full) // 2] if number == 1 else full

    client = FakeClient(responder)
    analyzer = MatchAnalyzer(client=client)
    assert len(analyzer.analyze_multiple_matches(matches(3))) == 3


def test_reponse_partielle_conservee_si_tout_echoue(db):
    def responder(prompt, number):
        full = json.loads(answer_for(prompt))
        return json.dumps(full[:1])                       # toujours un seul match sur trois

    analyzer = MatchAnalyzer(client=FakeClient(responder))
    assert len(analyzer.analyze_multiple_matches(matches(3))) == 1


def test_echec_total(db):
    def responder(prompt, number):
        raise TimeoutError("délai dépassé")

    client = FakeClient(responder)
    analyzer = MatchAnalyzer(client=client)
    assert analyzer.analyze_multiple_matches(matches(6)) == []
    assert len(client.calls) == 2 * config.IA_MAX_ATTEMPTS
    assert analyzer.analyze_multiple_matches([]) == []


# --------------------------------------------------------------------------
# Association des réponses aux matchs
# --------------------------------------------------------------------------

def test_association_par_id_puis_par_noms_exacts(db):
    analyzer = MatchAnalyzer(client=FakeClient(lambda p, n: "[]"))
    ms = matches(3)
    items = [{"id": "100", "home_team": "n'importe quoi", "away_team": "x", "v1": 60},
             {"home_team": "h101", "away_team": "  A101 ", "v1": 61},          # par noms, casse/espaces ignorés
             {"id": "999", "home_team": "H102 FC", "away_team": "A102", "v1": 62}]   # nom approximatif : refusé
    matched = analyzer.match_analyses(ms, items)
    assert matched["100"]["v1"] == 60 and matched["101"]["v1"] == 61
    assert "102" not in matched


def save_two_matches():
    collector = DataCollector()
    collector.save_match({"idEvent": "1", "strHomeTeam": "H100", "strAwayTeam": "Z", "intHomeScore": "2",
                          "intAwayScore": "0", "dateEvent": "2026-09-01", "strStatus": "FT"}, "Serie A")
    collector.save_match({"idEvent": "2", "strHomeTeam": "A100", "strAwayTeam": "H100", "intHomeScore": "1",
                          "intAwayScore": "1", "dateEvent": "2026-09-08", "strStatus": "FT"}, "Serie A")
    collector.collect_all_data = None
    collector.update_team_stats("H100")


def test_prompt_contient_ids_definitions_et_dossier(db):
    save_two_matches()
    analyzer = MatchAnalyzer(client=FakeClient(lambda p, n: "[]"))
    prompt = analyzer._build_prompt(matches(2))
    assert "MATCH id=100" in prompt and "H100 (domicile)" in prompt
    assert "STRICTEMENT supérieur" in prompt and "au_moins_1_marque_1_5" in prompt
    assert "H100 (DOMICILE) — saison : 2 matchs, 1V 1N 0D" in prompt        # bilan de la saison
    assert "V (D) 2-0 Z" in prompt and "N (E) 1-1 A100" in prompt           # forme récente : lieu et adversaire
    assert "CONFRONTATIONS DIRECTES : 08/09/2026 A100 1-1 H100" in prompt
    assert "REPÈRE STATISTIQUE" in prompt and "CHAMPIONNAT Serie A" in prompt
    assert "H101 (DOMICILE) : aucun match connu" in prompt                    # une équipe sans match est dite sans données
    assert "MARCHÉ" not in prompt.split("MATCHS :")[1]                          # pas de cotes, pas de ligne « marché »
    analyzer.close()


def test_le_dossier_de_chaque_match_est_dans_le_message(db):
    save_two_matches()
    analyzer = MatchAnalyzer(client=FakeClient(lambda p, n: "[]"))
    prompt = analyzer._build_prompt(matches(3))
    assert ids_in(prompt) == ["100", "101", "102"]
    assert prompt.count("REPÈRE STATISTIQUE") >= 3 and prompt.rstrip().endswith("guillemets doubles.")
    analyzer.close()


def test_l_avis_des_bookmakers_n_est_montre_que_s_il_est_fourni(db):
    save_two_matches()
    analyzer = MatchAnalyzer(client=FakeClient(lambda p, n: "[]"))
    match = {**matches(1)[0], "market": {"home": 2.0, "draw": 3.5, "away": 4.0, "over_2.5": 1.9, "under_2.5": 1.9}}
    prompt = analyzer._build_prompt([match])
    assert "MARCHÉ (cotes médianes de plusieurs bookmakers" in prompt and "plus de 2,5 50 %" in prompt
    assert "MARCHÉ (" not in analyzer._build_prompt(matches(1)).split("MATCHS :")[1]
    analyzer.close()


def test_la_base_n_est_lue_qu_une_fois_par_generation(db, monkeypatch):
    built = []
    real_builder = analyzerv2.DossierBuilder

    def counting(conn):
        built.append(1)
        return real_builder(conn)

    monkeypatch.setattr(analyzerv2, "DossierBuilder", counting)
    analyzer = MatchAnalyzer(client=FakeClient(lambda prompt, n: answer_for(prompt)))
    assert len(analyzer.analyze_multiple_matches(matches(15))) == 15
    assert len(built) == 1                                  # 5 lots, une seule lecture de la base
    analyzer.close()


# --------------------------------------------------------------------------
# La consigne : l'IA analyse avant de chiffrer
# --------------------------------------------------------------------------

def prompt_example():
    header = analyzerv2.PROMPT_HEADER
    return header[header.index("[{"):header.rindex("}]") + 2]


def test_la_consigne_demande_l_analyse_avant_les_chiffres():
    header = analyzerv2.PROMPT_HEADER
    answer_format = header[header.index("RÉPONSE :"):]
    assert answer_format.index('"analyse"') < answer_format.index("buts_attendus_domicile") < answer_format.index("v1, v2, 1x, 2x")
    for topic in ("NIVEAU", "LIEU", "FORME", "REPOS", "CONFRONTATIONS DIRECTES", "REPÈRE STATISTIQUE", "FIABILITÉ"):
        assert topic in header
    assert "N'en invente JAMAIS" in header and "compositions" in header and "blessés" in header
    assert "CALIBRATION" in header


def test_l_exemple_de_la_consigne_est_complet_et_coherent():
    item, = parse_ai_json(prompt_example())
    keys = ["v1", "v2", "1x", "2x", "over_0_5", "over_1_5", "over_2_5", "over_3_5", "under_0_5", "under_1_5", "under_2_5",
            "under_3_5", "eq1_over_0_5", "eq2_over_0_5", "au_moins_1_marque_1_5", "au_moins_1_marque_2_5", "au_moins_1_marque_3_5",
            "btts_oui", "btts_non"]
    assert list(item)[:6] == ["id", "home_team", "away_team", "analyse", "buts_attendus_domicile", "buts_attendus_exterieur"]
    assert all(isinstance(item[key], int) for key in keys)
    assert len(item["analyse"].split()) <= 110 and not any(char in item["analyse"] for char in '"[]{}\n')
    base = reconcile(item)                                  # les chiffres de l'exemple sont déjà cohérents : rien ne bouge
    assert base["v1"] == item["v1"] and base["v2"] == item["v2"] and round(base["x"]) == 100 - item["v1"] - item["v2"]
    assert item["1x"] == item["v1"] + round(base["x"]) and item["2x"] == item["v2"] + round(base["x"])
    assert item["over_2_5"] + item["under_2_5"] == 100 and item["btts_oui"] + item["btts_non"] == 100
    assert item["over_1_5"] > item["over_2_5"] > item["over_3_5"]


def test_les_reperes_de_poisson_de_la_consigne_sont_justes():
    totals, scores = analyzerv2._poisson_reference()
    assert "2,5 → 71 / 46 / 24 %" in totals and "3,0 → 80 / 58 / 35 %" in totals
    assert "1,0 → 63 %" in scores and "2,0 → 86 %" in scores and "0,6 → 45 %" in scores
    assert totals in analyzerv2.PROMPT_HEADER and scores in analyzerv2.PROMPT_HEADER


def test_les_champs_en_plus_ne_gênent_pas_la_lecture_des_chiffres(db):
    """L'IA renvoie maintenant du texte (analyse) et des buts attendus avec ses chiffres : tout est conservé, rien ne casse."""
    answer = json.dumps([{**poisson_ai(random.Random(1)), "id": "100", "home_team": "H100", "away_team": "A100",
                          "analyse": "A reçoit B. Repère : 55 %. [Incertitude] élevée.", "buts_attendus_domicile": 1.6,
                          "buts_attendus_exterieur": 1.0}])
    analyzer = MatchAnalyzer(client=FakeClient(lambda p, n: answer))
    items = analyzer.analyze_multiple_matches(matches(1))
    analysis = analyzer.build_analysis_from_ia("H100", "A100", analyzer.match_analyses(matches(1), items)["100"])
    assert analysis["base"]["v1"] is not None and analysis["base"]["o25"] is not None
    analyzer.close()


# --------------------------------------------------------------------------
# Plafond de confiance
# --------------------------------------------------------------------------

def test_plafond_selon_la_taille_de_l_echantillon(db):
    import sqlite3
    conn = sqlite3.connect(db)
    for team, played in (("Riche", 20), ("Moyenne", 9), ("Debut", 5), ("Nouvelle", 2)):
        conn.execute("INSERT INTO team_stats (team_name, matches_played, wins, draws, losses, goals_for, goals_against, "
                     "btts_yes, btts_no) VALUES (?, ?, 1, 1, 1, 1.5, 1.0, 1, 1)", (team, played))
    conn.commit()
    conn.close()
    analyzer = MatchAnalyzer(client=FakeClient(lambda p, n: "[]"))
    assert analyzer._confidence_cap("Riche", "Riche") == 90
    assert analyzer._confidence_cap("Riche", "Moyenne") == 82       # le maillon le plus faible
    assert analyzer._confidence_cap("Moyenne", "Debut") == 72
    assert analyzer._confidence_cap("Riche", "Nouvelle") == 60
    assert analyzer._confidence_cap("Riche", "Inconnue") == 60
    analysis = analyzer.build_analysis_from_ia("Riche", "Moyenne", {"v1": 60, "v2": 20, "over_1_5": 80})
    assert analysis["cap"] == 82 and analysis["base"]["v1"] == 60 and "fallback" not in analysis
    assert analyzer.analyze_match("a", "b")["fallback"] is True


def test_sans_cle_api_le_client_se_construit(db, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    analyzer = MatchAnalyzer()
    assert analyzer.client is not None
    analyzer.close()


class ModelNotFound(Exception):
    status_code = 404


def test_modele_introuvable_bascule_sur_le_modele_de_repli(db):
    models = analyzerv2.ModelChooser(["modele-inconnu", "modele-retire", "modele-valide"])

    def responder(prompt, number):
        return answer_for(prompt)

    class PickyClient(FakeClient):
        def create(self, **kwargs):
            if kwargs["model"] != "modele-valide":
                with self.lock:
                    self.calls.append(kwargs)
                raise ModelNotFound("model: " + kwargs["model"])
            return super().create(**kwargs)

    client = PickyClient(responder)
    analyzer = MatchAnalyzer(client=client, models=models)
    result = analyzer.analyze_multiple_matches(matches(3))
    assert len(result) == 3                                              # un seul lot, deux changements de modèle
    assert [c["model"] for c in client.calls][:3] == ["modele-inconnu", "modele-retire", "modele-valide"]
    assert models.current() == "modele-valide"                           # mémorisé pour les générations suivantes
    client.calls.clear()
    analyzer.analyze_multiple_matches(matches(3))
    assert {c["model"] for c in client.calls} == {"modele-valide"}
    analyzer.close()


def test_aucun_modele_valide_renvoie_une_liste_vide_sans_boucler(db):
    models = analyzerv2.ModelChooser(["a", "b"])

    class AlwaysMissing(FakeClient):
        def create(self, **kwargs):
            with self.lock:
                self.calls.append(kwargs)
            raise ModelNotFound("introuvable")

    client = AlwaysMissing(lambda p, n: "[]")
    analyzer = MatchAnalyzer(client=client, models=models)
    assert analyzer.analyze_multiple_matches(matches(3)) == []
    assert len(client.calls) <= 4
    analyzer.close()


def test_choix_du_modele_par_defaut_et_repli_sans_doublon():
    chooser = analyzerv2.ModelChooser(["x", "x", "", "y"])
    assert chooser.models == ["x", "y"] and chooser.current() == "x"
    assert chooser.reject("x") is True and chooser.current() == "y"
    assert chooser.reject("x") is True                                   # un autre appel avait déjà changé de modèle
    assert chooser.reject("y") is False
    assert analyzerv2.SHARED_MODELS.models[0] == config.IA_MODEL


# --------------------------------------------------------------------------
# Limites de débit de l'API (429, 529)
# --------------------------------------------------------------------------

class Headers(dict):
    pass


class RateLimited(Exception):
    def __init__(self, status_code=429, retry_after=None):
        super().__init__(f"erreur {status_code}")
        self.status_code = status_code
        self.response = type("R", (), {"headers": Headers({} if retry_after is None else {"retry-after": retry_after})})()


def sleeps_of(monkeypatch):
    waits = []
    monkeypatch.setattr(analyzerv2.time, "sleep", lambda seconds: waits.append(seconds))
    monkeypatch.setattr(analyzerv2.random, "random", lambda: 0.0)
    return waits


def test_une_limite_de_debit_fait_patienter_puis_reessayer_sans_compter_une_tentative(db, monkeypatch):
    waits = sleeps_of(monkeypatch)

    def responder(prompt, number):
        if number <= 3:                                     # plus d'essais que IA_MAX_ATTEMPTS, mais ce ne sont pas des échecs
            raise RateLimited(429, "3")
        return answer_for(prompt)

    client = FakeClient(responder)
    analyzer = MatchAnalyzer(client=client)
    assert len(analyzer.analyze_multiple_matches(matches(3))) == 3
    assert len(client.calls) == 4 and waits == [3.0, 3.0, 3.0]            # l'attente demandée par l'API (retry-after)
    analyzer.close()


def test_attente_bornee_et_valeur_par_defaut(db, monkeypatch):
    waits = sleeps_of(monkeypatch)
    sequence = [RateLimited(429, "600"), RateLimited(529, None), RateLimited(429, "pas un nombre"), RateLimited(429, "0")]

    def responder(prompt, number):
        if number <= len(sequence):
            raise sequence[number - 1]
        return answer_for(prompt)

    analyzer = MatchAnalyzer(client=FakeClient(responder))
    analyzer.analyze_multiple_matches(matches(3))
    # 600 s demandées -> plafonnées ; 529 sans en-tête et en-tête illisible -> 5 s ; 0 s -> au moins 1 s. Au plus RETRIES attentes.
    assert waits[:config.IA_RATE_LIMIT_RETRIES] == [float(config.IA_RATE_LIMIT_MAX_WAIT), 5.0, 5.0][:config.IA_RATE_LIMIT_RETRIES]
    analyzer.close()


def test_une_limite_de_debit_persistante_finit_par_abandonner(db, monkeypatch):
    waits = sleeps_of(monkeypatch)
    client = FakeClient(lambda prompt, number: (_ for _ in ()).throw(RateLimited(429, "2")))
    analyzer = MatchAnalyzer(client=client)
    assert analyzer.analyze_multiple_matches(matches(3)) == []
    # IA_RATE_LIMIT_RETRIES attentes gratuites, puis les IA_MAX_ATTEMPTS tentatives habituelles : le total est borné.
    assert len(client.calls) == config.IA_RATE_LIMIT_RETRIES + config.IA_MAX_ATTEMPTS
    assert len([w for w in waits if w == 2.0]) == config.IA_RATE_LIMIT_RETRIES
    analyzer.close()


def test_les_autres_erreurs_ne_font_pas_patienter_comme_une_limite_de_debit(db, monkeypatch):
    waits = sleeps_of(monkeypatch)

    class Broken(Exception):
        status_code = 500

    def responder(prompt, number):
        raise Broken("panne")

    client = FakeClient(responder)
    analyzer = MatchAnalyzer(client=client)
    assert analyzer.analyze_multiple_matches(matches(3)) == []
    assert len(client.calls) == config.IA_MAX_ATTEMPTS and 5.0 not in waits and 20.0 not in waits
    assert analyzer._rate_limit_wait(Broken()) is None and analyzer._rate_limit_wait(ValueError()) is None
    analyzer.close()
