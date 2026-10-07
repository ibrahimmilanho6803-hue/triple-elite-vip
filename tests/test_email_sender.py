"""E-mail de licence : contenu, transports SMTP et Brevo, nouvelles tentatives (sans réseau)."""
import datetime
import smtplib

import pytest
import requests

import config
import email_sender
from email_sender import BrevoTransport, EmailError, SmtpTransport, build_message, envoyer_licence

EXPIRES = datetime.datetime(2026, 11, 5, 12, 0, 0)


def bodies(message):
    return (message.get_body(("plain",)).get_content(), message.get_body(("html",)).get_content())


# --------------------------------------------------------------------------
# Contenu
# --------------------------------------------------------------------------

def test_message_nouvelle_licence():
    message = build_message("client@exemple.com", "a1b2c3d4e5f60718", "Mensuel", EXPIRES)
    text, html = bodies(message)
    assert message["Subject"] == "Ta clé d’accès Triple Elite VIP"
    assert message["To"] == "client@exemple.com" and message["Reply-To"] == config.SELLER_EMAIL
    assert "Triple Elite VIP" in message["From"]
    for content in (text, html):
        assert "a1b2c3d4e5f60718" in content and "client@exemple.com" in content
        assert "05/11/2026" in content and f"{config.SITE_URL}/login" in content
        assert "abonnement mensuel" in content
        assert "Votre" not in content and "vous" not in content.lower().replace("vouloir", "")     # tutoiement
    assert "Valable jusqu’au" in text and "garantie de gain" in text
    assert message.get_content_charset() in (None, "utf-8") and message.is_multipart()


@pytest.mark.parametrize("renewed", [False, True])
def test_message_rappelle_l_acces_immediat_et_la_renonciation(renewed):
    """L'accord donné à la case des conditions doit être confirmé sur un support durable : cet e-mail."""
    text, html = bodies(build_message("client@exemple.com", "a1b2c3d4e5f60718", "Mensuel", EXPIRES, renewed=renewed))
    for content in (text, html):
        assert "ton accès a été ouvert immédiatement" in content
        assert "renonces à ton droit de rétractation" in content
        assert f"{config.SITE_URL}/conditions" in content


def test_message_renouvellement_garde_la_cle():
    message = build_message("client@exemple.com", "a1b2c3d4e5f60718", "Annuel", EXPIRES, renewed=True)
    text, html = bodies(message)
    assert message["Subject"] == "Ton abonnement Triple Elite VIP est prolongé"
    assert "prolongé" in text and "ne change pas" in text and "Abonnement actif jusqu’au : 05/11/2026" in text
    assert "ne change pas" in html and "a1b2c3d4e5f60718" in html


def test_message_sans_date_et_adresse_hostile():
    message = build_message("<script>alert(1)</script>@x.com", "cle", "Mensuel", None)
    text, html = bodies(message)
    assert "Valable" not in text and "<script>" not in html and "&lt;script&gt;" in html
    with pytest.raises(ValueError):
        build_message("a@b.com\nBcc: pirate@x.com", "cle", "Mensuel")        # pas d'injection d'en-têtes


# --------------------------------------------------------------------------
# Envoi et nouvelles tentatives
# --------------------------------------------------------------------------

class ScriptedTransport:
    name = "test"

    def __init__(self, *outcomes):
        self.outcomes, self.sent = list(outcomes), []

    def send(self, message):
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome:
            raise outcome
        self.sent.append(message)


def send(transport, **options):
    sleeps = []
    ok = envoyer_licence("client@exemple.com", "cle", "Mensuel", EXPIRES, transport=transport,
                         sleep=sleeps.append, **options)
    return ok, sleeps


def test_envoi_reussi_du_premier_coup():
    transport = ScriptedTransport()
    assert send(transport) == (True, []) and len(transport.sent) == 1


def test_erreur_passagere_reessayee_avec_attente_croissante():
    transport = ScriptedTransport(EmailError("occupé", transient=True), EmailError("occupé", transient=True))
    ok, sleeps = send(transport)
    assert ok is True and sleeps == [2, 6] and len(transport.sent) == 1


