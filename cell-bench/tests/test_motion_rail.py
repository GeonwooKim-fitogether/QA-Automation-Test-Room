"""레일 기준 — 전원을 넣은 자리를 믿지 않고, 끝에 닿아 잡은 양 끝(data/rail-motor1.json)만 믿는다 — 장비 없이 돈다.

기획: dynamic-bench/design/rail-recovery.md (2026-10-28 김건우 승인, 적대적 검토 반영판).
- 같은 전원 판정: 드라이브 0x607D 가 레일 파일의 limits 와 같을 때만. 기본값이든 다른 값이든 home.
  첫 서보 온이 FRESH_MS(0.5초) 넘게 걸려도 home. 실행 끝에는 0x607D 를 레일 한계로 써 둔다(되돌리지 않는다).
- home: ② 빠른 확인(기억한 last_pos 로 가까운 끝에 닿아 2 mm 안이면 끝) → 아니면 ③ 전체(+ 쪽 끝 → − 쪽 끝).
  적재물(payload)이 레일 파일과 다르면 ③ 강제. 양 끝 차이가 그 적재물의 길이와 2 mm 넘게 다르면 레일 파일을 쓰지 않는다.
- 모든 목표는 양 끝에서 20 mm 안쪽. goto = 왼쪽 끝에서 잰 절대 위치로.
가짜 드라이브의 벽(FakeDrive.wall)이 레일 양 끝이다. 가짜는 0x6081 을 보지 않는다 — 속도 · 감속 곡선은 이 시험이 증명하지 않는다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from motionbench import rail  # noqa: E402
import test_motion_m2_modes as mm  # noqa: E402
from test_motion_m2 import FakeDrive, _marker, _write  # noqa: E402
from test_motion_m2_modes import P0, FakeMaster, rm, run_move, wire  # noqa: E402,F401  (rm 은 fixture)
from test_motion_m3 import S1  # noqa: E402

L = rail.LENGTH
# 시험 축 정의(AXIS_GOOD)는 + = 왼쪽 → 왼쪽 끝 = plus_end
PLUS, MINUS = P0 + 30_000, P0 + 30_000 - L                 # 시작점에서 왼쪽 끝 30 mm, 오른쪽 끝 1,785.9 mm
HOME = {"approved_by": "김건우", "approved_at": "2026-10-28T11:00:00+09:00", "payload": "지그만",
        "creep_mm_s": 10, "torque_permil": 300, "touch_ferr_um": 500, "back_mm": 10}
GOTO = {"approved_by": "김건우", "approved_at": "2026-10-28T11:00:00+09:00", "to_from_left_mm": 300, "vmax_mm_s": 50, "amax_mm_s2": 250}


def doc(plus=PLUS, minus=MINUS, payload="지그만", last=None, lengths=None):
    return rail.make(plus, minus, payload, ["plus", "minus"], lengths or {payload: plus - minus}, 55, "2026-10-28T11:00:00+09:00", last)


# ---------------------------------------------------------------- 순수 판정 (motionbench/rail.py)

def test_constants_from_measurement():
    assert rail.LENGTH == 1_815_913 and rail.MARGIN == 20_000 and rail.FRESH_MS == 500 and rail.TOL == 2_000
    assert rail.SEARCH_EXTRA == 30_000


def test_make_sets_limits_inside_both_ends():
    d = doc()
    assert d["limits"] == [MINUS + 20_000, PLUS - 20_000] and rail.problem(d) is None
    assert d["length"] == PLUS - MINUS


@pytest.mark.parametrize("change", [{"plus_end": None}, {"limits": [1, 2, 3]}, {"payload": ""}, {"minus_end": PLUS + 1}])
def test_problem_refuses_broken_rail(change):
    assert rail.problem({**doc(), **change})
    assert rail.problem(None) and rail.problem([])


def test_same_power_only_when_drive_holds_our_limits():
    d = doc()
    assert rail.same_power(d, tuple(d["limits"]))
    assert not rail.same_power(d, (-2**31, 2**31 - 1))           # 전원 재투입 뒤 기본값
    assert not rail.same_power(d, (P0 - 15_000, P0 + 15_000))     # 비정상 종료로 남은 창 값
    assert not rail.same_power(None, tuple(d["limits"]))


def test_quick_plan_goes_to_nearest_end_from_memory():
    d = doc(last=PLUS - 25_000)                                     # 기억: 왼쪽 끝에서 25 mm
    q = rail.quick_plan(d, 0, "지그만")                             # 전원 재투입 — 지금 위치 0
    assert q == {"side": 1, "expect": 25_000, "reach": 25_000 + rail.SEARCH_EXTRA}
    q = rail.quick_plan(doc(last=MINUS + 40_000), 7, "지그만")
    assert q == {"side": -1, "expect": 7 - 40_000, "reach": 40_000 + rail.SEARCH_EXTRA}


@pytest.mark.parametrize("why", ["no_doc", "no_memory", "other_payload", "no_length"])
def test_quick_plan_none_forces_full(why):
    d = {"no_doc": None, "no_memory": doc(), "other_payload": doc(last=PLUS - 25_000),
         "no_length": {**doc(last=PLUS - 25_000), "lengths": {}}}[why]
    assert rail.quick_plan(d, 0, "셀 세트" if why == "other_payload" else "지그만") is None


def test_length_problem_tolerance():
    assert rail.length_problem(L + 2_000, L) is None
    assert "2 mm" in rail.length_problem(L + 2_001, L)


def test_left_and_target_checks():
    d = doc()
    assert rail.from_left(d, 300, plus_is_left=True) == PLUS - 300_000
    assert rail.from_left(d, 300, plus_is_left=False) == MINUS + 300_000
    assert rail.target_problem(PLUS - 20_000, d) is None
    assert "레일 한계" in rail.target_problem(PLUS - 19_999, d)
    assert "레일 한계" in rail.target_problem(MINUS + 19_999, d)


def test_home_approval():
    assert rail.home_approval_problem(HOME) is None
    assert "절대 상한" in rail.home_approval_problem({**HOME, "creep_mm_s": 11})
    assert "절대 상한" in rail.home_approval_problem({**HOME, "torque_permil": 501})
    assert "touch_ferr_um" in rail.home_approval_problem({**HOME, "touch_ferr_um": 2_001})
    assert "payload" in rail.home_approval_problem({**HOME, "payload": " "})
    assert "필드 빠짐" in rail.home_approval_problem({k: v for k, v in HOME.items() if k != "approved_by"})


def test_goto_approval():
    assert rail.goto_approval_problem(GOTO) is None
    assert "절대 상한" in rail.goto_approval_problem({**GOTO, "vmax_mm_s": 101})
    assert "절대 상한" in rail.goto_approval_problem({**GOTO, "amax_mm_s2": 501})
    assert "to_from_left_mm" in rail.goto_approval_problem({**GOTO, "to_from_left_mm": -1})


# ---------------------------------------------------------------- run_motion (가짜 드라이브 · 레일 양 끝 = 벽)

@pytest.fixture
def rr(rm, monkeypatch, tmp_path):
    """rm 위에 레일 · home · goto · m3 승인 자리를 tmp 로, 벽 = 레일 양 끝, 드라이브 0x607D 는 기본값(전원 재투입 뒤)."""
    monkeypatch.setattr(rm, "APPROVAL_HOME", tmp_path / "home-approval.json")
    monkeypatch.setattr(rm, "APPROVAL_GOTO", tmp_path / "goto-approval.json")
    monkeypatch.setattr(rm, "APPROVAL_M3", tmp_path / "m3-approval.json")
    rm.RAIL.unlink(missing_ok=True)                       # rm 이 깔아 둔 레일을 걷어 낸다 — 이 시험들은 스스로 정한다
    monkeypatch.setattr(mm, "LIMITS", (-2**31, 2**31 - 1))
    monkeypatch.setattr(FakeDrive, "wall", (MINUS, PLUS))
    (tmp_path / "home-approval.json").write_text(json.dumps(HOME, ensure_ascii=False), encoding="utf-8")
    _marker(tmp_path)
    return rm


def _put(path, d):
    Path(path).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def _rail_now(rm):
    return json.loads(rm.RAIL.read_text(encoding="utf-8"))


def _puts(box, key):
    return [v for i, s, v in box["slave"].puts if (i, s) == key]


def test_home_full_finds_both_ends_and_marks_drive(rr, monkeypatch):
    rm = rr
    box = wire(rm, monkeypatch, speed=3000)
    reason, info, m, _, _ = run_move(rm, "home")
    assert reason == rm.DONE_HOME, reason
    r = _rail_now(rm)
    assert (r["plus_end"], r["minus_end"]) == (PLUS, MINUS) and r["method"] == "full"
    assert r["length"] == L and r["lengths"] == {"지그만": L} and r["payload"] == "지그만"
    assert _puts(box, (0x607D, 1))[-1] == r["limits"][0] and _puts(box, (0x607D, 2))[-1] == r["limits"][1]
    assert _puts(box, (0x6072, 0)) == [300, 1234]          # 힘 상한을 서보 온 전에 낮추고 끝에 되돌린다
    assert info["torque_low_before_servo_on"] is True
    assert r["last_pos"] == info["final"]["pos"] and MINUS + 9_000 <= r["last_pos"] <= MINUS + 11_000   # − 쪽 끝에서 10 mm 물러남


def test_home_quick_when_memory_holds(rr, monkeypatch):
    rm = rr
    # 지난 전원: 왼쪽 끝 = P0 + 500_000, 마지막 위치 = 거기서 30 mm 안쪽 → 지금(P0)도 같은 자리라면 왼쪽 끝은 P0 + 30 mm
    old = doc(plus=P0 + 500_000, minus=P0 + 500_000 - L, last=P0 + 500_000 - 30_000)
    _put(rm.RAIL, old)
    box = wire(rm, monkeypatch, speed=3000)
    reason, info, m, _, _ = run_move(rm, "home")
    assert reason == rm.DONE_HOME, reason
    r = _rail_now(rm)
    assert r["method"] == "quick" and r["measured"] == ["plus"] and r["plus_end"] == PLUS and r["minus_end"] == PLUS - L
    assert box["cyc"].far < 40_000                              # 반대쪽 끝까지 가지 않았다


def test_home_quick_mismatch_falls_back_to_full(rr, monkeypatch):
    rm = rr
    old = doc(plus=P0 + 500_000, minus=P0 + 500_000 - L, last=P0 + 500_000 - 26_000)   # 기억은 26 mm — 실제는 30 mm (누가 4 mm 밀었다)
    _put(rm.RAIL, old)
    wire(rm, monkeypatch, speed=3000)
    reason, info, m, _, _ = run_move(rm, "home")
    assert reason == rm.DONE_HOME, reason
    r = _rail_now(rm)
    assert r["method"] == "full" and (r["plus_end"], r["minus_end"]) == (PLUS, MINUS)
    assert info["home"]["quick"]["ok"] is False


def test_home_new_payload_forces_full_and_learns_length(rr, monkeypatch, tmp_path):
    rm = rr
    _put(rm.RAIL, doc(plus=P0 + 500_000, minus=P0 + 500_000 - L, last=P0 + 500_000 - 30_000))
    _put(tmp_path / "home-approval.json", {**HOME, "payload": "셀 세트"})
    monkeypatch.setattr(FakeDrive, "wall", (MINUS + 5_000, PLUS))           # 독이 튀어나와 오른쪽 끝에 5 mm 먼저 닿는다
    wire(rm, monkeypatch, speed=3000)
    reason, info, m, _, _ = run_move(rm, "home")
    assert reason == rm.DONE_HOME, reason
    r = _rail_now(rm)
    assert r["method"] == "full" and r["payload"] == "셀 세트"
    assert r["lengths"] == {"지그만": L, "셀 세트": L - 5_000} and info["home"]["length_vs"] == {"지그만": -5_000}


def test_home_length_mismatch_writes_no_rail(rr, monkeypatch):
    rm = rr
    _put(rm.RAIL, doc(plus=P0 + 500_000, minus=P0 + 500_000 - L))           # 기억 없음 → 전체
    monkeypatch.setattr(FakeDrive, "wall", (MINUS + 3_000, PLUS))           # 같은 적재물인데 3 mm 짧다 — 장애물
    box = wire(rm, monkeypatch, speed=3000)
    reason, info, m, _, _ = run_move(rm, "home")
    assert "2 mm" in reason and "길이" in reason, reason
    assert _rail_now(rm)["plus_end"] == P0 + 500_000                         # 옛 파일 그대로
    assert _puts(box, (0x607D, 1)) == []                                    # 표시를 쓰지 않았다 — 다음 실행도 home


def test_home_refused_before_adapter_without_home_approval(rr, monkeypatch, tmp_path):
    rm = rr
    (tmp_path / "home-approval.json").unlink()
    wire(rm, monkeypatch)
    reason, info, m, _, _ = run_move(rm, "home")
    assert reason.startswith("승인 파일 거부") and m.opens == []


def test_same_power_m3_runs_without_home_and_leaves_marker(rr, monkeypatch, tmp_path):
    rm = rr
    d = doc()
    _put(rm.RAIL, d)
    monkeypatch.setattr(mm, "LIMITS", tuple(d["limits"]))
    _put(tmp_path / "m3-approval.json", S1)                                  # +1 → 왼쪽으로 60 mm — 왼쪽 끝 30 mm 라 넘는다
    wire(rm, monkeypatch, speed=3000)
    reason, info, m, _, _ = run_move(rm, "m3")
    assert "레일 한계" in reason and "servo_on" not in info, reason
    _put(tmp_path / "m3-approval.json", {**S1, "first_move_dir": -1})
    box = wire(rm, monkeypatch, speed=3000)
    reason, info, m, _, _ = run_move(rm, "m3")
    assert reason == rm.DONE_M2, reason
    assert "home" not in info and _puts(box, (0x6072, 0)) == []
    assert _puts(box, (0x607D, 1)) == [d["limits"][0]] * 2                   # 시작에 쓰고 끝에 그대로 둔다 — 기본값으로 되돌리지 않는다
    assert _rail_now(rm)["last_pos"] == info["final"]["pos"]


def test_power_cycled_m3_homes_first_then_needs_start_position(rr, monkeypatch, tmp_path):
    rm = rr
    _put(rm.RAIL, doc())                                                     # 드라이브 0x607D 는 기본값 — 전원 재투입
    _put(tmp_path / "m3-approval.json", {**S1, "first_move_dir": -1})
    wire(rm, monkeypatch, speed=3000)
    reason, info, m, _, _ = run_move(rm, "m3")
    assert "home" in info and info["home"]["done"] is True
    assert "start_from_left_mm" in reason, reason
    assert [g for g in info["legs"] if g.get("cycle")] == []                 # 왕복은 하지 않았다


def test_power_cycled_m3_with_start_position_continues_after_home(rr, monkeypatch, tmp_path):
    rm = rr
    _put(tmp_path / "m3-approval.json", {**S1, "first_move_dir": -1, "start_from_left_mm": 200})
    wire(rm, monkeypatch, speed=3000)
    reason, info, m, _, _ = run_move(rm, "m3")
    assert reason == rm.DONE_M2, reason
    start = PLUS - 200_000
    assert info["start"]["target"] == start
    assert [g["target"] for g in info["legs"] if g.get("cycle")] == [start - 60_000, start] * 3


def test_fresh_servo_on_triggers_home_even_if_marker_matches(rr, monkeypatch, tmp_path):
    rm = rr
    d = doc()
    _put(rm.RAIL, d)
    monkeypatch.setattr(mm, "LIMITS", tuple(d["limits"]))
    _put(tmp_path / "m3-approval.json", {**S1, "first_move_dir": -1, "start_from_left_mm": 200})
    orig = FakeDrive.__init__

    def slow(self, ax, pos, speed=40, estimate_s=0.0, swing=0, drift=0):
        orig(self, ax, pos, speed, estimate_s=0.7, swing=500, drift=0)
    monkeypatch.setattr(FakeDrive, "__init__", slow)
    wire(rm, monkeypatch, speed=3000)
    reason, info, m, _, _ = run_move(rm, "m3")
    assert info["servo_on"]["enable_ms"] > rail.FRESH_MS and info["home"]["why"].startswith("첫 서보 온")
    assert reason == rm.DONE_M2, reason


def test_goto_moves_to_position_from_left_end(rr, monkeypatch, tmp_path):
    rm = rr
    d = doc()
    _put(rm.RAIL, d)
    monkeypatch.setattr(mm, "LIMITS", tuple(d["limits"]))
    _put(rm.APPROVAL_GOTO, GOTO)
    wire(rm, monkeypatch, speed=3000)
    reason, info, m, _, _ = run_move(rm, "goto")
    assert reason == rm.DONE_GOTO, reason
    assert [g["target"] for g in info["legs"]] == [PLUS - 300_000] and info["legs"][0]["err_um"] <= 50
    assert info["axis"]["words"] == "왼쪽 끝에서 300 mm 자리로 · 50 mm/s"


def test_goto_inside_margin_refused(rr, monkeypatch, tmp_path):
    rm = rr
    d = doc()
    _put(rm.RAIL, d)
    monkeypatch.setattr(mm, "LIMITS", tuple(d["limits"]))
    _put(rm.APPROVAL_GOTO, {**GOTO, "to_from_left_mm": 10})
    wire(rm, monkeypatch)
    reason, info, m, _, _ = run_move(rm, "goto")
    assert "레일 한계" in reason and "servo_on" not in info


def test_main_writes_home_result_file(rr, monkeypatch):
    rm = rr
    wire(rm, monkeypatch, speed=3000)
    monkeypatch.setattr(rm.proc, "safe_stdio", lambda: None)
    monkeypatch.setattr(sys, "argv", ["run_motion.py", "home"])
    assert rm.main() == 0
    found = list(rm.DATA.glob("motion_home_*.json"))
    assert len(found) == 1 and json.loads(found[0].read_text(encoding="utf-8"))["exit"] == rm.DONE_HOME
