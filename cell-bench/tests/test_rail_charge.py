"""레일 독 충전 유지(motionbench/charge.py) — 끊었다 켜기가 꺼진 채로 남지 않는지, 상태 파일. 장비 없이 돈다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench.plug import PlugReading  # noqa: E402
from motionbench import charge as C  # noqa: E402


class FakePlug:
    def __init__(self, on_fails: int):
        self.on_fails, self.calls = on_fails, []

    def _call(self, action):
        self.calls.append(action)
        return PlugReading(action != "off", 0.0, 0.0)

    def on(self):
        self.calls.append("on")
        if self.on_fails > 0:
            self.on_fails -= 1
            raise RuntimeError("플러그를 찾지 못함")
        return PlugReading(True, 70.0, 0.0)


def test_safe_recharge_keeps_trying_until_on(monkeypatch):
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    p = FakePlug(on_fails=5)
    r = C.safe_recharge(p, 10, say=lambda m: None, tries=12, wait_s=0)
    assert r.on and p.calls[0] == "off" and p.calls.count("on") == 6


def test_safe_recharge_raises_loudly_when_never_on(monkeypatch):
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    said = []
    with pytest.raises(RuntimeError):
        C.safe_recharge(FakePlug(on_fails=99), 10, say=said.append, tries=3, wait_s=0)
    assert "손으로 켠다" in said[-1]


def test_write_status(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "STATUS_FILE", tmp_path / "rail_charge.json")
    C.write_status(state="lost", fails=3, error="x")
    d = json.loads((tmp_path / "rail_charge.json").read_text(encoding="utf-8"))
    assert d["state"] == "lost" and d["fails"] == 3 and "t" in d


def test_test_running_reads_qc_now(tmp_path):
    import time as _t
    q = tmp_path / "qc_now.json"
    q.write_text(json.dumps({"t": _t.time(), "step": {"key": "A0"}}), encoding="utf-8")
    assert C.test_running(q)
    q.write_text(json.dumps({"t": _t.time(), "step": {"key": "idle"}}), encoding="utf-8")
    assert not C.test_running(q)
    q.write_text(json.dumps({"t": _t.time() - 60, "step": {"key": "A0"}}), encoding="utf-8")
    assert not C.test_running(q)


def test_keep_rests_while_plug_manual(tmp_path, monkeypatch):
    """plug_manual 이 있으면 keep 은 플러그를 찾지 않는다 — 못 찾는 플러그를 1분마다 부르던 것(11-06 새벽 271번)."""
    (tmp_path / "plug_manual").write_text("", encoding="utf-8")
    monkeypatch.setattr(C, "PLUG_MANUAL", tmp_path / "plug_manual")
    monkeypatch.setattr(C, "rail_plug", lambda: (_ for _ in ()).throw(AssertionError("플러그를 불렀다")))
    said = []
    assert C.keep(0.0002, tick_s=0.05, say=said.append) == 0
    assert sum("쉼" in s for s in said) == 1


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_charge_until_returns_when_cells_report_full():
    clk, calls = Clock(), []
    seq = iter([(40, 24, None), (70, 24, None), (86, 24, None)])
    ok = C.charge_until(85, 20, say=lambda m: None, plug=lambda on, say: calls.append(on), low=lambda: next(seq),
                        sleep=clk.sleep, now=clk.now)
    assert ok and calls == [True]


def test_charge_until_turns_off_to_hear_silent_cells_then_back_on():
    # X 셀은 충전 중엔 안 들린다 — 20분마다 끄고 들어 보고, 모자라면 다시 켠다
    clk, calls, state = Clock(), [], {"on": False, "checks": 0}

    def plug(on, say):
        calls.append(on)
        state["on"] = on
        if not on:
            state["checks"] += 1

    def low():
        if state["on"]:
            return None, 0, "안 들림"
        return (60, 24, None) if state["checks"] == 1 else (90, 24, None)

    ok = C.charge_until(85, 20, say=lambda m: None, plug=plug, low=low, sleep=clk.sleep, now=clk.now,
                        check_every_s=1200, off_check_s=180, poll_s=30)
    assert ok and calls == [True, False, True, False]


def test_charge_until_gives_up_after_max_hours_with_plug_on():
    clk, calls = Clock(), []
    ok = C.charge_until(85, 20, say=lambda m: None, plug=lambda on, say: calls.append(on), low=lambda: (50, 24, None),
                        sleep=clk.sleep, now=clk.now, max_h=1.0)
    assert not ok and calls[-1] is True


def test_keep_stands_aside_while_collection_loop_holds_lock(tmp_path, monkeypatch):
    from cellbench import proc
    monkeypatch.setattr(C, "LOOP_LOCK", tmp_path / "qc_loop.lock")
    monkeypatch.setattr(C, "PLUG_MANUAL", tmp_path / "plug_manual")
    monkeypatch.setattr(C, "rail_plug", lambda: (_ for _ in ()).throw(AssertionError("플러그를 불렀다")))
    held = proc.acquire_lock(C.LOOP_LOCK)
    said = []
    try:
        monkeypatch.setattr(C.time, "sleep", lambda s: None)
        C.keep(hours=0.0005, tick_s=0.0, say=said.append)
    finally:
        held.close()
    assert any("수집 실행기가 돈다" in m for m in said)
