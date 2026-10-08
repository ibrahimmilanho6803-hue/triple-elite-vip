"""Vitrine publique (showcase.py) : ce que les pages publiques ont le droit de montrer, et jamais plus.

Le point essentiel : aucun combiné à venir ne sort de ce module, sauf le seul combiné gratuit. Tout le reste (bilan, cache,
mise à jour des scores en arrière-plan, partage) sert ce principe sans jamais coûter un appel payant ni bloquer une requête.
"""
import copy
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import combo_history
import config
import showcase
from showcase import Showcase, is_upcoming, public_results, share_links
from site_fakes import history_with, played_combo, sample_combos

NOW = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, now=NOW.timestamp()):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta).total_seconds()


class FakeService:
    """Ce que Showcase lit d'un GenerationService : le dossier de cache et la dernière génération (jamais rien d'autre)."""

    def __init__(self, cache_dir, entry=None):
        self.cache_dir = str(cache_dir)
        self.entry = entry
        self.reads = 0

    def latest(self):
        self.reads += 1
        return self.entry

    def __getattr__(self, name):                    # tout autre appel (génération...) serait un défaut du module
        raise AssertionError(f"la vitrine ne doit jamais appeler service.{name}")


def generation(now=NOW, age=timedelta(hours=1), combos=None):
    made = now - age
    return {"generated_ts": made.timestamp(), "generated_at": made.isoformat(),
            "combos": combos if combos is not None else sample_combos(now), "meta": {}}


def make(tmp_path, entry=None, clock=None, cache="cache", **kwargs):
    """cache : sous-dossier de cache. Plusieurs make() dans un même test doivent avoir chacun le leur : le combiné gratuit
    choisi est enregistré dans ce dossier et ressortirait sinon dans le make() suivant."""
    clock = clock or Clock()
    kwargs.setdefault("pick_ttl", 0)
    service = FakeService(tmp_path / cache, entry)
    return Showcase(service, loader=kwargs.pop("loader", lambda refresh: history_with(won=1)), clock=clock, **kwargs), service, clock


# --------------------------------------------------------------------------
# Bilan public
# --------------------------------------------------------------------------

def test_le_bilan_ne_compte_que_des_combines_joues():
    data = history_with(won=3, lost=2, pending=2, unfinished=1, void=1, now=NOW)
    bilan = public_results(data["combos"])
    assert bilan["combos_settled"] == 6 and bilan["won"] == 3 and bilan["lost"] == 3     # 2 perdus + 1 perdu pas fini
    assert bilan["pending"] == 2                                                         # ni comptés ni détaillés
    assert bilan["lost_unfinished"] == 1
    assert [c["status"] for c in bilan["recent"]] == ["won", "won", "won", "lost", "lost"]
    assert bilan["recent_total"] == 5


def test_un_combine_a_venir_ou_pas_fini_n_est_jamais_detaille():
    data = history_with(won=2, lost=1, pending=3, unfinished=2, void=1, now=NOW)
    shown = json.dumps(public_results(data["combos"]), ensure_ascii=False)
    for combo in data["combos"]:
        names = {leg["home_team"] for leg in combo["predictions"]}
        incomplete = any(leg["outcome"] is None for leg in combo["predictions"]) or combo["status"] == "void"
        for name in names:
            assert (name in shown) is (not incomplete), (combo["status"], name)


def test_les_combines_annules_ne_comptent_ni_ne_s_affichent():
    bilan = public_results(history_with(won=1, void=3, now=NOW)["combos"])
    assert bilan["combos_settled"] == 1 and bilan["recent_total"] == 1


def test_pas_de_pourcentage_sans_assez_de_combines_ni_de_pronostics():
    few = public_results(history_with(won=4, lost=5, now=NOW)["combos"])               # 9 combinés, 27 pronostics
    assert few["combos_settled"] == 9 and few["enough"] is False
    assert few["combo_win_rate"] is None and few["leg_win_rate"] is None and few["avg_chance"] is None
    assert few["won"] == 4 and few["legs_won"] == 22 and few["legs_settled"] == 27       # les chiffres bruts, eux, sont là
    enough = public_results(history_with(won=4, lost=6, now=NOW)["combos"])            # 10 combinés, 30 pronostics
    assert enough["enough"] is True and enough["combo_win_rate"] == 40
    assert enough["leg_win_rate"] == 80 and enough["avg_chance"] == 31


