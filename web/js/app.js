// The page: configuration, cache, background refresh, rendering loop and input (port of ui_tk.py + tray.py).

import * as astro from "./astro.js";
import * as climate from "./climate.js";
import * as config from "./config.js";
import * as H from "./hrdps.js";
import * as store from "./store.js";
import * as tides from "./tides.js";
import { TileCache } from "./tiles.js";
import { VERSION } from "./version.js";
import { Charts, LAYERS, MapView, compass } from "./view.js";

const $ = (id) => document.getElementById(id);
const FRAME_MS = 33;                     // ~30 fps while animating; idle frames are skipped

const app = {
  cfg: config.load(),
  data: null,                            // H.Data
  maps: null,
  state: {},                             // {place, ref, maps_ref, checked, lags}
  view: null,
  charts: new Charts(),
  tiles: new TileCache(),
  normals: null,                         // climate.Loader (normals of the nearest station)
  tides: null,                           // tides.Loader (the "Marées" tab)
  busy: false,
  dirty: true,
  lastDetails: "",
};

// ── persistence ──────────────────────────────────────────────────────────────
const placeKey = (c) => `${c.latitude.toFixed(4)},${c.longitude.toFixed(4)}`;

async function loadCache() {
  app.state = (await store.get("state")) || {};
  if (app.state.place !== placeKey(app.cfg)) {           // another place: nothing cached applies
    app.state = { place: placeKey(app.cfg), lags: app.state.lags || [] };
    await Promise.all([store.del("series"), store.del("maps")]).catch(() => {});
    return;
  }
  const raw = await store.get("series");
  if (raw) setData(raw);
  const maps = await store.get("maps");
  if (maps && app.data && maps.ref === app.data.raw.ref) setMaps(maps);
}

const saveState = (kw) => { Object.assign(app.state, kw); return store.set("state", app.state).catch(() => {}); };

// ── data → view ──────────────────────────────────────────────────────────────
function setData(raw) {
  app.data = new H.Data(raw, app.cfg);
  if (!app.view) {
    app.view = new MapView(app.data, app.cfg, app.tiles);
    const c = navigator.connection;
    app.view.prefetchRadar = !(c && (c.saveData || /2g/.test(c.effectiveType || "")));
    if (app.maps) app.view.maps = app.maps;
    app.view.tides = app.tides;
  } else app.view.setData(app.data);
  document.body.classList.remove("nodata");
  $("empty").hidden = true;
  resize();
  updateHeader();
  app.dirty = true;
}

function setMaps(maps) {
  app.maps = maps;
  if (app.view) { app.view.maps = maps; app.view.status = ""; }
  app.dirty = true;
}

// ── refresh (port of hrdps.refresh) ──────────────────────────────────────────
function status(textOrNull, frac = null) {
  $("status").hidden = textOrNull === null;
  if (textOrNull === null) return;
  $("status-text").textContent = textOrNull;
  const bar = $("status-bar");
  bar.hidden = frac === null;
  if (frac !== null) bar.value = frac;
}

