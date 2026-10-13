// A missing Precip-Prob value (GeoMet leaves saturated hours empty) is bridged, not read as 0 % (regression).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import * as H from "../js/hrdps.js";

const sample = JSON.parse(readFileSync(new URL("../../src/hrdps_weather/data/sample_series.json", import.meta.url), "utf-8"));
const place = { latitude: 46.8139, longitude: -71.208, timezone: "America/Toronto" };
const withPop = (pop) => { const raw = structuredClone(sample); raw.series.pop = pop; return new H.Data(raw, place); };
const n = sample.series.pop.length;

test("a gap between two 99 % values stays at 99 %", () => {
  const d = withPop([null, 97, 99, 99, null, null, null, 99, 98, ...Array(n - 9).fill(50)]);
  assert.deepEqual(d.pop.slice(4, 7), [99, 99, 99]);
  assert.equal(d.pop[0], 97);
});

test("no data at all gives 0, a ramp is interpolated linearly", () => {
  assert.deepEqual([...new Set(withPop(Array(n).fill(null)).pop)], [0]);
  const d = withPop([10, null, null, 40, ...Array(n - 4).fill(40)]);
  assert.ok(Math.abs(d.pop[1] - 20) < 1e-9 && Math.abs(d.pop[2] - 30) < 1e-9);
});
