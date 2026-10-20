"""Variété : choix des matchs analysés (variety.choose_pool) et pénalités de la composition (ComboGenerator.build_combos).

Le but : qu'un client qui génère plusieurs fois, ou plusieurs clients qui génèrent le même jour, ne reçoivent pas toujours les
mêmes équipes, sans jamais empêcher une génération quand il y a peu de matchs."""
import dataclasses
from collections import Counter

import pytest

import config
from combo_generator import SERVED_COUNTED_MAX, ComboGenerator
from helpers import FakeAnalyzer, make_all_preds
from variety import Variety, choose_pool


def matches_of(counts):
    """Des matchs {id, league, kickoff} : `counts` = {championnat: nombre de matchs}. Ids "0", "1"... dans l'ordre des
    championnats ; dans chaque championnat, les coups d'envoi sont croissants."""
    matches, n = [], 0
    for league, count in counts.items():
        for position in range(count):
            matches.append({"id": str(n), "league": league, "kickoff": f"2026-10-10T{12 + position % 10:02d}:00:00+00:00"})
            n += 1
    return matches


def ids(matches):
    return [m["id"] for m in matches]


# --------------------------------------------------------------------------
# Variety
# --------------------------------------------------------------------------

def test_les_ids_numeriques_ou_texte_se_valent():
    variety = Variety(seen=frozenset({"7"}), served={"8": 2})
    assert variety.is_seen(7) and variety.is_seen("7") and not variety.is_seen(8)
    assert variety.times_served(8) == 2 and variety.times_served("8") == 2 and variety.times_served(9) == 0


def test_une_variete_vide_ne_change_rien_et_ne_se_modifie_pas():
    empty = Variety()
    assert not empty.is_seen("1") and empty.times_served("1") == 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        empty.seen = frozenset({"1"})


# --------------------------------------------------------------------------
# choose_pool : quels matchs analyser
# --------------------------------------------------------------------------

def test_la_limite_par_defaut_vient_de_la_configuration():
    assert config.MATCHS_PAR_GENERATION == 18
    assert len(choose_pool(matches_of({"A": 30}))) == 18
    assert len(choose_pool(matches_of({"A": 30}), limit=7)) == 7


def test_sous_la_limite_tout_est_garde_dans_l_ordre_d_entree():
    matches = matches_of({"A": 3, "B": 2, "C": 4})
    assert choose_pool(matches) == matches
    assert choose_pool([]) == []
    assert choose_pool(matches_of({"A": 3}), limit=0) == []


def test_un_seul_championnat_remplit_la_liste_a_lui_seul_avec_les_premiers_coups_d_envoi():
    pool = choose_pool(matches_of({"A": 25}))
    assert ids(pool) == [str(n) for n in range(18)]


def test_tous_les_championnats_sont_representes_avant_qu_un_seul_en_ait_trois():
    pool = choose_pool(matches_of({"A": 10, "B": 10, "C": 10}), limit=5)
    assert Counter(m["league"] for m in pool) == {"A": 2, "B": 2, "C": 1}
    pool = choose_pool(matches_of({"A": 10, "B": 10, "C": 10}), limit=9)
    assert Counter(m["league"] for m in pool) == {"A": 3, "B": 3, "C": 3}


def test_un_championnat_qui_joue_peu_n_est_pas_oublie_quand_un_autre_deborde():
    pool = choose_pool(matches_of({"A": 30, "B": 2}))
    assert Counter(m["league"] for m in pool) == {"A": 16, "B": 2}


def test_le_resultat_garde_l_ordre_d_entree():
    matches = matches_of({"A": 8, "B": 8})
    pool = choose_pool(matches, limit=6)
    assert ids(pool) == sorted(ids(pool), key=int)


def test_les_matchs_deja_recus_passent_apres_les_autres():
    matches = matches_of({"A": 10})
    variety = Variety(seen=frozenset(str(n) for n in range(6)))                    # 0 à 5 déjà reçus
    assert ids(choose_pool(matches, variety, limit=5)) == ["0", "6", "7", "8", "9"]     # 4 nouveaux + 1 déjà reçu pour compléter


