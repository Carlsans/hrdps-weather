// Touching or dragging the tides chart moves the time line (MapView.indexAt / tideSet).
import assert from "node:assert/strict";
import { test } from "node:test";

import { MapView } from "../js/view.js";

const HOUR = 3600e3, T0 = 1_800_000_000_000;
const fake = () => {
  const v = Object.create(MapView.prototype);
  v.d = { utc: Array.from({ length: 49 }, (_, k) => T0 + k * HOUR) };
  v.i0 = 3; v.i1 = 48; v.t = 3;
  return v;
};

test("a moment is mapped to a fractional hour of the forecast, kept inside it", () => {
  const v = fake();
  assert.equal(v.indexAt(T0), 3);                            // before the first hour shown
  assert.equal(v.indexAt(T0 + 100 * HOUR), 48);              // after the last
  assert.equal(v.indexAt(T0 + 10.5 * HOUR), 10.5);
});

test("an x position on the chart sets the time", () => {
  const v = fake(), g = { t0: (T0 - 6 * HOUR) / 1000, t1: (T0 + 54 * HOUR) / 1000, cx0: 40, cx1: 340 };   // 60 h over 300 px
  v.tideSet(40 + 150, g);                                    // the middle: T0 + 24 h
  assert.equal(v.t, 24);
  v.tideSet(0, g); assert.equal(v.t, 3);                     // left of the chart
  v.tideSet(999, g); assert.equal(v.t, 48);                  // right of it
  v.tideSet(40 + 100, g); v.tideSet(40 + 250, g);            // dragging to the right goes later
  assert.equal(v.t, 44);                                     // 250 px = 50 h after the left edge = T0 + 44 h
});
