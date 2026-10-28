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


# ---------------------------------------------------------------- 검토 반영 (2026-10-28 적대적 검토)

def test_long_window_is_centered_between_a_and_b(rl, monkeypatch, tmp_path):
    """창을 A 가운데로 잡으면 A 바깥쪽(레일 끝 쪽)으로 거리만큼 열린다 — A · B 의 가운데로, 반거리 + 15 mm."""
    rm = rl
    _put(rm.APPROVAL_LONG, LONG)
    wire(rm, monkeypatch, speed=3000)
    reason, info, *_ = run_move(rm, "long")
    a, b = PLUS - 200_000, PLUS - 260_000
    lo, hi = info["long"]["window"]
    assert (lo + hi) // 2 == (a + b) // 2 and hi - lo == 2 * (30_000 + 15_000), (lo, hi)


def test_long_refuses_when_marker_ends_before_run(rl, monkeypatch, tmp_path):
    """입회 표지가 long 시간 + 여유 10분 전에 끝나면 어댑터를 열기 전에 거부 — 도중에 만료 Quick stop 으로 서지 않게."""
    rm = rl
    _put(rm.APPROVAL_LONG, {**LONG, "minutes": 30})
    now = __import__("time").time()
    _put(rm.MARKER, {"by": "김건우", "created": now - 1500, "until": now + 3600})   # 만든 지 25분 — 60분 마감까지 35분 < 40분
    reason, info, m, *_ = run_move(rm, "long")
    assert "입회 표지" in reason and "분" in reason and m.opens == [], reason


