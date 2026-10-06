/* Triple Elite VIP - pages de paiement : récapitulatif de l'offre, envoi du formulaire, copie de la clé.
 * Aucune donnée n'est interprétée comme du HTML : tout passe par textContent. */
(function () {
  'use strict';

  /* ---- page /paiement ---------------------------------------------------- */
  var form = document.getElementById('pay-form');
  if (form) {
    var radios = form.querySelectorAll('input[name="plan"]');
    var sumName = document.getElementById('sum-name');
    var sumEur = document.getElementById('sum-eur');
    var sumFcfa = document.getElementById('sum-fcfa');
    var button = document.getElementById('pay-btn');
    var buttonLabel = button ? button.textContent : '';

    var syncSummary = function () {
      for (var i = 0; i < radios.length; i++) {
        if (radios[i].checked) {
          sumName.textContent = radios[i].getAttribute('data-name');
          sumEur.textContent = radios[i].getAttribute('data-eur');
          sumFcfa.textContent = radios[i].getAttribute('data-fcfa');
          return;
        }
      }
    };
    for (var i = 0; i < radios.length; i++) {
      radios[i].addEventListener('change', syncSummary);
    }
    syncSummary();

    // Un seul envoi : le bouton est bloqué dès que le formulaire part (évite deux factures).
    form.addEventListener('submit', function () {
      if (button) {
        button.disabled = true;
        button.textContent = 'Redirection vers PayDunya…';
      }
    });
    // Retour arrière depuis PayDunya : la page revient du cache avec le bouton bloqué.
    window.addEventListener('pageshow', function (event) {
      if (event.persisted && button) {
        button.disabled = false;
        button.textContent = buttonLabel;
        syncSummary();
      }
    });
  }

  /* ---- page /succes : actualisation tant que la confirmation n'est pas arrivée ---------------- */
  var waiting = document.querySelector('[data-refresh-url]');
  if (waiting) {
    var delay = (parseInt(waiting.getAttribute('data-refresh-seconds'), 10) || 4) * 1000;
    var nextUrl = waiting.getAttribute('data-refresh-url');
    // replace : les rechargements successifs ne remplissent pas l'historique (le bouton retour reste utile).
    window.setTimeout(function () { window.location.replace(nextUrl); }, delay);
  }

  /* ---- page /succes : copier la clé -------------------------------------- */
  var copyButton = document.getElementById('copy-key');
  var keyElement = document.getElementById('license-key');
  if (copyButton && keyElement) {
    var status = document.getElementById('copy-status');
    var say = function (text) {
      if (status) { status.textContent = text; }
    };
    var selectKey = function () {
      var range = document.createRange();
      range.selectNodeContents(keyElement);
      var selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    };
    var fallbackCopy = function () {
      try {
        selectKey();
        say(document.execCommand('copy') ? 'Clé copiée.' : 'Sélectionnée : copie-la avec ton clavier.');
      } catch (e) {
        say('Sélectionnée : copie-la avec ton clavier.');
      }
    };

    copyButton.hidden = false;
    copyButton.addEventListener('click', function () {
      var key = keyElement.getAttribute('data-key') || keyElement.textContent;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(key).then(function () {
          say('Clé copiée.');
        }, fallbackCopy);
      } else {
        fallbackCopy();
      }
    });
  }
})();
