"""Camera, tiles, model sampling and radar loader — all offline."""
import io
import math
import time

import numpy as np
import pytest

from hrdps_weather import hrdps, radar, tiles, view
from .test_render import synthetic_maps


def make_view(data, with_maps=True):
    v = view.View(data)
    if with_maps:
        v.set_maps(synthetic_maps(data.n))
    return v


def test_zoom_keeps_anchor_fixed(data):
    v = make_view(data, False)
    mx, my, mw, mh = view.MAP_RECT
    ax, ay = mx + mw * 0.8, my + mh * 0.3
    cx, cy = v._center_world()
    before = view.world_lonlat(cx + (ax - mx - mw / 2), cy + (ay - my - mh / 2), v.z)
    v.zoom_by(+1.3, ax, ay)
    cx, cy = v._center_world()
    after = view.world_lonlat(cx + (ax - mx - mw / 2), cy + (ay - my - mh / 2), v.z)
    assert math.isclose(float(before[0]), float(after[0]), abs_tol=1e-6)
    assert math.isclose(float(before[1]), float(after[1]), abs_tol=1e-6)


def test_zoom_is_clamped_and_reset(data):
    v = make_view(data, False)
    v.zoom_by(+50); assert v.z == view.MAX_Z
    v.zoom_by(-50); assert v.z == view.MIN_Z
    v.pan_to(0, 0)
    v.begin_pan(); v.pan_to(120, -80)
    assert (v.clon, v.clat) != (hrdps.LON, hrdps.LAT)
    v.reset_camera()
    assert (v.clon, v.clat, v.z) == (hrdps.LON, hrdps.LAT, view.DEFAULT_Z)


def test_pan_moves_opposite_to_drag(data):
    v = make_view(data, False)
    v.begin_pan(); v.pan_to(100, 0)          # drag the map to the right → centre moves west
    assert v.clon < hrdps.LON


def test_world_roundtrip():
    wx, wy = view.world_xy(-71.2, 46.8, 8.3)
    lon, lat = view.world_lonlat(wx, wy, 8.3)
    assert math.isclose(float(lon), -71.2, abs_tol=1e-9) and math.isclose(float(lat), 46.8, abs_tol=1e-9)


def test_sampling_prefers_region_and_falls_back_to_wide(data):
    v = make_view(data)
    (rg, rb), (wg, wb) = v.maps["region"], v.maps["wide"]
    inside = (np.array([[hrdps.LON]]), np.array([[hrdps.LAT]]))
    outside = (np.array([[rb[0] - 3.0]]), np.array([[hrdps.LAT]]))      # west of the region block, inside wide
    far = (np.array([[-30.0]]), np.array([[hrdps.LAT]]))                # outside both
    assert not np.isnan(v._sample("tt", 0, *inside)).any()
    assert not np.isnan(v._sample("tt", 0, *outside)).any()
    assert np.isnan(v._sample("tt", 0, *far)).all()


def test_render_zoomed_in_and_out(tmp_path, data):
    for z in (view.MIN_Z, view.MAX_Z):
        v = make_view(data)
        v.z = z
        from cairo import ImageSurface, Context, FORMAT_ARGB32
        surf = ImageSurface(FORMAT_ARGB32, view.W, view.H)
        v.draw(Context(surf), view.W, view.H)
        assert getattr(v, "errors", 0) == 0


def test_tile_cache_disk_and_ancestor_fallback(tmp_path):
    from PIL import Image
    tc = tiles.TileCache(tmp_path, network=False)
    p = tc._path(7, 38, 45)
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (256, 256), (40, 40, 60)).save(p)
    assert tc.get(7, 38, 45) is not None                       # from disk
    best = tc.get_best(8, 77, 90)                              # child of (7, 38, 45), not on disk
    assert best is not None and best[1:] == (7, 38, 45)        # falls back to the ancestor
    assert tc.get_best(8, 200, 200) is None                    # nothing cached at all


def test_darken_is_dark_and_consistent():
    from PIL import Image
    land = tiles.darken(Image.new("RGB", (8, 8), (242, 239, 233)))
    assert max(land.getpixel((0, 0))) < 80


def test_radar_loader_with_fake_network(monkeypatch):
    from PIL import Image
    png = io.BytesIO()
    Image.new("RGBA", (16, 16), (0, 200, 0, 180)).save(png, format="PNG")
    caps = (b'<Layer><Name>RADAR_1KM_RRAI</Name><Dimension name="time" units="ISO8601">'
            b'2026-10-08T10:48:00Z/2026-10-08T13:48:00Z/PT6M</Dimension></Layer>')
    monkeypatch.setattr(radar, "_get", lambda url, timeout=30: caps if "GetCapabilities" in url else png.getvalue())
    times = radar.frame_times()
    assert len(times) == 31 and (times[-1] - times[0]).total_seconds() == 3 * 3600
    rl = radar.RadarLoader()
    bbox = (-8000000.0, 5600000.0, -7800000.0, 5800000.0)
    rl.request(bbox, 8.0, 560)
    rl.wait(20)
    assert len(rl.frames) == 31 and rl.state == "idle"
    assert rl.bbox[0] <= bbox[0] and rl.bbox[2] >= bbox[2]      # loaded area includes the view (with margin)
    before = rl.version
    rl.request(bbox, 8.0, 560)                                  # covered: no reload
    time.sleep(0.8)
    assert rl.version == before


def test_radar_layer_draws_and_slider(tmp_path, data, monkeypatch):
    from PIL import Image
    from cairo import ImageSurface, Context, FORMAT_ARGB32
    v = make_view(data)
    v.layer = "radar"
    v.radar = radar.RadarLoader()
    surf16 = tiles.pil_to_surface(Image.new("RGBA", (16, 16), (0, 200, 0, 180)))
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 10, 8, 13, 48, tzinfo=timezone.utc)
    v.radar.frames = [(now - timedelta(minutes=6 * (30 - k)), surf16) for k in range(31)]
    v.radar.bbox = v._view_meters()
    v.radar.zoom, v.radar.loaded_at = v.z, time.time()
    s = ImageSurface(FORMAT_ARGB32, view.W, view.H)
    v.draw(Context(s), view.W, view.H)
    assert getattr(v, "errors", 0) == 0
    v.slider_set(v.slider[0] + v.slider[1])                      # far right = latest frame
    assert round(v.rt) == 30
    assert "dernière image" in v._time_label()
    v.tick(0.5)                                                  # radar advances on its own clock, model time untouched
    assert v.t == float(v.i0)
