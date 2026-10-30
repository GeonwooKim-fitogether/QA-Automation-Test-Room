"""모터 끝 터치 — 아주 느리게 · 미는 힘 상한(0x6072)을 낮춘 채 끝에 닿아 그 위치를 잰다 — 장비 없이 돈다.

닿음 = 추종 오차(0x60F4)가 touch_ferr_um 을 넘는 순간. 그 주기에 Quick stop 걸쇠를 걸고(자동 규칙 알람과 따로 센다),
서보를 다시 켜 back_mm 물러난 뒤 다음 터치. 가짜 드라이브의 벽(FakeDrive.wall)은 위치를 막고, 막힌 동안 추종 오차를 쌓는다.
매뉴얼 근거: 0x6072 Max torque(0.1 %) 와 Pr0.13 중 작은 쪽이 실제 상한(SX-DSV03241 PDF 161쪽), 0x60F4 는 PDO 입력.
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
from motionbench import m2, safety  # noqa: E402
from test_motion_m2 import FakeDrive, _marker, _write  # noqa: E402
from test_motion_m2_modes import P0, FakeMaster, rm, run_move, wire  # noqa: E402,F401  (rm 은 fixture)

T = {"approved_by": "김건우", "approved_at": "2026-10-27T19:30:00+09:00", "side": "왼쪽", "creep_mm_s": 5,
     "max_mm": 30, "torque_permil": 300, "touch_ferr_um": 500, "touches": 3, "back_mm": 10}
WALL = 20_000                            # 시작점에서 + 쪽(시험 축 정의에서 + = 왼쪽) 20 mm 에 끝


# ---------------------------------------------------------------- 승인 판정

def test_touch_caps():
    assert safety.TOUCH_CAPS == {"creep_mm_s": 5, "max_mm": 40, "torque_permil": 500, "touches": 4, "back_mm": 20}
    assert safety.TOUCH_FERR_UM == (100, 1000)


def test_touch_approval_accepted():
    assert safety.touch_approval_problem(T) is None


@pytest.mark.parametrize("key,value", [("creep_mm_s", 6), ("max_mm", 41), ("torque_permil", 501), ("touches", 5), ("back_mm", 21)])
def test_touch_values_above_caps_refused(key, value):
    assert "절대 상한" in safety.touch_approval_problem({**T, key: value})


@pytest.mark.parametrize("value", [99, 1001, 0, "500"])
def test_touch_ferr_out_of_band_refused(value):
    assert "touch_ferr_um" in safety.touch_approval_problem({**T, "touch_ferr_um": value})


@pytest.mark.parametrize("side", ["left", "", None, "위"])
def test_touch_side_must_be_left_or_right(side):
    assert "side" in safety.touch_approval_problem({**T, "side": side})


def test_back_off_must_be_shorter_than_reach():
    assert "back_mm" in safety.touch_approval_problem({**T, "max_mm": 10, "back_mm": 10})


def test_touch_missing_field_refused():
    assert "필드 빠짐" in safety.touch_approval_problem({k: v for k, v in T.items() if k != "approved_by"})


def test_load_touch_approval(tmp_path):
    assert safety.load_touch_approval(_write(tmp_path, "t.json", T)) == (T, None)
    a, why = safety.load_touch_approval(_write(tmp_path, "t2.json", {**T, "creep_mm_s": 50}))
    assert a is None and why.startswith("승인 파일 거부")


# ---------------------------------------------------------------- m2.touch (가짜 드라이브)

def _axis_and_drive(monkeypatch, wall):
    ax = m2.Axis(P0, 35_000, time.time() + 600)
    monkeypatch.setattr(FakeDrive, "wall", wall)
    d = FakeDrive(ax, P0, speed=5)
    d.start()
    return ax, d


def test_touch_latches_at_wall_without_alarm(monkeypatch, tmp_path):
    marker = _marker(tmp_path)
    ax, d = _axis_and_drive(monkeypatch, (-10**9, P0 + WALL))
    try:
        m2.servo_on(ax, marker)
        ax.touch_ferr = 500
        out = m2.touch(ax, P0 + 30_000, marker, 5_000, 100_000, 1_000_000)
    finally:
        d.go = False
        d.join(2)
    assert out["touched_at"] == P0 + WALL and out["stopped_at"] == P0 + WALL
    assert ax.alarms == [] and ax.qs == m2.TOUCHED
    assert d.far == WALL and not d.bad


def test_touch_without_wall_reports_no_contact(monkeypatch, tmp_path):
    marker = _marker(tmp_path)
    ax, d = _axis_and_drive(monkeypatch, None)
    try:
        m2.servo_on(ax, marker)
        ax.touch_ferr = 500
        with pytest.raises(m2.Abort, match="닿지 않았다"):
            m2.touch(ax, P0 + 3_000, marker, 5_000, 100_000, 1_000_000)
    finally:
        d.go = False
        d.join(2)


def test_touch_latch_off_by_default():
    ax = m2.Axis(P0, 35_000, time.time() + 600)
    from motionbench.cia402 import Inputs
    ax.step(None, Inputs(0, 0x0237, 1, P0, 0, 0, 900, 0), 1)
    assert ax.touched is None and ax.qs is None


# ---------------------------------------------------------------- run_motion touch 모드

@pytest.fixture
def tr(rm, monkeypatch, tmp_path):
    p = tmp_path / "touch-approval.json"
    monkeypatch.setattr(rm, "APPROVAL_TOUCH", p)
    return rm, p


def test_touch_mode_three_touches_with_torque_limit_restored(tr, monkeypatch, tmp_path):
    rm, p = tr
    p.write_text(json.dumps(T, ensure_ascii=False), encoding="utf-8")
    _marker(tmp_path)
    monkeypatch.setattr(FakeDrive, "wall", (-10**9, P0 + WALL))
    box = wire(rm, monkeypatch, speed=5)
    reason, info, m, _, _ = run_move(rm, "touch")
    assert reason == rm.DONE_TOUCH, reason
    t = info["touches"]
    assert len(t) == 3 and all(x["touched_at"] == P0 + WALL for x in t)
    assert info["touch_summary"] == {"n": 3, "min": P0 + WALL, "max": P0 + WALL, "spread_um": 0,
                                     "from_start_mm": WALL / 1000}
    puts = [(i, s, v) for i, s, v in box["slave"].puts]
    torque = [v for i, s, v in puts if (i, s) == (0x6072, 0)]
    assert torque == [300, 1234]          # 낮추고 → 처음 읽은 값(가짜 1234)으로 되돌림
    assert (0x6081, 0, 5_000) in puts
    assert [g["target"] for g in info["legs"]] == [P0 + WALL - 10_000] * 3   # 터치마다 10 mm 물러난다 — 끝에 붙인 채 끝내지 않는다
    assert Path(info["csv"]).name.startswith("motion_touch_")
    assert info["axis"]["words"].startswith("왼쪽 끝")


def test_touch_mode_refused_without_approval(tr, monkeypatch, tmp_path):
    rm, p = tr
    _marker(tmp_path)
    wire(rm, monkeypatch)
    reason, info, m, _, _ = run_move(rm, "touch")
    assert reason.startswith("승인 파일 거부") and m.opens == []


def test_touch_mode_refused_when_axis_unconfirmed(tr, monkeypatch, tmp_path):
    rm, p = tr
    p.write_text(json.dumps(T, ensure_ascii=False), encoding="utf-8")
    doc = json.loads(rm.AXIS.read_text(encoding="utf-8"))
    rm.AXIS.write_text(json.dumps({**doc, "home_side": None}, ensure_ascii=False), encoding="utf-8")
    _marker(tmp_path)
    wire(rm, monkeypatch)
    reason, info, m, _, _ = run_move(rm, "touch")
    assert "Home 쪽이 확인되지 않았다" in reason and m.opens == []


def test_touch_mode_no_wall_ends_without_contact(tr, monkeypatch, tmp_path):
    rm, p = tr
    p.write_text(json.dumps({**T, "max_mm": 3, "back_mm": 1}, ensure_ascii=False), encoding="utf-8")
    _marker(tmp_path)
    monkeypatch.setattr(FakeDrive, "wall", None)
    box = wire(rm, monkeypatch, speed=50)
    reason, info, m, _, _ = run_move(rm, "touch")
    assert "닿지 않았다" in reason
    torque = [v for i, s, v in box["slave"].puts if (i, s) == (0x6072, 0)]
    assert torque[-1] == 1234             # 실패해도 힘 상한은 되돌린다


def test_touch_main_writes_its_own_result_file(tr, monkeypatch, tmp_path):
    rm, p = tr
    p.write_text(json.dumps(T, ensure_ascii=False), encoding="utf-8")
    _marker(tmp_path)
    monkeypatch.setattr(FakeDrive, "wall", (-10**9, P0 + WALL))
    wire(rm, monkeypatch, speed=5)
    monkeypatch.setattr(rm.proc, "safe_stdio", lambda: None)
    monkeypatch.setattr(sys, "argv", ["run_motion.py", "touch"])
    assert rm.main() == 0
    found = list(rm.DATA.glob("motion_touch_*.json"))
    assert len(found) == 1
    doc = json.loads(found[0].read_text(encoding="utf-8"))
    assert doc["mode"] == "touch" and doc["exit"] == rm.DONE_TOUCH and doc["touch_summary"]["n"] == 3


def test_touch_mode_starts_away_from_power_on_origin(tr, monkeypatch, tmp_path):
    """끝 터치는 끝을 찾는 일이라 원점(전원 투입 자리) 2 mm 검사를 하지 않는다 — 창(시작점 ± max_mm + 5)은 그대로."""
    import test_motion_m2_modes as mm
    rm, p = tr
    far = 50_000                          # 원점에서 50 mm — 왕복 모드라면 거부되는 자리
    monkeypatch.setattr(mm, "P0", far)
    p.write_text(json.dumps(T, ensure_ascii=False), encoding="utf-8")
    _marker(tmp_path)
    monkeypatch.setattr(FakeDrive, "wall", (-10**9, far + WALL))
    wire(rm, monkeypatch, speed=5)
    reason, info, m, _, _ = run_move(rm, "touch")
    assert reason == rm.DONE_TOUCH, reason
    assert info["touch_summary"]["min"] == far + WALL
