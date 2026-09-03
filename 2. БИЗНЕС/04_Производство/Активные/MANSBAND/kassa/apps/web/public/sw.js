// Оставлен как no-op: старые клиенты могут ещё держать регистрацию.
// Новый фронт снимает SW в main.tsx. Не кэшируем ничего — иначе Safari
// после деплоя отдаёт устаревший index.html с битыми hashed-чанками.
const CACHE = "kassa-shell-v3-noop";

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.map((k) => caches.delete(k))))
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});

// Не перехватываем fetch — только сеть.
self.addEventListener("fetch", () => {});
