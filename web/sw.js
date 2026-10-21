// Service worker: makes the app installable and usable offline (with the last downloaded run).
//   - the app files are cached per version; a new version waits until the page asks to switch (banner)
//   - OpenStreetMap tiles are kept on disk (cache first, refreshed in the background after a week)
//   - GeoMet requests are never intercepted: weather data always comes from the network (or IndexedDB)
const VERSION = "1.8.1";
const BUILD = "dev";                  // replaced by the commit id when GitHub Pages deploys (pages.yml)
const SHELL = `shell-${VERSION}-${BUILD}`;
const TILES = "osm-tiles";
const TILE_HOST = "tile.openstreetmap.org";
const TILE_MAX_AGE = 7 * 864e5;
const TILE_MAX_COUNT = 4000;
const FILES = [
  "./", "index.html", "app.css", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png",
  "icons/icon-maskable-512.png", "js/app.js", "js/appupdate.js", "js/astro.js", "js/climate.js", "js/config.js", "js/hrdps.js", "js/net.js",
  "js/radar.js", "js/store.js", "js/tides.js", "js/tiles.js", "js/version.js", "js/view.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES.map((f) => new Request(f, { cache: "reload" })))));
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k.startsWith("shell-") && k !== SHELL) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("message", (e) => { if (e.data === "skip-waiting") self.skipWaiting(); });

let puts = 0;
async function trimTiles(cache) {
  const keys = await cache.keys();
  for (let k = 0; k < keys.length - TILE_MAX_COUNT; k++) await cache.delete(keys[k]);     // oldest first
}

async function tile(req) {
  const cache = await caches.open(TILES);
  const hit = await cache.match(req);
  const refresh = async () => {
    const r = await fetch(req);
    if (r.ok) {
      await cache.put(req, r.clone());
      if (++puts % 200 === 0) trimTiles(cache);
    }
    return r;
  };
  if (hit) {
    const age = Date.now() - Date.parse(hit.headers.get("date") || 0);
    if (!(age < TILE_MAX_AGE)) refresh().catch(() => {});
    return hit;
  }
  return refresh();
}

async function shell(req) {
  const cache = await caches.open(SHELL);
  if (BUILD === "dev") {                                    // local development: always the files on disk
    try { return await fetch(req, { cache: "no-store" }); } catch { /* offline: fall back to the cache */ }
  }
  const hit = await cache.match(req, { ignoreSearch: true });
  if (hit) return hit;
  try {
    return await fetch(req);
  } catch (err) {
    if (req.mode === "navigate") return (await cache.match("index.html")) || Response.error();
    throw err;
  }
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.hostname === TILE_HOST) e.respondWith(tile(req));
  else if (url.origin === self.location.origin) e.respondWith(shell(req));
});
