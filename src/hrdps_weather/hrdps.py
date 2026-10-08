"""
HRDPS (ECCC, 2.5 km) data layer for the waybar weather module.

Everything comes from MSC GeoMet (WMS GetFeatureInfo for the point series,
WCS GetCoverage for map grids). No other weather source is used.

  load()            -> Data | None   read the cache (never blocks on network)
  refresh(force)    -> None          download a full run (series + map grids)
  ensure_fresh()    -> None          spawn a detached refresh if a new run may exist

Cache: ~/.cache/waybar-weather/
"""
import json, math, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__, config, net

# ── Configuration (see config.py: config.toml in the user config directory) ──
_cfg     = config.load()
LAT, LON = _cfg["latitude"], _cfg["longitude"]
LOCATION = _cfg["location"]
TZ       = ZoneInfo(_cfg["timezone"])
# ─────────────────────────────────────────────────────────────────────────────

GEOMET   = "https://geo.weather.gc.ca/geomet"
UA       = f"hrdps-weather/{__version__} (+https://github.com/Carlsans/hrdps-weather)"
CACHE    = config.cache_dir()
HOURS    = 48
FMT      = "%Y-%m-%dT%H:%M:%SZ"
CHECK_EVERY = 600            # s between "is there a new run?" checks
REGION_ZOOM, WIDE_ZOOM, MAP_TILES = 8, 6, 3   # data grids cover a 3x3 tile block: z8 (~±2°) and z6 (~±8°)
WIDE_CELLS = 120                              # the wide grid is coarse (≈0.14°): it only serves zoomed-out views
MAPS_FORMAT = 2

C = "HRDPS.CONTINENTAL_"
W = "HRDPS-WEonG_2.5km_"
# key -> GeoMet layer. acc_* are cumulative since the start of the run.
POINT_LAYERS = {
    "tt": C+"TT",   "td": C+"TD",   "hr": C+"HR",  "re": C+"RE",  "hmx": C+"HMX", "utci": C+"UTCI",
    "ws": C+"WSPD", "wd": C+"WD",   "wgx": C+"WGX", "slp": C+"PN-SLP",
    "nt": C+"NT",   "rt": C+"RT",
    "acc_pr": C+"PR", "acc_rn": C+"RN", "acc_sn": C+"SN", "acc_fr": C+"FR", "acc_pe": C+"PE",
    "uv": C+"IUVA", "sd": C+"SD", "pbl": C+"HPBL", "cape": C+"BE",
    "ptype": W+"DominantPrecipType", "pop": W+"Precip-Prob", "snowlvl": W+"SnowLevelHeight",
    "tsprob": W+"Thunderstorm-Prob", "pint": W+"TotalPrecipIntensityIndex",
    "pchar": W+"PrecipCharacter", "fogvis": W+"LiquidFogVisibility",
}
ACC_KEYS = [k for k in POINT_LAYERS if k.startswith("acc_")]
# these layers return no feature (transparent) when the value is nil
EMPTY_IS_ZERO = set(ACC_KEYS) | {"sd", "cape", "rt", "tsprob"}

MAP_LAYERS = {"rt": C+"RT", "tt": C+"TT", "ws": C+"WSPD", "wd": C+"WD", "nt": C+"NT"}

# ── HTTP ─────────────────────────────────────────────────────────────────────
def _get(url, timeout=30, tries=2):
    return net.get(url, UA, timeout, tries)

def _feature(layer, t):
    """-> (value, class) | 'empty' | None (network error)"""
    d = 0.01
    url = (f"{GEOMET}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo&LAYERS={layer}&QUERY_LAYERS={layer}"
           f"&CRS=EPSG:4326&BBOX={LAT-d},{LON-d},{LAT+d},{LON+d}&WIDTH=101&HEIGHT=101&I=50&J=50"
           f"&INFO_FORMAT=application/json&FORMAT=image/png&STYLES=&TIME={t.strftime(FMT)}")
    raw = _get(url)
    if raw is None:
        return None
    try:
        feats = json.loads(raw).get("features") or []
    except Exception:
        return None
    if not feats:
        return "empty"
    p = feats[0]["properties"]
    return p.get("value"), p.get("class")