async function refresh(force = false) {
  if (app.busy || !app.cfg) return;
  lastAttempt = Date.now();
  app.busy = true;
  const cfg = app.cfg;
  try {
    status("Recherche du dernier run HRDPS…");
    const ref = await H.latestRef();
    if (cfg !== app.cfg) return;
    if (ref === null) {
      status(app.data ? "Hors ligne : affichage des dernières données." : "GeoMet est injoignable (réseau ?). Nouvel essai dans quelques minutes.");
      return;
    }
    const refS = H.iso(ref), st = app.state;
    const haveSeries = st.ref === refS && !!app.data;
    const haveMaps = st.maps_ref === refS && !!app.maps;
    if (!force && haveSeries && haveMaps) { await saveState({ checked: Date.now() }); status(null); return; }
    if (force || !haveSeries) {
      const first = !app.data;
      const raw = await H.fetchSeries(ref, cfg, (n, tot) => {
        status(first ? `Téléchargement des données HRDPS… ${Math.round(n / tot * 100)} %` : `Nouveau run ${refS.slice(11, 13)}Z : ${Math.round(n / tot * 100)} %`, n / tot);
        if (first) $("empty-text").textContent = `Téléchargement des données HRDPS… ${Math.round(n / tot * 100)} % (~1 à 2 minutes la première fois)`;
      });
      if (cfg !== app.cfg) return;
      if (raw.series.tt.filter((v) => v !== null).length < 10) {     // run still being published: retry later
        status(app.data ? null : "Le run HRDPS est en cours de publication. Nouvel essai dans quelques minutes.");
        return;
      }
      const lags = [...(st.lags || [])];
      if (st.ref && st.ref !== raw.ref) {                             // a *new* run was just picked up
        const lag = (Date.now() - ref) / 1000;
        if (H.MIN_LAG <= lag && lag <= H.MAX_LAG) lags.push(lag);
      }
      await store.set("series", raw).catch(() => {});
      await saveState({ ref: raw.ref, checked: Date.now(), lags: lags.slice(-8) });
      setData(raw);
    }
    if (app.view && !app.maps) app.view.status = "Cartes en téléchargement…";
    const maps = await H.fetchMaps(ref, cfg, (n, tot) => status(`Cartes de la prévision : ${Math.round(n / tot * 100)} %`, n / tot));
    if (cfg !== app.cfg) return;
    if (maps) {
      await store.set("maps", maps).catch(() => {});
      await saveState({ maps_ref: maps.ref });
      setMaps(maps);
    } else if (app.view && !app.maps) app.view.status = "Cartes indisponibles (réseau ?)";
    status(null);
  } catch (e) {
    console.error(e);
    status("Erreur pendant la mise à jour : " + e.message);
  } finally {
    app.busy = false;
    app.dirty = true;
  }
}

// Same pace as the desktop: look for a new run every CHECK_EVERY; after a failed attempt wait RETRY_AFTER.
const RETRY_AFTER = 120e3;
let lastAttempt = 0;
function maybeRefresh() {
  if (document.hidden || !app.cfg || Date.now() - lastAttempt < RETRY_AFTER) return;
  const stale = Date.now() - (app.state.checked || 0) > H.CHECK_EVERY * 1000;
  if (!app.data || stale) { lastAttempt = Date.now(); refresh(); }
}

// ── header and details (HTML) ────────────────────────────────────────────────
function updateHeader() {
  const d = app.data;
  $("place-name").textContent = app.cfg ? app.cfg.location : "…";
  if (!d) return;
  const i = app.view ? app.view.i0 : d.nowIndex();
  $("icon").textContent = d.icon(i);
  $("temp").textContent = `${H.fx(d.tt[i])}°`;
  $("describe").textContent = d.describe(i);
  const sub = [d.re_[i] !== null ? `Ressenti ${H.fx(d.re_[i])}°` : "", d.utci_l[i],
    `Vent ${H.fx(d.ws[i])} km/h ${compass(d.wd[i])}`,
    d.gust[i] && d.gust[i] - d.ws[i] > 5 ? `rafales ${H.fx(d.gust[i])}` : "", `HR ${H.fx(d.hr[i])}%`];
  $("sub").textContent = sub.filter(Boolean).join("  ·  ");
  const al = d.alerts(i), el = $("alerts");
  el.classList.toggle("on", al.length > 0);
  el.textContent = al.length ? al.slice(0, 4).map(([a]) => "⚠ " + a).join("   ") : "Aucune alerte dans les 36 prochaines heures";
  $("run").textContent = `HRDPS 2,5 km · run ${String(new Date(d.ref).getUTCHours()).padStart(2, "0")}Z (${H.fr(H.localParts(d.ref, d.tz), "%a %d %b %Hh")})`;
  updateNextRun();
  updateAlmanac();
  document.title = `${H.fx(d.tt[i])}° · ${app.cfg.location} — Météo HRDPS`;
}

function updateNextRun() {
  if (app.data) $("next").textContent = H.nextRunText(app.data.ref, app.data.tz, Date.now(), H.runLag(app.state.lags));
}

