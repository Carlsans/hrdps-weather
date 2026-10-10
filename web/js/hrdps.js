// HRDPS (ECCC, 2.5 km) data layer — a line-by-line port of src/hrdps_weather/hrdps.py.
// Everything comes from MSC GeoMet (WMS GetFeatureInfo for the point series, WCS GetCoverage for the
// map grids). No browser-only API is used here so the logic can be tested under Node (web/tests).

import { get, pool, sleep } from "./net.js";

export const GEOMET = "https://geo.weather.gc.ca/geomet";
export const HOURS = 48;
export const CHECK_EVERY = 600;                 // s between "is there a new run?" checks
export const REGION_ZOOM = 8, WIDE_ZOOM = 6, MAP_TILES = 3;
export const WIDE_CELLS = 120;
export const WORKERS = 6;                       // browsers allow 6 connections per HTTP/1.1 host

const C = "HRDPS.CONTINENTAL_";
const W = "HRDPS-WEonG_2.5km_";
// key -> GeoMet layer. acc_* are cumulative since the start of the run.
export const POINT_LAYERS = {
  tt: C + "TT", td: C + "TD", hr: C + "HR", re: C + "RE", hmx: C + "HMX", utci: C + "UTCI",
  ws: C + "WSPD", wd: C + "WD", wgx: C + "WGX", slp: C + "PN-SLP",
  nt: C + "NT", rt: C + "RT",
  acc_pr: C + "PR", acc_rn: C + "RN", acc_sn: C + "SN", acc_fr: C + "FR", acc_pe: C + "PE",
  uv: C + "IUVA", sd: C + "SD", pbl: C + "HPBL", cape: C + "BE",
  ptype: W + "DominantPrecipType", pop: W + "Precip-Prob", snowlvl: W + "SnowLevelHeight",
  tsprob: W + "Thunderstorm-Prob", pint: W + "TotalPrecipIntensityIndex",
  pchar: W + "PrecipCharacter", fogvis: W + "LiquidFogVisibility",
};
const ACC_KEYS = Object.keys(POINT_LAYERS).filter((k) => k.startsWith("acc_"));
// these layers return no feature (transparent) when the value is nil
const EMPTY_IS_ZERO = new Set([...ACC_KEYS, "sd", "cape", "rt", "tsprob"]);

export const MAP_LAYERS = { rt: C + "RT", tt: C + "TT", ws: C + "WSPD", wd: C + "WD", nt: C + "NT" };

// ── time ─────────────────────────────────────────────────────────────────────
export const HOUR_MS = 3600e3;
export const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");   // "%Y-%m-%dT%H:%M:%SZ"
export const parseIso = (s) => Date.parse(s);

const _fmts = new Map();
const WD = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };
/** Wall-clock parts of a UTC instant in an IANA time zone; weekday 0 = Monday (like Python). */
export function localParts(ms, tz) {
  let f = _fmts.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone: tz, hourCycle: "h23", weekday: "short",
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });
    _fmts.set(tz, f);
  }
  const p = {};
  for (const { type, value } of f.formatToParts(new Date(ms))) p[type] = value;
  return { year: +p.year, month: +p.month, day: +p.day, hour: +p.hour % 24, minute: +p.minute, weekday: WD[p.weekday] };
}

const _DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
const _MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
  "septembre", "octobre", "novembre", "décembre"];
const _MON_AB = ["janv", "févr", "mars", "avr", "mai", "juin", "juil", "août", "sept", "oct", "nov", "déc"];
const pad2 = (n) => String(n).padStart(2, "0");

/** strftime with French day/month names; `p` = localParts(). Supports %A %a %B %b %d %H %M %m %Y. */
export function fr(p, pattern) {
  const d = _DAYS[p.weekday], mi = p.month - 1;
  return pattern.replace(/%([AaBbdHMmY%])/g, (_, c) => ({
    A: d, a: d.slice(0, 3), B: _MONTHS[mi], b: _MON_AB[mi], d: pad2(p.day), H: pad2(p.hour),
    M: pad2(p.minute), m: pad2(p.month), Y: String(p.year), "%": "%",
  })[c]);
}

