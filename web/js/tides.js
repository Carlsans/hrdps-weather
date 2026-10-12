// Tides from the Canadian Hydrographic Service, Fisheries and Oceans Canada (port of tides.py).
// IWLS web service: https://api-iwls.dfo-mpo.gc.ca  (Open Government Licence – Canada). Heights in metres.

import { fx } from "./hrdps.js";
import { get } from "./net.js";
import * as store from "./store.js";

const API = "https://api-iwls.dfo-mpo.gc.ca/api/v1";
export const MAX_KM = 120;
const OPERATING_BONUS_KM = 15;
const PAST = 24 * 3600e3, FUTURE = 72 * 3600e3;
export const REFRESH_AFTER = 20 * 60;                           // seconds
const STATIONS_MAX_AGE = 30 * 24 * 3600e3;
export const FEET_PER_M = 3.280839895;

const isoZ = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
const km = (lat, lon, s) => Math.hypot((s.lon - lon) * 111 * Math.cos(lat * Math.PI / 180), (s.lat - lat) * 111);

async function json(url, timeout = 60000) {
  const raw = await get(url, { timeout });
  try { return raw ? JSON.parse(raw) : null; } catch { return null; }
}

/** Compact list of every station with tide predictions (cached a month). */
export async function stations() {
  try {
    const d = await store.get("tide_stations");
    if (d && Date.now() - d.fetched < STATIONS_MAX_AGE) return d.stations;
  } catch { /* no cache */ }
  const raw = await json(`${API}/stations`, 90000);
  if (raw === null) return null;                               // unreachable (an empty list is a valid answer)
  const out = [];
  for (const s of raw) {
    const codes = (s.timeSeries || []).map((t) => t.code);
    if (codes.includes("wlp") && codes.includes("wlp-hilo")) {
      out.push({ code: s.code, id: s.id, name: s.officialName, type: s.type || "", operating: !!s.operating,
        lat: s.latitude, lon: s.longitude, series: codes });
    }
  }
  await store.set("tide_stations", { fetched: Date.now(), stations: out }).catch(() => {});
  return out;
}

/** The station to use: the configured one, else the nearest (preferring an operating one with observations). */
export function chooseStation(lat, lon, all, override = null) {
  if (override) {
    const s = all.find((x) => x.code === String(override));
    return s ? { ...s, km: Math.round(km(lat, lon, s) * 10) / 10 } : null;
  }
  let near = all.map((s) => [km(lat, lon, s), s]).sort((a, b) => a[0] - b[0]).filter(([d]) => d <= MAX_KM);
  if (!near.length) return null;
  let [bestKm, best] = near[0];
  if (!(best.operating && best.series.includes("wlo"))) {
    for (const [d, s] of near) {
      if (s.operating && s.series.includes("wlo") && d <= bestKm + OPERATING_BONUS_KM) { bestKm = d; best = s; break; }
    }
  }
  return { ...best, km: Math.round(bestKm * 10) / 10 };
}

async function series(st, code, start, end, resolution) {
  if (!st.series.includes(code)) return null;
  let url = `${API}/stations/${st.id}/data?time-series-code=${code}&from=${isoZ(start)}&to=${isoZ(end)}`;
  if (resolution) url += `&resolution=${resolution}`;
  const rows = await json(url);
  if (!Array.isArray(rows)) return null;
  const pts = [];
  for (const r of rows) {
    const t = Date.parse(r.eventDate) / 1000, v = Number(r.value);
    if (Number.isFinite(t) && Number.isFinite(v)) pts.push([t, v]);
  }
  return pts.sort((a, b) => a[0] - b[0]);
}