def test_les_matchs_deja_recus_ne_font_pas_perdre_son_tour_a_un_championnat():
    matches = matches_of({"A": 3, "B": 2})                                          # ids 0, 1, 2 (A) et 3, 4 (B)
    variety = Variety(seen=frozenset({"0", "1"}))                                   # les deux premiers de A sont déjà reçus
    assert ids(choose_pool(matches, variety, limit=2)) == ["2", "3"]                # un nouveau de chaque championnat, pas deux de B


def test_avec_assez_de_matchs_nouveaux_aucun_match_deja_recu_n_est_repris():
    matches = matches_of({"A": 15, "B": 15})
    seen = frozenset(str(n) for n in range(0, 30, 4))                              # 8 matchs déjà reçus ; 22 nouveaux
    pool = choose_pool(matches, Variety(seen=seen))
    assert len(pool) == 18 and not any(m["id"] in seen for m in pool)


def test_les_matchs_deja_recus_reviennent_quand_il_n_y_en_a_pas_d_autres():
    matches = matches_of({"A": 4})
    everything = Variety(seen=frozenset(ids(matches)))
    assert choose_pool(matches, everything) == matches                             # un jour pauvre : on redonne, on ne bloque pas


def test_les_matchs_deja_donnes_a_d_autres_clients_passent_apres_a_rang_egal():
    matches = matches_of({"A": 4})
    variety = Variety(served={"0": 3, "1": 1})
    assert ids(choose_pool(matches, variety, limit=2)) == ["2", "3"]
    assert ids(choose_pool(matches, variety, limit=3)) == ["1", "2", "3"]           # "0", le plus distribué, est le dernier


def test_entre_deux_championnats_le_moins_distribue_gagne_le_dernier_rang():
    matches = matches_of({"A": 1, "B": 1})                                          # ids "0" (A) et "1" (B)
    assert ids(choose_pool(matches, Variety(served={"0": 5}), limit=1)) == ["1"]
    assert ids(choose_pool(matches, Variety(served={"1": 5}), limit=1)) == ["0"]
    assert ids(choose_pool(matches, Variety(), limit=1)) == ["0"]                   # à égalité, l'ordre des championnats


# --------------------------------------------------------------------------
# Composition : pénalités
# --------------------------------------------------------------------------

@pytest.fixture()
def gen():
    return ComboGenerator(analyzer=FakeAnalyzer())


def test_aucune_penalite_sans_variete():
    assert ComboGenerator._variety_penalties(["1", "2"], None) == {}
    assert ComboGenerator._variety_penalties(["1", "2"], Variety()) == {}


def test_penalites_forte_pour_un_match_deja_recu_et_moderee_pour_ceux_d_autres_clients():
    variety = Variety(seen=frozenset({"1"}), served={"1": 2, "2": 1, "3": 10})
    penalties = ComboGenerator._variety_penalties(["1", "2", "3", "4"], variety)
    assert penalties == {"1": config.VARIETY_SEEN_PENALTY + 2 * config.VARIETY_SERVED_PENALTY,
                         "2": config.VARIETY_SERVED_PENALTY,
                         "3": SERVED_COUNTED_MAX * config.VARIETY_SERVED_PENALTY}   # plafonnée : "4" n'a aucune pénalité
    # « déjà reçu par CE client » pèse toujours plus lourd que n'importe quel nombre d'autres clients (3 au plus comptés), avec
    # une marge de 20 points au moins : de l'ordre de l'écart de probabilité entre un très bon et un mauvais combiné.
    assert config.VARIETY_SEEN_PENALTY >= SERVED_COUNTED_MAX * config.VARIETY_SERVED_PENALTY + 20


def synthetic(match_id, category, league):
    return {"match_id": match_id, "estimated_odds": 1.4, "category": category, "league": league, "probability": 70.0,
            "type": f"T{match_id}"}


def four_equal_matches():
    """Quatre matchs de même valeur : sans pénalité, tous les triplets se valent."""
    return {m: [synthetic(m, "ab"[n % 2], f"L{n}")] for n, m in enumerate(("1", "2", "3", "4"))}


def best_of(legs, penalties=None, target_odds=2.5):
    """Le meilleur combiné (un seul) des matchs donnés, ou None."""
    candidates = ComboGenerator._candidates(legs, target_odds, penalties)
    best = ComboGenerator._select(candidates, 1, config.GENRE_REPEAT_PENALTY)
    return best[0] if best else None


