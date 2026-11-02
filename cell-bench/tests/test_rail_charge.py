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
