// Canvas rendering of the dashboard: animated map, time slider and charts (port of view.py).
// The desktop draws one fixed 1400×924 picture; here each part is its own canvas sized by the page layout,
// so the drawing code takes its width and height as parameters. Text is drawn in CSS pixels.

import { fr, fx, fg, localParts } from "./hrdps.js";
import { RadarLoader, R, SNOW_BELOW_C } from "./radar.js";

// ── Theme (Catppuccin mocha) ─────────────────────────────────────────────────
const hx = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
export const BG = hx("#1e1e2e"), SURFACE = hx("#181825"), PANEL = hx("#11111b");
export const TEXT = hx("#cdd6f4"), SUBTLE = hx("#a6adc8"), MUTED = hx("#6c7086");
export const RED = hx("#f38ba8"), PEACH = hx("#fab387"), YELLOW = hx("#f9e2af"), GREEN = hx("#a6e3a1");
export const TEAL = hx("#94e2d5"), SKY = hx("#89dceb"), BLUE = hx("#89b4fa"), LAV = hx("#b4befe"), MAUVE = hx("#cba6f7");
const SNOWC = hx("#e6e9ff");
const rgba = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

export const LAYERS = [["rt", "💧", "Précip."], ["tt", "🌡️", "Temp."], ["ws", "💨", "Vent"], ["nt", "☁️", "Nuages"], ["radar", "📡", "Radar"]];
const LAYER_TITLE = { rt: "Précipitations (prévision HRDPS)", tt: "Température à 2 m (HRDPS)", ws: "Vent à 10 m (HRDPS)",
  nt: "Couverture nuageuse (HRDPS)", radar: "Radar météo (observé, ECCC)" };
const LAYER_SHORT = { rt: "Précipitations", tt: "Température à 2 m", ws: "Vent à 10 m", nt: "Nuages", radar: "Radar (observé)" };
// Camera zoom: 7.55 showed the 3-tile block in the desktop's 560-px map; other widths keep the same span.
const DESIGN_MAP = 560, DESIGN_Z = 7.55, MIN_Z = 5.5, MAX_Z = 11.0;
const OV = 160;                   // model overlay raster size (square), scaled up with bilinear filtering
// Official radar palette (GeoMet legend, 0.1 → 200 mm/h), evenly spaced on the legend.
const RADAR_COLORS = [[133, 197, 254], [0, 174, 222], [0, 241, 76], [0, 186, 0], [0, 133, 0], [106, 165, 0], [254, 231, 0],
  [254, 176, 0], [254, 123, 0], [254, 34, 0], [254, 1, 108], [178, 39, 191], [112, 11, 163], [59, 0, 89]];
const RADAR_TICKS = [0.1, 1, 2, 4, 8, 12, 16, 24, 32, 50, 64, 100, 125, 200];
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"];
export const compass = (deg) => COMPASS[Math.trunc(((deg % 360) + 360) % 360 / 22.5 + 0.5) % 16];

// ── drawing helpers ──────────────────────────────────────────────────────────
const FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", sans-serif';

/** Text at (x, y): anchor l|c|r, va t|m|b (y is the top, middle or bottom of the line). Returns its width. */
export function text(ctx, s, x, y, size = 12, color = TEXT, bold = false, anchor = "l", va = "t", alpha = 1) {
  ctx.font = `${bold ? "600 " : ""}${size}px ${FONT}`;
  ctx.textAlign = anchor === "r" ? "right" : anchor === "c" ? "center" : "left";
  ctx.textBaseline = va === "m" ? "middle" : va === "b" ? "bottom" : "top";
  ctx.fillStyle = rgba(color, alpha);
  ctx.fillText(String(s), x, y);
  return ctx.measureText(String(s)).width;
}

