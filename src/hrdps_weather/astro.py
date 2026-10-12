"""Sun and moon: rise/set times, twilight, day length and moon phase (NOAA low-precision formulas).

Good to about a minute for the sun between 60°S and 60°N; no network, no data files.
"""
import math
from datetime import datetime, timedelta, timezone



def _solar(day_of_year, hour_utc):
    g = 2 * math.pi / 365 * (day_of_year - 1 + (hour_utc - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    return decl, eqt                       # radians, minutes


def _hour_angle(lat, decl, altitude_deg):
    """Hour angle (degrees) at which the sun is at `altitude_deg`; None when it never gets there that day."""
    la = math.radians(lat)
    c = (math.sin(math.radians(altitude_deg)) - math.sin(la) * math.sin(decl)) / (math.cos(la) * math.cos(decl))
    if c > 1 or c < -1:
        return None
    return math.degrees(math.acos(c))


def sun_times(day, lat, lon, tz):
    """{'sunrise','sunset','noon','dawn','dusk'} as local datetimes for the local date `day` (None when the sun
    does not rise/set, or civil twilight does not start/end) and 'length' in seconds (0 or 86400 in polar cases)."""
    noon_local = datetime(day.year, day.month, day.day, 12, tzinfo=tz)
    noon_utc = noon_local.astimezone(timezone.utc)
    decl, eqt = _solar(noon_utc.timetuple().tm_yday, noon_utc.hour + noon_utc.minute / 60)
    midnight = datetime(noon_utc.year, noon_utc.month, noon_utc.day, tzinfo=timezone.utc)

    def at(minutes):
        return (midnight + timedelta(minutes=minutes)).astimezone(tz)

    solar_noon = 720 - 4 * lon - eqt
    out = {"noon": at(solar_noon)}
    for name, alt, sign in (("sunrise", -0.833, -1), ("sunset", -0.833, +1), ("dawn", -6.0, -1), ("dusk", -6.0, +1)):
        ha = _hour_angle(lat, decl, alt)
        out[name] = None if ha is None else at(solar_noon + sign * 4 * ha)
    if out["sunrise"] and out["sunset"]:
        out["length"] = (out["sunset"] - out["sunrise"]).total_seconds()
    else:
        out["length"] = 86400.0 if math.sin(math.radians(lat)) * math.sin(decl) > 0 else 0.0
    return out


def julian_day(dt):
    dt = dt.astimezone(timezone.utc)
    y, m = dt.year, dt.month
    d = dt.day + (dt.hour + dt.minute / 60 + dt.second / 3600) / 24
    if m <= 2:
        y, m = y - 1, m + 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def elongation(jd):
    """Moon-sun ecliptic longitude difference in degrees (0 = new moon, 180 = full), truncated Meeus ch. 47
    (error a few tenths of a degree, i.e. under an hour on the moment of a phase)."""
    t = (jd - 2451545.0) / 36525
    r = math.radians
    lp = 218.3164477 + 481267.88123421 * t
    d = r(297.8501921 + 445267.1114034 * t)
    m = r(357.5291092 + 35999.0502909 * t)
    mp = r(134.9633964 + 477198.8675055 * t)
    f = r(93.2720950 + 483202.0175233 * t)
    moon_lon = (lp + 6.288774 * math.sin(mp) + 1.274027 * math.sin(2 * d - mp) + 0.658314 * math.sin(2 * d)
                + 0.213618 * math.sin(2 * mp) - 0.185116 * math.sin(m) - 0.114332 * math.sin(2 * f)
                + 0.058793 * math.sin(2 * d - 2 * mp) + 0.057066 * math.sin(2 * d - m - mp)
                + 0.053322 * math.sin(2 * d + mp) + 0.045758 * math.sin(2 * d - m)
                - 0.040923 * math.sin(m - mp) - 0.034720 * math.sin(d) - 0.030383 * math.sin(m + mp))
    sun_lon = (280.46646 + 36000.76983 * t + (1.914602 - 0.004817 * t) * math.sin(m)
               + 0.019993 * math.sin(2 * m) + 0.000289 * math.sin(3 * m))
    return (moon_lon - sun_lon) % 360


def _next_crossing(jd, target):
    """Julian day (after `jd`) when the elongation next passes `target` degrees (1-hour scan + bisection)."""
    def diff(x):                                   # signed distance to the target, in (-180, 180]
        return (elongation(x) - target + 180) % 360 - 180
    a, step = jd, 1 / 24
    while diff(a) >= 0 or diff(a + step) < 0:      # find a bracket: diff goes from negative to positive
        a += step
        if a - jd > 31:
            return None
    lo, hi = a, a + step
    for _ in range(30):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if diff(mid) < 0 else (lo, mid)
    return (lo + hi) / 2


def _from_jd(jd):
    return datetime(2000, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=jd - 2451545.0)


NAMES = [(22.5, "Nouvelle lune"), (67.5, "Premier croissant"), (112.5, "Premier quartier"),
         (157.5, "Gibbeuse croissante"), (202.5, "Pleine lune"), (247.5, "Gibbeuse décroissante"),
         (292.5, "Dernier quartier"), (337.5, "Dernier croissant"), (361, "Nouvelle lune")]


def moon(dt):
    """{'illumination' 0..1, 'name', 'next_full', 'next_new'} for an aware datetime."""
    jd = julian_day(dt)
    e = elongation(jd)
    return {"elongation": e, "illumination": (1 - math.cos(math.radians(e))) / 2,
            "name": next(n for limit, n in NAMES if e < limit),
            "next_full": _from_jd(_next_crossing(jd, 180)), "next_new": _from_jd(_next_crossing(jd, 0))}


def fmt_duration(seconds):
    m = int(round(seconds / 60))
    return f"{m // 60} h {m % 60:02d}"
