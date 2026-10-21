"""Tides from the Canadian Hydrographic Service (Fisheries and Oceans Canada), IWLS web service.

For the station nearest to the configured place (a permanent station with observations is preferred over a
slightly nearer temporary one): predicted water levels, predicted highs and lows, observed levels, the
surge-aware forecast and the water temperature. Heights are in metres above chart datum.

Source: https://api-iwls.dfo-mpo.gc.ca  (Open Government Licence – Canada)
"""
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from . import __version__, config, hrdps, net

API = "https://api-iwls.dfo-mpo.gc.ca/api/v1"
UA = f"hrdps-weather/{__version__} (+https://github.com/Carlsans/hrdps-weather)"
FMT = "%Y-%m-%dT%H:%M:%SZ"
MAX_KM = 120.0                  # beyond this there is no meaningful tide at the place
OPERATING_BONUS_KM = 15.0       # an operating station with observations may be this much farther than the nearest
PAST, FUTURE = timedelta(hours=24), timedelta(hours=72)
REFRESH_AFTER = 20 * 60         # observations are published every few minutes
STATIONS_MAX_AGE = 30 * 24 * 3600
YEAR = 365 * 24 * 3600          # predictions kept for offline use: a year ahead (~0.7 MB)
YEAR_TOP_UP_BELOW = 335 * 24 * 3600   # fetched again once less than this much is left (about monthly)
YEAR_CHUNK = 30 * 24 * 3600     # the service refuses more than 31 days of 15-minute predictions per request
YEAR_RETRY_AFTER = 3600


def _cache():
    return config.cache_dir() / "tides.json"


def _stations_cache():
    return config.cache_dir() / "tide_stations.json"


def _year_cache():
    return config.cache_dir() / "tides_year.json"


def _json(url, timeout=60):
    raw = net.get(url, UA, timeout=timeout)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _km(lat, lon, s):
    return math.hypot((s["lon"] - lon) * 111.0 * math.cos(math.radians(lat)), (s["lat"] - lat) * 111.0)


def stations():
    """Compact list of every station that has tide predictions (cached a month)."""
    try:
        d = json.loads(_stations_cache().read_text(encoding="utf-8"))
        if time.time() - d["fetched"] < STATIONS_MAX_AGE:
            return d["stations"]
    except (OSError, ValueError, KeyError):
        pass
    raw = _json(f"{API}/stations", 90)
    if raw is None:                                   # unreachable (an empty list is a valid answer)
        return None
    out = []
    for s in raw:
        codes = [t["code"] for t in s.get("timeSeries", [])]
        if "wlp" in codes and "wlp-hilo" in codes:
            out.append({"code": s["code"], "id": s["id"], "name": s["officialName"], "type": s.get("type", ""),
                        "operating": bool(s.get("operating")), "lat": s["latitude"], "lon": s["longitude"],
                        "series": codes})
    try:
        p = _stations_cache()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"fetched": time.time(), "stations": out}), encoding="utf-8")
    except OSError:
        pass
    return out


def choose_station(lat, lon, all_stations, override=None):
    """The station to use: the configured one, else the nearest (preferring an operating one with observations)."""
    if override:
        s = next((s for s in all_stations if s["code"] == str(override)), None)
        return None if s is None else dict(s, km=round(_km(lat, lon, s), 1))
    near = sorted(((_km(lat, lon, s), s) for s in all_stations), key=lambda t: t[0])
    near = [(km, s) for km, s in near if km <= MAX_KM]
    if not near:
        return None
    best_km, best = near[0]
    if not (best["operating"] and "wlo" in best["series"]):
        for km, s in near:
            if s["operating"] and "wlo" in s["series"] and km <= best_km + OPERATING_BONUS_KM:
                best_km, best = km, s
                break
    return dict(best, km=round(best_km, 1))


def _series(station, code, start, end, resolution):
    if code not in station["series"]:
        return None
    url = (f"{API}/stations/{station['id']}/data?time-series-code={code}&from={start.strftime(FMT)}"
           f"&to={end.strftime(FMT)}&resolution={resolution}" if resolution else
           f"{API}/stations/{station['id']}/data?time-series-code={code}&from={start.strftime(FMT)}&to={end.strftime(FMT)}")
    rows = _json(url)
    if not isinstance(rows, list):
        return None
    pts = []
    for r in rows:
        try:
            t = datetime.strptime(r["eventDate"], FMT).replace(tzinfo=timezone.utc).timestamp()
            pts.append((t, float(r["value"])))
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(pts)


