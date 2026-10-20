# Triple Elite VIP

Site d'abonnement qui analyse douze compétitions de football (Premier League, La Liga, Bundesliga, Ligue 1, Serie A, Ligue des
champions, et les championnats de Russie, des Pays-Bas, du Portugal, de Belgique, de Turquie et du Danemark) avec l'IA Claude et compose **trois combinés de trois matchs**, de cote totale d'au moins 2,50, avec les
**matchs du jour** (moins de combinés les petits jours ; aucun un jour sans championnat : « Pas de championnat aujourd'hui »,
voir « Matchs du jour et générations des clients »).
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
   à partir d'un **dossier** que le code prépare pour chaque match : niveau des deux équipes, résultats à domicile et à
   l'extérieur, forme récente pesée par la force des adversaires, repos, confrontations directes, repère statistique (voir
   « L'analyse de l'IA : un dossier par match »). Elle n'a ni les compositions ni les blessés : la source de données n'en fournit pas.
2. Le code rend ces estimations **cohérentes** entre elles (un « plus de 2,5 buts » ne peut pas être plus probable
   qu'un « plus de 1,5 »), et calcule les paris composés (« victoire ET plus de 1,5 but ») par produit des probabilités.
3. La confiance est **plafonnée selon la quantité de données** : moins de 4 matchs connus pour une équipe, jamais plus
   de 60 % ; de 4 à 7 matchs, 72 % ; de 8 à 14, 82 % ; 15 ou plus, 90 %.
4. Seuls les paris à 65 % ou plus **et payés au moins 1,25** (`MIN_LEG_ODDS`) sont gardés : un pari « presque sûr » à 1,03 ne se
   joue pas, même quand l'IA a raison. Les combinés sont choisis par **probabilité de réussite**, avec un peu de variété
   (championnats, types de paris).
5. Les **cotes** sont celles de 1xBet, le bookmaker des clients (voir « Les cotes 1xBet ») : si `ODDS_API_KEY` est définie et que
   1xBet cote le pari, sa cote s'affiche **sans signe**. Sinon la cote est indicative et porte un « ≈ » : la médiane des autres
   bookmakers quand ils cotent le pari mais pas 1xBet, ou, quand personne ne le cote, une estimation `(1 − 7 %) / probabilité`
   (la probabilité est celle que l'IA a réellement estimée, **avant** le plafond du point 3 : le plafond limite la confiance
   affichée, il ne rend pas un pari plus payant). Sans clé de cotes, la cote
   totale de 2,50 est donc une estimation, pas une cote jouable : le coupon invite le client à la vérifier. Même avec une clé,
   the-odds-api ne cote que le résultat (victoire, double chance) et les totaux de buts du match : les paris « une équipe marque
   plus de 0,5 but », « au moins une équipe marque… », « les deux équipes marquent » et les paris composés gardent toujours leur
   « ≈ », même dans un combiné dont d'autres paris n'en ont pas. Le match est retrouvé chez the-odds-api par le nom des équipes,
   sans tenir compte des accents ni des tirets (« İstanbul Başakşehir » = « Istanbul Basaksehir »).

## Parcours d'un client

Avant d'acheter, un visiteur peut consulter les résultats réels et un combiné gratuit (voir « Pages publiques »).

1. Il choisit une offre sur `/paiement` : Pass 7 jours (2 000 FCFA), mensuel (6 000 FCFA, tarif de lancement) ou annuel
   (39 400 FCFA). Les prix sont en FCFA, la monnaie du paiement ; l'équivalent en euros n'est qu'indicatif (voir « Prix et offres »).
2. `/payer` crée la facture PayDunya et mémorise la commande **avant** la redirection.
3. Après paiement, PayDunya le renvoie sur `/succes?token=…`. Le site demande à PayDunya de confirmer, délivre la
   licence (une seule fois, même si la page est rechargée), l'**affiche** et l'envoie par e-mail.
4. Filet de sécurité : PayDunya appelle aussi `/ipn` ; la même logique y est appliquée, donc la licence est délivrée
   même si le client ferme son navigateur. Le contenu de cet appel n'est jamais cru : seul le jeton compte, et c'est
   PayDunya qui est interrogé. Le rappel (`callback_url`) est envoyé avec chaque facture : rien à régler chez PayDunya
   (l'adresse `/ipn-paydunya`, enregistrée dans l'application PayDunya, répond aussi).
5. Un client qui renouvelle **avant** la fin de son abonnement garde sa clé ; la durée achetée s'ajoute au temps restant.
   Après expiration, une nouvelle clé est délivrée. Le pass compte 7 jours, un mois 30 jours, un an 365 jours.
6. Il se connecte sur le site avec son e-mail et sa clé, puis génère SES combinés (un calcul en arrière-plan pour lui, affiché
   chez lui seul pendant 3 heures ; **2 générations par jour et par client** ; voir « Les combinés de chaque client, la variété
   et les analyses partagées ») et consulte l'historique avec le score de chaque match.
7. Il peut installer le site sur son téléphone comme une application (voir « Application installable »).

## Pages publiques : résultats et combiné gratuit

Deux pages ouvertes à tous (et aux moteurs de recherche) montrent le produit tel qu'il est, sans maquillage, avant tout
paiement. L'accueil (section « Nos résultats sont publics »), la barre de navigation du bas et le pied de page y mènent.

- `/resultats` : le bilan réel (combinés gagnés sur joués, pronostics gagnés sur joués, chance annoncée en moyenne face à la
  réussite constatée) et le détail des 12 derniers combinés **entièrement joués**, gagnés comme perdus, avec le score de chaque
  match. Il se met à jour tout seul à mesure que les scores arrivent.
- `/gratuit` : **un seul** combiné offert, de trois matchs du jour (celui de plus haute chance estimée dans la génération automatique
  du jour, jamais dans celle d'un abonné),
  avec cote, confiance de chaque pronostic et chance estimée, et des boutons WhatsApp, Telegram et « Copier le lien ». Un jour sans
  championnat il n'y en a pas, et la page répond « Pas de championnat aujourd'hui ».

Règles qui commandent le code (`showcase.py` ; vérifiées par `tests/test_showcase.py`, `tests/test_public_pages.py` et les tests
navigateur `TestResultatsEtCombineGratuit`) :

1. **Jamais un combiné à venir en public, sauf le combiné gratuit.** Les deux autres combinés d'une génération sont le produit
   payant. `/resultats` ne détaille que des combinés dont tous les matchs sont joués. Un combiné déjà perdu mais dont des matchs
   restent à jouer compte tout de suite dans le bilan (une défaite connue ne se cache pas), et ne s'affiche qu'à la fin. Chaque
   dictionnaire envoyé aux pages est construit champ par champ (liste blanche) : un champ ajouté plus tard aux combinés enregistrés
   ne devient pas public par accident.
2. **Un visiteur ne coûte rien.** Une page publique ne lance jamais de génération (appel payant à l'IA : elle lit seulement la
   dernière génération automatique, `GenerationService.latest`) et ne fait aucun appel réseau pendant la requête. Les scores manquants sont récupérés en
   arrière-plan, au plus tous les quarts d'heure (`combo_history` borne en plus chaque série d'appels à TheSportsDB). Une panne de
   l'historique ne casse ni l'accueil ni le combiné gratuit : `/resultats` répond alors « momentanément indisponibles » (503).
3. **Aucun chiffre trompeur.** Un pourcentage n'apparaît qu'à partir de 10 combinés joués (30 pronostics pour celui des pronostics) ;
   en dessous, seuls les nombres bruts s'affichent, et l'accueil n'affiche pas de chiffres du tout. Le bilan compte des pronostics
   gagnés ou perdus, jamais des gains en argent (les cotes sont souvent estimées : un « bénéfice » serait inventé). Un pronostic
   présent dans plusieurs combinés compte une fois ; les matchs reportés ou annulés ne comptent pas. Le pied de page garde
   l'avertissement sur les paris et l'interdiction aux moins de 18 ans.
4. **Le combiné gratuit est stable, et du jour.** Il est choisi une fois par génération automatique et enregistré (`free_pick.json`, dossier
   de cache) : si une autre génération sort dans la journée, le lien partagé le matin montre toujours le même coupon le soir. Il
   ne vient que d'une génération composée avec les matchs d'**aujourd'hui** (jour UTC, champ `day` de ses métadonnées) : celle
   d'hier, ou d'avant « matchs du jour seulement », n'en fournit plus. Il disparaît 15 minutes avant le premier match, et au plus
   tard à minuit UTC. Il faut alors une **nouvelle génération** pour en avoir un autre : les deux autres combinés de l'ancienne
   ne prennent pas sa place.

**La page « Combiné gratuit » est vide** tant que la génération automatique du jour n'a pas eu lieu (ou que tous les matchs ont
commencé) : elle l'explique au visiteur et renvoie vers les résultats. Le clic d'un abonné sur « Générer » n'y change rien : ses
combinés sont les siens. Quand la vérification des matchs du jour a conclu qu'il n'y a rien à composer, elle affiche à la place « Pas de
championnat aujourd'hui » (ou « Les matchs d'aujourd'hui ont déjà commencé », « Trop peu de matchs aujourd'hui »). Elle n'est
alimentée que par la génération automatique : sans `AUTO_GENERATE_HOUR`, elle reste vide.

