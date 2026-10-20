// Service worker: solo guarda la "carcasa" de la app para que abra aunque no haya cobertura.
// Los datos (/api/*) NUNCA se guardan en caché: siempre van a la red.
const CACHE = "conductores-v16"; // súbelo (v2, v3…) cuando cambies archivos de la carcasa
const SHELL = [
  "/", "/index.html", "/guia.html", "/styles.css", "/app.js", "/manifest.webmanifest",
  "/vendor/jsQR.js", "/icons/icon-192.png", "/icons/icon-512.png", "/icons/apple-touch-icon.png",
  "/img/trimodos.png", "/img/stinsa.png", "/img/transargi.png", "/fonts/atkinson-hyperlegible-next-latin-wght-normal.woff2",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  // Red primero (siempre la última versión si hay cobertura); caché si no hay red.
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
        return res;
      })
      .catch(() => caches.match(e.request, { ignoreSearch: true }).then((hit) => hit || caches.match("/index.html"))),
  );
});
