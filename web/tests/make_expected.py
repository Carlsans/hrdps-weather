"""Reference output of the Python data layer for the web tests (web/tests/data.test.mjs).

    python web/tests/make_expected.py expected.json

Runs src/hrdps_weather/hrdps.py on the bundled sample run, at a fixed place and time zone, and dumps everything
the web port must reproduce: derived series, per-hour labels and icons, alerts, French date formats, next-run text,
map bounds. The JS test compares its own output to this, so the two implementations cannot drift apart silently.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLACE = {"HRDPS_LAT": "46.8139", "HRDPS_LON": "-71.2080", "HRDPS_LOCATION": "Québec", "HRDPS_TZ": "America/Toronto"}
os.environ.update(PLACE)                                    # before importing: hrdps reads the config at import
os.environ["APPDATA"] = os.environ["XDG_CONFIG_HOME"] = str(Path(__file__).parent / "_no_config")

from hrdps_weather import hrdps  # noqa: E402

SERIES = ["tt", "td", "hr", "re_", "ws", "wd", "gust", "slp", "nt", "uv", "pbl", "cape", "sd", "pop", "snowlvl",
          "tsprob", "fogvis", "rate", "pr", "rain", "swe", "frz", "pel", "snow_cm", "ptype", "pint", "pchar", "utci_l"]


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
        "fmt": [[hrdps.fr(d.local[i], p) for p in ("%a %Hh", "%a %d %b %Hh", "%A %d %B · %Hh", "%a %d", "%Hh%M")]
                for i in rng],
        "fixed": [[f"{d.tt[i]:.0f}", f"{d.tt[i]:.1f}", f"{d.ws[i]:.0f}", f"{d.pr[i]:.2f}", f"{d.slp[i]:.1f}"] for i in rng],
        "next_run": [[t.strftime(hrdps.FMT), hrdps.next_run_text(d.ref, t)] for t in
                     (d.ref + timedelta(hours=h, minutes=m) for h, m in ((0, 0), (5, 1), (8, 55), (9, 9), (9, 11), (12, 0)))],
        "map_bounds": {"region": hrdps.map_bounds(hrdps.REGION_ZOOM), "wide": hrdps.map_bounds(hrdps.WIDE_ZOOM)},
        "sun": [[t.strftime(hrdps.FMT), hrdps.sun_elevation(t)] for t in
                (datetime(2026, m, 21, h, mi, tzinfo=timezone.utc) for m in (3, 6, 12) for h, mi in ((4, 30), (12, 0), (17, 45), (23, 59)))],
        "fill": [hrdps._fill(v) for v in ([None, 1.0, None, None, 4.0, None], [None, None], [2.0, None, 3.0])],
    }
    Path(path).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    hrdps.run_lag = lambda: hrdps.DEFAULT_LAG                 # no observed lags: the default, like a fresh install
    main(sys.argv[1])
