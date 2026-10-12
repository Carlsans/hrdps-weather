"""Tides tab: station choice, series maths, loader (fake IWLS API) and drawing."""
import json
import math
import time
from datetime import datetime, timedelta, timezone

import pytest

from hrdps_weather import hrdps, tides, view

UTC = timezone.utc


def station(code, name, lat, lon, typ="PERMANENT", operating=True, series=("wlo", "wlp", "wlp-hilo")):
    return {"code": code, "id": f"id-{code}", "name": name, "type": typ, "operating": operating,
            "lat": lat, "lon": lon, "series": list(series)}


STATIONS = [station("A", "Temporaire proche", 46.76, -71.24, "TEMPORARY", False, ("wlp", "wlp-hilo")),
            station("B", "Permanente", 46.81, -71.20),
            station("C", "Loin", 47.8, -69.5)]


# ── station choice ───────────────────────────────────────────────────────────
def test_prefers_operating_station_with_observations_when_close_enough():
    s = tides.choose_station(46.758, -71.240, STATIONS)
    assert s["code"] == "B" and 5 < s["km"] < 8


def test_keeps_nearest_when_the_operating_one_is_much_farther():
    far = STATIONS[:1] + [station("B", "Permanente loin", 47.4, -70.5)]
    assert tides.choose_station(46.758, -71.240, far)["code"] == "A"


def test_override_and_nothing_nearby():
    assert tides.choose_station(46.758, -71.240, STATIONS, override="C")["code"] == "C"
    assert tides.choose_station(46.758, -71.240, STATIONS, override="Z") is None
    assert tides.choose_station(10.0, -60.0, STATIONS) is None                 # nothing within MAX_KM


# ── series maths ─────────────────────────────────────────────────────────────
def sine_tide(start, hours=72, step=900, period=12.42, mean=2.5, amp=2.5):
    return [(start + k * step, mean + amp * math.sin(2 * math.pi * (k * step / 3600) / period)) for k in range(int(hours * 3600 / step))]


@pytest.fixture
def td():
    t0 = datetime(2026, 10, 12, 0, tzinfo=UTC).timestamp()
    wlp = sine_tide(t0)
    # highs/lows of the sine: quarter period offsets
    hilo = []
    for n in range(12):
        t = t0 + (n * 0.5 + 0.25) * 12.42 * 3600
        hilo.append((t, 5.0 if n % 2 == 0 else 0.0))
    return tides.TideData({"station": station("B", "Permanente", 46.8, -71.2), "fetched": time.time(), "wlp": wlp,
                           "hilo": hilo, "wlo": [(t0 + 3600 * h, 2.0 + 0.1 * h) for h in range(10)],
                           "wt": [(t0 + 3600 * 9, 13.1)]}), t0


def test_interpolation_and_gaps(td):
    d, t0 = td
    assert d.predicted(t0) == pytest.approx(2.5)
    assert d.predicted(t0 + 450) == pytest.approx((d.wlp[0][1] + d.wlp[1][1]) / 2)
    assert d.predicted(t0 - 10) is None and d.predicted(t0 + 80 * 3600) is None
    gappy = [(0, 1.0), (4 * 3600, 5.0)]
    assert tides.TideData.at(gappy, 2 * 3600) is None                          # across a gap of more than 3 h


def test_highs_and_lows_are_labelled_by_comparison(td):
    d, t0 = td
    assert [k for _, _, k in d.events[:4]] == ["H", "L", "H", "L"]
    nxt = d.next_events(t0 + 3600, 3)
    assert len(nxt) == 3 and nxt[0][0] >= t0 + 3600


def test_trend_current_level_and_temperature(td):
    d, t0 = td
    assert d.trend(t0 + 3600) > 0.5                                            # rising after the sine's zero crossing
    assert d.trend(t0) is None                                                 # no prediction before the series starts
    assert tides.trend_text(0.85) == "monte 0,85 m/h" and tides.trend_text(-0.2) == "baisse 0,20 m/h"
    assert tides.trend_text(0.01) == "étale" and tides.trend_text(None) == ""
    assert d.latest_observed(t0 + 9 * 3600 + 600)[1] == pytest.approx(2.9)
    assert d.latest_observed(t0 + 20 * 3600) is None                           # too old to be "current"
    assert d.water_temperature(t0 + 9.5 * 3600) == 13.1 and d.water_temperature(t0 + 30 * 3600) is None


def test_json_roundtrip(td):
    d, _ = td
    again = tides.TideData(json.loads(json.dumps(d.to_json())))
    assert again.events == d.events and again.wlp == d.wlp


