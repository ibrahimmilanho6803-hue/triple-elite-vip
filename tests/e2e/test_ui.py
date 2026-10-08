"""Parcours réels dans Chromium : les vrais gabarits, le vrai JavaScript et la vraie logique des deux sites ;
seuls les services extérieurs (IA, PayDunya, e-mail, base de licences du site client) sont simulés.

    python -m pytest tests/e2e -q

Ce que ces tests attrapent et que les tests Flask ne voient pas : un script bloqué par la politique de sécurité,
un formulaire refusé par le navigateur (en-têtes Origin/Referer), une page qui déborde sur téléphone,
un bouton qui ne réagit pas, une erreur JavaScript.
"""
import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright n'est pas installé")
from playwright.sync_api import expect  # noqa: E402

import config  # noqa: E402
import license_manager  # noqa: E402
from paydunya import PayDunyaError  # noqa: E402
from pipeline import GenerationError  # noqa: E402
from site_fakes import EMAIL, KEY, history_with, make_pipeline  # noqa: E402

def norm(text):
    """Texte sur une seule ligne, insécables compris (le site en met avant « % », « € », « : »...)."""
    return re.sub(r"[\s  ]+", " ", text or "").strip()


def log_in(page, site, email=EMAIL, key=KEY):
    page.goto(site.url + "/login")
    page.fill("#email", email)
    page.fill("#license_key", key)
    page.click("button[type=submit]")


def enter(page, site):
    """Connexion, puis attente de la fin du chargement de l'espace client (dont la première demande d'état au
    serveur) : un test qui modifie ensuite la licence ne doit pas se croiser avec cette demande."""
    log_in(page, site)
    page.wait_for_url("**/app")
    page.wait_for_load_state("networkidle")


def log_in_and_generate(page, site):
    enter(page, site)
    page.click("#btn-generate")
    expect(page.locator("#results")).to_be_visible()


def no_horizontal_overflow(page):
    """Aucun élément ne dépasse de l'écran : pas de défilement horizontal de la page."""
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def make_free_pick(site):
    """Un abonné vient de générer les combinés : le combiné gratuit du jour existe (sans cela, /gratuit est vide)."""
    site.service.request_generation()
    site.service._thread.join(15)


# ======================================================================================================
# Site des clients
# ======================================================================================================

class TestAccesEtConnexion:
    def test_connexion_reussie_mene_a_l_espace_client(self, page, site):
        enter(page, site)
        expect(page.locator("#app")).to_contain_text("Abonnement actif jusqu’au")
        expect(page.locator("#btn-generate")).to_be_enabled()

    def test_la_cle_est_acceptee_avec_espaces_et_majuscules(self, page, site):
        log_in(page, site, email="  Client@Exemple.com ", key=f"  {KEY.upper()} ")
        page.wait_for_url("**/app")

    def test_mauvaise_cle(self, page, site):
        log_in(page, site, key="0000000000000000")
        expect(page.locator(".notice--erreur")).to_contain_text("incorrect")
        assert page.url.endswith("/login")
        expect(page.locator("#email")).to_have_value(EMAIL)          # la saisie n'est pas perdue
        expect(page.locator("#license_key")).to_have_value("")       # la clé, si

    def test_espace_client_ferme_sans_connexion(self, page, site):
        page.goto(site.url + "/app")
        expect(page).to_have_url(re.compile(r"/login\?raison=session$"))
        expect(page.locator(".notice")).to_contain_text("session a expiré")

    def test_deconnexion(self, page, site):
        enter(page, site)
        page.click("text=Déconnexion")
        expect(page).to_have_url(re.compile(r"/login\?raison=deconnecte$"))
        page.goto(site.url + "/app")
        expect(page).to_have_url(re.compile(r"/login"))

    def test_abonnement_expire_pendant_l_utilisation(self, page, site):
        enter(page, site)
        site.licenses.records[EMAIL]["expires"] = license_manager._utcnow() - timedelta(days=2)
        site.app.extensions["tev"].gate.forget(EMAIL)
        page.click("#tab-history")                                    # première requête protégée après l'expiration
        expect(page).to_have_url(re.compile(r"/login\?raison=expire$"))
        expect(page.locator(".notice")).to_contain_text("abonnement a expiré")
        renew = page.locator("a", has_text="Renouveler mon abonnement")
        # Le lien de renouvellement préremplit l'e-mail : pas de licence créée par erreur sur une autre adresse.
        assert f"/paiement?email={quote(EMAIL, safe='')}" in renew.first.get_attribute("href")

    def test_licence_desactivee_pendant_l_utilisation(self, page, site):
        enter(page, site)
        site.licenses.records[EMAIL]["active"] = False
        site.app.extensions["tev"].gate.forget(EMAIL)
        page.click("#tab-history")
        expect(page).to_have_url(re.compile(r"/login\?raison=desactive$"))
        expect(page.locator(".notice")).to_contain_text("désactivée")

    def test_session_perdue_pendant_une_generation(self, page, site, context):
        enter(page, site)
        context.clear_cookies()
        page.click("#btn-generate")
        expect(page).to_have_url(re.compile(r"/login\?raison=session$"))


