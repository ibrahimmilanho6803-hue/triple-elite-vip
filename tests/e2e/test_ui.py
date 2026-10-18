"""Parcours réels dans Chromium : les vrais gabarits, le vrai JavaScript et la vraie logique des deux sites ;
seuls les services extérieurs (IA, PayDunya, e-mail, base de licences du site client) sont simulés.

    python -m pytest tests/e2e -q

Ce que ces tests attrapent et que les tests Flask ne voient pas : un script bloqué par la politique de sécurité,
un formulaire refusé par le navigateur (en-têtes Origin/Referer), une page qui déborde sur téléphone,
un bouton qui ne réagit pas, une erreur JavaScript.
"""
import os
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
from helpers import join_jobs  # noqa: E402
from paydunya import PayDunyaError  # noqa: E402
from pipeline import GenerationError, NoMatchToday  # noqa: E402
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
    """La génération automatique du jour a eu lieu : le combiné gratuit existe (sans cela, /gratuit est vide)."""
    site.service.request_generation()
    join_jobs(site.service, 15)


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
        assert "15 matchs analysés dans 5 compétitions" in norm(page.locator("#results-meta").inner_text())
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
        assert site.pipeline.state["calls"] == 1                      # ses combinés lui sont réaffichés sans refaire l'analyse

    def test_un_autre_abonne_ne_voit_pas_mes_combines_et_genere_les_siens(self, page, site, context):
        other_email, other_key = "autre@exemple.com", "fedcba9876543210"
        site.licenses.records[other_email] = {"key": other_key, "active": True,
                                              "expires": license_manager._utcnow() + timedelta(days=5)}
        log_in_and_generate(page, site)
        expect(page.locator("#results-grid .slip")).to_have_count(3)
        assert site.pipeline.state["calls"] == 1
        other_context = context.browser.new_context(locale="fr-FR", timezone_id="Europe/Paris",
                                                    viewport={"width": 1280, "height": 900})
        try:
            other = other_context.new_page()
            other.set_default_timeout(15_000)
            log_in(other, site, other_email, other_key)
            other.wait_for_url("**/app")
            other.wait_for_load_state("networkidle")
            expect(other.locator("#btn-generate")).to_be_enabled()            # rien d'autre que le bouton
            expect(other.locator("#results")).to_be_hidden()
            assert site.pipeline.state["calls"] == 1
            other.click("#btn-generate")
            expect(other.locator("#results")).to_be_visible()
            expect(other.locator("#results-grid .slip")).to_have_count(3)
            assert site.pipeline.state["calls"] == 2                          # son propre travail
        finally:
            other_context.close()
        page.reload()                                                         # le premier retrouve les siens, sans nouveau travail
        expect(page.locator("#results-grid .slip")).to_have_count(3)
        assert site.pipeline.state["calls"] == 2

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


def forget_combos(site):
    """Les combinés générés ne sont plus d'actualité (cache écoulé) : la prochaine demande relance une analyse."""
    scope = site.service.scope_of(EMAIL)
    for name in (f"last_generation_{scope}.json", f"job_{scope}.json"):
        site.service._mem.pop(name, None)
        path = os.path.join(site.service.cache_dir, name)
        if os.path.exists(path):
            os.remove(path)


def used_today(site):
    return site.licenses.usage.get((EMAIL, site.service.today()), 0)


