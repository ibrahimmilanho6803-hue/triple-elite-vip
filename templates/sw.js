/* Triple Elite VIP - service worker (généré par pwa.py).
 * Il sert à deux choses : rendre le site installable, et afficher une page claire quand le téléphone n'a pas de connexion.
 * Il ne met en cache AUCUNE page de l'espace client ni aucune réponse de l'API : seulement la page « hors connexion »
 * et ses fichiers. Tout le reste va toujours sur le réseau, comme sans service worker. */
'use strict';

var CACHE = {{ cache_name|tojson }};
var OFFLINE_URL = {{ offline_url|tojson }};
var FILES = {{ files|tojson }};

self.addEventListener('install', function (event) {
  // addAll échoue en bloc si un fichier manque : le service worker ne s'installe pas plutôt que d'être incomplet.
  event.waitUntil(
    caches.open(CACHE)
      .then(function (cache) { return cache.addAll(FILES); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (names) {
        return Promise.all(names
          .filter(function (name) { return name.indexOf('tev-') === 0 && name !== CACHE; })
          .map(function (name) { return caches.delete(name); }));
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (event) {
  var request = event.request;

  // Ouverture d'une page : toujours le réseau ; sans réseau, la page « hors connexion ».
  // ignoreVary : une seule version de chaque fichier est gardée, une en-tête « Vary » ne doit jamais l'empêcher de servir.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(function () {
        return caches.match(OFFLINE_URL, { ignoreVary: true }).then(function (page) { return page || Response.error(); });
      })
    );
    return;
  }

  // Les fichiers de la page « hors connexion » (style, polices) : le cache d'abord, pour qu'elle s'affiche sans réseau.
  if (request.method === 'GET') {
    var url = new URL(request.url);
    if (url.origin === self.location.origin && FILES.indexOf(url.pathname + url.search) !== -1) {
      event.respondWith(
        caches.match(request, { ignoreVary: true }).then(function (cached) { return cached || fetch(request); })
      );
    }
  }
  // Tout le reste (API, images, scripts) : on ne fait rien, le navigateur s'en occupe.
});
