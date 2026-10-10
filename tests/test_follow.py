"""An open window must follow the runs that the background refresh downloads, and the clock."""
import json
from datetime import datetime, timedelta, timezone

from hrdps_weather import hrdps, view
from tests.conftest import SAMPLE


def run_at(ref):
    """The bundled run, relabelled as if it started at `ref`."""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["ref"] = ref.strftime(hrdps.FMT)
    raw["times"] = [(ref + timedelta(hours=i)).strftime(hrdps.FMT) for i in range(len(raw["times"]))]
    return hrdps.Data(raw)


def freeze(monkeypatch, now):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(hrdps, "datetime", Clock)


def test_window_picks_up_a_newer_run(monkeypatch):
    old_ref = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    freeze(monkeypatch, datetime(2026, 10, 10, 20, 30, tzinfo=timezone.utc))
    old, new = run_at(old_ref), run_at(old_ref + timedelta(hours=6))
    v = view.View(old)
    v.maps = {"ref": old.raw["ref"]}                          # the grids of the old run
    v.t = v.i0 + 5                                            # the user looks 5 h ahead

    monkeypatch.setattr(hrdps, "_state", lambda: {"ref": old.raw["ref"]})
    assert v.follow(every=0) is False                         # nothing new yet

    monkeypatch.setattr(hrdps, "_state", lambda: {"ref": new.raw["ref"]})
    monkeypatch.setattr(hrdps, "load", lambda: new)
    assert v.follow(every=0) is True
    assert v.d is new and v.i0 == 2                           # 20:30 is hour 2 of the 18Z run
    assert v.t == v.i0 + 5                                    # still 5 h ahead
    assert v.maps is None                                     # old grids dropped until the new ones load
    assert "Run 18Z" not in hrdps.next_run_text(v.d.ref, datetime(2026, 10, 10, 20, 30, tzinfo=timezone.utc))


def test_window_moves_now_when_the_hour_changes(monkeypatch):
    ref = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    d = run_at(ref)
    monkeypatch.setattr(hrdps, "_state", lambda: {"ref": d.raw["ref"]})
    freeze(monkeypatch, datetime(2026, 10, 10, 14, 59, tzinfo=timezone.utc))
    v = view.View(d)
    v.maps = {"ref": d.raw["ref"]}
    assert v.i0 == 2
    assert v.follow(every=0) is False
    freeze(monkeypatch, datetime(2026, 10, 10, 15, 1, tzinfo=timezone.utc))
    assert v.follow(every=0) is True
    assert v.i0 == 3 and v.t >= v.i0
    assert v.maps is not None                                 # same run: the grids stay


def test_follow_is_throttled(monkeypatch):
    calls = []
    monkeypatch.setattr(hrdps, "_state", lambda: calls.append(1) or {})
    v = view.View(run_at(datetime(2026, 10, 10, 12, tzinfo=timezone.utc)))
    v.follow(every=30)
    v.follow(every=30)
    assert len(calls) == 1                                    # cheap enough to call on every frame