class TestGenerationsDuJourEtJourSansChampionnat:
    """Chaque client a deux générations par jour ; un jour sans championnat, le site répond « Pas de championnat
    aujourd'hui » et ne compte rien."""

    def test_l_espace_annonce_les_generations_du_jour_et_les_matchs_du_jour(self, page, site):
        enter(page, site)
        assert "matchs du jour" in norm(page.locator("#generate-intro").inner_text())
        assert "jusqu’à trois combinés" in norm(page.locator("#generate-intro").inner_text())
        expect(page.locator("#generate-quota")).to_be_visible()
        assert norm(page.locator("#generate-quota").inner_text()) == "Il te reste 2 générations aujourd’hui (2 par jour)."
        expect(page.locator("#btn-generate")).to_be_enabled()
        expect(page.locator("#no-match")).to_be_hidden()

    def test_il_reste_une_generation_au_singulier(self, page, site):
        site.licenses.usage[(EMAIL, site.service.today())] = 1
        enter(page, site)
        assert norm(page.locator("#generate-quota").inner_text()) == "Il te reste 1 génération aujourd’hui (2 par jour)."

    def test_la_generation_est_comptee_puis_la_deuxieme_aussi_et_la_troisieme_est_refusee(self, page, site):
        enter(page, site)
        page.click("#btn-generate")
        expect(page.locator("#results")).to_be_visible()
        assert used_today(site) == 1
        forget_combos(site)
        page.reload()
        expect(page.locator("#generate")).to_be_visible()
        assert norm(page.locator("#generate-quota").inner_text()) == "Il te reste 1 génération aujourd’hui (2 par jour)."
        page.click("#btn-generate")
        expect(page.locator("#results")).to_be_visible()
        assert used_today(site) == 2 and site.pipeline.state["calls"] == 2
        forget_combos(site)
        page.reload()
        # Plus de génération : le bouton est grisé et le message dit quand revenir. Aucune analyse ne part.
        expect(page.locator("#notice .notice--info")).to_contain_text("Tu as utilisé tes 2 générations d’aujourd’hui")
        expect(page.locator("#notice")).to_contain_text("Reviens demain pour en lancer de nouvelles")
        expect(page.locator("#btn-generate")).to_be_disabled()
        expect(page.locator("#generate-quota")).to_be_hidden()
        expect(page.locator("#results")).to_be_hidden()
        assert site.pipeline.state["calls"] == 2 and used_today(site) == 2

    def test_une_page_restee_ouverte_apprend_que_la_limite_est_atteinte_au_clic(self, page, site):
        enter(page, site)                                              # la page affiche un bouton actif...
        site.licenses.usage[(EMAIL, site.service.today())] = 2         # ...mais les deux générations ont été utilisées ailleurs
        page.click("#btn-generate")
        expect(page.locator("#notice .notice--info")).to_contain_text("Tu as utilisé tes 2 générations d’aujourd’hui")
        expect(page.locator("#btn-generate")).to_be_disabled()
        expect(page.locator("#progress")).to_be_hidden()
        assert site.pipeline.state["calls"] == 0

    def test_une_analyse_en_echec_ne_coute_rien_au_client(self, page, start_site):
        site = start_site(pipeline=make_pipeline(delay=0.05, error=GenerationError("L'IA ne répond pas. Réessaie plus tard.")))
        enter(page, site)
        page.click("#btn-generate")
        expect(page.locator("#notice .notice--erreur")).to_contain_text("L’IA ne répond pas")
        expect(page.locator("#btn-generate")).to_have_text("Réessayer")
        assert norm(page.locator("#generate-quota").inner_text()) == "Il te reste 2 générations aujourd’hui (2 par jour)."
        assert used_today(site) == 0

    def test_un_jour_sans_championnat_l_espace_le_dit_des_l_ouverture(self, page, site):
        site.service._note_matchday(0, 0)                              # le constat que fait la boucle quotidienne, dès minuit
        enter(page, site)
        expect(page.locator("#no-match")).to_be_visible()
        expect(page.locator("#no-match-title")).to_have_text("Pas de championnat aujourd’hui")
        assert "Reviens demain" in norm(page.locator("#no-match-text").inner_text())
        expect(page.locator("#generate")).to_be_hidden()
        expect(page.locator("#btn-generate")).to_be_hidden()
        expect(page.locator("#results")).to_be_hidden()
        expect(page.locator("#progress")).to_be_hidden()
        assert site.pipeline.state["calls"] == 0 and used_today(site) == 0

    def test_un_clic_un_jour_sans_championnat_donne_le_message_et_ne_compte_rien(self, page, start_site):
        site = start_site(pipeline=make_pipeline(delay=0.05, error=NoMatchToday(0, 0, "2026-10-11")))
        enter(page, site)
        page.click("#btn-generate")
        expect(page.locator("#no-match-title")).to_have_text("Pas de championnat aujourd’hui")
        expect(page.locator("#no-match")).to_be_visible()
        expect(page.locator("#progress")).to_be_hidden()
        expect(page.locator("#notice .notice")).to_have_count(0)       # pas d'erreur rouge : ce n'est pas une panne
        assert used_today(site) == 0 and site.service.generations_today() == 0
        page.reload()                                                  # le constat est gardé : plus d'analyse du tout
        expect(page.locator("#no-match-title")).to_have_text("Pas de championnat aujourd’hui")
        assert site.pipeline.state["calls"] == 1

    def test_trop_peu_de_matchs_et_matchs_commences_ont_leur_propre_message(self, page, site):
        site.service._note_matchday(8, 0)
        enter(page, site)
        expect(page.locator("#no-match-title")).to_have_text("Les matchs d’aujourd’hui ont déjà commencé")
        site.service._note_matchday(8, 2)
        page.reload()
        expect(page.locator("#no-match-title")).to_have_text("Trop peu de matchs aujourd’hui")

    def test_l_onglet_historique_reste_disponible_un_jour_sans_championnat(self, page, site):
        site.service._note_matchday(0, 0)
        enter(page, site)
        page.click("#tab-history")
        expect(page.locator("#history-content")).to_be_visible()
        page.click("#tab-combos")
        expect(page.locator("#no-match")).to_be_visible()

    def test_la_page_gratuite_dit_pas_de_championnat_aujourd_hui(self, page, site):
        site.service._note_matchday(0, 0)
        page.goto(site.url + "/gratuit")
        expect(page.locator(".empty__title")).to_have_text("Pas de championnat aujourd’hui")
        expect(page.locator(".empty")).to_have_attribute("data-no-match", "none")
        expect(page.locator(".free")).to_have_count(0)                 # aucun combiné, aucun partage
        expect(page.locator("a[href^='https://wa.me/']")).to_have_count(0)
        page.get_by_role("link", name="Voir les résultats").first.click()
        expect(page).to_have_url(re.compile(r"/resultats$"))
        assert site.pipeline.state["calls"] == 0

    @pytest.mark.parametrize("path", ["/gratuit", "/app"])
    def test_le_message_tient_dans_l_ecran_d_un_telephone(self, phone_page, site, path):
        site.service._note_matchday(0, 0)
        if path == "/app":
            enter(phone_page, site)
            expect(phone_page.locator("#no-match")).to_be_visible()
        else:
            phone_page.goto(site.url + path)
            expect(phone_page.locator(".empty__title")).to_be_visible()
        assert no_horizontal_overflow(phone_page)


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
CRAIE = "rgb(165, 173, 208)"                                            # --craie : texte d'un bouton qui n'est pas la page affichée
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
# Rapidité des boutons de la barre : réponse au toucher, pages voisines préchargées, service worker qui ne retarde rien
# ======================================================================================================