def test_la_chance_moyenne_annoncee_est_celle_des_combines_joues():
    combos = [played_combo(i, "won", NOW, chance=30.0) for i in range(5)] + \
             [played_combo(i + 5, "lost", NOW, chance=35.0) for i in range(5)]
    assert public_results(combos)["avg_chance"] == 32                                    # 32,5 -> 32 (arrondi pair de Python)


def test_un_pronostic_present_dans_plusieurs_combines_compte_une_fois():
    first, second = played_combo(0, "won", NOW), played_combo(1, "lost", NOW)
    second["predictions"][1] = dict(first["predictions"][1])                           # même match, même pronostic
    bilan = public_results([first, second])
    assert bilan["combos_settled"] == 2 and bilan["legs_settled"] == 5                 # 3 + 3 - 1


def test_depuis_quand_et_historique_vide():
    combos = history_with(won=2, lost=1, now=NOW)["combos"]
    assert public_results(combos)["since"] == combos[-1]["generated_at"]               # le plus ancien combiné joué
    empty = public_results([])
    assert empty["combos_settled"] == 0 and empty["since"] is None and empty["recent"] == []


def test_seuls_les_champs_prevus_sortent_du_bilan():
    combo = played_combo(0, "won", NOW)
    combo["secret"] = "ne sort pas"
    for leg in combo["predictions"]:
        leg.update({"match_id": "123", "probability": 66.6, "interne": "ne sort pas"})
    shown = public_results([combo])["recent"][0]
    assert "ne sort pas" not in json.dumps(shown) and "123" not in json.dumps(shown)
    assert set(shown) == {"generated_at", "total_odds", "success_probability", "status", "predictions"}
    assert set(shown["predictions"][0]) == set(showcase._DONE_LEG_FIELDS)


def test_le_bilan_garde_les_douze_derniers_combines_detailles():
    bilan = public_results(history_with(won=20, lost=10, now=NOW)["combos"])
    assert len(bilan["recent"]) == showcase.RECENT_COUNT == 12 and bilan["recent_total"] == 30


# --------------------------------------------------------------------------
# Combiné gratuit : choix
# --------------------------------------------------------------------------

def test_le_combine_gratuit_est_celui_dont_la_chance_est_la_plus_elevee(tmp_path):
    combos = sample_combos(NOW)
    combos[0], combos[2] = combos[2], combos[0]                       # le meilleur n'est plus en tête de liste
    for chance, combo in zip((30.1, 30.4, 31.4), combos):
        combo["success_probability"] = chance
    shown, _, _ = make(tmp_path, generation(combos=combos))
    pick = shown.free_pick()
    assert pick["combo"]["success_probability"] == 31.4
    assert len(pick["combo"]["predictions"]) == 3


def test_le_combine_gratuit_est_seul_et_ne_montre_rien_des_autres(tmp_path):
    pick = make(tmp_path, generation())[0].free_pick()
    page = json.dumps(pick, ensure_ascii=False)
    assert "Arsenal" in page                                           # le combiné 1 de sample_combos
    for other in ("Bayern", "Lille", "Chelsea", "Atlético", "Napoli", "Dortmund"):
        assert other not in page


def test_le_combine_gratuit_ne_garde_que_les_champs_prevus(tmp_path):
    entry = generation()
    entry["combos"][0]["secret"] = "ne sort pas"
    entry["combos"][0]["predictions"][0]["match_id"] = "999"
    pick = make(tmp_path, entry)[0].free_pick()
    assert "ne sort pas" not in json.dumps(pick) and "999" not in json.dumps(pick)
    assert set(pick["combo"]["predictions"][0]) == set(showcase._UPCOMING_LEG_FIELDS)
    assert pick["first_kickoff"] == min(leg["kickoff"] for leg in pick["combo"]["predictions"])


