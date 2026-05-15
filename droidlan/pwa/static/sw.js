// droidlan PWA service worker — minimal app-shell cache.
// Goal: the shell (index, manifest, icons) survives a flaky uplink so the
// transfer UI keeps loading even after the phone briefly drops Wi-Fi. POSTs
// to "/" intentionally bypass cache — uploads must always hit the live server.

const CACHE = 'droidlan-shell-v1';
const SHELL = [
  '/pwa/',
  '/pwa/index.html',
  '/pwa/manifest.webmanifest',
  '/pwa/icon-192.png',
  '/pwa/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
    ).then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (!url.pathname.startsWith('/pwa')) return;
  event.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((cache) => cache.put(req, copy));
      }
      return res;
    }).catch(() => caches.match('/pwa/index.html'))),
  );
});
