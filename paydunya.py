"""Client de l'API PayDunya (paiement par redirection : Orange Money, MTN, Moov, Wave, carte).

Deux appels seulement :
  - create_invoice() : crée la facture et renvoie l'adresse de la page de paiement ;
  - confirm()        : interroge PayDunya sur l'état d'une facture. C'est la SEULE source de
                       vérité pour savoir si un client a payé (jamais l'adresse de retour du
                       navigateur ni le contenu d'une notification, que n'importe qui peut forger).
"""
import hashlib
import hmac
import json
import logging
import os
import re
from urllib.parse import urlparse

import requests

import config

log = logging.getLogger(__name__)

API_BASE = "https://app.paydunya.com/api/v1"
# Les clés de TEST (« test_private_… ») ne fonctionnent que sur l'API « bac à sable », où les paiements sont fictifs.
SANDBOX_API_BASE = "https://app.paydunya.com/sandbox-api/v1"

# Forme d'un jeton de facture PayDunya (lettres, chiffres, tiret, soulignement).
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{6,80}$")

# États renvoyés par PayDunya pour une facture.
COMPLETED = "completed"
PENDING = "pending"


class PayDunyaError(Exception):
    """Appel PayDunya impossible ou refusé. `transient` : une nouvelle tentative a des chances de réussir."""

    def __init__(self, message, transient=False):
        super().__init__(message)
        self.transient = transient


def clean_token(value):
    """Le jeton s'il a la bonne forme, sinon None (évite d'interroger la base ou PayDunya avec n'importe quoi)."""
    value = (value or "").strip()
    return value if TOKEN_RE.match(value) else None


def is_test_token(token):
    """Une facture créée avec des clés de test porte un jeton qui commence par « test_ » : son paiement est fictif."""
    return str(token or "").startswith("test_")


def _is_paydunya_url(url):
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "paydunya.com" or host.endswith(".paydunya.com"))


