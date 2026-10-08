"""
Cairo rendering of the HRDPS weather dashboard (map animation + charts + details).
Pure drawing code: used by the GTK popup and by `--png` export for testing.
"""
import math
import os
import sys

import cairo

from . import hrdps

# Text: Pango (colour emoji, nice shaping) when PyGObject is installed (typical on Linux),
# otherwise cairo's built-in "toy" text API (Windows: pip-installed pycairo only).
try:
    if os.environ.get("HRDPS_TEXT") == "toy":
        raise ImportError
    import gi
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo
    HAVE_PANGO = True
except (ImportError, ValueError):
    HAVE_PANGO = False

# ── Theme (Catppuccin mocha, like the rest of the bar) ───────────────────────
def hx(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))

BG, SURFACE, PANEL = hx("1e1e2e"), hx("181825"), hx("11111b")
TEXT, SUBTLE, MUTED = hx("cdd6f4"), hx("a6adc8"), hx("6c7086")
RED, PEACH, YELLOW, GREEN = hx("f38ba8"), hx("fab387"), hx("f9e2af"), hx("a6e3a1")
TEAL, SKY, BLUE, LAV, MAUVE = hx("94e2d5"), hx("89dceb"), hx("89b4fa"), hx("b4befe"), hx("cba6f7")
SNOWC = hx("e6e9ff")

W, H = 1400, 924
M = 18
MAP_RECT = (M, 116, 560, 560)
CHART_RECT = (612, 110, 770, 680)
MP = 200                           # overlay raster size (square, mercator space)
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]

LAYERS = [("rt", "💧 Précip."), ("tt", "🌡 Temp."), ("ws", "💨 Vent"), ("nt", "☁ Nuages"), ("radar", "📡 Radar")]
LAYER_TITLE = {"rt": "Précipitations (prévision HRDPS)", "tt": "Température à 2 m (HRDPS)", "ws": "Vent à 10 m (HRDPS)",
               "nt": "Couverture nuageuse (HRDPS)", "radar": "Radar météo (observé, ECCC)"}
DEFAULT_Z, MIN_Z, MAX_Z = 7.55, 5.5, 11.0        # tile-zoom of the camera (7.55 ≈ the 3-tile block of earlier versions)
OV = 160                                         # model overlay raster size (square), scaled up with bilinear filtering
# Official radar palette (GeoMet legend, 0.1 → 200 mm/h), evenly spaced on the legend.
RADAR_COLORS = [(133, 197, 254), (0, 174, 222), (0, 241, 76), (0, 186, 0), (0, 133, 0), (106, 165, 0), (254, 231, 0),
                (254, 176, 0), (254, 123, 0), (254, 34, 0), (254, 1, 108), (178, 39, 191), (112, 11, 163), (59, 0, 89)]
RADAR_TICKS = [0.1, 1, 2, 4, 8, 12, 16, 24, 32, 50, 64, 100, 125, 200]

def compass(deg):
    return COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]

# ── Text helper ──────────────────────────────────────────────────────────────
FONT = "Segoe UI" if sys.platform == "win32" else "Noto Sans"
EMOJI_FONT = "Segoe UI Emoji" if sys.platform == "win32" else "Noto Color Emoji"

def _is_emoji(ch):
    return ord(ch) >= 0x2600

def _runs(s):
    """Split into (emoji?, text) runs; variation selectors are dropped (toy API has no shaping)."""
    out = []
    for ch in s:
        if ch in "\ufe0f\u200d":
            continue
        e = _is_emoji(ch)
        if out and out[-1][0] == e:
            out[-1][1] += ch
        else:
            out.append([e, ch])
    return out

def _text_toy(cr, s, x, y, size, color, bold, anchor, va, alpha):
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size * 0.95)
    asc, desc = cr.font_extents()[:2]
    runs = _runs(str(s))
    widths = []
    for emo, t in runs:
        cr.select_font_face(EMOJI_FONT if emo else FONT, cairo.FONT_SLANT_NORMAL,
                            cairo.FONT_WEIGHT_BOLD if bold and not emo else cairo.FONT_WEIGHT_NORMAL)
        widths.append(cr.text_extents(t).x_advance)
    w, h = sum(widths), asc + desc
    x -= w if anchor == "r" else w / 2 if anchor == "c" else 0
    y -= h if va == "b" else h / 2 if va == "m" else 0
    cr.set_source_rgba(*color, alpha)
    for (emo, t), tw in zip(runs, widths):
        cr.select_font_face(EMOJI_FONT if emo else FONT, cairo.FONT_SLANT_NORMAL,
                            cairo.FONT_WEIGHT_BOLD if bold and not emo else cairo.FONT_WEIGHT_NORMAL)
        cr.move_to(x, y + asc)
        cr.show_text(t)
        x += tw
    return w

def text(cr, s, x, y, size=12, color=TEXT, bold=False, anchor="l", va="t", alpha=1.0):
    if not HAVE_PANGO:
        return _text_toy(cr, s, x, y, size, color, bold, anchor, va, alpha)
    lay = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription(FONT)
    fd.set_absolute_size(size * Pango.SCALE)
    fd.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    lay.set_font_description(fd)
    lay.set_text(str(s), -1)
    w, h = lay.get_pixel_size()
    x -= w if anchor == "r" else w / 2 if anchor == "c" else 0
    y -= h if va == "b" else h / 2 if va == "m" else 0
    cr.set_source_rgba(*color, alpha)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, lay)
    return w

def rrect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()

def _bilinear(a, R, C):
    """Bilinear sampling of grid `a` at fractional (row, col) indices, edges clamped."""
    import numpy as np
    ny, nx = a.shape
    R, C = np.clip(R, 0, ny - 1), np.clip(C, 0, nx - 1)
    r0, c0 = np.floor(R).astype(int), np.floor(C).astype(int)
    r1, c1 = np.minimum(r0 + 1, ny - 1), np.minimum(c0 + 1, nx - 1)
    fr, fc = R - r0, C - c0
    return (a[r0, c0] * (1 - fr) * (1 - fc) + a[r0, c1] * (1 - fr) * fc
            + a[r1, c0] * fr * (1 - fc) + a[r1, c1] * fr * fc)