**Génération automatique quotidienne (désactivée par défaut)** : `AUTO_GENERATE_HOUR=6` (Render > service dashboard > Environment ;
heure **UTC**, de 0 à 23) lance chaque jour, à partir de cette heure, une génération s'il n'y en a pas déjà eu une pour les matchs
de ce jour-là (`daily_generation.py`). C'est un appel payant à l'IA (de l'ordre de 0,2 $ l'unité, à vérifier sur la console Anthropic :
environ 6 $ par mois à raison d'une par jour de match). Un jour sans match (ou avec moins de trois matchs à venir) elle ne lance rien
et ne coûte rien. Elle passe par les mêmes garde-fous que le bouton « Générer » (un seul travail à la fois pour elle, combinés
encore frais réutilisés, plafond du jour, pause après un échec) et s'arrête après 3 tentatives dans la journée. Supprimer la variable la
désactive. La boucle ne démarre qu'à la **première requête** reçue après chaque démarrage du serveur (la surveillance de Render la
fournit dans les secondes qui suivent), jamais à la création de l'application : sous gunicorn, un fil de fond lancé avant la création
du processus qui répond aux visiteurs laisse à celui-ci des appels HTTPS bloqués pour toujours, sans erreur ni délai dépassé (c'est
ce qui avait figé « Générer les combinés » le 11 octobre 2026). Mécanisme reproduit en laboratoire : un fil qui fait des appels TLS
au moment du fork laisse des verrous d'OpenSSL pris dans la copie, où la création d'un contexte TLS ou la poignée de main reste
alors coincée (environ 3 démarrages sur 100, jamais sans fil de fond). Dans les journaux, `génération automatique activée` apparaît
donc après le démarrage du serveur, pas avant.

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

## Matchs du jour et générations des clients

**Les combinés ne portent que sur les matchs d'aujourd'hui** : ceux de nos douze compétitions dont le coup d'envoi tombe dans le
jour **UTC** en cours (minuit UTC = 1 h du matin à Lagos, minuit à Abidjan) et qui n'ont pas encore commencé.
`DataCollector.get_matchday()` les cherche en douze appels gratuits à TheSportsDB (un par compétition) et les rend **tous**, classés par
heure de coup d'envoi dans chaque compétition ; une génération en retient au plus 18 (`MATCHS_PAR_GENERATION`, voir « Les combinés de
chaque client, la variété et les analyses partagées »). C'est la première étape de la chaîne (`pipeline.run_pipeline`), avant toute
collecte et tout appel payant à l'IA.

**Compétitions suivies** (`config.LEAGUES`, nom affiché et identifiant TheSportsDB) : Premier League (4328), La Liga (4335),
Bundesliga (4331), Ligue 1 (4334), Serie A (4332), Ligue des champions (4480), Premier-Liga de Russie (4355), Eredivisie des
Pays-Bas (4337), Primeira Liga du Portugal (4344), Pro League de Belgique (4338), Süper Lig de Turquie (4339), Superliga du Danemark (4340). Ajouter une
compétition = une ligne dans `LEAGUES`, une dans `LEAGUE_CARDS` (la carte de l'accueil) et une dans
`ComboGenerator.LEAGUE_TO_ODDS_SPORT` (le nom du sport chez the-odds-api, pour les vraies cotes) ; un test vérifie que les trois
listes restent d'accord, et les textes du site (« douze compétitions ») suivent tout seuls. Le nom affiché sert de clé partout (base des
matchs, historique, cotes) : le changer demande de le changer aussi dans ces trois listes.

Il faut au moins **3 matchs à venir** (`pipeline.MIN_MATCHES`). En dessous, rien n'est généré, l'IA n'est pas appelée, et le site
répond, dans l'espace client comme sur `/gratuit` :

| Situation | Message |
| --- | --- |
| Aucun match de nos douze compétitions aujourd'hui | « Pas de championnat aujourd'hui » |
| Des matchs, mais tous ont déjà commencé | « Les matchs d'aujourd'hui ont déjà commencé » |
| Un ou deux matchs encore à venir | « Trop peu de matchs aujourd'hui » |

- **Un constat gardé pour la journée** (`matchday.json`, dossier de cache). Le nombre de matchs vus ne baisse jamais dans la journée
  (TheSportsDB ne liste plus un match commencé) : c'est lui qui distingue « pas de championnat » de « tous ont commencé ». Un
  constat négatif est revérifié au plus toutes les 30 minutes (un calendrier peut se compléter), après une panne de TheSportsDB au
  bout de 5 minutes. **Jamais de « pas de championnat » sur une réponse incomplète** : si un championnat n'a pas répondu, c'est une
  panne à réessayer (« Les matchs du jour sont momentanément indisponibles »), pas un verdict.
- **Des attentes bornées.** Chaque appel à TheSportsDB attend au plus 10 secondes, et l'ensemble des appels d'une étape au plus
  25 secondes (`DataCollector.FETCH_DEADLINE`) : un appel qui ne revient pas est abandonné et compte comme une panne, sans bloquer
  la génération. Un championnat qui n'a pas répondu est réessayé une fois (15 secondes de plus). Si une analyse dure malgré tout
  plus de 5 minutes (`JOB_MAX_SECONDS`), elle est déclarée perdue : **le clic du client lui est rendu**, et les journaux disent à
  quelle étape elle en était et ce que faisait son fil (`génération perdue (…)`). Les clés d'API ne sont jamais écrites dans les
  journaux (les messages d'erreur réseau les contiennent : elles sont remplacées par `***`).
- **Qui fait ce constat.** La boucle quotidienne (`daily_generation.py`, active seulement si `AUTO_GENERATE_HOUR` est définie) le fait
  dès minuit UTC, avant l'heure de génération : « Pas de championnat aujourd'hui » s'affiche sans attendre personne. Sans elle, il se
  fait au premier clic d'un abonné sur « Générer » (qui ne lui coûte rien) ; d'ici là `/gratuit` dit seulement que le combiné n'est
  pas encore prêt.
- **Pour le client** : le bouton « Générer les combinés » est remplacé par le message (`/api/generate` répond `no_match`). Rien n'est
  compté, ni dans le plafond du jour (commun à tous les clients, `MAX_GENERATIONS_PAR_JOUR`), ni dans les générations du client.
- **Moins de trois combinés** : un petit jour de championnat (trois ou quatre matchs) peut ne donner qu'un ou deux combinés. Le site
  en affiche autant qu'il en trouve.

**Deux générations par jour et par client.** `CLIENT_GENERATIONS_PER_DAY = 2` (`config.py`), par e-mail et par jour UTC.

- Le compteur est une table PostgreSQL, `generation_usage` (`email`, `day`, `used`), créée toute seule au démarrage (les lignes de plus
  de 30 jours y sont effacées) : il survit aux redéploiements. La réservation est **atomique** (`INSERT … ON CONFLICT … WHERE used <
  limite`) : deux clics simultanés, ou deux onglets, ne dépassent jamais la limite.
- **Seul le clic qui démarre vraiment une analyse en consomme une.** Ne coûtent rien : réafficher ses combinés encore frais (3 h),
  rejoindre sa propre analyse déjà en cours (un second onglet), un jour sans match, et une analyse qui n'aboutit à aucun combiné (panne de
  l'IA, rien d'assez fiable) : le clic est alors **rendu** (`release_generation`). Tant que des combinés frais existent, un client qui a
  épuisé ses générations les voit quand même.
