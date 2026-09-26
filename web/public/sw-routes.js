/*
 * Pure routing rules for the service worker app-shell cache.
 *
 * This is a plain (non-module) script, not ESM: the service worker loads it
 * via `importScripts("/sw-routes.js")` with no bundler, and a classic script
 * cannot contain `import`/`export` statements. It exposes its API on
 * `self.CS_ROUTES` instead. Tests load this file's source and evaluate it
 * with a fake `self` (see tests/sw-routes.test.ts) to get the same object
 * without a real service worker or browser.
 *
 * Bump CACHE's version suffix whenever the precached app shell changes so
 * `activate` can drop the old cache.
 */
(function (self) {
  "use strict";

  var CACHE = "cs-shell-v2";
  var PRECACHE = ["/", "/onboarding", "/me", "/offline"];

  // classify(request) -> "bypass" | "navigate" | "static"
  //  - bypass: never touched by the service worker (API calls, SSE, any
  //    non-GET request, anything that isn't a navigation or a known static
  //    asset). `/api/*` must ALWAYS be bypass.
  //  - navigate: full-page navigations, served network-first with an
  //    offline-page fallback.
  //  - static: `_next/static` chunks and app icons, served cache-first.
  function classify(request) {
    if (request.method !== "GET") return "bypass";

    var pathname;
    try {
      pathname = new URL(request.url).pathname;
    } catch {
      pathname = request.url;
    }

    if (pathname.indexOf("/api/") === 0) return "bypass";
    if (request.mode === "navigate") return "navigate";
    if (pathname.indexOf("/_next/static/") === 0 || pathname.indexOf("/icons/") === 0) {
      return "static";
    }
    return "bypass";
  }

  // shouldCache(response) -> boolean: whether a fetched response is worth putting in the
  // app-shell cache. Opaque/error responses (4xx/5xx, or a network failure surfaced as a
  // non-ok response) must never be cached, or a later offline visit would replay the failure.
  function shouldCache(response) {
    return !!response && response.ok && response.status < 400;
  }

  self.CS_ROUTES = { CACHE: CACHE, PRECACHE: PRECACHE, classify: classify, shouldCache: shouldCache };
})(self);
