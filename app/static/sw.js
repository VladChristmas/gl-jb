const CACHE_NAME = 'gl_jb-v3';
const STATIC_ASSETS = [
  '/static/manifest.json',
  '/static/style.css',
  '/static/icons/icon-192.png',
];

// Install event - cache static assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        console.log('[SW] Caching static assets');
        return cache.addAll(STATIC_ASSETS.map(url => new Request(url, { credentials: 'same-origin' })));
      })
      .then(() => self.skipWaiting())
  );
});

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((name) => name !== CACHE_NAME)
            .map((name) => caches.delete(name))
        );
      })
      .then(() => self.clients.claim())
  );
});

// Fetch event - network first for API, cache first for static
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  
  // Skip non-GET requests
  if (event.request.method !== 'GET') {
    return;
  }
  
  // Skip cross-origin requests
  if (url.origin !== location.origin) {
    return;
  }
  
  // API routes - network first
  if (url.pathname.startsWith('/orders/') && url.pathname.includes('upload')) {
    return; // Let browser handle uploads
  }
  
  // Static assets - stale while revalidate (instant paint, background update)
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(staleWhileRevalidate(event.request));
    return;
  }
  
  // HTML pages - network first with cache fallback
  if (event.request.headers.get('accept')?.includes('text/html')) {
    event.respondWith(networkFirst(event.request));
    return;
  }
  
  // Images - cache first
  if (event.request.headers.get('accept')?.includes('image/')) {
    event.respondWith(cacheFirst(event.request));
    return;
  }
  
  // Default - network first
  event.respondWith(networkFirst(event.request));
});

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);

  const networkUpdate = fetch(request)
    .then((response) => {
      if (response && response.ok) {
        cache.put(request, response.clone());
      }
      return response;
    })
    .catch(() => null);

  if (cached) {
    return cached;
  }

  const fresh = await networkUpdate;
  return fresh || new Response('', { status: 504 });
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  
  if (cached) {
    return cached;
  }
  
  try {
    const response = await fetch(request);
    if (response.ok) {
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    // Return offline page or error
    return new Response('Offline', { status: 503 });
  }
}

async function networkFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  
  try {
    const response = await fetch(request);
    if (response.ok) {
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const cached = await cache.match(request);
    if (cached) {
      return cached;
    }
    
    // Offline fallback for HTML requests: saved page → home → plain message
    if (request.headers.get('accept')?.includes('text/html')) {
      const saved = await caches.match(request);
      if (saved) return saved;
      const home = await caches.match('/');
      if (home) return home;
      return new Response('Offline', { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
    }
    
    return new Response('Offline', { status: 503 });
  }
}

// Background sync for offline uploads
self.addEventListener('sync', (event) => {
  if (event.tag === 'photo-upload') {
    event.waitUntil(syncPhotos());
  }
});

async function syncPhotos() {
  // Implementation for background sync of photos
  // Would require IndexedDB to store pending uploads
  console.log('[SW] Background sync for photos');
}