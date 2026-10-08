"""Weather radar (Environment Canada, via MSC GeoMet): the last ~3 hours in 6-minute steps.

Frames are WMS GetMap images in Web Mercator for a bounding box around the current map view
(rain rate RADAR_1KM_RRAI, plus snow rate RADAR_1KM_RSNO when it is cold enough to matter).

Speed matters: GeoMet answers ~5 images/s whatever the parallelism, so
  * frames are fetched newest-first and shown as they arrive (the latest image appears in ~1 s,
    the loop fills in over a few seconds),
  * the map frame is snapped to a coarse grid so small pans / re-opens hit the on-disk cache,
  * the snow layer is only requested when the temperature is low.
When the user pans or zooms beyond what was loaded, a debounced reload fetches new frames; the old ones
keep being drawn, re-projected, meanwhile.
"""
import io
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import cairo

from . import __version__, config, net

GEOMET = "https://geo.weather.gc.ca/geomet"
UA = f"hrdps-weather/{__version__} (+https://github.com/Carlsans/hrdps-weather)"
FMT = "%Y-%m-%dT%H:%M:%SZ"
RAIN, SNOW = "RADAR_1KM_RRAI", "RADAR_1KM_RSNO"
MARGIN = 1.4               # loaded area = view × MARGIN, so small pans need no reload
MAX_PX = 800
WORKERS = 4                # with kept-alive connections 4 is as fast as more, and gentler on the server
MAX_AGE = 360              # seconds before the loop is refreshed (radar updates every 6 min)
SNOW_BELOW_C = 6.0         # request the snow layer only when the temperature is below this
KEEP_FILES = 5 * 3600      # on-disk frames older than this are deleted
R = 20037508.342789244     # Web Mercator half-extent in metres


def _get(url, timeout=30):
    return net.get(url, UA, timeout)


def frame_times(count=31):
    """UTC times of the most recent radar frames, from the layer's time dimension."""
    raw = _get(f"{GEOMET}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities&LAYER={RAIN}", 40)
    if raw:
        s = raw.decode("utf-8", "ignore")
        i = s.find(f"<Name>{RAIN}</Name>")
        m = re.search(r'<Dimension name="time"[^>]*>([^<]*)<', s[i:i + 5000]) if i >= 0 else None
        if m and m.group(1).count("/") == 2:
            _, b, step = m.group(1).split("/")
            end = datetime.strptime(b, FMT).replace(tzinfo=timezone.utc)
            mins = int(re.search(r"PT(\d+)M", step).group(1)) if re.search(r"PT(\d+)M", step) else 6
            return [end - timedelta(minutes=mins * k) for k in range(count - 1, -1, -1)]
    return []


def priority(times):
    """Newest first, then every other frame going back, then the rest: a usable loop appears early."""
    n = len(times)
    order = list(range(n - 1, -1, -2)) + list(range(n - 2, -1, -2))
    return [times[i] for i in order]


def bucket(view_bbox, zoom, widget_px):
    """Frame to load for a view: (bbox metres, image px, cache key). The centre is snapped to a grid and the
    zoom to half levels, so nearby views share the same frames (and the same disk cache entries)."""
    zb = round(zoom * 2) / 2
    half = widget_px / 2 * (2 * R / (256 * 2.0 ** zb)) * MARGIN
    step = half / 3
    cx = round((view_bbox[0] + view_bbox[2]) / 2 / step) * step
    cy = round((view_bbox[1] + view_bbox[3]) / 2 / step) * step
    px = int(min(widget_px * MARGIN, MAX_PX))
    return (cx - half, cy - half, cx + half, cy + half), px, f"{zb:g}_{int(cx)}_{int(cy)}_{px}"