function rrect(ctx, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

const fill = (ctx, color, a = 1) => { ctx.fillStyle = rgba(color, a); ctx.fill(); };

function newCanvas(w, h) {
  if (typeof OffscreenCanvas !== "undefined") return new OffscreenCanvas(w, h);
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  return c;
}

// ── Web Mercator (256-px tiles) ──────────────────────────────────────────────
const worldX = (lon, z) => (lon + 180) / 360 * 256 * 2 ** z;
const worldY = (lat, z) => (1 - Math.asinh(Math.tan(lat * Math.PI / 180)) / Math.PI) / 2 * 256 * 2 ** z;
const worldLon = (wx, z) => wx / (256 * 2 ** z) * 360 - 180;
const worldLat = (wy, z) => Math.atan(Math.sinh(Math.PI * (1 - 2 * wy / (256 * 2 ** z)))) * 180 / Math.PI;

// ── colour ramps for the map overlays: [value, r, g, b, alpha 0..255] ────────
const RAIN_STOPS = [[0.0, 90, 160, 255, 0], [0.05, 100, 170, 255, 90], [0.5, 50, 120, 255, 165], [1, 40, 200, 240, 195],
  [2, 60, 220, 120, 215], [4, 250, 230, 60, 230], [8, 255, 150, 40, 240], [16, 255, 60, 60, 245], [32, 225, 60, 225, 250]];
const SNOW_STOPS = [[0.0, 215, 225, 255, 0], [0.05, 215, 228, 255, 100], [0.5, 200, 205, 255, 165], [1, 175, 160, 255, 195],
  [2, 190, 110, 255, 215], [4, 235, 95, 235, 230], [8, 255, 80, 170, 245], [16, 255, 70, 90, 250]];
const TEMP_STOPS = [[-30, 170, 120, 255, 150], [-15, 80, 120, 255, 150], [-5, 80, 200, 255, 150], [0, 140, 240, 240, 150],
  [5, 120, 230, 140, 150], [12, 230, 240, 90, 150], [20, 255, 190, 60, 150], [28, 255, 110, 50, 150], [35, 230, 50, 80, 150]];
const WIND_STOPS = [[0, 40, 50, 120, 30], [10, 60, 120, 230, 95], [25, 70, 210, 200, 140], [40, 240, 220, 70, 175],
  [60, 255, 140, 50, 200], [80, 240, 60, 80, 220], [100, 230, 80, 230, 235]];
const CLOUD_STOPS = [[0, 255, 255, 255, 0], [100, 255, 255, 255, 190]];
const LEGENDS = {
  rt: [RAIN_STOPS, [0.1, 0.5, 1, 2, 4, 8, 16, 32], "mm/h"],
  tt: [TEMP_STOPS, [-30, -15, -5, 0, 5, 12, 20, 28, 35], "°C"],
  ws: [WIND_STOPS, [0, 10, 25, 40, 60, 80, 100], "km/h"],
  nt: [CLOUD_STOPS, [0, 25, 50, 75, 100], "%"],
};

/** np.interp of the 4 channels at x (clamped to the end stops), written into out[o..o+3]. */
function ramp(stops, x, out, o) {
  const n = stops.length;
  if (!(x > stops[0][0])) { const s = stops[0]; out[o] = s[1]; out[o + 1] = s[2]; out[o + 2] = s[3]; out[o + 3] = s[4]; return; }
  if (x >= stops[n - 1][0]) { const s = stops[n - 1]; out[o] = s[1]; out[o + 1] = s[2]; out[o + 2] = s[3]; out[o + 3] = s[4]; return; }
  let k = 1;
  while (stops[k][0] < x) k++;
  const a = stops[k - 1], b = stops[k], f = (x - a[0]) / (b[0] - a[0]);
  for (let c = 1; c <= 4; c++) out[o + c - 1] = a[c] + (b[c] - a[c]) * f;
}

const _rain = new Float32Array(4), _snow = new Float32Array(4);
/** Straight-alpha RGBA (0..255) of value a of a layer, written into out[o..o+3]; tt mixes rain into snow. */
function colorize(layer, a, tt, out, o) {
  if (layer === "rt") {
    ramp(RAIN_STOPS, a, _rain, 0); ramp(SNOW_STOPS, a, _snow, 0);
    const w = Math.min(Math.max((1.5 - tt) / 2.0, 0), 1);
    for (let c = 0; c < 4; c++) out[o + c] = _rain[c] * (1 - w) + _snow[c] * w;
  } else if (layer === "tt") ramp(TEMP_STOPS, a, out, o);
  else if (layer === "ws") ramp(WIND_STOPS, a * 3.6, out, o);
  else { out[o] = out[o + 1] = out[o + 2] = 255; out[o + 3] = Math.min(Math.max(a, 0), 100) / 100 * 190; }
}

// ── the map ──────────────────────────────────────────────────────────────────
export class MapView {
  constructor(data, place, tiles) {
    this.d = data;
    this.place = place;
    this.tiles = tiles;
    this.i0 = data.nowIndex(); this.i1 = data.n - 1;
    this.t = this.i0;
    this.layer = "rt"; this.playing = true; this.speed = 2.0;      // speed: model hours per second
    this.maps = null;
    this.radar = null;
    this.rt = 0;                                                    // radar frame cursor (float index)
    this.prefetchRadar = false;
    this.w = DESIGN_MAP; this.h = DESIGN_MAP;
    this.hits = [];
    this.reset();
    this._ov = newCanvas(OV, OV);
    this._ovImg = new ImageData(OV, OV);
    this._seen = "";
  }

  setData(data) {
    const now = data.nowIndex();
    const off = this.t - this.i0;
    this.d = data; this.i0 = now; this.i1 = data.n - 1;
    this.t = Math.min(Math.max(this.i0 + off, this.i0), this.i1);
  }

  // -- camera ----------------------------------------------------------------
  defaultZ() { return DESIGN_Z + Math.log2(Math.max(this.w, 200) / DESIGN_MAP); }
  reset() { this.clon = this.place.longitude; this.clat = this.place.latitude; this.z = this.defaultZ(); this._userZoomed = false; }
  resize(w, h) {
    if (!this._userZoomed) { this.w = w; this.h = h; this.z = this.defaultZ(); return; }
    this.w = w; this.h = h;
  }
  center() { return [worldX(this.clon, this.z), worldY(this.clat, this.z)]; }
  setCenter(wx, wy) {
    this.clon = Math.min(Math.max(worldLon(wx, this.z), -179.9), 179.9);
    this.clat = Math.min(Math.max(worldLat(wy, this.z), -80), 80);
  }
  /** Zoom by dz levels keeping the point under (ax, ay) (canvas coordinates) fixed. */
  zoomBy(dz, ax = this.w / 2, ay = this.h / 2) {
    const [cx, cy] = this.center();
    const lon = worldLon(cx + ax - this.w / 2, this.z), lat = worldLat(cy + ay - this.h / 2, this.z);
    this.z = Math.min(Math.max(this.z + dz, MIN_Z), MAX_Z);
    this._userZoomed = true;
    this.setCenter(worldX(lon, this.z) - (ax - this.w / 2), worldY(lat, this.z) - (ay - this.h / 2));
  }
  beginPan() { this._pan0 = this.center(); }
  panTo(dx, dy) { if (this._pan0) this.setCenter(this._pan0[0] - dx, this._pan0[1] - dy); }

  hit(x, y) {
    for (let k = this.hits.length - 1; k >= 0; k--) {
      const [[rx, ry, rw, rh], tag, arg] = this.hits[k];
      if (rx <= x && x <= rx + rw && ry <= y && y <= ry + rh) return [tag, arg];
    }
    return [null, null];
  }

  /** True when something that arrived in the background (tiles, radar) should trigger a redraw. */
  poll() {
    const seen = `${this.tiles ? this.tiles.version : -1}/${this.radar ? this.radar.version : -1}`;
    const changed = seen !== this._seen;
    this._seen = seen;
    return changed;
  }

  tick(dt) {
    if (!this.playing) return;
    if (this.layer === "radar") {
      const n = this.radar ? this.radar.frames.length : 0;
      if (n) {
        this.rt += dt * this.speed * 2;                       // ~4 frames/s at the default speed
        if (this.rt >= n + 1.5) this.rt = 0;                  // hold on the latest frame, then loop
      }
      return;
    }
    this.t += dt * this.speed;
    if (this.t >= this.i1) this.t = this.i0;
  }

  timeIndex() { return Math.min(Math.max(Math.round(this.t), this.i0), this.i1); }

  timeLabel() {
    if (this.layer === "radar") {
      const f = this.radar ? this.radar.frames : [];
      if (!f.length) return this.radar && this.radar.state === "loading" ? "chargement…" : "—";
      const t = f[Math.min(Math.round(this.rt), f.length - 1)][0];
      const ago = Math.round((f.at(-1)[0] - t) / 60e3);
      return `${fr(localParts(t, this.d.tz), "%Hh%M")} · ` + (ago === 0 ? "dernière image" : `il y a ${ago} min`);
    }
    return fr(this.d.local[Math.min(Math.round(this.t), this.i1)], "%a %d %b · %Hh");
  }

  // -- grids -----------------------------------------------------------------
  /** Layer k at model hour i on the overlay raster: wide grid first, region grid on top. NaN outside. */
  _sample(k, i, lons, lats, out) {
    out.fill(NaN);
    for (const part of [this.maps.wide, this.maps.region]) {
      if (!part) continue;
      const g = part.grids[k], [w, s, e, n] = part.bounds, { ny, nx } = g, base = i * ny * nx, a = g.data;
      for (let r = 0; r < OV; r++) {
        let row = (n - lats[r]) / (n - s) * ny - 0.5;
        if (row < -0.5 || row > ny - 0.5) continue;
        row = Math.min(Math.max(row, 0), ny - 1);
        const r0 = Math.floor(row), r1 = Math.min(r0 + 1, ny - 1), fr_ = row - r0;
        for (let c = 0; c < OV; c++) {
          let col = (lons[c] - w) / (e - w) * nx - 0.5;
          if (col < -0.5 || col > nx - 0.5) continue;
          col = Math.min(Math.max(col, 0), nx - 1);
          const c0 = Math.floor(col), c1 = Math.min(c0 + 1, nx - 1), fc = col - c0;
          const v = (q) => (Number.isNaN(q) ? 0 : q);                      // np.nan_to_num
          out[r * OV + c] = v(a[base + r0 * nx + c0]) * (1 - fr_) * (1 - fc) + v(a[base + r0 * nx + c1]) * (1 - fr_) * fc
            + v(a[base + r1 * nx + c0]) * fr_ * (1 - fc) + v(a[base + r1 * nx + c1]) * fr_ * fc;
        }
      }
    }
    return out;
  }

  _sampleT(k, lons, lats, out, tmp) {
    const i = Math.floor(this.t), f = this.t - i, j = Math.min(i + 1, this.i1);
    this._sample(k, i, lons, lats, out);
    if (f < 1e-3 || j === i) return out;
    this._sample(k, j, lons, lats, tmp);
    for (let p = 0; p < out.length; p++) out[p] = out[p] * (1 - f) + tmp[p] * f;
    return out;
  }

  _viewLonLat(n) {
    const [cx, cy] = this.center();
    const lons = new Float64Array(n), lats = new Float64Array(n);
    for (let k = 0; k < n; k++) {
      lons[k] = worldLon(cx + (k + 0.5) / n * this.w - this.w / 2, this.z);
      lats[k] = worldLat(cy + (k + 0.5) / n * this.h - this.h / 2, this.z);
    }
    return [lons, lats];
  }

  viewMeters() {
    const [cx, cy] = this.center(), n = 256 * 2 ** this.z;
    const toX = (wx) => (wx / n * 2 - 1) * R, toY = (wy) => (1 - wy / n * 2) * R;
    return [toX(cx - this.w / 2), toY(cy + this.h / 2), toX(cx + this.w / 2), toY(cy - this.h / 2)];
  }

  requestRadar() {
    if (!this.radar) this.radar = new RadarLoader();
    const cold = (this.d.tt[this.d.nowIndex()] || 99) < SNOW_BELOW_C;      // snow layer only matters when cold
    this.radar.request(this.viewMeters(), this.z, this.w, cold);
  }

  // -- drawing -----------------------------------------------------------------
  draw(ctx) {
    const { w, h } = this, narrow = w < 480;
    this.hits = [[[0, 0, w, h], "map", null]];
    ctx.save();
    rrect(ctx, 0, 0, w, h, 12); ctx.clip();
    ctx.fillStyle = rgba(PANEL); ctx.fillRect(0, 0, w, h);
    if (this.tiles) this._drawTiles(ctx);
    const lay = this.layer;
    if (lay !== "radar" && this.prefetchRadar && !this.radar) this.requestRadar();   // ready when the user opens it
    if (lay === "radar") this._drawRadar(ctx);
    else if (this.maps) this._drawOverlay(ctx, lay);
    else text(ctx, this.status || "Cartes en téléchargement…", w / 2, h / 2, 15, SUBTLE, false, "c", "m");
    this._drawMarker(ctx);
    ctx.restore();
    // caption, zoom buttons, scale bar, legend, attribution
    ctx.save(); rrect(ctx, 0, 0, w, 30, 12); ctx.clip();
    ctx.fillStyle = rgba(PANEL, 0.72); ctx.fillRect(0, 0, w, 30); ctx.restore();
    text(ctx, narrow ? LAYER_SHORT[lay] : LAYER_TITLE[lay], 12, 15, narrow ? 13 : 14, TEXT, true, "l", "m");
    text(ctx, this.timeLabel(), w - 12, 15, narrow ? 13 : 14, SUBTLE, true, "r", "m");
    this._drawZoomButtons(ctx);
    this._drawScale(ctx, 12, 44);
    this._drawLegend(ctx, 12, h - 32, w - 24);
    const credit = "© contributeurs OpenStreetMap · " + (lay === "radar" ? "radar ECCC" : "données HRDPS © ECCC");
    text(ctx, credit, 12, h - 15, 9, SUBTLE, false, "l", "t", 0.8);
  }

  _drawTiles(ctx) {
    const { w, h } = this, tc = this.tiles;
    const zt = Math.trunc(Math.min(Math.max(Math.round(this.z), 2), 12));
    const s = 2 ** (this.z - zt);
    const cxz = worldX(this.clon, zt), cyz = worldY(this.clat, zt);
    const hw = w / 2 / s, hh = h / 2 / s, n = 2 ** zt;
    ctx.imageSmoothingEnabled = true;
    for (let ty = Math.floor((cyz - hh) / 256); ty <= Math.floor((cyz + hh) / 256); ty++) {
      if (ty < 0 || ty >= n) continue;
      for (let tx = Math.floor((cxz - hw) / 256); tx <= Math.floor((cxz + hw) / 256); tx++) {
        const txm = ((tx % n) + n) % n;
        const best = tc.getBest(zt, txm, ty);
        if (!best) continue;
        const [img, zz, xx, yy] = best, f = 2 ** (zt - zz), sub = img.width / f;   // ancestor covers f×f tiles
        ctx.drawImage(img, (txm - xx * f) * sub, (ty - yy * f) * sub, sub, sub,
          w / 2 + (tx * 256 - cxz) * s, h / 2 + (ty * 256 - cyz) * s, 256 * s + 0.6, 256 * s + 0.6);
      }
    }
  }

  _drawOverlay(ctx, lay) {
    const [lons, lats] = this._viewLonLat(OV);
    const a = this._sampleT(lay, lons, lats, this._a ||= new Float32Array(OV * OV), this._tmp ||= new Float32Array(OV * OV));
    const tt = lay === "rt" ? this._sampleT("tt", lons, lats, this._tt ||= new Float32Array(OV * OV), this._tmp) : null;
    const px = this._ovImg.data, rgba4 = this._px ||= new Float32Array(4);
    for (let p = 0; p < OV * OV; p++) {
      const v = a[p];
      if (Number.isNaN(v)) { px[p * 4 + 3] = 0; continue; }
      const t = tt ? (Number.isNaN(tt[p]) ? 10 : tt[p]) : 0;
      colorize(lay, lay === "rt" ? v * 3600 : v, t, rgba4, 0);              // rt: kg/m²/s → mm/h
      px[p * 4] = rgba4[0]; px[p * 4 + 1] = rgba4[1]; px[p * 4 + 2] = rgba4[2]; px[p * 4 + 3] = rgba4[3];
    }
    this._ov.getContext("2d").putImageData(this._ovImg, 0, 0);
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
    ctx.drawImage(this._ov, 0, 0, this.w, this.h);
    if (lay === "ws") this._drawArrows(ctx);
  }

  _drawArrows(ctx, n = 13) {
    const { w, h } = this, [cx, cy] = this.center();
    const lons = new Float64Array(n), lats = new Float64Array(n);
    for (let k = 0; k < n; k++) {
      lons[k] = worldLon(cx + ((k + 0.5) / n * w - w / 2), this.z);
      lats[k] = worldLat(cy + ((k + 0.5) / n * h - h / 2), this.z);
    }
    const i = Math.floor(this.t), f = this.t - i, j = Math.min(i + 1, this.i1);
    const at = (key, hour, lon, lat) => {
      let out = NaN;
      for (const part of [this.maps.wide, this.maps.region]) {
        if (!part) continue;
        const g = part.grids[key], [W, S, E, N] = part.bounds, { ny, nx } = g;
        const col = (lon - W) / (E - W) * nx - 0.5, row = (N - lat) / (N - S) * ny - 0.5;
        if (col < -0.5 || col > nx - 0.5 || row < -0.5 || row > ny - 0.5) continue;
        const R_ = Math.min(Math.max(row, 0), ny - 1), C_ = Math.min(Math.max(col, 0), nx - 1);
        const r0 = Math.floor(R_), c0 = Math.floor(C_), r1 = Math.min(r0 + 1, ny - 1), c1 = Math.min(c0 + 1, nx - 1);
        const fr_ = R_ - r0, fc = C_ - c0, b = hour * ny * nx, d = g.data, v = (q) => (Number.isNaN(q) ? 0 : q);
        out = v(d[b + r0 * nx + c0]) * (1 - fr_) * (1 - fc) + v(d[b + r0 * nx + c1]) * (1 - fr_) * fc
          + v(d[b + r1 * nx + c0]) * fr_ * (1 - fc) + v(d[b + r1 * nx + c1]) * fr_ * fc;
      }
      return out;
    };
    const uv = (hour, lon, lat) => {
      const sp = at("ws", hour, lon, lat), wd = at("wd", hour, lon, lat) * Math.PI / 180;
      return [-sp * Math.sin(wd), -sp * Math.cos(wd)];
    };
    ctx.lineWidth = 1.6; ctx.lineCap = "round"; ctx.strokeStyle = "rgba(255,255,255,0.9)";
    for (let r = 0; r < n; r++) {
      for (let c = 0; c < n; c++) {
        const [u0, v0] = uv(i, lons[c], lats[r]), [u1, v1] = uv(j, lons[c], lats[r]);
        const u = u0 * (1 - f) + u1 * f, v = v0 * (1 - f) + v1 * f, sp = Math.hypot(u, v);
        if (!(sp >= 0.5)) continue;
        const L = 9 + Math.min(sp, 25) * 0.55;
        ctx.save(); ctx.translate((c + 0.5) / n * w, (r + 0.5) / n * h); ctx.rotate(Math.atan2(u, v));
        ctx.beginPath(); ctx.moveTo(0, L / 2); ctx.lineTo(0, -L / 2); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(-3.5, -L / 2 + 5); ctx.lineTo(0, -L / 2); ctx.lineTo(3.5, -L / 2 + 5); ctx.stroke();
        ctx.restore();
      }
    }
  }

  _drawRadar(ctx) {
    const { w, h } = this;
    this.requestRadar();
    const frames = this.radar.frames;
    if (!frames.length) {
      const msg = this.radar.state === "error" ? "Radar indisponible (réseau ?)" : "Chargement du radar…";
      text(ctx, msg, w / 2, h / 2, 15, SUBTLE, false, "c", "m");
      return;
    }
    const [t, bmp] = frames[Math.min(Math.round(this.rt), frames.length - 1)];
    const [minx, miny, maxx, maxy] = this.radar.bbox, n = 256 * 2 ** this.z, [cx, cy] = this.center();
    const wx0 = (minx / R + 1) / 2 * n, wx1 = (maxx / R + 1) / 2 * n, wy0 = (1 - maxy / R) / 2 * n, wy1 = (1 - miny / R) / 2 * n;
    ctx.save();
    ctx.globalAlpha = 0.88; ctx.imageSmoothingEnabled = true;
    for (const layer of [bmp, this.radar.snow.get(t)]) {
      if (layer) ctx.drawImage(layer, w / 2 + (wx0 - cx), h / 2 + (wy0 - cy), wx1 - wx0, wy1 - wy0);
    }
    ctx.restore();
    if (this.radar.state === "loading") text(ctx, `images ${this.radar.done}/${this.radar.total}…`, w - 52, 44, 11, SUBTLE, false, "r");
  }

  _drawMarker(ctx) {
    const [cx, cy] = this.center();
    const px = this.w / 2 + (worldX(this.place.longitude, this.z) - cx), py = this.h / 2 + (worldY(this.place.latitude, this.z) - cy);
    if (!(px >= -20 && px <= this.w + 20 && py >= -20 && py <= this.h + 20)) return;
    ctx.beginPath(); ctx.arc(px, py, 6, 0, 2 * Math.PI); fill(ctx, [255, 255, 255], 0.95);
    ctx.beginPath(); ctx.arc(px, py, 4, 0, 2 * Math.PI); fill(ctx, RED);
    ctx.save(); ctx.shadowColor = "rgba(0,0,0,0.8)"; ctx.shadowBlur = 3;
    text(ctx, this.place.location, px + 9, py - 8, 12, TEXT, true);
    ctx.restore();
  }

  _drawZoomButtons(ctx) {
    const S = 34, bx = this.w - S - 10;
    [["zoom_in", "+"], ["zoom_out", "−"], ["zoom_reset", ""]].forEach(([tag, label], k) => {
      const by = 40 + k * (S + 6);
      rrect(ctx, bx, by, S, S, 8); fill(ctx, PANEL, 0.82);
      ctx.lineWidth = 1; ctx.strokeStyle = rgba(SUBTLE, 0.5); rrect(ctx, bx + 0.5, by + 0.5, S - 1, S - 1, 8); ctx.stroke();
      const m = bx + S / 2, my = by + S / 2;
      if (label) text(ctx, label, m, my + 1, 20, TEXT, true, "c", "m");
      else {                                                    // "recentre" target
        ctx.strokeStyle = rgba(TEXT); ctx.lineWidth = 1.8;
        ctx.beginPath(); ctx.arc(m, my, 7, 0, 2 * Math.PI); ctx.stroke();
        for (const [dx, dy] of [[0, -11], [0, 11], [-11, 0], [11, 0]]) {
          ctx.beginPath(); ctx.moveTo(m + dx * 0.55, my + dy * 0.55); ctx.lineTo(m + dx, my + dy); ctx.stroke();
        }
      }
      this.hits.push([[bx, by, S, S], tag, null]);
    });
  }

  _drawScale(ctx, x, y) {
    const mPerPx = 40075016.686 * Math.cos(this.clat * Math.PI / 180) / (256 * 2 ** this.z);
    let km = 1000;
    for (const k of [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]) if (k * 1000 / mPerPx >= 60) { km = k; break; }
    const L = km * 1000 / mPerPx;
    ctx.strokeStyle = "rgba(255,255,255,0.9)"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(x, y + 5); ctx.lineTo(x, y + 10); ctx.lineTo(x + L, y + 10); ctx.lineTo(x + L, y + 5); ctx.stroke();
    text(ctx, `${km} km`, x + 4, y - 6, 10, TEXT, true);
  }

  _drawLegend(ctx, x, y, w) {
    if (this.layer === "radar") {
      const n = RADAR_COLORS.length, sw = w / n;
      ctx.save(); rrect(ctx, x, y, w, 8, 4); ctx.clip();
      RADAR_COLORS.forEach((c, k) => { ctx.fillStyle = rgba(c); ctx.fillRect(x + k * sw, y, sw + 0.6, 8); });
      ctx.restore();
      const ticks = w < 400 ? [0, 3, 6, 9, 13] : [0, 1, 3, 4, 6, 8, 9, 11, 13];
      for (const k of ticks) text(ctx, fg(RADAR_TICKS[k]), x + (k + 0.5) * sw, y - 14, 10, SUBTLE, false, "c");
      text(ctx, "mm/h", x + w, y + 10, 10, MUTED, false, "r");
      return;
    }
    const [stops, ticks, unit] = LEGENDS[this.layer], lo = stops[0][0], hi = stops.at(-1)[0];
    const vals = this.layer === "rt"
      ? [...Array.from({ length: 20 }, (_, k) => k / 19), ...Array.from({ length: 60 }, (_, k) => 1 + 31 * k / 59)]
      : Array.from({ length: 80 }, (_, k) => lo + (hi - lo) * k / 79);
    ctx.save(); rrect(ctx, x, y, w, 8, 4); ctx.clip();
    ctx.fillStyle = rgba(PANEL, 0.8); ctx.fillRect(x, y, w, 8);
    const sw = w / vals.length, c = new Float32Array(4);
    vals.forEach((v, k) => {
      colorize(this.layer, this.layer === "ws" ? v / 3.6 : v, 5.0, c, 0);
      ctx.fillStyle = `rgba(${c[0]},${c[1]},${c[2]},${this.layer === "nt" ? c[3] / 255 : Math.max(c[3] / 255, 0.25)})`;
      ctx.fillRect(x + k * sw, y, sw + 0.6, 8);
    });
    ctx.restore();
    const pos = this.layer === "rt" ? (v) => Math.log1p(v / 0.5) / Math.log1p(32 / 0.5) : (v) => (v - lo) / (hi - lo);
    const shown = w < 400 && this.layer === "rt" ? [0.1, 1, 4, 16, 32] : w < 400 && this.layer === "tt" ? [-30, -15, 0, 12, 20, 35] : ticks;
    for (const tk of shown) text(ctx, fg(tk), x + Math.min(Math.max(pos(tk), 0), 1) * w, y - 14, 10, SUBTLE, false, "c");
    text(ctx, unit, x + w, y + 10, 10, MUTED, false, "r");
  }

  // -- time slider -------------------------------------------------------------
  /** Slider track geometry within its own canvas: [x, width]. */
  sliderGeom(w) { return [12, w - 24]; }

  sliderSet(x, w) {
    const [sx, sw] = this.sliderGeom(w), f = Math.min(Math.max((x - sx) / sw, 0), 1);
    if (this.layer === "radar") {
      const n = this.radar ? this.radar.frames.length : 0;
      this.rt = f * Math.max(n - 1, 0);
    } else this.t = this.i0 + f * (this.i1 - this.i0);
  }

  drawSlider(ctx, w, h) {
    const [sx, sw] = this.sliderGeom(w), ty = 12;
    ctx.clearRect(0, 0, w, h);
    rrect(ctx, sx, ty - 2, sw, 12, 6); fill(ctx, SURFACE);
    let frac;
    if (this.layer === "radar") {
      const f = this.radar ? this.radar.frames : [], n = f.length;
      frac = n ? Math.min(this.rt, n - 1) / Math.max(n - 1, 1) : 0;
      rrect(ctx, sx, ty - 2, Math.max(12, frac * sw), 12, 6); fill(ctx, BLUE);
      for (let k = 0; k < n; k++) {                               // hour marks counted back from the latest frame
        const ago = (f.at(-1)[0] - f[k][0]) / 60e3;
        if (ago % 60 < 1 || ago < 1) {
          const px = sx + k / Math.max(n - 1, 1) * sw;
          ctx.fillStyle = rgba(TEXT, 0.5); ctx.fillRect(px - 0.5, ty - 6, 1, 20);
          if (ago < 1) text(ctx, "maint.", px - 3, ty + 12, 10, MUTED, false, "r");
          else text(ctx, `-${Math.round(ago / 60)} h`, px + 3, ty + 12, 10, MUTED);
        }
      }
    } else {
      frac = (this.t - this.i0) / Math.max(this.i1 - this.i0, 1);
      rrect(ctx, sx, ty - 2, Math.max(12, frac * sw), 12, 6); fill(ctx, BLUE);
      for (let k = this.i0; k <= this.i1; k++) {                  // midnight ticks
        if (this.d.local[k].hour === 0) {
          const px = sx + (k - this.i0) / Math.max(this.i1 - this.i0, 1) * sw;
          ctx.fillStyle = rgba(TEXT, 0.5); ctx.fillRect(px - 0.5, ty - 6, 1, 20);
          text(ctx, fr(this.d.local[k], "%a"), px + 3, ty + 12, 10, MUTED);
        }
      }
    }
    ctx.beginPath(); ctx.arc(sx + frac * sw, ty + 4, 9, 0, 2 * Math.PI); fill(ctx, TEXT);
  }
}

// ── charts ───────────────────────────────────────────────────────────────────
export class Charts {
  constructor() { this._key = ""; this._base = null; }

  /** Geometry for a canvas of w×h CSS px. */
  layout(w, h, v) {
    const narrow = w < 500;
    const cy = 8, ch = h - cy - 2;
    const px0 = narrow ? 34 : 48, px1 = w - (narrow ? 32 : 46), pw = px1 - px0;
    const gaps = 26, fracs = [0.24, 0.28, 0.20, 0.14, 0.14], usable = ch - 34 - 30 - gaps * 4;
    const panels = [];
    let y = cy + 34;
    for (const f of fracs) { panels.push([y, usable * f]); y += usable * f + gaps; }
    const span = Math.max(v.i1 - v.i0, 1);
    return { narrow, cy, px0, px1, pw, panels, X: (t) => px0 + (t - v.i0) / span * pw };
  }

  tFromX(x, w, h, v) {
    const { px0, pw } = this.layout(w, h, v);
    return Math.min(Math.max(v.i0 + (x - px0) / pw * (v.i1 - v.i0), v.i0), v.i1);
  }

  draw(ctx, w, h, dpr, v) {
    const key = `${w}x${h}@${dpr}/${v.d.raw.ref}/${v.i0}`;
    if (key !== this._key || !this._base) {
      this._base = newCanvas(Math.round(w * dpr), Math.round(h * dpr));
      const b = this._base.getContext("2d");
      b.scale(dpr, dpr);
      this._ys = this._drawBase(b, w, h, v);
      this._key = key;
    }
    ctx.clearRect(0, 0, w, h);
    ctx.drawImage(this._base, 0, 0, w, h);
    this._drawCursor(ctx, w, h, v);
  }

  _drawBase(ctx, w, h, v) {
    const d = v.d, { narrow, cy, px0, px1, pw, panels, X } = this.layout(w, h, v), i0 = v.i0, i1 = v.i1;
    const rng = []; for (let i = i0; i <= i1; i++) rng.push(i);
    const Ys = [];

    const frame = (k, title, vmin, vmax, fmt, ticks = 4) => {
      const [py, ph] = panels[k];
      rrect(ctx, px0, py, pw, ph, 8); fill(ctx, SURFACE);
      ctx.save(); rrect(ctx, px0, py, pw, ph, 8); ctx.clip();
      for (const i of rng) {                                  // night shading
        if (!d.isDay(i)) { ctx.fillStyle = "rgba(0,0,0,0.22)"; ctx.fillRect(X(i - 0.5), py, X(i + 0.5) - X(i - 0.5), ph); }
      }
      ctx.lineWidth = 0.6;
      for (let g = 0; g <= ticks; g++) {
        const gy = py + ph - g / ticks * ph;
        ctx.strokeStyle = rgba(MUTED, 0.25); ctx.beginPath(); ctx.moveTo(px0, gy); ctx.lineTo(px1, gy); ctx.stroke();
      }
      for (const i of rng) {                                  // midnights
        if (d.local[i].hour === 0) {
          ctx.strokeStyle = rgba(MUTED, 0.6); ctx.setLineDash([3, 3]); ctx.lineWidth = 0.8;
          ctx.beginPath(); ctx.moveTo(X(i), py); ctx.lineTo(X(i), py + ph); ctx.stroke(); ctx.setLineDash([]);
        }
      }
      ctx.restore();
      for (let g = 0; g <= ticks; g++) {
        const gy = py + ph - g / ticks * ph;
        text(ctx, fmt(vmin + (vmax - vmin) * g / ticks), px0 - 5, gy, 10, MUTED, false, "r", "m");
      }
      text(ctx, title, px0 + 2, py - 17, 11, SUBTLE, true);
      const span = vmax - vmin || 1;
      return [py, ph, (val) => py + ph - (val - vmin) / span * ph];
    };

    const line = (ys, Y, color, width = 2, dash = null, fillTo = null, alpha = 1) => {
      const pts = rng.filter((i) => ys[i] !== null).map((i) => [X(i), Y(ys[i])]);
      if (pts.length < 2) return;
      if (fillTo !== null) {
        ctx.beginPath(); ctx.moveTo(pts[0][0], fillTo);
        for (const p of pts) ctx.lineTo(p[0], p[1]);
        ctx.lineTo(pts.at(-1)[0], fillTo); ctx.closePath(); fill(ctx, color, 0.16);
      }
      ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
      for (const p of pts.slice(1)) ctx.lineTo(p[0], p[1]);
      ctx.strokeStyle = rgba(color, alpha); ctx.lineWidth = width; ctx.lineJoin = "round";
      ctx.setLineDash(dash || []); ctx.stroke(); ctx.setLineDash([]);
    };

    // 1 · temperature
    const vals = [d.tt, d.re_, d.td].flatMap((s) => rng.map((k) => s[k])).filter((x) => x !== null);
    let lo = Math.floor(Math.min(...vals) - 1), hi = Math.ceil(Math.max(...vals) + 1);
    if (hi - lo < 6) hi = lo + 6;
    let [py, ph, Y] = frame(0, narrow ? "Température · ressenti · rosée (°C)" : "Température · ressenti · point de rosée (°C)",
      lo, hi, (x) => `${fx(x)}°`);
    if (lo < 0 && 0 < hi) {
      ctx.strokeStyle = rgba(SKY, 0.6); ctx.setLineDash([5, 4]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(px0, Y(0)); ctx.lineTo(px1, Y(0)); ctx.stroke(); ctx.setLineDash([]);
    }
    line(d.td, Y, TEAL, 1.4, null, null, 0.9);
    line(d.re_, Y, PEACH, 1.6, [4, 3]);
    line(d.tt, Y, RED, 2.6, null, py + ph);
    Ys.push(["tt", Y, RED]);
    const tmax = rng.reduce((b, k) => (d.tt[k] > d.tt[b] ? k : b), rng[0]);
    const tmin = rng.reduce((b, k) => (d.tt[k] < d.tt[b] ? k : b), rng[0]);
    for (const [k, col, dy] of [[tmax, RED, -16], [tmin, SKY, 6]]) {
      text(ctx, `${fx(d.tt[k])}°`, Math.min(Math.max(X(k), px0 + 10), px1 - 10), Y(d.tt[k]) + dy, 12, col, true, "c");
    }

    // 2 · precipitation
    const pk = Math.max(...rng.map((k) => d.pr[k]), 1.0);
    const top = Math.max(4.0, Math.ceil(pk * 1.2 / 4) * 4);
    [py, ph, Y] = frame(1, narrow ? "Précip. (mm/h) · probabilité (%)" : "Précipitations (mm/h équiv. eau)  ·  probabilité (%)",
      0, top, fg);
    const bw = pw / (i1 - i0 + 1) * 0.78;
    const cols = { rain: BLUE, snow: SNOWC, frz: RED, pel: TEAL };
    for (const i of rng) {
      let comp = [["rain", d.rain[i]], ["snow", d.swe[i]], ["frz", d.frz[i]], ["pel", d.pel[i]]];
      const rest = d.pr[i] - comp.reduce((s, [, x]) => s + x, 0);
      if (rest > 0.02) {
        let k = d.kind(i); k = k === "none" || k === "mix" ? "rain" : k;
        comp = comp.map(([a, x]) => [a, x + (a === k ? rest : 0)]);
      }
      let yb = py + ph;
      for (const [kind, x] of comp) {
        if (x <= 0.005) continue;
        const hh = Math.min(x / top, 1) * ph;
        ctx.fillStyle = rgba(cols[kind], 0.92); ctx.fillRect(X(i) - bw / 2, yb - hh, bw, hh);
        yb -= hh;
      }
    }
    const pyP = py, phP = ph, popY = (x) => pyP + phP - x / 100 * phP;
    line(d.pop, popY, YELLOW, 1.6, [2, 3], null, 0.95);
    for (const g of [0, 50, 100]) text(ctx, `${g}%`, px1 + 5, popY(g), 10, YELLOW, false, "l", "m", 0.8);
    const tot = rng.reduce((s, k) => s + d.pr[k], 0), snow = rng.reduce((s, k) => s + d.snow_cm[k], 0);
    text(ctx, `Total ${fx(tot, 1)} mm` + (snow >= 0.5 ? ` · neige ~ ${fx(snow)} cm` : ""), px1, py - 17, 11, SUBTLE, true, "r");
    [["pluie", BLUE], ["neige", SNOWC], ["verglas", RED], ["grésil", TEAL]].forEach(([lab, c], j) => {
      const lx = px0 + 8 + j * (narrow ? 56 : 62);
      ctx.fillStyle = rgba(c); ctx.fillRect(lx, py + 8, 8, 8);
      text(ctx, lab, lx + 12, py + 6, 10, MUTED);
    });
    Ys.push(["pop", popY, YELLOW]);

    // 3 · wind
    const wm = Math.max(...rng.map((k) => d.gust[k] || 0), 30), wtop = Math.ceil(wm / 20) * 20;
    [py, ph, Y] = frame(2, "Vent à 10 m — rafales (km/h)", 0, wtop, (x) => fx(x));
    for (const i of rng) {
      if (d.gust[i] && d.gust[i] > d.ws[i] + 1) {
        ctx.fillStyle = rgba(MAUVE, 0.30);
        ctx.fillRect(X(i - 0.5), Y(d.gust[i]), X(i + 0.5) - X(i - 0.5), Y(d.ws[i]) - Y(d.gust[i]));
      }
    }
    line(d.gust, Y, MAUVE, 1.2, null, null, 0.9);
    line(d.ws, Y, SKY, 2.4, null, py + ph);
    const every = narrow ? 6 : 3;
    for (const i of rng) {                                    // direction arrows
      if ((i - i0) % every === 0 && d.wd[i] !== null) {
        ctx.save(); ctx.translate(X(i), py + 12); ctx.rotate((d.wd[i] + 180) * Math.PI / 180);
        ctx.strokeStyle = rgba(LAV, 0.95); ctx.lineWidth = 1.5; ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(0, 6); ctx.lineTo(0, -6); ctx.moveTo(-3, -2); ctx.lineTo(0, -6); ctx.lineTo(3, -2); ctx.stroke();
        ctx.restore();
      }
    }
    Ys.push(["ws", Y, SKY]);

    // 4 · cloud cover + humidity
    [py, ph, Y] = frame(3, "Nuages (zone) · humidité (ligne) %", 0, 100, (x) => fx(x), 2);
    line(d.nt, Y, SUBTLE, 1.4, null, py + ph, 0.8);
    line(d.hr, Y, SKY, 1.8);
    Ys.push(["nt", Y, SUBTLE]);

    // 5 · pressure + UV
    const pv = rng.map((k) => d.slp[k]).filter((x) => x !== null);
    const plo = Math.floor(Math.min(...pv) / 2) * 2 - 2, phi = Math.ceil(Math.max(...pv) / 2) * 2 + 2;
    [py, ph, Y] = frame(4, "Pression (hPa) · indice UV (barres)", plo, phi, (x) => fx(x), 2);
    const uvm = Math.max(...rng.map((k) => d.uv[k]), 4);
    for (const i of rng) {
      if (d.uv[i] > 0.05) {
        const hh = d.uv[i] / uvm * ph * 0.8;
        ctx.fillStyle = rgba(YELLOW, 0.55); ctx.fillRect(X(i) - bw / 2, py + ph - hh, bw, hh);
      }
    }
    text(ctx, `UV max ${fx(Math.max(...rng.map((k) => d.uv[k])), 1)}`, px1, py - 16, 10, YELLOW, false, "r", "t", 0.85);
    line(d.slp, Y, GREEN, 2);
    Ys.push(["slp", Y, GREEN]);

    // x axis labels
    const ay = panels.at(-1)[0] + panels.at(-1)[1] + 5;
    const step = pw / Math.max(i1 - i0, 1) * 3 >= 26 ? 3 : 6;
    for (const i of rng) {
      const hr = d.local[i].hour;
      if (hr % step === 0) text(ctx, `${String(hr).padStart(2, "0")}h`, X(i), ay, 10, MUTED, false, "c");
      if (hr === 0) text(ctx, fr(d.local[i], "%a %d"), X(i) + 3, ay + 13, 10, SUBTLE, true);
    }
    if (d.local[i0].hour !== 0 && d.local[i0].hour < 20) text(ctx, fr(d.local[i0], "%a %d"), px0 + 3, ay + 13, 10, SUBTLE, true);
    return Ys;
  }

  _drawCursor(ctx, w, h, v) {
    const d = v.d, { cy, px0, px1, panels, X } = this.layout(w, h, v);
    const ti = v.timeIndex(), xt = X(v.t);
    const vals = { tt: d.tt, pop: d.pop, ws: d.ws, nt: d.nt, slp: d.slp };
    for (const [k, Y, color] of this._ys) {                   // dots on each panel's main series
      const val = vals[k][ti];
      if (val === null) continue;
      ctx.beginPath(); ctx.arc(xt, Y(val), 5, 0, 2 * Math.PI); fill(ctx, BG);
      ctx.beginPath(); ctx.arc(xt, Y(val), 3.5, 0, 2 * Math.PI); fill(ctx, color);
    }
    ctx.strokeStyle = rgba(TEXT, 0.85); ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(xt, cy + 11); ctx.lineTo(xt, panels.at(-1)[0] + panels.at(-1)[1]); ctx.stroke();
    const lbl = fr(d.local[ti], "%a %Hh"), bwid = 62;
    rrect(ctx, Math.min(Math.max(xt - bwid / 2, px0), px1 - bwid), cy - 6, bwid, 17, 5); fill(ctx, TEXT);
    text(ctx, lbl, Math.min(Math.max(xt, px0 + bwid / 2), px1 - bwid / 2), cy + 2.5, 11, PANEL, true, "c", "m");
  }
}
