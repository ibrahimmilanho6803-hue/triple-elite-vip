"""Pages publiques du site client : Résultats, Combiné gratuit, accueil, plan du site, aperçus de partage.

Deux garanties tiennent tout le reste : (1) un combiné à venir n'apparaît jamais, hors le seul combiné gratuit ; (2) un
visiteur ne déclenche jamais un appel payant (génération IA) ni un appel réseau.
"""
import html as htmllib
import re
import struct
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

import pytest

import config
import dashboard
from generation_service import GenerationService
from showcase import Showcase
from site_fakes import FakeLicenses, history_with, make_pipeline, sample_combos

PAID_TEAMS = ("Bayern Munich", "Lille", "Chelsea", "Atlético Madrid", "Napoli", "Borussia Dortmund")   # combinés 2 et 3


@pytest.fixture()
def site(tmp_path):
    s = SimpleNamespace()
    s.pipeline = make_pipeline(delay=0.0)
    s.cache = tmp_path / "cache"
    s.service = GenerationService(pipeline=s.pipeline, cache_dir=str(s.cache), results_dir=str(tmp_path / "results"))
    s.history = history_with(won=8, lost=5, unfinished=1, pending=2, void=1)
    s.error = None
    s.loads = []

    def loader(refresh):
        s.loads.append(refresh)
        if s.error:
            raise s.error
        return s.history

    s.showcase = Showcase(s.service, loader=loader, results_ttl=0, pick_ttl=0)
    s.app = dashboard.create_app(lm=FakeLicenses(), service=s.service, history_loader=lambda: s.history,
                                 showcase=s.showcase)
    s.app.config["TESTING"] = True
    s.client = s.app.test_client()
    return s


def publish_generation(site, combos=None, age=timedelta(hours=1)):
    """Écrit une génération réussie dans le cache, comme le ferait un abonné qui clique sur « Générer »."""
    made = datetime.now(timezone.utc) - age
    site.service._write("last_generation.json", {"generated_ts": made.timestamp(), "generated_at": made.isoformat(),
                                                 "combos": combos if combos is not None else sample_combos(), "meta": {}})


def raw(response):
    return response.get_data(as_text=True)


def visible(response_or_html):
    """Le texte lu par un visiteur : sans balises, entités et espaces insécables ramenés à des espaces simples."""
    page = response_or_html if isinstance(response_or_html, str) else raw(response_or_html)
    page = re.sub(r"<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", htmllib.unescape(page.replace("&nbsp;", " "))).strip()


def meta(page, key, attr="property"):
    found = re.search(rf'<meta {attr}="{re.escape(key)}" content="([^"]*)"', page)
    return htmllib.unescape(found.group(1)) if found else None


def links(page, prefix):
    return [htmllib.unescape(href) for href in re.findall(r'href="([^"]+)"', page) if href.startswith(prefix)]


# --------------------------------------------------------------------------
# Les pages existent et sont des pages ordinaires du site
# --------------------------------------------------------------------------

def test_les_pages_publiques_repondent(site):
    publish_generation(site)
    for path in ("/", "/resultats", "/gratuit", "/conditions", "/sitemap.xml"):
        response = site.client.get(path)
        assert response.status_code == 200, path
    for path in ("/", "/resultats", "/gratuit"):
        assert "text/html" in site.client.get(path).content_type
        assert site.client.head(path).status_code == 200, path                # sonde de disponibilité
        assert "Set-Cookie" not in site.client.get(path).headers, path          # pages publiques : aucun cookie


def test_menus_et_pied_de_page_menent_aux_nouvelles_pages(site):
    for path in ("/", "/resultats", "/gratuit", "/conditions", "/login", "/page-inconnue"):
        page = raw(site.client.get(path))
        assert 'href="/resultats"' in page and 'href="/gratuit"' in page, path
        assert "Combiné gratuit du jour" in page and "Résultats" in page, path


# --------------------------------------------------------------------------
# Barre de navigation du bas (templates/_tabbar.html) : le menu du site, cinq boutons
# --------------------------------------------------------------------------

BARRE = [("Accueil", "/"), ("Combiné gratuit", "/gratuit"), ("Résultats combinés", "/resultats"), ("Accès VIP", "/login"),
         ("Abonnement VIP", f"{config.PAIEMENT_URL}/paiement")]


