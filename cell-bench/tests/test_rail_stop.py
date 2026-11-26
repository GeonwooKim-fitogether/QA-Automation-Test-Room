"""레일 사람 정지(#17) — 정지 표지 · 생명줄 · 정지 확인 · 감속 시간 · 0x6085 기준. 장비 없이 돈다(가짜 드라이브).

설계: 팀 제출서 submissions/2026-11-26_17_rail-safe-stop.md 12-1 의 U1 ~ U17 (QA 판정서 17 통과). U12 는 기존 시험 전부다.
정지 표지 자리는 시험 중 RAIL_LOCK_DIR(conftest 의 임시 폴더) — 시험마다 앞뒤로 비운다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from motionbench import m2, rail_stop  # noqa: E402
import test_motion_m2_modes as mm  # noqa: E402
from test_motion_m2_modes import rm, run_move, wire  # noqa: E402,F401  (rm 은 fixture)
from test_motion_rail import _put, doc, rr  # noqa: E402,F401  (rr 은 fixture)
from test_motion_long import LONG  # noqa: E402


@pytest.fixture(autouse=True)
def clean_stop_dir():
    d = rail_stop.stop_dir()
    assert os.environ.get("RAIL_NO_HARDWARE") and str(d) == os.environ["RAIL_LOCK_DIR"]   # 시험은 진짜 ProgramData 자리를 건드리지 않는다

    def wipe():
        for n in (rail_stop.STOP, rail_stop.ACK, rail_stop.WATCH):
            (d / n).unlink(missing_ok=True)
    wipe()
    yield d
    wipe()


@pytest.fixture
def rl(rr, monkeypatch, tmp_path):
    """같은 전원 · long 승인 자리는 tmp (test_motion_long.rl 과 같다)."""
    d = doc()
    _put(rr.RAIL, d)
    monkeypatch.setattr(mm, "LIMITS", tuple(d["limits"]))
    monkeypatch.setattr(rr, "APPROVAL_LONG", tmp_path / "long-approval.json")
    return rr


def stop_when_moved(mm_from_start: float, by="시험", why="사람 요청"):
    """on_tick — 서보가 켜지고 시작 자리에서 mm_from_start 넘게 움직였을 때 한 번 정지 표지를 만든다."""
    box = {"p0": None, "done": False}

    def on_tick(c, v):
        if box["done"] or (v.sw & 0x6F) != 0x27:          # Operation enabled 일 때만 센다
            return
        if box["p0"] is None:
            box["p0"] = v.pos
        if abs(v.pos - box["p0"]) >= mm_from_start * 1000:
            box["done"] = True
            box["req"] = rail_stop.request(by, why)
            box["t_ns"] = time.perf_counter_ns()
    return on_tick, box


# ---------------------------------------------------------------- U1 · U8 — 이동 중 정지 표지 → Quick stop → 끝 순서 → 정지 확인

def test_u1_stop_flag_mid_move_quick_stops_and_writes_confirmed_ack(rl, monkeypatch):
    rm = rl
    _put(rm.APPROVAL_LONG, {**LONG, "minutes": 0.2})
    on_tick, box = stop_when_moved(5)
    wire(rm, monkeypatch, speed=50, on_tick=on_tick)
    reason, info, *_ = run_move(rm, "long")
    assert box["done"] and rail_stop.HUMAN in reason and "시험 · 사람 요청" in reason, reason
    assert info["alarms"] == [], info["alarms"]                       # 사람 정지는 자동 규칙(alarms)에 넣지 않는다
    ack = info["stop_ack"]
    assert isinstance(ack, dict) and ack["id"] == box["req"]["id"], ack
    assert ack["verdict"].startswith(rail_stop.CONFIRMED), ack["verdict"]
    assert ack["qs_sent_ms"] is not None and ack["qs_sent_ms"] <= 50, ack       # 표지를 본 뒤 0x02 가 50 ms 안에
    assert ack["state"] == "SWITCH_ON_DISABLED" and ack["safe_op"] is True and ack["stale"] is False
    assert ack["still_ms"] >= 180 and ack["still_span_um"] <= rail_stop.STILL_UM
    assert rail_stop.read_ack() == ack                                # 파일로도 남았다
    assert rail_stop.active() is not None                             # 표지는 걸쇠 — 남는다


def test_u8_wait_confirm_reads_ack_only_after_it_exists(clean_stop_dir):
    req = rail_stop.request("시험", "사람 요청")
    good = {"id": req["id"], "state": "SWITCH_ON_DISABLED", "stale": False, "still_ms": 200.0, "still_span_um": 3, "safe_op": True,
            "err": "0x0000", "stopped_at": "2026-11-26 16:00:00"}
    rail_stop.write_ack(good)
    code, line = rail_stop.wait_confirm(req, lambda: (True, "pid 1"), wait_s=0.5, sleep=lambda s: None)
    assert code == 0 and line.startswith(rail_stop.CONFIRMED), line


@pytest.mark.parametrize("change, word", [
    ({"state": "OPERATION_ENABLED"}, "서보"), ({"stale": True}, "묵었다"), ({"still_span_um": 120}, "움직였다"),
    ({"still_ms": 40.0}, "모자란다"), ({"safe_op": False}, "SAFE-OP"),
])
def test_u8_judge_refuses_each_condition(change, word):
    good = {"state": "SWITCH_ON_DISABLED", "stale": False, "still_ms": 200.0, "still_span_um": 3, "safe_op": True, "err": "0x0000"}
    ok, line = rail_stop.judge_ack({**good, **change})
    assert not ok and word in line, line


def test_u8_fault_with_alarm_still_counts_as_stopped_but_names_alarm():
    ok, line = rail_stop.judge_ack({"state": "FAULT", "stale": False, "still_ms": 200.0, "still_span_um": 0, "safe_op": True, "err": "0xFF50"})
    assert ok and "0xFF50" in line


def test_u8_wait_confirm_other_outcomes(clean_stop_dir):
    req = rail_stop.request("시험", "사람 요청")
    code, line = rail_stop.wait_confirm(req, lambda: (False, "비어 있음"))
    assert code == 0 and "운전 중인 모터 프로그램 없음" in line                    # 다음 운전만 막았다
    rail_stop.write_ack({"id": "other", "state": "SWITCH_ON_DISABLED"})           # 다른 요청의 확인은 세지 않는다
    code, line = rail_stop.wait_confirm(req, lambda: (True, "pid 9"), wait_s=0.2, sleep=lambda s: time.sleep(0.01))
    assert code == 3 and "답하지 않는다" in line and "전원 스위치" in line, line
    calls = iter([(True, "pid 9"), (False, "비어 있음")])                           # 확인 파일 없이 모터 프로그램이 끝났다
    code, line = rail_stop.wait_confirm(req, lambda: next(calls, (False, "")), wait_s=1.0, sleep=lambda s: None)
    assert code == 3 and "정지 확인 파일이 없다" in line, line


# ---------------------------------------------------------------- U2 · U13 — 감시 스레드는 명령 스레드와 따로 · 박동

class _V:
    def __init__(self, pos):
        self.pos = pos


def test_u2_watch_trips_without_any_command_thread_loop(rm, clean_stop_dir):
    ax = m2.Axis(0, 10_000, time.time() + 60)
    ax.last = _V(123)
    w = rm.StopWatch(ax, clean_stop_dir, [])
    w.start()
    try:
        time.sleep(0.05)
        t0 = time.perf_counter()
        rail_stop.request("시험", "명령 스레드가 막혀 있다고 치고")
        while not ax.qs and time.perf_counter() - t0 < 1.0:
            time.sleep(0.001)
        took = time.perf_counter() - t0
    finally:
        w.halt()
    assert ax.qs and rail_stop.HUMAN in ax.qs and took < 0.1, (ax.qs, took)     # 목표 25 ms · 시험 여유 100 ms
    assert w.req["by"] == "시험" and w.seen_pos == 123


def test_u13_stale_beat_quick_stops_from_cycle_and_command_side():
    ax = m2.Axis(0, 10_000, time.time() + 60)
    ax.watch_beat = time.monotonic() - 1.0
    assert ax.watch_dead() and rail_stop.WATCH_DEAD in ax.watch_dead()
    ax.idle = lambda: None
    ax.dead = lambda: None
    with pytest.raises(m2.Abort) as e:
        ax.pause(0.05)                                                  # 명령 스레드 쪽
    assert rail_stop.WATCH_DEAD in str(e.value) and ax.alarms and rail_stop.WATCH_DEAD in ax.alarms[-1][1]
    for _ in range(50):                                                 # 같은 이유를 매 주기 알람에 쌓지 않는다
        ax._alarm(ax.watch_dead())
    assert sum(rail_stop.WATCH_DEAD in w for _, w in ax.alarms) == 1, ax.alarms
    ax2 = m2.Axis(0, 10_000, time.time() + 60)
    assert ax2.watch_dead() is None                                     # 감시를 띄우지 않은 축은 보지 않는다(기존 시험 · 읽기 전용 모드)


def test_u13_watch_thread_exception_stops_beat_then_quick_stop(rm, clean_stop_dir, monkeypatch):
    ax = m2.Axis(0, 10_000, time.time() + 60)
    ax.last = _V(0)
    calls = {"n": 0}

    def broken(d=None):
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("감시 고장 흉내")
    monkeypatch.setattr(rm.rail_stop, "active", broken)
    w = rm.StopWatch(ax, clean_stop_dir, [])
    w.start()
    w.join(1.0)
    assert w.error and "감시 고장" in w.error
    time.sleep(rail_stop.BEAT_STALE_S + 0.03)
    assert ax.watch_dead() is not None                                  # 225 ms 안에 주기 · 명령 스레드가 Quick stop 을 건다
    ax.watch_beat = None


# ---------------------------------------------------------------- U3 · U4 — 생명줄

def test_u3_lifeline_parent_gone_writes_flag_and_trips(rm, clean_stop_dir):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        from cellbench.proc import created
        c = None
        for _ in range(50):
            c = created(child.pid)
            if c:
                break
            time.sleep(0.02)
        ax = m2.Axis(0, 10_000, time.time() + 60)
        ax.last = _V(0)
        w = rm.StopWatch(ax, clean_stop_dir, [(child.pid, c or 0.0)])
        w.start()
        time.sleep(0.3)
        assert not ax.qs                                               # 살아 있는 동안은 아무 일 없다
        t0 = time.perf_counter()
        child.kill()
        child.wait(5)
        while not ax.qs and time.perf_counter() - t0 < 2.0:
            time.sleep(0.005)
        took = time.perf_counter() - t0
        w.halt()
    finally:
        if child.poll() is None:
            child.kill()
    assert ax.qs and "생명줄" in ax.qs and took < 0.5, (ax.qs, took)
    st = rail_stop.active()
    assert st and "생명줄" in st["by"]                                  # D1 — 표지를 남겨 사람이 푼다


def test_u4_lifeline_pid_reused_counts_as_gone():
    assert rail_stop.lifeline_broken([(42, 1000.0)], created=lambda pid: 2000.0)       # 같은 pid · 다른 생성 시각
    assert rail_stop.lifeline_broken([(42, 1000.0)], created=lambda pid: None)
    assert rail_stop.lifeline_broken([(42, 1000.0)], created=lambda pid: 1000.4) is None
    assert rail_stop.lifeline_broken([(42, 1000.0)], created=lambda pid: 0.0) is None  # 권한으로 시각을 못 읽음 — 살아 있음


def test_lifeline_env_appends_real_interpreter_once():
    env = rail_stop.lifeline_env({"RAIL_LIFELINE": "7:1.000"}, created=lambda pid: 5.0)
    assert env["RAIL_LIFELINE"] == f"7:1.000;{os.getpid()}:5.000"
    again = rail_stop.lifeline_env(env, created=lambda pid: 5.0)
    assert again["RAIL_LIFELINE"] == env["RAIL_LIFELINE"]
    assert rail_stop.lifeline_parse(env["RAIL_LIFELINE"]) == [(7, 1.0), (os.getpid(), 5.0)]


# ---------------------------------------------------------------- U5 · U9 — 표지가 있으면 움직이지 않는다

def test_u5_flag_present_refused_before_adapter(rl, monkeypatch):
    rm = rl
    _put(rm.APPROVAL_LONG, LONG)
    rail_stop.request("시험", "어제 멈춘 것")
    wire(rm, monkeypatch, speed=3000)
    reason, info, m, *_ = run_move(rm, "long")
    assert "정지 표지가 있다" in reason and m.opens == [], reason


def test_u5_flag_appearing_before_servo_on_keeps_servo_off(rl, monkeypatch):
    rm = rl
    _put(rm.APPROVAL_LONG, LONG)
    made = {"done": False}

    def on_tick(c, v):                                                 # 주기가 돌기 시작하면(서보 온 전) 표지가 생긴다
        if not made["done"]:
            made["done"] = True
            rail_stop.request("시험", "서보 온 직전")
    wire(rm, monkeypatch, speed=3000, on_tick=on_tick)
    reason, info, *_ = run_move(rm, "long")
    assert made["done"] and "servo_on" not in info and ("정지 표지" in reason or rail_stop.HUMAN in reason), reason


def test_u9_broken_flag_is_a_stop(clean_stop_dir):
    (clean_stop_dir / rail_stop.STOP).write_text("{깨짐", encoding="utf-8")
    st = rail_stop.active()
    assert st and st["broken"] and "정지 표지" in rail_stop.refuse_line(st)
    (clean_stop_dir / rail_stop.STOP).write_text("", encoding="utf-8")
    assert rail_stop.active()["broken"]


# ---------------------------------------------------------------- U6 · U7 — run_rail · 실행기

def test_u6_run_rail_returns_stopped_without_starting(clean_stop_dir, tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    import run_rail as R
    called = []
    monkeypatch.setattr(R, "install_preset", lambda *a: called.append("preset") or {})
    rail_stop.request("시험", "사람 요청")
    assert R.main(["IMU-FW", "--n", "1", "--out", str(tmp_path / "o")]) == R.STOPPED == 4
    assert called == []


def test_u6_run_plain_stops_on_human_stop_reason(clean_stop_dir, tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    import run_rail as R
    monkeypatch.setattr(R, "ensure_link", lambda: True)
    monkeypatch.setattr(R.charge, "rail_low", lambda: (80, 11, None))
    monkeypatch.setattr(R, "wait_marker", lambda need_s=30: None)
    monkeypatch.setattr(R, "motor_once", lambda log: (1, "Quick stop — 사람 정지 — 운전자 화면 · 사람 요청", None, time.time()))
    with pytest.raises(R.Stopped):
        R.run_plain(3, tmp_path, [], "IMU-FW")
    assert R.human_stopped("Quick stop — 생명줄 — 실행기 꺼짐 (pid 3)") and not R.human_stopped("Quick stop — 주기 지연 58.7 ms > 50 ms")


def test_u6_sleep_check_sees_flag(clean_stop_dir, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    import run_rail as R
    n = {"k": 0}

    def sleep(s):
        n["k"] += 1
        if n["k"] == 3:
            rail_stop.request("시험", "쉬는 사이")
    monkeypatch.setattr(R.time, "sleep", sleep)
    with pytest.raises(R.Stopped):
        R.sleep_check(5)


# ---------------------------------------------------------------- U10 · U14 — 정지 명령 (cp949 · 쓰기 실패)

def test_u10_stop_command_under_cp949_console(clean_stop_dir):
    env = {**os.environ, "PYTHONIOENCODING": "cp949"}
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "rail_stop.py"), "--why", "독 옮김"], env=env, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    st = json.loads((clean_stop_dir / rail_stop.STOP).read_text(encoding="utf-8"))
    assert st["why"] == "독 옮김" and st["by"] == "총괄(rail_stop.py)"
    assert "운전 중인 모터 프로그램 없음".encode("cp949") in r.stdout or "운전 중인 모터 프로그램 없음" in r.stdout.decode("utf-8", "replace")


def test_u14_request_failure_is_loud_and_nonzero(monkeypatch, tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("rail_stop_cli", ROOT / "tools" / "rail_stop.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(cli.rail_stop, "stop_dir", lambda: blocker / "sub")      # 폴더를 만들 수 없는 자리
    said = []
    code = cli.stop("시험", "사람 요청", say=said.append)
    assert code == cli.FAILED != 0 and code != cli.UNCONFIRMED
    assert "정지 요청 실패" in said[-1] and "전원 스위치" in said[-1], said


# ---------------------------------------------------------------- U11 — 풀기

def test_u11_clear_moves_flag_to_history(clean_stop_dir):
    req = rail_stop.request("시험", "사람 요청")
    dst = rail_stop.clear("김건우")
    assert rail_stop.active() is None and dst.parent.name == rail_stop.HISTORY
    h = json.loads(dst.read_text(encoding="utf-8"))
    assert h["id"] == req["id"] and h["cleared_by"] == "김건우" and h["cleared_at"]
    assert rail_stop.clear("김건우") is None


def test_u11_stop_command_has_no_clear_path():
    text = (ROOT / "tools" / "rail_stop.py").read_text(encoding="utf-8")
    assert "clear(" not in text                                        # 원격 풀기는 없다(D2) — 풀기는 레일 옆 창에만


# ---------------------------------------------------------------- U15 — 사람 정지 감속 시간 < 1.5초

def test_u15_stop_time_rule():
    k = 1000
    assert rail_stop.stop_time_problem(2000 * k, 10_000 * k) is None          # 0.2초
    assert rail_stop.stop_time_problem(1400 * k, 1000 * k) is None            # 1.4초
    assert "1.50초" in rail_stop.stop_time_problem(1500 * k, 1000 * k)
    assert rail_stop.stop_time_problem(1000 * k, 0)


def test_u15_every_approved_preset_keeps_rule():
    pre = ROOT.parent / "dynamic-bench" / "dev" / "presets"
    files = sorted(pre.glob("*.json"))
    assert files
    for f in files:
        a = json.loads(f.read_text(encoding="utf-8"))
        v = max(a.get("speeds_mm_s") or [a["vmax_mm_s"]])
        q = a.get("qs_decel_mm_s2") or 1000                           # 없으면 드라이브 평소 값(기준 파일)
        assert rail_stop.stop_time_problem(v * 1000, q * 1000) is None, f.name


def test_u15_long_refused_when_stop_time_too_long(rl, monkeypatch):
    rm = rl
    _put(rm.APPROVAL_LONG, {**LONG, "vmax_mm_s": 1600, "speeds_mm_s": [1600], "cycles_per_speed": 1})   # 급정지 감속을 올리지 않음 → 1.6초
    wire(rm, monkeypatch, speed=3000)
    reason, info, *_ = run_move(rm, "long")
    assert "사람 정지 감속 시간" in reason and "servo_on" not in info, reason


# ---------------------------------------------------------------- U16 — 0x6085 승인 기준값 대조 · 되돌림

def _bring_up_with(rm, box, q):
    real = rm.ecat.bring_up

    def bring_up(m, info):
        s, p0 = real(m, info)
        for r in info["sdo"]:
            if r["obj"] == "0x6085:00":
                r["value"] = q
        return s, p0
    return bring_up


def test_u16_leftover_raised_value_restored_and_recorded(rl, monkeypatch):
    rm = rl
    _put(rm.APPROVAL_LONG, LONG)
    said = []
    monkeypatch.setattr(rm.proc, "console", lambda s: said.append(str(s)))
    box = wire(rm, monkeypatch, speed=3000)
    monkeypatch.setattr(rm.ecat, "bring_up", _bring_up_with(rm, box, 10_000_000))
    reason, info, *_ = run_move(rm, "long")
    assert reason == rm.DONE_LONG, reason
    qb = info["qs_baseline"]
    assert qb["read"] == 10_000_000 and qb["baseline"] == 1_000_000 and qb["restored"] is True and qb["at"]
    assert (0x6085, 0, 1_000_000) in box["slave"].puts
    assert any("[QS] 0x6085 가 기준과 달라 되돌렸다" in s and "10000000" in s for s in said), said
    assert info.get("qs_restored", 1_000_000) == 1_000_000               # 끝에 되돌리는 값도 기준값


def test_u16_same_value_not_written(rl, monkeypatch):
    rm = rl
    _put(rm.APPROVAL_LONG, LONG)
    box = wire(rm, monkeypatch, speed=3000)
    reason, info, *_ = run_move(rm, "long")
    assert reason == rm.DONE_LONG, reason
    assert info["qs_baseline"]["restored"] is False and all(p[0] != 0x6085 for p in box["slave"].puts), box["slave"].puts


def test_u16_missing_baseline_file_refuses(rl, monkeypatch, tmp_path):
    rm = rl
    _put(rm.APPROVAL_LONG, LONG)
    monkeypatch.setattr(rm, "BASELINE", tmp_path / "없음.json")
    wire(rm, monkeypatch, speed=3000)
    reason, info, *_ = run_move(rm, "long")
    assert "0x6085 기준 파일" in reason and "servo_on" not in info, reason


def test_u16_baseline_file_in_repo_is_the_approved_value(rm):
    b = json.loads(rm.BASELINE.read_text(encoding="utf-8"))
    assert b["qs_decel_pulse_s2"] == 1_000_000 and b["approved_by"] and b["approved_at"]


# ---------------------------------------------------------------- U17 — 표지 자리 하나로

def test_u17_lock_dir_env_only_in_tests(monkeypatch, tmp_path):
    monkeypatch.setenv("RAIL_LOCK_DIR", str(tmp_path))
    monkeypatch.delenv("RAIL_NO_HARDWARE", raising=False)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "pd"))
    assert rail_stop.stop_dir() == tmp_path / "pd" / "QA-Automation-Test-Room" / "rail-lock"
    assert "시험 때만" in rail_stop.env_ignored()
    monkeypatch.setenv("RAIL_NO_HARDWARE", "pytest")
    assert rail_stop.stop_dir() == tmp_path and rail_stop.env_ignored() is None


def test_u17_watch_match_lines(clean_stop_dir, tmp_path):
    assert rail_stop.watch_matches(None)[0] is None
    rail_stop.write_watch(1234)
    assert rail_stop.watch_matches(1234) == (True, "정지 표지 자리: 모터 프로그램과 같음")
    same, line = rail_stop.watch_matches(999)
    assert same is False and "다르다" in line and "전원 스위치" in line
    assert rail_stop.watch_matches(1234, tmp_path)[0] is False          # 화면이 다른 자리를 본다


def test_u17_motor_records_stop_dir(rl, monkeypatch):
    rm = rl
    _put(rm.APPROVAL_LONG, LONG)
    wire(rm, monkeypatch, speed=3000)
    reason, info, *_ = run_move(rm, "long")
    assert info["stop_dir"] == str(rail_stop.stop_dir()) and info["stop_watch"]["started"] is True
    w = json.loads((rail_stop.stop_dir() / rail_stop.WATCH).read_text(encoding="utf-8"))
    assert w["pid"] == os.getpid() and w["stop_dir"] == str(rail_stop.stop_dir())
    assert info["alarms"] == [], info["alarms"]                        # 감시를 세워도 정상 끝에 '감시 멈춤' 이 남지 않는다