def fetch_year(st, now, workers=3):
    """A year of predictions for station `st` from `now`: {"from", "to", "wlp", "hilo"} (epoch seconds), or None when any
    part is missing (a half year would silently run out)."""
    start = datetime.fromtimestamp(now, timezone.utc) - timedelta(days=1)
    end = datetime.fromtimestamp(now + YEAR, timezone.utc)
    hilo = _series(st, "wlp-hilo", start, end, None)
    if hilo is None:
        return None
    spans, a = [], start
    while a < end:
        b = min(a + timedelta(seconds=YEAR_CHUNK), end)
        spans.append((a, b))
        a = b

    def chunk(span):
        for _ in range(2):
            pts = _series(st, "wlp", span[0], span[1], "FIFTEEN_MINUTES")
            if pts is not None:
                return pts
        return None
    with ThreadPoolExecutor(workers) as ex:
        parts = list(ex.map(chunk, spans))
    if any(p is None for p in parts):
        return None
    wlp = sorted({t: v for part in parts for t, v in part}.items())
    return {"from": start.timestamp(), "to": end.timestamp(), "wlp": wlp, "hilo": hilo}


class TideData:
    """Series of (epoch seconds, value) with the lookups the interface needs."""

    def __init__(self, d):
        self.station = d["station"]
        self.fetched = d.get("fetched", 0)
        self.wlp = [tuple(p) for p in d.get("wlp") or []]          # predicted level, 15 min
        self.wlo = [tuple(p) for p in d.get("wlo") or []]          # observed level, 5 min
        self.wlf = [tuple(p) for p in d.get("wlf") or []]          # surge-aware forecast, 15 min
        self.wt = [tuple(p) for p in d.get("wt") or []]            # water temperature, °C
        hilo = [tuple(p) for p in d.get("hilo") or []]
        self.events = self._label(hilo)

    @classmethod
    def from_year(cls, station, year, now):
        """Offline: the predictions around `now` out of the year kept on disk (no observations, no surge forecast)."""
        lo, hi = now - PAST.total_seconds(), now + FUTURE.total_seconds()
        return cls({"station": station, "fetched": 0, "wlp": [p for p in year["wlp"] if lo <= p[0] <= hi],
                    "hilo": [p for p in year["hilo"] if p[0] >= lo]})

    @staticmethod
    def _label(hilo):
        """[(epoch, height, 'H'|'L')]: each extreme compared with its neighbours."""
        out = []
        for i, (t, v) in enumerate(hilo):
            nb = [hilo[j][1] for j in (i - 1, i + 1) if 0 <= j < len(hilo)]
            out.append((t, v, "H" if nb and v > sum(nb) / len(nb) else "L"))
        return out

    @staticmethod
    def at(series, t):
        """Linear interpolation (None outside the series, or across a gap of more than 3 hours)."""
        if len(series) < 2 or t < series[0][0] or t > series[-1][0]:
            return None
        lo, hi = 0, len(series) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if series[mid][0] <= t else (lo, mid)
        (t0, v0), (t1, v1) = series[lo], series[hi]
        if t1 - t0 > 3 * 3600:
            return None
        return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)

    def predicted(self, t):
        return self.at(self.wlp, t)

    def trend(self, t):
        """Predicted rate of change in m/h around t."""
        a, b = self.predicted(t - 900), self.predicted(t + 900)
        return None if a is None or b is None else (b - a) * 2

    def latest_observed(self, t, max_age=45 * 60):
        if self.wlo and 0 <= t - self.wlo[-1][0] <= max_age:
            return self.wlo[-1]
        return None

    def next_events(self, t, n=6):
        return [e for e in self.events if e[0] >= t][:n]

    def water_temperature(self, t, max_age=3 * 3600):
        return self.wt[-1][1] if self.wt and 0 <= t - self.wt[-1][0] <= max_age else None

    def to_json(self):
        return {"station": self.station, "fetched": self.fetched, "wlp": self.wlp, "wlo": self.wlo, "wlf": self.wlf,
                "wt": self.wt, "hilo": [(t, v) for t, v, _ in self.events]}


def trend_text(rate):
    if rate is None:
        return ""
    if abs(rate) < 0.05:
        return "étale"
    return f"{'monte' if rate > 0 else 'baisse'} {abs(rate):.2f} m/h".replace(".", ",")