// ── sun, normals and the official page (port of View.draw_almanac) ───────────
let almanacKey = "";
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
/** A paragraph from [text, kind] pieces: kind "b" bold, "warm"/"cold" coloured, "" plain. */
function para(parts, cls = "") {
  const p = el("p", cls);
  parts.forEach(([t, k], n) => {
    const s = el(k === "b" ? "b" : "span", k && k !== "b" ? k : "", t);
    p.append(s);
    if (n < parts.length - 1) p.append(" ");
  });
  return p;
}
function updateAlmanac() {
  const d = app.data, box = $("almanac");
  if (!d || !app.cfg) { box.replaceChildren(); almanacKey = ""; return; }
  const nm = app.normals ? app.normals.normals : null, nowI = d.nowIndex();
  const key = `${d.raw.ref}/${nowI}/${nm ? "n" : app.normals ? app.normals.state : "-"}/${app.cfg.latitude},${app.cfg.longitude}`;
  if (key === almanacKey) return;
  almanacKey = key;
  const tz = d.tz, today = d.local[nowI];
  const prev = new Date(Date.UTC(today.year, today.month - 1, today.day - 1));
  const sun = astro.sunTimes(today.year, today.month, today.day, app.cfg.latitude, app.cfg.longitude, tz);
  const before = astro.sunTimes(prev.getUTCFullYear(), prev.getUTCMonth() + 1, prev.getUTCDate(), app.cfg.latitude, app.cfg.longitude, tz);
  const hm = (ms) => (ms === null ? "—" : H.fr(H.localParts(ms, tz), "%H:%M"));
  const cols = [];

  const s = el("div", "sun");
  s.append(el("h3", "", "SOLEIL"), para([["Lever", ""], [hm(sun.sunrise), "b"]]), para([["Coucher", ""], [hm(sun.sunset), "b"]]),
    para([["Durée", ""], [astro.fmtDuration(sun.length), "b"], [`(${astro.signed((sun.length - before.length) / 60, " min")} vs hier)`, ""]]));
  cols.push(s);

  const n = el("div");
  if (!nm) {
    const st = app.normals ? app.normals.state : "";
    n.append(el("h3", "", st === "missing" ? "Normales climatiques indisponibles (réseau ?)" : "Normales climatiques : chargement…"));
  } else {
    const st = nm.station("tmean");
    n.append(el("h3", "", `NORMALES ${nm.period}` + (st ? ` — ${climate.prettyStation(st.name)} (${Math.round(st.km)} km)` : "")));
    const nd = ["tmean", "tmax", "tmin"].every((k) => nm.has(k));
    n.append(para(nd ? [[`Normale du jour (${today.day} ${climate.MONTHS[today.month - 1]})`, ""],
      [`max ${H.fx(nm.daily("tmax", today))}°`, "b"], [`min ${H.fx(nm.daily("tmin", today))}°`, "b"], [`moy. ${H.fx(nm.daily("tmean", today))}°`, "b"]] : [["—", ""]]));
    [["Aujourd'hui", 0], ["Demain", 1]].forEach(([lab, off]) => {
      const dt = new Date(Date.UTC(today.year, today.month - 1, today.day + off));
      const day = { year: dt.getUTCFullYear(), month: dt.getUTCMonth() + 1, day: dt.getUTCDate() };
      const idx = [];
      d.local.forEach((p, i) => { if (p.year === day.year && p.month === day.month && p.day === day.day && d.tt[i] !== null) idx.push(i); });
      if (!idx.length || !nd) return;
      const hi = Math.max(...idx.map((i) => d.tt[i])), lo = Math.min(...idx.map((i) => d.tt[i]));
      const dh = hi - nm.daily("tmax", day), dl = lo - nm.daily("tmin", day);
      const kind = (v) => (v > 0.5 ? "warm" : v < -0.5 ? "cold" : "");
      n.append(para([[`${lab} prévu${idx.length >= 20 ? "" : " (partiel)"}`, ""], [`max ${H.fx(hi)}°`, "b"], [astro.signed(dh), kind(dh)],
        ["min", ""], [`${H.fx(lo)}°`, "b"], [astro.signed(dl), kind(dl)], ["vs normale", ""]]));
    });
    const m = today.month, bits = [];
    if (nm.has("precip")) {
      bits.push(["Mois :", ""], [`${H.fx(nm.month("precip", m))} mm`, "b"]);
      if (nm.has("pdays")) bits.push([`(${H.fx(nm.month("pdays", m))} j)`, ""]);
    }
    if (nm.has("snow") && nm.month("snow", m) >= 0.5) bits.push(["neige", ""], [`${H.fx(nm.month("snow", m))} cm`, "b"]);
    if (nm.has("sun")) bits.push(["soleil", ""], [`${H.fx(nm.month("sun", m))} h`, "b"]);
    if (nm.has("wind")) bits.push(["vent", ""], [`${H.fx(nm.month("wind", m))} km/h`, "b"]);
    n.append(para(bits.length ? bits : [["—", ""]]));
  }
  cols.push(n);

  const link = el("div", "link"), a = el("a", "", "Prévisions Environnement Canada ↗");
  a.href = `https://meteo.gc.ca/fr/location/index.html?coords=${app.cfg.latitude.toFixed(3)},${app.cfg.longitude.toFixed(3)}`;
  a.target = "_blank"; a.rel = "noopener";
  link.append(a, el("small", "", "Source : Environnement et Changement climatique Canada"));
  cols.push(link);
  box.replaceChildren(...cols);
}

