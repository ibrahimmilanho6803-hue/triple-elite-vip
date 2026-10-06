"""Envoi de la clé de licence par e-mail, en arrière-plan, avec nouvelles tentatives.

Deux façons d'envoyer, choisies selon les variables d'environnement :
  - SMTP Gmail (GMAIL_EMAIL + GMAIL_MDP, un « mot de passe d'application ») : par défaut ;
  - API HTTPS de Brevo (BREVO_API_KEY) : à préférer sur Render, dont l'offre gratuite
    BLOQUE les ports SMTP (25, 465, 587), donc Gmail n'y fonctionne pas.
Sans aucune des deux, rien n'est envoyé (c'est journalisé) : la clé reste affichée à l'écran
sur la page de confirmation du paiement.
"""
import logging
import os
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from html import escape

import requests

import config
from privacy import mask_email
from web_common import typo

log = logging.getLogger(__name__)

BREVO_URL = "https://api.brevo.com/v3/smtp/email"


class EmailError(Exception):
    """Envoi impossible. `transient` : une nouvelle tentative peut réussir (réseau, serveur occupé)."""

    def __init__(self, message, transient=False):
        super().__init__(message)
        self.transient = transient


def sender_address():
    return os.environ.get("EMAIL_SENDER") or os.environ.get("GMAIL_EMAIL") or config.SELLER_EMAIL


# --------------------------------------------------------------------------
# Contenu du message
# --------------------------------------------------------------------------

def _date_label(expires):
    return expires.strftime("%d/%m/%Y") if expires else None


def build_message(destinataire, cle, plan, expires=None, renewed=False, expediteur=None):
    """Message texte + HTML (UTF-8, accents compris). `renewed` : renouvellement d'une licence encore valide."""
    expediteur = expediteur or sender_address()
    until = _date_label(expires)
    login_url = f"{config.SITE_URL}/login"
    plan_label = f"abonnement {plan.lower()}" if plan else "abonnement"

    until_label = typo("Abonnement actif jusqu'au" if renewed else "Valable jusqu'au")
    if renewed:
        subject = "Ton abonnement Triple Elite VIP est prolongé"
        intro = "Merci ! Ton abonnement est prolongé."
        key_note = typo("Ta clé de licence ne change pas : c'est celle que tu utilises déjà.")
    else:
        subject = typo("Ta clé d'accès Triple Elite VIP")
        intro = f"Merci pour ton {plan_label} Triple Elite VIP !"
        key_note = typo("Garde cet e-mail : tu en auras besoin pour te reconnecter.")

    lines = ["Bonjour,", "", intro, "", f"E-mail : {destinataire}", f"Clé de licence : {cle}"]
    if until:
        lines.append(f"{until_label} : {until}")
    lines += ["", key_note, "", f"Pour te connecter : {login_url}", "",
              "Rappel : nos pronostics sont des estimations statistiques, jamais une garantie de gain. "
              "Les paris sont réservés aux personnes majeures : ne mise que ce que tu peux te permettre de perdre.",
              "", f"Une question ? Réponds à cet e-mail ou écris à {config.SELLER_EMAIL}.", "", "Triple Elite VIP"]
    text = typo("\n".join(lines))

    e = escape
    until_row = ""
    if until:
        until_row = (f'<p style="margin:14px 0 0;font-size:14px;color:#4a5278;">{e(until_label)} '
                     f'<strong style="color:#0a0f2c;">{e(until)}</strong></p>')
    html = f"""<!doctype html>
<html lang="fr">
<body style="margin:0;padding:0;background:#eef0f8;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef0f8;padding:24px 12px;">
<tr><td align="center">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border-radius:8px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#0a0f2c;">
    <tr><td style="background:#0a0f2c;padding:20px 28px;">
      <span style="font-family:Arial Narrow,Arial,Helvetica,sans-serif;font-weight:bold;font-size:22px;letter-spacing:2px;color:#e3b341;text-transform:uppercase;">Triple Elite VIP</span>
    </td></tr>
    <tr><td style="padding:28px;">
      <p style="margin:0 0 14px;font-size:16px;line-height:1.5;">Bonjour,</p>
      <p style="margin:0 0 20px;font-size:16px;line-height:1.5;">{e(intro)}</p>
      <div style="background:#f4f5fa;border:1px dashed #c99a2c;border-radius:6px;padding:18px 20px;">
        <p style="margin:0;font-size:13px;color:#4a5278;">E-mail</p>
        <p style="margin:2px 0 14px;font-size:16px;font-weight:bold;">{e(destinataire)}</p>
        <p style="margin:0;font-size:13px;color:#4a5278;">Clé de licence</p>
        <p style="margin:2px 0 0;font-family:'Courier New',Courier,monospace;font-size:24px;font-weight:bold;letter-spacing:3px;color:#0a0f2c;">{e(cle)}</p>
      </div>
      {until_row}
      <p style="margin:14px 0 24px;font-size:14px;line-height:1.5;color:#4a5278;">{e(key_note)}</p>
      <p style="margin:0 0 28px;"><a href="{e(login_url)}" style="display:inline-block;background:#e3b341;color:#0a0f2c;font-weight:bold;font-size:16px;text-decoration:none;padding:13px 26px;border-radius:4px;">Me connecter</a></p>
      <p style="margin:0;font-size:12px;line-height:1.5;color:#6a7298;">Nos pronostics sont des estimations statistiques, jamais une garantie de gain. Les paris sont réservés aux personnes majeures : ne mise que ce que tu peux te permettre de perdre.</p>
      <p style="margin:12px 0 0;font-size:12px;line-height:1.5;color:#6a7298;">Une question ? Réponds à cet e-mail ou écris à <a href="mailto:{e(config.SELLER_EMAIL)}" style="color:#6a7298;">{e(config.SELLER_EMAIL)}</a>.</p>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((config.EMAIL_FROM_NAME, expediteur))
    message["To"] = destinataire
    message["Reply-To"] = config.SELLER_EMAIL
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=expediteur.split("@")[-1])
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message


# --------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------

class SmtpTransport:
    name = "SMTP"

    def __init__(self, user, password, host=None, port=None, timeout=None, smtp_factory=smtplib.SMTP):
        self.user, self.password = user, password
        self.host = host or config.SMTP_HOST
        self.port = port or config.SMTP_PORT
        self.timeout = timeout or config.EMAIL_TIMEOUT
        self.smtp_factory = smtp_factory

    def send(self, message):
        try:
            with self.smtp_factory(self.host, self.port, timeout=self.timeout) as server:
                server.starttls(context=ssl.create_default_context())
                server.login(self.user, self.password)
                server.send_message(message)
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailError("Gmail a refusé l'identifiant ou le mot de passe d'application (GMAIL_EMAIL / GMAIL_MDP)") from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise EmailError("adresse du destinataire refusée") from exc
        except smtplib.SMTPResponseException as exc:
            raise EmailError(f"serveur SMTP : {exc.smtp_code}", transient=400 <= exc.smtp_code < 500) from exc
        except (OSError, smtplib.SMTPException) as exc:
            unreachable = isinstance(exc, (TimeoutError, ConnectionError)) or getattr(exc, "errno", None) in (101, 110, 111)
            hint = (" - sur Render, l'offre gratuite bloque les ports SMTP : passe à une offre payante "
                    "ou définis BREVO_API_KEY") if unreachable else ""
            raise EmailError(f"connexion SMTP impossible ({exc.__class__.__name__}){hint}", transient=True) from exc


class BrevoTransport:
    name = "Brevo"

    def __init__(self, api_key, sender, timeout=None, http=requests):
        self.api_key, self.sender = api_key, sender
        self.timeout = timeout or config.EMAIL_TIMEOUT
        self.http = http

    def send(self, message):
        to = message["To"]
        payload = {
            "sender": {"name": config.EMAIL_FROM_NAME, "email": self.sender},
            "to": [{"email": str(to)}],
            "replyTo": {"email": config.SELLER_EMAIL},
            "subject": str(message["Subject"]),
            "textContent": message.get_body(("plain",)).get_content(),
            "htmlContent": message.get_body(("html",)).get_content(),
        }
        try:
            response = self.http.post(BREVO_URL, json=payload, timeout=self.timeout,
                                      headers={"api-key": self.api_key, "accept": "application/json"})
        except requests.RequestException as exc:
            raise EmailError(f"Brevo injoignable ({exc.__class__.__name__})", transient=True) from exc
        if response.status_code >= 300:
            raise EmailError(f"Brevo a répondu HTTP {response.status_code}",
                             transient=response.status_code >= 500 or response.status_code == 429)


def default_transport():
    """Transport selon l'environnement, ou None si l'envoi n'est pas configuré."""
    brevo = os.environ.get("BREVO_API_KEY")
    if brevo:
        return BrevoTransport(brevo, sender_address())
    password = os.environ.get("GMAIL_MDP")
    if password:
        return SmtpTransport(sender_address(), password)
    return None


