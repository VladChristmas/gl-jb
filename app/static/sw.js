const CACHE_NAME = 'gl_jb-v7';
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

// Fetch event
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

  // Только статические ассеты. Приватные HTML-страницы, API, /uploads/*
  // и изображения в Cache Storage НЕ кладутся: иначе после logout (и в
  // другой вкладке) браузер продолжает отдавать устаревшую
  // авторизованную страницу/фото из кэша.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(staleWhileRevalidate(event.request));
    return;
  }

  // Всё остальное — только сеть, без записи в кэш
  event.respondWith(networkOnly(event.request));
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

// Только сеть: ответ не сохраняется, при отсутствии сети — заглушка для HTML.
async function networkOnly(request) {
  try {
    return await fetch(request);
  } catch (error) {
    if (request.headers.get('accept')?.includes('text/html')) {
      return new Response('Нет соединения с сервером. Повторите попытку позже.', {
        status: 503,
        headers: { 'Content-Type': 'text/plain; charset=utf-8' },
      });
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