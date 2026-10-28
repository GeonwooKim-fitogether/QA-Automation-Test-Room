"""레일 기준 — 전원을 넣은 자리를 믿지 않고, 끝 터치로 잡은 양 끝(data/rail.json)만 믿는다 — 장비 없이 돈다.

규칙 (2026-10-28 김건우): 전원을 켜면 가까운 끝을 터치해 양 끝을 잡는다(한쪽만 터치하면 레일 길이로 반대쪽을 계산).
모든 이동 목표는 양 끝에서 RAIL_MARGIN(20 mm) 안쪽. 전원 재투입은 첫 서보 온이 FRESH_MS 보다 오래 걸리는 것으로 알아채고
(자극 추정, 실측 1,310~1,551 ms · 그 뒤 52~58 ms), 그때는 터치 말고는 움직이지 않는다. goto 모드 = 왼쪽 끝에서 잰 절대 위치로 이동.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from motionbench import safety  # noqa: E402
from test_motion_m2 import FakeDrive, _marker, _write  # noqa: E402
from test_motion_m2_modes import P0, FakeMaster, rm, run_move, wire  # noqa: E402,F401  (rm 은 fixture)
from test_motion_m3 import S1  # noqa: E402
from test_motion_touch import T, WALL  # noqa: E402

L = safety.RAIL_LENGTH
# 시험 축 정의(AXIS_GOOD)는 + = 왼쪽. 왼쪽 끝 = plus_end, 오른쪽 끝 = minus_end
RAIL = {"plus_end": 20_000, "minus_end": 20_000 - L, "measured": ["plus"], "at": "2026-10-28T10:40:00+09:00", "enable_ms": 1400}
GOTO = {"approved_by": "김건우", "approved_at": "2026-10-28T10:50:00+09:00", "to_from_left_mm": 300, "vmax_mm_s": 50, "amax_mm_s2": 250}


# ---------------------------------------------------------------- 순수 판정

def test_rail_constants_from_measurement():
    assert safety.RAIL_LENGTH == 1_815_913 and safety.RAIL_MARGIN == 20_000 and safety.FRESH_MS == 500


def test_rail_problem_accepts_valid_and_refuses_bad():
    assert safety.rail_problem(RAIL) is None
    assert "없다" in safety.rail_problem(None)
    assert "plus_end" in safety.rail_problem({**RAIL, "plus_end": None})
    assert "길이" in safety.rail_problem({**RAIL, "minus_end": 0})            # 양 끝 차이가 레일 길이와 2 mm 넘게 다르다
    assert safety.rail_problem({**RAIL, "minus_end": RAIL["minus_end"] + 1_500}) is None


def test_rail_limits_are_margin_inside_both_ends():
    assert safety.rail_limits(RAIL) == (RAIL["minus_end"] + 20_000, RAIL["plus_end"] - 20_000)


def test_rail_from_one_touch_computes_the_other_end():
    r = safety.rail_from_touch(None, "plus", 18_878, 1400)
    assert r["plus_end"] == 18_878 and r["minus_end"] == 18_878 - L and r["measured"] == ["plus"]
    r2 = safety.rail_from_touch(r, "minus", 18_878 - L + 100, 55)           # 반대쪽도 터치 — 실측으로 바꾼다
    assert r2["minus_end"] == 18_878 - L + 100 and sorted(r2["measured"]) == ["minus", "plus"]
    assert safety.rail_problem(r2) is None


def test_rail_from_touch_after_fresh_power_discards_old_ends():
    old = safety.rail_from_touch(None, "plus", 18_878, 1400)
    old = safety.rail_from_touch(old, "minus", 18_878 - L, 55)
    new = safety.rail_from_touch(old, "minus", -1_797_035, 1500, fresh=True)   # 전원 재투입 뒤 첫 터치
    assert new["measured"] == ["minus"] and new["plus_end"] == -1_797_035 + L


def test_goto_approval():
    assert safety.goto_approval_problem(GOTO) is None
    assert "절대 상한" in safety.goto_approval_problem({**GOTO, "vmax_mm_s": 101})
    assert "절대 상한" in safety.goto_approval_problem({**GOTO, "amax_mm_s2": 501})
    assert "to_from_left_mm" in safety.goto_approval_problem({**GOTO, "to_from_left_mm": -1})
    assert "필드 빠짐" in safety.goto_approval_problem({k: v for k, v in GOTO.items() if k != "to_from_left_mm"})


def test_target_outside_rail_refused():
    lo, hi = safety.rail_limits(RAIL)
    assert safety.rail_target_problem(hi, lo, hi) is None
    assert "레일 한계" in safety.rail_target_problem(hi + 1, lo, hi)
    assert "레일 한계" in safety.rail_target_problem(lo - 1, lo, hi)


# ---------------------------------------------------------------- run_motion (가짜 드라이브)

@pytest.fixture
def rr(rm, monkeypatch, tmp_path):
    """rm 위에 레일 파일 · goto 승인 · touch 승인 · m3 승인 자리를 tmp 로."""
    monkeypatch.setattr(rm, "RAIL", rm.DATA / "rail.json")
    monkeypatch.setattr(rm, "APPROVAL_GOTO", tmp_path / "goto-approval.json")
    monkeypatch.setattr(rm, "APPROVAL_TOUCH", tmp_path / "touch-approval.json")
    monkeypatch.setattr(rm, "APPROVAL_M3", tmp_path / "m3-approval.json")
    return rm


def _rail(rm, doc=RAIL):
    rm.RAIL.write_text(json.dumps(doc), encoding="utf-8")


def test_m3_refused_before_adapter_without_rail(rr, monkeypatch, tmp_path):
    rm = rr
    (tmp_path / "m3-approval.json").write_text(json.dumps(S1), encoding="utf-8")
    _marker(tmp_path)
    wire(rm, monkeypatch)
    reason, info, m, _, _ = run_move(rm, "m3")
    assert "끝 터치" in reason and "rail.json" in reason and m.opens == []


def test_m3_with_rail_sets_soft_limits_inside_rail_and_runs(rr, monkeypatch, tmp_path):
    rm = rr
    _rail(rm)
    (tmp_path / "m3-approval.json").write_text(json.dumps(S1), encoding="utf-8")
    _marker(tmp_path)
    box = wire(rm, monkeypatch, speed=400)
    reason, info, m, _, _ = run_move(rm, "m3")
    assert reason == rm.DONE_M2, reason
    lo, hi = safety.rail_limits(RAIL)
    puts = {}
    for i, s, v in box["slave"].puts:
        puts.setdefault((i, s), v)
    assert puts[(0x607D, 1)] == max(P0 - 75_000, lo) and puts[(0x607D, 2)] == min(P0 + 75_000, hi)
    assert info["rail"] == RAIL


def test_m3_target_beyond_rail_end_refused_before_servo_on(rr, monkeypatch, tmp_path):
    rm = rr
    _rail(rm, {**RAIL, "plus_end": P0 + 30_000, "minus_end": P0 + 30_000 - L})   # 왼쪽 끝이 시작점에서 30 mm — 60 mm 왕복은 끝을 넘는다
    (tmp_path / "m3-approval.json").write_text(json.dumps(S1), encoding="utf-8")
    _marker(tmp_path)
    box = wire(rm, monkeypatch)
    reason, info, m, _, _ = run_move(rm, "m3")
    assert "레일 한계" in reason and "servo_on" not in info and box["slave"].puts == []


def test_fresh_power_servo_on_stops_before_any_move(rr, monkeypatch, tmp_path):
    """전원 재투입 뒤 첫 서보 온(자극 추정 > FRESH_MS)이면 레일 파일이 있어도 움직이지 않는다 — 터치부터."""
    rm = rr
    _rail(rm)
    (tmp_path / "m3-approval.json").write_text(json.dumps(S1), encoding="utf-8")
    _marker(tmp_path)
    box = wire(rm, monkeypatch, speed=400)
    monkeypatch.setattr(FakeDrive, "__init__", _slow_estimate(FakeDrive.__init__))
    reason, info, m, _, _ = run_move(rm, "m3")
    assert "전원 재투입" in reason and "끝 터치" in reason, reason
    assert info["servo_on"]["enable_ms"] > safety.FRESH_MS and "legs" in info and info["legs"] == []
    assert box["cyc"].far <= 2_000                                            # 추정 움찔 말고는 안 갔다


def _slow_estimate(orig):
    def init(self, ax, pos, speed=40, estimate_s=0.0, swing=0, drift=0):
        orig(self, ax, pos, speed, estimate_s=0.7, swing=1_000, drift=300)
    return init


def test_touch_after_fresh_power_writes_new_rail(rr, monkeypatch, tmp_path):
    rm = rr
    _rail(rm, {**RAIL, "plus_end": 999_999, "minus_end": 999_999 - L})          # 옛 값 — 버려져야 한다
    (tmp_path / "touch-approval.json").write_text(json.dumps(T, ensure_ascii=False), encoding="utf-8")
    _marker(tmp_path)
    monkeypatch.setattr(FakeDrive, "wall", (-10**9, P0 + WALL))
    monkeypatch.setattr(FakeDrive, "__init__", _slow_estimate(FakeDrive.__init__))
    wire(rm, monkeypatch, speed=5)
    reason, info, m, _, _ = run_move(rm, "touch")
    assert reason == rm.DONE_TOUCH, reason
    r = json.loads(rm.RAIL.read_text(encoding="utf-8"))
    assert r["plus_end"] == P0 + WALL and r["minus_end"] == P0 + WALL - L and r["measured"] == ["plus"]
    assert r["enable_ms"] > safety.FRESH_MS and info["rail"] == r


def test_touch_without_fresh_power_keeps_other_measured_end(rr, monkeypatch, tmp_path):
    rm = rr
    _rail(rm, {**RAIL, "measured": ["minus"], "minus_end": P0 + WALL - L + 50})   # 반대쪽은 실측
    (tmp_path / "touch-approval.json").write_text(json.dumps(T, ensure_ascii=False), encoding="utf-8")
    _marker(tmp_path)
    monkeypatch.setattr(FakeDrive, "wall", (-10**9, P0 + WALL))
    wire(rm, monkeypatch, speed=5)
    reason, info, m, _, _ = run_move(rm, "touch")
    assert reason == rm.DONE_TOUCH, reason
    r = json.loads(rm.RAIL.read_text(encoding="utf-8"))
    assert r["plus_end"] == P0 + WALL and r["minus_end"] == P0 + WALL - L + 50 and sorted(r["measured"]) == ["minus", "plus"]


def test_goto_moves_to_absolute_position_from_left_end(rr, monkeypatch, tmp_path):
    rm = rr
    _rail(rm)
    rm.APPROVAL_GOTO.write_text(json.dumps(GOTO, ensure_ascii=False), encoding="utf-8")
    _marker(tmp_path)
    box = wire(rm, monkeypatch, speed=2000)
    reason, info, m, _, _ = run_move(rm, "goto")
    assert reason == rm.DONE_GOTO, reason
    target = RAIL["plus_end"] - 300_000                                         # + = 왼쪽이라 왼쪽 끝에서 300 mm 는 − 쪽
    assert [g["target"] for g in info["legs"]] == [target] and info["legs"][0]["err_um"] <= 50
    lo, hi = safety.rail_limits(RAIL)
    puts = {}
    for i, s, v in box["slave"].puts:
        puts.setdefault((i, s), v)
    assert (puts[(0x607D, 1)], puts[(0x607D, 2)]) == (lo, hi) and puts[(0x6081, 0)] == 50_000
    assert info["axis"]["words"] == "왼쪽 끝에서 300 mm 자리로 (-) · 50 mm/s"


def test_goto_beyond_margin_refused_before_adapter(rr, monkeypatch, tmp_path):
    rm = rr
    _rail(rm)
    rm.APPROVAL_GOTO.write_text(json.dumps({**GOTO, "to_from_left_mm": 10}, ensure_ascii=False), encoding="utf-8")
    _marker(tmp_path)
    wire(rm, monkeypatch)
    reason, info, m, _, _ = run_move(rm, "goto")
    assert "레일 한계" in reason and m.opens == []


def test_m3_can_start_from_absolute_position(rr, monkeypatch, tmp_path):
    rm = rr
    _rail(rm)
    (tmp_path / "m3-approval.json").write_text(json.dumps({**S1, "start_from_left_mm": 200}), encoding="utf-8")
    _marker(tmp_path)
    wire(rm, monkeypatch, speed=2000)
    reason, info, m, _, _ = run_move(rm, "m3")
    assert reason == rm.DONE_M2, reason
    start = RAIL["plus_end"] - 200_000
    assert info["goto"]["target"] == start
    assert [g["target"] for g in info["legs"]] == [start + 60_000, start] * 3     # first_move_dir +1 → + 쪽(왼쪽)으로 60 mm


def test_origin_check_no_longer_blocks_when_rail_known(rr, monkeypatch, tmp_path):
    rm = rr
    _rail(rm)
    (tmp_path / "m3-approval.json").write_text(json.dumps(S1), encoding="utf-8")
    _marker(tmp_path)
    wire(rm, monkeypatch, speed=400)
    fake = rm.ecat.bring_up
    monkeypatch.setattr(rm.ecat, "bring_up", lambda m, info: (fake(m, info)[0], P0 - 9_000))   # 원점에서 9 mm — 옛 규칙이면 거부
    reason, info, m, _, _ = run_move(rm, "m3")
    assert reason == rm.DONE_M2, reason