function startLoaders() {
  app.normals = new climate.Loader(app.cfg);
  app.tides = new tides.Loader(app.cfg);
  if (app.view) app.view.tides = app.tides;
  almanacKey = "";
}

function updateDetails() {
  const v = app.view, d = app.data;
  if (!v) return;
  const i = v.timeIndex(), key = `${d.raw.ref}/${i}/${d.nowIndex()}`;
  if (key === app.lastDetails) return;
  app.lastDetails = key;
  const vis = d.fogvis[i], gust = d.gust[i];
  const cells = [
    ["Température", `${H.fx(d.tt[i], 1)} °C`], ["Ressenti", d.re_[i] !== null ? `${H.fx(d.re_[i], 1)} °C` : "—"],
    ["Point de rosée", `${H.fx(d.td[i], 1)} °C`], ["Humidité", `${H.fx(d.hr[i])} %`],
    ["Vent", `${H.fx(d.ws[i])} km/h ${compass(d.wd[i])}`], ["Rafales", `${H.fx(gust)} km/h`],
    ["Pression", `${H.fx(d.slp[i], 1)} hPa`], ["Nuages", `${H.fx(d.nt[i])} %`],
    ["Visibilité (brume)", vis === null || vis >= 10000 ? ">10 km" : `${H.fx(vis / 1000, 1)} km`],
    ["Indice UV", H.fx(d.uv[i], 1)], ["Couche limite", `${H.fx(d.pbl[i])} m`], ["CAPE", `${H.fx(d.cape[i])} J/kg`],
    ["Précip. (heure)", `${H.fx(d.pr[i], 2)} mm` + (d.pint[i] ? ` · ${d.pint[i].toLowerCase()}` : "")],
    ["Neige ~", `${H.fx(d.snow_cm[i], 1)} cm` + (d.sd[i] >= 0.5 ? ` · sol ${H.fx(d.sd[i])} cm` : "")],
    ["Type", d.ptype[i] + (d.pchar[i] && d.ptype[i] !== "Aucune" ? ` · ${d.pchar[i].toLowerCase()}` : "")],
    ["Probabilité précip.", `${H.fx(d.pop[i])} %`],
    ["Limite pluie/neige", d.snowlvl[i] !== null ? `${H.fx(d.snowlvl[i])} m` : "—"],
    ["Orage", `${H.fx(d.tsprob[i])} %`],
  ];
  const grid = $("d-grid");
  grid.replaceChildren(...cells.map(([lab, val]) => {
    const div = document.createElement("div"), dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = lab; dd.textContent = val; div.append(dt, dd);
    return div;
  }));
  const off = i - d.nowIndex();
  $("d-when").textContent = `${d.icon(i)} ${H.fr(d.local[i], "%A %d %B · %Hh")}`;
  $("d-off").textContent = off === 0 ? "maintenant" : off > 0 ? `dans ${off} h` : `il y a ${-off} h`;
}

// ── canvases ─────────────────────────────────────────────────────────────────
const canv = {};
function fit(id, w, h) {
  const c = $(id), dpr = window.devicePixelRatio || 1;
  const W = Math.round(w * dpr), Hh = Math.round(h * dpr);
  if (c.width !== W || c.height !== Hh) { c.width = W; c.height = Hh; }
  if (id !== "map") c.style.height = `${h}px`;
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  canv[id] = { ctx, w, h, dpr };
}

