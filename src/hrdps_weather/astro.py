"""Sunrise, sunset and day length (NOAA low-precision formulas).

Good to about a minute between 60°S and 60°N; no network, no data files.
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
    """{'sunrise', 'sunset'} as local datetimes for the local date `day` (None when the sun does not rise/set)
    and 'length' in seconds (0 or 86400 in the polar cases)."""
    noon_local = datetime(day.year, day.month, day.day, 12, tzinfo=tz)
    noon_utc = noon_local.astimezone(timezone.utc)
    decl, eqt = _solar(noon_utc.timetuple().tm_yday, noon_utc.hour + noon_utc.minute / 60)
    midnight = datetime(noon_utc.year, noon_utc.month, noon_utc.day, tzinfo=timezone.utc)

    def at(minutes):
        return (midnight + timedelta(minutes=minutes)).astimezone(tz)

    solar_noon = 720 - 4 * lon - eqt
    out = {}
    for name, sign in (("sunrise", -1), ("sunset", +1)):
        ha = _hour_angle(lat, decl, -0.833)
        out[name] = None if ha is None else at(solar_noon + sign * 4 * ha)
    if out["sunrise"] and out["sunset"]:
        out["length"] = (out["sunset"] - out["sunrise"]).total_seconds()
    else:
        out["length"] = 86400.0 if math.sin(math.radians(lat)) * math.sin(decl) > 0 else 0.0
    return out


def fmt_duration(seconds):
    m = int(round(seconds / 60))
    return f"{m // 60} h {m % 60:02d}"
