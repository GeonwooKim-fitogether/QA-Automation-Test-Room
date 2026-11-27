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


def test_op_state_failure_is_retried():
    """EtherCAT 상태 올리기 실패는 다시 띄운다 — 2026-11-26 19:42 야외 10번째가 이 문구로 재시도 없이 멈췄다(총괄 과제 1)."""
    assert R.comm_error("OP 에 못 감 — 상태 0 · AL 0x0000 No error")          # run_motion OP 요청(11-26 실측 문구)
    assert R.comm_error("SAFE_OP 에 못 감 — 상태 2, AL 0x001D Invalid output configuration")   # ecat.bring_up
    assert R.comm_error("PRE_OP 에 못 감 — 상태 1, AL 0x0000 No error")
    assert not R.comm_error("Quick stop 시험 지점에 못 감")                   # 서보 온 뒤 m2 의 문구 — 'OP 에 못 감' 이 아니다


def test_op_state_failures_happen_before_servo_on():
    """'OP 에 못 감' 을 다시 띄워도 되는 근거 — 그 문구를 내는 곳이 모두 서보 온보다 앞이다. 순서가 바뀌면 이 시험이 먼저 깨진다."""
    src = (Path(R.__file__).resolve().parents[1] / "run_motion.py").read_text(encoding="utf-8")
    op = src.index('raise m2.Abort(f"OP 에 못 감')
    assert src.index("ecat.bring_up(m, info)") < op < src.index("m2.servo_on(ax, MARKER)")
    assert src.count("에 못 감") == 1                                          # run_motion 에서 이 문구는 OP 요청 한 곳뿐
    ecat_src = (Path(R.__file__).resolve().parents[1] / "motionbench" / "ecat.py").read_text(encoding="utf-8")
    bring_up = ecat_src.split("def bring_up")[1].split("\ndef ")[0]
    calls = "_reach(m, s, pysoem."                                              # PRE_OP · SAFE_OP 에 못 감 — bring_up 안 두 곳뿐
    assert ecat_src.count(calls) == bring_up.count(calls) == 2 and "servo" not in bring_up.lower()


def test_run_plain_retries_op_state_failure(env, monkeypatch):
    monkeypatch.setattr(R.charge, "rail_low", lambda: (55, 23, None))
    monkeypatch.setattr(R, "wait_marker", lambda need_s=30: None)
    seq = iter([(1, "OP 에 못 감 — 상태 0 · AL 0x0000 No error"), (0, "오래 왕복 다 됨")])
    monkeypatch.setattr(R, "motor_once", lambda log: (*next(seq), "m.csv", 1.0))
    runs = []
    assert R.run_plain(1, env, runs) == 0
    assert [r["rc"] for r in runs] == [1, 0] and DRIVE_CHECKS == ["wait_drive"]   # 다시 띄우기 전에 드라이브 점검을 거친다


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


def test_first_move_skips_rows_lost_to_a_comm_gap(tmp_path):
    """2026-11-16 20:28 — 드라이브 통신이 잠깐 끊긴 주기는 위치 칸 없이 기록된다. 그 줄을 건너뛰고 출발을 읽는다."""
    p = tmp_path / "c.csv"
    lines = ["t_wall,wkc,pos"] + [f"{100 + k * 0.1:.1f},3,{q}" for k, q in enumerate([0, 0, -900, -1500000, -1500000, -700000, 0, 0])]
    lines += ["101.0,-1", "101.1,-1"] + [f"{101.2 + k * 0.1:.1f},3,{q}" for k, q in enumerate([-800000, -1500000, -600000, 0, 0, 0])]
    p.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    assert R.first_move(str(p)) == (pytest.approx(100.2), False)