def slow_start(inner, environ, start_response, milliseconds):
    """Réponse de /sw.js précédée d'une attente de `milliseconds` : le service worker met ce temps à démarrer, comme sur un
    téléphone modeste (sur le site réel, mesuré à plus de 300 ms sur un ordinateur)."""
    captured = {}

    def capture(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = [(name, value) for name, value in headers if name.lower() != "content-length"]
        return lambda data: None

    body = b"".join(inner(environ, capture))
    body = f"var __t = Date.now(); while (Date.now() - __t < {milliseconds}) {{}}\n".encode() + body
    start_response(captured["status"], captured["headers"] + [("Content-Length", str(len(body)))])
    return [body]


class Journal:
    """Ce que reçoit le site, page par page (chemin, « Sec-Purpose » du préchargement, en-tête du préchargement de navigation),
    avec un réseau simulé : `delays` = {chemin: secondes d'attente avant de répondre} ; `sw_boot_ms` : démarrage lent du service
    worker. Les fichiers statiques ne sont pas comptés."""

    def __init__(self, site, delays=None, sw_boot_ms=0):
        self.requests, self.delays = [], delays or {}
        inner = site.app.wsgi_app

        def serve(environ, start_response):
            path = environ.get("PATH_INFO", "")
            if path == "/sw.js" and sw_boot_ms:
                return slow_start(inner, environ, start_response, sw_boot_ms)
            if not path.startswith("/static/"):
                self.requests.append({"path": path, "purpose": environ.get("HTTP_SEC_PURPOSE", ""),
                                      "preload": environ.get("HTTP_SERVICE_WORKER_NAVIGATION_PRELOAD", "")})
                time.sleep(self.delays.get(path, 0))
            return inner(environ, start_response)

        site.app.wsgi_app = serve

    def paths(self, start=0, purpose=None):
        return [r["path"] for r in self.requests[start:] if purpose is None or r["purpose"] == purpose]

    def prefetched(self):
        return sorted(set(self.paths(purpose="prefetch")))


def wait_for_prefetch(page, journal, expected, seconds=10):
    """Attend que le navigateur ait demandé en tâche de fond les pages `expected`, puis qu'il ait fini de les recevoir."""
    deadline = time.monotonic() + seconds
    while journal.prefetched() != sorted(expected):
        assert time.monotonic() < deadline, f"Délai dépassé : préchargement de {expected} (reçu : {journal.prefetched()})"
        page.wait_for_timeout(100)
    page.wait_for_timeout(400)


def bouton(page, name):
    return page.locator("nav.tabbar").get_by_role("link", name=name)


# Playwright n'exécute plus rien dans une page dont la navigation est en cours : pour voir ce que montre la barre PENDANT
# l'attente d'une vraie page, ce script (ajouté à toutes les pages) note l'état à chaque changement et le garde dans
# sessionStorage, que la page suivante rend lisible.
ENREGISTREUR_DE_LA_BARRE = """
addEventListener('click', () => sessionStorage.setItem('__clic', String(performance.now())), true);
addEventListener('DOMContentLoaded', () => {
  const bar = document.querySelector('nav.tabbar');
  if (!bar) { return; }
  const journal = [];
  new MutationObserver(() => {
    const touched = bar.querySelector('.is-pending'), old = bar.querySelector('[aria-current=page]');
    journal.push({ at: performance.now(), bar: bar.className, touched: touched && touched.dataset.tab,
                   touchedColor: touched && getComputedStyle(touched).color,
                   oldColor: old && getComputedStyle(old).color, oldMark: old && getComputedStyle(old, '::before').display });
    sessionStorage.setItem('__barre', JSON.stringify(journal));
  }).observe(bar, { attributes: true, subtree: true, attributeFilter: ['class'] });
  addEventListener('pagehide', () => sessionStorage.setItem('__quitte', String(performance.now())));
});
"""

# Empêche la navigation d'un bouton de la barre APRÈS que le script de la barre a réagi (il s'exécute avant, en remontant
# l'arbre) : la page reste là, ce qui permet de lire son état sans attendre une navigation.
SANS_NAVIGATION = """document.addEventListener('click', e => { if (e.target.closest('a.tabbar__link')) { e.preventDefault(); } })"""


def with_connection(effective_type, save_data=False):
    """Script qui fait croire à la page qu'elle est sur une connexion de ce type (Chromium sans écran en annonce toujours une bonne)."""
    return ("Object.defineProperty(navigator, 'connection', { configurable: true, value: "
            f"{{ effectiveType: '{effective_type}', saveData: {str(save_data).lower()} }} }})")


class TestRapiditeDesBoutons:
    # ---------------------------------------------------------------- réponse au toucher
    def test_le_bouton_touche_prend_tout_de_suite_l_aspect_de_la_page_affichee(self, phone_page, site, monkeypatch):
        monkeypatch.setattr(config, "PUBLIC_PAGE_CACHE_SECONDS", 0)               # rien de préchargé : la page est vraiment attendue
        Journal(site, delays={"/resultats": 1.5})
        phone_page.add_init_script(ENREGISTREUR_DE_LA_BARRE)
        phone_page.goto(site.url + "/")
        bouton(phone_page, "Résultats combinés").click(no_wait_after=True)
        expect(phone_page).to_have_url(site.url + "/resultats")                   # 1,5 s plus tard
        expect(phone_page.locator("nav.tabbar a[aria-current=page]")).to_have_text("Résultats combinés")
        expect(phone_page.locator("nav.tabbar")).not_to_have_class(re.compile("tabbar--pending"))     # page neuve : état d'origine
        seen = phone_page.evaluate("""() => ({ log: JSON.parse(sessionStorage.getItem('__barre')),
            click: Number(sessionStorage.getItem('__clic')), left: Number(sessionStorage.getItem('__quitte')) })""")
        first = seen["log"][0]
        assert first["touched"] == "resultats" and "tabbar--pending" in first["bar"]
        assert first["at"] - seen["click"] < 150, seen                             # tout de suite, pas après la réponse du serveur
        assert seen["left"] - first["at"] > 1200, seen                             # et l'aspect est tenu pendant toute l'attente
        assert first["touchedColor"] == DORE and first["oldColor"] == CRAIE and first["oldMark"] == "none"  # le doré a changé de bouton, sans fondu

    def test_le_bouton_reagit_des_que_le_doigt_se_pose(self, phone_page, site):
        phone_page.goto(site.url + "/")
        link = bouton(phone_page, "Résultats combinés")
        before = link.evaluate("e => getComputedStyle(e).backgroundColor")
        box = link.bounding_box()
        phone_page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 20)
        phone_page.mouse.down()
        pressed = link.evaluate("e => getComputedStyle(e).backgroundColor")                        # aussitôt, sans fondu
        phone_page.wait_for_timeout(500)
        settled = link.evaluate("e => getComputedStyle(e).backgroundColor")
        phone_page.mouse.move(5, 100)                                                              # le doigt glisse ailleurs : pas de clic
        phone_page.mouse.up()
        assert pressed == settled != before, (before, pressed, settled)

    def test_un_clic_avec_une_touche_enfoncee_laisse_la_barre_telle_quelle(self, page, site):
        page.goto(site.url + "/")
        link = bouton(page, "Résultats combinés")
        link.click(modifiers=["Control"], no_wait_after=True)               # ouvre un autre onglet : cette page ne bouge pas
        page.wait_for_timeout(300)
        expect(link).not_to_have_class(re.compile("is-pending"))
        expect(page.locator("nav.tabbar")).not_to_have_class(re.compile("tabbar--pending"))
        expect(page).to_have_url(site.url + "/")

    @pytest.mark.parametrize("how", ["delai", "retour-en-arriere", "application-cachee"])
    def test_le_bouton_retrouve_son_aspect_quand_la_page_ne_vient_pas(self, phone_page, site, how):
        phone_page.clock.install()
        phone_page.goto(site.url + "/")
        phone_page.evaluate(SANS_NAVIGATION)                                       # la page « n'arrive jamais »
        link, home = bouton(phone_page, "Résultats combinés"), bouton(phone_page, "Accueil")
        link.click()
        expect(link).to_have_class(re.compile("is-pending"))
        assert home.evaluate("e => getComputedStyle(e).color") == CRAIE            # l'ancien bouton s'éteint aussitôt, sans fondu
        if how == "delai":
            phone_page.clock.run_for(9000)
            expect(link).to_have_class(re.compile("is-pending"))                   # 9 s : on attend encore
            phone_page.clock.run_for(1500)
        elif how == "retour-en-arriere":                                          # page rendue telle quelle par le navigateur (bfcache)
            phone_page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))")
        else:                                                                     # autre application ouverte par-dessus (Android)
            phone_page.evaluate("""Object.defineProperty(document, 'hidden', { configurable: true, value: true });
                                   document.dispatchEvent(new Event('visibilitychange'))""")
        expect(link).not_to_have_class(re.compile("is-pending"))
        expect(phone_page.locator("nav.tabbar")).not_to_have_class(re.compile("tabbar--pending"))
        expect(home).to_have_css("color", DORE)                                    # la page affichée reprend sa place (fondu de 0,15 s)

    def test_un_nouveau_toucher_remplace_le_precedent(self, phone_page, site):
        phone_page.goto(site.url + "/")
        phone_page.evaluate(SANS_NAVIGATION)
        bouton(phone_page, "Résultats combinés").click()
        bouton(phone_page, "Accès VIP").click()
        expect(phone_page.locator("nav.tabbar .is-pending")).to_have_count(1)
        expect(bouton(phone_page, "Accès VIP")).to_have_class(re.compile("is-pending"))

    # ---------------------------------------------------------------- pages voisines préchargées
    def test_les_pages_voisines_sont_preparees_puis_s_ouvrent_sans_aller_au_reseau(self, phone_page, site):
        make_free_pick(site)
        journal = Journal(site)
        phone_page.goto(site.url + "/")
        # Les deux autres pages publiques, et elles seules : ni la page affichée, ni la connexion, ni le site de paiement.
        wait_for_prefetch(phone_page, journal, ["/gratuit", "/resultats"])
        assert journal.paths(purpose="prefetch").count("/gratuit") == journal.paths(purpose="prefetch").count("/resultats") == 1
        assert "/login" not in journal.paths() and "/app" not in journal.paths()
        mark = len(journal.requests)
        bouton(phone_page, "Résultats combinés").click()
        expect(phone_page.locator("h1")).to_contain_text(TITRES["/resultats"])
        expect(phone_page.locator("nav.tabbar a[aria-current=page]")).to_have_text("Résultats combinés")
        assert "/resultats" not in journal.paths(mark), journal.paths(mark)          # servie par le navigateur, pas par le site

    def test_la_page_preparee_est_redemandee_quand_le_delai_est_passe(self, phone_page, site, monkeypatch):
        monkeypatch.setattr(config, "PUBLIC_PAGE_CACHE_SECONDS", 1)
        journal = Journal(site)
        phone_page.goto(site.url + "/")
        wait_for_prefetch(phone_page, journal, ["/gratuit", "/resultats"])
        phone_page.wait_for_timeout(2300)                                         # la copie a plus d'une seconde : périmée
        mark = len(journal.requests)
        bouton(phone_page, "Résultats combinés").click()
        expect(phone_page.locator("h1")).to_contain_text(TITRES["/resultats"])
        assert journal.paths(mark).count("/resultats") == 1                       # une vraie demande, donc une page à jour

    @pytest.mark.parametrize("effective_type, save_data, prefetched", [
        ("4g", False, True), ("3g", False, True), ("2g", False, False), ("slow-2g", False, False), ("4g", True, False)])
    def test_le_prechargement_respecte_la_connexion_de_la_personne(self, make_page, site, effective_type, save_data, prefetched):
        make_free_pick(site)
        journal = Journal(site)
        page = make_page(**CHROME_ANDROID)
        page.add_init_script(with_connection(effective_type, save_data))
        page.goto(site.url + "/")
        if prefetched:
            wait_for_prefetch(page, journal, ["/gratuit", "/resultats"])
        else:
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)
            assert journal.prefetched() == []                                       # économie de données ou 2G : aucun transfert en plus

    def test_sans_information_sur_la_connexion_rien_n_est_precharge(self, make_page, site):
        # Safari et Firefox ne savent pas réutiliser une page préchargée : du transfert pour rien.
        journal = Journal(site)
        page = make_page(**CHROME_ANDROID)
        page.add_init_script("Object.defineProperty(navigator, 'connection', { configurable: true, value: undefined })")
        page.goto(site.url + "/")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
        assert journal.prefetched() == []
        expect(bouton(page, "Résultats combinés")).to_be_visible()                  # et la barre fonctionne comme d'habitude

    def test_la_duree_zero_coupe_le_prechargement_et_la_memorisation(self, phone_page, site, monkeypatch):
        monkeypatch.setattr(config, "PUBLIC_PAGE_CACHE_SECONDS", 0)
        journal = Journal(site)
        phone_page.goto(site.url + "/")
        phone_page.wait_for_load_state("networkidle")
        phone_page.wait_for_timeout(1500)
        assert journal.prefetched() == []
        bouton(phone_page, "Résultats combinés").click()
        expect(phone_page.locator("h1")).to_contain_text(TITRES["/resultats"])
        assert journal.paths().count("/resultats") == 1

    def test_une_page_voisine_en_panne_n_est_pas_gardee(self, phone_page, start_site):
        site = start_site(history_error=RuntimeError("base de résultats illisible"))
        journal = Journal(site)
        phone_page.goto(site.url + "/")
        wait_for_prefetch(phone_page, journal, ["/gratuit", "/resultats"])           # /resultats répond 503 : sans effet sur l'accueil
        expect(phone_page.locator("h1")).to_contain_text(TITRES["/"])
        mark = len(journal.requests)
        bouton(phone_page, "Résultats combinés").click()
        expect(phone_page.locator("body")).to_contain_text("momentanément indisponibles")
        # La page d'erreur (aucun bouton doré) prépare à son tour les deux autres pages publiques, jamais elle-même.
        wait_for(phone_page, "document.querySelectorAll('head link[rel=prefetch]').length > 0", "la page d'erreur prépare ses voisines")
        hints = phone_page.eval_on_selector_all("head link[rel=prefetch]", "els => els.map(e => new URL(e.href).pathname)")
        assert sorted(hints) == ["/", "/gratuit"], hints
        assert journal.paths(mark).count("/resultats") == 1                          # l'erreur n'a pas été gardée : vraie demande, une seule

    # ---------------------------------------------------------------- « Accès VIP » droit sur l'espace
    def test_acces_vip_va_droit_a_l_espace_apres_une_visite_de_l_espace_puis_retrouve_la_connexion(self, page, site):
        journal = Journal(site)
        enter(page, site)                                                          # l'espace s'affiche : l'appareil le retient
        assert page.evaluate("localStorage.getItem('tev-espace')") == "1"
        page.goto(site.url + "/")
        vip = bouton(page, "Accès VIP")
        expect(vip).to_have_attribute("href", "/app")
        mark = len(journal.requests)
        vip.click()
        expect(page).to_have_url(site.url + "/app")
        expect(page.locator("#app")).to_contain_text("Abonnement actif jusqu’au")
        assert "/login" not in journal.paths(mark), journal.paths(mark)             # pas de détour par la page de connexion
        page.click("text=Déconnexion")
        expect(page).to_have_url(re.compile(r"/login\?raison=deconnecte$"))
        assert page.evaluate("localStorage.getItem('tev-espace')") is None          # la page de connexion efface le repère
        page.goto(site.url + "/")
        expect(bouton(page, "Accès VIP")).to_have_attribute("href", "/login")

    def test_un_repere_perime_ne_trompe_personne(self, page, site):
        page.goto(site.url + "/login")
        page.evaluate("localStorage.setItem('tev-espace', '1')")                    # repère resté d'une session finie
        page.goto(site.url + "/")
        vip = bouton(page, "Accès VIP")
        expect(vip).to_have_attribute("href", "/app")
        vip.click()
        expect(page).to_have_url(re.compile(r"/login\?raison=session$"))            # /app renvoie à la connexion, comme avant
        expect(page.locator(".notice")).to_contain_text("session a expiré")
        assert page.evaluate("localStorage.getItem('tev-espace')") is None          # et le repère disparaît
        page.goto(site.url + "/")
        expect(bouton(page, "Accès VIP")).to_have_attribute("href", "/login")

    def test_sans_stockage_le_bouton_acces_vip_reste_un_simple_lien(self, make_page, site):
        page = make_page(**CHROME_ANDROID)
        page.add_init_script("""Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new Error('bloqué'); } })""")
        page.goto(site.url + "/")
        expect(bouton(page, "Accès VIP")).to_have_attribute("href", "/login")
        bouton(page, "Accès VIP").click()
        expect(page).to_have_url(site.url + "/login")

    # ---------------------------------------------------------------- site de paiement
    def test_le_site_de_paiement_a_le_meme_retour_au_toucher_sans_rien_precharger(self, phone_page, pay_site):
        phone_page.goto(pay_site.url + "/paiement")
        phone_page.wait_for_load_state("networkidle")
        phone_page.wait_for_timeout(1000)
        expect(phone_page.locator("nav.tabbar a[data-prefetch]")).to_have_count(0)
        phone_page.evaluate(SANS_NAVIGATION)                                       # (les pages de l'autre site ne sont pas appelées)
        link = bouton(phone_page, "Accueil")
        link.click()
        expect(link).to_have_class(re.compile("is-pending"))
        assert link.evaluate("e => getComputedStyle(e).color") == DORE
        assert bouton(phone_page, "Abonnement VIP").evaluate("e => getComputedStyle(e).color") == CRAIE          # l'ancien s'éteint


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
# Le service worker ne doit jamais retarder l'ouverture d'une page
# ======================================================================================================