def tabbar(page):
    """La barre du bas telle que la lit un visiteur : [(nom, adresse, page affichée ?)], de gauche à droite."""
    nav = re.search(r'<nav class="tabbar".*?</nav>', page, re.S)
    assert nav, "pas de barre du bas"
    return [(htmllib.unescape(label), htmllib.unescape(href), bool(current)) for href, current, label in re.findall(
        r'<a class="tabbar__link" href="([^"]+)"( aria-current="page")?>.*?<span class="tabbar__label">([^<]+)</span>',
        nav.group(0), re.S)]


def test_la_barre_du_bas_a_cinq_boutons_qui_menent_aux_bonnes_pages(site):
    publish_generation(site)
    for path in ("/", "/gratuit", "/resultats", "/conditions", "/login", "/page-inconnue"):
        assert [(name, href) for name, href, _ in tabbar(raw(site.client.get(path)))] == BARRE, path


def test_la_barre_du_bas_marque_la_page_affichee_et_une_seule(site):
    publish_generation(site)
    expected = {"/": "Accueil", "/gratuit": "Combiné gratuit", "/resultats": "Résultats combinés", "/login": "Accès VIP",
                "/conditions": None, "/page-inconnue": None}                  # ces deux pages n'ont pas de bouton à elles
    for path, name in expected.items():
        shown = [button for button, _, here in tabbar(raw(site.client.get(path))) if here]
        assert shown == ([name] if name else []), path


def test_la_barre_du_bas_perd_le_combine_gratuit_quand_la_page_est_coupee(site, monkeypatch):
    monkeypatch.setattr(config, "FREE_PICK_ENABLED", False)
    for path in ("/", "/resultats", "/login"):
        names = [name for name, _, _ in tabbar(raw(site.client.get(path)))]
        assert names == ["Accueil", "Résultats combinés", "Accès VIP", "Abonnement VIP"], path


def test_l_en_tete_ne_garde_que_la_marque(site):
    """Le menu est la barre du bas : plus de second menu (ni de bouton « S'abonner ») en haut des pages."""
    for path in ("/", "/gratuit", "/resultats", "/conditions", "/login"):
        header = re.search(r'<header class="topbar">.*?</header>', raw(site.client.get(path)), re.S).group(0)
        assert header.count("<a ") == 1 and 'class="brand"' in header and "<nav" not in header, path


def test_la_page_hors_connexion_n_a_pas_de_barre(site):
    """Sans réseau, les boutons mèneraient à des pages introuvables : la page enregistrée dans le téléphone n'en a pas."""
    response = site.client.get("/hors-ligne")
    assert response.status_code == 200 and "tabbar" not in raw(response)


def test_aucun_script_ni_style_en_ligne_dans_les_nouvelles_pages(site):
    publish_generation(site)
    for path in ("/", "/resultats", "/gratuit"):
        page = raw(site.client.get(path))
        assert "<style" not in page and not re.search(r"\sstyle=", page), path
        assert not re.search(r"<script(?![^>]*\bsrc=)", page), path
        assert not re.search(r"\son[a-z]+=", page) and "javascript:" not in page, path
    scripts = re.findall(r'<script[^>]*src="([^"]+)"', raw(site.client.get("/gratuit")))
    assert scripts and all(src.startswith("/static/") for src in scripts)


def test_les_liens_vers_l_exterieur_sont_surs(site):
    publish_generation(site)
    page = raw(site.client.get("/gratuit"))
    external = {}
    for anchor in re.findall(r"<a [^>]*href=\"https?://[^>]*>", page):
        host = urlparse(htmllib.unescape(re.search(r'href="([^"]+)"', anchor).group(1))).hostname
        external[host] = anchor
        if host in ("wa.me", "t.me"):
            assert 'target="_blank"' in anchor and 'rel="noopener noreferrer"' in anchor, anchor
    assert {"wa.me", "t.me"} <= set(external)                                         # les deux boutons de partage existent


# --------------------------------------------------------------------------
# Résultats
# --------------------------------------------------------------------------