class TestGenerationEtHistorique:
    def test_generation_des_trois_combines(self, page, site):
        enter(page, site)
        page.click("#btn-generate")

        expect(page.locator("#progress")).to_be_visible()             # avancement affiché pendant le travail
        expect(page.locator("#progress-msg")).not_to_be_empty()
        expect(page.locator("#results")).to_be_visible()
        expect(page.locator("#progress")).to_be_hidden()

        slips = page.locator("#results-grid .slip")
        expect(slips).to_have_count(3)
        first = norm(slips.nth(0).inner_text())
        assert "Combiné 1" in first
        assert "Arsenal – Brighton" in first
        assert "Cote totale 2,55" in first                            # virgule française
        assert "Chance estimée 31 %" in first
        assert "confiance 70 %" in first
        for slip in range(3):
            expect(slips.nth(slip).locator(".leg")).to_have_count(3)
        assert "15 matchs analysés dans 5 championnats" in norm(page.locator("#results-meta").inner_text())
        # Cote estimée : le « ≈ » et la mention lue par les lecteurs d'écran ; cote réelle (Bayern) : ni l'un ni l'autre.
        estimated = slips.nth(0).locator(".leg").first
        expect(estimated.locator(".approx")).to_have_text("≈")
        real = slips.nth(1).locator(".leg").first
        expect(real.locator(".approx")).to_have_count(0)
        assert site.pipeline.state["calls"] == 1

    def test_les_combines_restent_affiches_au_rechargement_sans_nouvelle_generation(self, page, site):
        log_in_and_generate(page, site)
        page.reload()
        expect(page.locator("#results")).to_be_visible()
        expect(page.locator("#results-grid .slip")).to_have_count(3)
        expect(page.locator("#generate")).to_be_hidden()
        assert site.pipeline.state["calls"] == 1                      # le cache sert tous les clients

    def test_historique_avec_scores_et_bilan(self, page, site):
        enter(page, site)
        page.click("#tab-history")
        expect(page.locator("#history-content")).to_be_visible()
        assert page.url.endswith("#historique")

        stamps = [norm(t) for t in page.locator("#history-grid .stamp").all_text_contents()]   # (affichés en capitales)
        assert stamps == ["En attente", "Gagné", "Perdu", "Annulé"]
        expect(page.locator("#bilan-combos")).to_have_text("2 / 5")
        assert "40 % des combinés réglés" in norm(page.locator("#bilan-combos-sub").inner_text())
        expect(page.locator("#bilan-legs")).to_have_text("12 / 18")
        expect(page.locator("#bilan-pending")).to_have_text("1")
        text = norm(page.locator("#history-grid").inner_text())
        assert "Gagné, score 3 – 1" in text                           # le score réel de chaque match joué
        assert "Perdu, score 1 – 0" in text
        assert "Match reporté ou annulé : pronostic non compté" in text
        assert "Match à venir" in text
        # Un lien direct vers l'historique ouvre le bon onglet.
        page.reload()
        expect(page.locator("#history-content")).to_be_visible()
        expect(page.locator("#panel-combos")).to_be_hidden()

    def test_historique_vide(self, page, start_site):
        site = start_site(history={"summary": {}, "combos": []})
        enter(page, site)
        page.click("#tab-history")
        expect(page.locator("#history-empty")).to_be_visible()
        expect(page.locator("#history-empty")).to_contain_text("Pas encore d’historique")
        page.click("#btn-to-combos")
        expect(page.locator("#panel-combos")).to_be_visible()
        expect(page.locator("#tab-combos")).to_be_focused()

    def test_historique_indisponible(self, page, start_site):
        site = start_site(history_error=RuntimeError("base de résultats illisible"))
        enter(page, site)
        page.click("#tab-history")
        expect(page.locator("#history-error .notice--erreur")).to_contain_text("Impossible de charger l’historique")
        assert "illisible" not in page.content()                      # jamais de détail technique côté client

    def test_echec_de_generation_propose_de_reessayer(self, page, start_site):
        error = GenerationError("Pas assez de matchs à venir pour le moment. Réessaie plus tard.")
        site = start_site(pipeline=make_pipeline(delay=0.05, error=error))
        enter(page, site)
        page.click("#btn-generate")
        expect(page.locator("#notice .notice--erreur")).to_contain_text("Pas assez de matchs à venir")
        expect(page.locator("#btn-generate")).to_have_text("Réessayer")
        expect(page.locator("#btn-generate")).to_be_enabled()
        expect(page.locator("#progress")).to_be_hidden()
        expect(page.locator("#results")).to_be_hidden()

    def test_panne_de_l_ia_ne_montre_aucun_detail_technique(self, page, start_site):
        site = start_site(pipeline=make_pipeline(delay=0.05, error=RuntimeError("clé sk-ant-secrète invalide")))
        enter(page, site)
        page.click("#btn-generate")
        expect(page.locator("#notice .notice--erreur")).to_be_visible()
        assert "sk-ant" not in page.content()
        expect(page.locator("#btn-generate")).to_have_text("Réessayer")

    def test_onglets_au_clavier(self, page, site):
        enter(page, site)
        page.focus("#tab-combos")
        page.keyboard.press("ArrowRight")
        expect(page.locator("#tab-history")).to_have_attribute("aria-selected", "true")
        expect(page.locator("#tab-history")).to_be_focused()
        expect(page.locator("#panel-history")).to_be_visible()
        page.keyboard.press("Home")
        expect(page.locator("#tab-combos")).to_have_attribute("aria-selected", "true")
        expect(page.locator("#panel-combos")).to_be_visible()
        page.keyboard.press("End")
        expect(page.locator("#tab-history")).to_have_attribute("aria-selected", "true")


class TestPagesPubliques:
    def test_accueil(self, page, site):
        response = page.goto(site.url + "/")
        assert response.status == 200
        expect(page).to_have_title(re.compile("Triple Elite VIP"))
        expect(page.locator("html")).to_have_attribute("lang", "fr")
        text = norm(page.locator("main").inner_text())
        assert "2 000 FCFA" in text and "6 000 FCFA" in text and "39 400 FCFA" in text
        assert "≈ 3 €" in text and "≈ 9 €" in text and "≈ 60 €" in text
        assert "34 %" in text                                         # l'avertissement honnête sur les combinés
        for plan in ("weekly", "monthly", "yearly"):
            assert page.locator(f"a[href$='/paiement?plan={plan}']").count() >= 1

    @pytest.mark.parametrize("path, status", [("/conditions", 200), ("/login", 200), ("/resultats", 200),
                                              ("/gratuit", 200), ("/n-existe-pas", 404)])
    def test_pages_sans_erreur_de_navigateur(self, page, site, path, status):
        response = page.goto(site.url + path)
        assert response.status == status
        expect(page.locator("h1").first).to_be_visible()

    def test_aucun_avertissement_du_navigateur_apres_le_chargement(self, page, site):
        # Un préchargement de police qui ne correspond pas à l'adresse réellement utilisée n'est signalé
        # que quelques secondes après le chargement : on attend (la page échoue s'il reste un avertissement).
        page.goto(site.url + "/")
        page.wait_for_timeout(4500)

    def test_la_politique_de_securite_bloque_les_scripts_en_ligne(self, page, site):
        page.goto(site.url + "/")
        page.evaluate("""() => { const s = document.createElement('script');
                                 s.textContent = 'window.__injecte = true'; document.head.appendChild(s); }""")
        assert page.evaluate("window.__injecte === true") is False
        assert any("Content Security Policy" in p for p in page.problems)
        page.problems.clear()                                         # la violation est le comportement voulu