class Loader:
    """Loads (and keeps fresh) the tide data of the nearest station, in the background."""

    def __init__(self, network=True):
        self.network = network
        self.data = None
        self.state = "idle"                      # idle | loading | none (no station nearby) | error
        self.version = 0
        self._thread = None
        self._year_failed_at = 0
        self.year = None
        try:
            y = json.loads(_year_cache().read_text(encoding="utf-8"))
            if self._same_place(y["station"]) and y["to"] > time.time():
                self.year = y
        except (OSError, ValueError, KeyError):
            pass
        try:
            d = json.loads(_cache().read_text(encoding="utf-8"))
            if self._same_place(d["station"]) and time.time() - d["fetched"] < 12 * 3600:
                self.data = TideData(d)
        except (OSError, ValueError, KeyError):
            pass
        if self.data is None:
            self._from_year()

    def _from_year(self):
        """No fresh download: show the predictions kept for offline use, if there are any for this place."""
        if self.year is not None and self.year["to"] > time.time():
            self.data = TideData.from_year(self.year["station"], self.year, time.time())

    @staticmethod
    def _override():
        try:
            return config.load().get("tide_station")
        except Exception:
            return None

    def _same_place(self, st):
        return abs(st.get("qlat", 0) - hrdps.LAT) < 0.01 and abs(st.get("qlon", 0) - hrdps.LON) < 0.01 \
            and str(st["code"]) == str(self._override() or st["code"])

    def ensure(self):
        """Start a (re)load when there is no data or it is stale; cheap to call on every frame."""
        if not self.network or (self._thread and self._thread.is_alive()):
            return
        if self.state == "none" or (self.state == "error" and time.time() - getattr(self, "_failed_at", 0) < 120):
            return
        if self.data is not None and time.time() - self.data.fetched < REFRESH_AFTER:
            return
        self.state = "loading"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            allst = stations()
            if allst is None:
                raise RuntimeError("no station list")
            st = choose_station(hrdps.LAT, hrdps.LON, allst, self._override())
            if st is None:
                self.state = "none"
                self.version += 1
                return
            st["qlat"], st["qlon"] = hrdps.LAT, hrdps.LON
            now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
            jobs = {"wlp": ("wlp", now - PAST, now + FUTURE, "FIFTEEN_MINUTES"),
                    "hilo": ("wlp-hilo", now - PAST, now + FUTURE, None),
                    "wlo": ("wlo", now - PAST, now, "FIVE_MINUTES"),
                    "wlf": ("wlf-spine", now - PAST, now + FUTURE, "FIFTEEN_MINUTES"),
                    "wt": ("wt1", now - timedelta(hours=6), now, "FIFTEEN_MINUTES")}
            got = dict(self.data.to_json()) if self.data and self.data.station["code"] == st["code"] else {}
            got.update({"station": st, "fetched": time.time()})
            fresh = False
            with ThreadPoolExecutor(5) as ex:
                futs = {ex.submit(_series, st, *args): key for key, args in jobs.items()}
                for fut in as_completed(futs):
                    key, pts = futs[fut], fut.result()
                    if pts is not None:
                        got[key] = pts
                        fresh = True
                        if "wlp" in got and "hilo" in got:        # usable as soon as the predictions are in
                            self.data = TideData(got)
                            self.version += 1
            if not fresh or not got.get("wlp"):                           # nothing new arrived: not a refresh
                raise RuntimeError("no predictions")
            self.data = TideData(got)
            p = _cache()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self.data.to_json()), encoding="utf-8")
            self.state = "idle"
            self._keep_a_year(st)
        except Exception:
            self.state = "error"
            self._failed_at = time.time()
            self._from_year()
        self.version += 1

    def _keep_a_year(self, st):
        """Once in a while (about monthly) store a year of predictions, so that the tides also work offline."""
        now = time.time()
        y = self.year
        if y is not None and y["station"]["code"] == st["code"] and y["to"] - now > YEAR_TOP_UP_BELOW:
            return
        if now - self._year_failed_at < YEAR_RETRY_AFTER:
            return
        try:
            got = fetch_year(st, now)
            if got is None:
                raise RuntimeError("incomplete year")
            got["station"] = st
            p = _year_cache()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(got, separators=(",", ":")), encoding="utf-8")
            self.year = got
        except Exception:
            self._year_failed_at = now
