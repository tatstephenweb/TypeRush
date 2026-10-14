// TypeRush service worker.
// - Static files (JS, icons): served from cache, refreshed in the background.
// - Pages: always loaded from the server; if you are offline you see /offline instead.
// - /api/ calls are never cached, so scores and the leaderboard are always live.
const VERSION = "typerush-v2";            // change this number to force an update
const STATIC_CACHE = VERSION + "-static";
const PRECACHE = ["/offline", "/js/common.js", "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => !key.startsWith(VERSION)).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;    // CDN files: let the browser handle them
  if (url.pathname.startsWith("/api/")) return;       // live data: never cache

  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match("/offline")));
    return;
  }

  if ((url.pathname.startsWith("/js/") || url.pathname.startsWith("/icons/"))) {
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const cached = await cache.match(request);
        const fresh = fetch(request)
          .then((response) => { if (response.ok) cache.put(request, response.clone()); return response; })
          .catch(() => cached);
        return cached || fresh;
      })
    );
  }
});