def test_resultats_affiche_le_bilan_gagnes_et_perdus(site):
    page = visible(site.client.get("/resultats"))
    assert "Nos résultats, gagnés et perdus" in page
    assert "8 / 14" in page and "soit 57 % des combinés joués" in page            # 8 gagnés sur 8 + 5 + 1 perdus
    assert "34 / 40" in page and "soit 85 % des pronostics joués" in page
    assert "Chance moyenne annoncée sur les coupons : 31 %. Réussite constatée : 57 %." in page
    assert "En attente 2" in page                                                    # les deux combinés pas joués
    assert "Les 12 plus récents sur 13" in page                                      # 13 combinés entièrement joués
    assert "jamais des gains en argent" in page


def test_resultats_montre_aussi_les_defaites_avec_le_score(site):
    page = visible(site.client.get("/resultats"))
    assert "Gagné" in page and "Perdu" in page
    assert "Gagné, score 2 – 1" in page and "Perdu, score 0 – 1" in page
    assert page.count("Perdu, score") >= 3                                           # les défaites ne sont pas triées


def test_resultats_compte_un_combine_perdu_avant_la_fin_sans_le_detailler(site):
    page = visible(site.client.get("/resultats"))
    assert "1 combiné déjà perdu compte dans le bilan" in page and "il s’affichera" in page
    site.history = history_with(won=4, lost=2, unfinished=3)
    page = visible(site.client.get("/resultats"))
    assert "3 combinés déjà perdus comptent dans le bilan" in page and "ils s’afficheront" in page
    assert "4 / 9" in page                                                             # 4 gagnés sur 4 + 2 + 3


def teams_of(combo):
    return [leg[side] for leg in combo["predictions"] for side in ("home_team", "away_team")]


def test_resultats_ne_montre_jamais_un_combine_a_venir(site):
    publish_generation(site)                       # la dernière génération contient, elle aussi, des combinés à venir
    page = raw(site.client.get("/resultats"))
    # Index 0 et 1 : combinés en attente ; index 2 : combiné perdu dont deux matchs restent à jouer ; 3 et plus : joués.
    for index in (0, 1, 2):
        for team in teams_of(site.history["combos"][index]):
            assert not re.search(rf"{re.escape(team)}(?!\d)", page), (index, team)
    for combo in sample_combos():
        for team in teams_of(combo):               # noms de la génération (sans numéro, contrairement à l'historique)
            assert not re.search(rf"{re.escape(team)}(?! \d)", page), team
    for team in teams_of(site.history["combos"][3]):
        assert team in page, team                   # alors que les combinés joués y sont


def test_resultats_ne_publie_aucun_champ_non_prevu(site):
    for combo in site.history["combos"]:
        combo["secret"] = "NE-DOIT-PAS-SORTIR"
        combo["match_id"] = "ID-INTERNE-4242"
        for leg in combo["predictions"]:
            leg["api_cost"] = "COUT-INTERNE-77"
    page = raw(site.client.get("/resultats")) + raw(site.client.get("/"))
    assert "NE-DOIT-PAS-SORTIR" not in page and "ID-INTERNE-4242" not in page and "COUT-INTERNE-77" not in page


def test_resultats_sans_assez_de_combines_n_affiche_pas_de_pourcentage(site):
    site.history = history_with(won=2, lost=1)
    page = visible(site.client.get("/resultats"))
    assert "2 / 3" in page
    assert "pourcentage affiché dès 10 combinés joués" in page and "pourcentage affiché dès 30 pronostics joués" in page
    assert re.search(r"soit \d+", page) is None
    assert "Annoncé et constaté" not in page


def test_resultats_sans_historique_joue_donne_un_message_clair(site):
    for history in (history_with(), history_with(pending=3), history_with(void=2)):
        site.history = history
        page = visible(site.client.get("/resultats"))
        assert "Les premiers résultats arrivent" in page and "Combinés gagnés" not in page
        assert "Voir le combiné gratuit du jour" in page
        assert not re.search(r"Arsenal \d", page)                                    # un combiné en attente reste caché


def test_resultats_une_panne_de_l_historique_donne_une_erreur_claire_et_ne_casse_pas_le_reste(site):
    site.error = OSError("disque illisible")
    response = site.client.get("/resultats")
    assert response.status_code == 503 and response.headers["Retry-After"] == "300"
    assert "momentanément indisponibles" in visible(response)
    assert site.client.get("/").status_code == 200
    assert site.client.get("/gratuit").status_code == 200
    assert site.client.get("/conditions").status_code == 200


