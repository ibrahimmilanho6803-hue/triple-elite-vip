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
  // Le réglage des routes, lui, est facultatif : voir routeRequests.
  event.waitUntil(
    Promise.all([routeRequests(event), caches.open(CACHE).then(function (cache) { return cache.addAll(FILES); })])
      .then(function () { return self.skipWaiting(); })
  );
});

// Un service worker s'arrête après quelques secondes d'inactivité, et sa remise en route prend de 0,1 s à plus d'une seconde
// sur un téléphone modeste. Sans réglage, chaque page ouverte après une pause (puis chacun de ses fichiers) attend ce
// démarrage AVANT d'être demandée au réseau, même quand le service worker ne fait que la lui passer.
// « Static routing » (Chrome 123 et suivants, donc Chrome sur Android) : tant que le service worker n'est pas démarré, la
// requête (GET seulement) part tout de suite au réseau (donc au cache du navigateur si la page y est) pendant qu'il démarre.
// La première réponse sert ; si le réseau échoue, c'est lui qui répond : la page « hors connexion » s'affiche comme avant.
// Les autres navigateurs ignorent ce réglage (voir « navigation preload » plus bas) ; s'il est refusé, rien ne change.
function routeRequests(event) {
  try {
    if (!event.addRoutes) { return null; }
    return Promise.resolve(event.addRoutes({
      condition: { requestMethod: 'GET', runningStatus: 'not-running' },
      source: 'race-network-and-fetch-handler'
    })).catch(function () { /* règle refusée : comme avant */ });
  } catch (error) {
    return null;
  }
}

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (names) {
        return Promise.all(names
          .filter(function (name) { return name.indexOf('tev-') === 0 && name !== CACHE; })
          .map(function (name) { return caches.delete(name); }));
      })
      .then(preloadPages)
      .then(function () { return self.clients.claim(); })
  );
});

// « Navigation preload » (Chrome, Safari 15.4+, Firefox 99+) : la même idée pour les pages seulement, dans les navigateurs sans
// « static routing » : le navigateur demande la page au serveur EN MÊME TEMPS qu'il (re)démarre le service worker, au lieu
// d'attendre qu'il soit prêt. Les navigateurs qui ne connaissent pas la fonction ne changent rien : la page est demandée
// comme avant, une fois le service worker prêt. L'activation, elle, ne dépend jamais de ce réglage.
function preloadPages() {
  var preload = self.registration && self.registration.navigationPreload;
  return preload ? preload.enable().catch(function () { /* tant pis : comme avant */ }) : null;
}

// Ouverture d'une page : toujours le réseau ; sans réseau, la page « hors connexion ».
// `preloadResponse` : la page déjà demandée par le navigateur (rien si la fonction est absente ou si ce n'est pas un GET).
// Sans elle, on demande la page ici. Dans les deux cas, une seule requête part.
// ignoreVary : une seule version de chaque fichier est gardée, une en-tête « Vary » ne doit jamais l'empêcher de servir.
function openPage(event) {
  return Promise.resolve(event.preloadResponse)
    .then(function (preloaded) { return preloaded || fetch(event.request); })
    .catch(function () {
      return caches.match(OFFLINE_URL, { ignoreVary: true }).then(function (page) { return page || Response.error(); });
    });
}

self.addEventListener('fetch', function (event) {
  var request = event.request;

  if (request.mode === 'navigate') {
    event.respondWith(openPage(event));
    return;
  }

  // Les fichiers de la page « hors connexion » (style, polices) : le cache du navigateur d'abord (il répond en quelques
  // millisecondes, le cache d'un service worker demande 20 à 100 ms et le style bloque l'affichage de la page), celui du
  // service worker seulement quand le réseau manque, pour que la page « hors connexion » reste mise en forme.
  if (request.method === 'GET') {
    var url = new URL(request.url);
    if (url.origin === self.location.origin && FILES.indexOf(url.pathname + url.search) !== -1) {
      event.respondWith(
        fetch(request).catch(function () {
          return caches.match(request, { ignoreVary: true }).then(function (file) { return file || Response.error(); });
        })
      );
    }
  }
  // Tout le reste (API, images, scripts) : on ne fait rien, le navigateur s'en occupe.
});
