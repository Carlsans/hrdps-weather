// Tiny key/value store over IndexedDB (series, map grids and state can be several MB: too big for localStorage).

const DB = "hrdps-weather", STORE = "kv";
let _db = null;

function open() {
  if (_db) return _db;
  _db = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return _db;
}

function tx(mode, fn) {
  return open().then((db) => new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const req = fn(t.objectStore(STORE));
    t.oncomplete = () => resolve(req?.result);
    t.onerror = t.onabort = () => reject(t.error);
  }));
}

export const get = (key) => tx("readonly", (s) => s.get(key)).catch(() => undefined);
export const set = (key, value) => tx("readwrite", (s) => s.put(value, key));
export const del = (key) => tx("readwrite", (s) => s.delete(key));
