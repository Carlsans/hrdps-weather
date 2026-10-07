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