def latest_ref():
    """Reference time (UTC) of the newest HRDPS run listed by GeoMet."""
    raw = _get(f"{GEOMET}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities&LAYER={C}TT", timeout=40)
    if not raw:
        return None
    s = raw.decode("utf-8", "ignore")
    i = s.find(f"<Name>{C}TT</Name>")
    m = re.search(r'name="reference_time"[^>]*>([^<]*)<', s[i:i + 6000]) if i >= 0 else None
    if not m:
        return None
    parts = m.group(1).split("/")
    last = parts[1] if len(parts) >= 2 else parts[0].split(",")[-1]
    return datetime.strptime(last, FMT).replace(tzinfo=timezone.utc)

# ── Point series ─────────────────────────────────────────────────────────────
def _fetch_series(ref):
    times = [ref + timedelta(hours=i) for i in range(HOURS + 1)]
    jobs = [(k, i) for k in POINT_LAYERS for i in range(len(times))]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda j: _feature(POINT_LAYERS[j[0]], times[j[1]]), jobs))
    # A failed request (network blip, 5xx) comes back as None; retry those cells before giving up on them.
    # Cells that stay None are interpolated by Data (see _fill). Some are legitimately None (e.g. the first
    # hour of the WEonG layers, which start one hour after the run), so the retry is cheap and bounded.
    for _ in range(2):
        todo = [n for n, r in enumerate(res) if r is None]
        if not todo:
            break
        time.sleep(2)
        with ThreadPoolExecutor(6) as ex:
            again = list(ex.map(lambda n: _feature(POINT_LAYERS[jobs[n][0]], times[jobs[n][1]]), todo))
        for n, r in zip(todo, again):
            res[n] = r
    series = {k: [None] * len(times) for k in POINT_LAYERS}
    labels = {k: [None] * len(times) for k in POINT_LAYERS}
    for (k, i), r in zip(jobs, res):
        if r == "empty":
            series[k][i] = 0.0 if k in EMPTY_IS_ZERO else None
        elif r:
            series[k][i], labels[k][i] = r
    return {"ref": ref.strftime(FMT), "times": [t.strftime(FMT) for t in times],
            "series": series, "labels": labels}

# ── Map grids (WCS) ──────────────────────────────────────────────────────────
def tile_xy(z=REGION_ZOOM):
    n = 2 ** z
    return (int((LON + 180) / 360 * n),
            int((1 - math.asinh(math.tan(math.radians(LAT))) / math.pi) / 2 * n))

def _tile_lonlat(x, y, z):
    n = 2 ** z
    return x / n * 360 - 180, math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))

def map_bounds(z=REGION_ZOOM):
    """(lon_w, lat_s, lon_e, lat_n) of the 3x3 tile block around the point at zoom z."""
    x, y = tile_xy(z); h = MAP_TILES // 2
    w, n = _tile_lonlat(x - h, y - h, z)
    e, s = _tile_lonlat(x + h + 1, y + h + 1, z)
    return w, s, e, n

def _fetch_grid(layer, t, bounds, cells=None):
    """One hourly grid (rows north→south). `cells` = number of columns to resample to (None: native-ish)."""
    import numpy as np
    w, s, e, n = bounds
    scale = f"&SCALESIZE=x({cells}),y({round(cells * (n - s) / (e - w))})" if cells else ""
    url = (f"{GEOMET}?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID={layer}&FORMAT=image/x-aaigrid"
           f"&SUBSETTINGCRS=EPSG:4326&SUBSET=x({w},{e})&SUBSET=y({s},{n}){scale}&TIME={t.strftime(FMT)}")
    raw = _get(url, timeout=60)
    if not raw or b"ncols" not in raw:
        return None
    lines = raw[raw.index(b"ncols"):].split(b"\n")
    hdr, k = {}, 0
    while k < len(lines) and lines[k][:1].isalpha():
        a = lines[k].split()
        hdr[a[0].lower()] = float(a[1]); k += 1
    rows = []
    for ln in lines[k:]:
        if ln.startswith(b"--") or not ln.strip():
            break
        rows.append(np.array(ln.split(), dtype=np.float32))
    arr = np.array(rows, dtype=np.float32)
    nd = hdr.get(b"nodata_value")
    if nd is not None:
        arr[arr == nd] = np.nan
    arr[arr < -1e20] = np.nan
    return arr