def test_resultats_s_adapte_aux_donnees_manquantes(site):
    site.history = history_with(won=6, lost=5, chance=None)                          # aucune chance enregistrée
    page = visible(site.client.get("/resultats"))
    assert "6 / 11" in page and "Annoncé et constaté" not in page and "None" not in page
    for combo in site.history["combos"]:
        combo["total_odds"] = None
        combo["generated_at"] = None
    response = site.client.get("/resultats")
    assert response.status_code == 200 and "None" not in visible(response)


def test_les_noms_d_equipe_sont_echappes(site):
    hostile = "<script>alert(1)</script>"
    site.history = history_with(won=1)
    site.history["combos"][0]["predictions"][0]["home_team"] = hostile
    page = raw(site.client.get("/resultats"))
    assert hostile not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    publish_generation(site)
    entry = site.service.latest()
    entry["combos"][0]["predictions"][0]["home_team"] = hostile
    site.service._write("last_generation.json", entry)
    page = raw(site.client.get("/gratuit"))
    assert hostile not in page and "&lt;script&gt;" in page


# --------------------------------------------------------------------------
# Combiné gratuit
# --------------------------------------------------------------------------

def test_gratuit_montre_un_seul_combine_avec_cote_chance_et_confiance(site):
    publish_generation(site)
    page = visible(site.client.get("/gratuit"))
    assert "Le combiné gratuit du jour" in page and "Combiné gratuit" in page
    for team in ("Arsenal", "Brighton", "Inter", "Torino", "Real Sociedad", "Getafe"):
        assert team in page
    assert "Cote totale 2,55" in page and "Chance estimée 31 %" in page
    assert page.count("confiance") >= 3 and "≈" in page
    assert "Une chance estimée n’est pas une certitude" in page
    assert "nos résultats réels sont publics" in page


def test_gratuit_ne_montre_rien_des_combines_payants(site):
    publish_generation(site)
    for path in ("/gratuit", "/"):
        page = raw(site.client.get(path))
        for team in PAID_TEAMS:
            assert team not in page, (path, team)
    page = raw(site.client.get("/resultats"))              # l'historique réutilise ces noms, suivis d'un numéro
    for team in PAID_TEAMS:
        assert not re.search(rf"{re.escape(team)}(?! \d)", page), team
    assert "Tu veux les autres combinés" in visible(site.client.get("/gratuit"))


def test_gratuit_offre_le_combine_de_la_plus_haute_chance(site):
    combos = sample_combos()
    combos[0], combos[1] = combos[1], combos[0]
    combos[1]["success_probability"] = 29.0
    combos[0]["success_probability"] = 33.3                                         # Bayern, Lille, Chelsea
    publish_generation(site, combos)
    page = visible(site.client.get("/gratuit"))
    assert "Bayern Munich" in page and "Chance estimée 33 %" in page and "Arsenal" not in page


def test_gratuit_affiche_l_heure_limite_en_utc_remplacee_par_l_heure_de_l_appareil(site):
    publish_generation(site)
    page = raw(site.client.get("/gratuit"))
    deadline = re.search(r"À jouer avant le premier match.{1,20}?<time class=\"local\" datetime=\"([^\"]+)\">([^<]+)</time>",
                         page, re.S)
    assert deadline and deadline.group(2).endswith("UTC")
    first = min(leg["kickoff"] for leg in sample_combos()[0]["predictions"])
    assert datetime.fromisoformat(deadline.group(1)).timestamp() == pytest.approx(datetime.fromisoformat(first).timestamp(), abs=5)
    assert page.count('<time class="local"') >= 4                                      # la limite + l'heure de chaque match


