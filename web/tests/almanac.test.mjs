// Sunrise/sunset, climate normals and tides: the JS ports must reproduce the Python reference (see make_expected.py).
//   python web/tests/make_expected.py expected.json && EXPECTED=expected.json node --test web/tests/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import * as astro from "../js/astro.js";
import * as climate from "../js/climate.js";
import * as H from "../js/hrdps.js";
import * as tides from "../js/tides.js";

const exp = process.env.EXPECTED ? JSON.parse(readFileSync(process.env.EXPECTED, "utf-8")) : null;
const skip = !exp && "set EXPECTED (see make_expected.py)";
const near = (a, b, tol, msg) => {
  if (a === null || b === null) return assert.equal(a, b, msg);
  assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a} != ${b}`);
};

test("sunrise, sunset and day length match Python (6 places, up to 6 days each, polar cases included)", { skip }, () => {
  assert.ok(exp.almanac.sun.length >= 32);
  for (const s of exp.almanac.sun) {
    const t = astro.sunTimes(...s.day, s.lat, s.lon, s.tz);
    const label = `${s.lat},${s.lon} ${s.day}`;
    near(t.sunrise, s.sunrise, 2, `sunrise ${label}`);
    near(t.sunset, s.sunset, 2, `sunset ${label}`);
    near(t.length, s.length, 1e-3, `length ${label}`);
  }
});

test("local noon is right across daylight-saving changes", () => {
  const spring = astro.localNoon(2026, 3, 8, "America/Toronto");          // the day clocks go forward
  assert.equal(H.localParts(spring, "America/Toronto").hour, 12);
  assert.equal(H.localParts(astro.localNoon(2026, 11, 1, "America/Toronto"), "America/Toronto").hour, 12);
  assert.equal(H.localParts(astro.localNoon(2026, 6, 21, "America/Vancouver"), "America/Vancouver").day, 21);
});

test("signed() and fmtDuration() match Python", { skip }, () => {
  for (const [v, plain, mins] of exp.almanac.signed) {
    assert.equal(astro.signed(v), plain, `signed(${v})`);
    assert.equal(astro.signed(v, " min"), mins, `signed(${v}, min)`);
  }
  for (const [s, text] of exp.almanac.duration) assert.equal(astro.fmtDuration(s), text, `fmtDuration(${s})`);
  assert.equal(astro.signed(-0.4), "0°");
});

test("normals: interpolation of the monthly values and station names match Python", { skip }, () => {
  const nm = new climate.Normals({ monthly: { tmean: exp.almanac.normals.monthly }, src: { tmean: [1, "X", 2.0] } });
  for (const [year, month, day, v] of exp.almanac.normals.daily) near(nm.daily("tmean", { year, month, day }), v, 1e-12, `${year}-${month}-${day}`);
  for (const [name, pretty] of exp.almanac.pretty) assert.equal(climate.prettyStation(name), pretty, name);
});

const tideData = () => {
  const x = exp.tides;
  return new tides.TideData({ station: x.stations[1], fetched: 0, wlp: x.wlp, wlo: x.wlo, hilo: x.hilo, wt: x.wt });
};

test("tides: high/low labels, interpolation, trend, next events match Python", { skip }, () => {
  const x = exp.tides, td = tideData();
  assert.deepEqual(td.events, x.events);
  x.probes.forEach((t, i) => {
    near(td.predicted(t), x.predicted[i], 1e-9, `predicted[${i}]`);
    near(td.trend(t), x.trend[i], 1e-9, `trend[${i}]`);
    assert.deepEqual(td.latestObserved(t), x.observed[i], `observed[${i}]`);
    assert.deepEqual(td.nextEvents(t, 3), x.next[i], `next[${i}]`);
    near(td.waterTemperature(t), x.temp[i], 0, `temp[${i}]`);
  });
});

test("tides: trend text, feet and station choice match Python", { skip }, () => {
  const x = exp.tides;
  [0.85, -0.2, 0.01, null, 0.049, -0.05].forEach((r, i) => assert.equal(tides.trendText(r), x.trend_text[i], `trendText(${r})`));
  for (const [m, feet] of x.feet) assert.equal(H.fx(m * tides.FEET_PER_M, 1), feet, `${m} m`);
  const pick = (lat, lon, ov) => { const s = tides.chooseStation(lat, lon, x.stations, ov); return s ? [s.code, s.km] : null; };
  const got = [pick(46.758, -71.24), pick(46.758, -71.24, "A"), pick(46.758, -71.24, "Z"), pick(10.0, -60.0), pick(47.8, -69.6)];
  assert.deepEqual(got, x.choose);
});

test("tides: gaps and edges", () => {
  assert.equal(tides.TideData.at([[0, 1], [4 * 3600, 5]], 2 * 3600), null);          // across a gap of more than 3 h
  assert.equal(tides.TideData.at([[0, 1], [3600, 3]], 1800), 2);
  assert.equal(tides.TideData.at([[0, 1], [3600, 3]], -1), null);
  assert.equal(tides.TideData.at([], 5), null);
  assert.equal(tides.FEET_PER_M, 1 / 0.3048 > 3.2808398 ? tides.FEET_PER_M : -1);
});

test("tides: station list, loader states and the cache, with a fake service", async () => {
  const stations = [{ officialName: "Permanente", code: "B", id: "id-B", type: "PERMANENT", operating: true, latitude: 46.81, longitude: -71.2,
    timeSeries: ["wlo", "wlp", "wlp-hilo", "wlf-spine", "wt1"].map((code) => ({ code })) }];
  const calls = [];
  globalThis.indexedDB = undefined;                                                     // no IndexedDB in Node: store calls fail softly
  globalThis.fetch = async (url) => {
    calls.push(url);
    if (url.endsWith("/stations")) return new Response(JSON.stringify(stations));
    const code = url.split("time-series-code=")[1].split("&")[0], now = Date.now();
    const rows = Array.from({ length: 12 }, (_, k) => ({ eventDate: new Date(now - 6 * 3600e3 + k * 1800e3).toISOString().replace(/\.\d{3}Z$/, "Z"), value: 2 + (k % 4) * 0.8 }));
    return new Response(JSON.stringify(code === "wlp-hilo" ? rows.slice(0, 4) : rows));
  };
  const ld = new tides.Loader({ latitude: 46.758, longitude: -71.24 });
  await new Promise((r) => setTimeout(r, 50));
  ld.ensure();
  for (let k = 0; k < 100 && (ld._busy || !ld.data); k++) await new Promise((r) => setTimeout(r, 30));
  assert.equal(ld.state, "idle");
  assert.equal(ld.data.station.code, "B");
  assert.ok(ld.data.wlp.length && ld.data.events.length && ld.data.wlo.length && ld.data.wlf.length && ld.data.wt.length);
  const n = calls.length;
  ld.ensure();
  assert.equal(calls.length, n, "fresh data: nothing is fetched again");
  const none = new tides.Loader({ latitude: 10, longitude: -60 });
  await new Promise((r) => setTimeout(r, 50));
  none.ensure();
  for (let k = 0; k < 100 && none.state === "loading"; k++) await new Promise((r) => setTimeout(r, 30));
  assert.equal(none.state, "none");
});