- Le client lit « Tu peux lancer 2 générations par jour », puis « Il te reste 1 génération aujourd'hui (2 par jour) » ; une fois
  épuisées, le bouton est grisé avec « Tu as utilisé tes 2 générations d'aujourd'hui. Reviens demain pour en lancer de nouvelles. »
- **La deuxième génération se fait sans attendre l'expiration des premiers combinés** (version 2.4.2). Avant, les combinés restaient
  affichés 3 heures (ou jusqu'au premier coup d'envoi) et le bouton « Générer » n'était plus là : un client ne pouvait pas utiliser sa
  deuxième génération du jour. Sous les combinés, il y a maintenant **« Générer de nouveaux combinés »**, avec le nombre de générations
  qui lui restent. Il demande confirmation (« Cela compte pour 1 de tes générations du jour… Les combinés affichés seront remplacés,
  mais tu les retrouves dans l'onglet Historique »), puis une vraie analyse repart. Côté serveur : `POST /api/generate/again` (mêmes
  protections que `/api/generate`) ; `GenerationService.status(client, again=True)` ignore les combinés frais du client.
  - **Même compte que le premier clic** : seul le clic qui démarre une analyse en consomme une ; deux onglets ne lancent qu'une analyse ;
    la troisième demande est refusée (`quota`) sans aucun appel payant, et les combinés restent affichés avec le message.
  - **Un échec ne fait rien perdre.** Si la nouvelle analyse n'aboutit pas (panne de l'IA, trop peu de matchs), le clic est rendu et
    **les combinés d'avant restent affichés**, avec la raison : `again_error` (panne, en rouge) ou `notice` (pas assez de matchs, en
    neutre) dans la réponse de `/api/generate/status`, pendant la pause de 2 minutes qui suit l'échec. Pendant cette pause, un nouveau
    clic ne relance rien.
  - **Les premiers combinés ne sont pas perdus** : chaque génération est rangée dans l'historique du client (`results/combo_<date>_<identifiant>.json`).
  - Si le plafond d'analyses du jour (commun à tous) est atteint, les combinés du client restent affichés avec l'avis habituel et le bouton
    disparaît ; son clic n'est pas compté.
- **Si la base est injoignable**, personne n'est bloqué à tort : l'analyse est autorisée sans être comptée (le plafond du jour, commun à
  tous, borne de toute façon la dépense).
- Changer la limite : `CLIENT_GENERATIONS_PER_DAY` dans `config.py` (textes et décompte suivent), puis envoyer sur `main`. Voir ou
  remettre à zéro le compteur d'un client (geste commercial), depuis le Shell de la base (jour UTC, e-mail en minuscules) :

```sql
SELECT email, used FROM generation_usage WHERE day = '2026-10-12';
DELETE FROM generation_usage WHERE email = 'client@exemple.com' AND day = '2026-10-12';
```

## Les combinés de chaque client, la variété et les analyses partagées

Depuis la version 2.3.0, **chaque client a ses propres combinés**. Avant, tous les abonnés se partageaient la dernière génération : les
combinés qu'un client venait de lancer s'affichaient chez tous les autres. Maintenant :

