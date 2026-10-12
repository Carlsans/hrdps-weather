"""Climate normals (Environment Canada, 1981–2010) for the station nearest to the configured place.

Source: the MSC GeoMet OGC API (api.weather.gc.ca, collections climate-stations / climate-normals).
Fetched once in the background and kept for two months in the cache: normals do not change.
"""
import json
import math
import threading
import time
from collections import defaultdict
from datetime import date

from . import __version__, config, hrdps, net

API = "https://api.weather.gc.ca/collections"
UA = f"hrdps-weather/{__version__} (+https://github.com/Carlsans/hrdps-weather)"
MAX_AGE = 60 * 24 * 3600
PERIOD = "1981–2010"

# key -> element name in the API (months 1..12 are the monthly normals).
ELEMENTS = {
    "tmean": "Mean daily temperature deg C", "tmax": "Mean daily max temperature deg C",
    "tmin": "Mean daily min temperature deg C",
    "precip": "Total precipitation mm", "snow": "Total snowfall cm",
    "pdays": "Days with precipitation GE 0.2 mm",
    "sun": "Total hours bright sunshine", "wind": "Mean of hourly wind speed kmh",
}
_BY_NAME = {v: k for k, v in ELEMENTS.items()}
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre"]


def _path():
    return config.cache_dir() / "normals.json"


def _items(url):
    raw = net.get(url, UA, timeout=60)
    try:
        return json.loads(raw)["features"] if raw else None
    except (ValueError, KeyError):
        return None


def _km(lat, lon, c):
    return math.hypot((c[0] - lon) * 111.0 * math.cos(math.radians(lat)), (c[1] - lat) * 111.0)


def nearest_stations(lat, lon):
    """[(km, station id, name)] of stations with normals, nearest first (search radius grows if none are close)."""
    cands = []
    for half in (0.6, 1.2, 2.4):
        feats = _items(f"{API}/climate-stations/items?f=json&limit=300&HAS_NORMALS_DATA=Y"
                       f"&bbox={lon - half * 1.5},{lat - half},{lon + half * 1.5},{lat + half}")
        if feats is None:
            return []
        cands = sorted((_km(lat, lon, f["geometry"]["coordinates"]), f["properties"]["STN_ID"],
                        f["properties"]["STATION_NAME"]) for f in feats if f.get("geometry"))
        if len(cands) >= 3:
            break
    return cands[:6]


def build(lat, lon):
    """Download the normals; None when the service cannot be reached or no station has data."""
    cands = nearest_stations(lat, lon)
    if not cands:
        return None
    out = {"lat": lat, "lon": lon, "period": PERIOD, "monthly": {}, "src": {}, "fetched": time.time()}
    for km, stn, name in cands:
        feats = _items(f"{API}/climate-normals/items?f=json&limit=2000&STN_ID={stn}")
        if not feats:
            continue
        by = defaultdict(dict)
        for f in feats:
            p = f["properties"]
            key = _BY_NAME.get(p["E_NORMAL_ELEMENT_NAME"])
            if key and p["VALUE"] is not None:
                by[key].setdefault(p["MONTH"], p["VALUE"])
        for key, months in by.items():
            if key not in out["monthly"] and all(m in months for m in range(1, 13)):
                out["monthly"][key] = [months[m] for m in range(1, 13)]
                out["src"][key] = [stn, name, round(km, 1)]
        if all(k in out["monthly"] for k in ELEMENTS):
            break                                            # complete: no need to ask further stations
    return out if "tmean" in out["monthly"] else None


def load_cached(lat, lon):
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        if abs(d["lat"] - lat) < 0.01 and abs(d["lon"] - lon) < 0.01 and time.time() - d["fetched"] < MAX_AGE:
            return d
    except (OSError, ValueError, KeyError):
        pass
    return None


class Normals:
    """Monthly normals with the lookups the interface needs."""

    def __init__(self, d):
        self.d = d

    def has(self, key):
        return key in self.d["monthly"]

    def month(self, key, month):
        return self.d["monthly"][key][month - 1]

    def daily(self, key, day):
        """Normal for a date: the monthly normals (centred on the 15th) interpolated linearly."""
        vals = self.d["monthly"][key]
        dim = (date(day.year + (day.month == 12), day.month % 12 + 1, 1) - date(day.year, day.month, 1)).days
        pos = day.month - 1 + (day.day - 15) / dim
        i0 = math.floor(pos)
        f = pos - i0
        return vals[i0 % 12] * (1 - f) + vals[(i0 + 1) % 12] * f

    def station(self, key="tmean"):
        s = self.d["src"].get(key)
        return {"id": s[0], "name": s[1], "km": s[2]} if s else None

    @property
    def period(self):
        return self.d.get("period", PERIOD)


def pretty_station(name):
    return name.title().replace("Intl", "Int.").replace("/", " / ") if name else ""


class Loader:
    """Loads the normals in the background (cache first, then the network if it is allowed)."""

    def __init__(self, network=True):
        self.network = network
        self.normals = None
        self.state = "idle"                    # idle | loading | missing
        self._thread = None
        self.normals = self._cached()

    def _cached(self):
        d = load_cached(hrdps.LAT, hrdps.LON)
        return Normals(d) if d else None

    def start(self):
        if self.normals is not None or not self.network or (self._thread and self._thread.is_alive()):
            return
        self.state = "loading"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            d = build(hrdps.LAT, hrdps.LON)
            if d:
                p = _path()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(d), encoding="utf-8")
                self.normals = Normals(d)
                self.state = "idle"
            else:
                self.state = "missing"
        except Exception:
            self.state = "missing"
