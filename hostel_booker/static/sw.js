// Minimal service worker — required for Chrome/Edge to treat this as an
// installable PWA. We deliberately do NOT cache API/booking calls, since
// this app must always hit the live portal, not stale offline data.
const CACHE_NAME = "hostel-booker-v1";
const STATIC_ASSETS = [
  "/static/icon-192.png",
  "/static/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Network-first for everything — never serve a stale login/dashboard/booking
// response. Only static icon assets fall back to cache if offline.
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;

  event.respondWith(
    fetch(event.request).catch(() =>
      caches.match(event.request).then((cached) => cached || Response.error())
    )
  );
});
