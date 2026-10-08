# Triple Elite VIP

Site d'abonnement qui analyse les cinq grands championnats de football (Premier League, La Liga, Bundesliga,
Ligue 1, Serie A) avec l'IA Claude et compose **trois combinés de trois matchs**, de cote totale d'au moins 2,50.
Les clients paient par Mobile Money (PayDunya ; la carte bancaire viendra quand PayDunya l'aura activée sur le compte,
voir « Moyens de paiement annoncés aux clients »), reçoivent une clé de licence, et se connectent à leur espace.

- Site des clients : <https://triple-elite-vip.com> (service `dashboard`)
- Paiement : <https://paiement.triple-elite-vip.com> (service `paiement`, adresse de secours : <https://triple-elite-vip-paiement.onrender.com>)
- Hébergement : Render. Chaque envoi sur la branche `main` de GitHub redéploie les deux services.

## Ce que le site promet, et ce qu'il ne promet pas

Un combiné n'est gagné que si ses **trois** pronostics le sont. L'IA estime chaque pari autour de 65 à 76 %
de chance ; trois paris à 68 % donnent environ **31 à 34 % de chance** que le combiné passe (0,68³ ≈ 0,31).
C'est ce que montre le site : la chance estimée de chaque coupon est affichée, l'historique des combinés gagnés
**et perdus** est visible par les clients, et la page « Conditions » rappelle qu'aucun gain n'est garanti.
Ne jamais promettre 80 ou 90 % de réussite sur un combiné : ce n'est pas atteignable, et c'est faux.

Comment les chiffres sont fabriqués (détails dans `probabilities.py` et `combo_generator.py`) :

1. L'IA ne fait qu'**estimer des probabilités** par match (victoire, double chance, buts, deux équipes qui marquent…)
   à partir des données récoltées : forme récente, confrontations directes, bilan de saison.
2. Le code rend ces estimations **cohérentes** entre elles (un « plus de 2,5 buts » ne peut pas être plus probable
   qu'un « plus de 1,5 »), et calcule les paris composés (« victoire ET plus de 1,5 but ») par produit des probabilités.
3. La confiance est **plafonnée selon la quantité de données** : moins de 4 matchs connus pour une équipe, jamais plus
   de 60 % ; de 4 à 7 matchs, 72 % ; de 8 à 14, 82 % ; 15 ou plus, 90 %.
4. Seuls les paris à 65 % ou plus sont gardés ; les combinés sont choisis par **probabilité de réussite**, avec un peu
   de variété (championnats, types de paris).
5. Les **cotes** sont les vraies cotes des bookmakers (médiane) si `ODDS_API_KEY` est définie et que le pari est coté ;
   sinon elles sont estimées par `(1 − 7 %) / probabilité` et affichées avec un « ≈ ». Sans clé de cotes, la cote
   totale de 2,50 est donc une estimation, pas une cote jouable : le coupon invite le client à la vérifier.

## Parcours d'un client

Avant d'acheter, un visiteur peut consulter les résultats réels et un combiné gratuit (voir « Pages publiques »).

1. Il choisit une offre sur `/paiement` (30 € par mois, 60 € par an, débités en FCFA : 19 700 et 39 400 FCFA).
2. `/payer` crée la facture PayDunya et mémorise la commande **avant** la redirection.
3. Après paiement, PayDunya le renvoie sur `/succes?token=…`. Le site demande à PayDunya de confirmer, délivre la
   licence (une seule fois, même si la page est rechargée), l'**affiche** et l'envoie par e-mail.
4. Filet de sécurité : PayDunya appelle aussi `/ipn` ; la même logique y est appliquée, donc la licence est délivrée
   même si le client ferme son navigateur. Le contenu de cet appel n'est jamais cru : seul le jeton compte, et c'est
   PayDunya qui est interrogé. Le rappel (`callback_url`) est envoyé avec chaque facture : rien à régler chez PayDunya
   (l'adresse `/ipn-paydunya`, enregistrée dans l'application PayDunya, répond aussi).
5. Un client qui renouvelle **avant** la fin de son abonnement garde sa clé ; la durée achetée s'ajoute au temps restant.
   Après expiration, une nouvelle clé est délivrée. Un mois compte 30 jours, un an 365 jours.
6. Il se connecte sur le site avec son e-mail et sa clé, puis génère ses combinés (un seul calcul en arrière-plan pour
   tous les clients, réutilisé pendant 3 heures) et consulte l'historique avec le score de chaque match.
7. Il peut installer le site sur son téléphone comme une application (voir « Application installable »).

## Pages publiques : résultats et combiné gratuit

Deux pages ouvertes à tous (et aux moteurs de recherche) montrent le produit tel qu'il est, sans maquillage, avant tout
paiement. L'accueil (section « Nos résultats sont publics »), le menu et le pied de page y mènent.

- `/resultats` : le bilan réel (combinés gagnés sur joués, pronostics gagnés sur joués, chance annoncée en moyenne face à la
  réussite constatée) et le détail des 12 derniers combinés **entièrement joués**, gagnés comme perdus, avec le score de chaque
  match. Il se met à jour tout seul à mesure que les scores arrivent.
- `/gratuit` : **un seul** combiné offert (celui de plus haute chance estimée dans la dernière génération), avec cote, confiance de
  chaque pronostic et chance estimée, et des boutons WhatsApp, Telegram et « Copier le lien ».

Règles qui commandent le code (`showcase.py` ; vérifiées par `tests/test_showcase.py`, `tests/test_public_pages.py` et les tests
navigateur `TestResultatsEtCombineGratuit`) :

1. **Jamais un combiné à venir en public, sauf le combiné gratuit.** Les deux autres combinés d'une génération sont le produit
   payant. `/resultats` ne détaille que des combinés dont tous les matchs sont joués. Un combiné déjà perdu mais dont des matchs
   restent à jouer compte tout de suite dans le bilan (une défaite connue ne se cache pas), et ne s'affiche qu'à la fin. Chaque
   dictionnaire envoyé aux pages est construit champ par champ (liste blanche) : un champ ajouté plus tard aux combinés enregistrés
   ne devient pas public par accident.
2. **Un visiteur ne coûte rien.** Une page publique ne lance jamais de génération (appel payant à l'IA : elle lit seulement la
   dernière, `GenerationService.latest`) et ne fait aucun appel réseau pendant la requête. Les scores manquants sont récupérés en
   arrière-plan, au plus tous les quarts d'heure (`combo_history` borne en plus chaque série d'appels à TheSportsDB). Une panne de
   l'historique ne casse ni l'accueil ni le combiné gratuit : `/resultats` répond alors « momentanément indisponibles » (503).