function resize() {
  const box = document.querySelector(".map-box").getBoundingClientRect();
  if (box.width > 0) {
    fit("map", box.width, box.height);
    app.view?.resize(box.width, box.height);
  }
  const sw = $("slider").getBoundingClientRect().width;
  if (sw > 0) fit("slider", sw, 40);
  const cw = $("charts").parentElement.getBoundingClientRect().width;
  if (cw > 0) {
    const wide = matchMedia("(min-width: 1100px)").matches;
    const left = document.querySelector(".left").getBoundingClientRect().height;
    fit("charts", cw, wide ? Math.max(left, 600) : Math.round(Math.min(Math.max(cw * 1.45, 560), 780)));
  }
  app.dirty = true;
}

let last = performance.now(), lastDraw = 0, normalsSeen = -1;
function loop(now) {
  requestAnimationFrame(loop);
  const dt = Math.min((now - last) / 1000, 0.25);
  last = now;
  const v = app.view;
  if (!v || document.hidden) return;
  if (app.normals && app.normals.version !== normalsSeen) { normalsSeen = app.normals.version; updateAlmanac(); }
  v.tick(dt);
  if (!(v.playing || v.poll() || app.dirty) || now - lastDraw < FRAME_MS) return;
  lastDraw = now;
  app.dirty = false;
  try {
    if (canv.map) v.draw(canv.map.ctx);
    if (canv.slider) v.drawSlider(canv.slider.ctx, canv.slider.w, canv.slider.h);
    if (canv.charts) app.charts.draw(canv.charts.ctx, canv.charts.w, canv.charts.h, canv.charts.dpr, v);
    updateDetails();
    $("play").textContent = v.playing ? "❚❚" : "▶";
    $("speed").textContent = `×${H.fg(v.speed / 2)}`;
  } catch (e) {
    console.error(e);
  }
}

// ── input ────────────────────────────────────────────────────────────────────
function buildLayerButtons() {
  const box = document.querySelector(".layers");
  for (const [key, ic, label] of LAYERS) {
    const b = document.createElement("button");
    b.type = "button"; b.dataset.layer = key;
    b.innerHTML = `<span class="ic" aria-hidden="true">${ic}</span><span>${label}</span>`;
    b.addEventListener("click", () => setLayer(key));
    box.append(b);
  }
  syncLayerButtons();
}
function syncLayerButtons() {
  for (const b of document.querySelectorAll(".layers button")) b.setAttribute("aria-pressed", String(b.dataset.layer === (app.view?.layer || "rt")));
}
function setLayer(key) {
  if (!app.view) return;
  app.view.layer = key;
  syncLayerButtons();
  app.dirty = true;
}

const local = (e, el) => { const r = el.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };

function mapInput() {
  const el = $("map"), ptrs = new Map();
  let pinch = null, lastTap = { t: 0, x: 0, y: 0 }, moved = false;
  el.addEventListener("pointerdown", (e) => {
    const v = app.view;
    if (!v) return;
    el.setPointerCapture(e.pointerId);
    const [x, y] = local(e, el);
    ptrs.set(e.pointerId, [x, y]);
    app.dirty = true;
    if (ptrs.size === 2) {
      const [a, b] = [...ptrs.values()];
      pinch = { dist: Math.hypot(a[0] - b[0], a[1] - b[1]), mid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] };
      return;
    }
    if (ptrs.size > 2) return;
    moved = false;
    const [tag] = v.hit(x, y);
    if (tag === "zoom_in") v.zoomBy(+0.5);
    else if (tag === "zoom_out") v.zoomBy(-0.5);
    else if (tag === "zoom_reset") v.reset();
    else if (tag === "map") {
      v.beginPan();
      el._press = [x, y];
    }
  });
  el.addEventListener("pointermove", (e) => {
    const v = app.view;
    if (!v || !ptrs.has(e.pointerId)) return;
    const [x, y] = local(e, el);
    ptrs.set(e.pointerId, [x, y]);
    if (pinch && ptrs.size >= 2) {
      const [a, b] = [...ptrs.values()];
      const dist = Math.hypot(a[0] - b[0], a[1] - b[1]), mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      if (pinch.dist > 0 && dist > 0) v.zoomBy(Math.log2(dist / pinch.dist), mid[0], mid[1]);
      const [cx, cy] = v.center();
      v.setCenter(cx - (mid[0] - pinch.mid[0]), cy - (mid[1] - pinch.mid[1]));
      pinch = { dist, mid };
      moved = true;
    } else if (el._press && ptrs.size === 1) {
      const dx = x - el._press[0], dy = y - el._press[1];
      if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
      v.panTo(dx, dy);
    }
    app.dirty = true;
  });
  const up = (e) => {
    const v = app.view;
    if (!ptrs.has(e.pointerId)) return;
    const [x, y] = ptrs.get(e.pointerId);
    ptrs.delete(e.pointerId);
    if (ptrs.size < 2) pinch = null;
    if (ptrs.size === 1) {                                 // pinch -> one finger: continue as a pan from here
      el._press = [...ptrs.values()][0]; v?.beginPan();
    }
    if (ptrs.size === 0) {
      el._press = null;
      if (e.type === "pointerup" && !moved && v && v.hit(x, y)[0] === "map") {    // double tap / double click: zoom in
        if (e.timeStamp - lastTap.t < 320 && Math.hypot(x - lastTap.x, y - lastTap.y) < 30) {
          v.zoomBy(+1.0, x, y); lastTap = { t: 0, x: 0, y: 0 };
        } else lastTap = { t: e.timeStamp, x, y };
      }
    }
    app.dirty = true;
  };
  el.addEventListener("pointerup", up);
  el.addEventListener("pointercancel", up);
  el.addEventListener("wheel", (e) => {
    const v = app.view;
    if (!v) return;
    e.preventDefault();
    const [x, y] = local(e, el);
    v.zoomBy(0.4 * (e.deltaY < 0 ? 1 : -1), x, y);
    app.dirty = true;
  }, { passive: false });
}

function sliderInput() {
  const el = $("slider");
  let down = false;
  const set = (e) => { const v = app.view; if (!v) return; v.playing = false; v.sliderSet(local(e, el)[0], canv.slider.w); app.dirty = true; };
  el.addEventListener("pointerdown", (e) => { down = true; el.setPointerCapture(e.pointerId); set(e); });
  el.addEventListener("pointermove", (e) => { if (down) set(e); });
  el.addEventListener("pointerup", () => { down = false; });
  el.addEventListener("pointercancel", () => { down = false; });
}

function chartInput() {
  // Mouse: press/drag scrubs, hovering scrubs while paused (like the desktop). Touch: a tap or a horizontal drag
  // scrubs; a vertical swipe scrolls the page (touch-action: pan-y) and leaves the time alone.
  const el = $("charts");
  let start = null, scrubbing = false;
  const scrub = (e) => {
    const v = app.view;
    if (!v) return;
    v.playing = false;
    v.t = app.charts.tFromX(local(e, el)[0], canv.charts.w, canv.charts.h, v);
    app.dirty = true;
  };
  el.addEventListener("pointerdown", (e) => {
    start = [e.clientX, e.clientY]; scrubbing = e.pointerType === "mouse";
    if (scrubbing) scrub(e);
  });
  el.addEventListener("pointermove", (e) => {
    const v = app.view;
    if (start && !scrubbing && e.pointerType !== "mouse" && Math.abs(e.clientX - start[0]) > 8
        && Math.abs(e.clientX - start[0]) > Math.abs(e.clientY - start[1])) scrubbing = true;
    if (scrubbing && start) scrub(e);
    else if (!start && e.pointerType === "mouse" && v && !v.playing) scrub(e);
  });
  el.addEventListener("pointerup", (e) => {
    if (start && !scrubbing && Math.hypot(e.clientX - start[0], e.clientY - start[1]) < 10) scrub(e);   // tap
    start = null; scrubbing = false;
  });
  el.addEventListener("pointercancel", () => { start = null; scrubbing = false; });
}

