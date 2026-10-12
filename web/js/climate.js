// Climate normals 1981–2010 of the nearest Environment Canada stations (port of climate.py).
// Source: the MSC GeoMet OGC API (api.weather.gc.ca). Fetched once, then kept two months in IndexedDB.

import { get } from "./net.js";
import * as store from "./store.js";

const API = "https://api.weather.gc.ca/collections";
export const MAX_AGE = 60 * 24 * 3600e3;
export const PERIOD = "1981–2010";
export const ELEMENTS = {
  tmean: "Mean daily temperature deg C", tmax: "Mean daily max temperature deg C", tmin: "Mean daily min temperature deg C",
  precip: "Total precipitation mm", snow: "Total snowfall cm", pdays: "Days with precipitation GE 0.2 mm",
  sun: "Total hours bright sunshine", wind: "Mean of hourly wind speed kmh",
};
const BY_NAME = Object.fromEntries(Object.entries(ELEMENTS).map(([k, v]) => [v, k]));
export const MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
  "novembre", "décembre"];

async function items(url) {
  const raw = await get(url, { timeout: 60000 });
  try { return raw ? JSON.parse(raw).features : null; } catch { return null; }
}

const km = (lat, lon, c) => Math.hypot((c[0] - lon) * 111 * Math.cos(lat * Math.PI / 180), (c[1] - lat) * 111);

export async function nearestStations(lat, lon) {
  let cands = [];
  for (const half of [0.6, 1.2, 2.4]) {
    const feats = await items(`${API}/climate-stations/items?f=json&limit=300&HAS_NORMALS_DATA=Y`
      + `&bbox=${lon - half * 1.5},${lat - half},${lon + half * 1.5},${lat + half}`);
    if (feats === null) return [];
    cands = feats.filter((f) => f.geometry).map((f) => [km(lat, lon, f.geometry.coordinates), f.properties.STN_ID, f.properties.STATION_NAME])
      .sort((a, b) => a[0] - b[0]);
    if (cands.length >= 3) break;
  }
  return cands.slice(0, 6);
}

/** The normals, or null when the service cannot be reached or no station has data. */
export async function build(lat, lon) {
  const cands = await nearestStations(lat, lon);
  if (!cands.length) return null;
  const out = { lat, lon, period: PERIOD, monthly: {}, src: {}, fetched: Date.now() / 1000 };
  for (const [dist, stn, name] of cands) {
    const feats = await items(`${API}/climate-normals/items?f=json&limit=2000&STN_ID=${stn}`);
    if (!feats) continue;
    const by = {};
    for (const f of feats) {
      const p = f.properties, key = BY_NAME[p.E_NORMAL_ELEMENT_NAME];
      if (key && p.VALUE !== null) (by[key] ||= {})[p.MONTH] ??= p.VALUE;
    }
    for (const [key, months] of Object.entries(by)) {
      if (!(key in out.monthly) && Array.from({ length: 12 }, (_, m) => m + 1).every((m) => m in months)) {
        out.monthly[key] = Array.from({ length: 12 }, (_, m) => months[m + 1]);
        out.src[key] = [stn, name, Math.round(dist * 10) / 10];
      }
    }
    if (Object.keys(ELEMENTS).every((k) => k in out.monthly)) break;           // complete: no need to ask further stations
  }
  return "tmean" in out.monthly ? out : null;
}

export class Normals {
  constructor(d) { this.d = d; }
  has(key) { return key in this.d.monthly; }
  month(key, m) { return this.d.monthly[key][m - 1]; }
  /** Normal for a date {year, month, day}: the monthly values (centred on the 15th) interpolated linearly. */
  daily(key, day) {
    const vals = this.d.monthly[key], dim = new Date(Date.UTC(day.year, day.month, 0)).getUTCDate();
    const pos = day.month - 1 + (day.day - 15) / dim, i0 = Math.floor(pos), f = pos - i0;
    const at = (i) => vals[((i % 12) + 12) % 12];
    return at(i0) * (1 - f) + at(i0 + 1) * f;
  }
  station(key = "tmean") { const s = this.d.src[key]; return s ? { id: s[0], name: s[1], km: s[2] } : null; }
  get period() { return this.d.period || PERIOD; }
}

/** "Quebec / Jean Lesage Int. A" */
export function prettyStation(name) {
  if (!name) return "";
  const t = name.toLowerCase().replace(/(^|[^a-zà-ÿ])([a-zà-ÿ])/g, (_, a, b) => a + b.toUpperCase());
  return t.replace("Intl", "Int.").replace(/\//g, " / ");
}

/** Loads the normals (IndexedDB first, then the network once). `normals` is null until they are there. */
export class Loader {
  constructor(place, { network = true } = {}) {
    this.place = place; this.network = network;
    this.normals = null; this.state = "idle"; this.version = 0;
    this._start();
  }
  async _start() {
    try {
      const d = await store.get("normals");
      if (d && Math.abs(d.lat - this.place.latitude) < 0.01 && Math.abs(d.lon - this.place.longitude) < 0.01
          && Date.now() - d.fetched * 1000 < MAX_AGE) {
        this.normals = new Normals(d); this.version++;
        return;
      }
    } catch { /* no cache */ }
    if (!this.network) return;
    this.state = "loading"; this.version++;
    try {
      const d = await build(this.place.latitude, this.place.longitude);
      if (d) {
        await store.set("normals", d).catch(() => {});
        this.normals = new Normals(d); this.state = "idle";
      } else this.state = "missing";
    } catch { this.state = "missing"; }
    this.version++;
  }
}
