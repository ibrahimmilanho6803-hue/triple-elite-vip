/* Triple Elite VIP - pages publiques (résultats, combiné gratuit) : heures locales et bouton « Copier le lien ».
 *
 * Rien n'est envoyé nulle part. Sans JavaScript, les heures restent en UTC (écrites par le serveur, avec « UTC » à
 * côté) et les boutons WhatsApp et Telegram, de simples liens, fonctionnent quand même ; seul « Copier le lien », qui
 * a besoin de ce script, reste caché. Aucune donnée n'est injectée en HTML (textContent seulement).
 */
(function () {
  'use strict';

  var NBSP = ' ';
  var each = function (list, fn) { Array.prototype.forEach.call(list, fn); };

  // ------------------------------------------------------------------ heures : de l'UTC à l'heure de l'appareil
  // Mêmes formats que static/js/dashboard.js : « sam. 11 oct. à 20 h 45 ».
  var dfDay = new Intl.DateTimeFormat('fr-FR', { weekday: 'short', day: 'numeric', month: 'short' });
  var dfTime = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

  each(document.querySelectorAll('time.local[datetime]'), function (el) {
    var d = new Date(el.getAttribute('datetime'));
    if (isNaN(d.getTime())) { return; }
    el.textContent = dfDay.format(d) + ' à' + NBSP + dfTime.format(d).replace(':', NBSP + 'h' + NBSP);
  });

  // ------------------------------------------------------------------ « Copier le lien »
  var status = document.querySelector('[data-copy-status]');

  function say(text) {
    if (status) { status.textContent = text; }
  }

  // Navigateurs sans API presse-papiers (ou page non sécurisée) : copie par un champ temporaire.
  function copyWithField(text) {
    var field = document.createElement('textarea');
    field.value = text;
    field.setAttribute('readonly', '');
    field.className = 'sr-only';
    document.body.appendChild(field);
    field.select();
    field.setSelectionRange(0, text.length);
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(field);
    return ok;
  }

  function copy(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(
        function () { return true; },
        function () { return copyWithField(text); }
      );
    }
    return Promise.resolve(copyWithField(text));
  }

  each(document.querySelectorAll('[data-copy]'), function (button) {
    button.hidden = false;
    button.addEventListener('click', function () {
      copy(button.getAttribute('data-copy')).then(function (ok) {
        say(ok ? 'Lien copié.' : 'Copie impossible : copie l’adresse dans la barre de ton navigateur.');
      });
    });
  });
})();