def world_xy(lon, lat, z):
    """Web Mercator pixel coordinates (256-px tiles) of lon/lat at (fractional) zoom z. Works on arrays."""
    import numpy as np
    n = 256 * 2.0 ** z
    return (np.asarray(lon) + 180) / 360 * n, (1 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2 * n

def world_lonlat(wx, wy, z):
    import numpy as np
    n = 256 * 2.0 ** z
    return np.asarray(wx) / n * 360 - 180, np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * np.asarray(wy) / n))))

def merc(lat):
    return math.asinh(math.tan(math.radians(lat)))

# ── Colour ramps for the map overlays ────────────────────────────────────────
def _ramp(stops, x):
    import numpy as np
    xs = [s[0] for s in stops]
    return np.stack([np.interp(x, xs, [s[k] for s in stops]) for k in (1, 2, 3, 4)], axis=-1)

RAIN_STOPS = [(0.0, 90, 160, 255, 0), (0.05, 100, 170, 255, 90), (0.5, 50, 120, 255, 165), (1, 40, 200, 240, 195),
              (2, 60, 220, 120, 215), (4, 250, 230, 60, 230), (8, 255, 150, 40, 240), (16, 255, 60, 60, 245),
              (32, 225, 60, 225, 250)]
SNOW_STOPS = [(0.0, 215, 225, 255, 0), (0.05, 215, 228, 255, 100), (0.5, 200, 205, 255, 165), (1, 175, 160, 255, 195),
              (2, 190, 110, 255, 215), (4, 235, 95, 235, 230), (8, 255, 80, 170, 245), (16, 255, 70, 90, 250)]
TEMP_STOPS = [(-30, 170, 120, 255, 150), (-15, 80, 120, 255, 150), (-5, 80, 200, 255, 150), (0, 140, 240, 240, 150),
              (5, 120, 230, 140, 150), (12, 230, 240, 90, 150), (20, 255, 190, 60, 150), (28, 255, 110, 50, 150),
              (35, 230, 50, 80, 150)]
WIND_STOPS = [(0, 40, 50, 120, 30), (10, 60, 120, 230, 95), (25, 70, 210, 200, 140), (40, 240, 220, 70, 175),
              (60, 255, 140, 50, 200), (80, 240, 60, 80, 220), (100, 230, 80, 230, 235)]
LEGENDS = {   # layer -> (stops used for the bar, tick values, unit)
    "rt": (RAIN_STOPS, [0.1, 0.5, 1, 2, 4, 8, 16, 32], "mm/h"),
    "tt": (TEMP_STOPS, [-30, -15, -5, 0, 5, 12, 20, 28, 35], "°C"),
    "ws": (WIND_STOPS, [0, 10, 25, 40, 60, 80, 100], "km/h"),
    "nt": ([(0, 255, 255, 255, 0), (100, 255, 255, 255, 190)], [0, 25, 50, 75, 100], "%"),
    "radar": (None, None, "mm/h"),
}

def colorize(layer, a, tt=None):
    """a: float grid -> straight-alpha RGBA float array [0..255]."""
    import numpy as np
    if layer == "rt":
        rain, snow = _ramp(RAIN_STOPS, a), _ramp(SNOW_STOPS, a)
        w = np.clip((1.5 - tt) / 2.0, 0, 1)[..., None] if tt is not None else 0
        return rain * (1 - w) + snow * w
    if layer == "tt":
        return _ramp(TEMP_STOPS, a)
    if layer == "ws":
        return _ramp(WIND_STOPS, a * 3.6)
    out = np.zeros(a.shape + (4,)); out[..., :3] = 255; out[..., 3] = np.clip(a, 0, 100) / 100 * 190
    return out

def to_surface(rgba):
    import numpy as np
    a = rgba[..., 3:4] / 255.0
    bgra = np.empty(rgba.shape, np.uint8)
    bgra[..., 0] = rgba[..., 2] * a[..., 0]; bgra[..., 1] = rgba[..., 1] * a[..., 0]
    bgra[..., 2] = rgba[..., 0] * a[..., 0]; bgra[..., 3] = rgba[..., 3]
    h, w = rgba.shape[:2]
    buf = bytearray(bgra.tobytes())
    return cairo.ImageSurface.create_for_data(buf, cairo.FORMAT_ARGB32, w, h, w * 4), buf

