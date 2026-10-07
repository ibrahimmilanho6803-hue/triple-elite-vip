# Triple Elite VIP

Site d'abonnement qui analyse les cinq grands championnats de football (Premier League, La Liga, Bundesliga,
Ligue 1, Serie A) avec l'IA Claude et compose **trois combinés de trois matchs**, de cote totale d'au moins 2,50.
Les clients paient par Mobile Money ou carte (PayDunya), reçoivent une clé de licence, et se connectent à leur espace.

- Site des clients : <https://triple-elite-vip.com> (service `dashboard`)
- Paiement : <https://triple-elite-vip-paiement.onrender.com> (service `paiement`)
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

## Les fichiers

| Fichier | Rôle |
| --- | --- |
| `dashboard.py` | Site des clients : accueil, conditions, connexion, espace client, API de génération et d'historique |
| `paiement.py`, `paydunya.py` | Site de paiement et client de l'API PayDunya |
| `license_manager.py` | Licences et commandes dans PostgreSQL (délivrance atomique, renouvellements cumulés) |
| `email_sender.py` | Envoi de la clé : Brevo (HTTPS) ou Gmail (SMTP), avec nouvelles tentatives |
| `generation_service.py`, `pipeline.py` | Génération en arrière-plan : une à la fois, cache, plafond quotidien, avancement |
| `data_collector.py`, `analyzerv2.py` | Données TheSportsDB, analyse IA (modèles de repli si un nom de modèle n'existe pas) |
| `probabilities.py`, `markets.py`, `combo_generator.py` | Probabilités, catalogue des paris et verdicts, composition des combinés et cotes |
| `combo_history.py` | Historique : résultats réels des matchs joués, bilan |
| `web_common.py`, `privacy.py` | Sécurité commune (en-têtes, anti-CSRF, limitation d'essais), gabarits, e-mails masqués dans les journaux |
| `config.py` | Réglages non secrets ; lit aussi un fichier `.env` en local |
| `templates/`, `static/` | Pages et styles (sans script ni style en ligne), JavaScript du site |
| `main.py`, `generate_keys.py`, `scripts/send_test_email.py` | Outils en ligne de commande (voir plus bas) |
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
| `PAYDUNYA_MASTER_KEY`, `PAYDUNYA_PRIVATE_KEY`, `PAYDUNYA_TOKEN` | paiement | Clés API PayDunya |
| `PAYDUNYA_TEST_EMAILS` | paiement | Facultatif, **mode test seulement** : adresses autorisées à commander, séparées par des virgules (voir plus bas) |
| `LICENSE_SECRET_KEY` | paiement | Sel de fabrication des nouvelles clés (en changer n'invalide aucune clé existante) |
| `GMAIL_EMAIL`, `GMAIL_MDP` | paiement | Envoi par Gmail (mot de passe d'application) |
| `BREVO_API_KEY`, `EMAIL_SENDER` | paiement | Envoi par Brevo (prioritaire sur Gmail si définie) ; `EMAIL_SENDER` = adresse d'expéditeur validée |
| `SITE_URL`, `PAIEMENT_BASE_URL` | les deux | Adresses publiques (valeurs par défaut correctes) |
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
python -m pytest tests/e2e -q             # parcours réels dans Chromium : connexion, génération, historique, paiement
```

Les tests ne contactent jamais TheSportsDB, PayDunya, Anthropic ni un serveur d'e-mail : tout est simulé. Les tests
navigateur sont ignorés automatiquement si Playwright ou Chromium n'est pas installé.
Pour regarder le rendu : `python tests/e2e/harness.py captures` écrit des captures d'écran (ordinateur et téléphone).

## Outils en ligne de commande

```bash
python main.py --save                        # génère les combinés sans passer par le site (--save : les ajoute à l'historique)
python generate_keys.py                      # crée, prolonge ou désactive une licence à la main (cadeau, geste commercial)
python scripts/send_test_email.py moi@exemple.com   # vérifie que l'envoi d'e-mail fonctionne
```

`generate_keys.py` et `send_test_email.py` utilisent les mêmes variables que le site (`DATABASE_URL`, `GMAIL_*` ou `BREVO_*`).

## Déploiement sur Render

Les services actuels ont été créés à la main : **les réglages du Dashboard font foi**, pas `render.yaml` (qui documente
la cible). Liste de contrôle :

- **Offres** (relevé du 6 octobre 2026) : les deux services sont sur l'offre à 7 $/mois (0,5 CPU, 512 Mo), région
  Frankfurt, branche `main`, déploiement automatique à chaque envoi sur `main`.
- **Start Command** : `gunicorn dashboard:app --bind 0.0.0.0:10000 --timeout 240 --graceful-timeout 240` et
  `gunicorn paiement:app --bind 0.0.0.0:10000` (chaque service a son propre port chez Render). `gunicorn.conf.py` ajoute
  des fils d'exécution (pages réactives pendant une génération) sans rien changer côté Render.
- **Health Check Path** : `/health` sur les deux services.
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
sable » : les paiements y sont fictifs. Le site la reconnaît tout seul :

1. PayDunya > **Intégrer** > ton application > **Afficher les clés API** > « Clés API de Test ». La clé principale
   (Master Key) est en général la même qu'en production (si la section de test en affiche une autre, la remplacer aussi) ;
   la **clé privée** et le **token** changent.
2. Dans Render (service **paiement**), remplacer `PAYDUNYA_PRIVATE_KEY` et `PAYDUNYA_TOKEN` par les valeurs de test, et
   définir `PAYDUNYA_TEST_EMAILS` avec **ton** adresse. Attendre la fin du redéploiement.
3. Le site passe alors en mode test : appels vers l'API bac à sable, bandeau « Mode test » sur la page de paiement, et
   **toute autre adresse que celles de `PAYDUNYA_TEST_EMAILS` est refusée** (sinon un visiteur obtiendrait une vraie
   licence avec un faux paiement). `/health` affiche `"paydunya":"test"`.
4. PayDunya > Intégrer > **Clients fictifs** : créer un client de test, puis commander sur le site avec ton adresse et payer
   avec ce client sur la page de paiement du bac à sable. Vérifier : page de confirmation avec la clé, e-mail reçu,
   connexion au site avec l'e-mail et la clé.
5. **Remettre les clés de production**, supprimer `PAYDUNYA_TEST_EMAILS`, et vérifier que `/health` affiche
   `"paydunya":"live"` (une surveillance par mot-clé sur `"paydunya":"live"` prévient si un oubli laisse le site en test).

La licence obtenue pendant l'essai est une vraie licence pour ton adresse ; elle peut être désactivée avec
`python generate_keys.py`.

## Dépannage

Les journaux sont dans Render > le service > **Logs** (les e-mails y sont masqués : `j***@gmail.com`).

| Symptôme | Où regarder, que faire |
| --- | --- |
| Un client a payé mais ne reçoit rien | Chercher son e-mail (masqué) dans les logs du service de paiement. `PAIEMENT CONFIRMÉ MAIS LICENCE NON DÉLIVRÉE` : la base était injoignable, le client peut réactualiser sa page de confirmation ; sinon créer sa licence à la main avec `python generate_keys.py` (même e-mail que l'achat). Si PayDunya montre le paiement `completed`, il est dû |
| Aucun e-mail de licence n'arrive | Logs du service de paiement : `e-mail de licence NON envoyé` suivi de la cause (`connexion SMTP impossible` : port bloqué ; `Brevo` : réponse de l'API ; variables absentes). Sur l'offre gratuite de Render, Gmail est bloqué : définir `BREVO_API_KEY` ou rester sur une offre payante. `Gmail a refusé l'identifiant ou le mot de passe d'application` : le mot de passe d'application a été révoqué ou mal collé. Le client voit sa clé sur la page de confirmation |
| « Le paiement est momentanément indisponible » | Clés PayDunya absentes ou invalides, ou PayDunya en panne : logs du service de paiement (`facture PayDunya impossible`). `code '1001', 'The payin is not enabled'` : le compte PayDunya n'est pas encore validé (tableau de bord PayDunya : « compte en cours de validation ») ; rien à corriger sur le site, écrire au support PayDunya |
| « Les paiements ne sont pas encore ouverts » | Le site est en mode test (clés de test) : remettre les clés de production, voir « Essayer le paiement sans argent » |
| « Service momentanément indisponible » à la connexion | La base PostgreSQL est injoignable ou a expiré (offre gratuite) : Render > Postgres |
| La génération échoue ou « L'analyse IA est momentanément indisponible » | Logs du service client : clé `ANTHROPIC_API_KEY`, crédit du compte Anthropic, nom du modèle (`modèle introuvable` : un modèle de repli est essayé) |
| Pas assez de matchs à venir | Trêve internationale ou calendrier incomplet chez TheSportsDB ; les cinq championnats compensent en général. Réessayer plus tard |
| Les cotes sont toutes précédées de « ≈ » | `ODDS_API_KEY` absente, ou quota de the-odds-api atteint : c'est normal, ce sont des cotes estimées |
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
- La qualité des pronostics dépend des données de TheSportsDB (calendriers parfois incomplets) et de l'estimation de
  l'IA ; le site affiche donc des probabilités, pas des certitudes.
- Les conditions du site (`/conditions`) décrivent le service tel qu'il est ; les conditions générales de vente, les
  mentions légales et la politique de remboursement doivent être relues par un juriste avant de vendre à grande échelle.
