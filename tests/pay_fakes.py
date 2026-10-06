"""Faux PayDunya et faux envoi d'e-mails pour les tests du site de paiement."""
from paydunya import PayDunyaError


class FakePayDunya:
    """Même interface que paydunya.PayDunya, sans réseau. Les paiements se simulent avec pay() / cancel()."""

    configured = True

    def __init__(self):
        self.created = []            # arguments de chaque create_invoice()
        self.invoices = {}           # jeton -> {"status", "amount"}
        self.confirm_calls = []
        self.fail_create = None      # exception à lever à la création
        self.fail_confirm = None     # exception à lever à la vérification
        self.good_hash = "bon-hash"
        self.url_base = "https://paydunya.com/sandbox-checkout/invoice"

    def create_invoice(self, **kwargs):
        if self.fail_create:
            raise self.fail_create
        token = f"test_tok{len(self.created) + 1:07d}"
        self.created.append({"token": token, **kwargs})
        self.invoices[token] = {"status": "pending", "amount": kwargs["amount"]}
        return {"token": token, "url": f"{self.url_base}/{token}"}

    def confirm(self, token):
        self.confirm_calls.append(token)
        if self.fail_confirm:
            raise self.fail_confirm
        invoice = self.invoices.get(token)
        if invoice is None:
            raise PayDunyaError("facture inconnue", transient=True)
        return {"status": invoice["status"], "amount": invoice["amount"], "mode": "test"}

    def hash_is_valid(self, digest):
        return digest == self.good_hash

    # --- simulation du client ---
    def pay(self, token):
        self.invoices[token]["status"] = "completed"

    def cancel(self, token, status="cancelled"):
        self.invoices[token]["status"] = status


class FakeMailer:
    """Remplace envoyer_licence_async : enregistre les e-mails au lieu de les envoyer."""

    def __init__(self):
        self.sent = []
        self.error = None

    def __call__(self, destinataire, cle, plan, expires=None, renewed=False):
        if self.error:
            raise self.error
        self.sent.append({"to": destinataire, "key": cle, "plan": plan, "expires": expires, "renewed": renewed})
