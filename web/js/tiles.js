// Base-map tiles (port of tiles.py): OpenStreetMap raster tiles, darkened to match the interface.
// Fetched on demand by two workers (the OSM tile policy forbids bulk downloads), kept as ready-to-draw canvases
// in a small LRU; the service worker keeps the raw tiles on disk. While a tile is missing the caller falls back
// to a coarser ancestor tile.

import { blocked } from "./net.js";

const TILE_URL = (z, x, y) => `https://tile.openstreetmap.org/${z}/${x}/${y}.png`;
export const MAX_ZOOM = 12;
const MEM_TILES = 300;
const RETRY_AFTER = 60e3;            // ms before a failed tile is requested again

// OSM's light style -> dark UI tint (same fixed mapping as the desktop app, so neighbouring tiles match):
// grey = PIL luma, inverted, compressed to 18..212, then tinted slightly blue.
const LUT = (() => {
  const t = new Uint8Array(256 * 3);
  for (let l = 0; l < 256; l++) {
    const g = Math.trunc(18 + (255 - l) * 0.76);
    t[l * 3] = Math.trunc(g * 0.95); t[l * 3 + 1] = g; t[l * 3 + 2] = Math.min(255, Math.trunc(g * 1.25 + 4));
  }
  return t;
})();

function canvas(w, h) {
  if (typeof OffscreenCanvas !== "undefined") return new OffscreenCanvas(w, h);
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  return c;
}

function darken(bitmap) {
  const c = canvas(bitmap.width, bitmap.height), ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(bitmap, 0, 0);
  const img = ctx.getImageData(0, 0, c.width, c.height), p = img.data;
  for (let k = 0; k < p.length; k += 4) {
    const l = (p[k] * 19595 + p[k + 1] * 38470 + p[k + 2] * 7471 + 0x8000) >> 16;      // PIL "L" conversion
    p[k] = LUT[l * 3]; p[k + 1] = LUT[l * 3 + 1]; p[k + 2] = LUT[l * 3 + 2]; p[k + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

export class TileCache {
  constructor({ network = true } = {}) {
    this.network = network;
    this.version = 0;                 // bumped when a tile arrives (the UI redraws on change)
    this._mem = new Map();
    this._pending = new Set();
    this._failed = new Map();
    this._queue = [];
    this._workers = 0;
  }

  /** Ready canvas for the tile, or null (and queue its download). */
  get(z, x, y, schedule = true) {
    const key = `${z}/${x}/${y}`;
    const s = this._mem.get(key);
    if (s) {
      this._mem.delete(key); this._mem.set(key, s);           // LRU: most recent last
      return s;
    }
    if (schedule && this.network && z <= MAX_ZOOM) this._schedule(key);
    return null;
  }

  /** [canvas, z, x, y] of the tile or, while it loads, its nearest cached ancestor; null if nothing. */
  getBest(z, x, y) {
    for (let k = 0; k < 4; k++) {
      const zz = z - k;
      if (zz < 0) break;
      const s = this.get(zz, x >> k, y >> k, k === 0);
      if (s) return [s, zz, x >> k, y >> k];
    }
    return null;
  }

  _schedule(key) {
    if (this._pending.has(key) || Date.now() - (this._failed.get(key) || 0) < RETRY_AFTER) return;
    this._pending.add(key);
    this._queue.push(key);
    if (this._workers < 2) { this._workers++; this._work(); }      // one more worker per request, up to two
  }

  async _work() {
    while (this._queue.length) {
      const key = this._queue.pop();                          // newest request first: the current view
      const [z, x, y] = key.split("/").map(Number);
      try {
        // Android, roaming: no download. The service worker still answers from the tiles it already keeps ("#offline").
        if (blocked() && !navigator.serviceWorker?.controller) throw new Error("blocked");
        const r = await fetch(TILE_URL(z, x, y) + (blocked() ? "#offline" : ""), { mode: "cors" });
        if (!r.ok) throw new Error(r.status);
        const bmp = await createImageBitmap(await r.blob());
        this._mem.set(key, darken(bmp));
        bmp.close?.();
        while (this._mem.size > MEM_TILES) this._mem.delete(this._mem.keys().next().value);
        this.version++;
      } catch {
        this._failed.set(key, Date.now());
      } finally {
        this._pending.delete(key);
      }
    }
    this._workers--;
  }
}