- **Un résultat par client** (`generation_service.py`). Chaque génération est rangée sous une « portée » : l'identifiant opaque du
  client (`privacy.client_scope` : 16 caractères tirés de son e-mail par SHA-256, jamais l'adresse elle-même) ou, pour la génération
  automatique de `AUTO_GENERATE_HOUR`, « la maison ». Chaque portée a son travail en cours, son résultat (`job_<identifiant>.json`,
  `last_generation_<identifiant>.json` dans le dossier de cache), sa pause après un échec et son clic rendu. Les fichiers d'un client
  non touchés depuis 2 jours sont effacés.
- **Avant d'avoir cliqué, un client ne voit que le bouton « Générer »**, jamais les combinés d'un autre. Après son clic, il retrouve ses
  combinés pendant `CACHE_MINUTES` (3 heures) ou jusqu'au premier coup d'envoi. Un second clic du même client (autre onglet) rejoint
  son travail ; le clic d'un autre client lance le sien et lui coûte sa propre génération du jour.
- **La génération automatique alimente seulement** le combiné gratuit, les résultats publics et l'historique (`GenerationService.latest`).
- **L'historique d'un client** (`combo_history.load_history(viewer=…)`). Le fichier de chaque génération d'un client porte son
  identifiant (`results/combo_AAAAMMJJ_HHMMSS_<identifiant>.json` ; celui de la génération automatique n'en a pas). L'onglet
  « Historique » lui montre ses propres combinés, joués ou non, et ceux des autres seulement quand **tous** leurs matchs ont commencé :
  un combiné à venir d'un autre abonné n'y apparaît jamais. Deux générations qui donnent exactement le même combiné ne font qu'une
  ligne, visible de chacun de leurs propriétaires. Les pages publiques lisent l'historique sans filtre, mais leurs règles (voir « Pages
  publiques ») ne montrent jamais un combiné à venir hors le combiné gratuit.

**Des équipes qui ne se répètent pas** (`variety.py`, `served.json`). Le service note chaque jour quels matchs chaque portée a reçus et
combien de portées ont reçu chaque match. Une génération s'en sert deux fois :

1. *Quels matchs analyser* (`variety.choose_pool`) : au plus 18 (`MATCHS_PAR_GENERATION`) parmi tous les matchs à venir du jour. Ceux que
   ce client n'a pas encore reçus passent d'abord ; puis, compétition après compétition, le premier de chacune avant le deuxième de
   chacune (aucune compétition qui joue n'est oubliée, et un jour où une seule joue, elle remplit toute la liste) ; à égalité, les
   matchs reçus par le moins d'autres clients, puis les plus proches du coup d'envoi.
2. *Quels combinés composer* (`ComboGenerator.build_combos(variety=…)`) : un pronostic sur un match que ce client a déjà reçu perd 40 points de
   note (`VARIETY_SEEN_PENALTY`), et 6 points par autre client qui a reçu ce match, 3 au plus (`VARIETY_SERVED_PENALTY`). La note d'un
   combiné étant sa probabilité de réussite (de 10 à 40 environ), un match déjà reçu n'est repris que si les matchs du jour ne suffisent
   pas à faire autrement : un petit jour de championnat donne quand même des combinés, avec des équipes déjà vues. Mesuré par simulation
   (trois clients, jours de 27 et de 40 matchs) : jamais un match redonné au même client, et la chance de réussite moyenne des combinés
   ne bouge pas (32 %) ; la part des matchs du troisième client déjà donnés aux deux autres passe d'environ 11 % à 6 %.

La variété repart de zéro chaque jour UTC.

**Des analyses de l'IA partagées** (`shared_data.py`). Les clients n'ont pas les mêmes combinés, mais ils lisent les mêmes données de
matchs : l'estimation de l'IA pour un match est gardée `ANALYSIS_CACHE_MINUTES` (12 h, `analyses.json`) et réutilisée par toutes les
générations (elle ne sert jamais à un autre match qui aurait le même numéro : les noms des équipes sont vérifiés) ; les matchs du jour ne
sont relus chez TheSportsDB qu'au plus toutes les 5 minutes et les derniers résultats collectés au plus toutes les heures ; une seule
génération à la fois passe par « collecte + appel à l'IA » (si deux clients ont besoin du même match, le second attend puis le trouve déjà
analysé) ; quand l'IA ne répond pas, les autres générations n'insistent pas pendant 1 minute et se font avec les analyses déjà gardées.
Conséquences :

- **La dépense d'IA suit le nombre de matchs du jour, pas le nombre de clients** : un match n'est payé qu'une fois par période de 12 h.
  Une génération entièrement servie par des analyses déjà faites (`ai_matches` = 0 dans ses métadonnées) ne compte pas dans le plafond du jour.
- `MAX_GENERATIONS_PAR_JOUR` (60, tous clients confondus) reste un filet de sécurité contre un emballement.
- Pour un même match, tous les clients ont la même analyse dans la journée : ce qui change d'un client à l'autre, ce sont les matchs
  retenus et leurs associations dans les combinés.

## L'analyse de l'IA : un dossier par match

Depuis la version 2.4.0, l'IA ne reçoit plus « le bilan de la saison et cinq résultats bruts » : elle reçoit un **dossier** par match
(`dossier.py`), calculé à partir des matchs terminés de la base (aucun appel réseau, aucun coût), et elle doit **écrire son analyse
avant de donner ses chiffres** (`analyzerv2.PROMPT_HEADER`). Une IA qui lit des chiffres bien présentés, les compare et les
justifie, puis seulement les convertit en pourcentages, est plus cohérente qu'une IA à qui l'on demande directement dix-neuf nombres.

**Ce que contient le dossier** (`DossierBuilder.render`) :

- **le championnat** : buts par match (pour l'équipe qui reçoit, pour l'autre), part de victoires à domicile, de nuls et de victoires
  à l'extérieur, de matchs à plus de 1,5 / 2,5 / 3,5 buts, de matchs où les deux équipes marquent ;
- **pour chaque équipe** : le bilan de la saison, son **niveau** dans son championnat (rang aux points par match ; force offensive et
  défensive, où 1,00 est la moyenne et où une défense basse est une bonne défense), ses résultats **à domicile pour l'équipe qui reçoit
  et à l'extérieur pour l'autre**, ses statistiques de buts (plus de 1,5 / 2,5 / 3,5, les deux marquent, sans encaisser, sans marquer),
  la moyenne de ses 5 derniers matchs, son **repos** (jours depuis son dernier match) et ses **8 derniers matchs, chacun avec la
  force de l'adversaire** (« pt/m » : ses points par match, car un 3-0 contre le dernier ne vaut pas un 1-0 contre le premier) ;
- **les 6 dernières confrontations directes** (toutes saisons) ;
- **un repère statistique** (plus bas) : les buts attendus de chaque équipe et les probabilités qui en découlent ;
- **l'avis du marché**, seulement si `AI_SEES_MARKET` est activée (plus bas).

Tout est calculé sur la saison en cours (la plus récente de la base) : une équipe ne se juge pas sur l'an dernier. Les données sont celles
de la base, remises à jour par la collecte (au plus une fois par heure) avant chaque analyse. Quand elles sont maigres, le dossier le dit
à l'IA (« ÉCHANTILLON FAIBLE » sous 5 matchs, « aucun match connu cette saison ») et la consigne lui demande de se rapprocher des
moyennes du championnat.

**Ce que l'IA n'a pas, et ne doit pas inventer.** Les compositions, les blessés et suspendus, l'enjeu du match (titre, maintien, derby),
la météo : TheSportsDB n'en fournit aucun, même pour un match déjà joué. La consigne le dit à l'IA (n'en inventer aucun, ne citer
aucun joueur absent du dossier) et en fait une raison de plus de rester mesurée. La seule voie indirecte est l'avis des bookmakers
(`AI_SEES_MARKET`, plus bas) ; une vraie source de compositions serait une autre API, payante (voir « Limites connues »).

**Le repère statistique** (`dossier.fit_ratings`, `DossierBuilder.baseline`). Un modèle de Poisson : la force offensive et la force
défensive de chaque équipe sont estimées d'après les buts de TOUS les matchs du championnat, chaque équipe corrigeant les autres
(marquer trois fois contre une défense poreuse compte moins), et tirées vers la moyenne du championnat tant que l'équipe a peu joué
(`PRIOR_STRENGTH_GOALS` : 9 buts, soit environ 7 matchs). Il en déduit les buts attendus des deux équipes, puis victoire / nul / défaite,
lignes de buts, « les deux marquent » et « chaque équipe marque ». Il n'existe pas pour la Ligue des champions (équipes de championnats
différents) : le dossier le dit, et l'IA s'appuie alors sur le niveau de chaque équipe dans son championnat. L'IA le reçoit comme **un
point de départ chiffré**, pas comme la réponse : elle le corrige avec sa lecture du dossier et doit expliquer un écart important.

Mesuré sur des saisons passées (Premier League, Liga, Bundesliga et Serie A, 10 saisons, près de 14 000 matchs, chacun prévu avec les
seuls résultats de sa saison joués avant lui) :

- **le résultat** est mieux prévu que par la simple moyenne du championnat (perte logarithmique plus basse de 0,04 à 0,07 pour une
  victoire, de 0,01 pour le nul) et **bien calibré** : 74 % annoncés pour une victoire à domicile, 73 % observés ; 84 % annoncés, 85 % observés ;
- **les buts** (plus de 1,5 / 2,5 / 3,5, les deux équipes marquent) sont bien moins prévisibles : le modèle brut n'y fait presque pas
  mieux que la moyenne du championnat et était trop sûr de lui (74 % annoncés pour des « plus de 2,5 » qui arrivaient 65 % du temps).
  Ces quatre chiffres sont donc ramenés à moitié vers la moyenne du championnat (`GOAL_MARKET_SHRINK`), et la consigne dit la même
  chose à l'IA. Le résultat est l'endroit où l'analyse apporte de l'information ; sur les buts, un bon pronostic reste proche des moyennes.

**La consigne** (`analyzerv2.PROMPT_HEADER`). Elle fait suivre à l'IA, dans l'ordre, les sept regards d'un analyste (niveau, lieu, forme
pesée par l'adversaire, repos, confrontations directes, repère statistique, fiabilité), puis demande pour chaque match un objet JSON
dont les clés sont **dans cet ordre** : `id` et équipes, **`analyse`** (5 à 7 phrases courtes, 110 mots au plus, qui citent des chiffres
du dossier), `buts_attendus_domicile` et `buts_attendus_exterieur`, puis les dix-neuf probabilités. Ce que l'IA écrit avant les chiffres
guide les chiffres. Les règles de calibration y sont écrites : les pourcentages sont de vraies probabilités (« quand tu écris 70, cela
arrive 7 fois sur 10 »), jamais plus de 85 pour une victoire ni 93 pour « victoire ou nul », rien sous 3 ni au-dessus de 97 ; deux petites
tables de Poisson lui évitent de faire ces calculs de tête. Le texte de l'analyse n'est pas montré aux clients ; il est gardé avec
l'estimation dans `analyses.json` (dossier de cache), ce qui permet de relire pourquoi l'IA a donné tel chiffre. Le code, lui, ne change
pas : `probabilities.reconcile` rend les chiffres cohérents entre eux, et le plafond de confiance selon la quantité de données s'applique toujours.

**Coût et limites de débit.** Un appel (3 matchs) envoie environ 4 500 jetons et en reçoit environ 1 400 : de l'ordre de 0,023 $ avec
Sonnet 5.5 (2 $ et 10 $ le million de jetons), soit environ **0,008 $ par match**, et 0,2 à 0,4 $ par jour puisque les analyses sont
partagées entre clients (estimation, à recouper avec la console Anthropic après quelques jours). Les appels partent par 5 en parallèle.
Quand l'API répond « trop de requêtes » (429) ou « surchargée » (529), le lot patiente le temps qu'elle indique (au plus
`IA_RATE_LIMIT_MAX_WAIT`, 20 s) puis réessaie, au plus `IA_RATE_LIMIT_RETRIES` (3) fois, sans que cela compte comme une tentative
ratée. `IA_TIMEOUT` (75 s par appel) et `IA_MAX_TOKENS` (4 000) laissent de la marge à une réponse plus longue qu'avant.

