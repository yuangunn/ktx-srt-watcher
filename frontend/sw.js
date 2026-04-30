// 발권창구 service worker — caches the app shell only.
// Data (config.json / state.json) is always fetched fresh from GitHub.

const VERSION = 'v12';
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
  e.respondWith(
    caches.match(e.request).then(hit => hit || fetch(e.request).then(res => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(VERSION).then(c => c.put(e.request, copy));
      }
      return res;
    }).catch(() => caches.match('./index.html')))
  );
});
