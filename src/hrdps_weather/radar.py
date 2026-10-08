"""Weather radar (Environment Canada, via MSC GeoMet): the last ~3 hours in 6-minute steps.

Frames are WMS GetMap images in Web Mercator for a bounding box around the current map view (rain rate
RADAR_1KM_RRAI + snow rate RADAR_1KM_RSNO composited). When the user pans or zooms beyond what was
loaded, a debounced reload fetches new frames; the old ones keep being drawn, re-projected, meanwhile.
"""
import io
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import __version__
from .tiles import pil_to_surface

GEOMET = "https://geo.weather.gc.ca/geomet"
UA = f"hrdps-weather/{__version__} (+https://github.com/Carlsans/hrdps-weather)"
FMT = "%Y-%m-%dT%H:%M:%SZ"
LAYERS = ("RADAR_1KM_RRAI", "RADAR_1KM_RSNO")
MARGIN = 1.5               # loaded area = view × MARGIN, so small pans need no reload
MAX_PX = 1000
MAX_AGE = 360              # seconds before the loop is reloaded (radar updates every 6 min)
R = 20037508.342789244     # Web Mercator half-extent in metres


def _get(url, timeout=30):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def frame_times(count=31):
    """UTC times of the most recent radar frames, from the layer's time dimension."""
    raw = _get(f"{GEOMET}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities&LAYER={LAYERS[0]}", 40)
    if raw:
        s = raw.decode("utf-8", "ignore")
        i = s.find(f"<Name>{LAYERS[0]}</Name>")
        m = re.search(r'<Dimension name="time"[^>]*>([^<]*)<', s[i:i + 5000]) if i >= 0 else None
        if m and m.group(1).count("/") == 2:
            a, b, step = m.group(1).split("/")
            end = datetime.strptime(b, FMT).replace(tzinfo=timezone.utc)
            mins = int(re.search(r"PT(\d+)M", step).group(1)) if re.search(r"PT(\d+)M", step) else 6
            return [end - timedelta(minutes=mins * k) for k in range(count - 1, -1, -1)]
    return []


class RadarLoader:
    def __init__(self):
        self.frames = []          # [(utc datetime, cairo surface)]
        self.bbox = None          # (minx, miny, maxx, maxy) metres, Web Mercator, of the loaded frames
        self.zoom = None
        self.loaded_at = 0.0
        self.version = 0
        self.state = "idle"       # idle | loading | error
        self._want = None
        self._lock = threading.Lock()
        self._thread = None

    def wait(self, timeout=60.0):
        """Block until the pending load finished (still-image export)."""
        end = time.time() + timeout
        time.sleep(0.6)                                    # let the debounce fire
        while time.time() < end and (self._want is not None or self.state == "loading"):
            time.sleep(0.2)

    def request(self, view_bbox, zoom, widget_px):
        """Ask for frames covering `view_bbox`; cheap to call every frame (debounced, no-op when covered)."""
        with self._lock:
            covered = (self.bbox is not None and abs(zoom - (self.zoom or 0)) < 0.7
                       and self.bbox[0] <= view_bbox[0] and self.bbox[1] <= view_bbox[1]
                       and self.bbox[2] >= view_bbox[2] and self.bbox[3] >= view_bbox[3]
                       and time.time() - self.loaded_at < MAX_AGE)
            if covered or (self.state == "error" and time.time() - self.loaded_at < 20):
                return
            new = (view_bbox, zoom, widget_px)
            if self._want is None or self._want[:2] != new[:2]:
                self._want = (*new, time.time() + 0.4)          # debounce: wait for the camera to settle
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

    def _run(self):
        while True:
            with self._lock:
                want = self._want
            if want is None:
                return
            delay = want[3] - time.time()
            if delay > 0:
                time.sleep(delay)
                continue
            self.state = "loading"
            self.version += 1
            ok = self._load(*want[:3])
            with self._lock:
                if self._want is want:                         # nothing newer was requested meanwhile
                    self._want = None
            self.state = "idle" if ok else "error"
            self.loaded_at = time.time()
            self.version += 1

    def _load(self, view_bbox, zoom, widget_px):
        from PIL import Image
        minx, miny, maxx, maxy = view_bbox
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        hw, hh = (maxx - minx) / 2 * MARGIN, (maxy - miny) / 2 * MARGIN
        hw, hh = min(hw, R), min(hh, R)
        bbox = (max(cx - hw, -R), max(cy - hh, -R), min(cx + hw, R), min(cy + hh, R))
        px = int(min(widget_px * MARGIN, MAX_PX))
        times = frame_times()
        if not times:
            return False

        def one(t):
            im = None
            for layer in LAYERS:
                url = (f"{GEOMET}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS={layer}&CRS=EPSG:3857"
                       f"&BBOX={bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}&WIDTH={px}&HEIGHT={px}"
                       f"&FORMAT=image/png&TRANSPARENT=TRUE&TIME={t.strftime(FMT)}")
                raw = _get(url)
                if raw is None:
                    return None
                try:
                    layer_im = Image.open(io.BytesIO(raw)).convert("RGBA")
                except Exception:
                    return None
                im = layer_im if im is None else Image.alpha_composite(im, layer_im)
            return im

        with ThreadPoolExecutor(8) as ex:
            imgs = list(ex.map(one, times))
        frames = [(t, pil_to_surface(im)) for t, im in zip(times, imgs) if im is not None]
        if len(frames) < len(times) // 2:
            return False
        self.frames, self.bbox, self.zoom = frames, bbox, zoom
        return True