def _fetch_maps(ref):
    """{key: ndarray[T, ny, nx]} for the region grids ('rt'…) and the coarse wide grids ('w_rt'…); None if the
    region failed entirely. A wide-grid failure only costs the zoomed-out view."""
    import numpy as np
    times = [ref + timedelta(hours=i) for i in range(HOURS + 1)]
    sets = {"": (map_bounds(REGION_ZOOM), None), "w_": (map_bounds(WIDE_ZOOM), WIDE_CELLS)}
    jobs = [(p, k, i) for p in sets for k in MAP_LAYERS for i in range(len(times))]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda j: _fetch_grid(MAP_LAYERS[j[1]], times[j[2]], *sets[j[0]]), jobs))
    out = {}
    for p in sets:
        shape = next((r.shape for (pp, _, _), r in zip(jobs, res) if pp == p and r is not None), None)
        if shape is None:
            if p == "":
                return None
            continue
        for k in MAP_LAYERS:
            out[p + k] = np.full((len(times),) + shape, np.nan, np.float32)
        for (pp, k, i), r in zip(jobs, res):
            if pp == p and r is not None and r.shape == shape:
                out[p + k][i] = r
    return out

# ── Cache ────────────────────────────────────────────────────────────────────
def _series_path(): return CACHE / "series.json"
def _maps_path():   return CACHE / "maps.npz"
def _state_path():  return CACHE / "state.json"

def _state():
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_state(**kw):
    st = _state(); st.update(kw)
    CACHE.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(st), encoding="utf-8")

class _Lock:
    """Non-blocking inter-process lock (fcntl on POSIX, msvcrt on Windows)."""
    def __init__(self, path):
        self.path, self.f = path, None

    def acquire(self):
        CACHE.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.f.close(); self.f = None
            return False

    def release(self):
        if self.f is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0); msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f, fcntl.LOCK_UN)
        finally:
            self.f.close(); self.f = None


def refresh(force=False, maps=True):
    """Download the latest run: series first (fast), then map grids."""
    lock = _Lock(CACHE / "refresh.lock")
    if not lock.acquire():
        return                                # another refresh is running
    try:
        ref = latest_ref()
        if ref is None:
            return
        st = _state()
        have_series = st.get("ref") == ref.strftime(FMT) and _series_path().exists()
        have_maps   = (st.get("maps_ref") == ref.strftime(FMT) and st.get("maps_fmt") == MAPS_FORMAT
                       and _maps_path().exists())
        if not force and have_series and (have_maps or not maps):
            _save_state(checked=time.time()); return
        if force or not have_series:
            data = _fetch_series(ref)
            if sum(v is not None for v in data["series"]["tt"]) < 10:
                return                        # run still being published: keep old cache, retry later
            tmp = _series_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8"); tmp.replace(_series_path())
            lags = list(st.get("lags", []))
            if st.get("ref") and st.get("ref") != data["ref"]:       # a *new* run was just picked up
                lag = time.time() - ref.timestamp()
                if MIN_LAG <= lag <= MAX_LAG:                        # ignore PC-was-off outliers
                    lags = (lags + [lag])[-8:]
            _save_state(ref=data["ref"], checked=time.time(), lags=lags)
        if maps:
            g = _fetch_maps(ref)
            if g:
                import numpy as np
                tmp = CACHE / "maps.tmp.npz"
                np.savez_compressed(tmp, bounds=np.array(map_bounds(REGION_ZOOM)), wide_bounds=np.array(map_bounds(WIDE_ZOOM)),
                                    ref=np.array(ref.strftime(FMT)), **g)
                tmp.replace(_maps_path())
                _save_state(maps_ref=ref.strftime(FMT), maps_fmt=MAPS_FORMAT)
    finally:
        lock.release()

def ensure_fresh(force=False):
    """Spawn a detached refresh when the cache is missing or a new run may exist."""
    if os.environ.get("HRDPS_NO_REFRESH"):                  # CI / smoke tests: stay offline
        return
    st = _state()
    stale = (not _series_path().exists()) or (time.time() - st.get("checked", 0) > CHECK_EVERY)
    if not (force or stale):
        return
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x08000000      # DETACHED_PROCESS | CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    cmd = [sys.executable, "refresh"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "hrdps_weather", "refresh"]
    subprocess.Popen(cmd, **kw)

def refresh_running():
    lock = _Lock(CACHE / "refresh.lock")
    if lock.acquire():
        lock.release()
        return False
    return True

# ── Next model run ───────────────────────────────────────────────────────────
RUN_STEP = timedelta(hours=6)              # HRDPS runs at 00, 06, 12 and 18 UTC
DEFAULT_LAG = 3 * 3600 + 10 * 60           # observed: a run is complete on GeoMet ≈ 3 h after its cycle time
MIN_LAG, MAX_LAG = 2.5 * 3600, 4.5 * 3600
_lag_cache = (0.0, DEFAULT_LAG)

