"""Reference output of the Python data layer for the web tests (web/tests/data.test.mjs).

    python web/tests/make_expected.py expected.json

Runs src/hrdps_weather/hrdps.py on the bundled sample run, at a fixed place and time zone, and dumps everything
the web port must reproduce: derived series, per-hour labels and icons, alerts, French date formats, next-run text,
map bounds. The JS test compares its own output to this, so the two implementations cannot drift apart silently.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PLACE = {"HRDPS_LAT": "46.8139", "HRDPS_LON": "-71.2080", "HRDPS_LOCATION": "Québec", "HRDPS_TZ": "America/Toronto"}
os.environ.update(PLACE)                                    # before importing: hrdps reads the config at import
os.environ["APPDATA"] = os.environ["XDG_CONFIG_HOME"] = str(Path(__file__).parent / "_no_config")

from hrdps_weather import hrdps  # noqa: E402

SERIES = ["tt", "td", "hr", "re_", "ws", "wd", "gust", "slp", "nt", "uv", "pbl", "cape", "sd", "pop", "snowlvl",
          "tsprob", "fogvis", "rate", "pr", "rain", "swe", "frz", "pel", "snow_cm", "ptype", "pint", "pchar", "utci_l"]


def almanac():
    """Sunrise/sunset for a few places and dates, climate-normal interpolation, number formatting."""
    from zoneinfo import ZoneInfo

    from hrdps_weather import astro, climate, view
    out = {"sun": [], "signed": [[v, view.signed(v), view.signed(v, " min")] for v in (-0.4, -0.5, 0.5, 2.6, -2.6, 0.0, 14.49, -14.51)],
           "duration": [[s, astro.fmt_duration(s)] for s in (0, 59, 3600, 40_140, 86_400, 11 * 3600 + 1800)]}
    for lat, lon, tzname in ((46.8139, -71.208, "America/Toronto"), (45.5, -73.6, "America/Toronto"), (49.28, -123.12, "America/Vancouver"),
                             (53.5, -113.5, "America/Edmonton"), (80.0, -71.2, "America/Toronto"), (64.0, -139.4, "America/Whitehorse")):
        for day in ((2026, 3, 20), (2026, 6, 21), (2026, 10, 12), (2026, 11, 1), (2026, 3, 8), (2026, 12, 21)):
            if tzname not in ("America/Toronto", "America/Whitehorse") and day[1] >= 11:
                continue        # newer tz databases give the western provinces permanent daylight time, older ones still change
            t = astro.sun_times(date(*day), lat, lon, ZoneInfo(tzname))
            out["sun"].append({"lat": lat, "lon": lon, "tz": tzname, "day": day,
                               "sunrise": None if t["sunrise"] is None else t["sunrise"].timestamp() * 1000,
                               "sunset": None if t["sunset"] is None else t["sunset"].timestamp() * 1000, "length": t["length"]})
    temp = [-12.8, -11.0, -5.0, 2.5, 10.0, 15.5, 18.5, 17.5, 12.5, 6.5, 0.5, -8.5]
    nm = climate.Normals({"lat": 0, "lon": 0, "period": "1981–2010", "monthly": {"tmean": temp}, "src": {"tmean": [1, "X", 2.0]}})
    out["normals"] = {"monthly": temp, "daily": [[y, m, d, nm.daily("tmean", date(y, m, d))]
                                                 for y, m, d in ((2026, 1, 1), (2026, 1, 15), (2026, 2, 28), (2026, 10, 12), (2026, 10, 31),
                                                                 (2026, 12, 31), (2028, 2, 29), (2026, 6, 16))]}
    out["pretty"] = [[n, climate.pretty_station(n)] for n in ("QUEBEC/JEAN LESAGE INTL A", "BEAUSEJOUR", "ST FLAVIEN", "L'ANCIENNE-LORETTE", "JEAN'S POINT 2")]
    return out


def tides_reference():
    """A synthetic tide (sine + irregular highs/lows): the series maths and labels the JS port must reproduce."""
    import math

    from hrdps_weather import tides, view
    t0 = 1_791_000_000.0
    wlp = [(t0 + k * 900, 2.5 + 2.5 * math.sin(2 * math.pi * (k * 900 / 3600) / 12.42)) for k in range(400)]
    wlo = [(t0 + k * 300, 2.4 + 0.1 * math.sin(k / 7)) for k in range(120)]
    hilo = [(t0 + (n * 0.5 + 0.25) * 12.42 * 3600, [5.0, 0.3, 5.3, 0.1, 4.9, 0.4][n % 6]) for n in range(10)]
    st = {"code": "B", "id": "x", "name": "Test", "type": "PERMANENT", "operating": True, "lat": 46.8, "lon": -71.2, "km": 6.6, "series": ["wlo", "wlp", "wlp-hilo"]}
    td = tides.TideData({"station": st, "fetched": 0, "wlp": wlp, "wlo": wlo, "hilo": hilo, "wt": [(t0 + 100, 12.4)]})
    probes = [t0 + x for x in (0, 450, 3600 * 3.3, 3600 * 40, 3600 * 99, -10, 3600 * 30 + 77)]
    stations = [{"code": "A", "id": "a", "name": "A", "type": "TEMPORARY", "operating": False, "lat": 46.76, "lon": -71.24, "series": ["wlp", "wlp-hilo"]},
                {"code": "B", "id": "b", "name": "B", "type": "PERMANENT", "operating": True, "lat": 46.81, "lon": -71.20, "series": ["wlo", "wlp", "wlp-hilo"]},
                {"code": "C", "id": "c", "name": "C", "type": "PERMANENT", "operating": True, "lat": 47.9, "lon": -69.5, "series": ["wlo", "wlp", "wlp-hilo"]}]
    choose = lambda lat, lon, ov=None: (lambda s: None if s is None else [s["code"], s["km"]])(tides.choose_station(lat, lon, stations, ov))
    return {"t0": t0, "wlp": wlp, "wlo": wlo, "hilo": hilo, "wt": [(t0 + 100, 12.4)], "probes": probes,
            "events": [[t, v, k] for t, v, k in td.events], "predicted": [td.predicted(t) for t in probes],
            "trend": [td.trend(t) for t in probes], "observed": [td.latest_observed(t) for t in probes],
            "next": [[list(e) for e in td.next_events(t, 3)] for t in probes], "temp": [td.water_temperature(t) for t in probes],
            "trend_text": [tides.trend_text(r) for r in (0.85, -0.2, 0.01, None, 0.049, -0.05)],
            "feet": [[v, f"{v * view.FEET_PER_M:.1f}"] for v in (2.57, 5.29, 0.36, 0.0, 12.345)],
            "stations": stations, "choose": [choose(46.758, -71.24), choose(46.758, -71.24, "A"), choose(46.758, -71.24, "Z"), choose(10.0, -60.0), choose(47.8, -69.6)]}


def main(path):
    raw = json.loads((Path(hrdps.__file__).parent / "data" / "sample_series.json").read_text(encoding="utf-8"))
    d = hrdps.Data(raw)
    rng = range(d.n)
    out = {
        "place": {"latitude": hrdps.LAT, "longitude": hrdps.LON, "timezone": str(hrdps.TZ)},
        "series": {k: getattr(d, k) for k in SERIES},
        "kind": [d.kind(i) for i in rng],
        "icon": [d.icon(i) for i in rng],
        "describe": [d.describe(i) for i in rng],
        "is_day": [d.is_day(i) for i in rng],
        "alerts": [d.alerts(i) for i in rng],
        "alerts12": [d.alerts(i, 12) for i in rng],                # the large Android widget's window
        "fmt": [[hrdps.fr(d.local[i], p) for p in ("%a %Hh", "%a %d %b %Hh", "%A %d %B · %Hh", "%a %d", "%Hh%M")]
                for i in rng],
        "fixed": [[f"{d.tt[i]:.0f}", f"{d.tt[i]:.1f}", f"{d.ws[i]:.0f}", f"{d.pr[i]:.2f}", f"{d.slp[i]:.1f}"] for i in rng],
        "next_run": [[t.strftime(hrdps.FMT), hrdps.next_run_text(d.ref, t)] for t in
                     (d.ref + timedelta(hours=h, minutes=m) for h, m in ((0, 0), (5, 1), (8, 55), (9, 9), (9, 11), (12, 0)))],
        "map_bounds": {"region": hrdps.map_bounds(hrdps.REGION_ZOOM), "wide": hrdps.map_bounds(hrdps.WIDE_ZOOM)},
        "sun": [[t.strftime(hrdps.FMT), hrdps.sun_elevation(t)] for t in
                (datetime(2026, m, 21, h, mi, tzinfo=timezone.utc) for m in (3, 6, 12) for h, mi in ((4, 30), (12, 0), (17, 45), (23, 59)))],
        "fill": [hrdps._fill(v) for v in ([None, 1.0, None, None, 4.0, None], [None, None], [2.0, None, 3.0])],
        "almanac": almanac(),
        "tides": tides_reference(),
    }
    Path(path).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    hrdps.run_lag = lambda: hrdps.DEFAULT_LAG                 # no observed lags: the default, like a fresh install
    main(sys.argv[1])