def test_scene_sync_counts_a_second_move_to_start_but_does_not_learn_its_lead(env, monkeypatch):
    """한 속도에서 자리 옮김이 두 번 — 두 번째는 횟수에 넣되 그 지연은 배우지 않는다(2026-11-16 19:23 선 정리 뒤)."""
    import motionbench.labsat as L

    class FakeLabSat:
        def stop(self): pass
        def play(self, scene): return R.time.time()

    monkeypatch.setattr(L, "LabSat", FakeLabSat)
    monkeypatch.setattr(R.charge, "should_stop", lambda low: False)
    monkeypatch.setattr(R.charge, "rail_low", lambda: (90, 12, None))
    monkeypatch.setattr(R, "wait_marker", lambda s: None)
    monkeypatch.setattr(R, "motor_once", lambda log: (0, "오래 왕복 다 됨", "csv", 1000.0))
    moves = iter([(1002.9, True), (1002.6, False), (1003.3, True), (1002.6, False)])
    monkeypatch.setattr(R, "first_move", lambda csvp: next(moves))
    out = env / "out"; out.mkdir(); runs = []
    assert R.run_scene_sync(3, out, runs, 5.3) == 0
    assert len(runs) == 4                                                   # 첫 자리 옮김만 세지 않았다
    assert [round(r["lead_used"], 2) for r in runs] == [5.3, 5.3, 2.6, 2.6]  # 두 번째 자리 옮김의 3.3초는 배우지 않았다


def test_scene_sync_stops_with_stuck_when_a_cell_stays_unlocked(env, monkeypatch):
    """2026-11-19 — 장면을 다시 틀면(되감기) 셀이 위치를 잃고 남는다. 출발 직전과 운전 끝에 모두 못 잡은 셀이 있으면
    그 운전을 기록하고 STUCK 으로 돌아온다(실행기가 재부팅하고 이어 간다). 한쪽에서만 못 잡은 셀은 잠깐 잃은 것이라 넘어간다."""
    import motionbench.labsat as L

    class FakeLabSat:
        def stop(self): pass
        def play(self, scene): return R.time.time()

    monkeypatch.setattr(L, "LabSat", FakeLabSat)
    monkeypatch.setattr(R.charge, "should_stop", lambda low: False)
    monkeypatch.setattr(R.charge, "rail_low", lambda: (90, 23, None))
    monkeypatch.setattr(R, "wait_marker", lambda s: None)
    monkeypatch.setattr(R, "ensure_link", lambda: True)
    monkeypatch.setattr(R, "motor_once", lambda log: (0, "오래 왕복 다 됨", "csv", 1000.0))
    monkeypatch.setattr(R, "first_move", lambda csvp: (1002.7, False))
    seen = iter([{10691, 10680}, {10691}, set(), set()])      # 1번째 운전: 출발 전 · 끝
    monkeypatch.setattr(R, "unlocked_now", lambda: next(seen))
    out = env / "out"; out.mkdir(); runs = []
    assert R.run_scene_sync(3, out, runs, 5.3, stuck_exit=True) == R.STUCK
    assert len(runs) == 1 and runs[0]["stuck"] == [10691]                       # 10680 은 끝에 다시 잡아 넘어갔다


def test_ensure_link_does_not_block_without_live_record(env, monkeypatch):
    """실시간 기록이 꺼져 있으면(qc_now.json 에 셀 칸이 없음) 들리는지 알 수 없으니 와이파이를 건드리지 않고 넘어간다."""
    monkeypatch.setattr(R, "live_cells", lambda: None)
    called = []
    monkeypatch.setattr(R.wifi, "ensure", lambda say: called.append(1) or {})
    assert R.ensure_link() is True and not called


def test_ensure_link_reconnects_when_no_cell_is_heard(env, monkeypatch):
    """2026-11-19 12:40 — 셀이 하나도 안 들리면 와이파이를 다시 붙이고, 셀이 돌아오면 이어 간다."""
    states = iter([{}, {}, {10680: {"fix": "D", "age_s": 1}}, {10680: {"fix": "D", "age_s": 1}}])
    monkeypatch.setattr(R, "live_cells", lambda: next(states))
    called = []
    monkeypatch.setattr(R.wifi, "ensure", lambda say: called.append(1) or {"why": None})
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    assert R.ensure_link() is True and called