/** Python's f"{x:.{d}f}": exact ties round half to even (JavaScript's toFixed rounds them up). */
export function fx(x, d = 0) {
  if (x === null || x === undefined || Number.isNaN(x)) return "nan";
  const neg = x < 0 || Object.is(x, -0), a = Math.abs(x), s = 10 ** d, y = a * s, f = Math.floor(y);
  // an exact tie needs a dyadic value (0.5, 0.25, 0.125…): then a * 10^d is computed exactly
  const tie = Number.isInteger(a * 2 ** (d + 1)) && y - f === 0.5;
  return (neg ? "-" : "") + (tie && f % 2 === 0 ? (f / s).toFixed(d) : a.toFixed(d));
}

/** Python's f"{x:g}" for the tick values used here. */
export const fg = (x) => String(Number(x.toPrecision(6)));

// ── geometry ─────────────────────────────────────────────────────────────────
export function tileXY(lat, lon, z = REGION_ZOOM) {
  const n = 2 ** z;
  return [Math.floor((lon + 180) / 360 * n),
    Math.floor((1 - Math.asinh(Math.tan(lat * Math.PI / 180)) / Math.PI) / 2 * n)];
}

function tileLonLat(x, y, z) {
  const n = 2 ** z;
  return [x / n * 360 - 180, Math.atan(Math.sinh(Math.PI * (1 - 2 * y / n))) * 180 / Math.PI];
}

/** [lon_w, lat_s, lon_e, lat_n] of the 3x3 tile block around the point at zoom z. */
export function mapBounds(lat, lon, z = REGION_ZOOM) {
  const [x, y] = tileXY(lat, lon, z), h = Math.floor(MAP_TILES / 2);
  const [w, n] = tileLonLat(x - h, y - h, z);
  const [e, s] = tileLonLat(x + h + 1, y + h + 1, z);
  return [w, s, e, n];
}

// ── GeoMet requests ──────────────────────────────────────────────────────────
/** -> [value, class] | "empty" | null (network error) */
export async function feature(layer, t, lat, lon, signal) {
  const d = 0.01;
  const url = `${GEOMET}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo&LAYERS=${layer}&QUERY_LAYERS=${layer}`
    + `&CRS=EPSG:4326&BBOX=${lat - d},${lon - d},${lat + d},${lon + d}&WIDTH=101&HEIGHT=101&I=50&J=50`
    + `&INFO_FORMAT=application/json&FORMAT=image/png&STYLES=&TIME=${iso(t)}`;
  const raw = await get(url, { signal });
  if (raw === null) return null;
  let feats;
  try {
    feats = JSON.parse(raw).features || [];
  } catch {
    return null;
  }
  if (!feats.length) return "empty";
  const p = feats[0].properties || {};
  return [p.value ?? null, p.class ?? null];
}

/** Reference time (UTC ms) of the newest run listed in a GetCapabilities document, or null. */
export function parseLatestRef(s) {
  const i = s.indexOf(`<Name>${C}TT</Name>`);
  const m = i >= 0 ? /name="reference_time"[^>]*>([^<]*)</.exec(s.slice(i, i + 6000)) : null;
  if (!m) return null;
  const parts = m[1].split("/");
  const last = parts.length >= 2 ? parts[1] : parts[0].split(",").at(-1);
  const t = parseIso(last.trim());
  return Number.isNaN(t) ? null : t;
}

export async function latestRef(signal) {
  const raw = await get(`${GEOMET}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities&LAYER=${C}TT`, { timeout: 40000, signal });
  return raw ? parseLatestRef(raw) : null;
}

