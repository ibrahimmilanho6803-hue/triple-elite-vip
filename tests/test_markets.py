import pytest

import markets
from markets import (PREDICTION_CODES, category_of, combo_status, describe_prediction,
                     evaluate_prediction, parse_market)


def test_catalogue_sans_doublon_et_decodable():
    assert len(PREDICTION_CODES) == len(set(PREDICTION_CODES))
    assert len(PREDICTION_CODES) >= 45
    for code in PREDICTION_CODES:
        assert parse_market(code) is not None, code


def test_catalogue_sans_paris_identiques():
    """Aucun couple de codes ne désigne le même évènement (ex. « plus de 1 » et
    « plus de 1.5 » sont le même pari pour un total de buts entier)."""
    scores = [(h, a) for h in range(0, 8) for a in range(0, 8)]
    signatures = {}
    for code in PREDICTION_CODES:
        sig = tuple(evaluate_prediction(code, h, a) for h, a in scores)
        assert sig not in signatures, f"{code} == {signatures[sig]}"
        signatures[sig] = code


@pytest.mark.parametrize("code,home,away,expected", [
    ("V1", 2, 1, True), ("V1", 1, 1, False), ("V2", 0, 1, True),
    ("1X", 1, 1, True), ("1X", 0, 1, False), ("2X", 1, 1, True), ("2X", 1, 0, False),
    ("BTTS_OUI", 1, 1, True), ("BTTS_OUI", 2, 0, False), ("BTTS_NON", 2, 0, True),
    ("TOTAL_2.5+", 2, 1, True), ("TOTAL_2.5+", 1, 1, False),
    ("TOTAL_2.5-", 1, 1, True), ("TOTAL_2.5-", 2, 1, False),
    ("TOTAL_0.5-", 0, 0, True), ("TOTAL_0.5-", 1, 0, False),
    # Anciens codes à ligne entière : « plus de N » = strictement plus, « moins de N » = strictement moins.
    ("TOTAL_1+", 1, 0, False), ("TOTAL_1+", 1, 1, True),
    ("TOTAL_3-", 2, 1, False), ("TOTAL_3-", 1, 1, True),
    ("EQ1_1.5+", 2, 0, True), ("EQ1_1.5+", 1, 3, False), ("EQ2_0.5+", 0, 1, True),
    ("V1_ET_2.5+", 2, 1, True), ("V1_ET_2.5+", 1, 0, False), ("V1_ET_2.5+", 1, 2, False),
    ("1X_ET_1.5+", 1, 1, True), ("1X_ET_1.5+", 1, 0, False),
    ("V2_ET_1.5+", 0, 2, True), ("2X_ET_3.5+", 2, 2, True),
    ("AU_MOINS_1.5", 2, 0, True), ("AU_MOINS_1.5", 1, 1, False), ("AU_MOINS_2.5", 0, 3, True),
    ("V1_T1_2.5+", 3, 0, True), ("V1_T1_2.5+", 2, 0, False), ("1X_T1_0.5+", 0, 0, False),
    ("1X_T1_0.5+", 1, 1, True), ("V2_T2_2.5+", 0, 3, True), ("2X_T2_0.5+", 0, 0, False),
    # Anciens codes (plus proposés) toujours évalués correctement dans l'historique.
    ("V1_T1_1.5+", 2, 0, True), ("V1_T1_1.5+", 1, 0, False), ("V2_T2_1.5+", 0, 2, True),
])
def test_evaluate_prediction(code, home, away, expected):
    assert evaluate_prediction(code, home, away) is expected


def test_evaluate_codes_invalides():
    assert evaluate_prediction("INCONNU", 1, 0) is None
    assert evaluate_prediction(None, 1, 0) is None
    assert evaluate_prediction("V1", None, 0) is None
    assert evaluate_prediction("V1", "1", 0) is None
    assert evaluate_prediction("V1", True, 0) is None


def test_combo_status():
    assert combo_status([True, True, True]) == "won"
    assert combo_status([True, False, None]) == "lost"
    assert combo_status([True, None, True]) == "pending"
    assert combo_status([]) == "pending"


def test_categories():
    assert category_of("V1") == "RESULTAT"
    assert category_of("TOTAL_2.5+") == "TOTAL"
    assert category_of("BTTS_OUI") == "BTTS"
    assert category_of("EQ1_1.5+") == "EQUIPE"
    assert category_of("AU_MOINS_1.5") == "AU_MOINS"
    assert category_of("V1_ET_1.5+") == "COMBINE"
    assert category_of("2X_T2_0.5+") == "COMBINE"
    assert category_of("???") == "AUTRE"


def test_libelles_avec_noms_des_equipes():
    assert describe_prediction("V1", "Arsenal", "Chelsea") == "Victoire Arsenal"
    assert describe_prediction("V2", "Arsenal", "Chelsea") == "Victoire Chelsea"
    assert describe_prediction("1X", "Arsenal", "Chelsea") == "Arsenal gagne ou match nul"
    assert describe_prediction("BTTS_OUI", "A", "B") == "Les deux équipes marquent : OUI"
    assert describe_prediction("TOTAL_2.5+", "A", "B") == "Plus de 2,5 buts dans le match"
    assert describe_prediction("TOTAL_0.5+", "A", "B") == "Plus de 0,5 but dans le match"
    assert describe_prediction("TOTAL_1.5-", "A", "B") == "Moins de 1,5 but dans le match"
    assert describe_prediction("EQ2_1.5+", "Arsenal", "Chelsea") == "Chelsea : plus de 1,5 but"
    assert describe_prediction("V1_ET_2.5+", "Arsenal", "Chelsea") == "Victoire Arsenal et plus de 2,5 buts dans le match"
    assert describe_prediction("V2_T2_2.5+", "Arsenal", "Chelsea") == "Victoire Chelsea et Chelsea marque plus de 2,5 buts"
    assert describe_prediction("AU_MOINS_2.5", "A", "B") == "Au moins une équipe marque plus de 2,5 buts"
    assert describe_prediction("ZZZ", "A", "B") == "ZZZ"


def test_libelles_sans_nom_d_equipe():
    assert describe_prediction("V1", "", None) == "Victoire Équipe 1"
    assert describe_prediction("V2", None, "  ") == "Victoire Équipe 2"
