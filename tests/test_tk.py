from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture
def win(monkeypatch, data):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.withdraw()
    from hrdps_weather import hrdps, ui_tk
    monkeypatch.setenv("HRDPS_NO_REFRESH", "1")            # no tile downloads either
    monkeypatch.setattr(hrdps, "ensure_fresh", lambda force=False: None)
    monkeypatch.setattr(hrdps, "load", lambda: data)
    monkeypatch.setattr(hrdps, "maps_ready", lambda d=None: False)
    w = ui_tk.WeatherWindow(root)
    w.render_once()                                         # populates the hit-boxes
    yield w
    if w.alive:
        w.close()
    root.destroy()


def ev(win, x, y, **kw):
    """A Tk-like event at design-space coordinates (x, y)."""
    return SimpleNamespace(x=x * win.scale, y=y * win.scale, **kw)


def centre(win, tag):
    (x, y, w, h), _, _ = next(hb for hb in win.view.hits if hb[1] == tag)
    return x + w / 2, y + h / 2


def test_tk_window_renders_a_frame(win):
    assert win._photo is not None
    win.on_key(SimpleNamespace(keysym="2"))
    assert win.view.layer == "tt"
    win.on_key(SimpleNamespace(keysym="5"))
    assert win.view.layer == "radar"


def test_zoom_buttons_wheel_and_double_click(win):
    v, z0 = win.view, win.view.z
    win.on_press(ev(win, *centre(win, "zoom_in")))
    assert v.z > z0
    z1 = v.z
    win.on_press(ev(win, *centre(win, "zoom_out")))
    assert v.z < z1
    mx, my, mw, mh = __import__("hrdps_weather.view", fromlist=["x"]).MAP_RECT
    z2 = v.z
    win.on_wheel(ev(win, mx + 100, my + 100, delta=120))
    assert v.z > z2
    win.on_wheel(ev(win, mx + 100, my + 100, delta=0), -1)
    assert v.z <= z2 + 1e-9
    z3 = v.z
    win.on_double(ev(win, mx + 200, my + 200))
    assert v.z > z3
    win.on_press(ev(win, *centre(win, "zoom_reset")))
    assert v.z == pytest.approx(7.55)


def test_drag_pans_the_map(win):
    v = win.view
    mx, my, mw, mh = __import__("hrdps_weather.view", fromlist=["x"]).MAP_RECT
    lon0, lat0 = v.clon, v.clat
    win.on_press(ev(win, mx + 300, my + 300))               # a point away from the zoom buttons
    assert win.drag == "map"
    win.on_drag(ev(win, mx + 360, my + 340))
    assert (v.clon, v.clat) != (lon0, lat0)
    win.on_release = None
    win.drag = None