def stop_service_workers(page):
    """Arrête le service worker, comme le navigateur le fait après quelques secondes d'inactivité : le toucher suivant
    doit le redémarrer (de 0,1 s à plus d'une seconde sur un téléphone modeste)."""
    cdp = page.context.new_cdp_session(page)
    cdp.send("ServiceWorker.enable")
    cdp.send("ServiceWorker.stopAllWorkers")
    page.wait_for_timeout(200)


# Note l'instant du toucher (page quittée), puis, sur la page suivante, les instants d'arrivée de la réponse et de la page prête.
ENREGISTREUR_DU_TOUCHER = "addEventListener('click', () => sessionStorage.setItem('__clic', String(Date.now())), true)"
DELAIS_DE_LA_PAGE = """() => {
    const nav = performance.getEntriesByType('navigation')[0], origin = performance.timeOrigin;
    return { clic: Number(sessionStorage.getItem('__clic')), reponse: origin + nav.responseStart,
             prete: origin + nav.domContentLoadedEventEnd };
}"""
DEMARRAGE_LENT_MS = 2000                 # le service worker met 2 s à démarrer
SEUIL_MS = 1200                          # une page qui s'ouvre sans l'attendre arrive bien avant


class TestServiceWorkerSansRetard:
    def test_le_service_worker_prepare_la_page_en_meme_temps_qu_il_demarre(self, page, site):
        page.goto(site.url + "/")
        wait_for_service_worker(page)
        state = page.evaluate("navigator.serviceWorker.ready.then(registration => registration.navigationPreload.getState())")
        assert state["enabled"] is True, state

    @pytest.mark.parametrize("name, path, hits", [("Résultats combinés", "/resultats", 0), ("Accès VIP", "/login", 1)],
                             ids=["page-preparee", "page-demandee"])
    def test_un_service_worker_arrete_et_lent_a_demarrer_ne_retarde_pas_la_page(self, phone_page, site, name, path, hits):
        journal = Journal(site, delays={"/login": 0.15}, sw_boot_ms=DEMARRAGE_LENT_MS)
        phone_page.add_init_script(ENREGISTREUR_DU_TOUCHER)
        phone_page.goto(site.url + "/")
        wait_for_service_worker(phone_page)
        wait_for_prefetch(phone_page, journal, ["/gratuit", "/resultats"])
        stop_service_workers(phone_page)
        mark = len(journal.requests)
        bouton(phone_page, name).click(no_wait_after=True)
        expect(phone_page).to_have_url(site.url + path)
        phone_page.wait_for_load_state("domcontentloaded")
        times = phone_page.evaluate(DELAIS_DE_LA_PAGE)
        assert times["prete"] - times["clic"] < SEUIL_MS, times               # sans le réglage : plus de 2 000 ms
        assert journal.paths(mark).count(path) == hits, journal.paths(mark)   # et une seule demande au site, jamais deux

    def test_un_service_worker_en_marche_ne_demande_la_page_qu_une_fois(self, phone_page, site):
        journal = Journal(site, delays={"/login": 0.15})
        phone_page.goto(site.url + "/")
        wait_for_service_worker(phone_page)
        phone_page.wait_for_timeout(300)
        mark = len(journal.requests)
        bouton(phone_page, "Accès VIP").click(no_wait_after=True)
        expect(phone_page).to_have_url(site.url + "/login")
        asked = [r for r in journal.requests[mark:] if r["path"] == "/login"]
        assert len(asked) == 1 and asked[0]["preload"] == "true", asked         # la demande du navigateur sert, pas de doublon

    def test_sans_connexion_un_service_worker_arrete_affiche_quand_meme_la_page_hors_connexion(self, page, site):
        page.goto(site.url + "/")
        wait_for_service_worker(page)
        go_offline(page)
        stop_service_workers(page)                  # dans cet ordre : Playwright ne sait plus couper le réseau d'un service worker arrêté
        page.goto(site.url + "/conditions")         # le réseau échoue d'abord, puis le service worker démarre et répond
        expect(page.locator("h1")).to_have_text("Pas de connexion")
        expect(page.get_by_role("link", name="Réessayer")).to_be_visible()

    def test_sans_connexion_ni_cache_du_navigateur_la_page_hors_connexion_garde_son_style(self, page, site):
        page.goto(site.url + "/")
        wait_for_service_worker(page)
        # Le réseau refuse aussi les fichiers du site (et Playwright ne garde plus de cache HTTP pour eux) : seul le cache du
        # service worker peut les fournir.
        page.context.route("**/static/**", lambda route: route.abort())
        go_offline(page)
        stop_service_workers(page)
        page.goto(site.url + "/conditions")
        expect(page.locator("h1")).to_have_text("Pas de connexion")
        expect(page.locator("body")).to_have_css("background-color", "rgb(10, 15, 44)")
        families = page.evaluate("""async () => { await document.fonts.ready;
            return [...document.fonts].filter(font => font.status === 'loaded').map(font => font.family.replaceAll('"', '')); }""")
        assert {"Onest", "Big Shoulders Display"} <= set(families), families
        page.problems.clear()                                                    # (les fichiers refusés à dessein)


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