/** One run's point series, in the same JSON shape as the desktop cache (series.json). */
export async function fetchSeries(ref, { latitude: lat, longitude: lon }, onProgress = () => {}, signal) {
  const times = Array.from({ length: HOURS + 1 }, (_, i) => ref + i * HOUR_MS);
  const keys = Object.keys(POINT_LAYERS);
  const jobs = [];
  for (let i = 0; i < times.length; i++) for (const k of keys) jobs.push([k, i]);   // hour by hour
  let done = 0;
  const one = async ([k, i]) => {
    const r = await feature(POINT_LAYERS[k], times[i], lat, lon, signal);
    onProgress(++done, jobs.length);
    return r;
  };
  const res = await pool(jobs, WORKERS, one);
  // Failed requests come back as null: retry those before giving up on them (Data interpolates the rest).
  for (let pass = 0; pass < 2; pass++) {
    const todo = res.flatMap((r, n) => (r === null ? [n] : []));
    if (!todo.length || signal?.aborted) break;
    await sleep(2000);
    const again = await pool(todo, WORKERS, (n) => feature(POINT_LAYERS[jobs[n][0]], times[jobs[n][1]], lat, lon, signal));
    todo.forEach((n, j) => { res[n] = again[j]; });
  }
  const series = {}, labels = {};
  for (const k of keys) { series[k] = new Array(times.length).fill(null); labels[k] = new Array(times.length).fill(null); }
  jobs.forEach(([k, i], n) => {
    const r = res[n];
    if (r === "empty") series[k][i] = EMPTY_IS_ZERO.has(k) ? 0.0 : null;
    else if (r) [series[k][i], labels[k][i]] = r;
  });
  return { ref: iso(ref), times: times.map(iso), series, labels };
}

/** ESRI ASCII grid (WCS image/x-aaigrid) -> {ny, nx, data: Float32Array} (rows north→south), or null. */
export function parseAAIGrid(text) {
  const start = text.indexOf("ncols");
  if (start < 0) return null;
  const lines = text.slice(start).split("\n");
  const hdr = {};
  let k = 0;
  while (k < lines.length && /^[A-Za-z]/.test(lines[k])) {
    const a = lines[k].trim().split(/\s+/);
    hdr[a[0].toLowerCase()] = parseFloat(a[1]);
    k++;
  }
  const rows = [];
  for (; k < lines.length; k++) {
    const ln = lines[k];
    if (ln.startsWith("--") || !ln.trim()) break;
    rows.push(ln.trim().split(/\s+/).map(Number));
  }
  if (!rows.length) return null;
  const ny = rows.length, nx = rows[0].length;
  if (rows.some((r) => r.length !== nx)) return null;
  const data = new Float32Array(ny * nx);
  const nd = hdr.nodata_value;
  rows.forEach((r, y) => r.forEach((v, x) => {
    data[y * nx + x] = (nd !== undefined && v === nd) || v < -1e20 ? NaN : v;
  }));
  return { ny, nx, data };
}

async function fetchGrid(layer, t, [w, s, e, n], cells, signal) {
  const scale = cells ? `&SCALESIZE=x(${cells}),y(${Math.round(cells * (n - s) / (e - w))})` : "";
  const url = `${GEOMET}?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=${layer}&FORMAT=image/x-aaigrid`
    + `&SUBSETTINGCRS=EPSG:4326&SUBSET=x(${w},${e})&SUBSET=y(${s},${n})${scale}&TIME=${iso(t)}`;
  const raw = await get(url, { timeout: 60000, signal });
  return raw && raw.includes("ncols") ? parseAAIGrid(raw) : null;
}

/** Map grids: {ref, region: {bounds, grids}, wide: {bounds, grids} | null} where grids[layer] =
 *  {T, ny, nx, data: Float32Array(T*ny*nx)}; null if the region grids failed entirely. */
export async function fetchMaps(ref, { latitude: lat, longitude: lon }, onProgress = () => {}, signal) {
  const times = Array.from({ length: HOURS + 1 }, (_, i) => ref + i * HOUR_MS);
  const sets = { region: [mapBounds(lat, lon, REGION_ZOOM), null], wide: [mapBounds(lat, lon, WIDE_ZOOM), WIDE_CELLS] };
  const jobs = [];
  for (const p in sets) for (let i = 0; i < times.length; i++) for (const k in MAP_LAYERS) jobs.push([p, k, i]);
  let done = 0;
  const res = await pool(jobs, WORKERS, async ([p, k, i]) => {
    const g = await fetchGrid(MAP_LAYERS[k], times[i], sets[p][0], sets[p][1], signal);
    onProgress(++done, jobs.length);
    return g;
  });
  const out = { ref: iso(ref), region: null, wide: null };
  for (const p in sets) {
    const first = jobs.findIndex(([pp], n) => pp === p && res[n]);
    if (first < 0) {
      if (p === "region") return null;
      continue;
    }
    const { ny, nx } = res[first], T = times.length, grids = {};
    for (const k in MAP_LAYERS) grids[k] = { T, ny, nx, data: new Float32Array(T * ny * nx).fill(NaN) };
    jobs.forEach(([pp, k, i], n) => {
      const r = res[n];
      if (pp === p && r && r.ny === ny && r.nx === nx) grids[k].data.set(r.data, i * ny * nx);
    });
    out[p] = { bounds: sets[p][0], grids };
  }
  return out;
}