class PayDunya:
    def __init__(self, master_key=None, private_key=None, token=None, http=None, timeout=None, mode=None):
        # Les clés vivent UNIQUEMENT dans les variables d'environnement de Render, jamais dans le code.
        # PAYDUNYA_MODE=test fait lire les clés de TEST (PAYDUNYA_TEST_PRIVATE_KEY, PAYDUNYA_TEST_TOKEN et, si elle
        # diffère, PAYDUNYA_TEST_MASTER_KEY) : les clés de production restent en place, intactes. Toute autre
        # valeur (ou aucune) : production. Seule la clé principale peut être commune aux deux modes.
        mode = os.environ.get("PAYDUNYA_MODE") if mode is None else mode
        self.mode = "test" if (mode or "").strip().lower() == "test" else "live"
        env = os.environ.get
        if self.mode == "test":
            self.master_key = master_key if master_key is not None else (
                env("PAYDUNYA_TEST_MASTER_KEY") or env("PAYDUNYA_MASTER_KEY"))
            self.private_key = env("PAYDUNYA_TEST_PRIVATE_KEY") if private_key is None else private_key
            self.token = env("PAYDUNYA_TEST_TOKEN") if token is None else token
        else:
            self.master_key = env("PAYDUNYA_MASTER_KEY") if master_key is None else master_key
            self.private_key = env("PAYDUNYA_PRIVATE_KEY") if private_key is None else private_key
            self.token = env("PAYDUNYA_TOKEN") if token is None else token
        self.http = http or requests
        self.timeout = timeout or config.PAYDUNYA_TIMEOUT

    @property
    def configured(self):
        return bool(self.master_key and self.private_key and self.token)

    @property
    def expected_variables(self):
        """Variables d'environnement à définir, pour les messages d'erreur."""
        if self.mode == "test":
            return "PAYDUNYA_TEST_PRIVATE_KEY, PAYDUNYA_TEST_TOKEN et PAYDUNYA_MASTER_KEY (ou PAYDUNYA_TEST_MASTER_KEY)"
        return "PAYDUNYA_MASTER_KEY, PAYDUNYA_PRIVATE_KEY, PAYDUNYA_TOKEN"

    @property
    def test_mode(self):
        """Vrai en mode test (PAYDUNYA_MODE=test) ou dès que la clé privée est une clé de test : les paiements
        sont alors fictifs."""
        return self.mode == "test" or (self.private_key or "").strip().startswith("test_")

    @property
    def api_base(self):
        return SANDBOX_API_BASE if self.test_mode else API_BASE

    def _headers(self):
        return {
            "PAYDUNYA-MASTER-KEY": self.master_key,
            "PAYDUNYA-PRIVATE-KEY": self.private_key,
            "PAYDUNYA-TOKEN": self.token,
            "Content-Type": "application/json",
        }

    def _call(self, method, url, **kwargs):
        if not self.configured:
            raise PayDunyaError(f"clés PayDunya absentes ({self.expected_variables})")
        try:
            response = self.http.request(method, url, headers=self._headers(), timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise PayDunyaError(f"PayDunya injoignable : {exc.__class__.__name__}", transient=True) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise PayDunyaError(f"réponse PayDunya illisible (HTTP {response.status_code})",
                                transient=response.status_code >= 500 or response.status_code == 429) from exc
        if not isinstance(data, dict):
            raise PayDunyaError(f"réponse PayDunya inattendue (HTTP {response.status_code})")
        return data

    # ------------------------------------------------------------------

    def create_invoice(self, *, name, amount, description, return_url, cancel_url, callback_url, custom_data=None):
        """Crée la facture. Renvoie {"token": ..., "url": ...} (url = page de paiement PayDunya)."""
        amount = int(amount)
        payload = {
            "invoice": {
                # PayDunya attend les articles sous la forme {"item_0": {...}}.
                "items": {"item_0": {"name": name, "quantity": 1, "unit_price": amount,
                                     "total_price": amount, "description": description}},
                "total_amount": amount,
                "description": description,
            },
            "store": {"name": config.PRODUCT_NAME, "website_url": config.SITE_URL},
            "actions": {"return_url": return_url, "cancel_url": cancel_url, "callback_url": callback_url},
        }
        if custom_data:
            payload["custom_data"] = custom_data
        data = self._call("POST", f"{self.api_base}/checkout-invoice/create", json=payload)
        if data.get("response_code") != "00" or not data.get("token"):
            raise PayDunyaError(f"facture refusée : code {data.get('response_code')!r}, {data.get('response_text')!r}")
        token = str(data["token"])
        if not clean_token(token):
            raise PayDunyaError("jeton de facture inattendu")
        return {"token": token, "url": self._payment_url(data, token)}

    @staticmethod
    def _payment_url(data, token):
        """Adresse de la page de paiement. PayDunya la fournit dans `response_text` (ou `invoice_url`) ;
        faute de mieux, on la reconstruit à partir du jeton. Toujours vérifiée : https et paydunya.com."""
        for candidate in (data.get("invoice_url"), data.get("response_text")):
            if isinstance(candidate, str) and _is_paydunya_url(candidate):
                return candidate
        log.warning("adresse de paiement absente de la réponse PayDunya : adresse reconstruite")
        area = "sandbox-checkout" if is_test_token(token) else "checkout"
        return f"https://paydunya.com/{area}/invoice/{token}"

    def confirm(self, token):
        """État de la facture : {"status": "completed" | "pending" | "cancelled" | "failed" | ..., "amount", "mode"}."""
        token = clean_token(token)
        if not token:
            raise PayDunyaError("jeton de facture invalide")
        data = self._call("GET", f"{self.api_base}/checkout-invoice/confirm/{token}")
        status = data.get("status")
        if data.get("response_code") != "00" or not isinstance(status, str):
            raise PayDunyaError(f"état de la facture indisponible : code {data.get('response_code')!r}, "
                                f"{data.get('response_text')!r}", transient=True)
        invoice = data.get("invoice") if isinstance(data.get("invoice"), dict) else {}
        try:
            amount = int(float(invoice.get("total_amount")))
        except (TypeError, ValueError):
            amount = None
        return {"status": status.lower(), "amount": amount, "mode": data.get("mode")}

    # ------------------------------------------------------------------

    def hash_is_valid(self, received):
        """Les notifications portent le SHA-512 de la clé maître. Contrôle indicatif seulement :
        la décision se prend toujours avec confirm()."""
        if not (received and self.master_key):
            return False
        expected = hashlib.sha512(self.master_key.encode()).hexdigest()
        return hmac.compare_digest(str(received).strip().lower(), expected)


def parse_notification(form=None, body=None):
    """Extrait (jeton de facture, hash annoncé) d'une notification IPN de PayDunya, quelle que soit sa forme :
    champs de formulaire `data[invoice][token]` (format documenté), champ `data` contenant du JSON,
    ou corps JSON {"data": {...}}. Le statut annoncé est volontairement ignoré."""
    form = form or {}
    data = None
    if isinstance(body, dict):
        data = body.get("data", body)
    if data is None and form.get("data"):
        try:
            data = json.loads(form.get("data"))
        except (TypeError, ValueError):
            data = None
    if isinstance(data, dict):
        invoice = data.get("invoice") if isinstance(data.get("invoice"), dict) else {}
        token = invoice.get("token") or data.get("token")
        digest = data.get("hash")
    else:
        token = form.get("data[invoice][token]") or form.get("invoice[token]") or form.get("token")
        digest = form.get("data[hash]") or form.get("hash")
    return clean_token(token if isinstance(token, str) else None), (digest if isinstance(digest, str) else None)
