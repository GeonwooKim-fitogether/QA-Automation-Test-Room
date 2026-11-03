"""장면을 바꿀 때 GPS 확인(qc_round.gps_ready) — 장비 없이 돈다. 2026-11-03: 5분까지 기다리고, 90초에 못 잡은 셀만 재부팅."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qc_round as Q  # noqa: E402


class FakeLs:
    def __init__(self): self.played = []
    def stop(self): pass
    def play(self, name): self.played.append(name)


def _clock(monkeypatch, counts):
    t = {"now": 0.0}
    monkeypatch.setattr(Q.time, "time", lambda: t["now"])
    monkeypatch.setattr(Q.time, "sleep", lambda s: t.__setitem__("now", t["now"] + s))
    monkeypatch.setattr(Q, "fixed_count", lambda: (counts(t["now"]), 23))
    monkeypatch.setattr(Q, "unlocked", lambda: [10679])
    return t


def test_starts_as_soon_as_all_cells_fix(monkeypatch):
    t = _clock(monkeypatch, lambda now: 23 if now >= 40 else 5)
    ls = FakeLs()
    got, tap = Q.gps_ready(ls, "S05_RailB500", print)
    assert got == 23 and ls.played == ["S05_RailB500"] and t["now"] < 60


def test_waits_full_five_minutes_then_reboots_once(monkeypatch):
    calls = []
    _clock(monkeypatch, lambda now: 14)
    monkeypatch.setattr(Q, "stop_tap", lambda p: calls.append("stop"))
    monkeypatch.setattr(Q, "reboot", lambda bad, say: calls.append(("reboot", tuple(bad))))
    monkeypatch.setattr(Q, "start_tap", lambda part, minutes, log: "tap2")
    got, tap = Q.gps_ready(FakeLs(), "S04_RailB100", print, tap="tap1")
    assert got == 14 and tap == "tap2" and calls == ["stop", ("reboot", (10679,))]


def test_none_when_too_few_after_five_minutes(monkeypatch):
    _clock(monkeypatch, lambda now: 4)
    got, tap = Q.gps_ready(FakeLs(), "S05_RailB500", print)
    assert got is None and tap is None