def test_abandon_apres_le_nombre_maximal_de_tentatives():
    transport = ScriptedTransport(*[EmailError("coupé", transient=True)] * 5)
    ok, sleeps = send(transport)
    assert ok is False and len(sleeps) == config.EMAIL_ATTEMPTS - 1 and len(transport.outcomes) == 5 - config.EMAIL_ATTEMPTS


def test_erreur_definitive_jamais_reessayee():
    transport = ScriptedTransport(EmailError("mot de passe refusé", transient=False))
    ok, sleeps = send(transport)
    assert ok is False and sleeps == [] and transport.sent == []


def test_erreur_inattendue_ne_fait_jamais_planter_l_appelant():
    ok, sleeps = send(ScriptedTransport(RuntimeError("bug")))
    assert ok is False and sleeps == []


def test_sans_transport_configure_rien_n_est_envoye(monkeypatch, caplog):
    monkeypatch.delenv("GMAIL_MDP", raising=False)
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    assert envoyer_licence("client@exemple.com", "cle", "Mensuel") is False
    assert "NON envoyé" in caplog.text and "client@exemple.com" not in caplog.text      # adresse masquée


def test_envoi_en_arriere_plan():
    transport = ScriptedTransport()
    thread = email_sender.envoyer_licence_async("client@exemple.com", "cle", "Mensuel", EXPIRES, False,
                                                transport=transport)
    thread.join(5)
    assert not thread.is_alive() and len(transport.sent) == 1


def test_choix_du_transport_selon_l_environnement(monkeypatch):
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    monkeypatch.delenv("GMAIL_MDP", raising=False)
    assert email_sender.default_transport() is None
    monkeypatch.setenv("GMAIL_MDP", "motdepasse")
    assert isinstance(email_sender.default_transport(), SmtpTransport)
    monkeypatch.setenv("BREVO_API_KEY", "cle-brevo")
    assert isinstance(email_sender.default_transport(), BrevoTransport)           # prioritaire : fonctionne sur Render gratuit
    monkeypatch.setenv("GMAIL_EMAIL", "autre@gmail.com")
    assert email_sender.sender_address() == "autre@gmail.com"


@pytest.mark.parametrize("saisi", [
    "abcd efgh ijkl mnop",              # tel que Google l'affiche
    "abcd efgh ijkl mnop",   # espaces insécables, fréquentes après un copier-coller
    "  abcdefghijklmnop \n",            # espaces et retour à la ligne autour
    "abcdefghijklmnop",                 # déjà propre
])
def test_mot_de_passe_d_application_colle_avec_des_espaces(monkeypatch, saisi):
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    monkeypatch.setenv("GMAIL_MDP", saisi)
    transport = email_sender.default_transport()
    assert isinstance(transport, SmtpTransport) and transport.password == "abcdefghijklmnop"
    assert transport.password.isascii()          # sinon smtplib plante au moment de s'identifier


def test_mot_de_passe_vide_ou_blanc_equivaut_a_non_configure(monkeypatch):
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    for vide in ("", "   ", " \n"):
        monkeypatch.setenv("GMAIL_MDP", vide)
        assert email_sender.default_transport() is None


def test_cle_brevo_avec_espaces_autour(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "  cle-brevo\n")
    transport = email_sender.default_transport()
    assert isinstance(transport, BrevoTransport) and transport.api_key == "cle-brevo"
    monkeypatch.setenv("BREVO_API_KEY", "   ")
    monkeypatch.delenv("GMAIL_MDP", raising=False)
    assert email_sender.default_transport() is None


# --------------------------------------------------------------------------
# SMTP
# --------------------------------------------------------------------------

class FakeSmtp:
    log = []
    login_error = None
    connect_error = None
    send_error = None

    def __init__(self, host, port, timeout=None):
        if FakeSmtp.connect_error:
            raise FakeSmtp.connect_error
        FakeSmtp.log.append(("connect", host, port, timeout))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        FakeSmtp.log.append(("quit",))

    def starttls(self, context=None):
        FakeSmtp.log.append(("starttls", context is not None))

    def login(self, user, password):
        if FakeSmtp.login_error:
            raise FakeSmtp.login_error
        FakeSmtp.log.append(("login", user, password))

    def send_message(self, message):
        if FakeSmtp.send_error:
            raise FakeSmtp.send_error
        FakeSmtp.log.append(("send", message["To"]))