**Changer la méthode sans servir de vieilles analyses.** L'estimation gardée pour un match (12 h) porte la version de la méthode
(`AI_PROMPT_VERSION`, suivie de « m » quand l'IA voit le marché). Une estimation faite par une autre version n'est jamais réutilisée :
après une mise à jour de la consigne, les matchs du jour sont ré-analysés une fois (environ 0,2 $), puis partagés comme avant. Après
toute modification du texte de la consigne ou du contenu du dossier, augmenter `AI_PROMPT_VERSION` dans `config.py`.

**L'avis du marché (`AI_SEES_MARKET`, désactivé par défaut).** Avec `AI_SEES_MARKET=1` (Render > Environment ; un redémarrage suffit)
et `ODDS_API_KEY` définie, le dossier d'un match coté contient une ligne « MARCHÉ » : les probabilités tirées des cotes médianes de
plusieurs bookmakers, marge retirée. Les bookmakers connaissent les compositions et les absences ; l'IA s'en sert comme point de départ
pour ce que la ligne couvre (résultat et lignes de buts) et ne s'en éloigne que pour une raison lue dans le dossier. À peser avant
d'activer : les pronostics se rapprochent alors du marché, donc aussi de ses cotes basses sur les favoris. C'est une décision de produit,
pas un réglage technique. Sans `ODDS_API_KEY`, aucun effet.

## La cote minimale d'un pari

Un client trouvait parfois dans un combiné un pari à 1,03 ou 1,05 : impossible à jouer, et il gonflait la chance annoncée du combiné.
Trois causes, corrigées en 2.4.0 :

1. rien n'écartait les paris très peu payés : `MIN_LEG_ODDS` (1,25 par défaut) écarte maintenant tout pari dont la cote, réelle ou
   estimée, est plus basse. Variable facultative : de 1,0 à 2,0, virgule ou point ; une valeur illisible ou hors de ces bornes revient à 1,25 ;
2. la cote estimée venait de la chance déjà plafonnée par la quantité de données : une équipe peu connue (plafond à 60 %) affichait
   1,5 pour un pari que les bookmakers paient 1,05, et ce faux 1,5 le faisait choisir. Elle vient maintenant de la chance réellement
   estimée par l'IA ; le plafond ne limite plus que la confiance affichée ;
3. les paris déjà presque sûrs, qui servaient de remplissage pour atteindre 2,50, sont écartés par le point 1.

Mesuré par simulation (IA réaliste, 12 ou 18 matchs analysés par génération, 150 à 250 générations par ligne) : avec les vraies cotes
des bookmakers, 45 à 70 % des combinés contenaient avant un pari sous 1,20 (selon la justesse supposée de l'IA) ; maintenant aucun
(cote minimale moyenne d'un combiné : 1,28). Il y a
toujours 3 combinés par génération et une cote totale moyenne proche de 2,5 à 2,8, et **la cote totale affichée suit la cote réellement
pariable** (2,65 contre 2,64, au lieu de 2,65 contre 2,50). La chance annoncée baisse (environ 39 % au lieu de 42 %) parce qu'elle était
gonflée par les paris à 1,05 ; la chance réelle d'un combiné est d'environ 30 à 34 %. Cette simulation ne montre **aucun gain à long
terme** : le rendement espéré par unité misée reste autour de 0,85 (la marge des bookmakers, avec une IA qui en sait moins que le
marché, hypothèse prudente). La cote minimale rend les cotes affichées honnêtes ; elle ne rend pas les paris rentables.

## Les cotes 1xBet

Les clients jouent chez 1xBet. Une cote affichée doit donc être celle qu'ils trouveront chez lui, et non une médiane de bookmakers
européens qui ne se retrouve peut-être nulle part. Depuis la version 2.4.1 :

- **D'où vient la cote** : the-odds-api rend, pour chaque match, les cotes de tous les bookmakers de la région `eu` que nous demandions
  déjà ; 1xBet y a la clé `onexbet`. On lit simplement son bloc dans la même réponse : aucune requête ni aucun crédit en plus.
- **Ce qui s'affiche** : si 1xBet cote le pari, **sa cote, sans signe**. Sinon la cote est indicative et porte un « ≈ » : la médiane des
  autres bookmakers quand ils cotent le pari, ou l'estimation de notre modèle quand personne ne le cote (buts d'une seule équipe, « les
  deux équipes marquent », paris combinés). Au sein d'un même combiné, chaque pari porte donc l'origine de sa propre cote.
- **La cote minimale suit** : `MIN_LEG_ODDS` (1,25) s'applique à la cote affichée. Un pari à 1,40 en médiane mais à 1,20 chez 1xBet n'est
  pas proposé, puisque le client ne pourrait pas le jouer à 1,40.
- **Le résultat 1/N/2 n'est pris chez 1xBet que s'il cote les trois issues** : une double chance se calcule avec deux cotes du **même**
  bookmaker, jamais l'une de 1xBet et l'autre d'un autre.
- **Les totaux de buts** : chaque bookmaker ne renvoie que sa ligne principale (en général 2,5). Les lignes 1,5 et 3,5 sont donc le plus
  souvent indicatives (« ≈ »), comme les paris sur les buts d'une équipe.
- **Réglage** : `ODDS_BOOKMAKER` (Render > Environment). Absente : `onexbet`. Une autre clé de la liste de the-odds-api
  (the-odds-api.com/sports-odds-data/bookmaker-apis.html) la remplace ; `1xbet` est accepté pour `onexbet`. **Vide** : aucun bookmaker de
  référence, la médiane de tous les bookmakers fait foi, sans « ≈ », comme avant la 2.4.1.
- **Dans les logs du service client**, à chaque génération : `cotes des N pronostics proposés : X relevées chez 1xBet, Y médiane des
  bookmakers (≈), Z estimées (≈)`. C'est la mesure de ce que voit le client : si X reste proche de 0, 1xBet n'est pas dans la réponse de
  the-odds-api (clé, région, ou bookmaker retiré de leur liste). Quand 1xBet ne cote pas du tout un match :
  `cotes onexbet : <domicile> – <extérieur> (<championnat>) absent chez ce bookmaker : médiane des autres, cotes marquées ≈`.
- **Les pages publiques ne nomment aucun bookmaker** (accueil, résultats, combiné gratuit, conditions, connexion, site de paiement) : 1xBet
  n'est cité que dans la légende de l'espace abonné. Un test le garantit. C'est voulu : les règles de publicité et des boutiques
  d'applications sur les paris sont plus strictes pour une page ouverte à tous.
- **Limite** : une cote bouge entre le moment où elle est relevée (6 heures au plus : cache de the-odds-api) et celui où le client parie. La
  légende de l'espace abonné le dit : « vérifie-la chez ton bookmaker avant de parier ». Les résultats publics ne comptent que des pronostics
  gagnés ou perdus, jamais des gains en argent.

## Barre de navigation du bas

Le menu des deux sites est une barre collée en bas de l'écran, avec cinq boutons de même largeur (`templates/_tabbar.html` ;
style : section « barre de navigation du bas » de `static/css/site.css`) :

| Bouton | Page ouverte |
| --- | --- |
| Accueil | l'accueil du site |
| Combiné gratuit | `/gratuit` (le bouton disparaît si `FREE_PICK_ENABLED=0`) |
| Résultats combinés | `/resultats` |
| Accès VIP | `/login`, la page de connexion ; un client déjà connecté est renvoyé tout de suite à son espace (`/app`) |
| Abonnement VIP | `/paiement`, sur le site de paiement |

- Le bouton de la page affichée est en doré. Chaque page le dit en tête de son gabarit : `{% set active_nav = "accueil" %}`
  (valeurs : `accueil`, `gratuit`, `resultats`, `vip`, `abonnement`). Les pages sans bouton à elles (conditions, erreurs) n'en
  disent rien : aucun bouton n'est doré.
- L'en-tête ne garde que la marque (et « Déconnexion » dans l'espace client) : il n'y a plus de second menu en haut. La page
  « Pas de connexion » (`hors_ligne.html`, enregistrée dans le téléphone) n'a pas de barre, ses boutons ne mèneraient nulle part.