export class TideData {
  constructor(d) {
    this.station = d.station; this.fetched = d.fetched || 0;
    this.wlp = d.wlp || []; this.wlo = d.wlo || []; this.wlf = d.wlf || []; this.wt = d.wt || [];
    this.events = TideData.label(d.hilo || []);
  }
  /** [[epoch, height, 'H'|'L']]: each extreme compared with its neighbours. */
  static label(hilo) {
    return hilo.map(([t, v], i) => {
      const nb = [i - 1, i + 1].filter((j) => j >= 0 && j < hilo.length).map((j) => hilo[j][1]);
      return [t, v, nb.length && v > nb.reduce((a, b) => a + b, 0) / nb.length ? "H" : "L"];
    });
  }
  /** Linear interpolation (null outside the series, or across a gap of more than 3 hours). */
  static at(s, t) {
    if (s.length < 2 || t < s[0][0] || t > s.at(-1)[0]) return null;
    let lo = 0, hi = s.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (s[mid][0] <= t) lo = mid; else hi = mid; }
    const [t0, v0] = s[lo], [t1, v1] = s[hi];
    if (t1 - t0 > 3 * 3600) return null;
    return t1 === t0 ? v0 : v0 + (v1 - v0) * (t - t0) / (t1 - t0);
  }
  predicted(t) { return TideData.at(this.wlp, t); }
  /** Predicted rate of change in m/h around t. */
  trend(t) {
    const a = this.predicted(t - 900), b = this.predicted(t + 900);
    return a === null || b === null ? null : (b - a) * 2;
  }
  latestObserved(t, maxAge = 45 * 60) {
    const last = this.wlo.at(-1);
    return last && t - last[0] >= 0 && t - last[0] <= maxAge ? last : null;
  }
  nextEvents(t, n = 6) { return this.events.filter((e) => e[0] >= t).slice(0, n); }
  waterTemperature(t, maxAge = 3 * 3600) {
    const last = this.wt.at(-1);
    return last && t - last[0] >= 0 && t - last[0] <= maxAge ? last[1] : null;
  }
  toJSON() {
    return { station: this.station, fetched: this.fetched, wlp: this.wlp, wlo: this.wlo, wlf: this.wlf, wt: this.wt,
      hilo: this.events.map(([t, v]) => [t, v]) };
  }
}

export function trendText(rate) {
  if (rate === null || rate === undefined) return "";
  if (Math.abs(rate) < 0.05) return "étale";
  return `${rate > 0 ? "monte" : "baisse"} ${fx(Math.abs(rate), 2)} m/h`.replace(".", ",");
}

/** Loads (and keeps fresh) the tide data of the nearest station. Call ensure() as often as you like. */
export class Loader {
  constructor(place, { network = true } = {}) {
    this.place = place; this.network = network;
    this.data = null; this.state = "idle"; this.version = 0;
    this._busy = false; this._failedAt = 0; this._cacheRead = false;
    this._readCache();
  }
  async _readCache() {
    try {
      const d = await store.get("tides");
      const st = d && d.station;
      if (st && Math.abs((st.qlat || 0) - this.place.latitude) < 0.01 && Math.abs((st.qlon || 0) - this.place.longitude) < 0.01
          && String(st.code) === String(this.place.tide_station || st.code) && Date.now() / 1000 - d.fetched < 12 * 3600) {
        this.data = new TideData(d); this.version++;
      }
    } catch { /* no cache */ }
    this._cacheRead = true; this.version++;
  }
  ensure() {
    if (!this.network || this._busy || !this._cacheRead) return;
    if (this.state === "none" || (this.state === "error" && Date.now() - this._failedAt < 120e3)) return;
    if (this.data && Date.now() / 1000 - this.data.fetched < REFRESH_AFTER) return;
    this.state = "loading"; this._busy = true;
    this._run().finally(() => { this._busy = false; this.version++; });
  }
  async _run() {
    const { latitude: lat, longitude: lon } = this.place;
    try {
      const all = await stations();
      if (all === null) throw new Error("no station list");
      const st = chooseStation(lat, lon, all, this.place.tide_station || null);
      if (!st) { this.state = "none"; return; }
      st.qlat = lat; st.qlon = lon;
      const now = Math.floor(Date.now() / 60e3) * 60e3;
      const jobs = { wlp: ["wlp", now - PAST, now + FUTURE, "FIFTEEN_MINUTES"], hilo: ["wlp-hilo", now - PAST, now + FUTURE, null],
        wlo: ["wlo", now - PAST, now, "FIVE_MINUTES"], wlf: ["wlf-spine", now - PAST, now + FUTURE, "FIFTEEN_MINUTES"],
        wt: ["wt1", now - 6 * 3600e3, now, "FIFTEEN_MINUTES"] };
      const got = this.data && this.data.station.code === st.code ? { ...this.data.toJSON() } : {};
      Object.assign(got, { station: st, fetched: Date.now() / 1000 });
      await Promise.all(Object.entries(jobs).map(async ([key, args]) => {
        const pts = await series(st, ...args);
        if (pts !== null) {
          got[key] = pts;
          if (got.wlp && got.hilo) { this.data = new TideData(got); this.version++; }    // usable once the predictions are in
        }
      }));
      if (!(got.wlp && got.wlp.length)) throw new Error("no predictions");
      this.data = new TideData(got);
      await store.set("tides", this.data.toJSON()).catch(() => {});
      this.state = "idle";
    } catch {
      this.state = "error"; this._failedAt = Date.now();
    }
  }
}
