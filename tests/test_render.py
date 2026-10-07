import importlib

import pytest

from hrdps_weather import view


@pytest.mark.parametrize("layer", ["rt", "tt", "ws", "nt"])
def test_render_without_maps(tmp_path, data, layer):
    out = tmp_path / f"{layer}.png"
    view.render_png(str(out), data, layer, 6)
    assert out.stat().st_size > 20_000                 # a real image, not a blank canvas


def test_render_with_synthetic_maps(tmp_path, data):
    import numpy as np
    from PIL import Image
    T, ny, nx = data.n, 40, 40
    rng = np.random.default_rng(0)
    grids = {k: rng.random((T, ny, nx), dtype=np.float32) for k in ("rt", "tt", "ws", "wd", "nt")}
    grids["wd"] *= 360
    grids["nt"] *= 100
    maps = (grids, (-73.1, 45.1, -68.9, 48.0), "synthetic")
    out = tmp_path / "maps.png"
    view.render_png(str(out), data, "ws", 3, maps, Image.new("RGB", (768, 768), (30, 30, 46)))
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