def test_gratuit_liens_de_partage(site):
    publish_generation(site)
    page = raw(site.client.get("/gratuit"))
    url = f"{config.SITE_URL}/gratuit"
    whatsapp = links(page, "https://wa.me/")
    telegram = links(page, "https://t.me/")
    assert len(whatsapp) == 1 and len(telegram) == 1
    text = parse_qs(urlparse(whatsapp[0]).query)["text"][0]
    assert text.endswith(url) and "Le combiné gratuit du jour sur Triple Elite VIP" in text and "cote 2,55" in text
    query = parse_qs(urlparse(telegram[0]).query)
    assert query["url"] == [url] and "chance estimée 31 %" in query["text"][0]
    copy_button = re.search(r"<button[^>]*data-copy=\"([^\"]+)\"[^>]*>", page)
    assert copy_button and unquote(copy_button.group(1)) == url and " hidden" in copy_button.group(0)   # caché sans JavaScript
    assert 'data-copy-status' in page and 'role="status"' in page


def test_gratuit_vide_tant_qu_aucune_generation_n_existe_et_ne_coute_rien(site):
    response = site.client.get("/gratuit")
    page = visible(response)
    assert response.status_code == 200 and "Le combiné gratuit n’est pas encore prêt" in page
    assert "wa.me" not in raw(response) and "Voir les résultats" in page
    for _ in range(25):                                                              # une rafale de visiteurs
        for path in ("/gratuit", "/resultats", "/"):
            site.client.get(path)
    assert site.pipeline.state["calls"] == 0                                         # jamais d'appel payant
    assert site.service.generations_today() == 0 and site.service.status() == {"state": "idle"}
    assert not (site.cache / "job.json").exists() and not (site.cache / "usage.json").exists()
    assert set(site.loads) == {False}                                                # jamais de récupération de scores


def test_gratuit_apparait_apres_la_generation_d_un_abonne_et_reste_le_meme(site):
    assert "pas encore prêt" in visible(site.client.get("/gratuit"))
    site.service.request_generation()                                                # clic d'un abonné
    site.service._thread.join(10)
    first = visible(site.client.get("/gratuit"))
    assert "Arsenal" in first and "pas encore prêt" not in first
    assert (site.cache / "free_pick.json").exists()
    better = sample_combos()
    better[2]["success_probability"] = 48.0                                          # une génération plus tard, un meilleur combiné
    publish_generation(site, better, age=timedelta(0))
    assert visible(site.client.get("/gratuit")) == first                             # le lien partagé le matin montre le même coupon


