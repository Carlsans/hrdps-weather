// Weather radar (Environment Canada, via MSC GeoMet): the last ~3 hours in 6-minute steps (port of radar.py).
// Frames are WMS GetMap images in Web Mercator for a box around the current map view (rain rate, plus snow
// rate when it is cold enough to matter). They are fetched newest-first and shown as they arrive; the box is
// snapped to a coarse grid so small pans reuse the loaded frames; a debounced reload follows pans and zooms.

import { GEOMET, iso, parseIso } from "./hrdps.js";
import { get } from "./net.js";

export const RAIN = "RADAR_1KM_RRAI", SNOW = "RADAR_1KM_RSNO";
const MARGIN = 1.4;               // loaded area = view × MARGIN, so small pans need no reload
const MAX_PX = 800;
const WORKERS = 4;
const MAX_AGE = 360e3;            // ms before the loop is refreshed (radar updates every 6 min)
export const SNOW_BELOW_C = 6.0;  // request the snow layer only when the temperature is below this
export const R = 20037508.342789244;

/** UTC times (ms) of the most recent radar frames, from the layer's time dimension. */
export async function frameTimes(count = 31) {
  const s = await get(`${GEOMET}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities&LAYER=${RAIN}`, { timeout: 40000 });
  if (!s) return [];
  const i = s.indexOf(`<Name>${RAIN}</Name>`);
  const m = i >= 0 ? /<Dimension name="time"[^>]*>([^<]*)</.exec(s.slice(i, i + 5000)) : null;
  if (!m || m[1].split("/").length !== 3) return [];
  const [, b, step] = m[1].split("/");
  const end = parseIso(b.trim()), sm = /PT(\d+)M/.exec(step), mins = sm ? +sm[1] : 6;
  return Array.from({ length: count }, (_, k) => end - mins * 60e3 * (count - 1 - k));
}

/** Newest first, then every other frame going back, then the rest: a usable loop appears early. */
export function priority(times) {
  const n = times.length, order = [];
  for (let i = n - 1; i >= 0; i -= 2) order.push(i);
  for (let i = n - 2; i >= 0; i -= 2) order.push(i);
  return order.map((i) => times[i]);
}

/** Frame to load for a view: [bbox metres, image px, key]. Centre snapped to a grid, zoom to half levels. */
export function bucket(view, zoom, widgetPx) {
  const zb = Math.round(zoom * 2) / 2;
  const half = widgetPx / 2 * (2 * R / (256 * 2 ** zb)) * MARGIN, step = half / 3;
  const cx = Math.round((view[0] + view[2]) / 2 / step) * step;
  const cy = Math.round((view[1] + view[3]) / 2 / step) * step;
  const px = Math.trunc(Math.min(widgetPx * MARGIN, MAX_PX));
  return [[cx - half, cy - half, cx + half, cy + half], px, `${zb}_${Math.trunc(cx)}_${Math.trunc(cy)}_${px}`];
}

async function frame(layer, t, bbox, px) {
  const url = `${GEOMET}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=${layer}&CRS=EPSG:3857`
    + `&BBOX=${bbox.join(",")}&WIDTH=${px}&HEIGHT=${px}&FORMAT=image/png&TRANSPARENT=TRUE&TIME=${iso(t)}`;
  const blob = await get(url, { as: "blob" });
  if (!blob) return null;
  try {
    return await createImageBitmap(blob);
  } catch {
    return null;
  }
}

export class RadarLoader {
  constructor() {
    this.frames = [];          // [[utc ms, bitmap]] — rain, oldest first
    this.snow = new Map();     // utc ms -> bitmap (only filled when it is cold)
    this.bbox = null;          // metres, Web Mercator, of the loaded frames
    this.zoom = null;
    this.key = null;
    this.loadedAt = 0;
    this.version = 0;
    this.state = "idle";       // idle | loading | error
    this.done = this.total = 0;
    this._rain = new Map();
    this._want = null;
    this._running = false;
  }

  /** Ask for frames covering `view`; cheap to call every frame (debounced, no-op when covered). */
  request(view, zoom, widgetPx, wantSnow = false) {
    const covered = this.bbox !== null && Math.abs(zoom - (this.zoom || 0)) < 0.7
      && this.bbox[0] <= view[0] && this.bbox[1] <= view[1] && this.bbox[2] >= view[2] && this.bbox[3] >= view[3]
      && Date.now() - this.loadedAt < MAX_AGE && (!wantSnow || this.snow.size > 0);
    if (covered || (this.state === "error" && Date.now() - this.loadedAt < 20e3)) return;
    if (this._want === null || this._want.view.join() !== view.join() || this._want.zoom !== zoom) {
      this._want = { view, zoom, widgetPx, wantSnow, at: Date.now() + 400 };     // wait for the camera to settle
    }
    if (!this._running) this._run();
  }

  async _run() {
    this._running = true;
    try {
      for (;;) {
        const want = this._want;
        if (want === null) return;
        const delay = want.at - Date.now();
        if (delay > 0) { await new Promise((r) => setTimeout(r, delay)); continue; }
        this.state = "loading"; this.version++;
        let ok = false;
        try { ok = await this._load(want); } catch { ok = false; }
        if (this._want === want) this._want = null;                  // nothing newer was requested meanwhile
        this.state = ok ? "idle" : "error";
        this.loadedAt = Date.now();
        this.version++;
      }
    } finally {
      this._running = false;
    }
  }

  async _load({ view, zoom, widgetPx, wantSnow }) {
    const [bbox, px, key] = bucket(view, zoom, widgetPx);
    const times = await frameTimes();
    if (!times.length) return false;
    const order = priority(times), same = key === this.key, keep = new Set(times);
    const rain = same ? new Map(this._rain) : new Map();
    let got = 0;
    const publish = (firstOfNewKey) => {
      if (firstOfNewKey) {                                  // switch to the new box only once it has content
        this.bbox = bbox; this.zoom = zoom; this.key = key; this.snow = new Map();
      }
      this._rain = rain;
      this.frames = [...rain].filter(([t]) => keep.has(t)).sort((a, b) => a[0] - b[0]);
      this.version++;
    };
    for (const layer of [RAIN, SNOW]) {
      if (layer === SNOW && !wantSnow) break;
      const todo = layer === RAIN ? order.filter((t) => !rain.has(t)) : order;
      this.done = 0; this.total = todo.length;
      if (layer === SNOW && !same) this.snow = new Map();
      let next = 0;
      const worker = async () => {
        while (next < todo.length) {
          const t = todo[next++];
          const bmp = await frame(layer, t, bbox, px);
          this.done++;
          if (!bmp) continue;
          if (layer === RAIN) {
            rain.set(t, bmp);
            publish(got === 0 && !same);
            got++;
          } else {
            this.snow = new Map(this.snow).set(t, bmp);
            this.version++;
          }
        }
      };
      await Promise.all(Array.from({ length: WORKERS }, worker));
      if (layer === RAIN && got === 0 && rain.size === 0) return false;
    }
    this.loadedAt = Date.now();
    return true;
  }
}
