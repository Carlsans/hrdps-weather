// A year of tide predictions kept for offline use (port of the tests in tests/test_tides.py).
import assert from "node:assert/strict";
import { test } from "node:test";

import { TideData, fetchYear } from "../js/tides.js";

const st = { id: "x", code: "B", series: ["wlp", "wlp-hilo"] };
const NOW = 1_800_000_000;

function fakeService(failAfterDays = null) {
  const spans = [];
  globalThis.fetch = async (url) => {
    const q = new URL(url).searchParams;
    const a = Date.parse(q.get("from")) / 1000, b = Date.parse(q.get("to")) / 1000;
    const code = q.get("time-series-code");
    let rows;
    if (code === "wlp-hilo") rows = [[a + 3600, 1], [b - 3600, 5]];
    else {
      spans.push(b - a);
      if (failAfterDays !== null && a > NOW + failAfterDays * 86400) return new Response("nope", { status: 400 });
      rows = Array.from({ length: Math.floor((b - a) / 900) + 1 }, (_, k) => [a + 900 * k, 2]);
    }
    return new Response(JSON.stringify(rows.map(([t, v]) => ({ eventDate: new Date(t * 1000).toISOString().replace(/\.\d{3}Z$/, "Z"), value: v }))));
  };
  return spans;
}

test("a year is asked in chunks the service accepts, and merged without duplicates", async () => {
  const spans = fakeService();
  const y = await fetchYear(st, NOW);
  assert.ok(spans.length >= 13 && Math.max(...spans) <= 31 * 86400);
  assert.equal(y.to - NOW, 365 * 86400);
  const times = y.wlp.map((p) => p[0]);
  assert.deepEqual(times, [...new Set(times)].sort((p, q) => p - q));
  assert.ok(times.at(-1) - times[0] > 364 * 86400);
  assert.equal(y.hilo.length, 2);
});

test("nothing rather than half a year", async () => {
  fakeService(100);
  assert.equal(await fetchYear(st, NOW), null);
});

test("offline data is the window around now, flagged as not downloaded", () => {
  const wlp = Array.from({ length: 365 * 96 }, (_, k) => [NOW - 86400 + 900 * k, 2]);
  const hilo = [[NOW - 7 * 86400, 1], [NOW + 3600, 5], [NOW + 30 * 86400, 1]];
  const d = TideData.fromYear({ code: "B" }, { wlp, hilo }, NOW);
  assert.equal(d.fetched, 0);
  assert.ok(d.wlp.length > 300 && d.wlp.length < 500 && d.wlp[0][0] >= NOW - 86400 && d.wlp.at(-1)[0] <= NOW + 72 * 3600);
  assert.equal(d.events.length, 2);                      // the whole future list is kept, the past before the window is not
  assert.equal(d.wlo.length, 0);
});
