"""Base-map tiles: OpenStreetMap raster tiles, darkened to match the interface, cached on disk.

Tiles are fetched on demand by two background workers (the OSM tile policy forbids bulk downloads and
requires a real User-Agent), kept as ready-to-draw cairo surfaces in a small LRU, and stored on disk
already darkened. While a tile is missing the caller falls back to a coarser ancestor tile.
"""
import io
import queue
import threading
import time
import urllib.request
from collections import OrderedDict
from pathlib import Path

import cairo

from . import __version__

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
UA = f"hrdps-weather/{__version__} (+https://github.com/Carlsans/hrdps-weather)"
MAX_ZOOM = 12
MEM_TILES = 300
RETRY_AFTER = 60          # seconds before a failed tile is requested again


def darken(img):
    """OSM's light style -> dark UI tint (fixed mapping, so neighbouring tiles match)."""
    from PIL import Image, ImageOps
    g = ImageOps.invert(img.convert("L")).point(lambda v: int(18 + v * 0.76))
    return Image.merge("RGB", (g.point(lambda v: int(v * 0.95)), g, g.point(lambda v: min(255, int(v * 1.25 + 4)))))


def pil_to_surface(img):
    """PIL image -> cairo surface (pixels are copied, so nothing has to be kept alive)."""
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, format="PNG", compress_level=1)
    buf.seek(0)
    return cairo.ImageSurface.create_from_png(buf)


class TileCache:
    def __init__(self, cache_dir, network=True):
        self.dir = Path(cache_dir) / "tiles"
        self.network = network
        self.version = 0                              # bumped when a tile arrives (the UI redraws on change)
        self._mem = OrderedDict()
        self._lock = threading.Lock()
        self._pending, self._failed = set(), {}
        self._q = queue.Queue()
        self._workers = []

    # -- lookup -----------------------------------------------------------------
    def _path(self, z, x, y):
        return self.dir / str(z) / str(x) / f"{y}.png"

    def get(self, z, x, y, schedule=True):
        """Ready surface for the tile, or None (and queue its download)."""
        key = (z, x, y)
        with self._lock:
            s = self._mem.get(key)
            if s is not None:
                self._mem.move_to_end(key)
                return s
        p = self._path(z, x, y)
        if p.exists():
            try:
                from PIL import Image
                s = pil_to_surface(Image.open(p))
                self._store(key, s)
                return s
            except Exception:
                p.unlink(missing_ok=True)
        if schedule and self.network and z <= MAX_ZOOM:
            self._schedule(key)
        return None

    def get_best(self, z, x, y):
        """(surface, z, x, y) of the tile or, while it loads, its nearest cached ancestor; None if nothing."""
        for k in range(0, 4):
            zz = z - k
            if zz < 0:
                break
            s = self.get(zz, x >> k, y >> k, schedule=(k == 0))
            if s is not None:
                return s, zz, x >> k, y >> k
        return None

    def _store(self, key, surf):
        with self._lock:
            self._mem[key] = surf
            while len(self._mem) > MEM_TILES:
                self._mem.popitem(last=False)

    # -- background download -----------------------------------------------------
    def _schedule(self, key):
        with self._lock:
            if key in self._pending or time.time() - self._failed.get(key, 0) < RETRY_AFTER:
                return
            self._pending.add(key)
        self._q.put(key)
        if not self._workers:
            for _ in range(2):
                t = threading.Thread(target=self._work, daemon=True)
                t.start()
                self._workers.append(t)

    def _work(self):
        from PIL import Image
        while True:
            key = self._q.get()
            z, x, y = key
            try:
                req = urllib.request.Request(TILE_URL.format(z=z, x=x, y=y), headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=20) as r:
                    img = darken(Image.open(io.BytesIO(r.read())))
                p = self._path(z, x, y)
                p.parent.mkdir(parents=True, exist_ok=True)
                img.save(p)
                self._store(key, pil_to_surface(img))
                self.version += 1
            except Exception:
                self._failed[key] = time.time()
            finally:
                with self._lock:
                    self._pending.discard(key)
            time.sleep(0.05)                          # stay gentle with the tile servers

    def wait(self, timeout=10.0):
        """Block until the download queue is empty (used by still-image export)."""
        end = time.time() + timeout
        while time.time() < end:
            with self._lock:
                if not self._pending:
                    return True
            time.sleep(0.1)
        return False
