// The web data layer must reproduce the Python one exactly (same run, same place, same output).
//   python web/tests/make_expected.py expected.json && EXPECTED=expected.json node --test web/tests/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import * as H from "../js/hrdps.js";

const here = new URL(".", import.meta.url);
const sample = JSON.parse(readFileSync(new URL("../../src/hrdps_weather/data/sample_series.json", here), "utf-8"));
const expectedPath = process.env.EXPECTED;
const exp = expectedPath ? JSON.parse(readFileSync(expectedPath, "utf-8")) : null;
const place = exp ? exp.place : { latitude: 46.8139, longitude: -71.208, timezone: "America/Toronto" };
const d = new H.Data(sample, place);

const close = (a, b, msg) => {
  if (a === null || b === null || typeof a === "string") return assert.equal(a, b, msg);
  assert.ok(Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(b)), `${msg}: ${a} != ${b}`);
};

test("Python reference available", { skip: !exp && "set EXPECTED (see make_expected.py)" }, () => {});

test("derived series match Python", { skip: !exp }, () => {
  for (const [k, vals] of Object.entries(exp.series)) {
    assert.equal(d[k].length, vals.length, k);
    vals.forEach((v, i) => close(d[k][i], v, `${k}[${i}]`));
  }
});

test("per-hour kind, icon, description, day/night", { skip: !exp }, () => {
  for (let i = 0; i < d.n; i++) {
    assert.equal(d.kind(i), exp.kind[i], `kind[${i}]`);
    assert.equal(d.icon(i), exp.icon[i], `icon[${i}]`);
    assert.equal(d.describe(i), exp.describe[i], `describe[${i}]`);
    assert.equal(d.isDay(i), exp.is_day[i], `is_day[${i}]`);
  }
});

test("alerts from every starting hour", { skip: !exp }, () => {
  for (let i = 0; i < d.n; i++) assert.deepEqual(d.alerts(i), exp.alerts[i], `alerts(${i})`);
});

test("French dates and number formatting", { skip: !exp }, () => {
  const pats = ["%a %Hh", "%a %d %b %Hh", "%A %d %B · %Hh", "%a %d", "%Hh%M"];
  for (let i = 0; i < d.n; i++) {
    assert.deepEqual(pats.map((p) => H.fr(d.local[i], p)), exp.fmt[i], `fmt[${i}]`);
    assert.deepEqual([H.fx(d.tt[i]), H.fx(d.tt[i], 1), H.fx(d.ws[i]), H.fx(d.pr[i], 2), H.fx(d.slp[i], 1)],
      exp.fixed[i], `fixed[${i}]`);
  }
});

test("next run text, map bounds, sun, gap filling", { skip: !exp }, () => {
  for (const [t, text] of exp.next_run) assert.equal(H.nextRunText(d.ref, place.timezone, H.parseIso(t)), text);
  const b = exp.map_bounds;
  H.mapBounds(place.latitude, place.longitude, H.REGION_ZOOM).forEach((v, k) => close(v, b.region[k], "region"));
  H.mapBounds(place.latitude, place.longitude, H.WIDE_ZOOM).forEach((v, k) => close(v, b.wide[k], "wide"));
  for (const [t, el] of exp.sun) close(H.sunElevation(H.parseIso(t), place.latitude, place.longitude), el, `sun ${t}`);
  const inputs = [[null, 1.0, null, null, 4.0, null], [null, null], [2.0, null, 3.0]];
  inputs.forEach((v, k) => assert.deepEqual(H.fill(v), exp.fill[k]));
});

test("Python-style rounding", () => {
  assert.equal(H.fx(0.5), "0");
  assert.equal(H.fx(1.5), "2");
  assert.equal(H.fx(2.5), "2");
  assert.equal(H.fx(-0.5), "-0");
  assert.equal(H.fx(-0.3), "-0");
  assert.equal(H.fx(-1.5), "-2");
  assert.equal(H.fx(0.25, 1), "0.2");
  assert.equal(H.fx(0.45, 1), "0.5");          // 0.45 is slightly above .45 in binary: not a tie
  assert.equal(H.fx(0.125, 2), "0.12");
  assert.equal(H.fx(12.34, 1), "12.3");
  assert.equal(H.fg(0.1), "0.1");
  assert.equal(H.fg(32), "32");
});

test("GetCapabilities and ASCII grid parsing", () => {
  const caps = `<Layer><Name>HRDPS.CONTINENTAL_TT</Name><Dimension name="time">x</Dimension>
    <Dimension name="reference_time" units="ISO8601">2026-10-09T06:00:00Z/2026-10-10T12:00:00Z/PT6H</Dimension></Layer>`;
  assert.equal(H.iso(H.parseLatestRef(caps)), "2026-10-10T12:00:00Z");
  const list = caps.replace("2026-10-09T06:00:00Z/2026-10-10T12:00:00Z/PT6H", "2026-10-10T06:00:00Z,2026-10-10T12:00:00Z");
  assert.equal(H.iso(H.parseLatestRef(list)), "2026-10-10T12:00:00Z");
  assert.equal(H.parseLatestRef("<nothing/>"), null);

  const grid = "--wcs\r\nContent-Type: image/x-aaigrid\r\n\r\nncols        3\nnrows        2\nxllcorner    -72.4\n"
    + "yllcorner    45.8\ndx           0.02\ndy           0.02\nNODATA_value -9999\n 1 2.5 -3\n -9999 5 -1e30\n--wcs--\n";
  const g = H.parseAAIGrid(grid);
  assert.equal(g.ny, 2);
  assert.equal(g.nx, 3);
  assert.deepEqual([...g.data.slice(0, 3)], [1, 2.5, -3]);
  assert.ok(Number.isNaN(g.data[3]) && Number.isNaN(g.data[5]));
  assert.equal(g.data[4], 5);
  assert.equal(H.parseAAIGrid("no grid here"), null);
});

test("now index is clamped to the run", () => {
  assert.equal(d.nowIndex(d.ref - 5 * H.HOUR_MS), 0);
  assert.equal(d.nowIndex(d.ref + 7.5 * H.HOUR_MS), 7);
  assert.equal(d.nowIndex(d.ref + 500 * H.HOUR_MS), d.n - 1);
});
