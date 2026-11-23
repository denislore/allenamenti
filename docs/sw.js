/* Service worker della dashboard Allenamenti.
   - Pagina: sempre l'ultima versione quando c'è rete (scavalca la cache di 10 minuti di GitHub Pages),
     la copia salvata quando non c'è (uso offline, per esempio in montagna).
   - Librerie e immagini: copia salvata, aggiornata in background.
   - Dati cifrati (.enc) e stato: non passano di qui, li gestisce l'app (che ne tiene una copia per l'offline).
   - Notifiche push inviate da GitHub Actions e numero sull'icona. */
const VERSIONE = "v2";
const CACHE = "allenamenti-app-" + VERSIONE;
const FILE = ["./", "index.html", "chart.umd.min.js", "leaflet.js", "leaflet.css", "manifest.webmanifest",
  "icon-192.png", "icon-512.png", "icon-maskable-192.png", "icon-maskable-512.png", "apple-touch-icon.png", "images/layers.png", "images/layers-2x.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE)
    .then(c => Promise.all(FILE.map(f => c.add(new Request(f, { cache: "reload" })).catch(() => {}))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(k => Promise.all(k.filter(n => n.startsWith("allenamenti-app-") && n !== CACHE).map(n => caches.delete(n))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== self.location.origin) return;           // GitHub, mappe, meteo: niente cache qui
  if (/\.(enc|json)$/.test(u.pathname)) return;                                  // dati e stato: li gestisce l'app
  if (r.mode === "navigate" || u.pathname.endsWith("/") || u.pathname.endsWith("/index.html")) {
    e.respondWith(fetch(u.origin + u.pathname, { cache: "no-cache", credentials: "same-origin" })
      .then(res => { if (res.ok) { const copia = res.clone(); caches.open(CACHE).then(c => c.put("index.html", copia)); } return res; })
      .catch(() => caches.match("index.html").then(x => x || caches.match("./"))));
    return;
  }
  e.respondWith(caches.match(r).then(salvata => {
    const rete = fetch(r).then(res => { if (res.ok) { const copia = res.clone(); caches.open(CACHE).then(c => c.put(r, copia)); } return res; })
      .catch(() => salvata);
    return salvata || rete;
  }));
});

self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data ? e.data.text() : "" }; }
  const lavori = [self.registration.showNotification(d.title || "Allenamenti", {
    body: d.body || "", icon: "icon-192.png", badge: "icon-192.png", tag: d.tag || undefined, data: { url: d.url || "./" } })];
  if (d.badge != null && self.navigator.setAppBadge)
    lavori.push((d.badge ? self.navigator.setAppBadge(d.badge) : self.navigator.clearAppBadge()).catch(() => {}));
  e.waitUntil(Promise.all(lavori));
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = new URL(e.notification.data?.url || "./", self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(finestre => {
    for (const w of finestre) {
      if ("focus" in w) return (w.navigate ? w.navigate(url) : Promise.resolve()).then(() => w.focus()).catch(() => w.focus());
    }
    return self.clients.openWindow(url);
  }));
});