class TestResultatsEtCombineGratuit:
    """Pages publiques « Résultats » et « Combiné gratuit du jour » : vrai JavaScript (heures locales, copie du lien)."""

    def test_resultats_affiche_le_bilan_et_les_coupons_joues(self, page, start_site):
        site = start_site(history=history_with(won=8, lost=5, unfinished=1, pending=2))
        response = page.goto(site.url + "/resultats")
        assert response.status == 200
        expect(page.locator("h1")).to_have_text("Nos résultats, gagnés et perdus")
        text = norm(page.locator("main").inner_text())
        assert "8 / 14" in text and "soit 57 % des combinés joués" in text
        expect(page.locator(".history__grid .slip")).to_have_count(12)
        expect(page.locator(".stamp--won").first).to_be_visible()
        expect(page.locator(".stamp--lost").first).to_be_visible()
        expect(page.locator(".leg__result--lost").first).to_contain_text("Perdu, score")
        assert no_horizontal_overflow(page)

    def test_resultats_sans_historique_joue(self, page, start_site):
        site = start_site(history=history_with(pending=2))
        page.goto(site.url + "/resultats")
        expect(page.locator(".empty__title")).to_have_text("Les premiers résultats arrivent")
        expect(page.locator(".bilan")).to_have_count(0)

    @pytest.mark.parametrize("zone", ["Europe/Paris", "Africa/Lagos", "America/New_York"])
    def test_les_heures_sont_celles_de_l_appareil(self, make_page, site, zone):
        make_free_pick(site)
        page = make_page(timezone_id=zone)
        page.goto(site.url + "/gratuit")
        times = page.locator("time.local")
        assert times.count() >= 4                                           # la limite et l'heure de chaque match
        for index in range(times.count()):
            element = times.nth(index)
            local = datetime.fromisoformat(element.get_attribute("datetime")).astimezone(ZoneInfo(zone))
            shown = norm(element.inner_text())
            assert "UTC" not in shown, shown
            assert shown.endswith(f"à {local.hour:02d} h {local.minute:02d}"), (shown, local)
            assert re.search(rf"\b{local.day}\b", shown), (shown, local)

    def test_le_combine_gratuit_est_affiche_en_entier(self, page, site):
        make_free_pick(site)
        page.goto(site.url + "/gratuit")
        expect(page.locator("h1")).to_have_text("Le combiné gratuit du jour")
        expect(page.locator(".free .slip .leg")).to_have_count(3)
        text = norm(page.locator(".free").inner_text())
        assert "Cote totale 2,55" in text.replace("COTE TOTALE", "Cote totale") and "31 %" in text
        for other in ("Bayern", "Lille", "Chelsea", "Napoli"):
            assert other not in text and other not in page.content()         # les autres combinés restent payants
        assert no_horizontal_overflow(page)

    def test_copier_le_lien(self, page, site):
        make_free_pick(site)
        page.goto(site.url + "/gratuit")
        button = page.get_by_role("button", name="Copier le lien")
        expect(button).to_be_visible()                                      # caché dans le HTML, montré par le script
        button.click()
        expect(page.locator("[data-copy-status]")).to_have_text("Lien copié.")
        assert page.evaluate("navigator.clipboard.readText()") == f"{config.SITE_URL}/gratuit"

    @pytest.mark.parametrize("clipboard, copied, message", [
        ("absent", True, "Lien copié."),
        ("refuse", True, "Lien copié."),
        ("absent", False, "Copie impossible : copie l’adresse dans la barre de ton navigateur."),
    ])
    def test_copier_le_lien_sans_presse_papiers_moderne(self, make_page, site, clipboard, copied, message):
        make_free_pick(site)
        page = make_page()
        page.add_init_script("""(() => {
            %s
            document.execCommand = command => {
                const field = document.activeElement;
                window.__copie = { command, text: field && field.value ? field.value.substring(field.selectionStart, field.selectionEnd) : null };
                return %s;
            };
        })()""" % ("Object.defineProperty(navigator, 'clipboard', { value: undefined });" if clipboard == "absent"
                   else "Object.defineProperty(navigator.clipboard, 'writeText', { value: () => Promise.reject(new Error('refusé')) });",
                   "true" if copied else "false"))
        page.goto(site.url + "/gratuit")
        page.get_by_role("button", name="Copier le lien").click()
        expect(page.locator("[data-copy-status]")).to_have_text(message)
        assert page.evaluate("window.__copie") == {"command": "copy", "text": f"{config.SITE_URL}/gratuit"}

    def test_les_boutons_de_partage_sont_de_vrais_liens(self, page, site):
        make_free_pick(site)
        page.goto(site.url + "/gratuit")
        for name, host in (("WhatsApp", "https://wa.me/"), ("Telegram", "https://t.me/")):
            link = page.get_by_role("link", name=name)
            expect(link).to_be_visible()
            assert link.get_attribute("href").startswith(host)
            assert link.get_attribute("target") == "_blank" and "noopener" in link.get_attribute("rel")

    def test_sans_javascript_le_partage_marche_et_les_heures_restent_en_utc(self, make_page, site):
        make_free_pick(site)
        page = make_page(java_script_enabled=False)
        page.goto(site.url + "/gratuit")
        expect(page.get_by_role("link", name="WhatsApp")).to_be_visible()
        expect(page.get_by_role("link", name="Telegram")).to_be_visible()
        expect(page.get_by_role("button", name="Copier le lien")).to_be_hidden()      # il a besoin du script
        assert "UTC" in norm(page.locator(".free").inner_text())

    def test_sans_generation_la_page_gratuite_est_vide_et_renvoie_vers_les_resultats(self, page, site):
        page.goto(site.url + "/gratuit")
        expect(page.locator(".empty__title")).to_have_text("Le combiné gratuit n’est pas encore prêt")
        expect(page.get_by_role("link", name="WhatsApp")).to_have_count(0)
        page.locator(".empty").get_by_role("link", name="Voir les résultats").click()
        expect(page).to_have_url(re.compile(r"/resultats$"))
        expect(page.locator("h1")).to_have_text("Nos résultats, gagnés et perdus")

    def test_visiter_ces_pages_ne_lance_aucune_generation(self, page, site):
        for path in ("/", "/resultats", "/gratuit"):
            page.goto(site.url + path)
        assert site.pipeline.state["calls"] == 0 and site.service.generations_today() == 0

    def test_depuis_l_accueil_on_arrive_aux_resultats_et_au_combine_gratuit(self, page, start_site):
        site = start_site(history=history_with(won=8, lost=5, unfinished=1, pending=2))
        make_free_pick(site)
        page.goto(site.url + "/")
        section = page.locator("#resultats")
        expect(section).to_contain_text("8 / 14")
        expect(section).to_contain_text("57 %")
        section.get_by_role("link", name="Voir tous les résultats").click()
        expect(page).to_have_url(re.compile(r"/resultats$"))
        page.go_back()
        page.locator("#resultats").get_by_role("link", name="Voir le combiné gratuit du jour").click()
        expect(page).to_have_url(re.compile(r"/gratuit$"))
        expect(page.locator(".free .slip")).to_be_visible()

    def test_le_pied_de_page_garde_les_liens_vers_les_nouvelles_pages(self, phone_page, site):
        phone_page.goto(site.url + "/")
        footer = phone_page.locator("footer")
        expect(footer.get_by_role("link", name="Résultats")).to_be_visible()
        expect(footer.get_by_role("link", name="Combiné gratuit du jour")).to_be_visible()
        assert no_horizontal_overflow(phone_page)


@pytest.mark.parametrize("path", ["/", "/login", "/conditions", "/resultats", "/gratuit"])
def test_pages_publiques_sans_debordement_sur_telephone(phone_page, site, path):
    phone_page.goto(site.url + path)
    assert no_horizontal_overflow(phone_page)


@pytest.mark.parametrize("path", ["/", "/resultats", "/gratuit"])
def test_pages_publiques_pleines_sans_debordement_sur_telephone(phone_page, start_site, path):
    """Avec un historique fourni et un combiné gratuit : les coupons (cotes, scores, tampons) tiennent dans l'écran."""
    site = start_site(history=history_with(won=8, lost=5, unfinished=1, pending=2, void=1))
    make_free_pick(site)
    phone_page.goto(site.url + path)
    assert no_horizontal_overflow(phone_page)
    for box in phone_page.locator(".slip").evaluate_all(
            "slips => slips.map(s => { const r = s.getBoundingClientRect(); return [r.left, r.right]; })"):
        assert box[0] >= 0 and box[1] <= 390 + 0.5, box


def test_espace_client_sans_debordement_sur_telephone(phone_page, site):
    log_in_and_generate(phone_page, site)
    assert no_horizontal_overflow(phone_page)
    phone_page.click("#tab-history")
    expect(phone_page.locator("#history-content")).to_be_visible()
    assert no_horizontal_overflow(phone_page)


# ======================================================================================================
# Barre de navigation du bas (les deux sites)
# ======================================================================================================

BARRE = ["Accueil", "Combiné gratuit", "Résultats combinés", "Accès VIP", "Abonnement VIP"]
DORE = "rgb(227, 179, 65)"                                              # --or de la feuille de style
TITRES = {"/": "Trois combinés", "/gratuit": "Le combiné gratuit du jour", "/resultats": "Nos résultats, gagnés et perdus",
          "/login": "Connexion"}


def boutons(page):
    """Boîtes (gauche, droite, haut, bas) des boutons de la barre, de gauche à droite."""
    return page.locator("nav.tabbar a").evaluate_all(
        "els => els.map(e => { const r = e.getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom]; })")