class RadarLoader:
    def __init__(self, cache_dir=None):
        self.dir = (cache_dir or config.cache_dir()) / "radar"
        self.frames = []          # [(utc datetime, cairo surface)] — rain, oldest first
        self.snow = {}            # utc datetime -> cairo surface (only filled when it is cold)
        self.bbox = None          # metres, Web Mercator, of the loaded frames
        self.zoom = None
        self.key = None
        self.loaded_at = 0.0
        self.version = 0
        self.state = "idle"       # idle | loading | error
        self.done = self.total = 0
        self._rain = {}
        self._want = None
        self._lock = threading.Lock()
        self._thread = None

    # -- public ----------------------------------------------------------------
    def wait(self, timeout=60.0):
        """Block until the pending load finished (still-image export)."""
        end = time.time() + timeout
        time.sleep(0.6)                                    # let the debounce fire
        while time.time() < end and (self._want is not None or self.state == "loading"):
            time.sleep(0.2)

    def request(self, view_bbox, zoom, widget_px, want_snow=False):
        """Ask for frames covering `view_bbox`; cheap to call every frame (debounced, no-op when covered)."""
        with self._lock:
            covered = (self.bbox is not None and abs(zoom - (self.zoom or 0)) < 0.7
                       and self.bbox[0] <= view_bbox[0] and self.bbox[1] <= view_bbox[1]
                       and self.bbox[2] >= view_bbox[2] and self.bbox[3] >= view_bbox[3]
                       and time.time() - self.loaded_at < MAX_AGE
                       and (not want_snow or self.snow))
            if covered or (self.state == "error" and time.time() - self.loaded_at < 20):
                return
            new = (view_bbox, zoom, widget_px, want_snow)
            if self._want is None or self._want[:2] != new[:2]:
                self._want = (*new, time.time() + 0.4)     # debounce: wait for the camera to settle
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

    # -- loading ---------------------------------------------------------------
    def _run(self):
        while True:
            with self._lock:
                want = self._want
            if want is None:
                return
            delay = want[4] - time.time()
            if delay > 0:
                time.sleep(delay)
                continue
            self.state = "loading"
            self.version += 1
            try:
                ok = self._load(*want[:4])
            except Exception:
                ok = False
            with self._lock:
                if self._want is want:                     # nothing newer was requested meanwhile
                    self._want = None
            self.state = "idle" if ok else "error"
            self.loaded_at = time.time()
            self.version += 1

    def _path(self, layer, t, key):
        return self.dir / layer / f"{t.strftime('%Y%m%dT%H%MZ')}_{key}.png"

    def _frame(self, layer, t, bbox, px, key):
        """Cairo surface of one frame: from the disk cache, else downloaded (and cached)."""
        p = self._path(layer, t, key)
        raw = None
        if p.exists():
            try:
                raw = p.read_bytes()
            except OSError:
                raw = None
        if raw is None:
            url = (f"{GEOMET}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS={layer}&CRS=EPSG:3857"
                   f"&BBOX={bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}&WIDTH={px}&HEIGHT={px}"
                   f"&FORMAT=image/png&TRANSPARENT=TRUE&TIME={t.strftime(FMT)}")
            raw = _get(url)
            if raw is None:
                return None
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(raw)
            except OSError:
                pass
        try:
            return cairo.ImageSurface.create_from_png(io.BytesIO(raw))
        except Exception:
            p.unlink(missing_ok=True)
            return None

    def _prune(self):
        cutoff = time.time() - KEEP_FILES
        try:
            for f in self.dir.rglob("*.png"):
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
        except OSError:
            pass

    def _load(self, view_bbox, zoom, widget_px, want_snow):
        bbox, px, key = bucket(view_bbox, zoom, widget_px)
        times = frame_times()
        if not times:
            return False
        self._prune()
        order = priority(times)
        same = key == self.key
        rain = dict(self._rain) if same else {}
        keep = set(times)
        got = 0

        def publish(first_of_new_key=False):
            if first_of_new_key:                          # switch to the new frame only once it has content
                self.bbox, self.zoom, self.key = bbox, zoom, key
                self.snow = {}
            self._rain = rain
            self.frames = sorted(((t, s) for t, s in rain.items() if t in keep), key=lambda f: f[0])
            self.version += 1

        for layer, store in ((RAIN, rain), (SNOW, None)):
            if layer == SNOW and not want_snow:
                break
            todo = [t for t in order if (store is None or t not in store)] if layer == RAIN else order
            self.done, self.total = 0, len(todo)
            if layer == SNOW and not same:
                self.snow = {}
            with ThreadPoolExecutor(WORKERS) as ex:
                futs = {ex.submit(self._frame, layer, t, bbox, px, key): t for t in todo}
                for fut in as_completed(futs):
                    t, surf = futs[fut], fut.result()
                    self.done += 1
                    if surf is None:
                        continue
                    if layer == RAIN:
                        rain[t] = surf
                        publish(first_of_new_key=(got == 0 and not same))
                        got += 1
                    else:
                        self.snow = {**self.snow, t: surf}
                        self.version += 1
            if layer == RAIN and got == 0 and not rain:
                return False
        self.loaded_at = time.time()
        return True