// ── next model run ───────────────────────────────────────────────────────────
export const RUN_STEP = 6 * HOUR_MS;               // HRDPS runs at 00, 06, 12 and 18 UTC
export const DEFAULT_LAG = 3 * 3600 + 10 * 60;     // a run is complete on GeoMet ≈ 3 h after its cycle time
export const MIN_LAG = 2.5 * 3600, MAX_LAG = 4.5 * 3600;

/** Typical availability delay (s): median of the lags observed on this device, else the default. */
export function runLag(lags) {
  const s = [...(lags || [])].sort((a, b) => a - b);
  return s.length ? s[Math.floor(s.length / 2)] : DEFAULT_LAG;
}

export function nextRun(ref, now = Date.now(), lag = DEFAULT_LAG) {
  const nxt = ref + RUN_STEP, eta = nxt + lag * 1000;
  return [nxt, eta, (eta - now) / 1000];
}

/** One line for the UI, e.g. 'Prochain run 12Z : dans ~1 h 25 (vers 11h10)'. */
export function nextRunText(ref, tz, now = Date.now(), lag = DEFAULT_LAG) {
  const [nxt, eta, left] = nextRun(ref, now, lag);
  const cycle = pad2(new Date(nxt).getUTCHours()) + "Z";
  if (left <= 0) return `Run ${cycle} attendu : publication en cours…`;
  const mins = Math.floor(left / 60) + 1;
  const span = mins >= 60 ? `${Math.floor(mins / 60)} h ${pad2(mins % 60)}` : `${mins} min`;
  return `Prochain run ${cycle} : dans ~${span} (vers ${fr(localParts(eta, tz), "%Hh%M")})`;
}

// ── data access ──────────────────────────────────────────────────────────────
// English class labels from GeoMet → French
const PTYPE_FR = { None: "Aucune", Rain: "Pluie", Snow: "Neige", "Freezing rain": "Pluie verglaçante",
  "Ice pellets": "Grésil", Drizzle: "Bruine", "Freezing drizzle": "Bruine verglaçante",
  "Wet snow": "Neige mouillée", "Rain/snow": "Pluie et neige", "Snow grains": "Neige en grains" };
const PINT_FR = { "No Intensity": "", "Low Intensity": "Faible", "Light Intensity": "Faible", "Moderate Intensity": "Modérée",
  "Heavy Intensity": "Forte", "Very Heavy Intensity": "Très forte", "No precipitation": "Aucune" };
const PCHAR_FR = { Showers: "Averses", Continuous: "Continue", Intermittent: "Intermittente",
  Flurries: "Giboulées de neige", Squalls: "Bourrasques", None: "" };
const UTCI_FR = { "Extreme cold stress": "Froid extrême", "Very strong cold stress": "Froid très fort",
  "Strong cold stress": "Froid fort", "Moderate cold stress": "Froid modéré",
  "Slight cold stress": "Froid léger", "No thermal stress": "Confortable",
  "Moderate heat stress": "Chaleur modérée", "Strong heat stress": "Chaleur forte",
  "Very strong heat stress": "Chaleur très forte", "Extreme heat stress": "Chaleur extrême" };
const tr = (map, x) => (Object.prototype.hasOwnProperty.call(map, x) ? map[x] : x);

/** Approximate solar elevation (degrees) at lat/lon (NOAA low-precision formula). */
export function sunElevation(ms, lat, lon) {
  const t = new Date(ms);
  const yday = Math.floor((Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate()) - Date.UTC(t.getUTCFullYear(), 0, 1)) / 864e5) + 1;
  const d = yday + (t.getUTCHours() + t.getUTCMinutes() / 60) / 24;
  const g = 2 * Math.PI / 365 * (d - 1);
  const decl = 0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g) - 0.006758 * Math.cos(2 * g)
    + 0.000907 * Math.sin(2 * g);
  const eqt = 229.18 * (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g)
    - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));
  const ha = ((t.getUTCHours() * 60 + t.getUTCMinutes() + eqt + 4 * lon) / 4 - 180) * Math.PI / 180;
  const la = lat * Math.PI / 180;
  return Math.asin(Math.sin(la) * Math.sin(decl) + Math.cos(la) * Math.cos(decl) * Math.cos(ha)) * 180 / Math.PI;
}