class TestBarreDeNavigation:
    def test_cinq_boutons_de_meme_largeur_en_bas_de_l_ecran_sur_telephone(self, phone_page, site):
        phone_page.goto(site.url + "/")
        links = phone_page.locator("nav.tabbar").get_by_role("link")
        expect(links).to_have_count(5)
        assert [norm(name) for name in links.all_inner_texts()] == BARRE
        boxes = boutons(phone_page)
        widths = [right - left for left, right, _, _ in boxes]
        assert max(widths) - min(widths) < 1.5, widths                              # cinq boîtes égales
        assert boxes[0][0] == pytest.approx(0, abs=1) and boxes[-1][1] == pytest.approx(390, abs=1)    # toute la largeur
        assert all(box[3] == pytest.approx(844, abs=1) for box in boxes)             # collées au bas de l'écran
        assert no_horizontal_overflow(phone_page)

    @pytest.mark.parametrize("width", [320, 360, 390])
    def test_les_noms_des_boutons_tiennent_sur_deux_lignes_au_plus(self, make_page, site, width):
        """À 320 px, chaque bouton fait 64 px de large : « Abonnement » ne doit être coupé ni en deux lignes, ni dépasser."""
        page = make_page(viewport={"width": width, "height": 640}, is_mobile=True, has_touch=True)
        page.goto(site.url + "/")
        page.evaluate("document.fonts.ready.then(() => true)")                       # polices chargées : mesures fiables
        broken = page.locator(".tabbar__label").evaluate_all("""els => els.filter(e => {
            const text = e.firstChild;                                  // le texte du nom, sans autre balise
            const lines = range => new Set(Array.from(range.getClientRects()).map(r => Math.round(r.top))).size;
            const all = document.createRange();
            all.selectNodeContents(e);
            if (lines(all) > 2 || e.scrollWidth > e.clientWidth + 1) return true;
            for (const word of text.data.matchAll(/\\S+/g)) {            // un mot réparti sur deux lignes est un mot coupé
                const one = document.createRange();
                one.setStart(text, word.index);
                one.setEnd(text, word.index + word[0].length);
                if (lines(one) > 1) return true;
            }
            return false;
        }).map(e => e.textContent.trim())""")
        assert broken == []
        assert no_horizontal_overflow(page)

    def test_chaque_bouton_ouvre_la_page_prevue(self, phone_page, site):
        make_free_pick(site)
        bar = phone_page.locator("nav.tabbar")
        phone_page.goto(site.url + "/")
        for name, path in (("Combiné gratuit", "/gratuit"), ("Résultats combinés", "/resultats"), ("Accès VIP", "/login"),
                           ("Accueil", "/")):
            bar.get_by_role("link", name=name).click()
            expect(phone_page).to_have_url(site.url + path)
            expect(phone_page.locator("h1")).to_contain_text(TITRES[path])
        # « Abonnement VIP » quitte le site client pour le site de paiement (autre adresse : on ne la suit pas ici).
        expect(bar.get_by_role("link", name="Abonnement VIP")).to_have_attribute("href", config.PAIEMENT_URL + "/paiement")

    def test_acces_vip_mene_droit_a_l_espace_d_un_client_connecte(self, phone_page, site):
        enter(phone_page, site)
        phone_page.goto(site.url + "/")
        phone_page.locator("nav.tabbar").get_by_role("link", name="Accès VIP").click()
        expect(phone_page).to_have_url(site.url + "/app")                           # pas de page de connexion entre les deux
        expect(phone_page.locator("#app")).to_contain_text("Abonnement actif jusqu’au")
        expect(phone_page.locator("nav.tabbar a[aria-current=page]")).to_have_text("Accès VIP")
        expect(phone_page.locator("header").get_by_role("button", name="Déconnexion")).to_be_visible()

    @pytest.mark.parametrize("path, name", [("/", "Accueil"), ("/gratuit", "Combiné gratuit"),
                                            ("/resultats", "Résultats combinés"), ("/login", "Accès VIP")])
    def test_le_bouton_de_la_page_affichee_est_dore(self, phone_page, site, path, name):
        make_free_pick(site)
        phone_page.goto(site.url + path)
        current = phone_page.locator("nav.tabbar a[aria-current=page]")
        expect(current).to_have_count(1)
        expect(current).to_have_text(name)
        assert current.evaluate("e => getComputedStyle(e).color") == DORE
        other = phone_page.locator("nav.tabbar a:not([aria-current])").first
        assert other.evaluate("e => getComputedStyle(e).color") != DORE

    def test_la_barre_suit_le_defilement_et_ne_cache_pas_le_bas_de_la_page(self, phone_page, site):
        phone_page.goto(site.url + "/")
        bar = phone_page.locator("nav.tabbar")
        phone_page.evaluate("window.scrollTo({top: 1500, behavior: 'instant'})")
        box = bar.bounding_box()
        assert box["y"] + box["height"] == pytest.approx(844, abs=1)                # toujours collée au bas de l'écran
        phone_page.evaluate("window.scrollTo({top: document.documentElement.scrollHeight, behavior: 'instant'})")
        legal, box = phone_page.locator(".footer__legal").bounding_box(), bar.bounding_box()
        assert legal["y"] + legal["height"] <= box["y"] + 1                         # la dernière ligne reste lisible

    def test_sur_ordinateur_la_barre_flotte_en_bas_au_centre(self, page, site):
        page.goto(site.url + "/")
        expect(page.locator("nav.tabbar").get_by_role("link")).to_have_count(5)
        box = page.locator("nav.tabbar").bounding_box()
        assert box["width"] <= 641
        assert box["x"] + box["width"] / 2 == pytest.approx(640, abs=1)             # centrée dans la fenêtre de 1280 px
        assert box["y"] + box["height"] == pytest.approx(900 - 16, abs=1)
        assert no_horizontal_overflow(page)
        assert page.locator("header a").count() == 1                                # l'en-tête ne garde que la marque

    def test_sans_combine_gratuit_la_barre_a_quatre_boutons(self, phone_page, site, monkeypatch):
        monkeypatch.setattr(config, "FREE_PICK_ENABLED", False)
        phone_page.goto(site.url + "/")
        links = phone_page.locator("nav.tabbar").get_by_role("link")
        assert [norm(name) for name in links.all_inner_texts()] == [name for name in BARRE if name != "Combiné gratuit"]
        widths = [right - left for left, right, _, _ in boutons(phone_page)]
        assert max(widths) - min(widths) < 1.5 and sum(widths) == pytest.approx(390, abs=1.5)

    def test_navigation_au_clavier_dans_l_ordre_de_la_page(self, page, site):
        """La barre est la dernière chose de la page : la touche Tab y arrive après le contenu et le pied de page."""
        page.goto(site.url + "/conditions")
        page.locator("footer a").last.focus()
        page.keyboard.press("Tab")
        expect(page.locator("nav.tabbar a").first).to_be_focused()
        for _ in range(4):
            page.keyboard.press("Tab")
        expect(page.locator("nav.tabbar a").last).to_be_focused()
        box = page.locator("nav.tabbar a").last.bounding_box()
        assert box["y"] + box["height"] <= 900                                      # le bouton atteint est bien à l'écran

    def test_la_page_hors_connexion_n_a_pas_de_barre(self, phone_page, site):
        phone_page.goto(site.url + "/hors-ligne")
        expect(phone_page.locator("h1")).to_have_text("Pas de connexion")
        expect(phone_page.locator("nav.tabbar")).to_have_count(0)

    def test_le_site_de_paiement_a_la_meme_barre(self, phone_page, pay_site):
        phone_page.goto(pay_site.url + "/paiement")
        links = phone_page.locator("nav.tabbar").get_by_role("link")
        assert [norm(name) for name in links.all_inner_texts()] == BARRE
        expect(phone_page.locator("nav.tabbar a[aria-current=page]")).to_have_text("Abonnement VIP")
        hrefs = links.evaluate_all("els => els.map(e => e.getAttribute('href'))")
        assert hrefs[:4] == [config.SITE_URL, config.SITE_URL + "/gratuit", config.SITE_URL + "/resultats",
                             config.SITE_URL + "/login"]                            # pages de l'autre site : adresses complètes
        assert boutons(phone_page)[0][3] == pytest.approx(844, abs=1)
        assert no_horizontal_overflow(phone_page)


# ======================================================================================================
# Offres et prix : trois formules en FCFA, lisibles sur tous les écrans
# ======================================================================================================

