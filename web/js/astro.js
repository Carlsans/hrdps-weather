// Sunrise, sunset and day length (port of astro.py; NOAA low-precision formulas, about a minute of accuracy).

import { fx, localParts } from "./hrdps.js";

const rad = (d) => d * Math.PI / 180;

function solar(doy, hourUtc) {
  const g = 2 * Math.PI / 365 * (doy - 1 + (hourUtc - 12) / 24);
  const decl = 0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g) - 0.006758 * Math.cos(2 * g)
    + 0.000907 * Math.sin(2 * g) - 0.002697 * Math.cos(3 * g) + 0.00148 * Math.sin(3 * g);
  const eqt = 229.18 * (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g)
    - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));
  return [decl, eqt];                                    // radians, minutes
}

/** Hour angle (degrees) at which the sun is at altitude `alt`; null when it never gets there that day. */
function hourAngle(lat, decl, alt) {
  const la = rad(lat);
  const c = (Math.sin(rad(alt)) - Math.sin(la) * Math.sin(decl)) / (Math.cos(la) * Math.cos(decl));
  return c > 1 || c < -1 ? null : Math.acos(c) * 180 / Math.PI;
}

/** Epoch ms of 12:00 local time on the calendar day (y, m, d) in time zone tz. */
export function localNoon(y, m, d, tz) {
  const want = Date.UTC(y, m - 1, d, 12);
  let ms = want;
  for (let k = 0; k < 2; k++) {                          // two passes settle any offset, DST included
    const p = localParts(ms, tz);
    ms -= Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute) - want;
  }
  return ms;
}

/** {sunrise, sunset} as epoch ms (null when the sun does not rise/set) and length in seconds, for the local day. */
export function sunTimes(y, m, d, lat, lon, tz) {
  const noon = new Date(localNoon(y, m, d, tz));
  const doy = Math.round((Date.UTC(noon.getUTCFullYear(), noon.getUTCMonth(), noon.getUTCDate())
    - Date.UTC(noon.getUTCFullYear(), 0, 1)) / 864e5) + 1;
  const [decl, eqt] = solar(doy, noon.getUTCHours() + noon.getUTCMinutes() / 60);
  const midnight = Date.UTC(noon.getUTCFullYear(), noon.getUTCMonth(), noon.getUTCDate());
  const solarNoon = 720 - 4 * lon - eqt;
  const ha = hourAngle(lat, decl, -0.833);
  const out = { sunrise: null, sunset: null };
  if (ha !== null) {
    out.sunrise = midnight + (solarNoon - 4 * ha) * 60e3;
    out.sunset = midnight + (solarNoon + 4 * ha) * 60e3;
  }
  out.length = ha !== null ? (out.sunset - out.sunrise) / 1000 : Math.sin(rad(lat)) * Math.sin(decl) > 0 ? 86400 : 0;
  return out;
}

/** "11 h 09" */
export function fmtDuration(seconds) {
  const m = Number(fx(seconds / 60, 0));
  return `${Math.trunc(m / 60)} h ${String(m % 60).padStart(2, "0")}`;
}

/** +3° / −2° / 0° (never "−0°"). */
export function signed(v, unit = "°") {
  const r = Number(fx(v, 0));
  return r === 0 ? `0${unit}` : `${r > 0 ? "+" : ""}${r}${unit}`.replace("-", "−");
}