def run_lag():
    """Typical delay (s) between a run's cycle time and its availability: median of the lags this installation
    observed (kept in the state file), else the default."""
    global _lag_cache
    if time.time() - _lag_cache[0] > 30:
        lags = sorted(_state().get("lags", []))
        _lag_cache = (time.time(), lags[len(lags) // 2] if lags else DEFAULT_LAG)
    return _lag_cache[1]

def next_run(ref, now=None, lag=None):
    """-> (next_cycle_utc, eta_utc, seconds_left) for the run following `ref`; seconds_left <= 0 means it is due
    (publication in progress or about to be picked up)."""
    now = now or datetime.now(timezone.utc)
    lag = run_lag() if lag is None else lag
    nxt = ref + RUN_STEP
    eta = nxt + timedelta(seconds=lag)
    return nxt, eta, (eta - now).total_seconds()

def next_run_text(ref, now=None):
    """One line for the UI, e.g. 'Prochain run 12Z : dans ~1 h 25 (vers 11h10)'."""
    nxt, eta, left = next_run(ref, now)
    cycle = nxt.strftime("%HZ")
    if left <= 0:
        return f"Run {cycle} attendu : publication en cours…"
    mins = int(left // 60) + 1
    span = f"{mins // 60} h {mins % 60:02d}" if mins >= 60 else f"{mins} min"
    return f"Prochain run {cycle} : dans ~{span} (vers {fr(eta.astimezone(TZ), '%Hh%M')})"

# ── Data access ──────────────────────────────────────────────────────────────
# English class labels from GeoMet → French
PTYPE_FR = {"None": "Aucune", "Rain": "Pluie", "Snow": "Neige", "Freezing rain": "Pluie verglaçante",
            "Ice pellets": "Grésil", "Drizzle": "Bruine", "Freezing drizzle": "Bruine verglaçante",
            "Wet snow": "Neige mouillée", "Rain/snow": "Pluie et neige", "Snow grains": "Neige en grains"}
PINT_FR = {"No Intensity": "", "Low Intensity": "Faible", "Light Intensity": "Faible", "Moderate Intensity": "Modérée", "Heavy Intensity": "Forte",
           "Very Heavy Intensity": "Très forte", "No precipitation": "Aucune"}
PCHAR_FR = {"Showers": "Averses", "Continuous": "Continue", "Intermittent": "Intermittente",
            "Flurries": "Giboulées de neige", "Squalls": "Bourrasques", "None": ""}
UTCI_FR = {"Extreme cold stress": "Froid extrême", "Very strong cold stress": "Froid très fort",
           "Strong cold stress": "Froid fort", "Moderate cold stress": "Froid modéré",
           "Slight cold stress": "Froid léger", "No thermal stress": "Confortable",
           "Moderate heat stress": "Chaleur modérée", "Strong heat stress": "Chaleur forte",
           "Very strong heat stress": "Chaleur très forte", "Extreme heat stress": "Chaleur extrême"}

_DAYS   = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"]
_MON_AB = ["janv", "févr", "mars", "avr", "mai", "juin", "juil", "août", "sept", "oct", "nov", "déc"]

def fr(dt, pattern):
    """strftime with French day/month names (independent of the process locale; GTK resets it)."""
    d, mi = _DAYS[dt.weekday()], dt.month - 1
    pattern = pattern.replace("%A", d).replace("%a", d[:3]).replace("%B", _MONTHS[mi]).replace("%b", _MON_AB[mi])
    return dt.strftime(pattern)

def sun_elevation(utc):
    """Approximate solar elevation (degrees) at the configured point (NOAA low-precision formula)."""
    d = utc.timetuple().tm_yday + (utc.hour + utc.minute / 60) / 24
    g = 2 * math.pi / 365 * (d - 1)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g))
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    ha = math.radians(((utc.hour * 60 + utc.minute + eqt + 4 * LON) / 4) - 180)
    la = math.radians(LAT)
    return math.degrees(math.asin(math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)))

def _fill(vals):
    """Fill gaps of a continuous series: linear interpolation inside, nearest value at the edges."""
    known = [i for i, v in enumerate(vals) if v is not None]
    if not known or len(known) == len(vals):
        return vals
    out = list(vals)
    for i, v in enumerate(vals):
        if v is not None:
            continue
        lo = max((k for k in known if k < i), default=None)
        hi = min((k for k in known if k > i), default=None)
        if lo is None:
            out[i] = vals[hi]
        elif hi is None:
            out[i] = vals[lo]
        else:
            out[i] = vals[lo] + (vals[hi] - vals[lo]) * (i - lo) / (hi - lo)
    return out


