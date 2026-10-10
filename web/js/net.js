// HTTP GET with retries and a small worker pool (port of net.py).
// Browsers open at most 6 connections per host over HTTP/1.1 (GeoMet), so 6 workers is the useful maximum.

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Body of a 200 response (as text, bytes or blob), or null (network error, 4xx/5xx). */
export async function get(url, { as = "text", timeout = 30000, tries = 2, signal } = {}) {
  for (let attempt = 0; attempt < tries; attempt++) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeout);
    const onAbort = () => ctl.abort();
    signal?.addEventListener("abort", onAbort);
    try {
      const r = await fetch(url, { signal: ctl.signal, cache: "no-store" });
      if (r.ok) {
        return as === "blob" ? await r.blob() : as === "bytes" ? new Uint8Array(await r.arrayBuffer()) : await r.text();
      }
      if (r.status >= 400 && r.status < 500) return null;
    } catch {
      if (signal?.aborted) return null;
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
    if (attempt + 1 < tries) await sleep(500 * (attempt + 1));
  }
  return null;
}

/** Run fn over items with n concurrent workers; results keep the input order. */
export async function pool(items, n, fn) {
  const out = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const k = next++;
      out[k] = await fn(items[k], k);
    }
  };
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, worker));
  return out;
}