/** Fill gaps of a continuous series: linear interpolation inside, nearest value at the edges. */
export function fill(vals) {
  const known = vals.flatMap((v, i) => (v !== null ? [i] : []));
  if (!known.length || known.length === vals.length) return vals;
  return vals.map((v, i) => {
    if (v !== null) return v;
    const lo = known.filter((k) => k < i).at(-1), hi = known.find((k) => k > i);
    if (lo === undefined) return vals[hi];
    if (hi === undefined) return vals[lo];
    return vals[lo] + (vals[hi] - vals[lo]) * (i - lo) / (hi - lo);
  });
}

const argmax = (r, key) => r.reduce((b, i) => (key(i) > key(b) ? i : b), r[0]);   // first of ties, like max()
const argmin = (r, key) => r.reduce((b, i) => (key(i) < key(b) ? i : b), r[0]);

/** Hourly series at the configured point; index 0 = run start (UTC). */
export class Data {
  constructor(raw, { latitude, longitude, timezone }) {
    this.raw = raw;
    this.lat = latitude; this.lon = longitude; this.tz = timezone;
    this.ref = parseIso(raw.ref);
    this.utc = raw.times.map(parseIso);
    this.local = this.utc.map((t) => localParts(t, timezone));
    this.n = this.utc.length;
    const s = raw.series, lab = raw.labels;
    this.lab = lab;
    const f = (k, d = null) => s[k].map((v) => (v !== null ? v : d));
    const kmh = (k) => s[k].map((v) => (v !== null ? v * 3.6 : null));
    const c = (k) => fill(f(k));
    this.tt = c("tt"); this.td = c("td"); this.hr = c("hr"); this.re_ = c("re");
    this.hmx = f("hmx"); this.utci = f("utci");
    this.ws = fill(kmh("ws")); this.wd = c("wd");
    // GeoMet leaves the gust layer empty when gusts do not exceed the mean wind
    this.gust = kmh("wgx").map((g, i) => { const w = this.ws[i]; return g !== null && w !== null ? Math.max(g, w) : w; });
    this.slp = fill(s.slp.map((v) => (v !== null ? v / 100 : null)));          // hPa
    this.nt = c("nt"); this.uv = f("uv", 0); this.pbl = c("pbl"); this.cape = f("cape", 0);
    this.sd = s.sd.map((v) => (v !== null ? v * 100 : 0));                     // cm
    this.pop = f("pop", 0); this.snowlvl = f("snowlvl"); this.tsprob = f("tsprob", 0);
    this.fogvis = f("fogvis");
    this.rate = s.rt.map((v) => (v !== null ? v * 3600 : 0));                  // mm/h instantaneous
    const diff = (k) => {                                                      // hourly amount from cumulative
      const a = s[k].map((v) => (v !== null ? v : 0.0));
      return [0.0, ...a.slice(1).map((v, j) => Math.max(v - a[j], 0.0))];
    };
    this.pr = diff("acc_pr"); this.rain = diff("acc_rn"); this.swe = diff("acc_sn");
    this.frz = diff("acc_fr"); this.pel = diff("acc_pe");
    this.snow_cm = [...this.swe];                // ≈10:1 snow-to-liquid ratio → 1 mm SWE ≈ 1 cm
    this.ptype = lab.ptype.map((x) => (x ? tr(PTYPE_FR, x) : "Aucune"));
    this.pint = lab.pint.map((x) => (x ? tr(PINT_FR, x) : ""));
    this.pchar = lab.pchar.map((x) => (x ? tr(PCHAR_FR, x) : ""));
    this.utci_l = lab.utci.map((x) => (x ? tr(UTCI_FR, x) : ""));
    this._day = new Map();
  }

  nowIndex(now = Date.now()) {
    const hour = Math.floor(now / HOUR_MS) * HOUR_MS;
    return Math.min(Math.max(Math.floor((hour - this.ref) / HOUR_MS), 0), this.n - 1);
  }

  upto(i0, hours) {
    const out = [];
    for (let i = i0; i < Math.min(i0 + hours, this.n); i++) out.push(i);
    return out;
  }