def test_gratuit_disparait_apres_un_jour_ou_quand_les_matchs_ont_commence(site):
    publish_generation(site, age=timedelta(hours=26))
    assert "pas encore prêt" in visible(site.client.get("/gratuit"))
    started = sample_combos()
    for combo in started:
        combo["predictions"][0]["kickoff"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    publish_generation(site, started)
    assert "pas encore prêt" in visible(site.client.get("/gratuit"))


def test_gratuit_une_panne_ne_casse_pas_la_page(site, monkeypatch):
    def broken():
        raise RuntimeError("panne")

    monkeypatch.setattr(site.showcase, "free_pick", broken)
    response = site.client.get("/gratuit")
    assert response.status_code == 200 and "pas encore prêt" in visible(response)


def test_gratuit_peut_etre_coupe_sans_deploiement(site, monkeypatch):
    publish_generation(site)
    monkeypatch.setattr(config, "FREE_PICK_ENABLED", False)
    assert site.client.get("/gratuit").status_code == 404
    for path in ("/", "/resultats", "/conditions", "/login"):
        page = raw(site.client.get(path))
        assert 'href="/gratuit"' not in page, path                                    # plus aucun lien vers la page coupée
        assert 'href="/resultats"' in page, path
    assert "/gratuit" not in raw(site.client.get("/sitemap.xml"))
    assert site.pipeline.state["calls"] == 0


# --------------------------------------------------------------------------
# Accueil
# --------------------------------------------------------------------------

def test_l_accueil_montre_les_chiffres_reels_a_partir_de_dix_combines(site):
    page = visible(site.client.get("/"))
    assert "Nos résultats sont publics" in page
    assert "Combinés gagnés 8 / 14" in page and "soit 57 % des combinés joués" in page
    assert "Pronostics gagnés 34 / 40" in page and "Chance annoncée 31 %" in page
    links_in_page = raw(site.client.get("/"))
    assert 'href="/resultats"' in links_in_page and 'href="/gratuit"' in links_in_page


def test_l_accueil_n_invente_aucun_chiffre_sans_assez_de_donnees(site):
    for history in (history_with(won=3, lost=2), history_with(), history_with(pending=4)):
        site.history = history
        response = site.client.get("/")
        page = visible(response)
        assert response.status_code == 200 and "Nos résultats sont publics" in page
        assert "Combinés gagnés" not in page and "Chance annoncée" not in page
        assert "Voir tous les résultats" in page


def test_l_accueil_s_adapte_aux_donnees_manquantes_et_aux_pannes(site):
    site.history = history_with(won=7, lost=5, chance=None)
    page = visible(site.client.get("/"))
    assert "Combinés gagnés 7 / 12" in page and "Chance annoncée" not in page and "None" not in page
    assert "En attente 0" in page                                                     # la case libre montre les combinés en attente
    site.error = RuntimeError("panne")
    response = site.client.get("/")
    assert response.status_code == 200 and "Nos résultats sont publics" in visible(response)


def test_toutes_les_pages_publiques_gardent_l_avertissement_sur_les_paris(site):
    publish_generation(site)
    for path in ("/", "/resultats", "/gratuit", "/conditions"):
        page = visible(site.client.get(path))
        assert "jamais une garantie de résultat" in page and "interdits aux moins de 18 ans" in page, path


# --------------------------------------------------------------------------
# Offres : l'essai de 7 jours est proposé là où le visiteur juge le service
# --------------------------------------------------------------------------

def test_les_pages_de_preuve_proposent_l_essai_de_7_jours(site):
    publish_generation(site)
    for path in ("/gratuit", "/resultats"):
        page = raw(site.client.get(path))
        text = visible(page)
        assert f'href="{config.PAIEMENT_URL}/paiement?plan=weekly"' in page, path
        assert "Essayer 7 jours, 2 000 FCFA" in text, path                         # le bouton convenu avec le vendeur
        assert "Essaie 7 jours pour 2 000 FCFA (≈ 3 €)" in text and "à partir de 6 000 FCFA par mois" in text, path
        assert f'href="{config.PAIEMENT_URL}"' in page and "voir toutes les offres" in text, path
        assert "30 €" not in text and "19 700" not in text, path                      # les anciens prix ont disparu


def test_l_accueil_presente_trois_formules_en_fcfa(site):
    page = raw(site.client.get("/"))
    text = visible(page)
    names = re.findall(r'<h3 class="plan__name">([^<]+)</h3>', page)
    assert [visible(name) for name in names] == ["Pass 7 jours", "Mensuel", "Annuel"]
    assert [href.split("=")[-1] for href in re.findall(r'href="[^"]*/paiement\?plan=(\w+)"', page)] == [
        "weekly", "monthly", "yearly"] and "Trois formules, le même accès" in text
    assert "À partir de 2 000 FCFA (≈ 3 €) pour 7 jours" in text                  # sous le bouton principal
    assert "soit 3 283 FCFA par mois" in text and "pour le prix de 6,6 mois (45 % d’économie)" in text
    assert "En quelle monnaie je paie" in text and "Puis-je d’abord essayer" in text
    assert "1 € = 655,957 FCFA" in text


def test_l_accueil_annonce_le_tarif_de_lancement_puis_l_efface(site, monkeypatch):
    today = datetime.now(timezone.utc).date()
    monkeypatch.setattr(config, "LAUNCH_PRICE_UNTIL", today + timedelta(days=30))
    text = visible(site.client.get("/"))
    assert "Tarif de lancement" in text and "Jusqu’au " in text
    monkeypatch.setattr(config, "LAUNCH_PRICE_UNTIL", today - timedelta(days=1))      # la date est passée : plus rien d'annoncé
    text = visible(site.client.get("/"))
    assert "Tarif de lancement" not in text and "Jusqu’au " not in text and "30 jours d’accès" in text


def test_les_conditions_decrivent_les_trois_durees_et_les_prix_en_fcfa(site):
    text = visible(site.client.get("/conditions"))
    assert "7 jours (Pass 7 jours), 30 jours (mensuel) ou 365 jours (annuel)" in text
    assert "Les prix sont affichés et débités en FCFA" in text and "1 € = 655,957 FCFA" in text
    assert "arrondi à la centaine supérieure" not in text


# --------------------------------------------------------------------------
# Aperçus de partage, plan du site, robots
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/resultats", "/gratuit"])
def test_apercu_de_partage_complet(site, path):
    publish_generation(site)
    page = raw(site.client.get(path))
    assert meta(page, "og:type") == "website" and meta(page, "og:locale") == "fr_FR"
    assert meta(page, "og:site_name") == "Triple Elite VIP"
    assert meta(page, "og:title") and meta(page, "og:description")
    assert meta(page, "og:url") == config.SITE_URL + path.rstrip("/") + ("/" if path == "/" else "")
    assert re.search(rf'<link rel="canonical" href="{re.escape(meta(page, "og:url"))}">', page)
    image = meta(page, "og:image")
    assert image.startswith(config.SITE_URL + "/static/images/partage.png?v=")          # adresse absolue : exigée par WhatsApp
    assert meta(page, "og:image:width") == "1200" and meta(page, "og:image:height") == "630"
    assert meta(page, "og:image:alt") and meta(page, "twitter:card", "name") == "summary_large_image"
    assert 'name="robots" content="index, follow"' in page


def test_l_image_de_partage_existe_et_a_la_bonne_taille(site):
    image = meta(raw(site.client.get("/resultats")), "og:image")
    response = site.client.get(image.replace(config.SITE_URL, ""))
    assert response.status_code == 200 and response.content_type == "image/png"
    data = response.data
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (1200, 630)
    assert len(data) < 600 * 1024                                                      # WhatsApp ignore les images trop lourdes


def test_les_titres_des_pages_sont_distincts(site):
    publish_generation(site)
    titles = {path: re.search(r"<title>(.*?)</title>", raw(site.client.get(path)), re.S).group(1).strip()
              for path in ("/", "/resultats", "/gratuit", "/conditions")}
    assert len(set(titles.values())) == 4 and all(len(title) <= 90 for title in titles.values()), titles


def test_plan_du_site_et_robots(site):
    response = site.client.get("/sitemap.xml")
    assert response.status_code == 200 and response.content_type.startswith("application/xml")
    body = raw(response)
    locations = re.findall(r"<loc>([^<]+)</loc>", body)
    assert locations == [f"{config.SITE_URL}{path}" for path in ("/", "/resultats", "/gratuit", "/conditions")]
    assert not any(path in body for path in ("/app", "/login", "/api"))
    robots = raw(site.client.get("/robots.txt"))
    assert f"Sitemap: {config.SITE_URL}/sitemap.xml" in robots
    assert "Disallow: /app" in robots and "Disallow: /api/" in robots and "Disallow: /login" in robots
    assert "Disallow: /resultats" not in robots and "Disallow: /gratuit" not in robots


# --------------------------------------------------------------------------
# Génération automatique : branchement au démarrage
# --------------------------------------------------------------------------

class SpyDaily:
    created = []

    def __init__(self, service, hour):
        self.service, self.hour, self.started = service, hour, 0
        SpyDaily.created.append(self)

    def start(self):
        self.started += 1
        return self


def test_la_generation_automatique_n_existe_pas_sans_reglage(tmp_path, monkeypatch):
    SpyDaily.created = []
    monkeypatch.setattr(dashboard, "DailyGeneration", SpyDaily)
    monkeypatch.setattr(config, "AUTO_GENERATE_HOUR", None)
    app = dashboard.create_app(lm=FakeLicenses(), service=GenerationService(pipeline=make_pipeline(0.0),
                               cache_dir=str(tmp_path / "c"), results_dir=str(tmp_path / "r")),
                               history_loader=lambda: history_with())
    assert SpyDaily.created == [] and app.extensions["tev"].daily is None


def test_la_generation_automatique_demarre_a_l_heure_reglee(tmp_path, monkeypatch):
    SpyDaily.created = []
    monkeypatch.setattr(dashboard, "DailyGeneration", SpyDaily)
    monkeypatch.setattr(config, "AUTO_GENERATE_HOUR", 7)
    service = GenerationService(pipeline=make_pipeline(0.0), cache_dir=str(tmp_path / "c"), results_dir=str(tmp_path / "r"))
    app = dashboard.create_app(lm=FakeLicenses(), service=service, history_loader=lambda: history_with())
    (daily,) = SpyDaily.created
    assert daily.service is service and daily.hour == 7 and daily.started == 1
    assert app.extensions["tev"].daily is daily