# Géométrie de chaque carte de formule : son contenu reste dans la carte, l'unité (FCFA) reste sur la ligne du montant.
GEOMETRIE_DES_FORMULES = """els => els.map(card => {
    const box = card.getBoundingClientRect();
    const rect = selector => card.querySelector(selector).getBoundingClientRect();
    const inside = Array.from(card.querySelectorAll(
        '.plan__name, .plan__amount, .plan__unit, .plan__per, .plan__note, .plan__list li, .btn')).every(e => {
        const r = e.getBoundingClientRect();
        return r.left >= box.left - 0.5 && r.right <= box.right + 0.5;
    });
    const amount = rect('.plan__amount'), unit = rect('.plan__unit');
    const badge = card.querySelector('.plan__badge');
    return {top: box.top, bottom: box.bottom, left: box.left, inside,
            sameLine: unit.top < amount.bottom && unit.bottom > amount.top,
            nameTop: rect('.plan__name').top, buttonBottom: rect('.btn').bottom,
            badgeTop: badge ? badge.getBoundingClientRect().top : null};
})"""


class TestOffres:
    @pytest.mark.parametrize("width", [320, 390, 768, 1000, 1280])
    def test_les_trois_formules_tiennent_dans_l_ecran(self, make_page, site, width):
        page = make_page(viewport={"width": width, "height": 900}, is_mobile=width < 800, has_touch=width < 800)
        page.goto(site.url + "/")
        page.evaluate("document.fonts.ready.then(() => true)")                       # polices chargées : mesures fiables
        assert no_horizontal_overflow(page)
        cards = page.locator(".plan").evaluate_all(GEOMETRIE_DES_FORMULES)
        assert len(cards) == 3
        assert all(card["inside"] for card in cards), cards                          # rien ne dépasse de sa carte
        assert all(card["sameLine"] for card in cards), cards                        # « FCFA » reste à côté du montant
        if width > 960:                                                              # trois colonnes de même hauteur
            assert len({round(card["top"]) for card in cards}) == 1
            assert len({round(card["buttonBottom"]) for card in cards}) == 1         # boutons alignés en bas des cartes
            assert cards[0]["left"] < cards[1]["left"] < cards[2]["left"]
            # Le badge « Tarif de lancement » est posé sur le bord : le contenu des trois cartes reste à la même hauteur.
            assert len({round(card["nameTop"]) for card in cards}) == 1
        else:                                                                        # une colonne, dans l'ordre
            for before, after in zip(cards, cards[1:]):
                assert after["top"] >= before["bottom"] + 20, cards
            assert cards[1]["badgeTop"] >= cards[0]["bottom"] + 10                   # le badge ne mord pas sur la carte d'avant

    def test_sans_tarif_de_lancement_ni_badge_ni_date(self, page, site, monkeypatch):
        monkeypatch.setattr(config, "LAUNCH_PRICE_UNTIL", None)
        page.goto(site.url + "/")
        expect(page.locator(".plan__badge")).to_have_count(0)
        assert "Tarif de lancement" not in norm(page.locator("main").inner_text())
        assert "30 jours d’accès" in norm(page.locator(".plan--vedette").inner_text())

    @pytest.mark.parametrize("width", [320, 360, 390, 1280])
    def test_les_choix_de_la_page_de_paiement_tiennent_dans_l_ecran(self, make_page, pay_site, width):
        page = make_page(viewport={"width": width, "height": 900}, is_mobile=width < 800, has_touch=width < 800)
        page.goto(pay_site.url + "/paiement")
        page.evaluate("document.fonts.ready.then(() => true)")
        assert no_horizontal_overflow(page)
        boxes = page.locator(".choice__box").evaluate_all("""els => els.map(box => {
            const r = box.getBoundingClientRect();
            const amount = box.querySelector('.choice__amount');
            const a = amount.getBoundingClientRect();
            const texts = Array.from(box.querySelectorAll('.choice__name, .choice__desc, .choice__tag'));
            return {right: r.right, amountLeft: a.left, amountRight: a.right, amountTop: a.top,
                    textRight: Math.max(...texts.map(e => e.getBoundingClientRect().right)),
                    textBottom: Math.max(...texts.map(e => e.getBoundingClientRect().bottom)),
                    cut: texts.some(e => e.scrollWidth > e.clientWidth + 1) || amount.scrollWidth > amount.clientWidth + 1,
                    eurRight: box.querySelector('.choice__eur').getBoundingClientRect().right};
        })""")
        assert len(boxes) == 3
        for box in boxes:
            assert not box["cut"] and box["amountRight"] <= box["right"] and box["eurRight"] <= box["right"], box
            side_by_side = box["amountTop"] < box["textBottom"]
            if side_by_side:
                assert box["textRight"] <= box["amountLeft"], box                    # le prix ne recouvre pas le texte
            assert side_by_side == (width > 380), (width, box)                       # petit téléphone : le prix passe dessous
        total = page.locator(".pay__total strong").bounding_box()
        assert total["x"] + total["width"] <= width                                  # « 39 400 FCFA » du récapitulatif en entier
        page.click("label.choice:has(input[value=yearly])")
        total = page.locator(".pay__total strong").bounding_box()
        assert total["x"] + total["width"] <= width and no_horizontal_overflow(page)

    def test_les_boutons_d_essai_menent_au_pass_de_7_jours(self, page, site):
        make_free_pick(site)
        for path in ("/gratuit", "/resultats"):
            page.goto(site.url + path)
            button = page.get_by_role("link", name="Essayer 7 jours, 2 000 FCFA")
            expect(button).to_have_count(1)
            assert button.get_attribute("href") == f"{config.PAIEMENT_URL}/paiement?plan=weekly", path


# ======================================================================================================
# Application installable (site des clients)
# ======================================================================================================

