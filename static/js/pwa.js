/* Triple Elite VIP - application installable : service worker et invitation à installer.
 * Rien n'est envoyé nulle part : tout reste dans le navigateur. */
(function () {
  'use strict';

  /* ---- service worker : rend le site installable et affiche « Pas de connexion » quand il n'y a pas de réseau.
   * S'il ne peut pas s'enregistrer, le site fonctionne exactement pareil. */
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function () { /* silencieux */ });
    });
  }

  /* ---- invitation à installer ------------------------------------------------ */
  var boxes = document.querySelectorAll('[data-install]');
  if (!boxes.length) { return; }

  var STORAGE_KEY = 'tev-install-masque';
  var dismissed = function () {
    try { return window.localStorage.getItem(STORAGE_KEY) === '1'; } catch (e) { return false; }
  };
  var remember = function () {
    try { window.localStorage.setItem(STORAGE_KEY, '1'); } catch (e) { /* stockage indisponible : l'invitation reviendra */ }
  };

  // Déjà ouverte comme une application, ou invitation déjà refusée : rien à proposer.
  var standalone = (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
    window.navigator.standalone === true;
  if (standalone || dismissed()) { return; }

  var each = function (selector, fn) {
    for (var i = 0; i < boxes.length; i++) {
      var items = boxes[i].querySelectorAll(selector);
      for (var j = 0; j < items.length; j++) { fn(items[j]); }
    }
  };
  var setBoxes = function (visible) {
    for (var i = 0; i < boxes.length; i++) { boxes[i].hidden = !visible; }
  };
  // mode « android » : le bouton d'installation ; mode « ios » : l'explication du geste (Safari n'a pas de bouton).
  var show = function (mode) {
    each('[data-install-android]', function (el) { el.hidden = mode !== 'android'; });
    each('[data-install-button]', function (el) { el.hidden = mode !== 'android'; });
    each('[data-install-ios]', function (el) { el.hidden = mode !== 'ios'; });
    setBoxes(true);
  };

  // Notre bouton ne s'adresse qu'aux téléphones et tablettes Android : sur ordinateur (Chrome, Edge), le navigateur
  // garde sa propre invitation (l'icône dans la barre d'adresse) et on n'y touche pas.
  var ua = navigator.userAgent || '';
  var deferred = null;
  window.addEventListener('beforeinstallprompt', function (event) {
    if (!/android/i.test(ua)) { return; }
    event.preventDefault();          // l'invitation du navigateur est gardée pour notre bouton
    deferred = event;
    show('android');
  });
  window.addEventListener('appinstalled', function () {
    deferred = null;
    setBoxes(false);
  });

  each('[data-install-button]', function (button) {
    button.addEventListener('click', function () {
      if (!deferred) { return; }
      var invitation = deferred;
      deferred = null;
      invitation.prompt();
      var done = function () { setBoxes(false); };
      Promise.resolve(invitation.userChoice).then(done, done);
    });
  });
  each('[data-install-dismiss]', function (button) {
    button.addEventListener('click', function () {
      remember();
      setBoxes(false);
    });
  });

  // iPhone et iPad (Safari) : on explique le geste. Les autres navigateurs d'iOS (Chrome, Firefox, Edge) n'ont pas
  // le même menu : on ne leur promet rien.
  var apple = /iphone|ipad|ipod/i.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  if (apple && !/crios|fxios|edgios/i.test(ua)) { show('ios'); }
})();
