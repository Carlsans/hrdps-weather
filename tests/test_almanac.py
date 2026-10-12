"""Sun/moon maths, climate normals (fake API), links and the bottom strip."""
import json
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from hrdps_weather import astro, climate, hrdps, links, view

TZ = ZoneInfo("America/Toronto")
UTC = timezone.utc


# ── sun and moon ─────────────────────────────────────────────────────────────
def test_equinox_day_is_about_twelve_hours_and_noon_is_noon():
    s = astro.sun_times(date(2026, 3, 20), 46.8, -71.2, TZ)
    assert 11.9 * 3600 < s["length"] < 12.3 * 3600
    assert s["sunrise"] < s["noon"] < s["sunset"] and s["dawn"] < s["sunrise"] and s["sunset"] < s["dusk"]


def test_solstices_and_polar_cases():
    jun = astro.sun_times(date(2026, 6, 21), 46.8, -71.2, TZ)["length"]
    dec = astro.sun_times(date(2026, 12, 21), 46.8, -71.2, TZ)["length"]
    assert 15.6 * 3600 < jun < 16.1 * 3600 and 8.3 * 3600 < dec < 8.8 * 3600
    polar_night = astro.sun_times(date(2026, 12, 21), 80.0, -71.2, TZ)
    assert polar_night["sunrise"] is None and polar_night["length"] == 0
    midnight_sun = astro.sun_times(date(2026, 6, 21), 80.0, -71.2, TZ)
    assert midnight_sun["sunset"] is None and midnight_sun["length"] == 86400


def test_days_get_shorter_after_the_solstice_and_longer_after_december():
    f = lambda d: astro.sun_times(d, 46.8, -71.2, TZ)["length"]
    assert f(date(2026, 10, 12)) < f(date(2026, 10, 11))
    assert f(date(2026, 1, 12)) > f(date(2026, 1, 11))


@pytest.mark.parametrize("start, key, expected", [
    (datetime(2026, 10, 12, 12, tzinfo=UTC), "next_full", datetime(2026, 10, 26, 4, 12, tzinfo=UTC)),
    (datetime(2026, 10, 12, 12, tzinfo=UTC), "next_new", datetime(2026, 11, 9, 7, 2, tzinfo=UTC)),
    (datetime(2024, 1, 20, tzinfo=UTC), "next_full", datetime(2024, 1, 25, 17, 54, tzinfo=UTC)),
    (datetime(2024, 1, 5, tzinfo=UTC), "next_new", datetime(2024, 1, 11, 11, 57, tzinfo=UTC)),
])
def test_moon_phases_match_published_times(start, key, expected):
    got = astro.moon(start)[key]
    assert abs((got - expected).total_seconds()) < 20 * 60            # within 20 minutes


def test_moon_illumination_and_names():
    assert astro.moon(datetime(2024, 1, 11, 12, tzinfo=UTC))["illumination"] < 0.01
    full = astro.moon(datetime(2024, 1, 25, 18, tzinfo=UTC))
    assert full["illumination"] > 0.99 and full["name"] == "Pleine lune"
    assert astro.moon(datetime(2024, 1, 18, 3, 53, tzinfo=UTC))["name"] == "Premier quartier"


def test_signed_never_shows_minus_zero():
    assert view.signed(-0.4) == "0°" and view.signed(2.6) == "+3°" and view.signed(-2.6) == "−3°"
    assert view.signed(-3, " min") == "−3 min"