3. **Aucun chiffre trompeur.** Un pourcentage n'apparaît qu'à partir de 10 combinés joués (30 pronostics pour celui des pronostics) ;
   en dessous, seuls les nombres bruts s'affichent, et l'accueil n'affiche pas de chiffres du tout. Le bilan compte des pronostics
   gagnés ou perdus, jamais des gains en argent (les cotes sont souvent estimées : un « bénéfice » serait inventé). Un pronostic
   présent dans plusieurs combinés compte une fois ; les matchs reportés ou annulés ne comptent pas. Le pied de page garde
   l'avertissement sur les paris et l'interdiction aux moins de 18 ans.
4. **Le combiné gratuit est stable.** Il est choisi une fois par génération et enregistré (`free_pick.json`, dossier de cache) : si
   une autre génération sort dans la journée, le lien partagé le matin montre toujours le même coupon le soir. Il disparaît 15
   minutes avant le premier match, ou 24 heures après avoir été choisi. Il faut alors une **nouvelle génération** pour en avoir un
   autre : les deux autres combinés de l'ancienne ne prennent pas sa place.

**La page « Combiné gratuit » est vide** quand aucune génération récente n'existe (personne n'a cliqué sur « Générer » depuis 24
heures, ou tous les matchs ont commencé) : elle l'explique au visiteur et renvoie vers les résultats. Pour qu'elle soit alimentée
même les jours calmes, activer la génération automatique.

**Génération automatique quotidienne (désactivée par défaut)** : `AUTO_GENERATE_HOUR=6` (Render > service dashboard > Environment ;
heure **UTC**, de 0 à 23) lance chaque jour, à partir de cette heure, une génération s'il n'y en a pas déjà eu une ce jour-là
(`daily_generation.py`). C'est un appel payant à l'IA (de l'ordre de 0,2 $ l'unité, à vérifier sur la console Anthropic : environ 6 $
par mois à raison d'une par jour). Elle passe par les mêmes garde-fous que le bouton « Générer » (un travail à la fois, combinés
encore frais réutilisés, plafond de 10 par jour, pause après un échec) et s'arrête après 3 tentatives dans la journée. Supprimer la
variable la désactive.

**Couper le combiné gratuit** : `FREE_PICK_ENABLED=0` (Render > Environment ; Render redémarre le service quand une variable change)
retire `/gratuit` (erreur 404), ses liens et son entrée du plan du site. Valeur par défaut : activé.

**Partage** : les boutons WhatsApp et Telegram sont de simples liens (ils marchent sans JavaScript) ; « Copier le lien » apparaît
grâce à `static/js/public.js`, qui convertit aussi les heures de match en heure de l'appareil (sans JavaScript elles restent en
UTC, écrites comme telles). L'aperçu que WhatsApp, Telegram, Facebook ou X affichent quand on colle l'adresse d'une page vient des
balises `og:` de `templates/_meta.html` et de l'image `static/images/partage.png` (1200 x 630 px, sans aucun chiffre, pour ne jamais
se démentir). Elle est fabriquée par `python scripts/make_share_image.py` (Playwright) et versionnée dans Git ; à relancer seulement si
son texte ou son style changent. Les messageries gardent un aperçu en mémoire : le nom du fichier porte une empreinte, donc
l'adresse de l'image change seule quand l'image change. Pour contrôler un aperçu : coller l'adresse d'une page dans une conversation
avec soi-même. `/sitemap.xml` liste les pages publiques et `robots.txt` l'annonce ; pour être indexé plus vite, déclarer ce plan
du site dans la Search Console de Google (facultatif).

**Quand on modifie les coupons de l'espace client** (`static/js/dashboard.js`), reporter le changement dans
`templates/_coupon.html`, leur copie côté serveur pour les pages publiques : la feuille de style est commune.

## Les fichiers