- Sur téléphone la barre prend toute la largeur ; à partir de 720 px c'est un bloc arrondi, centré, qui flotte en bas ; un
  téléphone tenu en large la rend plus basse, sans icônes. Elle est en `position: sticky` à la fin de la page : elle suit le
  défilement sans cacher le pied de page.
- Pour changer un nom, une adresse ou une icône : `templates/_tabbar.html`, une seule fois pour les deux sites. Les noms doivent
  tenir sur deux lignes sans couper de mot dès 320 px de large (« Abonnement » est le plus long) ; les tests navigateur
  (`TestBarreDeNavigation`) le vérifient.

### Rapidité des boutons

Un toucher doit répondre tout de suite, même sur une connexion mobile lente. Mesuré sur le site réel en octobre 2026, un toucher
coûtait environ 0,3 s pour réveiller le service worker, puis 0,2 à 0,5 s d'aller-retour réseau par page (le serveur, lui, répond en
quelques millisecondes). Ce qui a été fait :

1. **Le service worker ne retarde plus rien** (`templates/sw.js`). Il s'arrête après quelques secondes d'inactivité ; avant, chaque
   toucher attendait son redémarrage AVANT que la page soit demandée. Maintenant : « static routing » (Chrome 123 et suivants, donc
   Chrome sur Android : tant qu'il dort, la page est demandée au réseau pendant qu'il démarre), « navigation preload » pour les
   autres navigateurs (la page est demandée en même temps que le démarrage), et les fichiers de la page « Pas de connexion » servis
   par le cache du navigateur d'abord (celui du service worker ne sert que sans réseau). Une seule demande part vers le serveur.
2. **Pages publiques gardées une minute** (`PUBLIC_PAGE_CACHE_SECONDS`, 60 par défaut). L'accueil, les résultats, le combiné gratuit
   et les conditions sont les mêmes pour tout le monde : le serveur les envoie avec `Cache-Control: private, max-age=60` (réponses 200
   seulement). Une fois la page affichée, `static/js/tabbar.js` demande discrètement les autres pages publiques (`rel=prefetch`) —
   seulement si le navigateur annonce l'état de la connexion (Chrome, Edge, Samsung Internet), hors mode « économiser les données » et
   hors 2G — et un bouton touché dans la minute s'ouvre sans réseau. Conséquence à connaître : un nouveau résultat ou un nouveau combiné
   gratuit peut mettre jusqu'à une minute à apparaître chez quelqu'un qui revient sur la page. L'espace client, la connexion, le
   paiement et l'API ne sont **jamais** gardés (`no-store`). **Tout couper** : `PUBLIC_PAGE_CACHE_SECONDS=0` dans Render > Environment
   (plus de mémorisation ni de préchargement), sans changer le code.
3. **« Accès VIP » droit sur l'espace.** Pour un client connecté, la page de connexion ne fait que le renvoyer vers `/app` : un
   aller-retour de plus. Quand l'appareil a déjà montré l'espace (repère `tev-espace` dans `localStorage`, aucune donnée personnelle),
   le bouton mène directement à `/app` ; si la session a expiré, `/app` renvoie à la connexion comme avant et le repère s'efface.
4. **Connexion anticipée** vers l'autre site (`<link rel="preconnect">`) : « Abonnement VIP » ne paie plus l'établissement d'une
   connexion sécurisée au moment du toucher (et inversement depuis le site de paiement).
5. **Réponse visible au toucher.** Le bouton touché prend aussitôt l'aspect « page affichée » (doré, trait en haut qui pulse) et
   l'ancien le perd. Le lien n'est jamais détourné (Ctrl + clic, nouvel onglet restent normaux) ; si la page ne vient pas (10 s, retour
   en arrière, application cachée), l'aspect d'origine revient.

