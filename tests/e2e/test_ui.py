"""Parcours réels dans Chromium : les vrais gabarits, le vrai JavaScript et la vraie logique des deux sites ;
seuls les services extérieurs (IA, PayDunya, e-mail, base de licences du site client) sont simulés.

    python -m pytest tests/e2e -q

Ce que ces tests attrapent et que les tests Flask ne voient pas : un script bloqué par la politique de sécurité,
un formulaire refusé par le navigateur (en-têtes Origin/Referer), une page qui déborde sur téléphone,
un bouton qui ne réagit pas, une erreur JavaScript.
"""
import re
from datetime import timedelta
from urllib.parse import quote

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright n'est pas installé")
from playwright.sync_api import expect  # noqa: E402

import config  # noqa: E402
import license_manager  # noqa: E402
from paydunya import PayDunyaError  # noqa: E402
from pipeline import GenerationError  # noqa: E402
from site_fakes import EMAIL, KEY, make_pipeline  # noqa: E402

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
        assert "30 €" in text and "60 €" in text
        assert "34 %" in text                                         # l'avertissement honnête sur les combinés
        assert page.locator("a[href$='/paiement?plan=yearly']").count() >= 1

    @pytest.mark.parametrize("path, status", [("/conditions", 200), ("/login", 200), ("/n-existe-pas", 404)])
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


@pytest.mark.parametrize("path", ["/", "/login", "/conditions"])
def test_pages_publiques_sans_debordement_sur_telephone(phone_page, site, path):
    phone_page.goto(site.url + path)
    assert no_horizontal_overflow(phone_page)


def test_espace_client_sans_debordement_sur_telephone(phone_page, site):
    log_in_and_generate(phone_page, site)
    assert no_horizontal_overflow(phone_page)
    phone_page.click("#tab-history")
    expect(phone_page.locator("#history-content")).to_be_visible()
    assert no_horizontal_overflow(phone_page)


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
        assert norm(page.locator("#sum-eur").inner_text()) == "30 €"
        assert norm(page.locator("#sum-fcfa").inner_text()) == "19 700 FCFA"
        page.click("label.choice:has(input[value=yearly])")
        assert norm(page.locator("#sum-name").inner_text()) == "Abonnement annuel"
        assert norm(page.locator("#sum-eur").inner_text()) == "60 €"
        assert norm(page.locator("#sum-fcfa").inner_text()) == "39 400 FCFA"

    def test_page_de_paiement_sans_avertissement_du_navigateur(self, page, pay_site):
        page.goto(pay_site.url + "/paiement")
        page.wait_for_timeout(4500)                                   # (voir le test équivalent du site client)

    def test_achat_d_un_nouvel_abonne(self, page, pay_site):
        token = start_payment(page, pay_site, "Nouveau.Client@Exemple.com", plan="yearly")
        invoice = pay_site.pd.created[-1]
        assert invoice["amount"] == config.PRICE_YEARLY_FACTURE_FCFA     # le montant vient du serveur, jamais du navigateur
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
        expect(page.locator(".notice--info")).to_contain_text("Paiement annulé")
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
        context = browser.new_context(java_script_enabled=False, locale="fr-FR")
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
