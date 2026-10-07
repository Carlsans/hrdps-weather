"""
HRDPS (ECCC, 2.5 km) data layer for the waybar weather module.

Everything comes from MSC GeoMet (WMS GetFeatureInfo for the point series,
WCS GetCoverage for map grids). No other weather source is used.

  load()            -> Data | None   read the cache (never blocks on network)
  refresh(force)    -> None          download a full run (series + map grids)
  ensure_fresh()    -> None          spawn a detached refresh if a new run may exist

Cache: ~/.cache/waybar-weather/
"""
import io, json, math, os, re, subprocess, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__, config

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
MAP_ZOOM, MAP_TILES = 8, 3   # basemap: 3x3 OSM tiles at z8 (~±2° around the point)

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
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(0.5)

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
    with ThreadPoolExecutor(12) as ex:
        res = list(ex.map(lambda j: _feature(POINT_LAYERS[j[0]], times[j[1]]), jobs))
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
def tile_xy(z=MAP_ZOOM):
    n = 2 ** z
    return (int((LON + 180) / 360 * n),
            int((1 - math.asinh(math.tan(math.radians(LAT))) / math.pi) / 2 * n))

def _tile_lonlat(x, y, z):
    n = 2 ** z
    return x / n * 360 - 180, math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))

def map_bounds():
    """(lon_w, lat_s, lon_e, lat_n) of the basemap tile block."""
    x, y = tile_xy(); h = MAP_TILES // 2
    w, n = _tile_lonlat(x - h, y - h, MAP_ZOOM)
    e, s = _tile_lonlat(x + h + 1, y + h + 1, MAP_ZOOM)
    return w, s, e, n

def _fetch_grid(layer, t, bounds):
    import numpy as np
    w, s, e, n = bounds
    url = (f"{GEOMET}?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID={layer}&FORMAT=image/x-aaigrid"
           f"&SUBSETTINGCRS=EPSG:4326&SUBSET=x({w},{e})&SUBSET=y({s},{n})&TIME={t.strftime(FMT)}")
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
    import numpy as np
    b = map_bounds()
    times = [ref + timedelta(hours=i) for i in range(HOURS + 1)]
    jobs = [(k, i) for k in MAP_LAYERS for i in range(len(times))]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda j: _fetch_grid(MAP_LAYERS[j[0]], times[j[1]], b), jobs))
    shape = next((r.shape for r in res if r is not None), None)
    if shape is None:
        return None
    out = {k: np.full((len(times),) + shape, np.nan, np.float32) for k in MAP_LAYERS}
    for (k, i), r in zip(jobs, res):
        if r is not None and r.shape == shape:
            out[k][i] = r
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
        have_maps   = st.get("maps_ref") == ref.strftime(FMT) and _maps_path().exists()
        if not force and have_series and (have_maps or not maps):
            _save_state(checked=time.time()); return
        if force or not have_series:
            data = _fetch_series(ref)
            if sum(v is not None for v in data["series"]["tt"]) < 10:
                return                        # run still being published: keep old cache, retry later
            tmp = _series_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8"); tmp.replace(_series_path())
            _save_state(ref=data["ref"], checked=time.time())
        if maps:
            g = _fetch_maps(ref)
            if g:
                import numpy as np
                tmp = CACHE / "maps.tmp.npz"
                np.savez_compressed(tmp, bounds=np.array(map_bounds()), ref=np.array(ref.strftime(FMT)), **g)
                tmp.replace(_maps_path())
                _save_state(maps_ref=ref.strftime(FMT))
    finally:
        lock.release()

def ensure_fresh(force=False):
    """Spawn a detached refresh when the cache is missing or a new run may exist."""
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
        self.tt, self.td, self.hr, self.re_ = f("tt"), f("td"), f("hr"), f("re")
        self.hmx, self.utci = f("hmx"), f("utci")
        self.ws, self.wd = kmh("ws"), f("wd")                                  # km/h, °
        # GeoMet leaves the gust layer empty when gusts do not exceed the mean wind
        self.gust = [max(g, w) if g is not None and w is not None else w for g, w in zip(kmh("wgx"), self.ws)]
        self.slp  = [v / 100 if v is not None else None for v in s["slp"]]     # hPa
        self.nt, self.uv, self.pbl, self.cape = f("nt"), f("uv", 0), f("pbl"), f("cape", 0)
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
    return _maps_path().exists() and (data is None or st.get("maps_ref") == data.raw["ref"])

def load_maps():
    """-> ({layer: ndarray[T, ny, nx]}, (lon_w, lat_s, lon_e, lat_n), ref_str) or None."""
    try:
        import numpy as np
        z = np.load(_maps_path())
        return {k: z[k] for k in MAP_LAYERS}, tuple(float(v) for v in z["bounds"]), str(z["ref"])
    except Exception:
        return None

def basemap():
    """Darkened OSM tile block (PIL RGB), cached on disk; None if tiles are unreachable."""
    from PIL import Image, ImageOps
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"basemap_{MAP_ZOOM}_{MAP_TILES}.png"
    if out.exists():
        return Image.open(out).convert("RGB")
    x0, y0 = tile_xy(); h = MAP_TILES // 2
    img = Image.new("RGB", (256 * MAP_TILES, 256 * MAP_TILES), (30, 30, 46))
    for dx in range(-h, h + 1):
        for dy in range(-h, h + 1):
            raw = _get(f"https://tile.openstreetmap.org/{MAP_ZOOM}/{x0+dx}/{y0+dy}.png")
            if raw is None:
                return None
            img.paste(Image.open(io.BytesIO(raw)).convert("RGB"), (256 * (dx + h), 256 * (dy + h)))
    g = ImageOps.autocontrast(ImageOps.invert(img.convert("L")), cutoff=1).point(lambda v: int(18 + v * 0.36))
    dark = Image.merge("RGB", (g.point(lambda v: int(v * 0.95)), g,
                               g.point(lambda v: min(255, int(v * 1.25 + 4)))))
    dark.save(out)
    return dark
