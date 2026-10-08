import os
from datetime import datetime, timezone

import pytest

from hrdps_weather import config, hrdps


def test_fr_names_ignore_locale():
    dt = datetime(2026, 10, 7, 15, tzinfo=timezone.utc)
    assert hrdps.fr(dt, "%a %d %b") == "mer 07 oct"
    assert hrdps.fr(dt, "%A %B") == "mercredi octobre"


def test_series_shape(data):
    assert data.n == hrdps.HOURS + 1
    assert len(data.tt) == len(data.pr) == len(data.local) == data.n
    assert all(v >= 0 for v in data.pr)
    assert 0 <= data.now_index() < data.n


def test_derived_values(data):
    i = data.now_index()
    assert isinstance(data.icon(i), str) and data.icon(i)
    assert data.kind(i) in {"none", "rain", "snow", "frz", "pel", "mix"}
    assert data.describe(i)
    assert isinstance(data.alerts(i), list)
    s = data.slot(data.local[i].date(), 0, 24)
    assert s is None or s["mm"] >= 0


def test_sun_elevation_sane():
    noon = datetime(2026, 6, 21, 17, tzinfo=timezone.utc)     # solar noon-ish in Québec
    midnight = datetime(2026, 12, 21, 5, tzinfo=timezone.utc)
    assert hrdps.sun_elevation(noon) > 40
    assert hrdps.sun_elevation(midnight) < 0


def test_config_defaults_and_domain_check(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    for var in ("HRDPS_LAT", "HRDPS_LON", "HRDPS_LOCATION", "HRDPS_TZ"):
        monkeypatch.delenv(var, raising=False)
    cfg = config.load()
    assert cfg["location"] == "Québec"
    monkeypatch.setenv("HRDPS_LAT", "10")            # Caribbean: outside the HRDPS domain
    with pytest.raises(ValueError):
        config.load()


def test_cache_dir_is_per_user():
    assert config.APP in str(config.cache_dir())
    assert str(config.cache_dir()).startswith(os.path.expanduser("~")) or os.environ.get("XDG_CACHE_HOME") \
        or os.environ.get("LOCALAPPDATA")


def test_lock_is_exclusive(tmp_path, monkeypatch):
    monkeypatch.setattr(hrdps, "CACHE", tmp_path)
    a, b = hrdps._Lock(tmp_path / "x.lock"), hrdps._Lock(tmp_path / "x.lock")
    assert a.acquire()
    assert not b.acquire()
    a.release()
    assert b.acquire()
    b.release()


def test_tray_open_request_roundtrip(tmp_path, monkeypatch):
    from hrdps_weather import tray
    monkeypatch.setattr(hrdps, "CACHE", tmp_path)
    assert not tray.consume_open_request()
    tray.request_open()
    assert tray.consume_open_request()
    assert not tray.consume_open_request()          # consumed once


def test_autostart_is_minimized():
    from hrdps_weather import tray
    assert tray.autostart_command().endswith("tray --minimized")


@pytest.mark.parametrize("fmt", ["waybar", "plain", "polybar", "i3blocks", "i3status-rs"])
def test_status_formats(fmt, capsys, monkeypatch, data):
    import json
    from hrdps_weather import waybar
    monkeypatch.setattr(hrdps, "ensure_fresh", lambda force=False: None)
    monkeypatch.setattr(hrdps, "load", lambda: data)
    waybar.print_status(fmt)
    out = capsys.readouterr().out.strip().splitlines()
    if fmt in ("waybar", "i3status-rs"):
        d = json.loads(out[0])
        assert d["text"] if fmt == "i3status-rs" else d["tooltip"]
    elif fmt == "i3blocks":
        assert len(out) == 3 and out[0] and out[1]
    else:
        assert out and "°" in out[0]


def test_gaps_are_interpolated_not_fatal(data, tmp_path):
    """A failed request leaves a hole in the series: charts must still render (regression: TypeError on max())."""
    import copy
    from hrdps_weather import view
    raw = copy.deepcopy(data.raw)
    for k in ("tt", "td", "hr", "ws", "wd", "slp", "nt"):
        raw["series"][k][5] = None
        raw["series"][k][0] = None
        raw["series"][k][-1] = None
    d = hrdps.Data(raw)
    assert all(v is not None for v in d.tt + d.td + d.hr + d.ws + d.wd + d.slp + d.nt)
    assert min(d.tt[4], d.tt[6]) <= d.tt[5] <= max(d.tt[4], d.tt[6])
    out = tmp_path / "gap.png"
    view.render_png(str(out), d, "rt", 0)
    assert out.stat().st_size > 20_000


def test_fill_edges():
    assert hrdps._fill([None, 2.0, None, 4.0, None]) == [2.0, 2.0, 3.0, 4.0, 4.0]
    assert hrdps._fill([None, None]) == [None, None]
