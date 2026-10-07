"""Fixtures des tests de navigateur : Chromium (tests ignorés s'il est absent), un site de démonstration
par test, et une page qui échoue au moindre message d'erreur du navigateur (JavaScript, CSP, ressource).

Installation, une seule fois : python -m pip install playwright && python -m playwright install chromium
"""
import contextlib
import os

import pytest

import harness
from site_fakes import make_pipeline


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api", reason="Playwright n'est pas installé")
    # Sans ce réglage (lu au lancement de Playwright), « hors connexion » ne s'applique pas aux requêtes d'un service
    # worker : le test du mode sans réseau (TestApplication) verrait le site répondre alors que le réseau est « coupé ».
    os.environ.setdefault("PW_EXPERIMENTAL_SERVICE_WORKER_NETWORK_EVENTS", "1")
    with sync_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except Exception as exc:                         # Chromium absent ou inutilisable ici
            pytest.skip(f"Chromium indisponible ({exc.__class__.__name__}) : python -m playwright install chromium")
        sync_api.expect.set_options(timeout=15_000)
        try:
            yield chromium
        finally:
            chromium.close()


DESKTOP = {"width": 1280, "height": 900}
PHONE = {"width": 390, "height": 844}


# Types de ressources dont l'échec (404...) est un vrai défaut : style, script, police, image. Une page ou un appel
# de l'API qui répond 401, 404 ou 500 est souvent voulu dans un test : le test vérifie alors ce que l'écran affiche.
ASSET_TYPES = ("stylesheet", "script", "font", "image", "media", "manifest", "other")


def watch(page):
    """Note ce que le navigateur signale dans `page.problems` : erreurs et avertissements de la console
    (exceptions JavaScript, violations de la politique de sécurité...) et ressources statiques introuvables."""
    problems = []

    def on_console(msg):
        # « Failed to load resource » accompagne chaque réponse 4xx/5xx, voulue ou non : traitée plus bas.
        if msg.type in ("error", "warning") and not msg.text.startswith("Failed to load resource"):
            problems.append(f"console.{msg.type} : {msg.text}")

    def on_response(response):
        if response.status >= 400 and response.request.resource_type in ASSET_TYPES:
            problems.append(f"ressource en échec : HTTP {response.status} {response.url}")

    page.on("console", on_console)
    page.on("response", on_response)
    page.on("pageerror", lambda exc: problems.append(f"exception JavaScript : {exc}"))
    page.problems = problems
    return problems


def check_no_problems(problems):
    assert not problems, "Le navigateur a signalé des erreurs :\n" + "\n".join(problems)


@pytest.fixture
def context(browser):
    ctx = browser.new_context(locale="fr-FR", timezone_id="Europe/Paris", viewport=DESKTOP,
                              permissions=["clipboard-read", "clipboard-write"])
    ctx.set_default_timeout(15_000)
    yield ctx
    ctx.close()


@pytest.fixture
def page(context):
    """Page de test (ordinateur). Le test échoue s'il reste des problèmes à la fin ; un test qui provoque
    volontairement une erreur (réponse 401, 500...) vide `page.problems` après coup."""
    page = context.new_page()
    problems = watch(page)
    yield page
    check_no_problems(problems)


@pytest.fixture
def phone_page(browser):
    """Même chose sur un écran de téléphone (390 x 844, tactile)."""
    ctx = browser.new_context(locale="fr-FR", timezone_id="Europe/Paris", viewport=PHONE, is_mobile=True,
                              has_touch=True, permissions=["clipboard-read", "clipboard-write"])
    ctx.set_default_timeout(15_000)
    page = ctx.new_page()
    problems = watch(page)
    yield page
    ctx.close()
    check_no_problems(problems)


@pytest.fixture
def make_page(browser):
    """Fabrique de pages aux réglages particuliers (navigateur d'iPhone, application déjà installée...) :
    `make_page(user_agent=...)`. Chacune est surveillée comme `page` et fermée en fin de test."""
    contexts, watched = [], []

    def make(**options):
        options.setdefault("locale", "fr-FR")
        options.setdefault("timezone_id", "Europe/Paris")
        options.setdefault("viewport", DESKTOP)
        context = browser.new_context(**options)
        context.set_default_timeout(15_000)
        page = context.new_page()
        watched.append(watch(page))
        contexts.append(context)
        return page

    yield make
    for context in contexts:
        context.close()
    for problems in watched:
        check_no_problems(problems)


@pytest.fixture
def start_site():
    """Démarre un site client de démonstration (arguments de harness.running_site) ; arrêté en fin de test."""
    stack = contextlib.ExitStack()

    def start(**kwargs):
        kwargs.setdefault("pipeline", make_pipeline(delay=0.05))
        return stack.enter_context(harness.running_site(**kwargs))

    yield start
    stack.close()


@pytest.fixture
def site(start_site):
    return start_site()


@pytest.fixture
def pay_site():
    """Site de paiement de démonstration : vraie base de licences (SQLite), faux PayDunya, faux e-mail."""
    with harness.running_payment() as running:
        yield running
