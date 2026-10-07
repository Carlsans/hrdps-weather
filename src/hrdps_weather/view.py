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

LAYERS = [("rt", "💧 Précip."), ("tt", "🌡 Temp."), ("ws", "💨 Vent"), ("nt", "☁ Nuages")]
LAYER_TITLE = {"rt": "Précipitations (taux)", "tt": "Température à 2 m", "ws": "Vent à 10 m", "nt": "Couverture nuageuse"}

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
    def __init__(self, data):
        self.d = data
        self.i0, self.i1 = data.now_index(), data.n - 1
        self.t = float(self.i0)
        self.layer, self.playing, self.speed = "rt", True, 2.0       # hours per second
        self.hits = []
        self.maps = None; self.base = None; self.proj = None; self.bounds = None
        self.status = ""                                           # message shown over the map

    # -- maps ----------------------------------------------------------------
    def set_maps(self, maps, base_img):
        import numpy as np
        if maps:
            grids, bounds, _ = maps
            w, s, e, n = bounds
            ys = (np.arange(MP) + 0.5) / MP
            m_n, m_s = merc(n), merc(s)
            lat = np.degrees(np.arctan(np.sinh(m_n + (m_s - m_n) * ys)))
            lon = w + (e - w) * (np.arange(MP) + 0.5) / MP
            out = {}
            for k, arr in grids.items():
                T, ny, nx = arr.shape
                R, Cc = np.meshgrid((n - lat) / (n - s) * ny - 0.5, (lon - w) / (e - w) * nx - 0.5, indexing="ij")
                out[k] = np.stack([_bilinear(np.nan_to_num(arr[i]), R, Cc) for i in range(T)]).astype(np.float32)
            out["rt"] *= 3600                                  # kg/m²/s → mm/h
            wd = np.radians(out["wd"])
            self.u, self.v = -out["ws"] * np.sin(wd), -out["ws"] * np.cos(wd)   # wind blows *toward* (east, north)
            self.bounds = bounds
            self.proj = out                       # assigned last: the GTK thread may already be drawing
        if base_img is not None:
            arr = np.array(base_img.convert("RGBA"))[..., [2, 1, 0, 3]].copy()
            h, w = arr.shape[:2]
            self._base_buf = bytearray(arr.tobytes())
            self.base = cairo.ImageSurface.create_for_data(self._base_buf, cairo.FORMAT_ARGB32, w, h, w * 4)
        self.status = ""

    def _frame(self, k):
        i = int(math.floor(self.t)); f = self.t - i
        j = min(i + 1, self.i1)
        a = self.proj[k]
        return a[i] * (1 - f) + a[j] * f

    # -- interaction ---------------------------------------------------------
    def hit(self, x, y):
        for (rx, ry, rw, rh), tag, arg in reversed(self.hits):
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return tag, arg
        return None, None

    def t_from_x(self, x, x0, w):
        return min(max(self.i0 + (x - x0) / w * (self.i1 - self.i0), self.i0), self.i1)

    def tick(self, dt):
        if self.playing:
            self.t += dt * self.speed
            if self.t >= self.i1:
                self.t = float(self.i0)

    # -- drawing -------------------------------------------------------------
    def draw(self, cr, w, h):
        self.hits = []
        cr.save(); cr.scale(w / W, h / H)
        cr.set_source_rgb(*BG); cr.paint()
        self.draw_header(cr); self.draw_map(cr); self.draw_controls(cr)
        self.draw_charts(cr); self.draw_details(cr)
        cr.restore()

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

    # map -----------------------------------------------------------------------
    def draw_map(self, cr):
        x, y, w, h = MAP_RECT
        cr.save(); rrect(cr, x, y, w, h, 12); cr.clip()
        cr.set_source_rgb(*PANEL); cr.paint()
        if self.base is not None:
            cr.save(); cr.translate(x, y); cr.scale(w / self.base.get_width(), h / self.base.get_height())
            cr.set_source_surface(self.base, 0, 0); cr.paint(); cr.restore()
        if self.proj is not None:
            lay = self.layer
            a = self._frame(lay)
            surf, buf = to_surface(colorize(lay, a, self._frame("tt") if lay == "rt" else None))
            cr.save(); cr.translate(x, y); cr.scale(w / MP, h / MP)
            cr.set_source_surface(surf, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_BILINEAR); cr.paint(); cr.restore()
            if lay == "ws":
                self.draw_arrows(cr, x, y, w, h)
            self.draw_marker(cr, x, y, w, h)
        else:
            text(cr, self.status or "Cartes en téléchargement…", x + w / 2, y + h / 2, 16, SUBTLE, False, "c", "m")
        cr.restore()
        # caption + legend
        cr.save(); rrect(cr, x, y, w, 30, 12); cr.clip()
        cr.set_source_rgba(*PANEL, 0.72); cr.paint(); cr.restore()
        t = self.d.local[min(int(round(self.t)), self.i1)]
        text(cr, LAYER_TITLE[self.layer], x + 12, y + 6, 14, TEXT, True)
        text(cr, hrdps.fr(t, "%a %d %b · %Hh"), x + w - 12, y + 6, 14, SUBTLE, True, "r")
        self.draw_legend(cr, x + 12, y + h - 32, w - 24)
        text(cr, "© contributeurs OpenStreetMap · données HRDPS © ECCC", x + 12, y + h - 15, 9, SUBTLE, False, "l", alpha=0.8)

    def draw_marker(self, cr, x, y, w, h):
        bw, bs, be, bn = self.bounds
        fx = (hrdps.LON - bw) / (be - bw)
        fy = (merc(bn) - merc(hrdps.LAT)) / (merc(bn) - merc(bs))
        px, py = x + fx * w, y + fy * h
        cr.set_source_rgba(1, 1, 1, 0.95); cr.arc(px, py, 6, 0, 2 * math.pi); cr.fill()
        cr.set_source_rgb(*RED); cr.arc(px, py, 4, 0, 2 * math.pi); cr.fill()
        text(cr, hrdps.LOCATION, px + 9, py - 8, 12, TEXT, True)

    def draw_arrows(self, cr, x, y, w, h, n=13):
        i = int(math.floor(self.t)); f = self.t - i; j = min(i + 1, self.i1)
        u = self.u[i] * (1 - f) + self.u[j] * f
        v = self.v[i] * (1 - f) + self.v[j] * f
        step = MP / n
        cr.set_line_width(1.6); cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for r in range(n):
            for c in range(n):
                gy, gx = int((r + 0.5) * step), int((c + 0.5) * step)
                uu, vv = float(u[gy, gx]), float(v[gy, gx])
                sp = math.hypot(uu, vv)
                if sp < 0.5:
                    continue
                L = 9 + min(sp, 25) * 0.55
                ang = math.atan2(uu, vv)                # compass angle of travel (0 = north)
                cx, cy = x + (gx + 0.5) / MP * w, y + (gy + 0.5) / MP * h
                cr.save(); cr.translate(cx, cy); cr.rotate(ang)
                cr.set_source_rgba(1, 1, 1, 0.9)
                cr.move_to(0, L / 2); cr.line_to(0, -L / 2); cr.stroke()
                cr.move_to(-3.5, -L / 2 + 5); cr.line_to(0, -L / 2); cr.line_to(3.5, -L / 2 + 5); cr.stroke()
                cr.restore()

    def draw_legend(self, cr, x, y, w):
        stops, ticks, unit = LEGENDS[self.layer]
        import numpy as np
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
        bx = x0
        for key, label in LAYERS:
            bw = 124
            on = key == self.layer
            cr.set_source_rgb(*(BLUE if on else SURFACE)); rrect(cr, bx, by, bw, 32, 9); cr.fill()
            text(cr, label, bx + bw / 2, by + 16, 14, PANEL if on else SUBTLE, on, "c", "m")
            self.hits.append(((bx, by, bw, 32), "layer", key)); bx += bw + 8
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
        frac = (self.t - self.i0) / max(self.i1 - self.i0, 1)
        cr.set_source_rgb(*BLUE); rrect(cr, sx, ty - 2, max(12, frac * sw), 12, 6); cr.fill()
        for k in range(self.i0, self.i1 + 1):                     # midnight ticks
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


def render_png(path, data, layer="rt", hour=None, maps=None, base=None):
    v = View(data)
    v.layer = layer
    if hour is not None:
        v.t = float(min(max(data.now_index() + hour, v.i0), v.i1))
    v.set_maps(maps, base)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    cr = cairo.Context(surf)
    v.draw(cr, W, H)
    surf.write_to_png(path)
