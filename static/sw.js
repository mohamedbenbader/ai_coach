const CACHE_NAME = 'coach-bot-v1';

// Assets à mettre en cache lors de l'installation
const PRECACHE = [
  '/',
  'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js',
];

// ── Install : précache des assets essentiels ──────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(PRECACHE))
  );
  self.skipWaiting();
});

// ── Activate : supprime les anciens caches ─────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ── Fetch : stratégie selon le type de requête ─────────────────────────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Appels API → réseau uniquement (données toujours fraîches)
  if (url.pathname.startsWith('/api/')) return;

  // Authentification → réseau uniquement
  if (url.pathname.startsWith('/login') || url.pathname.startsWith('/logout') || url.pathname.startsWith('/register')) return;

  // Navigation (HTML) → réseau en priorité, cache en fallback offline
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then(response => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, clone));
          return response;
        })
        .catch(() => caches.match('/').then(cached => cached || new Response(
          '<html><body style="background:#0f1117;color:#e2e8f0;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column;gap:16px">' +
          '<div style="font-size:48px">📶</div>' +
          '<h2>Hors ligne</h2>' +
          '<p style="color:#64748b">Connecte-toi à internet pour accéder à Coach Bot.</p>' +
          '</body></html>',
          { headers: { 'Content-Type': 'text/html' } }
        ))
      )
    );
    return;
  }

  // Assets statiques (JS, CSS, images) → cache en priorité
  event.respondWith(
    caches.match(request).then(cached => {
      if (cached) return cached;
      return fetch(request).then(response => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, clone));
        }
        return response;
      });
    })
  );
});
