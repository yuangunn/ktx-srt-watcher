// 발권창구 service worker — caches the app shell only.
// Data (config.json / state.json) is always fetched fresh from GitHub.

const VERSION = 'v17';
const SHELL = [
  './',
  './index.html',
  './manifest.json',
  './css/app.css',
  './js/app.js',
  './icons/icon-192.svg',
  './icons/icon-512.svg',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k)));
    await self.clients.claim();
    // Tell already-open windows that a new shell is live.  app.js listens
    // for this and soft-reloads (only when no dialog is open, so an
    // in-progress edit isn't lost).
    const wins = await self.clients.matchAll({ type: 'window' });
    wins.forEach(w => w.postMessage({ type: 'sw-updated', version: VERSION }));
  })());
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // Never cache GitHub API or font CDNs — must hit network.
  if (url.host !== self.location.host) return;
  if (e.request.method !== 'GET') return;
  // Network-first for the app shell: a fresh deploy shows up on the next
  // launch without waiting for a VERSION bump. Cache is only a fallback
  // for offline; we refresh it on every successful fetch.
  e.respondWith((async () => {
    try {
      const res = await fetch(e.request);
      if (res.ok) {
        const copy = res.clone();
        caches.open(VERSION).then(c => c.put(e.request, copy));
      }
      return res;
    } catch {
      const hit = await caches.match(e.request);
      return hit || caches.match('./index.html');
    }
  })());
});
