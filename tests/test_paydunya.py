"""Client PayDunya : requêtes envoyées, lecture des réponses, notifications (sans réseau)."""
import hashlib

import pytest
import requests

import config
from paydunya import PayDunya, PayDunyaError, clean_token, is_test_token, parse_notification

KEYS = {"master_key": "MASTER", "private_key": "PRIVATE", "token": "TOKEN"}


class Reply:
    def __init__(self, data=None, status=200, bad_json=False):
        self._data, self.status_code, self._bad = data, status, bad_json

    def json(self):
        if self._bad:
            raise ValueError("pas du JSON")
        return self._data


class FakeHttp:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.error:
            raise self.error
        return self.reply


def client(reply=None, error=None, **keys):
    http = FakeHttp(reply, error)
    return PayDunya(**{**KEYS, **keys}, http=http), http


INVOICE = dict(name="Triple Elite VIP - Mensuel", amount=19700, description="Pronostics",
               return_url="https://p.example/succes", cancel_url="https://p.example/paiement",
               callback_url="https://p.example/ipn", custom_data={"plan": "monthly"})


# --------------------------------------------------------------------------
# Création de facture
# --------------------------------------------------------------------------

def test_creation_de_facture_envoie_ce_que_paydunya_attend():
    pd, http = client(Reply({"response_code": "00", "response_text": "https://paydunya.com/checkout/invoice/abc123XYZ",
                             "token": "abc123XYZ"}))
    invoice = pd.create_invoice(**INVOICE)
    assert invoice == {"token": "abc123XYZ", "url": "https://paydunya.com/checkout/invoice/abc123XYZ"}
    call = http.calls[0]
    assert call["method"] == "POST" and call["url"].endswith("/checkout-invoice/create")
    assert call["headers"]["PAYDUNYA-MASTER-KEY"] == "MASTER"
    assert call["headers"]["PAYDUNYA-PRIVATE-KEY"] == "PRIVATE"
    assert call["headers"]["PAYDUNYA-TOKEN"] == "TOKEN"
    assert call["timeout"] == config.PAYDUNYA_TIMEOUT
    body = call["json"]
    assert body["invoice"]["total_amount"] == 19700
    item = body["invoice"]["items"]["item_0"]                  # format documenté : item_0, item_1...
    assert item["quantity"] == 1 and item["unit_price"] == 19700 and item["total_price"] == 19700
    assert body["actions"] == {"return_url": "https://p.example/succes", "cancel_url": "https://p.example/paiement",
                               "callback_url": "https://p.example/ipn"}
    assert body["custom_data"] == {"plan": "monthly"}
    assert body["store"]["name"] == "Triple Elite VIP"


def test_adresse_de_paiement_dans_invoice_url_ou_reconstruite():
    pd, _ = client(Reply({"response_code": "00", "invoice_url": "https://app.paydunya.com/checkout/invoice/abc123XYZ",
                          "response_text": "Checkout Invoice Created", "token": "abc123XYZ"}))
    assert pd.create_invoice(**INVOICE)["url"] == "https://app.paydunya.com/checkout/invoice/abc123XYZ"
    pd, _ = client(Reply({"response_code": "00", "response_text": "Checkout Invoice Created", "token": "abc123XYZ"}))
    assert pd.create_invoice(**INVOICE)["url"] == "https://paydunya.com/checkout/invoice/abc123XYZ"
    pd, _ = client(Reply({"response_code": "00", "response_text": "ok", "token": "test_abc123XYZ"}))
    assert pd.create_invoice(**INVOICE)["url"] == "https://paydunya.com/sandbox-checkout/invoice/test_abc123XYZ"


@pytest.mark.parametrize("url", ["https://evil.example/pay", "http://paydunya.com/checkout/invoice/x",
                                 "https://paydunya.com.evil.example/x", "javascript:alert(1)"])
def test_adresse_etrangere_jamais_utilisee_pour_la_redirection(url):
    pd, _ = client(Reply({"response_code": "00", "response_text": url, "token": "abc123XYZ"}))
    assert pd.create_invoice(**INVOICE)["url"] == "https://paydunya.com/checkout/invoice/abc123XYZ"


def test_creation_refusee_ou_illisible():
    for reply in (Reply({"response_code": "1001", "response_text": "clés invalides"}),
                  Reply({"response_code": "00"}),                             # pas de jeton
                  Reply({"response_code": "00", "token": "x y"}),             # jeton de forme invalide
                  Reply(None, 502, bad_json=True),
                  Reply(["pas", "un", "objet"])):
        pd, _ = client(reply)
        with pytest.raises(PayDunyaError):
            pd.create_invoice(**INVOICE)


def test_reseau_en_panne_est_transitoire_et_cles_absentes_ne_font_aucun_appel():
    pd, _ = client(error=requests.ConnectTimeout("lent"))
    with pytest.raises(PayDunyaError) as raised:
        pd.create_invoice(**INVOICE)
    assert raised.value.transient is True
    pd, http = client(Reply({}), master_key="")
    assert pd.configured is False
    with pytest.raises(PayDunyaError):
        pd.create_invoice(**INVOICE)
    assert http.calls == []


# --------------------------------------------------------------------------
# Vérification d'une facture
# --------------------------------------------------------------------------