  isDay(i) {
    if (!this._day.has(i)) this._day.set(i, sunElevation(this.utc[i] + 30 * 60e3, this.lat, this.lon) > -1.0);
    return this._day.get(i);
  }

  /** Dominant precipitation of hour i: rain | snow | frz | pel | mix | none. */
  kind(i) {
    if (this.pr[i] < 0.05 && this.rate[i] < 0.05) return "none";
    const amt = { rain: this.rain[i], snow: this.swe[i], frz: this.frz[i], pel: this.pel[i] };
    const tot = amt.rain + amt.snow + amt.frz + amt.pel;
    if (tot < 1e-3) return { Neige: "snow", "Grésil": "pel", "Pluie verglaçante": "frz" }[this.ptype[i]] || "rain";
    if (amt.frz > 0.2 * tot) return "frz";
    const top = Object.keys(amt).reduce((b, k) => (amt[k] > amt[b] ? k : b), "rain");
    if ((top === "snow" || top === "rain") && amt[top === "rain" ? "snow" : "rain"] > 0.3 * tot) return "mix";
    return top;
  }

  icon(i) {
    const k = this.kind(i), day = this.isDay(i);
    if (k !== "none" && this.tsprob[i] >= 40) return "⛈️";
    if (k === "snow") return this.pr[i] < 1.5 ? "🌨️" : "❄️";
    if (k === "frz" || k === "pel") return "🧊";
    if (k === "mix") return "🌨️";
    if (k === "rain") return this.pr[i] < 0.5 && (this.nt[i] || 100) < 80 ? "🌦️" : "🌧️";
    if (this.fogvis[i] !== null && this.fogvis[i] < 1000) return "🌫️";
    const nt = this.nt[i] !== null ? this.nt[i] : 50;
    if (nt < 15) return day ? "☀️" : "🌙";
    if (nt < 40) return day ? "🌤️" : "🌙";
    if (nt < 75) return day ? "⛅" : "☁️";
    return "☁️";
  }

  sky(i) {
    const nt = this.nt[i] !== null ? this.nt[i] : 50;
    return nt < 15 ? "Dégagé" : nt < 40 ? "Peu nuageux" : nt < 75 ? "Partiellement nuageux" : "Couvert";
  }

  describe(i) {
    const k = this.kind(i);
    if (k === "none") return this.sky(i);
    const name = { rain: "Pluie", snow: "Neige", frz: "Pluie verglaçante", pel: "Grésil", mix: "Pluie et neige" }[k];
    return this.pint[i] ? `${name} (${this.pint[i].toLowerCase()})` : name;
  }

  /** [[text, hour_index]] for noteworthy conditions in the next `hours` hours. */
  alerts(i0, hours = 36) {
    const r = this.upto(i0, hours);
    const when = (i) => fr(this.local[i], "%a %Hh");
    const out = [];
    for (const i of r) if (this.kind(i) === "frz") { out.push([`Pluie verglaçante · ${when(i)}`, i]); break; }
    for (const i of r) if (this.kind(i) === "pel") { out.push([`Grésil · ${when(i)}`, i]); break; }
    const g = argmax(r, (i) => this.gust[i] || 0);
    if ((this.gust[g] || 0) >= 60) out.push([`Rafales ${fx(this.gust[g])} km/h · ${when(g)}`, g]);
    const p = argmax(r, (i) => this.rain[i]);
    if (this.rain[p] >= 5) out.push([`Forte pluie ${fx(this.rain[p], 1)} mm/h · ${when(p)}`, p]);
    const sn = argmax(r, (i) => this.snow_cm[i]);
    if (this.snow_cm[sn] >= 2) out.push([`Neige ~${fx(this.snow_cm[sn], 1)} cm/h · ${when(sn)}`, sn]);
    const t = argmin(r, (i) => (this.tt[i] !== null ? this.tt[i] : 99));
    if (this.tt[t] !== null && this.tt[t] <= 0) out.push([`Gel ${fx(this.tt[t])}° · ${when(t)}`, t]);
    const o = argmax(r, (i) => this.tsprob[i]);
    if (this.tsprob[o] >= 30) out.push([`Orage ${fx(this.tsprob[o])}% · ${when(o)}`, o]);
    return out;
  }
}