# Ce que fait Chrome sur Android quand le site est installable : il annonce l'événement « beforeinstallprompt »,
# que le site garde pour son propre bouton. Chromium sans écran ne le déclenche pas tout seul : on le simule.
PROPOSITION_D_INSTALLER = """() => {
    const event = new Event('beforeinstallprompt', { cancelable: true });
    event.prompt = () => { window.__invitations = (window.__invitations || 0) + 1; return Promise.resolve(); };
    event.userChoice = Promise.resolve({ outcome: 'accepted', platform: 'web' });
    window.dispatchEvent(event);
    return event.defaultPrevented;
}"""
CACHES_DU_NAVIGATEUR = """async () => {
    const out = {};
    for (const name of await caches.keys()) {
        const cache = await caches.open(name);
        out[name] = (await cache.keys()).map(request => new URL(request.url).pathname + new URL(request.url).search);
    }
    return out;
}"""
CHROME_ANDROID = {
    "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0.0.0 Mobile Safari/537.36",
    "viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}
SAFARI_IPHONE = {
    "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                  "Version/17.5 Mobile/15E148 Safari/604.1",
    "viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}
CHROME_IPHONE = dict(SAFARI_IPHONE, user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                     "AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0.6478.153 Mobile/15E148 Safari/604.1")


def propose_install(page):
    """Simule l'annonce « le site est installable » de Chrome sur Android. Renvoie True si le site a retenu
    l'invitation (preventDefault) pour la présenter avec son propre bouton."""
    return page.evaluate(PROPOSITION_D_INSTALLER)


def wait_for(page, expression, what, seconds=15):
    """Attend qu'une expression JavaScript devienne vraie. (page.wait_for_function passe par eval, que la politique
    de sécurité du site interdit : d'où cette boucle.)"""
    deadline = time.monotonic() + seconds
    while not page.evaluate(expression):
        assert time.monotonic() < deadline, f"Délai dépassé : {what}"
        page.wait_for_timeout(100)


def wait_for_service_worker(page):
    """Le service worker est installé, activé et contrôle la page : le mode « hors connexion » est prêt."""
    wait_for(page, "navigator.serviceWorker.controller !== null", "le service worker prend le contrôle de la page")


def go_offline(page):
    """Coupe le réseau du navigateur, service worker compris. Constaté avec Playwright : la coupure ne tient que pour la
    prochaine requête du service worker ; on la renouvelle donc avant chaque navigation."""
    page.context.set_offline(False)
    page.context.set_offline(True)


class TestApplication:
    def test_chromium_juge_le_site_installable(self, page, site):
        page.goto(site.url + "/login")
        wait_for_service_worker(page)
        cdp = page.context.new_cdp_session(page)
        manifest = cdp.send("Page.getAppManifest")
        assert manifest["url"].endswith("/manifest.webmanifest") and manifest["errors"] == []
        # Les mêmes vérifications que Chrome fait avant de proposer « Installer » : manifeste, icônes, service worker.
        assert cdp.send("Page.getInstallabilityErrors")["installabilityErrors"] == []

    def test_le_service_worker_ne_garde_que_la_page_hors_connexion_apres_une_vraie_session(self, page, site):
        log_in_and_generate(page, site)
        wait_for_service_worker(page)
        page.click("#tab-history")
        expect(page.locator("#history-content")).to_be_visible()
        caches = page.evaluate(CACHES_DU_NAVIGATEUR)
        (name, files), = caches.items()                                       # un seul cache...
        assert name.startswith("tev-")
        assert files[0] == "/hors-ligne" and len(files) == 5, files           # ...avec la page, le style, l'icône, 2 polices
        # Ni espace client, ni API, ni connexion : un téléphone peut être prêté, ces données restent chez le serveur.
        assert not [path for path in files if path.startswith(("/app", "/api/", "/login", "/logout"))], files

    def test_l_ancien_cache_est_supprime_a_l_activation_pas_ceux_des_autres(self, page, site):
        page.add_init_script("""if (window.caches) {
            caches.open('tev-ancien').then(cache => cache.put('/vieux', new Response('vieux')));
            caches.open('autre-cache').then(cache => cache.put('/autre', new Response('autre')));
        }""")
        page.goto(site.url + "/login")
        wait_for_service_worker(page)
        names = sorted(page.evaluate(CACHES_DU_NAVIGATEUR))
        assert "tev-ancien" not in names and "autre-cache" in names and len(names) == 2, names

    def test_sans_connexion_la_page_hors_connexion_s_affiche_puis_reessayer_ramene_a_l_espace(self, page, site):
        enter(page, site)
        wait_for_service_worker(page)
        for path in ("/app", "/conditions"):                                  # n'importe quelle page, pas seulement l'espace
            go_offline(page)
            page.goto(site.url + path)
            expect(page.locator("h1")).to_have_text("Pas de connexion")
        # Le style et les polices viennent du cache du service worker : sans réseau, il n'y a pas d'autre source.
        expect(page.locator("body")).to_have_css("background-color", "rgb(10, 15, 44)")
        families = page.evaluate("""async () => { await document.fonts.ready;
            return [...document.fonts].filter(font => font.status === 'loaded').map(font => font.family.replaceAll('"', '')); }""")
        assert {"Onest", "Big Shoulders Display"} <= set(families), families
        expect(page.locator("body")).not_to_contain_text(EMAIL)               # rien de personnel dans la page gardée
        page.context.set_offline(False)
        page.get_by_role("link", name="Réessayer").click()
        expect(page).to_have_url(re.compile(r"/app$"))
        expect(page.locator("#app")).to_contain_text("Abonnement actif jusqu’au")

    def test_le_bouton_installer_apparait_quand_le_navigateur_propose_l_installation(self, make_page, site):
        page = make_page(**CHROME_ANDROID)
        enter(page, site)
        box = page.locator("[data-install]")
        expect(box).to_be_hidden()                                            # rien tant que le navigateur ne propose rien
        assert propose_install(page) is True                                  # l'invitation est gardée pour notre bouton
        expect(box).to_be_visible()
        expect(box.locator("[data-install-android]")).to_be_visible()
        expect(box.locator("[data-install-ios]")).to_be_hidden()
        assert box.bounding_box()["y"] < page.locator(".tabs").bounding_box()["y"]       # en haut de l'espace client
        page.get_by_role("button", name="Installer l’application").click()
        wait_for(page, "window.__invitations === 1", "l'invitation du navigateur est présentée")
        expect(box).to_be_hidden()

    def test_sur_ordinateur_le_navigateur_garde_sa_propre_invitation(self, page, site):
        enter(page, site)
        assert propose_install(page) is False                                 # le site n'y touche pas (icône de la barre d'adresse)
        expect(page.locator("[data-install]")).to_be_hidden()

    def test_l_invitation_se_montre_aussi_sur_les_pages_publiques(self, make_page, site):
        page = make_page(**CHROME_ANDROID)
        for path in ("/", "/login"):
            page.goto(site.url + path)
            assert propose_install(page) is True
            expect(page.locator("[data-install]")).to_be_visible()
            expect(page.get_by_role("button", name="Installer l’application")).to_be_visible()

    def test_plus_tard_masque_l_invitation_aux_visites_suivantes(self, make_page, site):
        page = make_page(**CHROME_ANDROID)
        enter(page, site)
        propose_install(page)
        page.get_by_role("button", name="Plus tard").click()
        expect(page.locator("[data-install]")).to_be_hidden()
        for path in ("/app", "/"):
            page.goto(site.url + path)
            assert propose_install(page) is False                             # le site laisse alors faire le navigateur
            expect(page.locator("[data-install]")).to_be_hidden()

    @pytest.mark.parametrize("script", [
        "Object.defineProperty(navigator, 'standalone', { value: true })",                      # iPhone : ajouté à l'écran d'accueil
        """const reel = window.matchMedia.bind(window);
           window.matchMedia = query => query.includes('display-mode: standalone')
               ? { matches: true, media: query, addEventListener() {}, removeEventListener() {} } : reel(query);""",
    ], ids=["ios-ecran-d-accueil", "android-application-installee"])
    def test_une_application_deja_installee_ne_propose_pas_de_l_installer(self, make_page, site, script):
        page = make_page(**CHROME_ANDROID)
        page.add_init_script(script)
        enter(page, site)
        assert propose_install(page) is False
        expect(page.locator("[data-install]")).to_be_hidden()

    def test_sur_iphone_l_invitation_explique_le_geste_de_safari(self, make_page, site):
        page = make_page(**SAFARI_IPHONE)
        enter(page, site)
        box = page.locator("[data-install]")
        expect(box).to_be_visible()
        expect(box.locator("[data-install-ios]")).to_contain_text("Partager")
        expect(box.locator("[data-install-ios]")).to_contain_text("Sur l’écran d’accueil")
        expect(box.locator("[data-install-android]")).to_be_hidden()
        expect(box.locator("[data-install-button]")).to_be_hidden()           # Safari n'a pas de bouton d'installation
        page.get_by_role("button", name="Plus tard").click()
        expect(box).to_be_hidden()

    def test_dans_chrome_sur_iphone_l_invitation_ne_promet_rien(self, make_page, site):
        page = make_page(**CHROME_IPHONE)                                     # menu différent de Safari : rien à expliquer
        enter(page, site)
        expect(page.locator("[data-install]")).to_be_hidden()

    @pytest.mark.parametrize("path", ["/", "/login", "/app"])
    def test_l_invitation_ne_fait_pas_deborder_la_page_sur_telephone(self, make_page, site, path):
        page = make_page(**CHROME_ANDROID)
        if path == "/app":
            enter(page, site)
        else:
            page.goto(site.url + path)
        propose_install(page)
        expect(page.locator("[data-install]")).to_be_visible()
        assert no_horizontal_overflow(page)


# ======================================================================================================
# Site de paiement
# ======================================================================================================

def start_payment(page, pay_site, email, plan="monthly"):
    """Choisit l'offre, saisit l'e-mail, valide : le navigateur doit arriver chez « PayDunya » (simulé)."""
    page.goto(pay_site.url + f"/paiement?plan={plan}")
    page.fill("#email", email)
    page.check("#accept")                                             # la case des conditions est obligatoire
    page.click("#pay-btn")
    page.wait_for_url("**/paydunya-simule/**")
    return pay_site.pd.created[-1]["token"]


class TestPaiement:
    def test_le_recapitulatif_suit_l_offre_choisie(self, page, pay_site):
        page.goto(pay_site.url + "/paiement")
        expect(page.locator("input[value=monthly]")).to_be_checked()
        assert norm(page.locator("#sum-fcfa").inner_text()) == "6 000 FCFA"
        assert norm(page.locator("#sum-eur").inner_text()) == "≈ 9 €"
        page.click("label.choice:has(input[value=yearly])")
        assert norm(page.locator("#sum-name").inner_text()) == "Abonnement annuel"
        assert norm(page.locator("#sum-fcfa").inner_text()) == "39 400 FCFA"
        assert norm(page.locator("#sum-eur").inner_text()) == "≈ 60 €"
        page.click("label.choice:has(input[value=weekly])")
        assert norm(page.locator("#sum-name").inner_text()) == "Pass 7 jours"
        assert norm(page.locator("#sum-fcfa").inner_text()) == "2 000 FCFA"
        assert norm(page.locator("#sum-eur").inner_text()) == "≈ 3 €"

    def test_le_lien_d_essai_preselectionne_le_pass_de_7_jours(self, page, pay_site):
        page.goto(pay_site.url + "/paiement?plan=weekly")
        expect(page.locator("input[value=weekly]")).to_be_checked()
        assert norm(page.locator("#sum-name").inner_text()) == "Pass 7 jours"
        assert norm(page.locator("#sum-fcfa").inner_text()) == "2 000 FCFA"
        assert norm(page.locator("#sum-eur").inner_text()) == "≈ 3 €"

    def test_achat_du_pass_de_7_jours(self, page, pay_site):
        token = start_payment(page, pay_site, "Essai@Exemple.com", plan="weekly")
        invoice = pay_site.pd.created[-1]
        assert invoice["amount"] == config.PRICE_WEEKLY_FCFA == 2000
        assert invoice["name"] == "Triple Elite VIP - Pass 7 jours"
        pay_site.pd.pay(token)
        page.goto(pay_site.url + f"/succes?token={token}")
        expect(page.locator("h1")).to_have_text("Paiement confirmé")
        key = page.locator("#license-key").get_attribute("data-key")
        assert [(m["to"], m["key"], m["plan"]) for m in pay_site.mailer.sent] == [("essai@exemple.com", key, "Pass 7 jours")]
        assert pay_site.lm.check_login("essai@exemple.com", key)["ok"]
        left = pay_site.lm.get_status("essai@exemple.com")["expires"] - license_manager._utcnow()
        assert timedelta(days=6, hours=23) < left <= timedelta(days=7)

    def test_page_de_paiement_sans_avertissement_du_navigateur(self, page, pay_site):
        page.goto(pay_site.url + "/paiement")
        page.wait_for_timeout(4500)                                   # (voir le test équivalent du site client)

    def test_achat_d_un_nouvel_abonne(self, page, pay_site):
        token = start_payment(page, pay_site, "Nouveau.Client@Exemple.com", plan="yearly")
        invoice = pay_site.pd.created[-1]
        assert invoice["amount"] == config.PRICE_YEARLY_FCFA     # le montant vient du serveur, jamais du navigateur
        assert invoice["custom_data"]["email"] == "nouveau.client@exemple.com"

        # Le client est revenu de PayDunya, la confirmation n'est pas encore arrivée.
        page.goto(pay_site.url + f"/succes?token={token}")
        expect(page.locator("h1")).to_have_text("Confirmation en cours…")
        expect(page.locator("#license-key")).to_have_count(0)

        # Paiement confirmé : la clé s'affiche, une seule fois envoyée par e-mail.
        pay_site.pd.pay(token)
        page.goto(pay_site.url + f"/succes?token={token}")
        expect(page.locator("h1")).to_have_text("Paiement confirmé")
        key = page.locator("#license-key").get_attribute("data-key")
        assert re.fullmatch(r"[0-9a-f]{16}", key)
        expect(page.locator(".keybox__email")).to_have_text("nouveau.client@exemple.com")
        assert "".join(page.locator("#license-key").text_content().split()) == key     # (affichée en capitales)
        page.click("#copy-key")
        expect(page.locator("#copy-status")).to_have_text("Clé copiée.")
        assert page.evaluate("navigator.clipboard.readText()") == key

        page.reload()                                                 # recharger ne délivre pas une seconde licence
        expect(page.locator("#license-key")).to_have_attribute("data-key", key)
        assert [(m["to"], m["key"], m["plan"], m["renewed"]) for m in pay_site.mailer.sent] == [
            ("nouveau.client@exemple.com", key, "Annuel", False)]
        assert pay_site.lm.check_login("nouveau.client@exemple.com", key)["ok"]
        status = pay_site.lm.get_status("nouveau.client@exemple.com")
        assert status["state"] == "active"
        assert status["expires"] - license_manager._utcnow() > timedelta(days=364)

    def test_renouvellement_garde_la_cle_et_ne_la_reaffiche_pas(self, page, pay_site):
        email = "fidele@exemple.com"
        key = pay_site.lm.issue_license(email, 1)["key"]
        page.goto(pay_site.url + f"/paiement?email={quote(email, safe='')}&plan=monthly")
        expect(page.locator("#email")).to_have_value(email)           # le lien de renouvellement préremplit l'e-mail
        expect(page.locator("#accept")).not_to_be_checked()           # mais jamais la case des conditions
        page.check("#accept")
        page.click("#pay-btn")
        page.wait_for_url("**/paydunya-simule/**")
        token = pay_site.pd.created[-1]["token"]
        pay_site.pd.pay(token)
        page.goto(pay_site.url + f"/succes?token={token}")
        expect(page.locator("h1")).to_have_text("Abonnement prolongé")
        expect(page.locator("#license-key")).to_have_count(0)
        assert [(m["to"], m["key"], m["renewed"]) for m in pay_site.mailer.sent] == [(email, key, True)]
        status = pay_site.lm.get_status(email)
        assert status["expires"] - license_manager._utcnow() > timedelta(days=59)    # 30 jours restants + 30 achetés

    def test_paiement_annule(self, page, pay_site):
        token = start_payment(page, pay_site, "annule@exemple.com")
        pay_site.pd.cancel(token)
        page.goto(pay_site.url + f"/succes?token={token}")
        expect(page.locator("h1")).to_have_text("Paiement non abouti")
        expect(page.locator("a", has_text="Réessayer le paiement")).to_be_visible()
        expect(page.locator("#license-key")).to_have_count(0)
        assert pay_site.mailer.sent == []
        assert pay_site.lm.get_status("annule@exemple.com")["state"] == "unknown"

    def test_retour_apres_annulation_chez_paydunya(self, page, pay_site):
        page.goto(pay_site.url + "/paiement?annule=1&plan=yearly")
        expect(page.get_by_role("status")).to_contain_text("Paiement annulé")     # l'avis sur les pays n'a pas ce rôle
        expect(page.locator("#pay-methods")).to_be_visible()                       # il reste affiché à côté
        expect(page.locator("input[value=yearly]")).to_be_checked()

    def test_la_page_d_attente_se_met_a_jour_toute_seule(self, page, pay_site, monkeypatch):
        monkeypatch.setattr(config, "SUCCESS_REFRESH_SECONDS", 1)
        token = start_payment(page, pay_site, "patient@exemple.com")
        page.goto(pay_site.url + f"/succes?token={token}")
        expect(page.locator("h1")).to_have_text("Confirmation en cours…")
        pay_site.pd.pay(token)                                        # le client finit de payer, page toujours ouverte
        expect(page.locator("#license-key")).to_be_visible(timeout=15_000)
        expect(page.locator("h1")).to_have_text("Paiement confirmé")

    def test_la_page_d_attente_se_met_a_jour_aussi_sans_javascript(self, browser, pay_site, monkeypatch):
        monkeypatch.setattr(config, "SUCCESS_REFRESH_SECONDS", 1)
        # « reduced_motion » coupe le défilement doux de la page (scroll-behavior: smooth). Constaté : sans JavaScript et
        # dès qu'il doit défiler jusqu'à la case (fenêtre de 720 px), Playwright la juge « instable » et expire ; avec
        # JavaScript, ou sans défilement doux, tout va bien. Ce n'est pas un défaut du site, seulement de l'automatisation.
        context = browser.new_context(java_script_enabled=False, locale="fr-FR", reduced_motion="reduce")
        try:
            page = context.new_page()
            page.goto(pay_site.url + "/paiement")
            page.fill("#email", "sansjs@exemple.com")
            page.check("#accept")
            page.click("#pay-btn")                                    # sans JavaScript : simple envoi de formulaire
            page.wait_for_url("**/paydunya-simule/**")
            token = pay_site.pd.created[-1]["token"]
            page.goto(pay_site.url + f"/succes?token={token}")
            expect(page.locator("h1")).to_have_text("Confirmation en cours…")
            pay_site.pd.pay(token)
            expect(page.locator("#license-key")).to_be_visible(timeout=15_000)     # la balise « meta » a rechargé la page
        finally:
            context.close()

    def test_l_attente_ne_remplit_pas_l_historique_du_navigateur(self, page, pay_site, monkeypatch):
        monkeypatch.setattr(config, "SUCCESS_REFRESH_SECONDS", 1)
        token = start_payment(page, pay_site, "historique@exemple.com")
        page.goto(pay_site.url + f"/succes?token={token}")
        page.wait_for_url(re.compile(r"n=2"), timeout=15_000)         # deux actualisations successives...
        page.go_back()                                                # ...et un seul retour suffit pour quitter la page
        expect(page).to_have_url(re.compile(r"/paydunya-simule/"))

    def test_e_mail_invalide_garde_la_saisie_et_l_offre(self, page, pay_site):
        page.goto(pay_site.url + "/paiement?plan=yearly")
        page.evaluate("document.getElementById('pay-form').noValidate = true")   # laisse le serveur répondre
        page.fill("#email", "pas-un-email")
        page.click("#pay-btn")
        expect(page.locator(".notice--erreur")).to_contain_text("adresse e-mail")
        expect(page.locator("#email")).to_have_value("pas-un-email")
        expect(page.locator("input[value=yearly]")).to_be_checked()
        expect(page.locator("#pay-btn")).to_be_enabled()
        assert pay_site.pd.created == []

    def test_le_navigateur_bloque_l_envoi_tant_que_la_case_n_est_pas_cochee(self, page, pay_site):
        page.goto(pay_site.url + "/paiement")
        page.fill("#email", "pressee@exemple.com")
        page.click("#pay-btn")
        expect(page.locator("#accept")).to_be_focused()               # le navigateur désigne la case à cocher
        assert page.evaluate("document.getElementById('accept').validity.valueMissing")
        assert page.url.endswith("/paiement") and pay_site.pd.created == []
        page.check("#accept")
        page.click("#pay-btn")
        page.wait_for_url("**/paydunya-simule/**")

    def test_sans_la_case_le_serveur_refuse_aussi(self, page, pay_site):
        page.goto(pay_site.url + "/paiement")
        page.evaluate("document.getElementById('pay-form').noValidate = true")   # laisse le serveur répondre
        page.fill("#email", "pressee@exemple.com")
        page.click("#pay-btn")
        expect(page.locator(".notice--erreur")).to_contain_text("Coche la case")
        expect(page.locator("#accept")).not_to_be_checked()
        expect(page.locator("#email")).to_have_value("pressee@exemple.com")
        assert pay_site.pd.created == []

    def test_la_case_cochee_reste_cochee_apres_une_erreur(self, page, pay_site):
        page.goto(pay_site.url + "/paiement")
        page.evaluate("document.getElementById('pay-form').noValidate = true")
        page.fill("#email", "pas-un-email")
        page.check("#accept")
        page.click("#pay-btn")
        expect(page.locator(".notice--erreur")).to_contain_text("adresse e-mail")
        expect(page.locator("#accept")).to_be_checked()

    def test_les_conditions_s_ouvrent_dans_un_autre_onglet_sans_perdre_le_formulaire(self, page, pay_site):
        page.goto(pay_site.url + "/paiement")
        link = page.locator("label[for=accept] a")
        expect(link).to_have_attribute("target", "_blank")
        assert "noopener" in link.get_attribute("rel")
        assert link.get_attribute("href").endswith("/conditions")

    def test_paydunya_indisponible(self, page, pay_site):
        pay_site.pd.fail_create = PayDunyaError("PayDunya injoignable", transient=True)
        page.goto(pay_site.url + "/paiement")
        page.fill("#email", "client@exemple.com")
        page.check("#accept")
        page.click("#pay-btn")
        expect(page.locator(".notice--erreur")).to_contain_text("momentanément indisponible")
        expect(page.locator("#email")).to_have_value("client@exemple.com")
        expect(page.locator("#accept")).to_be_checked()               # elle l'avait cochée : inutile de recommencer
        assert "injoignable" not in page.content()                    # jamais de détail technique côté client
        assert pay_site.lm.get_status("client@exemple.com")["state"] == "unknown"

    def test_double_clic_sur_payer_ne_cree_qu_une_facture(self, page, pay_site):
        page.goto(pay_site.url + "/paiement")
        page.fill("#email", "presse@exemple.com")
        page.check("#accept")
        page.dblclick("#pay-btn")
        page.wait_for_url("**/paydunya-simule/**")
        assert len(pay_site.pd.created) == 1

    def test_reference_inconnue(self, page, pay_site):
        response = page.goto(pay_site.url + "/succes?token=test_inconnu0001")
        assert response.status == 404
        expect(page.locator("h1")).to_have_text("Commande introuvable")

    def test_succes_sans_reference(self, page, pay_site):
        page.goto(pay_site.url + "/succes")
        expect(page.locator("h1")).to_have_text("Ton paiement")


@pytest.mark.parametrize("path", ["/paiement", "/paiement?plan=yearly", "/succes"])
def test_pages_de_paiement_sans_debordement_sur_telephone(phone_page, pay_site, path):
    phone_page.goto(pay_site.url + path)
    assert no_horizontal_overflow(phone_page)


def test_page_de_confirmation_sans_debordement_sur_telephone(phone_page, pay_site):
    token = start_payment(phone_page, pay_site, "mobile@exemple.com")
    pay_site.pd.pay(token)
    phone_page.goto(pay_site.url + f"/succes?token={token}")
    expect(phone_page.locator("#license-key")).to_be_visible()
    assert no_horizontal_overflow(phone_page)
    box = phone_page.locator("#license-key").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390                    # la clé tient dans l'écran