def test_un_combine_dont_un_match_est_proche_ou_commence_n_est_pas_offert(tmp_path):
    entry = generation()
    entry["combos"][0]["predictions"][1]["kickoff"] = (NOW + timedelta(minutes=10)).isoformat()      # < 15 min
    entry["combos"][1]["predictions"][0]["kickoff"] = (NOW - timedelta(minutes=5)).isoformat()       # commencé
    pick = make(tmp_path, entry)[0].free_pick()
    assert pick["combo"]["success_probability"] == 30.1                # le combiné 3, seul resté valable


def test_pas_de_combine_gratuit_sans_heure_de_match_lisible(tmp_path):
    entry = generation()
    for combo in entry["combos"]:
        combo["predictions"][0]["kickoff"] = "bientôt"
    assert make(tmp_path, entry)[0].free_pick() is None
    assert is_upcoming({"predictions": []}, NOW.timestamp(), 0) is False
    assert is_upcoming(None, NOW.timestamp(), 0) is False


def test_pas_de_combine_gratuit_sans_generation_ou_trop_ancienne(tmp_path):
    assert make(tmp_path, None, cache="a")[0].free_pick() is None
    assert make(tmp_path, generation(age=timedelta(hours=25)), cache="b")[0].free_pick() is None
    assert make(tmp_path, generation(age=timedelta(hours=23)), cache="c")[0].free_pick() is not None
    broken = generation()
    broken["generated_ts"] = "hier"
    assert make(tmp_path, broken, cache="d")[0].free_pick() is None


def test_pas_de_combine_gratuit_quand_tous_les_combines_ont_commence(tmp_path):
    shown, _, clock = make(tmp_path, generation())
    assert shown.free_pick() is not None
    clock.advance(days=5)
    assert shown.free_pick() is None


