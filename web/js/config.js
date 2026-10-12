// User configuration (port of config.py), kept in localStorage on this device only.

const KEY = "hrdps-weather.config";

export const DEFAULTS = {
  latitude: 46.8139,          // Québec (ville)
  longitude: -71.208,
  location: "Québec",
  timezone: "America/Toronto",
};

// HRDPS continental domain (approx.): anything outside has no data.
export const LAT_RANGE = [27.3, 70.6];
export const LON_RANGE = [-152.7, -40.7];

export function deviceTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || DEFAULTS.timezone;
  } catch {
    return DEFAULTS.timezone;
  }
}

export function validTimezone(tz) {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/** Error message (French) for an invalid configuration, or "" when it is usable. */
export function check(cfg) {
  const lat = Number(cfg.latitude), lon = Number(cfg.longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return "Latitude et longitude doivent être des nombres.";
  if (!(LAT_RANGE[0] <= lat && lat <= LAT_RANGE[1] && LON_RANGE[0] <= lon && lon <= LON_RANGE[1])) {
    return `(${lat}, ${lon}) est hors du domaine HRDPS (lat ${LAT_RANGE[0]}–${LAT_RANGE[1]}, lon ${LON_RANGE[0]}–${LON_RANGE[1]}).`;
  }
  if (!validTimezone(cfg.timezone)) return `Fuseau horaire inconnu : ${cfg.timezone}`;
  return "";
}

/** The saved configuration, or null on first launch. */
export function load() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const cfg = { ...DEFAULTS, ...JSON.parse(raw) };
    cfg.latitude = Number(cfg.latitude);
    cfg.longitude = Number(cfg.longitude);
    return check(cfg) ? null : cfg;
  } catch {
    return null;
  }
}

export function save(cfg) {
  const clean = {
    latitude: Number(cfg.latitude), longitude: Number(cfg.longitude),
    location: String(cfg.location || "").trim() || "Ma position", timezone: cfg.timezone,
  };
  const station = String(cfg.tide_station || "").trim();                    // optional: code of a tide station
  if (station) clean.tide_station = station;
  localStorage.setItem(KEY, JSON.stringify(clean));
  return clean;
}

/** Same place (to the ~100 m the forecast cares about)? */
export function samePlace(a, b) {
  return !!a && !!b && Math.abs(a.latitude - b.latitude) < 1e-3 && Math.abs(a.longitude - b.longitude) < 1e-3;
}
