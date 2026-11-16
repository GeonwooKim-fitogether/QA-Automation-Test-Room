"""레일 반복 실행기(tools/run_rail.py) — 모터 · LabSat 없이 순서와 멈춤 조건만 본다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import run_rail as R  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    pre = tmp_path / "presets"; pre.mkdir()
    (pre / "IMU-FW_a50.json").write_text(json.dumps({"a_from_left_mm": 150, "b_from_left_mm": 750}), encoding="utf-8")
    monkeypatch.setattr(R, "PRESETS", pre)
    monkeypatch.setattr(R, "APPROVAL", tmp_path / "long-approval.json")
    monkeypatch.setattr(R, "MARKER", tmp_path / "motion_attended")
    monkeypatch.setattr(R, "DATA", tmp_path / "data")                  # 진짜 data/ 에 drive_needed.json 등을 쓰지 않게
    monkeypatch.setattr(R, "NEED_FILE", tmp_path / "attend_needed.json")
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    # 드라이브 점검(run_motion.py scan)은 진짜 어댑터를 여는 자식 프로세스다 — 늘 가짜로. 2026-11-12 16:43 사고는
    # preflight 시험이 이것을 바꾸지 않아 진짜 scan 이 운전 중인 드라이브에 붙은 것이었다(tests/conftest.py)
    DRIVE_CHECKS.clear()
    monkeypatch.setattr(R, "wait_drive", lambda: DRIVE_CHECKS.append("wait_drive"))
    return tmp_path


DRIVE_CHECKS: list = []


def test_install_preset_copies_only_approved_file(env):
    a = R.install_preset("IMU-FW")              # 기본 프리셋 IMU-FW_a50
    assert a["b_from_left_mm"] == 750 and json.loads((env / "long-approval.json").read_text(encoding="utf-8")) == a
    assert R.install_preset("IMU-MOVE", "IMU-FW_a50") == a      # 이름을 주면 그 파일
    with pytest.raises(SystemExit):
        R.install_preset("GNSS-MOVE")              # 프리셋이 없으면 값을 지어내지 않고 멈춘다
    with pytest.raises(SystemExit):
        R.install_preset("cap")                     # 상한 시험은 기본 프리셋이 없다 — 이름을 꼭 준다


def test_main_takes_only_rail_test_ids(env):
    for bad in ("C", "B", "GNSS-STATIC", "IMU-STATIC"):
        with pytest.raises(SystemExit):
            R.main([bad, "--out", str(env / "x")])


def test_preflight_waits_for_fresh_marker_then_checks_battery(env, monkeypatch):
    monkeypatch.setattr(R.charge, "rail_low", lambda: (55, 23, None))
    monkeypatch.setattr(R, "NEED_FILE", env / "attend_needed.json")
    calls = {"n": 0}
    def fake_sleep(s):                       # 기다리는 동안 운전자가 현장 확인을 한다
        calls["n"] += 1
        if calls["n"] == 3:
            (env / "motion_attended").write_text(json.dumps({"created": R.time.time(), "until": R.time.time() + 3600}), encoding="utf-8")
    monkeypatch.setattr(R.time, "sleep", fake_sleep)
    R.preflight("IMU-FW", 10, 95)
    assert calls["n"] == 3 and not (env / "attend_needed.json").exists()
    assert DRIVE_CHECKS == ["wait_drive"]                               # 드라이브 점검을 먼저 부른다 — 가짜로
    monkeypatch.setattr(R.charge, "rail_low", lambda: (25, 23, None))
    with pytest.raises(SystemExit, match="30%"):
        R.preflight("IMU-FW", 10, 95)


def test_marker_fresh_left_uses_10_hours_from_created(env):
    now = R.time.time()
    (env / "motion_attended").write_text(json.dumps({"created": now - 35400, "until": now + 9000}), encoding="utf-8")
    assert 590 < R.marker_fresh_left() <= 600


def test_run_plain_records_each_run_and_stops_on_motor_error(env, monkeypatch):
    monkeypatch.setattr(R.charge, "rail_low", lambda: (55, 23, None))
    monkeypatch.setattr(R, "wait_marker", lambda need_s=30: None)
    codes = iter([0, 0, 3])
    monkeypatch.setattr(R, "motor_once", lambda log: (next(codes), "오래 왕복 다 됨", f"m{log.stem}.csv", 1.0))
    runs = []
    assert R.run_plain(5, env, runs) == 1
    assert [r["rc"] for r in runs] == [0, 0, 3]
    assert json.loads((env / "runs.json").read_text(encoding="utf-8"))[-1]["i"] == 3


def test_run_plain_stops_when_battery_low(env, monkeypatch):
    monkeypatch.setattr(R.charge, "rail_low", lambda: (15, 23, None))
    monkeypatch.setattr(R, "wait_marker", lambda need_s=30: None)
    monkeypatch.setattr(R, "motor_once", lambda log: pytest.fail("배터리가 낮은데 모터를 띄웠다"))
    assert R.run_plain(3, env, []) == 2


def test_comm_error_is_retried_but_safety_stop_is_not():
    assert R.comm_error("SDO 0x1C12:00 읽기 실패 — PacketError: (1, 1)")
    assert R.comm_error("슬레이브 수 -1 — 1대여야 한다")
    assert not R.comm_error("입회 표지 무효 — 서보 온 거부: created 가 10시간 안이 아니다 (606분 전)")
    assert not R.comm_error("추종 오차 한도 초과")
    assert R.comm_error("Quick stop — 주기 지연 60.8 ms > 50 ms")
    assert not R.comm_error("Quick stop — 끝에 닿음 (끝 터치)")
    assert not R.comm_error(None)


def test_run_plain_retries_comm_error_then_counts_only_successes(env, monkeypatch):
    monkeypatch.setattr(R.charge, "rail_low", lambda: (55, 23, None))
    monkeypatch.setattr(R, "wait_marker", lambda need_s=30: None)
    monkeypatch.setattr(R, "wait_drive", lambda: None)
    seq = iter([(1, "SDO 0x1C12:00 읽기 실패 — PacketError: (1, 1)"), (0, "오래 왕복 다 됨"), (0, "오래 왕복 다 됨")])
    monkeypatch.setattr(R, "motor_once", lambda log: (*next(seq), "m.csv", 1.0))
    runs = []
    assert R.run_plain(2, env, runs) == 0
    assert [r["rc"] for r in runs] == [1, 0, 0]


def test_run_plain_gives_up_after_three_comm_errors(env, monkeypatch):
    monkeypatch.setattr(R.charge, "rail_low", lambda: (55, 23, None))
    monkeypatch.setattr(R, "wait_marker", lambda need_s=30: None)
    monkeypatch.setattr(R, "wait_drive", lambda: None)
    monkeypatch.setattr(R, "motor_once", lambda log: (1, "슬레이브 수 -1 — 1대여야 한다", None, 1.0))
    runs = []
    assert R.run_plain(2, env, runs) == 1 and len(runs) == 4


def _motor_csv(path, pos):
    path.write_text("t_wall,pos" + chr(10) + "".join(f"{100 + k * 0.1:.1f},{q}" + chr(10) for k, q in enumerate(pos)), encoding="utf-8")
    return str(path)


def test_first_move_is_when_the_stroke_leaves_its_start_point(tmp_path):
    """시작점에서 출발한 운전 — 처음 위치를 벗어난 때가 출발이고, 자리 옮김은 없다."""
    csvp = _motor_csv(tmp_path / "a.csv", [0, 0, 0, -900, -1500000, -1500000, -700000, 0, 0, -800000, -1500000, -1500000, -600000, 0, 0, 0])
    assert R.first_move(csvp) == (pytest.approx(100.3), False)


def test_first_move_skips_the_move_to_the_start_point(tmp_path):
    """2026-11-16 2.0 m/s 첫 운전 — 150 mm 에 서 있다가 250 mm 시작점으로 자리를 옮긴 뒤 왕복한다. 자리 옮김은 출발이 아니다."""
    csvp = _motor_csv(tmp_path / "b.csv", [4188, 4188, -32000, -95800, -95800, -600000, -1295800, -1295800, -700000, -95800,
                                           -95800, -700000, -1295800, -1295800, -600000, -95800, -95800, -95800, -95800, -95800])
    assert R.first_move(csvp) == (pytest.approx(100.5), True)
    assert R.first_move(None) == (None, False)


def test_scene_sync_does_not_count_or_learn_from_a_run_that_moved_to_start(env, monkeypatch):
    """자리 옮김이 낀 운전은 횟수에 넣지 않고 지연도 배우지 않는다 — 한 번 더 돌고, 그 뒤 운전의 지연으로 맞춘다."""
    import motionbench.labsat as L

    class FakeLabSat:
        def stop(self): pass
        def play(self, scene): return R.time.time()

    monkeypatch.setattr(L, "LabSat", FakeLabSat)
    monkeypatch.setattr(R.charge, "should_stop", lambda low: False)
    monkeypatch.setattr(R.charge, "rail_low", lambda: (90, 23, None))
    monkeypatch.setattr(R, "wait_marker", lambda s: None)
    monkeypatch.setattr(R, "motor_once", lambda log: (0, "오래 왕복 다 됨", "csv", 1000.0))
    moves = iter([(1002.4, True), (1002.7, False), (1002.7, False)])
    monkeypatch.setattr(R, "first_move", lambda csvp: next(moves))
    out = env / "out"; out.mkdir(); runs = []
    assert R.run_scene_sync(2, out, runs, 5.3) == 0
    assert [r["moved_to_start"] for r in runs] == [True, False, False]          # 2번 세려고 3번 돌았다
    assert [round(r["lead_used"], 2) for r in runs] == [5.3, 5.3, 2.7]          # 자리 옮김 운전의 2.4초는 배우지 않았다
