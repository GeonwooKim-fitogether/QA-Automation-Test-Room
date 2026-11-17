"""레일 시험 시작 점검(2026-11-17) — 독 위 셀 수 · 위치 잡기 기준 대수 · 충전 유지의 바닥 전력. 장비 없이 돈다."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tools", ROOT / "tools" / "qc_tests"):
    sys.path.insert(0, str(p))
import gnss_move_run as G  # noqa: E402
import qc_round as Q  # noqa: E402
from motionbench import charge as C  # noqa: E402


def heard(monkeypatch, serials, age=1.0):
    monkeypatch.setattr(Q, "qc_now", lambda: {"cells": {str(s): {"age_s": age} for s in serials}})


def test_expected_cells_follows_rows():
    """배치-12(--rows 1,3)는 그 두 행의 칸 수, 행을 안 주면 독 전체라 None(세기만)."""
    assert G.expected_cells(SimpleNamespace(rows="1,3", layout="Y-shift")) == 12
    assert G.expected_cells(SimpleNamespace(rows="", layout="Y-shift")) is None


def test_dock_count_stops_when_more_cells_heard(monkeypatch):
    """11-16 17:14 — 배치-12 로 띄웠는데 23대가 들렸다. 모터 전에 멈추고 플러그를 켠다."""
    plug = []
    monkeypatch.setattr(Q.charge, "plug_set", lambda on, say: plug.append(on))
    heard(monkeypatch, range(10677, 10700))
    with pytest.raises(SystemExit, match="23대인데 배치는 12대"):
        Q.dock_count_ok(12, lambda m: None)
    assert plug == [True]


def test_dock_count_passes_and_ignores_stale(monkeypatch):
    """10초 넘게 안 들린 셀은 세지 않는다 — 들리는 12대면 통과하고 목록을 돌려준다."""
    monkeypatch.setattr(Q.charge, "plug_set", lambda on, say: pytest.fail("플러그를 건드리면 안 된다"))
    cells = {str(s): {"age_s": 1.0} for s in range(10689, 10701)} | {"10677": {"age_s": 60.0}}
    monkeypatch.setattr(Q, "qc_now", lambda: {"cells": cells})
    assert Q.dock_count_ok(12, lambda m: None) == list(range(10689, 10701))


def test_dock_count_none_only_counts(monkeypatch):
    """독 전체(expect None)는 몇 대든 막지 않는다."""
    heard(monkeypatch, range(10677, 10700))
    assert len(Q.dock_count_ok(None, lambda m: None)) == 23


def test_lock_ready_uses_need(monkeypatch):
    """위치 잡기 기준을 배치 대수로 낮춘다 — 12대 배치가 20대 기준에서 20분 뒤 멈추던 것(11-17 10:30)."""
    seen = []
    monkeypatch.setattr(Q, "start_tap", lambda *a: "tap")
    monkeypatch.setattr(Q.time, "sleep", lambda s: None)
    monkeypatch.setattr(Q, "wait_lock", lambda say, need=Q.LOCK_MIN, max_s=0: seen.append(need) or True)
    assert Q.lock_ready("GNSS-MOVE", 10, Path("."), lambda m: None, need=12) == "tap"
    assert seen == [12]


def test_floor_check_recycles_after_long_floor():
    """켜져 있는데 바닥 전력(10 ~ 35 W)이 2시간 이어지면 한 번 끊었다 켠다 — 11-16 밤 24 ~ 34 W 로 충전 안 함."""
    act, since = C.floor_check(None, True, 24.1, 0.0, None, 10.0, 35.0)
    assert (act, since) == (None, 0.0)
    act, since = C.floor_check(None, True, 33.8, C.FLOOR_RECYCLE_S - 1, since, 10.0, 35.0)
    assert act is None
    act, since = C.floor_check(None, True, 33.8, C.FLOOR_RECYCLE_S, since, 10.0, 35.0)
    assert (act, since) == ("recharge", None)


def test_floor_check_resets_when_charging_or_other_action():
    """충전 중(바닥 위) · 막힘(10 W 아래) · 꺼짐 · 다른 처리가 있으면 바닥 시간을 다시 센다."""
    assert C.floor_check(None, True, 55.0, 100.0, 0.0, 10.0, 35.0) == (None, None)
    assert C.floor_check(None, True, 9.4, 100.0, 0.0, 10.0, 35.0) == (None, None)
    assert C.floor_check(None, False, 0.0, 100.0, 0.0, 10.0, 35.0) == (None, None)
    assert C.floor_check("recharge", True, 9.4, 100.0, 0.0, 10.0, 35.0) == ("recharge", None)