@pytest.mark.parametrize("plus, a_mm, b_mm", [("왼쪽", 260, 200), ("오른쪽", 200, 260), ("오른쪽", 260, 200)])
def test_long_targets_follow_axis_and_order(rl, monkeypatch, tmp_path, plus, a_mm, b_mm):
    """+ 가 오른쪽인 축 · B 가 A 보다 왼쪽인 경우도 — 보내는 목표는 레일 검사를 받은 from_left(A) · from_left(B) 그대로."""
    rm = rl
    ax_doc = {**mm.AXIS_GOOD, "plus_direction": plus}
    _put(rm.AXIS, ax_doc)
    _put(rm.APPROVAL_LONG, {**LONG, "a_from_left_mm": a_mm, "b_from_left_mm": b_mm})
    wire(rm, monkeypatch, speed=3000)
    reason, info, *_ = run_move(rm, "long")
    assert reason == rm.DONE_LONG, reason
    d = doc()
    want_a, want_b = rail.from_left(d, a_mm, plus == "왼쪽"), rail.from_left(d, b_mm, plus == "왼쪽")
    assert info["start"]["target"] == want_a
    legs = [g["target"] for g in info["legs"] if g.get("cycle")]
    assert legs == [want_b, want_a] * (len(legs) // 2) and legs, legs


def test_long_sends_the_rail_checked_b_exactly(rl, monkeypatch, tmp_path):
    """반올림 때문에 p0 + D 와 from_left(B) 가 1 pulse 다를 수 있다 — 검사받은 B 를 그대로 보낸다."""
    rm = rl
    _put(rm.APPROVAL_LONG, {**LONG, "a_from_left_mm": 200.0005, "b_from_left_mm": 260.0015})
    wire(rm, monkeypatch, speed=3000)
    reason, info, *_ = run_move(rm, "long")
    d = doc()
    assert [g["target"] for g in info["legs"] if g.get("cycle")][0] == rail.from_left(d, 260.0015, True)


def test_long_time_limit_decides_cycle_count(rl, monkeypatch, tmp_path):
    rm = rl
    _put(rm.APPROVAL_LONG, {**LONG, "minutes": 0.001})                   # 0.06초 — 첫 왕복 하나만 (시작은 시간 안)
    wire(rm, monkeypatch, speed=3000)
    reason, info, *_ = run_move(rm, "long")
    assert reason == rm.DONE_LONG and info["long"]["cycles"] == 1, info.get("long")
    _put(rm.APPROVAL_LONG, {**LONG, "minutes": 0.04})                    # 2.4초 — 왕복 하나에 약 0.7초
    reason, info, *_ = run_move(rm, "long")
    assert reason == rm.DONE_LONG and info["long"]["cycles"] >= 2, info.get("long")


def test_long_keeps_emptying_the_pdo_log_while_running(rl, monkeypatch, tmp_path):
    """원래 idle(기록 비우기)을 계속 부른다 — 안 부르면 30분 내내 주기 행이 메모리에 쌓이고 CSV 는 끝에만 써진다."""
    rm = rl
    _put(rm.APPROVAL_LONG, {**LONG, "minutes": 0.02})
    monkeypatch.setattr(rm, "LONG_STATUS_S", 0.2)
    takes, marks = [], []
    orig = rm.rec.Log.take
    monkeypatch.setattr(rm.rec.Log, "take", lambda self, rows: (takes.append(1), orig(self, rows))[1])
    monkeypatch.setattr(rm.proc, "console", lambda s: marks.append(len(takes)) if "[LONG]" in str(s) else None)
    wire(rm, monkeypatch, speed=3000)
    reason, info, *_ = run_move(rm, "long")
    assert len(marks) >= 3 and marks[-1] - marks[0] > 10, marks


def test_long_ctrl_c_mid_leg_records_the_leg(rl, monkeypatch, tmp_path):
    rm = rl
    _put(rm.APPROVAL_LONG, {**LONG, "minutes": 0.2})
    seen = {"n": 0}

    def on_tick(c, v):
        seen["n"] += 1
        if seen["n"] == 7_000:                                             # 시작 이동(170 mm · 약 5.7초) 뒤 첫 왕복 도중
            import _thread
            _thread.interrupt_main()
    wire(rm, monkeypatch, speed=30, on_tick=on_tick)
    reason, info, *_ = run_move(rm, "long")
    assert "interrupted_leg" in info and info["interrupted_leg"]["name"].startswith("long"), info.get("interrupted_leg")


def test_leg_stats_includes_the_row_at_t1():
    trace = [(t, 0, 0, 0, 0, 1, t * 10, 9 if t == 50_020 else 1) for t in range(100_000)]
    assert m2._leg_stats(trace, 50_000, 50_020)["ferr_max"] == 9


# ---------------------------------------------------------------- 끝 멈춤 (2차부터 — 셀 움직임 판정의 꺼짐 · 멈춘 자리 평균을 재려고)

@pytest.mark.parametrize("value", [-0.1, 10.5, True, "5"])
def test_long_approval_refuses_bad_dwell(value):
    why = rail.long_approval_problem({**LONG, "dwell_s": value})
    assert why and "dwell_s" in why, why


def test_long_approval_dwell_is_optional_and_capped():
    assert rail.long_approval_problem(LONG) is None                          # 1차 승인 파일(멈춤 칸 없음)도 그대로 통과
    assert rail.long_approval_problem({**LONG, "dwell_s": 10}) is None and rail.LONG_DWELL_MAX_S == 10


def test_long_dwells_at_each_end(rl, monkeypatch, tmp_path):
    rm = rl
    wire(rm, monkeypatch, speed=3000)
    _put(rm.APPROVAL_LONG, {**LONG, "minutes": 0.001})
    _, info0, _, s0, _ = run_move(rm, "long")
    _put(rm.APPROVAL_LONG, {**LONG, "minutes": 0.001, "dwell_s": 0.6})
    reason, info, _, s1, _ = run_move(rm, "long")
    assert reason == rm.DONE_LONG and info["long"]["cycles"] == 1 and info["long"]["dwell_s"] == 0.6
    assert s1 - s0 >= 2 * 0.6 - 0.15, (s0, s1)                              # 왕복 하나 = 끝 두 번 멈춤
