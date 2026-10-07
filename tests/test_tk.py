import pytest

tk = pytest.importorskip("tkinter")


def test_tk_window_renders_a_frame(monkeypatch, data):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.withdraw()
    from hrdps_weather import hrdps, ui_tk
    monkeypatch.setattr(hrdps, "ensure_fresh", lambda force=False: None)
    monkeypatch.setattr(hrdps, "load", lambda: data)
    monkeypatch.setattr(hrdps, "maps_ready", lambda d=None: False)
    win = ui_tk.WeatherWindow(root)
    win.render_once()
    assert win._photo is not None
    win.on_key(type("E", (), {"keysym": "2"})())
    assert win.view.layer == "tt"
    win.close()
    root.destroy()