function keyboard() {
  document.addEventListener("keydown", (e) => {
    const v = app.view;
    if (!v || e.target.closest("input, dialog") || e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key;
    if (k === " ") v.playing = !v.playing;
    else if (k === "ArrowLeft" || k === "ArrowRight") {
      v.playing = false;
      v.t = Math.min(Math.max(Math.round(v.t) + (k === "ArrowRight" ? 1 : -1), v.i0), v.i1);
    } else if (k === "ArrowUp" || k === "ArrowDown") v.speed = Math.min(Math.max(v.speed * (k === "ArrowUp" ? 2 : 0.5), 1), 8);
    else if (k === "Home") v.t = v.i0;
    else if ("123456".includes(k) && k.length === 1) setLayer(LAYERS[+k - 1][0]);
    else if (k === "+" || k === "=") v.zoomBy(+0.5);
    else if (k === "-") v.zoomBy(-0.5);
    else if (k === "0") v.reset();
    else return;
    e.preventDefault();
    app.dirty = true;
  });
}

// ── settings ─────────────────────────────────────────────────────────────────
/** Inside the Android app (android/), hand the place to the home-screen widget. */
function tellAndroid() {
  try { window.HrdpsAndroid?.setPlace(JSON.stringify(app.cfg)); } catch { /* not in the Android app */ }
}

function openSettings(first = false) {
  const dlg = $("settings"), f = $("settings-form"), c = app.cfg || { ...config.DEFAULTS, timezone: config.deviceTimezone() };
  f.location.value = c.location; f.latitude.value = c.latitude; f.longitude.value = c.longitude; f.timezone.value = c.timezone;
  f.tide_station.value = c.tide_station || "";
  $("settings-error").textContent = "";
  $("settings-cancel").hidden = first;
  $("settings-intro").hidden = !first;
  dlg.showModal();
}

function settingsDialog() {
  const dlg = $("settings"), f = $("settings-form"), err = $("settings-error");
  dlg.addEventListener("cancel", (e) => { if (!app.cfg) e.preventDefault(); });     // first launch: a place is required
  $("settings-cancel").addEventListener("click", () => dlg.close());
  $("geo").addEventListener("click", () => {
    if (!navigator.geolocation) { err.textContent = "La géolocalisation n'est pas disponible sur cet appareil."; return; }
    err.textContent = "Recherche de la position…";
    navigator.geolocation.getCurrentPosition((p) => {
      f.latitude.value = p.coords.latitude.toFixed(4);
      f.longitude.value = p.coords.longitude.toFixed(4);
      if (!f.location.value.trim() || f.location.value === config.DEFAULTS.location) f.location.value = "Ma position";
      f.timezone.value = config.deviceTimezone();
      err.textContent = "";
    }, (e) => {
      err.textContent = e.code === 1 ? "Accès à la position refusé : entrez la latitude et la longitude." : "Position introuvable : entrez-la à la main.";
    }, { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 });
  });
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const num = (s) => Number(String(s).trim().replace(",", "."));
    const c = { location: f.location.value.trim(), latitude: num(f.latitude.value), longitude: num(f.longitude.value), timezone: f.timezone.value.trim(),
      tide_station: f.tide_station.value.trim() };
    const msg = config.check(c);
    if (msg) { err.textContent = msg; return; }
    const before = app.cfg;
    app.cfg = config.save(c);
    tellAndroid();
    dlg.close();
    if (!before) return;                                    // first launch: main() carries on
    if (!config.samePlace(before, app.cfg) || before.timezone !== app.cfg.timezone) await changePlace();
    else {
      if (app.view) { app.view.place = app.cfg; }
      if ((before.tide_station || "") !== (app.cfg.tide_station || "")) startLoaders();       // another tide station
      updateHeader(); app.dirty = true;
    }
  });
}

async function changePlace() {
  app.data = null; app.maps = null; app.view = null; app.lastDetails = ""; app.charts = new Charts();
  startLoaders();
  document.body.classList.add("nodata");
  $("empty").hidden = false;
  $("empty-text").textContent = "Téléchargement des données HRDPS… (~1 à 2 minutes la première fois)";
  for (const id of ["icon", "sub", "alerts", "run", "next"]) $(id).textContent = "";
  $("almanac").replaceChildren();
  $("temp").textContent = "–"; $("describe").textContent = "Météo HRDPS";
  await loadCache();
  updateHeader();
  syncLayerButtons();
  while (app.busy) await new Promise((r) => setTimeout(r, 200));       // let a refresh for the old place finish
  refresh();
}

