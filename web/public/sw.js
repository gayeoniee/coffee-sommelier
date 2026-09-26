/*
 * App-shell service worker. Classic (non-module) script so it can
 * `importScripts()` the routing rules without a bundler.
 *
 * - install: precache the app shell, then activate immediately.
 * - fetch: `/api/*`, SSE and any non-GET request always fall through to the
 *   network untouched (see sw-routes.js `classify`). Navigations are
 *   network-first with a cached / offline-page fallback. Static assets
 *   (`_next/static`, `icons/`) are cache-first.
 * - activate: drop any previous `cs-shell-*` cache and take control of open
 *   clients right away.
 */
importScripts("/sw-routes.js");

var CACHE = self.CS_ROUTES.CACHE;
var PRECACHE = self.CS_ROUTES.PRECACHE;
var classify = self.CS_ROUTES.classify;
var shouldCache = self.CS_ROUTES.shouldCache;

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches
      .open(CACHE)
      .then(function (cache) {
        return cache.addAll(PRECACHE);
      })
      .then(function () {
        return self.skipWaiting();
      })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches
      .keys()
      .then(function (keys) {
        return Promise.all(
          keys
            .filter(function (key) {
              return key.indexOf("cs-shell-") === 0 && key !== CACHE;
            })
            .map(function (key) {
              return caches.delete(key);
            })
        );
      })
      .then(function () {
        return self.clients.claim();
      })
  );
});

function putInCache(request, response) {
  if (shouldCache(response)) {
    var copy = response.clone();
    caches.open(CACHE).then(function (cache) {
      cache.put(request, copy);
    });
  }
  return response;
}

self.addEventListener("fetch", function (event) {
  var kind = classify(event.request);
  if (kind === "bypass") return;

  if (kind === "navigate") {
    event.respondWith(
      fetch(event.request)
        .then(function (response) {
          return putInCache(event.request, response);
        })
        .catch(function () {
          return caches.match(event.request).then(function (cached) {
            return cached || caches.match("/offline");
          });
        })
    );
    return;
  }

  // static: cache-first
  event.respondWith(
    caches.match(event.request).then(function (cached) {
      if (cached) return cached;
      return fetch(event.request).then(function (response) {
        return putInCache(event.request, response);
      });
    })
  );
});