Vérifier : les tests navigateur `TestRapiditeDesBoutons` et `TestServiceWorkerSansRetard` (service worker lent à démarrer simulé : la
page s'ouvre en moins de 1,2 s alors que le démarrage en prend 2 ; mode sans connexion, service worker arrêté ou non).

## Les fichiers

| Fichier | Rôle |
| --- | --- |
| `dashboard.py` | Site des clients : accueil, résultats, combiné gratuit, plan du site, conditions, connexion, espace client, API de génération et d'historique |
| `showcase.py`, `daily_generation.py` | Pages publiques : bilan et combiné gratuit (ce qui a le droit d'être montré) ; génération automatique quotidienne, facultative (elle seule alimente le combiné gratuit) |
| `paiement.py`, `paydunya.py` | Site de paiement et client de l'API PayDunya |
| `license_manager.py` | Licences et commandes dans PostgreSQL (délivrance atomique, renouvellements cumulés), compteur de générations par client (`generation_usage`) |
| `email_sender.py` | Envoi de la clé : Brevo (HTTPS) ou Gmail (SMTP), avec nouvelles tentatives |
| `generation_service.py`, `pipeline.py` | Génération en arrière-plan : un travail et un résultat par client, cache, plafond quotidien, avancement ; matchs du jour et constat « pas de championnat » (`matchday.json`) ; ce qui a déjà été distribué (`served.json`) |
| `variety.py`, `shared_data.py` | Variété (choix des matchs analysés, matchs déjà reçus) ; données communes à toutes les générations : matchs du jour, collecte, estimations de l'IA gardées par match (`analyses.json`) |
| `data_collector.py`, `analyzerv2.py` | Données TheSportsDB (matchs du jour, résultats récents), analyse IA : consigne, appels en parallèle, attente quand l'API limite le débit, modèles de repli si un nom de modèle n'existe pas |
| `dossier.py` | Dossier d'un match pour l'IA : niveau des équipes, domicile / extérieur, derniers matchs pesés par l'adversaire, repos, confrontations directes, repère statistique (Poisson), avis du marché en option ; aucun appel réseau |
| `probabilities.py`, `markets.py`, `combo_generator.py` | Probabilités, catalogue des paris et verdicts, composition des combinés et cotes |
| `combo_history.py` | Historique : résultats réels des matchs joués, bilan ; chaque client ne voit que ses combinés à venir |
| `web_common.py`, `privacy.py` | Sécurité commune (en-têtes, anti-CSRF, limitation d'essais), gabarits, e-mails masqués dans les journaux, identifiant opaque d'un client (`client_scope`) |
| `config.py` | Réglages non secrets ; lit aussi un fichier `.env` en local |
| `templates/`, `static/` | Pages et styles (sans script ni style en ligne), JavaScript du site ; `templates/_coupon.html` (coupons des pages publiques), `_meta.html` (aperçu de partage), `_tabbar.html` (barre de navigation du bas), `static/js/public.js`, `static/js/tabbar.js` (barre : réponse au toucher, préchargement), `static/images/partage.png` |
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
| `SPORTSDB_API_KEY` | dashboard | Ta clé TheSportsDB. Sans elle, la clé de test partagée `123` est utilisée (limitée ; l'ancienne `3` est désactivée) |
| `ODDS_API_KEY` | dashboard | Facultatif : vraies cotes (the-odds-api.com). Sans elle, cotes estimées |
| `ODDS_BOOKMAKER` | dashboard | Facultatif. Le bookmaker dont la cote s'affiche sans « ≈ » : 1xBet (`onexbet`) si la variable est absente ; une autre clé the-odds-api la remplace ; **vide** = aucun, la médiane de tous les bookmakers fait foi. Voir « Les cotes 1xBet » |
| `MIN_LEG_ODDS` | dashboard | Facultatif. Cote minimale d'un pari proposé : 1,25 par défaut, de 1,0 à 2,0 (virgule ou point). Voir « La cote minimale d'un pari » |
| `AI_SEES_MARKET` | dashboard | Facultatif. `1` : l'IA voit aussi l'avis des bookmakers (cotes médianes, marge retirée) ; sans `ODDS_API_KEY`, aucun effet. Désactivé par défaut (voir « L'analyse de l'IA : un dossier par match ») |
| `DATA_DIR` | dashboard | Dossier du disque persistant (`/var/data`) : historique des matchs, combinés générés, cache |
| `FREE_PICK_ENABLED` | dashboard | Facultatif. `0` (ou `non`, `false`, `off`) retire le combiné gratuit : `/gratuit` et ses liens disparaissent. Par défaut : activé |
| `AUTO_GENERATE_HOUR` | dashboard | Facultatif. Heure UTC (0 à 23) à partir de laquelle une génération automatique part chaque jour s'il n'y en a pas eu. **Appel payant à l'IA** ; absente = désactivée (voir « Pages publiques ») |
| `PUBLIC_PAGE_CACHE_SECONDS` | dashboard | Facultatif. Secondes pendant lesquelles le navigateur garde les pages publiques et les précharge (60 par défaut) ; `0` coupe les deux (voir « Rapidité des boutons ») |
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
python -m pytest tests/e2e -q             # parcours réels dans Chromium : connexion, génération, historique, pages publiques, paiement, application installable, rapidité de la barre
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

## Prix et offres

Trois offres, un seul accès (génération à la demande, historique, bilan). Tout est dans `config.py` ; les pages (accueil, FAQ,
paiement, conditions, e-mail) et le montant envoyé à PayDunya en sont déduits : un prix ne se change qu'à un endroit.

| Offre | Constante | Prix | Durée accordée |
| --- | --- | --- | --- |
| Pass 7 jours | `PRICE_WEEKLY_FCFA` | 2 000 FCFA (≈ 3 €) | 7 jours |
| Mensuel | `PRICE_MONTHLY_FCFA` | 6 000 FCFA (≈ 9 €), tarif de lancement | 30 jours |
| Annuel | `PRICE_YEARLY_FCFA` | 39 400 FCFA (≈ 60 €) | 365 jours |

- **FCFA d'abord.** C'est la monnaie des clients et la seule que PayDunya encaisse. L'euro n'est qu'un ordre de grandeur, arrondi à
  l'euro (`FCFA_PER_EUR`, taux fixe 655,957) et précédé de « ≈ » : il n'est jamais facturé.
- **Pas de renouvellement automatique.** Chaque offre est un paiement unique. Acheter avant la fin ajoute la durée au temps restant
  et garde la même clé : le pass peut donc servir d'essai, puis se prolonger par un mensuel.
- **Tarif de lancement du mensuel.** `LAUNCH_PRICE_UNTIL` (par défaut le 31 décembre 2026) : tant que la date n'est pas passée,
  l'accueil et la page de paiement annoncent « Tarif de lancement jusqu'au … ». Ensuite l'annonce **disparaît toute seule** (aucune
  mention périmée) mais le prix ne change pas tout seul : pour le monter, modifier `PRICE_MONTHLY_FCFA`. `None` n'annonce aucun
  tarif de lancement. Ne mets qu'une date que tu comptes tenir : c'est une promesse faite aux clients.
- **L'annuel affiche ce qu'il fait gagner**, calculé à partir des deux prix (« au prix de 6,6 mois », « 45 % d'économie »), arrondi dans
  le sens qui ne promet jamais plus que la réalité ; rien n'est affiché si l'annuel ne fait rien gagner.
- **Garde-fou.** Un test (`test_plus_on_s_engage_longtemps_moins_le_jour_coute`) échoue si une modification rend le jour plus cher
  sur une offre longue que sur une courte : les clients auraient intérêt à acheter la mauvaise offre.
- **Changer un prix** : modifier la constante, lancer `python -m pytest tests -q`, puis envoyer sur `main`. Les tests qui vérifient
  les montants affichés (accueil, paiement, boutons d'essai) écrivent les prix en toutes lettres : ils échouent tant qu'on ne les a pas
  mis à jour avec le prix, ce qui évite d'afficher un montant par mégarde. Une facture déjà créée garde son ancien montant.

Côté base, la commande (`pending_orders`) garde la durée **en mois** (`duree`, historique) et, depuis le pass, la durée exacte **en
jours** (`jours`, colonne ajoutée toute seule au démarrage). Quand `jours` est renseignée elle l'emporte ; vide (commandes
enregistrées avant le pass, paiement encore en cours pendant une mise à jour), la durée se lit en mois comme avant : aucun
paiement en cours n'est perdu ni raccourci. Pour le pass, `duree` vaut 1 (repli arrondi vers le haut pour une ancienne version du code).

Pour voir ce qui se vend par offre (factures créées, puis payées) :

```sql
SELECT plan, COUNT(*) AS factures, COUNT(*) FILTER (WHERE license_key IS NOT NULL) AS payees
FROM pending_orders GROUP BY plan ORDER BY plan;
```

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
  l'API (données privées, téléphone parfois prêté). Tout le reste passe par le réseau, comme sans service worker, et sans jamais
  attendre son démarrage (voir « Rapidité des boutons »).
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
| Les logs disent `limite de débit de l'API (429)` | L'API Anthropic limite le nombre de jetons par minute du compte (cinq appels en parallèle envoient environ 20 000 jetons). Le site patiente et réessaie seul, jusqu'à 3 fois par lot, sans compter une tentative ratée. Si des analyses échouent quand même : relever le palier du compte dans la console Anthropic, ou baisser `IA_MAX_PARALLEL` dans `config.py` |
| La première génération après une mise à jour est plus lente | Normal : les analyses gardées par l'ancienne méthode sont ignorées (`AI_PROMPT_VERSION`), les matchs du jour sont ré-analysés une fois. Les générations suivantes retrouvent des analyses partagées |
| Un client dit qu'il ne peut pas générer une deuxième fois | Depuis la 2.4.2, tant que ses combinés sont valables, il n'y a plus de bouton « Générer » : c'est **« Générer de nouveaux combinés »**, sous ses combinés (avec confirmation). S'il n'y est pas : il a déjà utilisé ses 2 générations du jour (le message le dit sous les combinés), ou sa page date d'avant la mise à jour (la recharger). Les logs disent `génération (de nouveaux combinés) demandée par j***@… : running (lancée)`, puis `génération refusée à j***@… : 2 sur 2 utilisées aujourd'hui` s'il insiste |
| « Aucun combiné assez fiable avec les matchs d'aujourd'hui » plus souvent depuis la 2.4.0 | La cote minimale (`MIN_LEG_ODDS`, 1,25) écarte les paris trop sûrs : un petit jour de championnat, il peut ne rester aucun combiné à 2,50. Les logs disent combien de pronostics sont restés (`… N pronostics, 0 combinés`). Si cela se répète, baisser un peu `MIN_LEG_ODDS` (1,20) dans Render > Environment ; en dessous, les paris à 1,0x reviennent |
| Un client trouve que les mêmes équipes reviennent, ou voit d'autres combinés que son voisin | Chaque client a ses propres combinés (voir « Les combinés de chaque client, la variété et les analyses partagées »). Les équipes se répètent surtout les petits jours (trêve, peu de matchs) : s'il reste peu de matchs à venir, ceux déjà reçus sont repris faute d'autre choix. Les logs du service client disent combien de matchs étaient disponibles : `génération : N matchs à venir, M retenus, K analysés (… par l'IA, … déjà gardés), … combinés`. Les générations faites avant la version 2.3.0 restent dans l'historique du client avec leurs répétitions d'alors (les matchs reçus avant cette version ne sont pas comptés dans la variété du jour) |
| « Pas de championnat aujourd'hui », « Trop peu de matchs aujourd'hui » ou « Les matchs d'aujourd'hui ont déjà commencé » | Pas une panne : les combinés ne portent que sur les matchs du jour (UTC) qui n'ont pas commencé, il en faut 3. Logs du service client : `matchs du jour (AAAA-MM-JJ) : N à venir sur M`. Trêve internationale, ou calendrier de TheSportsDB incomplet : le constat est revérifié toutes les 30 minutes. « Les matchs du jour sont momentanément indisponibles » : un championnat n'a pas répondu chez TheSportsDB, réessayer |
| « Générer les combinés » reste bloqué plusieurs minutes, puis « Une erreur est survenue pendant la génération » | Logs du service client : chaque étape est consignée (`génération : Recherche des matchs du jour… (5 %)`, puis `Collecte…`, `Analyse…`) ; la dernière dit où ça s'est arrêté. `génération perdue (elle dure trop longtemps) : étape « … »` est suivie de la pile du fil bloqué : sa dernière ligne montre l'appel qui ne revient pas. Le clic du client a été rendu (il peut réessayer). `TheSportsDB : N appel(s) sans réponse au bout de 25 s` : TheSportsDB, ou le réseau du serveur, ne répond plus ; l'étape est abandonnée au bout de 25 s et non de 5 minutes. Si cela se répète sans cause visible, redémarrer le service depuis Render |
| Un client ne peut plus générer (« Tu as utilisé tes 2 générations ») | Normal : 2 par jour UTC et par client (voir « Matchs du jour et générations des clients »). Lui en rendre une : `DELETE FROM generation_usage WHERE email = '…' AND day = '…'`. Log `compteur de générations illisible` : la base ne répond pas, personne n'est bloqué pour autant |
| Les cotes sont toutes précédées de « ≈ » | `ODDS_API_KEY` absente, ou quota de the-odds-api atteint (`the-odds-api <sport> : HTTP …` dans les logs) : ce sont alors des cotes estimées. Avec une clé qui répond : la ligne `cotes des N pronostics proposés : 0 relevées chez 1xBet…` dit que 1xBet n'est pas dans la réponse de the-odds-api (voir « Les cotes 1xBet ») ; les cotes sont alors la médiane des autres bookmakers, ou des estimations |
| Certains paris ont un « ≈ » et d'autres non | Normal : sans « ≈ », c'est la cote de 1xBet ; avec « ≈ », elle est indicative. Seuls le résultat (victoire, double chance) et les totaux de buts peuvent avoir la cote de 1xBet (voir « Ce que le site promet, et ce qu'il ne promet pas », point 5 ; les lignes 1,5 et 3,5 n'y sont le plus souvent pas). Si un de ces paris garde un « ≈ », cherche dans les logs `cotes réelles : <domicile> – <extérieur> (<championnat>) introuvable parmi les N match(s) cotés par the-odds-api` : le match n'est pas encore coté chez eux, ou son nom s'écrit autrement (au-delà des accents et des tirets, déjà ignorés) ; ou `cotes onexbet : … absent chez ce bookmaker` : 1xBet ne cote pas ce match |
| La page « Combiné gratuit » est vide | Normal tant que la génération automatique n'a pas eu lieu avec les matchs d'aujourd'hui (voir « Pages publiques ») : elle seule l'alimente, pas les clics des abonnés ; vérifier que `AUTO_GENERATE_HOUR` est définie. Si la génération automatique du jour existe et que la page reste vide : tous ses combinés ont un match qui commence dans moins de 15 minutes, ou `FREE_PICK_ENABLED=0` |
| Une page publique (résultats, combiné gratuit) paraît un peu ancienne | Le navigateur les garde 60 secondes (voir « Rapidité des boutons ») : actualiser la page, ou mettre `PUBLIC_PAGE_CACHE_SECONDS=0` sur Render |
| `/resultats` répond « momentanément indisponibles » | Les fichiers d'historique sont illisibles (disque `DATA_DIR`) : logs du service client, ligne `vitrine : bilan public impossible à calculer`. La page revient seule dès que l'historique est lisible ; un bilan déjà calculé reste affiché malgré une panne |
| Les résultats n'avancent pas | Les scores sont récupérés chez TheSportsDB en arrière-plan, au plus tous les quarts d'heure et 12 matchs à la fois ; un match dont le score n'est pas encore publié est revérifié toutes les 30 minutes. Tant que le score manque, le combiné reste « en attente » et n'est pas détaillé. Cherche `vitrine : récupération des scores impossible` dans les logs |
| L'aperçu d'un lien collé dans WhatsApp est vieux ou absent | Les messageries gardent l'aperçu en mémoire un moment. L'image `og:image` doit répondre 200 (`/static/images/partage.png`). Une nouvelle image reçoit seule une nouvelle adresse (empreinte) |
| L'historique est vide après un déploiement | Le disque persistant n'est pas monté sur `DATA_DIR` : voir la liste de contrôle ci-dessus |

## Sécurité en bref

Sessions en cookies `HttpOnly`, `SameSite=Lax` et `Secure` ; politique CSP stricte (aucun script ni style en ligne) ;
contrôle de l'origine des envois de formulaire et en-tête `X-Requested-With` sur l'API ; limitation des essais de
connexion (par e-mail) et des créations de facture (par e-mail, adresse IP et globalement) ; adresses e-mail et clés d'API masquées
dans les journaux ; messages d'erreur sans détail technique ; licence revérifiée en base à chaque requête protégée
(avec une courte tolérance si la base est momentanément injoignable).

## Limites connues

- Le paiement réel (PayDunya en production, retour du client, appel `/ipn`) est couvert par des tests simulés : il doit
  être essayé en mode test (voir plus haut), puis une fois avec un vrai paiement de l'offre la moins chère (Pass 7 jours,
  2 000 FCFA) : c'est aussi la preuve que PayDunya accepte une facture de ce montant.
- Les résultats publics valent ce que valent les scores disponibles chez TheSportsDB : un match dont le score n'est jamais publié
  garde son combiné « en attente » (il n'est ni compté ni affiché), et le bilan lit les 1000 dernières générations (la page dit « depuis le … »).
- L'IA ne connaît ni les compositions, ni les blessés et suspendus, ni l'enjeu d'un match (titre, maintien, derby) : TheSportsDB n'en
  fournit pas, même pour un match déjà joué. C'est la plus grande part d'inconnu d'un pronostic. Pistes, à décider : l'avis des
  bookmakers (`AI_SEES_MARKET`), une API de football payante qui donne les compositions (compte et clé à ouvrir par le propriétaire),
  ou une recherche sur le web par l'IA (plus lent et plus cher).
- Les paris sur les buts (plus de X buts, les deux équipes marquent) sont à peine plus prévisibles que la moyenne du championnat, même
  avec un bon dossier (voir « L'analyse de l'IA ») : une série de combinés perdus sur ces paris n'est pas anormale. Le résultat
  (victoire, double chance) est le seul pari où l'analyse apporte nettement de l'information.
- En début de saison, le dossier ne regarde que la saison en cours : avec peu de matchs joués, l'IA est invitée à rester proche des
  moyennes et le plafond de confiance (60 % sous 4 matchs connus) s'applique, donc moins de paris atteignent le seuil de 65 %.
- La qualité des pronostics dépend des données de TheSportsDB (calendriers parfois incomplets) et de l'estimation de
  l'IA ; le site affiche donc des probabilités, pas des certitudes. Le verdict « Pas de championnat aujourd'hui » vaut ce que vaut
  ce calendrier, et le « jour » est le jour UTC : un match à 23 h 30 UTC est déjà celui du lendemain à Lagos (UTC+1).
- Les conditions du site (`/conditions`) décrivent le service tel qu'il est ; les conditions générales de vente, les
  mentions légales et la politique de remboursement doivent être relues par un juriste avant de vendre à grande échelle.