# --------------------------------------------------------------------------
# Envoi
# --------------------------------------------------------------------------

def envoyer_licence(destinataire, cle, plan, expires=None, renewed=False, *, transport=None, attempts=None,
                    sleep=time.sleep):
    """Envoie la clé. Renvoie True si l'e-mail est parti, False sinon (toujours journalisé, jamais d'exception)."""
    transport = transport or default_transport()
    if transport is None:
        log.error("e-mail de licence NON envoyé à %s : ni GMAIL_MDP ni BREVO_API_KEY n'est défini",
                  mask_email(destinataire))
        return False
    attempts = attempts or config.EMAIL_ATTEMPTS
    try:
        message = build_message(destinataire, cle, plan, expires=expires, renewed=renewed)
    except Exception:
        log.exception("e-mail de licence : message invalide pour %s", mask_email(destinataire))
        return False
    for attempt in range(1, attempts + 1):
        try:
            transport.send(message)
        except EmailError as exc:
            retry = exc.transient and attempt < attempts
            log.warning("e-mail de licence à %s : échec (%s) %s, tentative %d/%d", mask_email(destinataire),
                        transport.name, exc, attempt, attempts)
            if not retry:
                log.error("e-mail de licence NON envoyé à %s : %s", mask_email(destinataire), exc)
                return False
            sleep(2 * 3 ** (attempt - 1))
        except Exception:
            log.exception("e-mail de licence NON envoyé à %s : erreur inattendue", mask_email(destinataire))
            return False
        else:
            log.info("e-mail de licence envoyé à %s (%s)", mask_email(destinataire), transport.name)
            return True
    return False


def envoyer_licence_async(destinataire, cle, plan, expires=None, renewed=False, **options):
    """Comme envoyer_licence(), sans faire attendre le client. Renvoie le thread (utile aux tests)."""
    thread = threading.Thread(target=envoyer_licence, args=(destinataire, cle, plan, expires, renewed),
                              kwargs=options, name="envoi-licence", daemon=True)
    thread.start()
    return thread
