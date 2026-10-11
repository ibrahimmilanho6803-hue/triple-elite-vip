/* Triple Elite VIP - barre de navigation du bas : une réponse immédiate au toucher, et les pages voisines prêtes d'avance.
 *
 * 1. Retour au toucher. Un bouton ouvre une autre page ; sur une connexion lente elle met un moment à arriver, et sans ce
 *    script l'écran ne bouge pas pendant ce temps : le bouton paraît ne pas répondre. Le bouton touché prend donc tout de
 *    suite l'aspect « page affichée » (doré, trait en haut qui pulse) et l'ancien le perd. Le lien lui-même n'est jamais
 *    touché : le navigateur le suit comme d'habitude (Ctrl + clic, nouvel onglet... compris).
 * 2. Préchargement. Les boutons marqués data-prefetch mènent à des pages publiques que le serveur laisse garder une minute
 *    au navigateur (PUBLIC_PAGE_CACHE_SECONDS) : une fois la page affichée, et seulement si la connexion le permet, le
 *    navigateur les demande en tâche de fond, et un bouton touché dans la minute qui suit s'ouvre sans attendre le réseau.
 * 3. « Accès VIP » droit sur l'espace. Pour un client déjà connecté, la page de connexion ne fait que le renvoyer vers son
 *    espace : un aller-retour réseau de plus à chaque toucher. Si cet appareil a déjà montré l'espace, le bouton y mène
 *    directement ; sinon (ou si la session a expiré) rien ne change, la connexion s'occupe de tout.
 *
 * Rien n'est lu ni envoyé : seulement des adresses déjà présentes dans la page, et un repère « 1 » gardé sur l'appareil,
 * sans aucune donnée personnelle. Sans JavaScript, les boutons restent de simples liens qui fonctionnent.
 */
(function () {
  'use strict';

  var bar = document.querySelector('nav.tabbar');
  if (!bar) { return; }

  // ------------------------------------------------------------------ 1. retour au toucher
  var PENDING = 'is-pending';            // le bouton touché
  var LOADING = 'tabbar--pending';       // la barre, tant qu'une page est attendue
  var GIVE_UP_MS = 10000;                // la page n'est jamais venue (réseau coupé...) : on rend l'aspect d'origine
  var pending = null;
  var timer = 0;

  function settle() {
    window.clearTimeout(timer);
    if (pending) { pending.classList.remove(PENDING); }
    pending = null;
    bar.classList.remove(LOADING);
  }

  bar.addEventListener('click', function (event) {
    var link = event.target && event.target.closest ? event.target.closest('a.tabbar__link') : null;
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
      return;                              // pas un simple clic : le navigateur ouvre autre chose (nouvel onglet...), la page reste
    }
    settle();
    pending = link;
    link.classList.add(PENDING);
    bar.classList.add(LOADING);
    timer = window.setTimeout(settle, GIVE_UP_MS);
  });

  // Page rendue telle qu'elle était à un retour en arrière (bouton en attente compris), ou cachée par une autre application
  // (l'application Android ouvre le site de paiement par-dessus) : l'attente n'a plus lieu d'être.
  window.addEventListener('pageshow', function (event) { if (event.persisted) { settle(); } });
  document.addEventListener('visibilitychange', function () { if (document.hidden) { settle(); } });

  // ------------------------------------------------------------------ 2. préchargement des pages voisines
  // Seuls les navigateurs qui exposent l'état de la connexion (Chrome, Edge, Samsung Internet : ceux de la quasi-totalité des
  // téléphones Android) savent réutiliser une page préchargée ; ailleurs ce serait du transfert pour rien. Pas non plus en
  // mode « économiser les données », ni sur une connexion 2G.
  function mayPrefetch() {
    var connection = navigator.connection;
    return !!connection && !connection.saveData && !/(^|-)2g$/.test(connection.effectiveType || '');
  }

  function prefetch() {
    if (!mayPrefetch()) { return; }
    var done = {};
    Array.prototype.forEach.call(bar.querySelectorAll('a[data-prefetch]'), function (link) {
      // Jamais la page affichée : ni quand son bouton est doré, ni sur une page d'erreur (aucun bouton doré) à la même adresse.
      if (link.getAttribute('aria-current') === 'page' || link.origin !== window.location.origin ||
          link.pathname === window.location.pathname || done[link.href]) { return; }
      done[link.href] = true;
      var hint = document.createElement('link');
      hint.rel = 'prefetch';
      hint.href = link.href;
      document.head.appendChild(hint);
    });
  }

  // Après le chargement de la page et quand le navigateur n'a rien d'autre à faire : jamais en concurrence avec elle.
  function whenIdle(task) {
    var later = window.requestIdleCallback
      ? function () { window.requestIdleCallback(task, { timeout: 5000 }); }
      : function () { window.setTimeout(task, 1500); };
    if (document.readyState === 'complete') { later(); } else { window.addEventListener('load', later, { once: true }); }
  }

  if (bar.querySelector('a[data-prefetch]')) { whenIdle(prefetch); }

  // ------------------------------------------------------------------ 3. « Accès VIP » droit sur l'espace d'un client connecté
  // Le repère est posé quand l'espace s'affiche (donc session valide) et effacé quand la page de connexion s'affiche
  // (déconnexion, ou session expirée : /app y renvoie). S'il est périmé, le bouton fait ce qu'il faisait avant : un détour.
  var SPACE = '/app';
  var LOGIN = '/login';
  var MARK = 'tev-espace';

  function mark(value) {
    try {
      if (value) { window.localStorage.setItem(MARK, '1'); } else { window.localStorage.removeItem(MARK); }
    } catch (e) { /* stockage indisponible : le bouton garde son détour par la connexion */ }
  }

  function marked() {
    try { return window.localStorage.getItem(MARK) === '1'; } catch (e) { return false; }
  }

  if (window.location.pathname === SPACE) { mark(true); }
  if (window.location.pathname === LOGIN) { mark(false); }
  var vip = bar.querySelector('a[data-tab="vip"]');
  if (vip && vip.origin === window.location.origin && vip.pathname === LOGIN && marked()) { vip.href = SPACE; }
})();
