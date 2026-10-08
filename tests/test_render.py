import importlib

import pytest

from hrdps_weather import view


@pytest.mark.parametrize("layer", ["rt", "tt", "ws", "nt"])
def test_render_without_maps(tmp_path, data, layer):
    out = tmp_path / f"{layer}.png"
    view.render_png(str(out), data, layer, 6)
    assert out.stat().st_size > 20_000                 # a real image, not a blank canvas


def synthetic_maps(T):
    import numpy as np
    rng = np.random.default_rng(0)

    def grids(ny, nx):
        g = {k: rng.random((T, ny, nx), dtype=np.float32) for k in ("rt", "tt", "ws", "wd", "nt")}
        g["wd"] *= 360
        g["nt"] *= 100
        g["tt"] = g["tt"] * 30 - 10
        return g
    from hrdps_weather import hrdps
    return {"region": (grids(40, 40), hrdps.map_bounds(hrdps.REGION_ZOOM)),
            "wide": (grids(30, 40), hrdps.map_bounds(hrdps.WIDE_ZOOM)), "ref": "synthetic"}


@pytest.mark.parametrize("layer", ["rt", "tt", "ws", "nt"])
def test_render_with_synthetic_maps(tmp_path, data, layer):
    out = tmp_path / f"maps_{layer}.png"
    view.render_png(str(out), data, layer, 3, synthetic_maps(data.n))
    assert out.stat().st_size > 20_000


def test_toy_text_backend(monkeypatch, tmp_path, data):
    """The Windows path: no PyGObject, cairo's built-in text API."""
    monkeypatch.setattr(view, "HAVE_PANGO", False)
    out = tmp_path / "toy.png"
    view.render_png(str(out), data, "rt", 0)
    assert out.stat().st_size > 20_000


def test_bilinear_identity():
    import numpy as np
    a = np.arange(12, dtype=float).reshape(3, 4)
    R, C = np.meshgrid(np.arange(3.0), np.arange(4.0), indexing="ij")
    assert np.allclose(view._bilinear(a, R, C), a)
    assert np.isclose(view._bilinear(a, np.array([0.5]), np.array([0.5]))[0], 2.5)
