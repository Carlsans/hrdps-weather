import assert from "node:assert/strict";
import { test } from "node:test";

import { isNewer, newerApk } from "../js/appupdate.js";

const rel = (tag, extra = {}) => ({ tag_name: tag, html_url: "https://example/r", assets: [{ name: "hrdps-weather-android.apk" }], ...extra });

test("versions compare numerically", () => {
  assert.ok(isNewer("1.8.10", "1.8.9") && isNewer("v2.0.0", "1.99.9") && isNewer("1.8.1", "1.8"));
  assert.ok(!isNewer("1.8.0", "1.8.0") && !isNewer("1.7.9", "1.8.0"));
});

test("only a newer release that already has its APK is offered", () => {
  assert.deepEqual(newerApk(rel("v1.9.0"), "1.8.0"), { version: "1.9.0", url: "https://example/r" });
  assert.equal(newerApk(rel("v1.8.0"), "1.8.0"), null);
  assert.equal(newerApk(rel("v1.9.0", { assets: [{ name: "hrdps-weather-linux-x86_64" }] }), "1.8.0"), null);
  assert.equal(newerApk(rel("v1.9.0", { prerelease: true }), "1.8.0"), null);
  assert.equal(newerApk(null, "1.8.0"), null);
});