def test_la_page_gratuite_peut_etre_coupee_sans_deploiement(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FREE_PICK_ENABLED", False)
    shown, service, _ = make(tmp_path, generation())
    assert shown.free_pick() is None and service.reads == 0


# --------------------------------------------------------------------------
# Combiné gratuit : un seul par génération, stable dans la journée
# --------------------------------------------------------------------------

def test_le_combine_choisi_reste_le_meme_quand_une_nouvelle_generation_sort(tmp_path):
    shown, service, clock = make(tmp_path, generation())
    first = shown.free_pick()["combo"]
    better = generation(age=timedelta(0), combos=sample_combos(NOW))
    better["combos"][2]["success_probability"] = 45.0                  # une nouvelle génération avec un bien meilleur combiné
    service.entry = better
    clock.advance(hours=2)
    assert shown.free_pick()["combo"] == first                          # le lien partagé le matin montre le même coupon le soir


def test_une_fois_commence_le_combine_n_est_remplace_que_par_une_nouvelle_generation(tmp_path):
    shown, service, clock = make(tmp_path, generation())
    first = shown.free_pick()["combo"]
    first_kickoff = min(leg["kickoff"] for leg in first["predictions"])
    # Les deux autres combinés de la MÊME génération ne prennent pas sa place : ils sont le produit payant.
    other_times = [(NOW + timedelta(days=9)).isoformat()] * 3
    for combo in service.entry["combos"][1:]:
        for leg, kickoff in zip(combo["predictions"], other_times):
            leg["kickoff"] = kickoff
    clock.now = datetime.fromisoformat(first_kickoff).timestamp() - 60
    assert shown.free_pick() is None
    # Une nouvelle génération, elle, en apporte un nouveau.
    service.entry = generation(now=datetime.fromtimestamp(clock.now, tz=timezone.utc), age=timedelta(0))
    renewed = shown.free_pick()
    assert renewed is not None and renewed["combo"]["predictions"][0]["kickoff"] != first["predictions"][0]["kickoff"]


def test_le_combine_choisi_survit_a_un_redemarrage_et_un_fichier_abime_est_ignore(tmp_path):
    shown, service, clock = make(tmp_path, generation())
    first = shown.free_pick()["combo"]
    path = tmp_path / "cache" / "free_pick.json"
    assert json.loads(path.read_text(encoding="utf-8"))["combo"] == first
    service.entry = None                                               # même sans génération disponible
    again = Showcase(service, loader=lambda refresh: history_with(won=1), clock=clock, pick_ttl=0)
    assert again.free_pick()["combo"] == first
    path.write_text("{pas du json", encoding="utf-8")
    service.entry = generation()
    assert Showcase(service, loader=lambda refresh: history_with(), clock=clock, pick_ttl=0).free_pick() is not None


def test_le_combine_gratuit_fonctionne_meme_si_le_disque_est_inutilisable(tmp_path):
    blocked = tmp_path / "pas-un-dossier"
    blocked.write_text("un fichier gêne le dossier de cache", encoding="utf-8")
    service = FakeService(blocked / "cache", generation())
    shown = Showcase(service, loader=lambda refresh: history_with(won=1), clock=Clock(), pick_ttl=0)
    first = shown.free_pick()
    assert first is not None and shown.free_pick() == first             # gardé en mémoire à défaut de disque


def test_le_combine_gratuit_est_garde_quelques_secondes_en_memoire(tmp_path):
    shown, _, clock = make(tmp_path, generation(), pick_ttl=10)
    computed = []
    original = shown._current_pick
    shown._current_pick = lambda now: computed.append(now) or original(now)
    first = shown.free_pick()
    assert shown.free_pick() == first and len(computed) == 1             # une rafale de visiteurs : un seul calcul
    clock.advance(seconds=11)
    assert shown.free_pick() == first and len(computed) == 2


def test_une_panne_du_service_ne_casse_pas_la_page_gratuite(tmp_path):
    class Broken(FakeService):
        def latest(self):
            raise RuntimeError("disque en panne")

    shown = Showcase(Broken(tmp_path / "cache"), loader=lambda refresh: history_with(), clock=Clock(), pick_ttl=0)
    assert shown.free_pick() is None


# --------------------------------------------------------------------------
# Bilan : cache, pannes, mise à jour des scores en arrière-plan
# --------------------------------------------------------------------------

class Loader:
    def __init__(self, data=None, error=None):
        self.calls = []
        self.data = data if data is not None else history_with(won=2, lost=1, now=NOW)
        self.error = error

    def __call__(self, refresh):
        self.calls.append(refresh)
        if self.error:
            raise self.error
        return self.data


def test_le_bilan_est_garde_cinq_minutes(tmp_path):
    loader = Loader()
    shown, _, clock = make(tmp_path, loader=loader)
    first = shown.results()
    assert shown.results() is first and loader.calls == [False]
    clock.advance(seconds=299)
    shown.results()
    assert loader.calls == [False]
    clock.advance(seconds=2)
    shown.results()
    assert loader.calls == [False, False]


def test_une_requete_de_visiteur_ne_va_jamais_chercher_de_score_sur_internet(tmp_path):
    loader = Loader()
    shown, _, _ = make(tmp_path, loader=loader)                         # chargeur de test : pas de mise à jour en arrière-plan
    for _ in range(3):
        shown.results()
    assert loader.calls == [False]                                       # refresh=False : lecture locale seulement


def test_le_bilan_survit_a_une_panne_de_l_historique(tmp_path):
    loader = Loader()
    shown, _, clock = make(tmp_path, loader=loader)
    good = shown.results()
    loader.error = OSError("disque")
    clock.advance(seconds=400)
    assert shown.results() == good                                       # dernière version connue
    calls = len(loader.calls)
    clock.advance(seconds=10)
    shown.results()
    assert len(loader.calls) == calls                                    # pas de nouvel essai à chaque requête pendant 30 s
    clock.advance(seconds=30)
    loader.error = None
    loader.data = history_with(won=5, now=NOW)
    assert shown.results()["won"] == 5                                   # reprise dès que l'historique revient


def test_sans_aucun_bilan_connu_une_panne_donne_none(tmp_path):
    shown, _, _ = make(tmp_path, loader=Loader(error=ValueError("illisible")))
    assert shown.results() is None


def test_les_scores_manquants_sont_recuperes_en_arriere_plan_au_plus_tous_les_quarts_d_heure(tmp_path):
    loader = Loader()
    done = threading.Event()

    def refresh_aware(refresh):
        result = loader(refresh)
        if refresh:
            done.set()
        return result

    shown, _, clock = make(tmp_path, loader=refresh_aware, background_refresh=True)
    shown.results()
    assert done.wait(5)                                                  # le fil de fond a tourné
    for _ in range(100):                                                 # (il se termine tout seul)
        if not shown._refreshing:
            break
        time.sleep(0.01)
    assert loader.calls.count(True) == 1 and loader.calls[0] is False    # la requête, elle, n'a rien attendu
    clock.advance(minutes=14)
    shown.results()
    assert loader.calls.count(True) == 1                                 # pas deux fois en moins d'un quart d'heure
    clock.advance(minutes=2)
    done.clear()
    shown.results()
    assert done.wait(5)
    assert loader.calls.count(True) == 2


def test_la_mise_a_jour_en_arriere_plan_remplace_le_bilan_et_survit_aux_erreurs(tmp_path):
    loader = Loader()
    shown, _, _ = make(tmp_path, loader=loader, background_refresh=False)
    shown.results()
    loader.data = history_with(won=4, now=NOW)
    shown._refresh()                                                     # appelé directement : sans fil, donc déterministe
    assert shown.results()["won"] == 4 and loader.calls[-1] is True
    loader.error = RuntimeError("TheSportsDB indisponible")
    shown._refresh()                                                     # l'erreur est journalisée, rien ne casse
    assert shown._refreshing is False and shown.results()["won"] == 4


def test_le_vrai_chargeur_lit_les_fichiers_et_ne_touche_pas_au_reseau(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    combo_history._next_check.clear()
    monkeypatch.setattr(combo_history.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("réseau interdit dans une requête")))
    os.makedirs(config.RESULTS_DIR)
    leg = {"match_id": "1", "home_team": "A", "away_team": "B", "league": "Serie A", "type": "V1", "estimated_odds": 1.4,
           "confidence": 70, "kickoff": "2026-10-03T14:00:00+00:00"}
    with open(os.path.join(config.RESULTS_DIR, "combo_20261003_100000.json"), "w", encoding="utf-8") as f:
        json.dump([{"predictions": [leg], "total_odds": 2.7, "avg_confidence": 70.0, "success_probability": 34.0}], f)
    shown = Showcase(FakeService(tmp_path / "cache"), clock=Clock(), background_refresh=False)
    bilan = shown.results()
    assert bilan["combos_settled"] == 0 and bilan["pending"] == 1        # score inconnu : en attente, rien d'inventé


# --------------------------------------------------------------------------
# Partage
# --------------------------------------------------------------------------

def test_liens_de_partage(tmp_path):
    pick = make(tmp_path, generation())[0].free_pick()
    links = share_links(pick, "https://triple-elite-vip.com/gratuit")
    assert links["url"] == "https://triple-elite-vip.com/gratuit"
    assert "3 matchs" in links["text"] and "cote 2,55" in links["text"] and "chance estimée 31 %" in links["text"]
    assert " " not in links["text"]                                 # espaces ordinaires : rien d'étrange dans l'application de messagerie
    assert links["whatsapp"].startswith("https://wa.me/?text=")
    assert "https%3A%2F%2Ftriple-elite-vip.com%2Fgratuit" in links["whatsapp"]
    assert links["telegram"].startswith("https://t.me/share/url?url=https%3A%2F%2Ftriple-elite-vip.com%2Fgratuit&text=")
    for link in (links["whatsapp"], links["telegram"]):
        assert " " not in link and "&&" not in link


def test_le_partage_s_adapte_aux_donnees_absentes(tmp_path):
    pick = copy.deepcopy(make(tmp_path, generation())[0].free_pick())
    pick["combo"].update({"total_odds": None, "success_probability": None})      # cote ou chance manquantes
    links = share_links(pick, "https://exemple.org/gratuit")
    assert "cote" not in links["text"] and "chance" not in links["text"] and "3 matchs" in links["text"]
    assert links["text"].endswith("3 matchs.")