# ── climate normals with a fake API ──────────────────────────────────────────
def fake_api(monkeypatch, stations=None):
    stations = stations or [(5315, "BEAUSEJOUR", -71.24, 46.85), (5251, "QUEBEC/JEAN LESAGE INTL A", -71.38, 46.8)]
    temp = [-12.8, -11.0, -5.0, 2.5, 10.0, 15.5, 18.5, 17.5, 12.5, 6.5, 0.5, -8.5]
    rows = {5315: {"Mean daily temperature deg C": temp, "Mean daily max temperature deg C": [t + 5 for t in temp],
                   "Mean daily min temperature deg C": [t - 5 for t in temp],
                   "Total precipitation mm": [90] * 12, "Mean date last frost in spring": {13: 143}},
            5251: {"Total hours bright sunshine": [100 + 10 * i for i in range(12)],
                   "Mean of hourly wind speed kmh": [14] * 12, "Mean date first frost in fall": {13: 268}}}
    calls = []

    def get(url, ua, timeout=30, tries=2):
        calls.append(url)
        if "climate-stations" in url:
            return json.dumps({"features": [{"geometry": {"coordinates": [lo, la]},
                                             "properties": {"STN_ID": i, "STATION_NAME": n}} for i, n, lo, la in stations]}).encode()
        stn = int(url.split("STN_ID=")[1].split("&")[0])
        feats = []
        for name, vals in rows.get(stn, {}).items():
            items = vals.items() if isinstance(vals, dict) else enumerate(vals, 1)
            feats += [{"properties": {"E_NORMAL_ELEMENT_NAME": name, "MONTH": m, "VALUE": v}} for m, v in items]
        return json.dumps({"features": feats}).encode()
    monkeypatch.setattr(climate.net, "get", get)
    return calls


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for var, sub in (("XDG_CONFIG_HOME", "cfg"), ("XDG_CACHE_HOME", "cache"), ("APPDATA", "cfg"), ("LOCALAPPDATA", "cache")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    return tmp_path


def test_build_merges_stations_nearest_first(monkeypatch):
    fake_api(monkeypatch)
    d = climate.build(46.76, -71.24)
    assert d["monthly"]["tmean"][0] == -12.8 and d["src"]["tmean"][1] == "BEAUSEJOUR"
    assert d["src"]["sun"][1].startswith("QUEBEC")                    # missing at the nearest station: taken from the next one
    assert d["annual"] == {"frost_last": 143, "frost_first": 268}
    assert climate.build(46.76, -71.24)["period"] == "1981–2010"


def test_build_returns_none_when_unreachable_or_without_temperature(monkeypatch):
    monkeypatch.setattr(climate.net, "get", lambda *a, **k: None)
    assert climate.build(46.76, -71.24) is None


def test_daily_interpolation_and_year_wrap(monkeypatch):
    fake_api(monkeypatch)
    n = climate.Normals(climate.build(46.76, -71.24))
    assert n.daily("tmean", date(2026, 10, 15)) == pytest.approx(6.5)                # mid-month = the monthly value
    assert 0.5 < n.daily("tmean", date(2026, 10, 31)) < 6.5 and n.daily("tmean", date(2026, 10, 31)) < n.daily("tmean", date(2026, 10, 20))
    assert -12.8 < n.daily("tmean", date(2026, 1, 1)) < -8.5                          # December → January wraps around
    assert n.station("tmean")["km"] >= 0 and n.has("precip") and not n.has("snow")


def test_loader_uses_cache_then_network_once(isolated, monkeypatch):
    calls = fake_api(monkeypatch)
    ld = climate.Loader()
    assert ld.normals is None
    ld.start()
    for _ in range(100):
        if ld.normals:
            break
        time.sleep(0.05)
    assert ld.normals and ld.state == "idle"
    n_calls = len(calls)
    ld2 = climate.Loader()                                             # next start: straight from the cache
    assert ld2.normals is not None and len(calls) == n_calls
    ld3 = climate.Loader(network=False)
    assert ld3.normals is not None


def test_loader_without_network_stays_empty(isolated, monkeypatch):
    calls = fake_api(monkeypatch)
    ld = climate.Loader(network=False)
    ld.start()
    assert ld.normals is None and not calls


def test_frost_date_text():
    assert climate.doy_text(131) == "11 mai" and climate.doy_text(277) == "4 oct."


# ── links ────────────────────────────────────────────────────────────────────
def test_urls():
    assert links.forecast_url(46.75817, -71.24036) == "https://meteo.gc.ca/fr/location/index.html?coords=46.758,-71.240"
    assert links.normals_url(5251).endswith("stnID=5251&autofwd=1") and links.normals_url(5251).startswith("https://climat.meteo.gc.ca/")


def test_open_url_refuses_non_http(monkeypatch):
    launched = []
    monkeypatch.setattr(links.subprocess, "Popen", lambda *a, **k: launched.append(a))
    monkeypatch.setattr(links.webbrowser, "open", lambda u: launched.append(u))
    assert not links.open_url("file:///etc/passwd") and not links.open_url("javascript:alert(1)") and not launched


def test_open_url_cleans_pyinstaller_library_path(monkeypatch):
    captured = {}
    monkeypatch.setattr(links.sys, "platform", "linux")
    monkeypatch.setattr(links.sys, "frozen", True, raising=False)
    monkeypatch.setattr(links.shutil, "which", lambda n: "/usr/bin/xdg-open")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEIxyz")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    monkeypatch.setattr(links.subprocess, "Popen", lambda cmd, **kw: captured.update(cmd=cmd, env=kw["env"]))
    assert links.open_url("https://meteo.gc.ca/")
    assert captured["cmd"] == ["xdg-open", "https://meteo.gc.ca/"] and captured["env"]["LD_LIBRARY_PATH"] == "/usr/local/lib"


# ── the strip ────────────────────────────────────────────────────────────────
def test_strip_draws_with_and_without_normals(data, monkeypatch):
    from cairo import ImageSurface, Context, FORMAT_ARGB32
    fake_api(monkeypatch)
    for loader in (None, SimpleNamespace(normals=None, state="loading"),
                   SimpleNamespace(normals=climate.Normals(climate.build(46.76, -71.24)), state="idle")):
        v = view.View(data)
        v.climate = loader
        v.draw(Context(ImageSurface(FORMAT_ARGB32, view.W, view.H)), view.W, view.H)
        assert getattr(v, "errors", 0) == 0
        urls = [a for _, t, a in v.hits if t == "link"]
        assert (len(urls) == 2) == bool(loader and loader.normals) and urls[0].startswith("https://meteo.gc.ca/")
