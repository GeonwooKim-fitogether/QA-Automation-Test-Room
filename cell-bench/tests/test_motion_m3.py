"""모터 M3 — 속도 세 계단(단계마다 사람 승인 · 앞 단계 통과 뒤에만) — 장비 없이 돈다. 진짜 pysoem 은 가져오지 않는다.

계단(dynamic-bench/README.md 와 같다): 1단계 60 mm · 20 mm/s · 100 mm/s², 2단계 150 mm · 50 mm/s · 250 mm/s²,
3단계 300 mm · 100 mm/s · 500 mm/s², 모두 왕복 3회. 승인 파일은 dynamic-bench/dev/m3-approval.json(stage 칸 하나 더).
가짜 드라이브(test_motion_m2_modes 의 wire)는 0x6081 을 보지 않는다 — 속도 · 감속 곡선은 이 시험이 증명하지 않는다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from motionbench import safety  # noqa: E402
from test_motion_m2 import _marker, _write  # noqa: E402
from test_motion_m2_modes import P0, FakeMaster, rm, run_move, wire  # noqa: E402,F401  (rm 은 fixture)

S1 = {"approved_by": "김건우", "approved_at": "2026-10-27T16:40:00+09:00", "first_move_dir": 1, "stage": 1,
      "distance_mm": 60, "window_mm": 75, "vmax_mm_s": 20, "amax_mm_s2": 100, "cycles": 3}
S2 = {**S1, "stage": 2, "distance_mm": 150, "window_mm": 165, "vmax_mm_s": 50, "amax_mm_s2": 250}
S3 = {**S1, "stage": 3, "distance_mm": 300, "window_mm": 315, "vmax_mm_s": 100, "amax_mm_s2": 500}
DONE = "왕복 다 됨"


def _result(stage, exit=DONE, alarms=(), mode="m3"):
    return {"mode": mode, "approval": {**S1, "stage": stage}, "exit": exit, "alarms": list(alarms)}


# ---------------------------------------------------------------- 순수 판정

def test_stage_caps_are_the_agreed_staircase():
    assert safety.M3_STAGES == {
        1: {"distance_mm": 60, "window_mm": 75, "vmax_mm_s": 20, "amax_mm_s2": 100, "cycles": 3},
        2: {"distance_mm": 150, "window_mm": 165, "vmax_mm_s": 50, "amax_mm_s2": 250, "cycles": 3},
        3: {"distance_mm": 300, "window_mm": 315, "vmax_mm_s": 100, "amax_mm_s2": 500, "cycles": 3},
    }


def test_stage_1_approval_accepted_without_any_pass():
    assert safety.m3_approval_problem(S1, set()) is None


@pytest.mark.parametrize("key,value", [("vmax_mm_s", 21), ("amax_mm_s2", 101), ("distance_mm", 61), ("window_mm", 76), ("cycles", 4)])
def test_stage_1_values_above_its_caps_refused(key, value):
    assert "절대 상한" in safety.m3_approval_problem({**S1, key: value}, set())


def test_stage_1_cannot_borrow_stage_3_caps():
    assert "절대 상한" in safety.m3_approval_problem({**S3, "stage": 1}, set())


@pytest.mark.parametrize("stage", [None, 0, 4, "1", 1.0, True])
def test_unknown_stage_refused(stage):
    a = {k: v for k, v in S1.items() if k != "stage"} if stage is None else {**S1, "stage": stage}
    assert "stage" in safety.m3_approval_problem(a, {1, 2, 3})


def test_next_stage_only_after_previous_passed():
    assert "1단계 통과" in safety.m3_approval_problem(S2, set())
    assert safety.m3_approval_problem(S2, {1}) is None
    assert "2단계 통과" in safety.m3_approval_problem(S3, {1})
    assert safety.m3_approval_problem(S3, {1, 2}) is None


def test_missing_common_field_refused():
    a = {k: v for k, v in S1.items() if k != "approved_by"}
    assert "필드 빠짐" in safety.m3_approval_problem(a, set())


def test_passed_counts_only_clean_m3_round_trips():
    results = [_result(1), _result(2, exit="Ctrl+C"), _result(2, alarms=[["t", "WKC"]]), _result(3, mode="move"),
               {"mode": "m3", "approval": "승인 파일 거부 — …", "exit": DONE, "alarms": []}, {"mode": "m3"}]
    assert safety.m3_passed(results, DONE) == {1}


def test_load_m3_approval_reads_file(tmp_path):
    p = _write(tmp_path, "m3.json", S1)
    assert safety.load_m3_approval(p, set()) == (S1, None)
    a, why = safety.load_m3_approval(_write(tmp_path, "m3b.json", S2), set())
    assert a is None and why.startswith("승인 파일 거부") and "1단계 통과" in why


# ---------------------------------------------------------------- run_motion m3 모드 (가짜 드라이브)

@pytest.fixture
def m3(rm, monkeypatch, tmp_path):
    """rm 위에 M3 승인 파일 자리를 tmp_path 로 옮긴다 → (run_motion, 승인 파일 경로)."""
    p = tmp_path / "m3-approval.json"
    monkeypatch.setattr(rm, "APPROVAL_M3", p)
    return rm, p


def test_m3_stage_1_full_run_three_round_trips_no_forced_quick_stop(m3, monkeypatch, tmp_path):
    rm, p = m3
    p.write_text(json.dumps(S1), encoding="utf-8")
    _marker(tmp_path)
    box = wire(rm, monkeypatch, speed=400)
    reason, info, m, _, _ = run_move(rm, "m3")
    assert reason == rm.DONE_M2, reason
    legs = info["legs"]
    assert len(legs) == 6 and all("qs_at" not in g for g in legs)
    assert [g["target"] for g in legs] == [P0 + 60_000, P0] * 3
    assert all(g["err_um"] <= 50 for g in legs)
    puts = dict(((i, s), v) for i, s, v in box["slave"].puts)
    assert puts[(0x6081, 0)] == 20_000 and puts[(0x6083, 0)] == 100_000 and puts[(0x6084, 0)] == 100_000
    assert puts[(0x607D, 1)] == P0 - 75_000 and puts[(0x607D, 2)] == P0 + 75_000
    assert Path(info["csv"]).name.startswith("motion_m3_s1_")
    assert info["approval"]["stage"] == 1


def test_m3_stage_2_refused_before_adapter_without_stage_1_pass(m3, monkeypatch, tmp_path):
    rm, p = m3
    p.write_text(json.dumps(S2), encoding="utf-8")
    _marker(tmp_path)
    wire(rm, monkeypatch)
    reason, info, m, _, _ = run_move(rm, "m3")
    assert "1단계 통과" in reason and m.opens == []


def test_m3_stage_2_allowed_after_stage_1_result_file(m3, monkeypatch, tmp_path):
    rm, p = m3
    (rm.DATA / "motion_m3_20261027-164000.json").write_text(json.dumps(_result(1), ensure_ascii=False), encoding="utf-8")
    p.write_text(json.dumps(S2), encoding="utf-8")
    _marker(tmp_path)
    wire(rm, monkeypatch, speed=1000)
    reason, info, m, _, _ = run_move(rm, "m3")
    assert reason == rm.DONE_M2, reason
    assert [g["target"] for g in info["legs"]][:2] == [P0 + 150_000, P0]


def test_m3_missing_approval_refused_before_adapter(m3, monkeypatch, tmp_path):
    rm, p = m3
    _marker(tmp_path)
    wire(rm, monkeypatch)
    reason, info, m, _, _ = run_move(rm, "m3")
    assert reason.startswith("승인 파일 거부") and m.opens == []


def test_m3_main_writes_its_own_result_file(m3, monkeypatch, tmp_path):
    rm, p = m3
    p.write_text(json.dumps(S1), encoding="utf-8")
    _marker(tmp_path)
    wire(rm, monkeypatch, speed=400)
    monkeypatch.setattr(rm.proc, "safe_stdio", lambda: None)
    monkeypatch.setattr(rm.ecat.pysoem, "Master", FakeMaster)
    monkeypatch.setattr(sys, "argv", ["run_motion.py", "m3"])
    assert rm.main() == 0
    found = list(rm.DATA.glob("motion_m3_*.json"))
    assert len(found) == 1 and found[0].name.startswith("motion_m3_2")
    doc = json.loads(found[0].read_text(encoding="utf-8"))
    assert doc["mode"] == "m3" and doc["exit"] == rm.DONE_M2 and doc["approval"]["stage"] == 1
    assert not (rm.DATA / "motion_m2.json").exists()