def chosen(best):
    return {leg["match_id"] for leg in best.legs}


def test_la_composition_laisse_de_cote_le_match_le_plus_penalise():
    legs = four_equal_matches()
    assert chosen(best_of(legs, penalties={"1": 2.25})) == {"2", "3", "4"}
    assert chosen(best_of(legs, penalties={"4": 0.75})) == {"1", "2", "3"}
    best = best_of(legs, penalties={"1": 0.75, "2": 0.75, "3": 0.75})
    assert "4" in chosen(best)                 # trois matchs pénalisés, un seul libre : le combiné évite d'en prendre trois


def test_un_match_deja_recu_par_un_autre_client_cede_la_place_a_un_match_un_peu_moins_probable():
    """Des clients différents reçoivent des matchs différents : environ trois points de chance de moins valent mieux qu'un match
    que d'autres clients ont déjà reçu (le défaut corrigé : la préférence était trop faible pour jamais l'emporter)."""
    def leg(match_id, probability, n):
        return {"match_id": match_id, "estimated_odds": 1.4, "category": "ab"[n % 2], "league": f"L{n}",
                "probability": probability, "type": f"T{match_id}"}

    legs = {}
    for n, match_id in enumerate(("1", "2", "3")):              # un peu plus probables, mais déjà reçus par un autre client
        legs[match_id] = [leg(match_id, 73.0, n)]
    for n, match_id in enumerate(("4", "5", "6")):
        legs[match_id] = [leg(match_id, 71.0, n)]
    penalties = ComboGenerator._variety_penalties(list(legs), Variety(served={"1": 1, "2": 1, "3": 1}))
    assert chosen(best_of(legs)) == {"1", "2", "3"}      # sans variété : les plus probables
    assert chosen(best_of(legs, penalties=penalties)) == {"4", "5", "6"}


def test_une_penalite_ne_supprime_jamais_un_combine():
    legs = four_equal_matches()
    everything = {m: config.VARIETY_SEEN_PENALTY * 4 for m in legs}                # tout a été reçu, et très largement
    assert best_of(legs, penalties=everything) is not None


def test_les_combines_d_une_nouvelle_generation_evitent_les_matchs_deja_recus(gen):
    for seed in range(8):
        preds = make_all_preds(gen, seed=seed, matches=15)
        first = gen.build_combos(preds, max_combos=1)
        given = {leg["match_id"] for combo in first for leg in combo["predictions"]}
        assert len(given) == 3
        again = gen.build_combos(preds, max_combos=1, variety=Variety(seen=frozenset(given)))
        assert again and not ({leg["match_id"] for combo in again for leg in combo["predictions"]} & given), seed


def test_trois_generations_de_suite_donnent_des_equipes_toutes_differentes(gen):
    preds = make_all_preds(gen, seed=3, matches=15)
    seen, rounds = set(), []
    for _ in range(3):
        combos = gen.build_combos(preds, max_combos=1, variety=Variety(seen=frozenset(seen)))
        legs = {leg["match_id"] for combo in combos for leg in combo["predictions"]}
        assert legs and not (legs & seen)
        rounds.append(legs)
        seen |= legs
    assert len(seen) == 9


def test_un_jour_de_peu_de_matchs_les_matchs_deja_recus_reviennent_quand_meme(gen):
    preds = make_all_preds(gen, seed=2, matches=3)
    everything = Variety(seen=frozenset(leg["match_id"] for leg in preds))
    combos = gen.build_combos(preds, target_odds=1.5, variety=everything)
    assert len(combos) == 1                                                         # la génération réussit, les pénalités s'y plient


def test_sans_variete_le_resultat_est_celui_d_avant(gen):
    preds = make_all_preds(gen, seed=5, matches=15)
    assert gen.build_combos(preds) == gen.build_combos(preds, variety=Variety()) == gen.build_combos(preds, variety=None)


def test_un_match_donne_a_d_autres_clients_est_legerement_moins_favorise(gen):
    legs = four_equal_matches()
    variety = Variety(served={"1": 1})
    penalties = ComboGenerator._variety_penalties(legs, variety)
    assert chosen(best_of(legs, penalties=penalties)) == {"2", "3", "4"}
