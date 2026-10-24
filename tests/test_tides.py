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


# ── a year kept for offline use ──────────────────────────────────────────────
def test_fetch_year_asks_in_chunks_the_service_accepts_and_merges(monkeypatch):
    spans = []

    def fake_series(st, code, start, end, res):
        if code == "wlp-hilo":
            return [(start.timestamp() + 3600, 1.0), (end.timestamp() - 3600, 5.0)]
        spans.append((end - start).total_seconds())
        n = int((end - start).total_seconds() // 900)
        return [(start.timestamp() + 900 * k, 2.0) for k in range(n + 1)]
    monkeypatch.setattr(tides, "_series", fake_series)
    now = 1_800_000_000
    y = tides.fetch_year({"id": "x", "series": ["wlp", "wlp-hilo"]}, now)
    assert len(spans) >= 13 and max(spans) <= 31 * 86400                            # 13 chunks, each within the limit
    assert y["to"] - now == tides.YEAR and y["from"] == now - 86400
    times = [t for t, _ in y["wlp"]]
    assert times == sorted(set(times)) and times[-1] - times[0] > 364 * 86400       # one continuous, de-duplicated series
    assert len(y["hilo"]) == 2


def test_fetch_year_gives_nothing_rather_than_half_a_year(monkeypatch):
    def fake_series(st, code, start, end, res):
        return None if code == "wlp" and start.timestamp() > 1_800_000_000 + 100 * 86400 else [(start.timestamp(), 1.0)]
    monkeypatch.setattr(tides, "_series", fake_series)
    assert tides.fetch_year({"id": "x", "series": []}, 1_800_000_000) is None


def test_year_is_stored_and_serves_the_tides_without_network(isolated, monkeypatch):
    fake_service(monkeypatch)
    ld = tides.Loader()
    ld.ensure()
    wait(ld)
    assert ld.year is not None and tides._year_cache().exists()
    assert tides._year_cache().stat().st_size < 3_000_000
    tides._cache().unlink()                                                          # a later session, no fresh download…
    fake_service(monkeypatch, fail_data=True)                                        # …and no network
    off = tides.Loader()
    assert off.data is not None and off.data.fetched == 0 and off.data.wlp and not off.data.wlo
    off.ensure()
    wait(off, lambda l: l.state == "error")
    assert off.state == "error" and off.data is not None and off.data.station["code"] == "B"   # still shown


def test_year_is_not_fetched_again_while_it_has_enough_left(isolated, monkeypatch):
    calls = fake_service(monkeypatch)
    ld = tides.Loader()
    ld.ensure()
    wait(ld)
    n = len([c for c in calls if "wlp-hilo" in c])
    ld.data.fetched = 0                                                               # force another refresh
    ld.ensure()
    wait(ld)
    assert len([c for c in calls if "wlp-hilo" in c]) == n + 1                        # only the 4-day hilo, no new year


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


def test_feet_conversion_is_exact():
    assert view.FEET_PER_M == pytest.approx(1 / 0.3048, rel=1e-8)
    assert round(2.57 * view.FEET_PER_M, 1) == 8.4 and round(5.29 * view.FEET_PER_M, 1) == 17.4


def test_tab_shows_feet_next_to_metres(data, monkeypatch):
    seen = []
    real = view.text
    monkeypatch.setattr(view, "text", lambda cr, s, *a, **k: (seen.append(str(s)), real(cr, s, *a, **k))[1])
    now = time.time()
    d = tides.TideData({"station": dict(station("B", "Permanente", 46.8, -71.2), km=6.6), "fetched": now,
                        "wlp": sine_tide(now - 24 * 3600, 96), "wlo": [(now - 300, 2.57)],
                        "hilo": [(now + 3 * 3600, 5.29), (now + 9 * 3600, 0.36)], "wt": [(now - 60, 12.4)]})
    loader = tides.Loader(network=False)
    loader.data = d
    v = draw(data, loader)
    assert getattr(v, "errors", 0) == 0
    joined = " | ".join(seen)
    assert "2,57 m" in seen and "8,4 pi" in seen                                # big level, feet beside it
    assert "5,3 m · 17,4 pi" in joined and "5,29 m (17,4 pi)" in joined         # chart label and the list
    assert "0,36 m (1,2 pi)" in joined


def test_tab_and_strip_draw_with_the_bare_text_backend(data, monkeypatch):
    """Windows / the Linux binary: no Pango. Symbols are drawn as paths, so nothing may rely on glyph fallback."""
    from cairo import ImageSurface, Context, FORMAT_ARGB32
    monkeypatch.setattr(view, "HAVE_PANGO", False)
    now = time.time()
    d = tides.TideData({"station": dict(station("B", "Permanente", 46.8, -71.2), km=6.6), "fetched": now,
                        "wlp": sine_tide(now - 24 * 3600, 96), "wlo": [(now - 300, 2.57)],
                        "hilo": [(now + 3 * 3600, 5.29), (now + 9 * 3600, 0.36)], "wt": [(now - 60, 12.4)]})
    loader = tides.Loader(network=False)
    loader.data = d
    seen = []
    real = view.text
    monkeypatch.setattr(view, "text", lambda cr, s, *a, **k: (seen.append(str(s)), real(cr, s, *a, **k))[1])
    v = draw(data, loader)
    assert getattr(v, "errors", 0) == 0
    joined = "".join(seen)
    assert not any(ch in joined for ch in "▲▼■↗"), "symbols must be drawn as paths, not as text"
    v.layer = "rt"
    v.draw(Context(ImageSurface(FORMAT_ARGB32, view.W, view.H)), view.W, view.H)       # the strip with the link button
    assert getattr(v, "errors", 0) == 0 and not any(ch in "".join(seen) for ch in "↗")


def live_loader(d):
    now = time.time()
    loader = tides.Loader(network=False)
    loader.data = tides.TideData({"station": dict(d.station, km=6.6), "fetched": now, "wlp": sine_tide(now - 24 * 3600, 96),
                                  "hilo": [(now + h * 3600, 5.0 if k % 2 == 0 else 0.1) for k, h in enumerate(range(2, 40, 6))],
                                  "wlo": [(now - h * 900, 2.5) for h in range(60, -1, -1)], "wt": [(now - 600, 12.4)],
                                  "wlf": sine_tide(now - 24 * 3600, 96, amp=2.7)})
    return loader


def test_clicking_the_tides_chart_moves_the_time_line(data, td, monkeypatch):
    now0 = data.utc[0].timestamp() + 3 * 3600                                    # the sample run is old: pretend it is now
    monkeypatch.setattr(time, "time", lambda: now0)
    monkeypatch.setattr(type(data), "now_index", lambda self: 3)
    v = draw(data, live_loader(td[0]))
    (x, y, w, h), tag, geom = next(hb for hb in v.hits if hb[1] == "tide_chart")
    assert v.hit(x + w / 2, y + 20)[0] == "tide_chart"
    t0, t1, cx0, cx1 = geom
    # the left end is the oldest moment shown: kept at the start of the forecast; the right end is the last hour
    assert v.t_from_tide(cx0 - 50, geom) == v.i0
    assert v.t_from_tide(cx1 + 50, geom) == v.i1
    mid = v.t_from_tide((cx0 + cx1) / 2, geom)
    assert v.i0 <= mid <= v.i1
    # moving right moves later; the index is the fractional hour of the model
    a, b = v.t_from_tide(cx0 + 0.55 * (cx1 - cx0), geom), v.t_from_tide(cx0 + 0.75 * (cx1 - cx0), geom)
    assert a < b
    secs = lambda i: v.d.utc[int(i)].timestamp() + (i - int(i)) * 3600
    assert secs(b) - secs(a) == pytest.approx(0.2 * (t1 - t0), abs=1)


def test_ui_zoom_steps_and_memory(tmp_path, monkeypatch):
    assert view.next_ui_zoom(1.0, +1) > 1.0 > view.next_ui_zoom(1.0, -1)
    assert view.next_ui_zoom(view.UI_ZOOMS[-1], +1) == view.UI_ZOOMS[-1]
    assert view.next_ui_zoom(view.UI_ZOOMS[0], -1) == view.UI_ZOOMS[0]
    monkeypatch.setattr(view.hrdps, "CACHE", tmp_path)
    assert view.load_ui_zoom() == 1.0
    view.save_ui_zoom(1.5)
    assert view.load_ui_zoom() == 1.5
    (tmp_path / "ui_zoom").write_text("nonsense")
    assert view.load_ui_zoom() == 1.0
