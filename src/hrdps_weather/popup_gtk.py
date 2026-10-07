#!/usr/bin/env python3
"""
Weather popup (GTK4) — HRDPS only.
Animated precipitation / temperature / wind / cloud map, synchronised charts
and a detail panel for the selected hour.

  hrdps-weather popup           open the popup
  hrdps-weather popup --kill    close a running popup

Keys : space play/pause · ←/→ ±1 h · ↑/↓ speed · 1-4 map layer · Home = now · Esc/q close
Mouse: click a layer button, drag the slider or hover/click a chart to scrub through time.
"""
import os, signal, tempfile, threading
from pathlib import Path

from . import hrdps

PIDFILE = Path(tempfile.gettempdir()) / "waybar-weather-popup.pid"

def kill_existing():
    if PIDFILE.exists():
        try:
            os.kill(int(PIDFILE.read_text()), signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            pass
        PIDFILE.unlink(missing_ok=True)

def load_maps_into(view):
    """Heavy part (basemap download, reprojection) — runs in a worker thread."""
    maps = hrdps.load_maps()
    try:
        base = hrdps.basemap()
    except Exception:
        base = None
    if maps:
        view.set_maps(maps, base)

def run_gtk():
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, GLib, Gdk
    from . import view as wv

    hrdps.ensure_fresh(force=True)               # cheap if the run is unchanged
    app = Gtk.Application(application_id="com.waybar.weather-popup")
    state = {"view": None, "loading": False, "drag": None, "area": None}

    def make_view():
        d = hrdps.load()
        if d is None:
            return None
        v = wv.View(d)
        v.status = "Cartes en téléchargement…"
        return v

    def redraw():
        if state["area"] is not None:
            state["area"].queue_draw()

    def start_maps():
        v = state["view"]
        if v is None or state["loading"] or v.proj is not None or not hrdps.maps_ready(v.d):
            return
        state["loading"] = True
        def work():
            try:
                load_maps_into(v)
            finally:
                state["loading"] = False
                GLib.idle_add(redraw)
        threading.Thread(target=work, daemon=True).start()

    def on_activate(app):
        win = Gtk.ApplicationWindow(application=app)
        win.set_title("Météo")
        win.set_decorated(False)
        win.set_resizable(False)

        area = Gtk.DrawingArea()
        area.set_content_width(wv.W)
        area.set_content_height(wv.H)
        win.set_child(area)
        state["area"] = area

        def draw(_a, cr, w, h):
            v = state["view"]
            if v is None:
                cr.set_source_rgb(*wv.BG); cr.paint()
                wv.text(cr, "Téléchargement des données HRDPS…", w / 2, h / 2, 22, wv.SUBTLE, False, "c", "m")
                wv.text(cr, "(≈1 minute la première fois)", w / 2, h / 2 + 34, 14, wv.MUTED, False, "c", "m")
            else:
                v.draw(cr, w, h)
        area.set_draw_func(draw)

        # ── interaction ───────────────────────────────────────────────────────
        def axis(v, tag):
            return v.slider if tag == "slider" else v.chart_geom

        def on_press(g, n, x, y):
            v = state["view"]
            if v is None:
                return
            tag, arg = v.hit(x, y)
            if tag == "layer":
                v.layer = arg
            elif tag == "play":
                v.playing = not v.playing
            elif tag == "speed":
                v.speed = {2.0: 4.0, 4.0: 8.0, 8.0: 1.0, 1.0: 2.0}.get(v.speed, 2.0)
            elif tag in ("slider", "chart"):
                v.playing = False
                v.t = v.t_from_x(x, *axis(v, tag))
                state["drag"] = tag
            redraw()
        click = Gtk.GestureClick(); click.connect("pressed", on_press); area.add_controller(click)

        def on_motion(c, x, y):
            v = state["view"]
            if v is not None and not v.playing and v.hit(x, y)[0] == "chart":   # hover scrubs while paused
                v.t = v.t_from_x(x, *v.chart_geom); redraw()
        mot = Gtk.EventControllerMotion(); mot.connect("motion", on_motion); area.add_controller(mot)

        drag = Gtk.GestureDrag()
        def on_drag(g, dx, dy):
            v = state["view"]
            if v is None or not state["drag"]:
                return
            ok, sx, sy = g.get_start_point()
            v.t = v.t_from_x(sx + dx, *axis(v, state["drag"])); redraw()
        drag.connect("drag-update", on_drag)
        drag.connect("drag-end", lambda *_: state.update(drag=None))
        area.add_controller(drag)

        def on_key(c, keyval, keycode, mods):
            v = state["view"]
            name = Gdk.keyval_name(keyval)
            if name in ("Escape", "q"):
                app.quit(); return True
            if v is None:
                return True
            if name == "space":
                v.playing = not v.playing
            elif name in ("Left", "Right"):
                v.playing = False
                v.t = float(min(max(round(v.t) + (1 if name == "Right" else -1), v.i0), v.i1))
            elif name in ("Up", "Down"):
                v.speed = min(max(v.speed * (2 if name == "Up" else 0.5), 1.0), 8.0)
            elif name == "Home":
                v.t = float(v.i0)
            elif name in ("1", "2", "3", "4"):
                v.layer = wv.LAYERS[int(name) - 1][0]
            redraw(); return True
        kc = Gtk.EventControllerKey(); kc.connect("key-pressed", on_key); win.add_controller(kc)

        # close when the window loses focus (as the previous popup did)
        win.connect("notify::is-active", lambda w, _: app.quit() if not w.props.is_active else None)

        # ── animation + background loading ────────────────────────────────────
        def tick():
            v = state["view"]
            if v is None:
                state["view"] = make_view(); redraw()
            else:
                if v.proj is None:
                    start_maps()
                if v.playing:
                    v.tick(0.033); redraw()
            return True
        GLib.timeout_add(33, tick)

        state["view"] = make_view()
        start_maps()
        win.present()

    app.connect("activate", on_activate)
    app.run([])

def main(kill=False):
    if kill:
        kill_existing()
        return
    kill_existing()
    PIDFILE.write_text(str(os.getpid()))
    try:
        run_gtk()
    finally:
        PIDFILE.unlink(missing_ok=True)