| Fichier | Rôle |
| --- | --- |
| `dashboard.py` | Site des clients : accueil, résultats, combiné gratuit, plan du site, conditions, connexion, espace client, API de génération et d'historique |
| `showcase.py`, `daily_generation.py` | Pages publiques : bilan et combiné gratuit (ce qui a le droit d'être montré) ; génération automatique quotidienne, facultative |
| `paiement.py`, `paydunya.py` | Site de paiement et client de l'API PayDunya |
| `license_manager.py` | Licences et commandes dans PostgreSQL (délivrance atomique, renouvellements cumulés) |
| `email_sender.py` | Envoi de la clé : Brevo (HTTPS) ou Gmail (SMTP), avec nouvelles tentatives |
| `generation_service.py`, `pipeline.py` | Génération en arrière-plan : une à la fois, cache, plafond quotidien, avancement |
| `data_collector.py`, `analyzerv2.py` | Données TheSportsDB, analyse IA (modèles de repli si un nom de modèle n'existe pas) |
| `probabilities.py`, `markets.py`, `combo_generator.py` | Probabilités, catalogue des paris et verdicts, composition des combinés et cotes |
| `combo_history.py` | Historique : résultats réels des matchs joués, bilan |
| `web_common.py`, `privacy.py` | Sécurité commune (en-têtes, anti-CSRF, limitation d'essais), gabarits, e-mails masqués dans les journaux |
| `config.py` | Réglages non secrets ; lit aussi un fichier `.env` en local |
| `templates/`, `static/` | Pages et styles (sans script ni style en ligne), JavaScript du site ; `templates/_coupon.html` (coupons des pages publiques), `_meta.html` (aperçu de partage), `static/js/public.js`, `static/images/partage.png` |
| `pwa.py`, `templates/sw.js`, `static/js/pwa.js`, `static/icons/` | Application installable : manifeste, service worker, page « hors connexion », bouton d'installation, icônes |
| `main.py`, `generate_keys.py`, `scripts/send_test_email.py` | Outils en ligne de commande (voir plus bas) |
| `scripts/make_icons.py`, `scripts/make_share_image.py` | Fabrication des icônes de l'application et de l'image d'aperçu de partage (Playwright) |
| `render.yaml`, `gunicorn.conf.py`, `.python-version` | Déploiement |
| `tests/` | Tests automatiques (Flask et navigateur) |

## Variables d'environnement

Sur Render : Dashboard > le service > **Environment**. En local : copier `.env.example` en `.env` (ignoré par Git).
Ne jamais mettre une clé dans le code ni dans Git.

| Variable | Service | Rôle |
| --- | --- | --- |
| `SECRET_KEY` | dashboard | Signe les sessions. Longue et aléatoire ; la changer déconnecte tous les clients. Sans elle, les sessions sautent à chaque redémarrage |
| `DATABASE_URL` | les deux | Base PostgreSQL des licences (« Internal Database URL » de Render). La **même** valeur pour les deux services |
| `ANTHROPIC_API_KEY` | dashboard | Analyse IA (obligatoire pour générer) |
| `ANTHROPIC_MODEL` | dashboard | Facultatif. Par défaut `claude-sonnet-5-5` ; si l'API répond « modèle introuvable », `claude-sonnet-5` puis `claude-sonnet-4-5` sont essayés |
| `SPORTSDB_API_KEY` | dashboard | Ta clé TheSportsDB. Sans elle, la clé de test partagée `3` est utilisée (limitée) |
| `ODDS_API_KEY` | dashboard | Facultatif : vraies cotes (the-odds-api.com). Sans elle, cotes estimées |
| `DATA_DIR` | dashboard | Dossier du disque persistant (`/var/data`) : historique des matchs, combinés générés, cache |
| `FREE_PICK_ENABLED` | dashboard | Facultatif. `0` (ou `non`, `false`, `off`) retire le combiné gratuit : `/gratuit` et ses liens disparaissent. Par défaut : activé |
| `AUTO_GENERATE_HOUR` | dashboard | Facultatif. Heure UTC (0 à 23) à partir de laquelle une génération automatique part chaque jour s'il n'y en a pas eu. **Appel payant à l'IA** ; absente = désactivée (voir « Pages publiques ») |
| `PAYDUNYA_MASTER_KEY`, `PAYDUNYA_PRIVATE_KEY`, `PAYDUNYA_TOKEN` | paiement | Clés API PayDunya |
| `PAYDUNYA_MODE`, `PAYDUNYA_TEST_PRIVATE_KEY`, `PAYDUNYA_TEST_TOKEN`, `PAYDUNYA_TEST_MASTER_KEY`, `PAYDUNYA_TEST_EMAILS` | paiement | Facultatif, **mode test seulement** : interrupteur, clés de test et adresses autorisées à commander (voir « Essayer le paiement sans argent ») |
| `LICENSE_SECRET_KEY` | paiement | Sel de fabrication des nouvelles clés (en changer n'invalide aucune clé existante) |
| `GMAIL_EMAIL`, `GMAIL_MDP` | paiement | Envoi par Gmail (mot de passe d'application) |
| `BREVO_API_KEY`, `EMAIL_SENDER` | paiement | Envoi par Brevo (prioritaire sur Gmail si définie) ; `EMAIL_SENDER` = adresse d'expéditeur validée |
| `SITE_URL`, `PAIEMENT_BASE_URL` | les deux | Adresses publiques (valeurs par défaut correctes ; si `PAIEMENT_BASE_URL` est définie sur Render, elle l'emporte sur le code) |
| `ODDS_CACHE_MINUTES`, `WEB_CONCURRENCY`, `GUNICORN_THREADS` | facultatif | Durée de conservation des cotes (360 min), nombre de processus (1) et de fils (4) |

## Lancer en local

```bash
python -m venv .venv && source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                      # puis remplir .env
python dashboard.py        # http://localhost:5000
python paiement.py         # http://localhost:5001   (sous Windows : start_all.bat lance les deux)
```

Sans `DATABASE_URL`, les sites démarrent mais personne ne peut se connecter ni payer (la base des licences est
introuvable). Pour voir le rendu sans rien configurer, voir `tests/e2e/harness.py` plus bas : il démarre le site complet
avec de fausses données.

## Tests

```bash
python -m pytest tests -q                 # tests de la logique et des deux sites (sans réseau, sans clé)
python -m playwright install chromium     # une seule fois, pour les tests de navigateur
python -m pytest tests/e2e -q             # parcours réels dans Chromium : connexion, génération, historique, pages publiques, paiement, application installable
```

Les tests ne contactent jamais TheSportsDB, PayDunya, Anthropic ni un serveur d'e-mail : tout est simulé. Les tests
navigateur sont ignorés automatiquement si Playwright ou Chromium n'est pas installé.
Pour regarder le rendu : `python tests/e2e/harness.py captures` écrit des captures d'écran (ordinateur et téléphone).

## Outils en ligne de commande

```bash
python main.py --save                        # génère les combinés sans passer par le site (--save : les ajoute à l'historique)
python generate_keys.py                      # crée, prolonge, désactive ou supprime une licence à la main (cadeau, geste commercial)
python scripts/send_test_email.py moi@exemple.com   # vérifie que l'envoi d'e-mail fonctionne
```

`generate_keys.py` et `send_test_email.py` utilisent les mêmes variables que le site (`DATABASE_URL`, `GMAIL_*` ou `BREVO_*`).
Après chaque création ou prolongation, `generate_keys.py` propose d'envoyer la clé par e-mail au client (réponse `o` pour
accepter ; sans cela, rien ne part). Le message est celui d'un accès offert : il ne rappelle pas la renonciation à la
rétractation d'un achat. À lancer dans le Shell du service de paiement, qui a `GMAIL_MDP`.

Menu de `generate_keys.py` : `1` créer ou prolonger, `2` voir toutes les licences, `3` désactiver (la clé reste en base mais
est refusée), `4` supprimer définitivement, `5` quitter. La suppression efface toutes les lignes d'un e-mail pour repartir de
zéro (ensuite `1` recrée une licence neuve) : le script montre d'abord ce qui va disparaître et n'efface rien sans que le mot
`supprimer` soit tapé en toutes lettres. Les commandes de paiement ne sont jamais touchées. Si deux lignes existent pour un
même e-mail (reste d'anciennes versions, qui gardaient l'e-mail tel que saisi : `Client@…` et `client@…`), la liste (`2`) les
marque `DOUBLON` et l'en-tête du menu l'indique (`2 licence(s) pour 1 e-mail(s) : DOUBLONS à supprimer`). La connexion au
site n'en est plus gênée (la ligne dont la clé correspond décide), mais mieux vaut les nettoyer avec `4` puis `1`.

## Déploiement sur Render

Les services actuels ont été créés à la main : **les réglages du Dashboard font foi**, pas `render.yaml` (qui documente
la cible). Liste de contrôle :

- **Offres** (relevé du 6 octobre 2026) : les deux services sont sur l'offre à 7 $/mois (0,5 CPU, 512 Mo), région
  Frankfurt, branche `main`, déploiement automatique à chaque envoi sur `main`.
- **Start Command** : `gunicorn dashboard:app --bind 0.0.0.0:10000 --timeout 240 --graceful-timeout 240` et
  `gunicorn paiement:app --bind 0.0.0.0:10000` (chaque service a son propre port chez Render). `gunicorn.conf.py` ajoute
  des fils d'exécution (pages réactives pendant une génération) sans rien changer côté Render.
- **Health Check Path** : `/health` sur les deux services.
- **Adresses** : le site des clients est `triple-elite-vip.com` (Custom Domain du service dashboard). Le site de paiement
  est `paiement.triple-elite-vip.com` : Custom Domain du service paiement (Settings > Custom Domains) **et** un
  enregistrement DNS chez Namecheap (Advanced DNS) de type CNAME, hôte `paiement`, valeur
  `triple-elite-vip-paiement.onrender.com`. L'adresse `…onrender.com` répond toujours : la garder (secours, anciens liens et
  anciennes factures PayDunya). Si `PAIEMENT_BASE_URL` est définie sur Render, elle prime sur la valeur du code.
- **Python** : `.python-version` fixe la version (3.13). Une variable `PYTHON_VERSION` sur Render prime sur ce fichier.
- **Disque persistant** (dashboard) monté sur `/var/data`, avec `DATA_DIR=/var/data`. Sans lui, l'historique des matchs et
  des combinés est perdu à chaque déploiement.
- **Base PostgreSQL** : sur l'offre gratuite de Render, la base **expire** (suppression après un délai, voir le tableau
  de bord). Elle contient toutes les licences : passer à une offre payante avant l'échéance, et garder une sauvegarde.
- **E-mails** : l'offre gratuite de Render **bloque les ports SMTP** ; sur une offre payante (le cas aujourd'hui),
  Gmail fonctionne avec un mot de passe d'application (`GMAIL_MDP`). Si on revenait à l'offre gratuite, définir
  `BREVO_API_KEY` (envoi par HTTPS ; l'offre gratuite de Brevo permet environ 300 e-mails par jour). Dans tous les cas, la
  clé s'affiche sur la page de confirmation, et le client peut la retrouver en rouvrant cette page.
- **Supervision** (UptimeRobot) : surveiller `/health` des **deux** services. Sur l'offre gratuite, un service s'endort
  après 15 minutes sans visite (premier chargement lent) ; sur l'offre payante, la supervision sert à être prévenu d'une panne.
- **Variables inutiles** : le code ne lit plus `STRIPE_SECRET_KEY`, `LICENSES` ni `APIFY_TOKEN` (restes d'anciennes versions) ;
  les supprimer de Render réduit les secrets exposés. `GMAIL_MDP` ne sert qu'au service de paiement.
- **Secrets** : après toute fuite (clé collée dans une conversation, capture d'écran…), régénérer la clé chez son
  fournisseur (PayDunya, Anthropic, Google, TheSportsDB) puis la remplacer dans Render.

## Essayer le paiement sans argent (mode test PayDunya)

PayDunya donne à chaque application des clés de **test** (`test_private_…`) qui ne fonctionnent que sur son API « bac à
sable » : les paiements y sont fictifs. Un interrupteur permet de les essayer **sans toucher aux clés de production** :

1. PayDunya > **Intégrer** > ton application > **Afficher les clés API** > section « Clés API de Test ».
2. Dans Render (service **paiement**) > Environment, **ajouter** ces variables (les clés de production ne bougent pas) :

   | Variable | Valeur |
   | --- | --- |
   | `PAYDUNYA_TEST_PRIVATE_KEY` | la clé privée de test (`test_private_…`) |
   | `PAYDUNYA_TEST_TOKEN` | le token de test |
   | `PAYDUNYA_TEST_EMAILS` | **ton** adresse (plusieurs : séparées par des virgules) |
   | `PAYDUNYA_TEST_MASTER_KEY` | seulement si la clé principale de test diffère de celle de production |
   | `PAYDUNYA_MODE` | `test` (à ajouter en dernier : c'est l'interrupteur) |

3. Après le redéploiement, le site est en mode test : appels vers l'API bac à sable, bandeau « Mode test » sur la page de
   paiement, et **toute autre adresse que celles de `PAYDUNYA_TEST_EMAILS` est refusée** (sinon un visiteur obtiendrait
   une vraie licence avec un faux paiement). `/health` affiche `"paydunya":"test"`.
4. PayDunya > Intégrer > **Clients fictifs** : créer un client de test, puis commander sur le site avec ton adresse et payer
   avec ce client sur la page de paiement du bac à sable. Vérifier : page de confirmation avec la clé, e-mail reçu,
   connexion au site avec l'e-mail et la clé.
5. **Revenir en production : supprimer `PAYDUNYA_MODE`** (les clés de production n'ont jamais bougé) ; supprimer aussi les
   variables de test si l'on ne compte plus s'en servir. Vérifier que `/health` affiche `"paydunya":"live"` : une
   surveillance par mot-clé sur `"paydunya":"live"` (UptimeRobot) prévient si un oubli laisse le site en test.

La licence obtenue pendant l'essai est une vraie licence pour ton adresse ; elle peut être désactivée ou supprimée avec
`python generate_keys.py`.

## Moyens de paiement annoncés aux clients

Les pages (accueil, FAQ, page de paiement) n'annoncent que ce que PayDunya propose **réellement** à ce compte marchand :
`PAYMENT_COUNTRIES` et `CARDS_ENABLED` dans `config.py`. État contrôlé le 07/10/2026 sur la page de paiement PayDunya :
du Mobile Money dans six pays (Côte d'Ivoire, Sénégal, Bénin, Togo, Burkina Faso, Cameroun), aucune carte bancaire, pays
« Autres » compris. Les clients des autres pays voient un avis sur la page de paiement et sont invités à écrire pour être
prévenus. Ce que PayDunya affiche dépend de la configuration du compte chez PayDunya (Intégrer > ton application) et
de ses activations (cartes internationales, Wave Sénégal), pas du code du site.

Quand PayDunya active les cartes internationales (ou un pays de plus) :

1. Ouvrir une vraie page de paiement PayDunya (commencer un achat sur `/paiement`, sans le terminer) et vérifier que la
   carte (ou le nouveau pays) apparaît, pays « Autres » compris.
2. Passer `CARDS_ENABLED = True` (ou compléter `PAYMENT_COUNTRIES`) dans `config.py`, lancer les tests, envoyer sur `main`.

## Application installable (Android et iPhone)

Le site des clients est une application web installable (PWA) : `pwa.py` fournit le manifeste (`/manifest.webmanifest`),
le service worker (`/sw.js`) et la page `/hors-ligne` ; `static/js/pwa.js` enregistre le service worker et affiche
l'invitation à installer ; `static/icons/` contient les icônes. Le site de paiement n'est pas concerné.

- **Android (Chrome, Samsung Internet)** : l'espace client (et le pied de page des pages publiques) propose un bouton « Installer
  l'application ». L'icône arrive sur l'écran d'accueil et le site s'ouvre en plein écran, sans barre d'adresse. Sur
  ordinateur, le site n'ajoute rien : Chrome et Edge gardent leur propre icône d'installation dans la barre d'adresse.
- **iPhone, iPad (Safari)** : Apple n'a pas de bouton d'installation ; l'invitation explique le geste (Partager, puis
  « Sur l'écran d'accueil »). Les autres navigateurs d'iOS (Chrome, Firefox, Edge) n'ont pas ce menu : aucune invitation.
- **« Plus tard »** masque l'invitation pour de bon sur cet appareil (`localStorage`, clé `tev-install-masque`) ; elle ne
  s'affiche jamais quand le site est déjà ouvert comme une application.
- **Sans connexion** : le téléphone affiche « Pas de connexion » avec un bouton « Réessayer ». Le service worker ne garde
  **que** cette page et ses fichiers (style, polices, icône) : jamais une page de l'espace client ni une réponse de
  l'API (données privées, téléphone parfois prêté). Tout le reste passe par le réseau, comme sans service worker.
- Le lancement de l'application ouvre `/debut` (`pwa.START_URL`, route `lancement` de `dashboard.py`), qui n'affiche rien :
  un client connecté arrive dans son espace, un visiteur sur l'accueil (offres, fonctionnement), pas sur un formulaire de
  connexion qu'il ne peut pas remplir. Un abonnement expiré est traité par l'espace, qui renvoie vers la connexion avec son
  message et le lien « Renouveler ». **Cette adresse est écrite dans l'APK Android** : ne pas la changer sans refaire l'APK ;
  ce qu'elle fait ensuite se règle côté serveur. Si un fichier gardé change (style, icône), le nom du cache change : l'ancien
  est supprimé à l'activation.
- Les icônes sont générées à partir du logo par `python scripts/make_icons.py` (Playwright) et versionnées dans Git ;
  à relancer seulement si le logo change.
- Vérifier : Chrome > outils de développement > Application > Manifest (« Installability »), ou les tests navigateur
  (`TestApplication`), qui interrogent Chromium comme le fait Chrome avant de proposer l'installation.

### Application Android (APK installé à la main, sans Play Store)

L'APK est un habillage du site (Trusted Web Activity) : il affiche le site en ligne, en plein écran. Un changement de
page, de texte ou de prix n'exige **aucun** nouvel APK ; seuls le nom, l'icône, les couleurs ou l'adresse de lancement
en exigent un. Identifiant : `com.tripleelitevip.app`. Lancement : `/debut`.

- **Fabrication (octobre 2026)** : paquet généré sur pwabuilder.com (adresse du site, « Package for stores », « Other
  Android »), qui livre un APK **non signé** (inutilisable tel quel) ; il a été signé avec `apksigner` (outil officiel d'Android)
  et une clé créée avec `keytool`, valable jusqu'en 2054 (RSA 4096). La clé n'a transité ni par PWABuilder ni par une boutique.
- **Versions** : la 1.0 (« version code » 1) s'ouvrait sur `/login` ; la **1.1 (« version code » 2)** s'ouvre sur `/debut` :
  accueil pour un visiteur, espace direct pour un client connecté. La 1.1 est le paquet non signé de la 1.0 dont seuls
  l'adresse de lancement (ressource `launchUrl` et copie du manifeste web) et les numéros de version ont été changés, par des
  chaînes de même longueur donc sans rien décaler d'autre ; elle a été re-signée avec la même clé et s'installe par-dessus la
  1.0. Une reconstruction par PWABuilder donne le même résultat, puisqu'il lit l'adresse de lancement dans le manifeste du site.
- **La clé de signature (fichier `.keystore`) et son mot de passe ne sont JAMAIS dans ce dépôt** : ils sont chez le
  propriétaire, en deux exemplaires, hors de l'ordinateur de travail. Sans elle, il est impossible de publier une mise à jour
  par-dessus l'application installée (les clients devraient la désinstaller puis la réinstaller) et de l'enregistrer
  auprès de Google.
- **Barre d'adresse** : l'application s'ouvre sans barre d'adresse si `/.well-known/assetlinks.json` (route de `pwa.py`)
  contient l'empreinte SHA-256 de la clé. Elle est dans `config.ANDROID_CERT_FINGERPRINTS` (ce n'est pas un secret). Pour
  une seconde clé (celle de Google Play, plus tard), **ajouter** son empreinte sans retirer l'ancienne. Google garde ce
  fichier en mémoire : après une modification, la barre peut mettre de quelques minutes à quelques heures à disparaître.
  Lire l'empreinte d'une clé : `keytool -list -v -keystore clé.keystore` (ligne SHA256).
- **Refaire un APK** (autre icône, autre nom) : PWABuilder avec le même identifiant, « Version code » augmenté de 1 (le
  dernier est le 2), puis
  `zipalign -c 4 fichier.apk` (doit répondre « Verification successful », sinon `zipalign -p 4 entrée sortie`) et
  `apksigner sign --ks clé.keystore --ks-key-alias triple-elite-vip --out Triple-Elite-VIP.apk fichier.apk`,
  puis `apksigner verify --verbose --print-certs Triple-Elite-VIP.apk` : l'empreinte affichée doit être celle de `config.py`.
- **Installer sur un téléphone** : ouvrir le fichier `.apk` (reçu par le chat, WhatsApp, Telegram, câble USB, Drive), autoriser
  « Installer des applications inconnues » pour l'application qui l'ouvre quand Android le demande. Play Protect peut
  afficher un avertissement pour une application qui ne vient pas de Google Play : c'est normal ici.
- **Paiement** : le paiement a lieu sur une autre adresse (`paiement.triple-elite-vip.com`, puis PayDunya) : Android l'ouvre
  dans une fenêtre Chrome par-dessus l'application, avec l'adresse et le cadenas visibles, qu'on ferme pour revenir. C'est
  voulu : pour un paiement, le client doit voir où il paie (et PayDunya renvoie de toute façon vers d'autres sites).
- **Vérification des développeurs par Google** : l'installation directe d'APK n'est pas bloquée aujourd'hui. Google l'impose
  depuis le 30 septembre 2026 dans quatre pays (Brésil, Indonésie, Singapour, Thaïlande) sur les appareils certifiés, et
  prévoit le monde entier en 2027 : enregistrer alors l'application (identifiant + empreinte) dans la console Android
  Developer (25 $, pièce d'identité) avant de distribuer l'APK à des clients. À revérifier : les règles évoluent.

### Version Google Play (pas encore faite)

La version Play sera un habillage de ce site (Trusted Web Activity), construit avec PWABuilder ou Bubblewrap, pas une
seconde application à maintenir. À vérifier au moment de publier, les règles de Google changent :

- Compte développeur Google Play (25 $ une fois, pièce d'identité). Un compte personnel doit d'abord faire un test fermé
  avec au moins 12 testeurs pendant 14 jours ; compter 3 à 4 semaines au total, examen compris.
- Ajouter à `config.ANDROID_CERT_FINGERPRINTS` l'empreinte de la clé de signature fournie par Google Play (« signature
  d'application »), en gardant celle de l'APK direct, pour que l'application s'ouvre sans barre d'adresse ; ainsi qu'une page
  de politique de confidentialité.
- **Paiement** : une application qui vend des abonnements numériques doit passer par Google Play Billing et ne doit pas
  renvoyer vers un paiement extérieur. La version Play ne devra donc proposer ni « S'abonner » ni lien vers le site de
  paiement : seulement la connexion de clients qui ont déjà acheté sur le site. Elle ne pourra donc pas s'ouvrir sur
  l'accueil du site (qui propose « S'abonner »), comme l'APK direct : prévoir pour elle une adresse de lancement et des
  pages à part.
- **Jeux d'argent** : les pronostics ne sont pas nommés dans la politique de Google, mais ce qui « facilite » les paris est
  encadré. Zone grise : risque de refus, voire de suspension du compte. Poser la question au support de la politique Play
  avant d'investir du temps.
- Une application qui n'est qu'un site peut être refusée pour manque de fonctions propres : ajouter des notifications
  (nouveaux combinés, abonnement bientôt terminé) réduit ce risque.

## Dépannage

Les journaux sont dans Render > le service > **Logs** (les e-mails y sont masqués : `j***@gmail.com`).

| Symptôme | Où regarder, que faire |
| --- | --- |
| Un client a payé mais ne reçoit rien | Chercher son e-mail (masqué) dans les logs du service de paiement. `PAIEMENT CONFIRMÉ MAIS LICENCE NON DÉLIVRÉE` : la base était injoignable, le client peut réactualiser sa page de confirmation ; sinon créer sa licence à la main avec `python generate_keys.py` (même e-mail que l'achat). Si PayDunya montre le paiement `completed`, il est dû |
| Aucun e-mail de licence n'arrive | Logs du service de paiement : `e-mail de licence NON envoyé` suivi de la cause (`connexion SMTP impossible` : port bloqué ; `Brevo` : réponse de l'API ; variables absentes). Sur l'offre gratuite de Render, Gmail est bloqué : définir `BREVO_API_KEY` ou rester sur une offre payante. `Gmail a refusé l'identifiant ou le mot de passe d'application` : le mot de passe d'application a été révoqué ou mal collé (les espaces que Google affiche entre les blocs de quatre lettres sont ignorées, inutile de les retirer). Le client voit sa clé sur la page de confirmation |
| « Le paiement est momentanément indisponible » | Clés PayDunya absentes ou invalides, ou PayDunya en panne : logs du service de paiement (`facture PayDunya impossible`). `code '1001', 'The payin is not enabled'` : la case **Payin** de l'application est décochée chez PayDunya (Intégrer > ton application > Modifier > Services : cocher Payin ; Payout n'est pas utilisé) ; si elle est déjà cochée, le compte n'est peut-être pas encore validé : écrire au support PayDunya |
| Un client dit qu'il ne trouve aucun moyen de payer chez PayDunya (son pays n'a pas de Mobile Money, pas de carte) | Ce n'est pas un défaut du site : PayDunya n'affiche que les moyens activés sur le compte marchand. Voir « Moyens de paiement annoncés aux clients » ; en attendant, la page de paiement le dit et invite à écrire |
| « Les paiements ne sont pas encore ouverts » | Le site est en mode test (clés de test) : remettre les clés de production, voir « Essayer le paiement sans argent » |
| « Service momentanément indisponible » à la connexion | La base PostgreSQL est injoignable ou a expiré (offre gratuite) : Render > Postgres |
| « E-mail ou clé de licence incorrect » alors que le client a bien sa clé | Le visiteur voit toujours ce même message ; le motif exact est dans les logs du service client : `connexion refusée : j***@… (motif)`. `e-mail inconnu` : aucune licence à cette adresse **dans la base de ce service** (faute de frappe dans l'adresse, ou les deux services ne pointent pas vers la même `DATABASE_URL` : au démarrage, chaque service écrit `Base de données des licences initialisée (base <nom>, N licence(s))`, et `python generate_keys.py` l'affiche en tête ; le nom doit être identique des deux côtés). `clé différente (N caractères saisis, 16 attendus)` : clé mal recopiée (le site accepte déjà espaces, majuscules, caractères invisibles, la lettre O pour 0 et I ou L pour 1). Vérifier avec `python generate_keys.py` puis `2` (liste des licences) depuis le Shell du service client, et renvoyer la clé par e-mail avec l'option proposée après `1`. `N lignes pour cet e-mail` à la fin du motif : doublons hérités (`Client@…` et `client@…`) ; la bonne clé fonctionne malgré tout, mais nettoyer avec `4` (supprimer) puis `1` (recréer) |
| La génération échoue ou « L'analyse IA est momentanément indisponible » | Logs du service client : clé `ANTHROPIC_API_KEY`, crédit du compte Anthropic, nom du modèle (`modèle introuvable` : un modèle de repli est essayé) |
| Pas assez de matchs à venir | Trêve internationale ou calendrier incomplet chez TheSportsDB ; les cinq championnats compensent en général. Réessayer plus tard |
| Les cotes sont toutes précédées de « ≈ » | `ODDS_API_KEY` absente, ou quota de the-odds-api atteint : c'est normal, ce sont des cotes estimées |
| La page « Combiné gratuit » est vide | Normal tant qu'aucune génération récente n'existe (voir « Pages publiques ») : générer depuis l'espace client, ou activer `AUTO_GENERATE_HOUR`. Si une génération récente existe et que la page reste vide : tous ses combinés ont un match qui commence dans moins de 15 minutes, ou `FREE_PICK_ENABLED=0` |
| `/resultats` répond « momentanément indisponibles » | Les fichiers d'historique sont illisibles (disque `DATA_DIR`) : logs du service client, ligne `vitrine : bilan public impossible à calculer`. La page revient seule dès que l'historique est lisible ; un bilan déjà calculé reste affiché malgré une panne |
| Les résultats n'avancent pas | Les scores sont récupérés chez TheSportsDB en arrière-plan, au plus tous les quarts d'heure et 12 matchs à la fois ; un match dont le score n'est pas encore publié est revérifié toutes les 30 minutes. Tant que le score manque, le combiné reste « en attente » et n'est pas détaillé. Cherche `vitrine : récupération des scores impossible` dans les logs |
| L'aperçu d'un lien collé dans WhatsApp est vieux ou absent | Les messageries gardent l'aperçu en mémoire un moment. L'image `og:image` doit répondre 200 (`/static/images/partage.png`). Une nouvelle image reçoit seule une nouvelle adresse (empreinte) |
| L'historique est vide après un déploiement | Le disque persistant n'est pas monté sur `DATA_DIR` : voir la liste de contrôle ci-dessus |

## Sécurité en bref

Sessions en cookies `HttpOnly`, `SameSite=Lax` et `Secure` ; politique CSP stricte (aucun script ni style en ligne) ;
contrôle de l'origine des envois de formulaire et en-tête `X-Requested-With` sur l'API ; limitation des essais de
connexion (par e-mail) et des créations de facture (par e-mail, adresse IP et globalement) ; adresses e-mail masquées
dans les journaux ; messages d'erreur sans détail technique ; licence revérifiée en base à chaque requête protégée
(avec une courte tolérance si la base est momentanément injoignable).

## Limites connues

- Le paiement réel (PayDunya en production, retour du client, appel `/ipn`) est couvert par des tests simulés : il doit
  être essayé en mode test (voir plus haut), puis une fois avec un vrai paiement de l'offre mensuelle.
- Les résultats publics valent ce que valent les scores disponibles chez TheSportsDB : un match dont le score n'est jamais publié
  garde son combiné « en attente » (il n'est ni compté ni affiché), et le bilan lit les 1000 dernières générations (la page dit « depuis le … »).
- La qualité des pronostics dépend des données de TheSportsDB (calendriers parfois incomplets) et de l'estimation de
  l'IA ; le site affiche donc des probabilités, pas des certitudes.
- Les conditions du site (`/conditions`) décrivent le service tel qu'il est ; les conditions générales de vente, les
  mentions légales et la politique de remboursement doivent être relues par un juriste avant de vendre à grande échelle.