# ── The view ─────────────────────────────────────────────────────────────────
class View:
    def __init__(self, data, tiles=None):
        self.d = data
        self.i0, self.i1 = data.now_index(), data.n - 1
        self.t = float(self.i0)
        self.layer, self.playing, self.speed = "rt", True, 2.0       # speed: model hours per second
        self.hits = []
        self.maps = None                                           # {"region": (grids, bounds), "wide": ...}
        self.status = ""                                           # message shown over the map
        self.tiles = tiles                                         # tiles.TileCache or None (no base map)
        self.radar = None                                          # radar.RadarLoader, created on first use
        self.rt = 0.0                                              # radar frame cursor (float index)
        self.reset_camera()
        self._seen = (-1, -1)
        self._pan0 = None

    # -- camera ----------------------------------------------------------------
    def reset_camera(self):
        self.clon, self.clat, self.z = hrdps.LON, hrdps.LAT, DEFAULT_Z

    def _center_world(self):
        wx, wy = world_xy(self.clon, self.clat, self.z)
        return float(wx), float(wy)

    def _set_center_world(self, wx, wy):
        lon, lat = world_lonlat(wx, wy, self.z)
        self.clon, self.clat = float(min(max(lon, -179.9), 179.9)), float(min(max(lat, -80), 80))

    def zoom_by(self, dz, ax=None, ay=None):
        """Zoom by dz levels keeping the point under (ax, ay) (design-space coordinates) fixed."""
        mx, my, mw, mh = MAP_RECT
        ax = mx + mw / 2 if ax is None else ax
        ay = my + mh / 2 if ay is None else ay
        cx, cy = self._center_world()
        lon, lat = world_lonlat(cx + (ax - mx - mw / 2), cy + (ay - my - mh / 2), self.z)
        self.z = min(max(self.z + dz, MIN_Z), MAX_Z)
        wx, wy = world_xy(lon, lat, self.z)
        self._set_center_world(float(wx) - (ax - mx - mw / 2), float(wy) - (ay - my - mh / 2))

    def begin_pan(self):
        self._pan0 = self._center_world()

    def pan_to(self, dx, dy):
        """Move the map by (dx, dy) pixels relative to where the drag began."""
        if self._pan0 is not None:
            self._set_center_world(self._pan0[0] - dx, self._pan0[1] - dy)

    # -- data ----------------------------------------------------------------
    def set_maps(self, maps):
        """maps = hrdps.load_maps(). Assigned last: the UI thread may already be drawing."""
        if maps:
            self.maps = maps
        self.status = ""

    @staticmethod
    def _grid_sample(arr, bounds, lon2, lat2):
        """Bilinear sample of one hourly grid at lon/lat arrays -> (values, inside-mask)."""
        import numpy as np
        w, s_, e, n = bounds
        ny, nx = arr.shape
        col = (lon2 - w) / (e - w) * nx - 0.5
        row = (n - lat2) / (n - s_) * ny - 0.5
        inside = (col >= -0.5) & (col <= nx - 0.5) & (row >= -0.5) & (row <= ny - 0.5)
        return _bilinear(np.nan_to_num(arr), row, col), inside

    def _sample(self, k, i, lon2, lat2):
        """Layer k at model hour i, at lon/lat arrays: coarse wide grid first, region grid on top of it. NaN outside."""
        import numpy as np
        out = np.full(np.shape(lon2), np.nan, np.float32)
        for key in ("wide", "region"):
            part = self.maps.get(key)
            if not part:
                continue
            grids, bounds = part
            val, inside = self._grid_sample(grids[k][i], bounds, lon2, lat2)
            out = np.where(inside, val, out)
        return out

    def _sample_t(self, k, lon2, lat2):
        i = int(math.floor(self.t)); f = self.t - i; j = min(i + 1, self.i1)
        a = self._sample(k, i, lon2, lat2)
        return a if f < 1e-3 or j == i else a * (1 - f) + self._sample(k, j, lon2, lat2) * f

    def _uv_t(self, lon2, lat2):
        """Wind vector (east, north) blowing *toward*, m/s, interpolated in time."""
        import numpy as np
        i = int(math.floor(self.t)); f = self.t - i; j = min(i + 1, self.i1)
        def uv(h):
            sp, wd = self._sample("ws", h, lon2, lat2), np.radians(self._sample("wd", h, lon2, lat2))
            return -sp * np.sin(wd), -sp * np.cos(wd)
        (u0, v0), (u1, v1) = uv(i), uv(j)
        return u0 * (1 - f) + u1 * f, v0 * (1 - f) + v1 * f

    def _view_lonlat(self, n):
        """lon/lat of an n×n grid of pixel centres covering the map rectangle."""
        import numpy as np
        mx, my, mw, mh = MAP_RECT
        cx, cy = self._center_world()
        px = (np.arange(n) + 0.5) / n * mw - mw / 2
        py = (np.arange(n) + 0.5) / n * mh - mh / 2
        lon, _ = world_lonlat(cx + px, np.full(n, cy), self.z)
        _, lat = world_lonlat(np.full(n, cx), cy + py, self.z)
        return np.meshgrid(lon, lat)

    # -- interaction ---------------------------------------------------------
    def hit(self, x, y):
        for (rx, ry, rw, rh), tag, arg in reversed(self.hits):
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return tag, arg
        return None, None

    def t_from_x(self, x, x0, w):
        return min(max(self.i0 + (x - x0) / w * (self.i1 - self.i0), self.i0), self.i1)

    def slider_set(self, x):
        """Move the time cursor from a slider x position (model time, or radar frames in radar mode)."""
        sx, sw = self.slider
        f = min(max((x - sx) / sw, 0.0), 1.0)
        if self.layer == "radar":
            n = len(self.radar.frames) if self.radar else 0
            self.rt = f * max(n - 1, 0)
        else:
            self.t = self.i0 + f * (self.i1 - self.i0)

    def poll(self):
        """True when something that arrived in the background (tiles, radar) should trigger a redraw."""
        seen = (self.tiles.version if self.tiles else -1, self.radar.version if self.radar else -1)
        changed = seen != self._seen
        self._seen = seen
        return changed

    def tick(self, dt):
        if not self.playing:
            return
        if self.layer == "radar":
            n = len(self.radar.frames) if self.radar else 0
            if n:
                self.rt += dt * self.speed * 2               # ~4 frames/s at the default speed
                if self.rt >= n + 1.5:                       # hold on the latest frame, then loop
                    self.rt = 0.0
            return
        self.t += dt * self.speed
        if self.t >= self.i1:
            self.t = float(self.i0)

    # -- drawing -------------------------------------------------------------
    def draw(self, cr, w, h):
        self.hits = []
        cr.save(); cr.scale(w / W, h / H)
        cr.set_source_rgb(*BG); cr.paint()
        for section in (self.draw_header, self.draw_map, self.draw_controls, self.draw_charts, self.draw_details):
            try:
                section(cr)
            except Exception as e:                         # never leave a half-drawn window without a reason
                self._report(cr, section.__name__, e)
        cr.restore()

    def _report(self, cr, name, exc):
        import traceback
        key = (name, repr(exc))
        if key != getattr(self, "_last_error", None):      # log once per distinct error, not every frame
            self._last_error = key
            traceback.print_exc()
        self.errors = getattr(self, "errors", 0) + 1
        text(cr, f"⚠ erreur d'affichage ({name}): {type(exc).__name__}: {exc}"[:160], M, H - 22 - 14 * (self.errors % 3), 11, RED, True)

    # header --------------------------------------------------------------------
    def draw_header(self, cr):
        d, i = self.d, self.i0
        text(cr, d.icon(i), M, 8, 54)
        text(cr, f"{d.tt[i]:.0f}°", M + 86, 4, 62, TEXT, True)
        x = M + 235
        text(cr, d.describe(i), x, 12, 24, TEXT, True)
        sub = [f"Ressenti {d.re_[i]:.0f}°" if d.re_[i] is not None else "",
               d.utci_l[i], f"Vent {d.ws[i]:.0f} km/h {compass(d.wd[i])}",
               f"rafales {d.gust[i]:.0f}" if d.gust[i] and d.gust[i] - d.ws[i] > 5 else "",
               f"HR {d.hr[i]:.0f}%"]
        text(cr, "  ·  ".join(s for s in sub if s), x, 46, 15, SUBTLE)
        al = d.alerts(i)
        if al:
            text(cr, "⚠  " + "    ⚠  ".join(a for a, _ in al[:4]), x, 72, 14, PEACH, True)
        else:
            text(cr, "Aucune alerte dans les 36 prochaines heures", x, 72, 14, MUTED)
        rn = d.ref.astimezone(hrdps.TZ)
        text(cr, hrdps.LOCATION, W - M, 8, 28, TEXT, True, "r")
        text(cr, f"HRDPS 2,5 km · run {d.ref.strftime('%HZ')} ({hrdps.fr(rn, '%a %d %b %Hh')})",
             W - M, 46, 13, MUTED, False, "r")
        text(cr, "Environnement Canada · GeoMet", W - M, 66, 11, MUTED, False, "r")
        text(cr, hrdps.next_run_text(d.ref), W - M, 84, 12, SUBTLE, True, "r")

    # map -----------------------------------------------------------------------
    def draw_map(self, cr):
        import numpy as np
        x, y, w, h = MAP_RECT
        self.hits.append(((x, y, w, h), "map", None))
        cr.save(); rrect(cr, x, y, w, h, 12); cr.clip()
        cr.set_source_rgb(*PANEL); cr.paint()
        if self.tiles is not None:
            self._draw_tiles(cr, x, y, w, h)
        lay = self.layer
        if lay == "radar":
            self._draw_radar(cr, x, y, w, h)
        elif self.maps is not None:
            lon2, lat2 = self._view_lonlat(OV)
            a = self._sample_t(lay, lon2, lat2)
            tt = self._sample_t("tt", lon2, lat2) if lay == "rt" else None
            if lay == "rt":
                a = a * 3600                                  # kg/m²/s → mm/h
            rgba = colorize(lay, np.nan_to_num(a), None if tt is None else np.nan_to_num(tt, nan=10.0))
            rgba[..., 3] *= (~np.isnan(a))
            surf, buf = to_surface(rgba)
            cr.save(); cr.translate(x, y); cr.scale(w / OV, h / OV)
            cr.set_source_surface(surf, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_BILINEAR); cr.paint(); cr.restore()
            if lay == "ws":
                self.draw_arrows(cr, x, y, w, h)
        else:
            text(cr, self.status or "Cartes en téléchargement…", x + w / 2, y + h / 2, 16, SUBTLE, False, "c", "m")
        self.draw_marker(cr, x, y, w, h)
        cr.restore()
        # caption, zoom buttons, scale bar, legend, attribution
        cr.save(); rrect(cr, x, y, w, 30, 12); cr.clip()
        cr.set_source_rgba(*PANEL, 0.72); cr.paint(); cr.restore()
        text(cr, LAYER_TITLE[lay], x + 12, y + 6, 14, TEXT, True)
        text(cr, self._time_label(), x + w - 12, y + 6, 14, SUBTLE, True, "r")
        self._draw_zoom_buttons(cr, x, y, w, h)
        self._draw_scale(cr, x + 12, y + 44)
        self.draw_legend(cr, x + 12, y + h - 32, w - 24)
        credit = "© contributeurs OpenStreetMap · " + ("radar ECCC" if lay == "radar" else "données HRDPS © ECCC")
        text(cr, credit, x + 12, y + h - 15, 9, SUBTLE, False, "l", alpha=0.8)

    def _time_label(self):
        if self.layer == "radar":
            fr = self.radar.frames if self.radar else []
            if not fr:
                return "chargement…" if (self.radar and self.radar.state == "loading") else "—"
            t = fr[min(int(round(self.rt)), len(fr) - 1)][0]
            ago = int(round((fr[-1][0] - t).total_seconds() / 60))
            return f"{hrdps.fr(t.astimezone(hrdps.TZ), '%Hh%M')} · " + ("dernière image" if ago == 0 else f"il y a {ago} min")
        return hrdps.fr(self.d.local[min(int(round(self.t)), self.i1)], "%a %d %b · %Hh")

    def _draw_tiles(self, cr, x, y, w, h):
        tc = self.tiles
        zt = int(min(max(round(self.z), 2), 12))
        s = 2.0 ** (self.z - zt)
        cxz, cyz = (float(v) for v in world_xy(self.clon, self.clat, zt))
        hw, hh = w / 2 / s, h / 2 / s
        n = 2 ** zt
        for ty in range(int((cyz - hh) // 256), int((cyz + hh) // 256) + 1):
            if ty < 0 or ty >= n:
                continue
            for tx in range(int((cxz - hw) // 256), int((cxz + hw) // 256) + 1):
                best = tc.get_best(zt, tx % n, ty)
                if best is None:
                    continue
                surf, zz, xx, yy = best
                f = 2 ** (zt - zz)                              # an ancestor tile covers f×f tiles of level zt
                left = xx * 256 * f + (tx - tx % n) * 256
                top = yy * 256 * f
                cr.save()
                cr.rectangle(x + w / 2 + (tx * 256 - cxz) * s, y + h / 2 + (ty * 256 - cyz) * s, 256 * s + 0.6, 256 * s + 0.6)
                cr.clip()
                cr.translate(x + w / 2 + (left - cxz) * s, y + h / 2 + (top - cyz) * s)
                cr.scale(f * s * 256 / surf.get_width(), f * s * 256 / surf.get_height())
                cr.set_source_surface(surf, 0, 0)
                cr.get_source().set_filter(cairo.FILTER_BILINEAR)
                cr.paint()
                cr.restore()

    def _view_meters(self):
        """Map rectangle as a Web Mercator bbox in metres (minx, miny, maxx, maxy)."""
        mx, my, mw, mh = MAP_RECT
        cx, cy = self._center_world()
        n = 256 * 2.0 ** self.z
        R = 20037508.342789244
        to_x = lambda wx: (wx / n * 2 - 1) * R
        to_y = lambda wy: (1 - wy / n * 2) * R
        return (to_x(cx - mw / 2), to_y(cy + mh / 2), to_x(cx + mw / 2), to_y(cy - mh / 2))

    def _draw_radar(self, cr, x, y, w, h):
        from .radar import RadarLoader, R
        if self.radar is None:
            self.radar = RadarLoader()
        self.radar.request(self._view_meters(), self.z, w)
        fr = self.radar.frames
        if not fr:
            msg = "Radar indisponible (réseau ?)" if self.radar.state == "error" else "Chargement du radar…"
            text(cr, msg, x + w / 2, y + h / 2, 16, SUBTLE, False, "c", "m")
            return
        t, surf = fr[min(int(round(self.rt)), len(fr) - 1)]
        minx, miny, maxx, maxy = self.radar.bbox
        n = 256 * 2.0 ** self.z
        cx, cy = self._center_world()
        wx0, wx1 = (minx / R + 1) / 2 * n, (maxx / R + 1) / 2 * n
        wy0, wy1 = (1 - maxy / R) / 2 * n, (1 - miny / R) / 2 * n
        cr.save()
        cr.translate(x + w / 2 + (wx0 - cx), y + h / 2 + (wy0 - cy))
        cr.scale((wx1 - wx0) / surf.get_width(), (wy1 - wy0) / surf.get_height())
        cr.set_source_surface(surf, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_BILINEAR)
        cr.paint_with_alpha(0.88)
        cr.restore()
        if self.radar.state == "loading":
            text(cr, "mise à jour…", x + w - 12, y + 44, 11, SUBTLE, False, "r")

    def draw_marker(self, cr, x, y, w, h):
        cx, cy = self._center_world()
        mx, my = world_xy(hrdps.LON, hrdps.LAT, self.z)
        px, py = x + w / 2 + (float(mx) - cx), y + h / 2 + (float(my) - cy)
        if not (x - 20 <= px <= x + w + 20 and y - 20 <= py <= y + h + 20):
            return
        cr.set_source_rgba(1, 1, 1, 0.95); cr.arc(px, py, 6, 0, 2 * math.pi); cr.fill()
        cr.set_source_rgb(*RED); cr.arc(px, py, 4, 0, 2 * math.pi); cr.fill()
        text(cr, hrdps.LOCATION, px + 9, py - 8, 12, TEXT, True)

    def _draw_zoom_buttons(self, cr, x, y, w, h):
        bx = x + w - 44
        for k, (tag, label) in enumerate((("zoom_in", "+"), ("zoom_out", "−"), ("zoom_reset", ""))):
            by = y + 40 + k * 38
            cr.set_source_rgba(*PANEL, 0.82); rrect(cr, bx, by, 32, 32, 8); cr.fill()
            cr.set_source_rgba(*SUBTLE, 0.5); cr.set_line_width(1); rrect(cr, bx + 0.5, by + 0.5, 31, 31, 8); cr.stroke()
            if label:
                text(cr, label, bx + 16, by + 16, 20, TEXT, True, "c", "m")
            else:                                              # "recentre" target
                cr.set_source_rgb(*TEXT); cr.set_line_width(1.8)
                cr.arc(bx + 16, by + 16, 7, 0, 2 * math.pi); cr.stroke()
                for dx, dy in ((0, -11), (0, 11), (-11, 0), (11, 0)):
                    cr.move_to(bx + 16 + dx * 0.55, by + 16 + dy * 0.55); cr.line_to(bx + 16 + dx, by + 16 + dy); cr.stroke()
            self.hits.append(((bx, by, 32, 32), tag, None))

    def _draw_scale(self, cr, x, y):
        m_per_px = 40075016.686 * math.cos(math.radians(self.clat)) / (256 * 2.0 ** self.z)
        for km in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000):
            if km * 1000 / m_per_px >= 60:
                break
        L = km * 1000 / m_per_px
        cr.set_source_rgba(1, 1, 1, 0.9); cr.set_line_width(2)
        cr.move_to(x, y + 5); cr.line_to(x, y + 10); cr.line_to(x + L, y + 10); cr.line_to(x + L, y + 5); cr.stroke()
        text(cr, f"{km} km", x + 4, y - 6, 10, TEXT, True)

    def draw_arrows(self, cr, x, y, w, h, n=13):
        import numpy as np
        px = (np.arange(n) + 0.5) / n
        cx, cy = self._center_world()
        lon, _ = world_lonlat(cx + (px * w - w / 2), np.full(n, cy), self.z)
        _, lat = world_lonlat(np.full(n, cx), cy + (px * h - h / 2), self.z)
        lon2, lat2 = np.meshgrid(lon, lat)
        u, v = self._uv_t(lon2, lat2)
        cr.set_line_width(1.6); cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for r in range(n):
            for c in range(n):
                uu, vv = float(u[r, c]), float(v[r, c])
                sp = math.hypot(uu, vv)
                if sp < 0.5 or math.isnan(sp):
                    continue
                L = 9 + min(sp, 25) * 0.55
                ang = math.atan2(uu, vv)                       # compass angle of travel (0 = north)
                cr.save(); cr.translate(x + (c + 0.5) / n * w, y + (r + 0.5) / n * h); cr.rotate(ang)
                cr.set_source_rgba(1, 1, 1, 0.9)
                cr.move_to(0, L / 2); cr.line_to(0, -L / 2); cr.stroke()
                cr.move_to(-3.5, -L / 2 + 5); cr.line_to(0, -L / 2); cr.line_to(3.5, -L / 2 + 5); cr.stroke()
                cr.restore()

    def draw_legend(self, cr, x, y, w):
        import numpy as np
        if self.layer == "radar":
            n = len(RADAR_COLORS)
            sw = w / n
            cr.save(); rrect(cr, x, y, w, 8, 4); cr.clip()
            for k, c in enumerate(RADAR_COLORS):
                cr.set_source_rgb(c[0] / 255, c[1] / 255, c[2] / 255); cr.rectangle(x + k * sw, y, sw + 0.6, 8); cr.fill()
            cr.restore()
            for k in (0, 1, 3, 4, 6, 8, 9, 11, 13):
                text(cr, f"{RADAR_TICKS[k]:g}", x + (k + 0.5) * sw, y - 14, 10, SUBTLE, False, "c")
            text(cr, "mm/h", x + w, y + 10, 10, MUTED, False, "r")
            return
        stops, ticks, unit = LEGENDS[self.layer]
        lo, hi = (stops[0][0], stops[-1][0])
        if self.layer == "rt":
            vals = np.concatenate([np.linspace(0.0, 1.0, 20), np.linspace(1, 32, 60)])
        else:
            vals = np.linspace(lo, hi, 80)
        cols = colorize(self.layer, vals if self.layer != "ws" else vals / 3.6,
                        np.array([5.0] * len(vals)) if self.layer == "rt" else None)
        cr.save(); rrect(cr, x, y, w, 8, 4); cr.clip()
        cr.set_source_rgba(*PANEL, 0.8); cr.paint()
        sw = w / len(vals)
        for k, c in enumerate(cols):
            cr.set_source_rgba(c[0] / 255, c[1] / 255, c[2] / 255, max(c[3] / 255, 0.25) if self.layer != "nt" else c[3] / 255)
            cr.rectangle(x + k * sw, y, sw + 0.6, 8); cr.fill()
        cr.restore()
        pos = (lambda v: (math.log1p(v / 0.5) / math.log1p(32 / 0.5))) if self.layer == "rt" else (lambda v: (v - lo) / (hi - lo))
        for tk in ticks:
            px = x + min(max(pos(tk), 0), 1) * w
            text(cr, f"{tk:g}", px, y - 14, 10, SUBTLE, False, "c")
        text(cr, unit, x + w, y + 10, 10, MUTED, False, "r")

    # controls ------------------------------------------------------------------
    def draw_controls(self, cr):
        x0, y0, w, _ = MAP_RECT
        by = y0 + MAP_RECT[3] + 12
        bx, gap = x0, 6
        bw = (w - gap * (len(LAYERS) - 1)) / len(LAYERS)
        for key, label in LAYERS:
            on = key == self.layer
            cr.set_source_rgb(*(BLUE if on else SURFACE)); rrect(cr, bx, by, bw, 32, 9); cr.fill()
            text(cr, label, bx + bw / 2, by + 16, 14, PANEL if on else SUBTLE, on, "c", "m")
            self.hits.append(((bx, by, bw, 32), "layer", key)); bx += bw + gap
        # play/pause + speed + slider
        ty = by + 50
        cr.set_source_rgb(*SURFACE); cr.arc(x0 + 20, ty + 4, 20, 0, 2 * math.pi); cr.fill()
        cr.set_source_rgb(*TEXT)
        if self.playing:
            cr.rectangle(x0 + 13, ty - 5, 5, 18); cr.rectangle(x0 + 22, ty - 5, 5, 18); cr.fill()
        else:
            cr.move_to(x0 + 14, ty - 6); cr.line_to(x0 + 29, ty + 4); cr.line_to(x0 + 14, ty + 14); cr.close_path(); cr.fill()
        self.hits.append(((x0, ty - 16, 40, 40), "play", None))
        cr.set_source_rgb(*SURFACE); rrect(cr, x0 + 50, ty - 12, 44, 32, 8); cr.fill()
        text(cr, f"×{self.speed / 2:g}", x0 + 72, ty + 4, 13, SUBTLE, True, "c", "m")
        self.hits.append(((x0 + 50, ty - 12, 44, 32), "speed", None))
        sx, sw = x0 + 112, w - 112
        self.slider = (sx, sw)
        cr.set_source_rgb(*SURFACE); rrect(cr, sx, ty - 2, sw, 12, 6); cr.fill()
        if self.layer == "radar":
            fr = self.radar.frames if self.radar else []
            n = len(fr)
            frac = min(self.rt, n - 1) / max(n - 1, 1) if n else 0.0
            cr.set_source_rgb(*BLUE); rrect(cr, sx, ty - 2, max(12, frac * sw), 12, 6); cr.fill()
            for k in range(n):                                   # hour marks counted back from the latest frame
                ago = (fr[-1][0] - fr[k][0]).total_seconds() / 60
                if ago % 60 < 1 or ago < 1:
                    px = sx + k / max(n - 1, 1) * sw
                    cr.set_source_rgba(*TEXT, 0.5); cr.rectangle(px - 0.5, ty - 6, 1, 20); cr.fill()
                    label = "maint." if ago < 1 else f"-{int(round(ago / 60))} h"
                    if ago < 1:
                        text(cr, label, px - 3, ty + 12, 10, MUTED, False, "r")      # keep it inside the slider
                    else:
                        text(cr, label, px + 3, ty + 12, 10, MUTED)
        else:
            frac = (self.t - self.i0) / max(self.i1 - self.i0, 1)
            cr.set_source_rgb(*BLUE); rrect(cr, sx, ty - 2, max(12, frac * sw), 12, 6); cr.fill()
            for k in range(self.i0, self.i1 + 1):                 # midnight ticks
                if self.d.local[k].hour == 0:
                    px = sx + (k - self.i0) / (self.i1 - self.i0) * sw
                    cr.set_source_rgba(*TEXT, 0.5); cr.rectangle(px - 0.5, ty - 6, 1, 20); cr.fill()
                    text(cr, hrdps.fr(self.d.local[k], "%a"), px + 3, ty + 12, 10, MUTED)
        cr.set_source_rgb(*TEXT); cr.arc(sx + frac * sw, ty + 4, 9, 0, 2 * math.pi); cr.fill()
        self.hits.append(((sx - 6, ty - 14, sw + 12, 36), "slider", None))

    # charts --------------------------------------------------------------------
    def draw_charts(self, cr):
        d = self.d
        cx, cy, cw, ch = CHART_RECT
        px0, px1 = cx + 48, cx + cw - 46
        pw = px1 - px0
        i0, i1 = self.i0, self.i1
        X = lambda t: px0 + (t - i0) / (i1 - i0) * pw
        rng = range(i0, i1 + 1)
        gaps = 26
        fr = [0.24, 0.28, 0.20, 0.14, 0.14]
        usable = ch - 34 - 30 - gaps * 4
        panels, y = [], cy + 34
        for f in fr:
            panels.append((y, usable * f)); y += usable * f + gaps
        self.chart_geom = (px0, pw)
        self.hits.append(((px0, cy, pw, ch), "chart", None))

        def frame(k, title, vmin, vmax, fmt, ticks=4):
            py, ph = panels[k]
            cr.set_source_rgb(*SURFACE); rrect(cr, px0, py, pw, ph, 8); cr.fill()
            cr.save(); rrect(cr, px0, py, pw, ph, 8); cr.clip()
            for i in rng:                                          # night shading
                if not d.is_day(i):
                    cr.set_source_rgba(0, 0, 0, 0.22); cr.rectangle(X(i - 0.5), py, X(i + 0.5) - X(i - 0.5), ph); cr.fill()
            cr.set_line_width(0.6)
            for g in range(ticks + 1):
                gy = py + ph - g / ticks * ph
                cr.set_source_rgba(*MUTED, 0.25); cr.move_to(px0, gy); cr.line_to(px1, gy); cr.stroke()
            for i in rng:                                          # midnights
                if d.local[i].hour == 0:
                    cr.set_source_rgba(*MUTED, 0.6); cr.set_dash([3, 3]); cr.set_line_width(0.8)
                    cr.move_to(X(i), py); cr.line_to(X(i), py + ph); cr.stroke(); cr.set_dash([])
            cr.restore()
            for g in range(ticks + 1):
                gy = py + ph - g / ticks * ph
                text(cr, fmt(vmin + (vmax - vmin) * g / ticks), px0 - 6, gy, 10, MUTED, False, "r", "m")
            text(cr, title, px0 + 2, py - 17, 11, SUBTLE, True)
            return py, ph, (lambda v: py + ph - (v - vmin) / (vmax - vmin) * ph)

        def line(ys, Y, color, width=2, dash=None, fill=None, alpha=1.0):
            pts = [(X(i), Y(ys[i])) for i in rng if ys[i] is not None]
            if len(pts) < 2:
                return
            if fill:
                base = fill
                cr.move_to(pts[0][0], base)
                for p in pts: cr.line_to(*p)
                cr.line_to(pts[-1][0], base); cr.close_path()
                cr.set_source_rgba(*color, 0.16); cr.fill()
            cr.move_to(*pts[0])
            for p in pts[1:]: cr.line_to(*p)
            cr.set_source_rgba(*color, alpha); cr.set_line_width(width); cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.set_dash(dash or []); cr.stroke(); cr.set_dash([])

        def dot(Y, v, color):
            if v is None: return
            cr.set_source_rgb(*BG); cr.arc(X(self.t), Y(v), 5, 0, 2 * math.pi); cr.fill()
            cr.set_source_rgb(*color); cr.arc(X(self.t), Y(v), 3.5, 0, 2 * math.pi); cr.fill()

        ti = min(max(int(round(self.t)), i0), i1)

        # 1 · temperature
        series = [d.tt, d.re_, d.td]
        vals = [v for s in series for v in (s[k] for k in rng) if v is not None]
        lo, hi = math.floor(min(vals) - 1), math.ceil(max(vals) + 1)
        if hi - lo < 6: hi = lo + 6
        py, ph, Y = frame(0, "Température · ressenti · point de rosée (°C)", lo, hi, lambda v: f"{v:.0f}°")
        if lo < 0 < hi:
            cr.set_source_rgba(*SKY, 0.6); cr.set_dash([5, 4]); cr.set_line_width(1)
            cr.move_to(px0, Y(0)); cr.line_to(px1, Y(0)); cr.stroke(); cr.set_dash([])
        line(d.td, Y, TEAL, 1.4, alpha=0.9)
        line(d.re_, Y, PEACH, 1.6, [4, 3])
        line(d.tt, Y, RED, 2.6, fill=py + ph)
        dot(Y, d.tt[ti], RED)
        tmax = max(rng, key=lambda k: d.tt[k]); tmin = min(rng, key=lambda k: d.tt[k])
        for k, col, dy in ((tmax, RED, -16), (tmin, SKY, 6)):
            text(cr, f"{d.tt[k]:.0f}°", X(k), Y(d.tt[k]) + dy, 12, col, True, "c")

        # 2 · precipitation
        pk = max([d.pr[k] for k in rng] + [1.0])
        top = max(4.0, math.ceil(pk * 1.2 / 4) * 4)
        py, ph, Y = frame(1, "Précipitations (mm/h équiv. eau)  ·  probabilité (%)", 0, top, lambda v: f"{v:g}")
        bw = pw / (i1 - i0 + 1) * 0.78
        cols = {"rain": BLUE, "snow": SNOWC, "frz": RED, "pel": TEAL}
        for i in rng:
            comp = [("rain", d.rain[i]), ("snow", d.swe[i]), ("frz", d.frz[i]), ("pel", d.pel[i])]
            rest = d.pr[i] - sum(v for _, v in comp)
            if rest > 0.02:
                k = d.kind(i); k = "rain" if k in ("none", "mix") else k
                comp = [(a, v + (rest if a == k else 0)) for a, v in comp]
            yb = py + ph
            for kind, v in comp:
                if v <= 0.005: continue
                hh = min(v / top, 1) * ph
                cr.set_source_rgba(*cols[kind], 0.92); cr.rectangle(X(i) - bw / 2, yb - hh, bw, hh); cr.fill()
                yb -= hh
        pop_Y = lambda v: py + ph - v / 100 * ph
        line(d.pop, pop_Y, YELLOW, 1.6, [2, 3], alpha=0.95)
        for g in (0, 50, 100):
            text(cr, f"{g}%", px1 + 6, pop_Y(g), 10, YELLOW, False, "l", "m", 0.8)
        tot, snow = sum(d.pr[k] for k in rng), sum(d.snow_cm[k] for k in rng)
        text(cr, f"Total {tot:.1f} mm" + (f" · neige ~ {snow:.0f} cm" if snow >= 0.5 else ""), px1, py - 17, 11, SUBTLE, True, "r")
        for j, (lab, c) in enumerate((("pluie", BLUE), ("neige", SNOWC), ("verglas", RED), ("grésil", TEAL))):
            lx = px0 + 8 + j * 62
            cr.set_source_rgb(*c); cr.rectangle(lx, py + 8, 8, 8); cr.fill()
            text(cr, lab, lx + 12, py + 5, 10, MUTED)
        dot(pop_Y, d.pop[ti], YELLOW)

        # 3 · wind
        wm = max([d.gust[k] for k in rng] + [30]); wtop = math.ceil(wm / 20) * 20
        py, ph, Y = frame(2, "Vent à 10 m — rafales (km/h)", 0, wtop, lambda v: f"{v:.0f}")
        for i in rng:
            if d.gust[i] and d.gust[i] > d.ws[i] + 1:
                cr.set_source_rgba(*MAUVE, 0.30); cr.rectangle(X(i - 0.5), Y(d.gust[i]), X(i + 0.5) - X(i - 0.5), Y(d.ws[i]) - Y(d.gust[i])); cr.fill()
        line(d.gust, Y, MAUVE, 1.2, alpha=0.9)
        line(d.ws, Y, SKY, 2.4, fill=py + ph)
        for i in rng:                                              # direction arrows every 3 h
            if (i - i0) % 3 == 0:
                cr.save(); cr.translate(X(i), py + 12); cr.rotate(math.radians(d.wd[i] + 180))
                cr.set_source_rgba(*LAV, 0.95); cr.set_line_width(1.5); cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.move_to(0, 6); cr.line_to(0, -6); cr.move_to(-3, -2); cr.line_to(0, -6); cr.line_to(3, -2); cr.stroke(); cr.restore()
        dot(Y, d.ws[ti], SKY)

        # 4 · cloud cover + humidity
        py, ph, Y = frame(3, "Nuages (zone) · humidité (ligne) %", 0, 100, lambda v: f"{v:.0f}", 2)
        line(d.nt, Y, SUBTLE, 1.4, fill=py + ph, alpha=0.8)
        line(d.hr, Y, SKY, 1.8)
        dot(Y, d.nt[ti], SUBTLE)

        # 5 · pressure + UV
        pv = [d.slp[k] for k in rng if d.slp[k] is not None]
        plo, phi = math.floor(min(pv) / 2) * 2 - 2, math.ceil(max(pv) / 2) * 2 + 2
        py, ph, Y = frame(4, "Pression (hPa) · indice UV (barres)", plo, phi, lambda v: f"{v:.0f}", 2)
        uvm = max([d.uv[k] for k in rng] + [4])
        for i in rng:
            if d.uv[i] > 0.05:
                hh = d.uv[i] / uvm * ph * 0.8
                cr.set_source_rgba(*YELLOW, 0.55); cr.rectangle(X(i) - bw / 2, py + ph - hh, bw, hh); cr.fill()
        text(cr, f"UV max {max(d.uv[k] for k in rng):.1f}", px1, py - 16, 10, YELLOW, False, "r", alpha=0.85)
        line(d.slp, Y, GREEN, 2)
        dot(Y, d.slp[ti], GREEN)

        # x axis labels
        ay = panels[-1][0] + panels[-1][1] + 5
        for i in rng:
            h = d.local[i].hour
            if h % 3 == 0:
                text(cr, f"{h:02d}h", X(i), ay, 10, MUTED, False, "c")
            if h == 0:
                text(cr, hrdps.fr(d.local[i], "%a %d"), X(i) + 3, ay + 13, 10, SUBTLE, True)
        text(cr, hrdps.fr(d.local[i0], "%a %d"), px0 + 3, ay + 13, 10, SUBTLE, True)
        # cursor
        cr.set_source_rgba(*TEXT, 0.85); cr.set_line_width(1.2)
        cr.move_to(X(self.t), cy + 11); cr.line_to(X(self.t), panels[-1][0] + panels[-1][1]); cr.stroke()
        lbl = hrdps.fr(d.local[ti], "%a %Hh")
        bwid = 62
        cr.set_source_rgb(*TEXT); rrect(cr, min(max(X(self.t) - bwid / 2, px0), px1 - bwid), cy - 6, bwid, 17, 5); cr.fill()
        text(cr, lbl, min(max(X(self.t), px0 + bwid / 2), px1 - bwid / 2), cy + 2.5, 11, PANEL, True, "c", "m")

    # details strip -------------------------------------------------------------
    def draw_details(self, cr):
        d = self.d
        i = min(max(int(round(self.t)), self.i0), self.i1)
        y0 = 808
        cr.set_source_rgb(*SURFACE); rrect(cr, M, y0 - 6, W - 2 * M, 116, 10); cr.fill()
        vis = d.fogvis[i]
        gust = d.gust[i]
        cells = [
            ("Température", f"{d.tt[i]:.1f} °C"), ("Ressenti", f"{d.re_[i]:.1f} °C" if d.re_[i] is not None else "—"),
            ("Point de rosée", f"{d.td[i]:.1f} °C"), ("Humidité", f"{d.hr[i]:.0f} %"),
            ("Vent", f"{d.ws[i]:.0f} km/h {compass(d.wd[i])}"), ("Rafales", f"{gust:.0f} km/h"),
            ("Pression", f"{d.slp[i]:.1f} hPa"), ("Nuages", f"{d.nt[i]:.0f} %"),
            ("Visibilité (brume)", ">10 km" if vis is None or vis >= 10000 else f"{vis/1000:.1f} km"),
            ("Indice UV", f"{d.uv[i]:.1f}"), ("Couche limite", f"{d.pbl[i]:.0f} m"), ("CAPE", f"{d.cape[i]:.0f} J/kg"),
            ("Précip. (heure)", f"{d.pr[i]:.2f} mm" + (f" · {d.pint[i].lower()}" if d.pint[i] else "")),
            ("Neige ~", f"{d.snow_cm[i]:.1f} cm" + (f" · sol {d.sd[i]:.0f} cm" if d.sd[i] >= 0.5 else "")),
            ("Type", d.ptype[i] + (f" · {d.pchar[i].lower()}" if d.pchar[i] and d.ptype[i] != "Aucune" else "")),
            ("Probabilité précip.", f"{d.pop[i]:.0f} %"),
            ("Limite pluie/neige", f"{d.snowlvl[i]:.0f} m" if d.snowlvl[i] is not None else "—"),
            ("Orage", f"{d.tsprob[i]:.0f} %"),
        ]
        cw = (W - 2 * M - 20) / 6
        for k, (lab, val) in enumerate(cells):
            cx, cy = M + 10 + (k % 6) * cw, y0 + 22 + (k // 6) * 29
            text(cr, lab, cx, cy, 10, MUTED)
            text(cr, val, cx, cy + 11, 14, TEXT, True)
        off = i - d.now_index()
        text(cr, hrdps.fr(d.local[i], "%A %d %B · %Hh"), M + 10, y0 - 1, 13, BLUE, True)
        text(cr, "maintenant" if off == 0 else f"dans {off} h" if off > 0 else f"il y a {-off} h", M + 300, y0 + 1, 11, MUTED)


def render_png(path, data, layer="rt", hour=None, maps=None, tiles=None, wait=False):
    """Render one still image. With `tiles` (a TileCache) and wait=True the base map is downloaded first."""
    v = View(data, tiles)
    v.layer = layer
    if hour is not None:
        v.t = float(min(max(data.now_index() + hour, v.i0), v.i1))
    v.set_maps(maps)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    if wait:
        v.draw(cairo.Context(surf), W, H)                   # first pass only schedules the downloads
        if tiles is not None:
            tiles.wait()
        if v.radar is not None:
            v.radar.wait()
        v.rt = 1e9                                 # radar still: latest frame
    cr = cairo.Context(surf)
    v.draw(cr, W, H)
    surf.write_to_png(path)
