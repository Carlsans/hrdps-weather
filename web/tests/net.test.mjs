// The "no downloads while roaming" switch of the Android app: get() must not even try the network.
import assert from "node:assert/strict";
import { test } from "node:test";

import { blocked, get, setBlocker } from "../js/net.js";

test("blocked: get() answers null without any request, and works again once released", async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls++; return new Response("ok"); };
  setBlocker(() => true);
  assert.equal(blocked(), true);
  assert.equal(await get("https://example.invalid/x"), null);
  assert.equal(calls, 0);
  setBlocker(() => false);
  assert.equal(await get("https://example.invalid/x"), "ok");
  assert.equal(calls, 1);
});
