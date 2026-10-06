import json
import threading
import time

import pytest

import analyzerv2
import config
from analyzerv2 import MatchAnalyzer, parse_ai_json
from data_collector import DataCollector
from helpers import poisson_ai
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


def test_prompt_contient_ids_definitions_et_contexte(db):
    collector = DataCollector()
    collector.save_match({"idEvent": "1", "strHomeTeam": "H100", "strAwayTeam": "Z", "intHomeScore": "2",
                          "intAwayScore": "0", "dateEvent": "2026-09-01", "strStatus": "FT"}, "Serie A")
    collector.save_match({"idEvent": "2", "strHomeTeam": "A100", "strAwayTeam": "H100", "intHomeScore": "1",
                          "intAwayScore": "1", "dateEvent": "2026-09-08", "strStatus": "FT"}, "Serie A")
    collector.collect_all_data = None
    collector.update_team_stats("H100")
    analyzer = MatchAnalyzer(client=FakeClient(lambda p, n: "[]"))
    prompt = analyzer._build_prompt(matches(1))
    assert "MATCH id=100" in prompt and "H100 (domicile)" in prompt
    assert "STRICTEMENT supérieur" in prompt and "au_moins_1_marque_1_5" in prompt
    assert "2 matchs : 1V 1N 0D" in prompt                # bilan de la saison
    assert "V H100 2-0 Z" in prompt                       # forme récente
    assert "H2H : A100 1-1 H100" in prompt


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