def test_confirmation_lit_le_statut_et_le_montant():
    pd, http = client(Reply({"response_code": "00", "status": "completed", "mode": "live",
                             "invoice": {"total_amount": "19700"}}))
    assert pd.confirm("abc123XYZ") == {"status": "completed", "amount": 19700, "mode": "live"}
    assert http.calls[0]["method"] == "GET" and http.calls[0]["url"].endswith("/checkout-invoice/confirm/abc123XYZ")
    for status in ("pending", "cancelled", "failed"):
        pd, _ = client(Reply({"response_code": "00", "status": status}))
        assert pd.confirm("abc123XYZ")["status"] == status
    pd, _ = client(Reply({"response_code": "00", "status": "Completed"}))
    assert pd.confirm("abc123XYZ")["status"] == "completed" and pd.confirm("abc123XYZ")["amount"] is None


def test_confirmation_impossible_est_une_erreur_transitoire():
    for reply in (Reply({"response_code": "1002", "response_text": "introuvable"}),
                  Reply({"response_code": "00"}),                              # statut absent
                  Reply(None, 503, bad_json=True)):
        pd, _ = client(reply)
        with pytest.raises(PayDunyaError):
            pd.confirm("abc123XYZ")


def test_confirmation_refuse_un_jeton_invalide_sans_appel_reseau():
    pd, http = client(Reply({}))
    for bad in ("", "../../x", "a b c d e f", "x" * 200, None):
        with pytest.raises(PayDunyaError):
            pd.confirm(bad)
    assert http.calls == []


# --------------------------------------------------------------------------
# Notifications (IPN)
# --------------------------------------------------------------------------

def test_empreinte_de_notification():
    pd, _ = client(Reply({}))
    good = hashlib.sha512(b"MASTER").hexdigest()
    assert pd.hash_is_valid(good) and pd.hash_is_valid(good.upper())
    assert not pd.hash_is_valid("autre") and not pd.hash_is_valid(None) and not pd.hash_is_valid("")


def test_notification_au_format_formulaire_documente():
    form = {"data[invoice][token]": "abc123XYZ", "data[status]": "completed", "data[hash]": "h"}
    assert parse_notification(form=form) == ("abc123XYZ", "h")


def test_notification_json_ou_champ_data_json():
    body = {"data": {"invoice": {"token": "abc123XYZ"}, "status": "completed", "hash": "h"}}
    assert parse_notification(body=body) == ("abc123XYZ", "h")
    assert parse_notification(form={"data": '{"invoice": {"token": "abc123XYZ"}, "hash": "h"}'}) == ("abc123XYZ", "h")
    assert parse_notification(body={"invoice": {"token": "abc123XYZ"}}) == ("abc123XYZ", None)


def test_notification_illisible_ne_donne_aucun_jeton():
    assert parse_notification() == (None, None)
    assert parse_notification(form={"data": "pas du json"}) == (None, None)
    assert parse_notification(form={"data[invoice][token]": "x y; DROP TABLE"}) == (None, None)
    assert parse_notification(body={"data": {"invoice": {"token": 12345678}}}) == (None, None)
    assert parse_notification(body=["liste"]) == (None, None)


def test_forme_du_jeton():
    assert clean_token(" abc123XYZ ") == "abc123XYZ" and clean_token("test_Ab-12_34") == "test_Ab-12_34"
    assert clean_token("court") is None and clean_token("a/b/c/d/e") is None and clean_token("é" * 10) is None


# --------------------------------------------------------------------------
# Mode test : les clés de test ne marchent que sur l'API « bac à sable » de PayDunya
# --------------------------------------------------------------------------

def test_les_cles_de_production_utilisent_l_api_normale():
    pd, http = client(Reply({"response_code": "00", "response_text": "ok", "token": "abc123XYZ"}))
    assert pd.test_mode is False and pd.api_base == "https://app.paydunya.com/api/v1"
    pd.create_invoice(**INVOICE)
    assert http.calls[0]["url"] == "https://app.paydunya.com/api/v1/checkout-invoice/create"


def test_les_cles_de_test_utilisent_l_api_bac_a_sable():
    pd, http = client(Reply({"response_code": "00", "response_text": "ok", "token": "test_abc123XYZ"}),
                      private_key=" test_private_XYZ ")
    assert pd.test_mode is True and pd.api_base == "https://app.paydunya.com/sandbox-api/v1"
    invoice = pd.create_invoice(**INVOICE)
    assert http.calls[0]["url"] == "https://app.paydunya.com/sandbox-api/v1/checkout-invoice/create"
    assert http.calls[0]["headers"]["PAYDUNYA-PRIVATE-KEY"] == " test_private_XYZ "      # les clés partent telles quelles
    assert invoice == {"token": "test_abc123XYZ", "url": "https://paydunya.com/sandbox-checkout/invoice/test_abc123XYZ"}


def test_la_verification_d_une_facture_de_test_interroge_aussi_le_bac_a_sable():
    pd, http = client(Reply({"response_code": "00", "status": "completed", "mode": "test",
                             "invoice": {"total_amount": "19700"}}), private_key="test_private_XYZ")
    assert pd.confirm("test_abc123XYZ") == {"status": "completed", "amount": 19700, "mode": "test"}
    assert http.calls[0]["url"] == "https://app.paydunya.com/sandbox-api/v1/checkout-invoice/confirm/test_abc123XYZ"


@pytest.mark.parametrize("key", ["", None, "live_private_x", "private_test_x", "PRIVATE"])
def test_sans_le_prefixe_test_c_est_la_production(key):
    pd, _ = client(Reply({}), private_key=key)
    assert pd.test_mode is False and pd.api_base.endswith("/api/v1")


def test_jeton_de_test():
    assert is_test_token("test_abc123XYZ") and not is_test_token("abc123XYZ") and not is_test_token(None)
    assert not is_test_token("TEST_abc123") and not is_test_token("")
