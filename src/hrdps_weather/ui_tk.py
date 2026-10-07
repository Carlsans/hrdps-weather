"""
Cross-platform window (Tkinter + Pillow) showing the cairo-rendered dashboard.
Used on Windows (and anywhere GTK4 is not installed).

Keys : space play/pause · ←/→ ±1 h · ↑/↓ speed · 1-4 map layer · Home = now · Esc/q close
Mouse: layer buttons, drag the slider, hover/click a chart to scrub through time.
"""
import threading
import time
import tkinter as tk

import cairo
from PIL import Image, ImageTk

from . import hrdps
from . import view as wv

FRAME_MS = 66            # ~15 fps target; the next frame is scheduled after the render


class WeatherWindow:
    def __init__(self, master):
        self.top = tk.Toplevel(master, class_="Hrdps-weather")
        self.top.title("Météo — HRDPS")
        try:                                             # X11/XWayland: tiling WMs float dialogs by default
            self.top.attributes("-type", "dialog")
        except tk.TclError:
            pass
        self.top.configure(bg="#1e1e2e")
        sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
        self.scale = max(0.5, min(sw * 0.92 / wv.W, sh * 0.86 / wv.H, 1.25))
        self.w, self.h = int(wv.W * self.scale), int(wv.H * self.scale)
        self.label = tk.Label(self.top, bd=0, highlightthickness=0, bg="#1e1e2e")
        self.label.pack()
        self.top.resizable(False, False)

        self.view = None
        self.loading = False
        self.drag = None
        self._photo = None
        self._last = time.monotonic()
        self._closed = False
        self._lock = threading.Lock()

        self.label.bind("<Button-1>", self.on_press)
        self.label.bind("<B1-Motion>", self.on_drag)
        self.label.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
        self.label.bind("<Motion>", self.on_motion)
        self.top.bind("<Key>", self.on_key)
        self.top.bind("<Escape>", lambda e: self.close())
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.focus_force()

        hrdps.ensure_fresh(force=True)           # cheap when the model run is unchanged
        self._make_view()
        self._frame()

    # ── data ─────────────────────────────────────────────────────────────────
    def _make_view(self):
        d = hrdps.load()
        if d is not None:
            self.view = wv.View(d)
            self.view.status = "Cartes en téléchargement…"

    def _start_maps(self):
        v = self.view
        if v is None or self.loading or v.proj is not None or not hrdps.maps_ready(v.d):
            return
        self.loading = True

        def work():
            try:
                maps = hrdps.load_maps()
                try:
                    base = hrdps.basemap()
                except Exception:
                    base = None
                if maps:
                    v.set_maps(maps, base)
            finally:
                self.loading = False
        threading.Thread(target=work, daemon=True).start()

    # ── drawing ──────────────────────────────────────────────────────────────
    def _frame(self):
        if self._closed:
            return
        t0 = time.monotonic()
        if self.view is None:
            self._make_view()
        else:
            if self.view.proj is None:
                self._start_maps()
            self.view.tick(t0 - self._last)
        self._last = t0
        self._render()
        wait = max(10, FRAME_MS - int((time.monotonic() - t0) * 1000))
        self.top.after(wait, self._frame)

    def _render(self):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.w, self.h)
        cr = cairo.Context(surf)
        if self.view is None:
            cr.set_source_rgb(*wv.BG); cr.paint()
            wv.text(cr, "Téléchargement des données HRDPS… (~1 minute la première fois)",
                    self.w / 2, self.h / 2, 20 * self.scale, wv.SUBTLE, False, "c", "m")
        else:
            self.view.draw(cr, self.w, self.h)
        surf.flush()
        img = Image.frombuffer("RGBA", (self.w, self.h), bytes(surf.get_data()), "raw", "BGRA", 0, 1)
        self._photo = ImageTk.PhotoImage(img)
        self.label.configure(image=self._photo)

    def render_once(self):
        """Render a single frame (used by tests)."""
        self._render()

    # ── interaction (coordinates are converted back to the 1400×924 design space) ──
    def _xy(self, e):
        return e.x / self.scale, e.y / self.scale

    def _axis(self, tag):
        return self.view.slider if tag == "slider" else self.view.chart_geom

    def on_press(self, e):
        v = self.view
        if v is None:
            return
        x, y = self._xy(e)
        tag, arg = v.hit(x, y)
        if tag == "layer":
            v.layer = arg
        elif tag == "play":
            v.playing = not v.playing
        elif tag == "speed":
            v.speed = {2.0: 4.0, 4.0: 8.0, 8.0: 1.0, 1.0: 2.0}.get(v.speed, 2.0)
        elif tag in ("slider", "chart"):
            v.playing = False
            v.t = v.t_from_x(x, *self._axis(tag))
            self.drag = tag

    def on_drag(self, e):
        if self.view is not None and self.drag:
            self.view.t = self.view.t_from_x(self._xy(e)[0], *self._axis(self.drag))

    def on_motion(self, e):
        v = self.view
        if v is not None and not v.playing and not self.drag:
            x, y = self._xy(e)
            if v.hit(x, y)[0] == "chart":
                v.t = v.t_from_x(x, *v.chart_geom)

    def on_key(self, e):
        v, k = self.view, e.keysym
        if k in ("q", "Q"):
            self.close(); return
        if v is None:
            return
        if k == "space":
            v.playing = not v.playing
        elif k in ("Left", "Right"):
            v.playing = False
            v.t = float(min(max(round(v.t) + (1 if k == "Right" else -1), v.i0), v.i1))
        elif k in ("Up", "Down"):
            v.speed = min(max(v.speed * (2 if k == "Up" else 0.5), 1.0), 8.0)
        elif k == "Home":
            v.t = float(v.i0)
        elif k in ("1", "2", "3", "4"):
            v.layer = wv.LAYERS[int(k) - 1][0]

    def close(self):
        self._closed = True
        self.top.destroy()

    @property
    def alive(self):
        return not self._closed and bool(self.top.winfo_exists())


def run_standalone():
    """Open the window alone (no tray) — `hrdps-weather popup --tk`."""
    root = tk.Tk(className="hrdps-weather")
    root.withdraw()
    win = WeatherWindow(root)
    win.top.bind("<Destroy>", lambda e: root.quit() if e.widget is win.top else None)
    root.mainloop()