@pytest.fixture()
def smtp():
    FakeSmtp.log, FakeSmtp.login_error, FakeSmtp.connect_error, FakeSmtp.send_error = [], None, None, None
    return SmtpTransport("vendeur@gmail.com", "mdp", smtp_factory=FakeSmtp)


def test_smtp_chiffre_la_connexion_avant_de_s_identifier(smtp):
    smtp.send(build_message("client@exemple.com", "cle", "Mensuel"))
    assert FakeSmtp.log == [("connect", "smtp.gmail.com", 587, config.EMAIL_TIMEOUT), ("starttls", True),
                            ("login", "vendeur@gmail.com", "mdp"), ("send", "client@exemple.com"), ("quit",)]


def test_smtp_erreurs_definitives_et_passageres(smtp):
    message = build_message("client@exemple.com", "cle", "Mensuel")
    FakeSmtp.login_error = smtplib.SMTPAuthenticationError(535, b"refus")
    with pytest.raises(EmailError) as raised:
        smtp.send(message)
    assert raised.value.transient is False and "GMAIL_MDP" in str(raised.value)
    FakeSmtp.login_error = None
    FakeSmtp.send_error = smtplib.SMTPRecipientsRefused({"client@exemple.com": (550, b"inconnu")})
    with pytest.raises(EmailError) as raised:
        smtp.send(message)
    assert raised.value.transient is False
    FakeSmtp.send_error = smtplib.SMTPResponseException(451, b"try again later")
    with pytest.raises(EmailError) as raised:
        smtp.send(message)
    assert raised.value.transient is True
    FakeSmtp.send_error = smtplib.SMTPResponseException(554, b"rejected")
    with pytest.raises(EmailError) as raised:
        smtp.send(message)
    assert raised.value.transient is False


def test_smtp_port_bloque_par_render_est_explique(smtp):
    FakeSmtp.connect_error = OSError(101, "Network is unreachable")
    with pytest.raises(EmailError) as raised:
        smtp.send(build_message("client@exemple.com", "cle", "Mensuel"))
    assert raised.value.transient is True and "BREVO_API_KEY" in str(raised.value)
    FakeSmtp.connect_error = smtplib.SMTPServerDisconnected("coupé")
    with pytest.raises(EmailError) as raised:
        smtp.send(build_message("client@exemple.com", "cle", "Mensuel"))
    assert raised.value.transient is True and "BREVO_API_KEY" not in str(raised.value)


# --------------------------------------------------------------------------
# Brevo (API HTTPS)
# --------------------------------------------------------------------------

class FakeBrevoHttp:
    def __init__(self, status=201, error=None):
        self.status, self.error, self.calls = status, error, []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.error:
            raise self.error
        return type("Reply", (), {"status_code": self.status})()


def test_brevo_envoie_texte_et_html():
    http = FakeBrevoHttp()
    BrevoTransport("cle-brevo", "vendeur@gmail.com", http=http).send(
        build_message("client@exemple.com", "a1b2c3d4e5f60718", "Mensuel", EXPIRES))
    call = http.calls[0]
    assert call["url"] == "https://api.brevo.com/v3/smtp/email" and call["headers"]["api-key"] == "cle-brevo"
    payload = call["json"]
    assert payload["sender"] == {"name": "Triple Elite VIP", "email": "vendeur@gmail.com"}
    assert payload["to"] == [{"email": "client@exemple.com"}] and payload["subject"] == "Ta clé d’accès Triple Elite VIP"
    assert "a1b2c3d4e5f60718" in payload["textContent"] and "<html" in payload["htmlContent"]


@pytest.mark.parametrize("status, transient", [(401, False), (400, False), (429, True), (500, True), (503, True)])
def test_brevo_erreurs_http(status, transient):
    transport = BrevoTransport("cle", "vendeur@gmail.com", http=FakeBrevoHttp(status))
    with pytest.raises(EmailError) as raised:
        transport.send(build_message("client@exemple.com", "cle", "Mensuel"))
    assert raised.value.transient is transient


def test_brevo_injoignable_est_passager():
    transport = BrevoTransport("cle", "vendeur@gmail.com", http=FakeBrevoHttp(error=requests.ConnectionError("hors ligne")))
    with pytest.raises(EmailError) as raised:
        transport.send(build_message("client@exemple.com", "cle", "Mensuel"))
    assert raised.value.transient is True
