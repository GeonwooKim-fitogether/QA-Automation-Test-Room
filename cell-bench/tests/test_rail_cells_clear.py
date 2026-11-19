"""레일 셀 받기 — 받기 · 지우기를 끝내지 못한 셀을 한 번 더 받고, 그래도 남으면 cells_not_cleared.json 에 적는다(2026-11-19).
그날 14:32 받기가 끊긴 5대 중 10692 는 지우지 못한 채 남아 저장이 가득 차 측정이 멈췄다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import rail_cells as RC  # noqa: E402
from cellbench.cells import CellResult  # noqa: E402

MB = 1048576


class FakeLive:
    def __init__(self, cfg): pass
    def start(self): pass
    def stop(self): pass
    def alive(self): return [10680, 10692, 10693]


def _setup(tmp_path, monkeypatch, plan):
    """plan = 받을 때마다 돌려줄 {시리얼: CellResult} 목록."""
    calls = []

    class FakeLink:
        def __init__(self, cfg, lv, log=None): pass
        def extract(self, serials, out):
            calls.append(sorted(serials))
            out.mkdir(parents=True, exist_ok=True)
            return {s: plan[len(calls) - 1][s] for s in serials}

    monkeypatch.setattr(RC, "DATA", tmp_path)
    monkeypatch.setattr(RC, "NOT_CLEARED", tmp_path / "cells_not_cleared.json")
    monkeypatch.setattr(RC, "LiveListener", FakeLive)
    monkeypatch.setattr(RC, "CellLink", FakeLink)
    monkeypatch.setattr(RC, "serials", lambda name: [10680, 10692, 10693, 10694])
    monkeypatch.setattr(RC.time, "sleep", lambda s: None)
    return calls


def ok(s, mb=10):
    return CellResult(s, "ip", size=mb * MB, got=mb * MB, ended=True, deleted=True)


def cut(s, mb=120):
    return CellResult(s, "ip", size=mb * MB, got=5 * MB, ended=False, deleted=False, error="받기 끊김")


def test_a_cut_cell_is_extracted_again_and_cleared(tmp_path, monkeypatch):
    calls = _setup(tmp_path, monkeypatch, [{10680: ok(10680), 10692: cut(10692), 10693: ok(10693)}, {10692: ok(10692, 120)}])
    RC.extract_all("Y", say=lambda m: None)
    assert calls == [[10680, 10692, 10693], [10692]]
    assert RC.not_cleared() == {}


def test_a_cell_still_cut_is_written_down_for_the_next_run(tmp_path, monkeypatch):
    calls = _setup(tmp_path, monkeypatch, [{10680: ok(10680), 10692: cut(10692), 10693: ok(10693)}, {10692: cut(10692, 125)}])
    said = []
    RC.extract_all("Y", say=said.append)
    left = RC.not_cleared()
    assert list(left) == ["10692"] and left["10692"]["size_mb"] == 125.0
    assert any("경고" in m and "10692" in m for m in said)


def test_a_cell_left_earlier_stays_until_it_is_cleared(tmp_path, monkeypatch):
    """앞에서 남은 셀이 이번에 실시간 데이터가 끊겨 받지 못하면(독에 없거나 가득 차 측정이 멈춤) 기록을 지우지 않는다."""
    _setup(tmp_path, monkeypatch, [{10680: ok(10680), 10692: ok(10692), 10693: ok(10693)}])
    (tmp_path / "cells_not_cleared.json").write_text(json.dumps({"10694": {"size_mb": 150.0}}), encoding="utf-8")
    RC.extract_all("Y", say=lambda m: None)
    assert list(RC.not_cleared()) == ["10694"]
