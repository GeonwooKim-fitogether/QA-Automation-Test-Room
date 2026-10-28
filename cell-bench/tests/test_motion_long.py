"""오래 도는 왕복(long) — 레일 위 두 자리 A ↔ B 를 정한 시간 동안 왕복한다. 장비 없이 돈다.

기획: dynamic-bench/design/long-run.md (2026-10-28 김건우 승인).
- 승인 dev/long-approval.json: a_from_left_mm · b_from_left_mm · vmax_mm_s ≤ 100 · amax_mm_s2 ≤ 500 · minutes ≤ 30.
- 두 자리는 레일 한계(양 끝 20 mm 안쪽 + 급정지 거리) 안. 같은 전원이 아니면 먼저 home.
- 시간이 다 되면 그 왕복을 마치고 A 에서 멈춘다. LONG_STATUS_S 마다 화면에 한 줄.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from motionbench import m2, rail  # noqa: E402
import test_motion_m2_modes as mm  # noqa: E402
from test_motion_m2_modes import rm, run_move, wire  # noqa: E402,F401  (rm 은 fixture)
from test_motion_rail import PLUS, _put, doc, rr  # noqa: E402,F401  (rr 은 fixture)

LONG = {"approved_by": "김건우", "approved_at": "2026-10-28T16:00:00+09:00", "a_from_left_mm": 200, "b_from_left_mm": 260,
        "vmax_mm_s": 50, "amax_mm_s2": 250, "minutes": 0.01}


# ---------------------------------------------------------------- 승인 판정 (motionbench/rail.py)

def test_long_approval_good_passes():
    assert rail.long_approval_problem(LONG) is None
    assert rail.LONG_CAPS == {"vmax_mm_s": 100, "amax_mm_s2": 500, "minutes": 30}


@pytest.mark.parametrize("change, word", [
    ({"vmax_mm_s": 101}, "vmax_mm_s"), ({"amax_mm_s2": 501}, "amax_mm_s2"), ({"minutes": 31}, "minutes"),
    ({"minutes": 0}, "minutes"), ({"vmax_mm_s": True}, "vmax_mm_s"), ({"a_from_left_mm": -1}, "a_from_left_mm"),
    ({"b_from_left_mm": float("nan")}, "b_from_left_mm"), ({"b_from_left_mm": 200.5}, "1 mm"), ({"approved_by": " "}, "approved_by"),
])
def test_long_approval_refuses(change, word):
    why = rail.long_approval_problem({**LONG, **change})
    assert why and word in why, why


def test_long_approval_needs_every_field():
    a = dict(LONG)
    del a["minutes"]
    assert "minutes" in rail.long_approval_problem(a)


# ---------------------------------------------------------------- 구간 통계는 그 구간만 본다 (오래 돌면 기록이 길어진다)

class _NoIter(list):
    def __iter__(self):
        raise AssertionError("기록 전체를 훑었다")


def test_leg_stats_reads_only_its_window():
    trace = _NoIter((t, 0, 0, 0, 0, 1, t * 10, t % 7) for t in range(100_000))
    out = m2._leg_stats(trace, 50_000, 50_020)
    assert out["ferr_max"] == 6 and out["ms"] == 0


# ---------------------------------------------------------------- 실행 (가짜 드라이브)

@pytest.fixture
def rl(rr, monkeypatch, tmp_path):
    """같은 전원 — 레일 파일과 드라이브 0x607D 가 맞는다. long 승인 자리는 tmp."""
    d = doc()
    _put(rr.RAIL, d)
    monkeypatch.setattr(mm, "LIMITS", tuple(d["limits"]))
    monkeypatch.setattr(rr, "APPROVAL_LONG", tmp_path / "long-approval.json")
    return rr


def test_long_goes_to_a_then_shuttles_until_time_and_ends_at_a(rl, monkeypatch, tmp_path):
    rm = rl
    _put(rm.APPROVAL_LONG, LONG)
    wire(rm, monkeypatch, speed=3000)
    reason, info, m, _, _ = run_move(rm, "long")
    assert reason == rm.DONE_LONG, reason
    a, b = PLUS - 200_000, PLUS - 260_000                                    # + = 왼쪽 → 왼쪽 끝에서 멀수록 작다
    assert info["start"]["target"] == a
    legs = [g["target"] for g in info["legs"] if g.get("cycle")]
    assert legs and len(legs) % 2 == 0 and legs == [b, a] * (len(legs) // 2)    # 왕복을 다 마치고 A 에서 멈춘다
    assert info["long"]["cycles"] == len(legs) // 2 and info["long"]["minutes"] == 0.01
    assert info["csv"].endswith(f"motion_long_{info['stamp']}.csv")
    assert info["final"]["pos"] == pytest.approx(a, abs=1000)


def test_long_prints_status_lines(rl, monkeypatch, tmp_path):
    rm = rl
    _put(rm.APPROVAL_LONG, {**LONG, "minutes": 0.02})
    monkeypatch.setattr(rm, "LONG_STATUS_S", 0.2)
    said = []
    monkeypatch.setattr(rm.proc, "console", lambda s: said.append(str(s)))
    wire(rm, monkeypatch, speed=3000)
    reason, info, *_ = run_move(rm, "long")
    assert reason == rm.DONE_LONG, reason
    lines = [s for s in said if "[LONG]" in s]
    assert len(lines) >= 3 and "왕복" in lines[-1] and "추종 오차" in lines[-1], lines


def test_long_refuses_point_outside_rail_before_servo_on(rl, monkeypatch, tmp_path):
    rm = rl
    _put(rm.APPROVAL_LONG, {**LONG, "b_from_left_mm": 1_800})                # 레일 1,815.9 mm — 오른쪽 끝 20 mm 안쪽을 넘는다
    wire(rm, monkeypatch, speed=3000)
    reason, info, *_ = run_move(rm, "long")
    assert "레일 한계" in reason and "servo_on" not in info, reason


def test_long_bad_approval_refused_before_adapter(rl, monkeypatch, tmp_path):
    rm = rl
    _put(rm.APPROVAL_LONG, {**LONG, "minutes": 45})
    reason, info, m, *_ = run_move(rm, "long")
    assert "minutes" in reason and m.opens == [], reason


def test_long_power_cycled_homes_first_then_shuttles(rr, monkeypatch, tmp_path):
    rm = rr                                                                  # 레일 파일 없음 · 0x607D 기본값 — 전원 재투입
    monkeypatch.setattr(rm, "APPROVAL_LONG", tmp_path / "long-approval.json")
    _put(rm.APPROVAL_LONG, LONG)
    wire(rm, monkeypatch, speed=3000)
    reason, info, *_ = run_move(rm, "long")
    assert info["home"]["done"] is True and reason == rm.DONE_LONG, reason
    assert info["start"]["target"] == PLUS - 200_000
