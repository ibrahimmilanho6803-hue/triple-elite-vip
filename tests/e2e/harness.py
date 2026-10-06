"""Site complet de démonstration pour les tests de navigateur : vrais gabarits, vrai JavaScript,
vraie logique de génération (GenerationService), mais licences, IA et historique simulés.

Utilisé par test_ui.py et pour prendre des captures d'écran :
    python tests/e2e/harness.py <dossier de sortie>
"""
import os
import sys
import tempfile
import threading
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for path in (ROOT, os.path.join(ROOT, "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)

from werkzeug.serving import make_server  # noqa: E402

from fakes import sqlite_license_manager  # noqa: E402
from pay_fakes import FakeMailer, FakePayDunya  # noqa: E402
from site_fakes import EMAIL, KEY, FakeLicenses, make_pipeline, sample_history  # noqa: E402


class Site:
    def __init__(self, url, app, licenses, service, pipeline, history):
        self.url, self.app, self.licenses, self.service, self.pipeline, self.history = url, app, licenses, service, pipeline, history


@contextmanager
def running_site(pipeline=None, history=None, history_error=None):
    """Démarre le site sur un port libre ; fournit l'adresse et les faux services."""
    import dashboard
    from generation_service import GenerationService

    tmp = tempfile.mkdtemp(prefix="tev_e2e_")
    licenses = FakeLicenses()
    pipeline = pipeline or make_pipeline()
    service = GenerationService(pipeline=pipeline, cache_dir=os.path.join(tmp, "cache"), results_dir=os.path.join(tmp, "results"))
    history = history if history is not None else sample_history()

    def load_history():
        if history_error:
            raise history_error
        return history

    app = dashboard.create_app(lm=licenses, service=service, history_loader=load_history)
    app.config["TESTING"] = False
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Site(f"http://127.0.0.1:{server.server_port}", app, licenses, service, pipeline, history)
    finally:
        server.shutdown()
        thread.join(5)


class PaySite:
    def __init__(self, url, app, lm, pd, mailer):
        self.url, self.app, self.lm, self.pd, self.mailer = url, app, lm, pd, mailer


@contextmanager
def running_payment():
    """Site de paiement avec une vraie base de licences (SQLite), un faux PayDunya et un faux e-mail."""
    import paiement

    tmp = tempfile.mkdtemp(prefix="tev_e2e_pay_")
    lm = sqlite_license_manager(os.path.join(tmp, "licences.db"))
    pd, mailer = FakePayDunya(), FakeMailer()
    app = paiement.create_app(lm=lm, paydunya=pd, send_license=mailer)
    app.config["TESTING"] = False
    server = make_server("127.0.0.1", 0, app, threaded=True)
    # La « page de paiement PayDunya » est une adresse de ce même serveur (une page 404 suffit : on vérifie
    # seulement que le navigateur y est bien redirigé).
    pd.url_base = f"http://127.0.0.1:{server.server_port}/paydunya-simule"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield PaySite(f"http://127.0.0.1:{server.server_port}", app, lm, pd, mailer)
    finally:
        server.shutdown()
        thread.join(5)


def capture_dashboard(browser, out):
    with running_site(pipeline=make_pipeline(delay=0.05)) as site:
        for name, size in (("desktop", {"width": 1280, "height": 900}), ("mobile", {"width": 390, "height": 844})):
            page = browser.new_page(viewport=size, device_scale_factor=1)
            page.goto(site.url + "/")
            page.wait_for_timeout(300)
            page.screenshot(path=os.path.join(out, f"accueil-{name}.png"), full_page=True)
            page.goto(site.url + "/login")
            page.screenshot(path=os.path.join(out, f"login-{name}.png"), full_page=True)
            page.fill("#email", EMAIL)
            page.fill("#license_key", KEY)
            page.click("button[type=submit]")
            page.wait_for_url("**/app")
            page.wait_for_timeout(500)
            if page.is_visible("#btn-generate"):                  # sinon : combinés déjà en cache, affichés d'emblée
                page.click("#btn-generate")
            page.wait_for_selector("#results:not([hidden])", timeout=15000)
            page.wait_for_timeout(1500)
            page.screenshot(path=os.path.join(out, f"combos-{name}.png"), full_page=True)
            page.click("#tab-history")
            page.wait_for_selector("#history-content:not([hidden])", timeout=10000)
            page.wait_for_timeout(300)
            page.screenshot(path=os.path.join(out, f"historique-{name}.png"), full_page=True)
            page.close()


def capture_payment(browser, out):
    with running_payment() as site:
        for name, size in (("desktop", {"width": 1280, "height": 900}), ("mobile", {"width": 390, "height": 844})):
            page = browser.new_page(viewport=size, device_scale_factor=1)
            page.goto(site.url + "/paiement?plan=yearly")
            page.wait_for_timeout(300)
            page.screenshot(path=os.path.join(out, f"paiement-{name}.png"), full_page=True)
            # Erreur : e-mail invalide (la validation du navigateur est désactivée pour atteindre le serveur).
            page.evaluate("document.getElementById('pay-form').noValidate = true")
            page.fill("#email", "pas-un-email")
            page.click("#pay-btn")
            page.wait_for_selector(".notice--erreur")
            page.screenshot(path=os.path.join(out, f"paiement-erreur-{name}.png"), full_page=True)
            # Achat réussi : nouvelle licence.
            page.goto(site.url + "/paiement")
            page.fill("#email", f"{name}@exemple.com")
            page.click("#pay-btn")
            page.wait_for_url("**/paydunya-simule/**")
            token = site.pd.created[-1]["token"]
            page.goto(site.url + f"/succes?token={token}")
            page.screenshot(path=os.path.join(out, f"succes-attente-{name}.png"), full_page=True)
            site.pd.pay(token)
            page.goto(site.url + f"/succes?token={token}")
            page.wait_for_selector("#license-key")
            page.screenshot(path=os.path.join(out, f"succes-{name}.png"), full_page=True)
            # Renouvellement d'une licence encore valide.
            page.goto(site.url + f"/paiement?email={name}%40exemple.com&plan=monthly")
            page.click("#pay-btn")
            page.wait_for_url("**/paydunya-simule/**")
            token = site.pd.created[-1]["token"]
            site.pd.pay(token)
            page.goto(site.url + f"/succes?token={token}")
            page.wait_for_selector("text=Abonnement prolongé")
            page.screenshot(path=os.path.join(out, f"succes-renouvellement-{name}.png"), full_page=True)
            # Paiement annulé.
            page.goto(site.url + "/paiement")
            page.fill("#email", f"autre-{name}@exemple.com")
            page.click("#pay-btn")
            page.wait_for_url("**/paydunya-simule/**")
            token = site.pd.created[-1]["token"]
            site.pd.cancel(token)
            page.goto(site.url + f"/succes?token={token}")
            page.screenshot(path=os.path.join(out, f"succes-echec-{name}.png"), full_page=True)
            page.close()


if __name__ == "__main__":
    # Captures d'écran manuelles : python tests/e2e/harness.py <dossier de sortie> [dashboard|payment]
    from playwright.sync_api import sync_playwright

    out = sys.argv[1] if len(sys.argv) > 1 else "."
    only = sys.argv[2] if len(sys.argv) > 2 else None
    os.makedirs(out, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        if only in (None, "dashboard"):
            capture_dashboard(browser, out)
        if only in (None, "payment"):
            capture_payment(browser, out)
        browser.close()
    print("captures écrites dans", out)