// ── service worker (offline + installable) ───────────────────────────────────
const AUTO_APPLY_MS = 20e3;              // an update found this soon after the page opened is applied silently
function serviceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("sw.js").then((reg) => {
    // Just opened: switch to the new version at once (one quick reload). Later: ask, so that a session in progress
    // (scrubbing the time, panning the map) is not interrupted.
    const offer = (w) => {
      if (performance.now() < AUTO_APPLY_MS) { w.postMessage("skip-waiting"); return; }
      const b = $("banner");
      b.hidden = false;
      b.onclick = () => w.postMessage("skip-waiting");
    };
    if (reg.waiting && navigator.serviceWorker.controller) offer(reg.waiting);
    reg.addEventListener("updatefound", () => {
      const w = reg.installing;
      w?.addEventListener("statechange", () => { if (w.state === "installed" && navigator.serviceWorker.controller) offer(w); });
    });
    // A browser looks for a new version by itself only when a page is *navigated to* (and at most once a day). An app
    // that stays in memory (the Android app, an installed PWA) is resumed, not navigated: that is how updates went
    // unnoticed. So look at startup, every time the app comes back to the foreground, and every half hour.
    const check = () => reg.update().catch(() => {});
    check();
    document.addEventListener("visibilitychange", () => { if (!document.hidden) check(); });
    setInterval(check, 30 * 60e3);
  }).catch(() => {});
  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => { if (!reloading) { reloading = true; location.reload(); } });
}

/** Escape hatch for a stuck copy of the app: forget the offline copy of the app files (not the data, the place or the
 *  map tiles) and reload. Nothing else is touched, so there is never a need to clear the app's data. */
async function reloadApp() {
  try { for (const r of await navigator.serviceWorker.getRegistrations()) await r.unregister(); } catch { /* none */ }
  try { for (const k of await caches.keys()) if (k.startsWith("shell-")) await caches.delete(k); } catch { /* none */ }
  location.reload();
}

// ── start ────────────────────────────────────────────────────────────────────
async function main() {
  $("version").textContent = VERSION;
  document.body.classList.add("nodata");
  $("empty").hidden = false;
  buildLayerButtons();
  mapInput(); sliderInput(); chartInput(); keyboard(); settingsDialog();
  $("place-btn").addEventListener("click", () => openSettings(false));
  $("refresh").addEventListener("click", () => refresh(true));
  $("reload-app").addEventListener("click", reloadApp);
  try {                                                             // Android app: offer to add the widget
    if (window.HrdpsAndroid?.canPinWidget?.()) {
      $("pin-widget").hidden = false;
      $("pin-widget").addEventListener("click", () => window.HrdpsAndroid.pinWidget());
      if (window.HrdpsAndroid.pinLargeWidget) {                     // app 1.6+
        $("pin-widget-large").hidden = false;
        $("pin-widget-large").addEventListener("click", () => window.HrdpsAndroid.pinLargeWidget());
      }
    }
  } catch { /* not in the Android app */ }
  $("play").addEventListener("click", () => { if (app.view) { app.view.playing = !app.view.playing; app.dirty = true; } });
  $("speed").addEventListener("click", () => {
    const v = app.view;
    if (v) { v.speed = { 2: 4, 4: 8, 8: 1, 1: 2 }[v.speed] || 2; app.dirty = true; }
  });
  new ResizeObserver(resize).observe(document.body);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { maybeRefresh(); app.dirty = true; } });
  setInterval(() => {                                               // every 30 s: clock-driven labels and checks
    if (app.data && app.view && app.data.nowIndex() !== app.view.i0) { app.view.setData(app.data); updateHeader(); }
    updateNextRun();
    maybeRefresh();
  }, 30e3);
  serviceWorker();
  requestAnimationFrame(loop);

  if (!app.cfg) {
    updateHeader();
    $("empty-text").textContent = "Choisissez votre position pour commencer.";
    openSettings(true);
    await new Promise((r) => $("settings").addEventListener("close", r, { once: true }));
    if (!app.cfg) return;
    $("empty-text").textContent = "Téléchargement des données HRDPS… (~1 à 2 minutes la première fois)";
  }
  tellAndroid();
  startLoaders();
  await loadCache();
  updateHeader();
  syncLayerButtons();
  maybeRefresh();
}

main();