class Data:
    """Hourly series at the configured point; index 0 = run start (UTC)."""
    def __init__(self, raw):
        self.raw   = raw
        self.ref   = datetime.strptime(raw["ref"], FMT).replace(tzinfo=timezone.utc)
        self.utc   = [datetime.strptime(t, FMT).replace(tzinfo=timezone.utc) for t in raw["times"]]
        self.local = [t.astimezone(TZ) for t in self.utc]
        self.n     = len(self.utc)
        s, self.lab = raw["series"], raw["labels"]
        f = lambda k, d=None: [v if v is not None else d for v in s[k]]
        kmh = lambda k: [v * 3.6 if v is not None else None for v in s[k]]
        c = lambda k: _fill(f(k))                       # continuous series: gaps are interpolated
        self.tt, self.td, self.hr, self.re_ = c("tt"), c("td"), c("hr"), c("re")
        self.hmx, self.utci = f("hmx"), f("utci")
        self.ws, self.wd = _fill(kmh("ws")), c("wd")                           # km/h, °
        # GeoMet leaves the gust layer empty when gusts do not exceed the mean wind
        self.gust = [max(g, w) if g is not None and w is not None else w for g, w in zip(kmh("wgx"), self.ws)]
        self.slp  = _fill([v / 100 if v is not None else None for v in s["slp"]])   # hPa
        self.nt, self.uv, self.pbl, self.cape = c("nt"), f("uv", 0), c("pbl"), f("cape", 0)
        self.sd   = [v * 100 if v is not None else 0 for v in s["sd"]]         # cm
        self.pop, self.snowlvl, self.tsprob = f("pop", 0), f("snowlvl"), f("tsprob", 0)
        self.fogvis = f("fogvis")
        self.rate = [v * 3600 if v is not None else 0 for v in s["rt"]]        # mm/h instantaneous
        def diff(k):                                                           # hourly amount from cumulative
            a = [v if v is not None else 0.0 for v in s[k]]
            return [0.0] + [max(a[i] - a[i - 1], 0.0) for i in range(1, len(a))]
        self.pr, self.rain, self.swe, self.frz, self.pel = (diff("acc_pr"), diff("acc_rn"), diff("acc_sn"),
                                                            diff("acc_fr"), diff("acc_pe"))
        self.snow_cm = list(self.swe)           # ≈10:1 snow-to-liquid ratio → 1 mm SWE ≈ 1 cm
        self.ptype  = [PTYPE_FR.get(x, x) if x else "Aucune" for x in self.lab["ptype"]]
        self.pint   = [PINT_FR.get(x, x) if x else "" for x in self.lab["pint"]]
        self.pchar  = [PCHAR_FR.get(x, x) if x else "" for x in self.lab["pchar"]]
        self.utci_l = [UTCI_FR.get(x, x) if x else "" for x in self.lab["utci"]]

    def now_index(self):
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        return min(max(int((now - self.ref).total_seconds() // 3600), 0), self.n - 1)

    def upto(self, i0, hours):
        return range(i0, min(i0 + hours, self.n))

    def is_day(self, i):
        return sun_elevation(self.utc[i] + timedelta(minutes=30)) > -1.0

    def kind(self, i):
        """Dominant precipitation of hour i: rain | snow | frz | pel | mix | none."""
        if self.pr[i] < 0.05 and self.rate[i] < 0.05:
            return "none"
        amt = {"rain": self.rain[i], "snow": self.swe[i], "frz": self.frz[i], "pel": self.pel[i]}
        tot = sum(amt.values())
        if tot < 1e-3:
            return {"Neige": "snow", "Grésil": "pel", "Pluie verglaçante": "frz"}.get(self.ptype[i], "rain")
        if amt["frz"] > 0.2 * tot:
            return "frz"
        top = max(amt, key=amt.get)
        if top in ("snow", "rain") and amt["snow" if top == "rain" else "rain"] > 0.3 * tot:
            return "mix"
        return top

    def icon(self, i):
        k, day = self.kind(i), self.is_day(i)
        if k != "none" and self.tsprob[i] >= 40: return "⛈️"
        if k == "snow": return "🌨️" if self.pr[i] < 1.5 else "❄️"
        if k in ("frz", "pel"): return "🧊"
        if k == "mix": return "🌨️"
        if k == "rain": return "🌦️" if (self.pr[i] < 0.5 and (self.nt[i] or 100) < 80) else "🌧️"
        if self.fogvis[i] is not None and self.fogvis[i] < 1000: return "🌫️"
        nt = self.nt[i] if self.nt[i] is not None else 50
        if nt < 15: return "☀️" if day else "🌙"
        if nt < 40: return "🌤️" if day else "🌙"
        if nt < 75: return "⛅" if day else "☁️"
        return "☁️"

    def sky(self, i):
        nt = self.nt[i] if self.nt[i] is not None else 50
        return "Dégagé" if nt < 15 else "Peu nuageux" if nt < 40 else "Partiellement nuageux" if nt < 75 else "Couvert"

    def describe(self, i):
        k = self.kind(i)
        if k == "none":
            return self.sky(i)
        name = {"rain": "Pluie", "snow": "Neige", "frz": "Pluie verglaçante", "pel": "Grésil", "mix": "Pluie et neige"}[k]
        return f"{name} ({self.pint[i].lower()})" if self.pint[i] else name

    def slot(self, date, h0, h1):
        """Aggregate local hours [h0, h1) of a local date, or None."""
        idx = [i for i, t in enumerate(self.local) if t.date() == date and h0 <= t.hour < h1]
        if not idx:
            return None
        temps = [self.tt[i] for i in idx if self.tt[i] is not None]
        mm, cm = sum(self.pr[i] for i in idx), sum(self.snow_cm[i] for i in idx)
        rep = max(idx, key=lambda i: self.pr[i]) if mm >= 0.2 else idx[len(idx) // 2]
        return {"idx": idx, "t": round(sum(temps) / len(temps)) if temps else None,
                "mm": mm, "cm": cm, "icon": self.icon(rep), "gust": max((self.gust[i] or 0) for i in idx)}

    def alerts(self, i0, hours=36):
        """[(text, hour_index)] for noteworthy conditions in the next `hours` hours."""
        r = self.upto(i0, hours)
        when = lambda i: fr(self.local[i], "%a %Hh")
        out = []
        for i in r:
            if self.kind(i) == "frz": out.append((f"Pluie verglaçante · {when(i)}", i)); break
        for i in r:
            if self.kind(i) == "pel": out.append((f"Grésil · {when(i)}", i)); break
        g = max(r, key=lambda i: self.gust[i] or 0)
        if (self.gust[g] or 0) >= 60: out.append((f"Rafales {self.gust[g]:.0f} km/h · {when(g)}", g))
        p = max(r, key=lambda i: self.rain[i])
        if self.rain[p] >= 5: out.append((f"Forte pluie {self.rain[p]:.1f} mm/h · {when(p)}", p))
        sn = max(r, key=lambda i: self.snow_cm[i])
        if self.snow_cm[sn] >= 2: out.append((f"Neige ~{self.snow_cm[sn]:.1f} cm/h · {when(sn)}", sn))
        t = min(r, key=lambda i: self.tt[i] if self.tt[i] is not None else 99)
        if self.tt[t] is not None and self.tt[t] <= 0: out.append((f"Gel {self.tt[t]:.0f}° · {when(t)}", t))
        o = max(r, key=lambda i: self.tsprob[i])
        if self.tsprob[o] >= 30: out.append((f"Orage {self.tsprob[o]:.0f}% · {when(o)}", o))
        return out

def load():
    try:
        return Data(json.loads(_series_path().read_text(encoding="utf-8")))
    except Exception:
        return None

def maps_ready(data=None):
    st = _state()
    return (_maps_path().exists() and st.get("maps_fmt") == MAPS_FORMAT
            and (data is None or st.get("maps_ref") == data.raw["ref"]))

def load_maps():
    """-> {"region": (grids, bounds), "wide": (grids, bounds) | None, "ref": str} or None.
    grids = {layer: ndarray[T, ny, nx]}; bounds = (lon_w, lat_s, lon_e, lat_n)."""
    try:
        import numpy as np
        z = np.load(_maps_path())
        region = ({k: z[k] for k in MAP_LAYERS}, tuple(float(v) for v in z["bounds"]))
        wide = None
        if "w_rt" in z.files:
            wide = ({k: z["w_" + k] for k in MAP_LAYERS}, tuple(float(v) for v in z["wide_bounds"]))
        return {"region": region, "wide": wide, "ref": str(z["ref"])}
    except Exception:
        return None