# ── loader with a fake service ───────────────────────────────────────────────
@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for var, sub in (("XDG_CONFIG_HOME", "cfg"), ("XDG_CACHE_HOME", "cache"), ("APPDATA", "cfg"), ("LOCALAPPDATA", "cache")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    return tmp_path


def fake_service(monkeypatch, stations=None, fail_data=False):
    stations = stations if stations is not None else [
        {"officialName": "Permanente", "code": "B", "id": "id-B", "type": "PERMANENT", "operating": True,
         "latitude": 46.81, "longitude": -71.2, "timeSeries": [{"code": c} for c in ("wlo", "wlp", "wlp-hilo", "wlf-spine", "wt1")]}]
    calls = []

    def get(url, ua, timeout=30, tries=2):
        calls.append(url)
        if url.endswith("/stations"):
            return json.dumps(stations).encode()
        if fail_data:
            return None
        code = url.split("time-series-code=")[1].split("&")[0]
        now = datetime.now(UTC)
        n = {"wlp-hilo": 6, "wlp": 40, "wlo": 20, "wlf-spine": 40, "wt1": 8}[code]
        rows = []
        for k in range(n):
            t = now - timedelta(hours=24) + timedelta(minutes=(96 * 60 // n) * k) if code != "wlo" and code != "wt1" \
                else now - timedelta(hours=5) + timedelta(minutes=15 * k)
            rows.append({"eventDate": t.strftime(tides.FMT), "value": 2.0 + (k % 5) * 0.7})
        return json.dumps(rows).encode()
    monkeypatch.setattr(tides.net, "get", get)
    return calls


def wait(loader, cond=lambda l: l.data is not None, secs=5):
    for _ in range(int(secs / 0.05)):
        if cond(loader) and not (loader._thread and loader._thread.is_alive()):
            return
        time.sleep(0.05)


def test_loader_end_to_end_and_cache(isolated, monkeypatch):
    calls = fake_service(monkeypatch)
    ld = tides.Loader()
    ld.ensure()
    wait(ld)
    assert ld.state == "idle" and ld.data.station["code"] == "B"
    assert ld.data.wlp and ld.data.events and ld.data.wlo and ld.data.wlf and ld.data.wt
    n = len(calls)
    ld.ensure()                                                                  # fresh: nothing to do
    assert len(calls) == n
    again = tides.Loader()                                                       # a new session reads the cache
    assert again.data is not None and again.data.station["code"] == "B"


def test_loader_no_station_nearby(isolated, monkeypatch):
    fake_service(monkeypatch, stations=[])
    ld = tides.Loader()
    ld.ensure()
    wait(ld, lambda l: l.state == "none")
    assert ld.state == "none" and ld.data is None


def test_loader_error_is_not_retried_in_a_tight_loop(isolated, monkeypatch):
    calls = fake_service(monkeypatch, fail_data=True)
    ld = tides.Loader()
    ld.ensure()
    wait(ld, lambda l: l.state == "error")
    assert ld.state == "error"
    n = len(calls)
    for _ in range(20):
        ld.ensure()
    assert len(calls) == n                                                        # backs off for a couple of minutes


def test_loader_without_network_does_nothing(isolated, monkeypatch):
    calls = fake_service(monkeypatch)
    ld = tides.Loader(network=False)
    ld.ensure()
    assert ld.data is None and not calls


# ── drawing ──────────────────────────────────────────────────────────────────
def draw(data, loader):
    from cairo import ImageSurface, Context, FORMAT_ARGB32
    v = view.View(data)
    v.layer, v.tides = "tides", loader
    v.draw(Context(ImageSurface(FORMAT_ARGB32, view.W, view.H)), view.W, view.H)
    return v


def test_tab_exists_and_has_its_key():
    assert [k for k, _ in view.LAYERS][-1] == "tides" and len(view.LAYERS) == 6


@pytest.mark.parametrize("state", ["loading", "none", "error"])
def test_tab_messages_without_data(data, state):
    loader = tides.Loader(network=False)
    loader.state = state
    assert getattr(draw(data, loader), "errors", 0) == 0
    assert getattr(draw(data, None), "errors", 0) == 0


def test_tab_draws_with_data_and_cursor_elsewhere(data, td):
    d, _ = td
    now = time.time()
    d2 = tides.TideData({"station": dict(d.station, km=6.6), "fetched": now, "wlp": sine_tide(now - 24 * 3600, 96),
                         "hilo": [(now + h * 3600, 5.0 if k % 2 == 0 else 0.1) for k, h in enumerate(range(2, 40, 6))],
                         "wlo": [(now - h * 900, 2.5) for h in range(60, -1, -1)], "wt": [(now - 600, 12.4)],
                         "wlf": sine_tide(now - 24 * 3600, 96, amp=2.7)})
    loader = tides.Loader(network=False)
    loader.data = d2
    v = draw(data, loader)
    assert getattr(v, "errors", 0) == 0
    v.t = float(v.i1)                                                             # cursor at the far end of the timeline
    from cairo import ImageSurface, Context, FORMAT_ARGB32
    v.draw(Context(ImageSurface(FORMAT_ARGB32, view.W, view.H)), view.W, view.H)
    assert getattr(v, "errors", 0) == 0
