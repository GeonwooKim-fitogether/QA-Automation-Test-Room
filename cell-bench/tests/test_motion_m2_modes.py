"""모터 M2 의 hold · slow 모드(입회 재기 · Ctrl+C 시험 · 랜선 시험) — 장비 없이 돈다. 진짜 pysoem 은 가져오지 않는다.

run_motion 과 motionbench.ecat 를 가짜 pysoem 모듈을 끼운 채 새로 가져온다 — 진짜 Master 를 만들지도, 어댑터를 열지도 않는다.
ecat 의 장비 함수(bring_up · sdo · sdo_put · go · shut · Cycler)는 test_motion_m2 의 FakeDrive(1 ms CiA402/PP 흉내) 위에 얹은
가짜로 바꿔 move() · main() 을 끝까지 돌린다. 승인 파일 · 입회 표지 · data 폴더는 전부 tmp_path 안이다.
가짜 드라이브의 speed 는 1 ms 틱당 pulse(10 ≈ 10 mm/s)이고 0x6081 은 보지 않는다 — 속도 · 감속 곡선은 이 시험이 증명하지 않는다.
"""
from __future__ import annotations

import _thread
import json
import math
import signal
import sys
import time
import types
from collections import deque
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from motionbench import m2, rail, safety  # noqa: E402
from motionbench.cia402 import state  # noqa: E402
from test_motion_m2 import GOOD, FakeDrive, _marker  # noqa: E402

P0, D, W = 1000, 10_000, 15_000          # GOOD: 10 mm · 창 15 mm · first_move_dir +1 · 1 pulse = 1 µm
# 레일 — 같은 전원으로 본다: 레일 파일을 깔고 드라이브 0x607D 가 그 limits 를 돌려준다 (시험마다 바꿀 수 있게 모듈 변수)
RAIL_DOC = rail.make(P0 + 400_000, P0 + 400_000 - rail.LENGTH, "지그만", ["plus", "minus"], {"지그만": rail.LENGTH}, 55,
                     "2026-10-28T00:00:00+09:00")
LIMITS = tuple(RAIL_DOC["limits"])
BIT4 = m2.NEW_SETPOINT                   # 0x10 — 새 목표
# 확인된 · GOOD(+1)과 맞는 축 정의 — + 는 왼쪽, Home 은 오른쪽이라 첫 이동(+)이 Home 에서 멀어진다. 진짜 axis-motor1.json 은 읽지 않는다
AXIS_GOOD = {"axis": "motor1", "viewpoint": "케이블 쪽이 사용자를 향하게 (시험)", "plus_direction": "왼쪽", "home_side": "오른쪽",
             "confirmed_by": "시험", "confirmed_at": "2026-10-27T15:00:00+09:00"}


# ---------------------------------------------------------------- 가짜 pysoem · 장비

class FakeMaster:
    """가짜 pysoem.Master — open() 은 부른 것을 적기만 한다. 어댑터 · 네트워크를 건드리는 코드가 없다."""
    made: list = []

    def __init__(self):
        self.opens, self.closes, self.expected_wkc, self.state = [], 0, 3, 0
        FakeMaster.made.append(self)

    def open(self, ifname):
        self.opens.append(ifname)

    def close(self):
        self.closes += 1


def _fake_pysoem():
    fake = types.ModuleType("pysoem")
    fake.INIT_STATE, fake.PREOP_STATE, fake.BOOT_STATE, fake.SAFEOP_STATE, fake.OP_STATE = 1, 2, 3, 4, 8
    for name in ("Emergency", "SdoError", "WkcError", "MailboxError", "PacketError"):
        setattr(fake, name, type(name, (Exception,), {}))
    fake.al_status_code_to_string = lambda code: "fake"
    fake.Master = FakeMaster
    return fake


class _Tap:
    """FakeDrive 가 보는 ax — 진짜 m2.Axis 에 그대로 넘기되, 그 사이에서 진짜 주기 스레드가 하던 일을 흉내 낸다."""

    def __init__(self, cyc, ax):
        self.cyc, self.ax = cyc, ax

    p0 = property(lambda self: self.ax.p0)
    window = property(lambda self: self.ax.window)

    def output(self):
        raw = self.ax.output()              # 진짜 Cycler 도 매 주기 ctl.output() 을 보낸다 — 랜선이 뽑혀도
        c = self.cyc
        if c.pulled:
            return c.frozen                 # 드라이브에는 더 닿지 않는다 — 마지막으로 받은 명령 그대로
        c.frozen = raw
        return raw

    def step(self, prev, v, t_mono):
        c = self.cyc
        if c.die:                           # 주기 스레드 안의 예외 — FakeCycler.run 이 진짜 Cycler.run 처럼 받는다
            raise c.die
        if c.pulled:                        # 진짜 Cycler: WKC 불일치 행 · step 없음 · 3주기째부터 매 주기 ctl.lost
            c.wkc_bad += 1
            c.rows.append((time.time(), t_mono, 20_000, -1, None))
            if c.wkc_bad >= 3:
                self.ax.lost(-1, 3)
            return
        if c.mutate:
            v = c.mutate(v)
        c.rows.append((time.time(), t_mono, 300, 3, v))
        self.ax.step(prev, v, t_mono)
        if c.on_tick:
            c.on_tick(c, v)


class FakeCycler(FakeDrive):
    """ecat.Cycler 자리 — FakeDrive 에 rows · stop · is_alive · join 과 세 가지 사고를 얹는다.
    pulled = True: 랜선 뽑힘. die = 예외 객체: 주기 스레드가 그 예외로 죽음. mutate(v): 드라이브가 보낸 입력을 바꿔 끼움."""

    def __init__(self, pos, ax, speed=40, on_tick=None):
        self.stop = None
        self.rows = deque()
        self.pulled, self.die, self.wkc_bad, self.frozen = False, None, 0, None
        self.on_tick, self.mutate, self.real = on_tick, None, ax
        super().__init__(_Tap(self, ax), pos, speed=speed)

    def run(self):                          # 진짜 Cycler.run 과 같다 — 예외면 이유를 stop 에 남기고 곧바로 끝난다
        try:
            super().run()
        except Exception as e:
            self.stop = self.stop or f"주기 스레드 예외 {type(e).__name__}: {e}"

    @property
    def go(self):                           # FakeDrive.run 의 while self.go — stop 이 채워지면 멈춘다
        return self.stop is None

    @go.setter
    def go(self, value):
        if not value:
            self.stop = self.stop or "시험 끝"


class Slave:
    def __init__(self):
        self.state, self.puts = 4, []


def wire(rm, monkeypatch, speed=40, on_tick=None) -> dict:
    """ecat 의 장비 함수를 가짜로 바꾼다 → 상자(move 가 만든 주기 스레드는 box["cyc"])."""
    box = {"slave": Slave()}
    ecat = rm.ecat

    def bring_up(m, info):
        info["slaves"] = 1
        info["sdo"] = [{"obj": "0x1018:04", "value": safety.SERIAL}, {"obj": "0x605A:00", "value": 2},
                       {"obj": "0x6085:00", "value": 1_000_000}, {"obj": "0x607D:01", "value": LIMITS[0]},
                       {"obj": "0x607D:02", "value": LIMITS[1]}]
        info["expected_wkc"] = m.expected_wkc
        return box["slave"], P0

    def sdo(s, index, sub, fmt, release_gil=False):
        key = f"0x{index:04X}:{sub:02X}"
        if key in safety.GEAR:
            return safety.GEAR[key], None
        return {0x3513: 7400, 0x603F: 0, 0x6041: 0x0250}.get(index, 1234), None

    def sdo_put(s, index, sub, fmt, value, release_gil=False):
        c = box.get("cyc")
        if c is not None and (c.pulled or c.die):
            raise ecat.Stop(f"SDO 0x{index:04X}:{sub:02X} 쓰기 실패 — WkcError (흉내: 통신 두절)")
        s.puts.append((index, sub, value))
        return value

    def go(m, s, want, seconds=3.0):
        s.state = want
        return f"상태 {want} · AL 0x0000 fake"

    def shut(m):
        m.close()
        return "INIT"

    def cycler(m, s, pos, seconds, ctl=None):
        box["cyc"] = FakeCycler(pos, ctl, speed, on_tick)
        return box["cyc"]

    for name, fn in (("bring_up", bring_up), ("sdo", sdo), ("sdo_put", sdo_put), ("go", go), ("shut", shut),
                     ("Cycler", cycler)):
        monkeypatch.setattr(ecat, name, fn)
    return box


class Keys:
    """run_motion._enter_key 자리 — Enter 기다림마다 한 번 불린다. plan[i] 초 뒤 Enter, None 이면 누르지 않는다."""

    def __init__(self, *plan):
        self.plan, self.calls, self.enters = list(plan), [], []

    def __call__(self):
        delay = self.plan.pop(0) if self.plan else None
        t0 = time.perf_counter_ns()
        self.calls.append(t0)

        def done():
            if delay is None or time.perf_counter_ns() - t0 < delay * 1e9:
                return False
            if len(self.enters) < len(self.calls):
                self.enters.append(time.perf_counter_ns())
            return True
        return done


@pytest.fixture
def rm(monkeypatch, tmp_path):
    """가짜 pysoem 을 끼우고 run_motion 을 새로 가져온다 → run_motion 모듈. 끝나면 가짜에 묶인 모듈을 걷어 낸다."""
    import motionbench
    fake = _fake_pysoem()
    for name in ("run_motion", "motionbench.ecat"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.delattr(motionbench, "ecat", raising=False)
    monkeypatch.setitem(sys.modules, "pysoem", fake)
    FakeMaster.made = []
    try:
        import run_motion
        assert run_motion.ecat.pysoem is fake and run_motion.ecat.pysoem.Master is FakeMaster
        monkeypatch.setattr(run_motion.ecat, "IFNAME", "bogus-no-adapter")
        data = tmp_path / "data"
        data.mkdir()
        approval = tmp_path / "m2-approval.json"
        approval.write_text(json.dumps(GOOD), encoding="utf-8")
        monkeypatch.setattr(run_motion, "APPROVAL", approval)
        monkeypatch.setattr(run_motion, "DATA", data)
        monkeypatch.setattr(run_motion, "MARKER", tmp_path / "motion_attended")   # 아직 없다 — 쓰는 시험만 _marker 로 만든다
        axis = tmp_path / "axis-motor1.json"                                      # 진짜 축 정의(home_side 가 아직 null)는 읽지 않는다
        axis.write_text(json.dumps(AXIS_GOOD, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(run_motion, "AXIS", axis)
        monkeypatch.setattr(run_motion, "_console_ok", lambda: True)
        monkeypatch.setattr(run_motion, "BATT_LOW", lambda: (80, 24, None))           # 충전 기준 — 진짜 잔량 파일 · 플러그에 닿지 않는다
        run_motion.plug_calls = []
        monkeypatch.setattr(run_motion, "PLUG_SET", lambda on, say=None: run_motion.plug_calls.append(on) or {"want": "on" if on else "off", "on": on})
        monkeypatch.setattr(run_motion, "RAIL", data / "rail-motor1.json")
        (data / "rail-motor1.json").write_text(json.dumps(RAIL_DOC, ensure_ascii=False), encoding="utf-8")
        yield run_motion
    finally:
        sys.modules.pop("run_motion", None)
        sys.modules.pop("motionbench.ecat", None)
        if "ecat" in vars(motionbench):
            delattr(motionbench, "ecat")


def run_move(rm, mode, hold_s=5.0, mm_s=2.0):
    """→ (끝난 이유, info, 가짜 Master, 걸린 초, 앞의 SIGINT 처리기). KeyboardInterrupt 가 새면 pytest 전체가 멈추므로 실패로 바꾼다."""
    m, info = FakeMaster(), {}
    before = signal.getsignal(signal.SIGINT)
    t0 = time.monotonic()
    try:
        reason = rm.move(m, info, mode, hold_s, mm_s)
    except KeyboardInterrupt:
        pytest.fail("KeyboardInterrupt 가 move() 밖으로 샜다")
    return reason, info, m, time.monotonic() - t0, before


def after(trace, t_ns):
    return [r for r in trace if r[0] >= t_ns]   # trace 행: (t_mono_ns, 보낸 cw, 보낸 target, sw, err, mode, pos, ferr)


def _servo_out(tmp_path, speed=40):
    """m2 수준 준비 — 가짜 드라이브를 돌리고 서보 온 → p0 + D 로 한 번 간다 → (ax, drive, marker)."""
    marker = _marker(tmp_path)
    ax = m2.Axis(P0, W, time.time() + 600)
    drive = FakeCycler(P0, ax, speed=speed)
    drive.start()
    m2.servo_on(ax, marker)
    out = m2.move(ax, P0 + D, marker, 10_000, 50_000, 1_000_000)
    assert out["reached"] == P0 + D
    return ax, drive, marker


# ---------------------------------------------------------------- ① hold 가 Enter 로 끝난다

def test_hold_ends_on_enter_without_new_setpoint(tmp_path):
    ax, drive, _ = _servo_out(tmp_path)
    try:
        n0, cws0 = len(ax.trace), list(drive.cws)
        t_end = time.monotonic() + 0.4
        h = m2.hold(ax, 5.0, lambda: time.monotonic() >= t_end)
        rows, cws1, qs = ax.trace[n0:], list(drive.cws), ax.qs
    finally:
        drive.go = False
        drive.join(1)
    assert h["ended_by"] == "Enter" and 0.35 <= h["held_s"] < 1.0, h
    assert h["pos"] == h["pos_min"] == h["pos_max"] == P0 + D and h["ferr_max"] == 0
    assert len(rows) > 100 and {(r[1], r[2]) for r in rows} == {(m2.PP_RUN, P0 + D)}   # 0x2F · 같은 목표뿐 — bit4 없음
    assert qs is None and cws1 == cws0 and m2.QUICK_STOP not in cws1 and not drive.bad


# ---------------------------------------------------------------- ② hold 가 시간으로 끝난다

def test_hold_ends_on_time(tmp_path):
    ax, drive, _ = _servo_out(tmp_path)
    try:
        n0 = len(ax.trace)
        h = m2.hold(ax, 0.5, lambda: False)
        rows, qs = ax.trace[n0:], ax.qs
    finally:
        drive.go = False
        drive.join(1)
    assert h["ended_by"] == "시간" and 0.5 <= h["held_s"] <= 0.75, h
    assert h["pos_min"] == h["pos_max"] == P0 + D and qs is None
    assert not any(r[1] & BIT4 for r in rows)


def test_hold_mode_timeout_ends_without_moving_back(rm, monkeypatch, tmp_path):
    _marker(tmp_path)
    box = wire(rm, monkeypatch)
    keys = Keys(None)                                  # Enter 를 누르지 않는다
    monkeypatch.setattr(rm, "_enter_key", keys)
    reason, info, m, took, before = run_move(rm, "hold", hold_s=0.5)
    c = box["cyc"]
    assert reason == "유지 시간 다 됨 — +10 mm 자리에서 움직이지 않고 끝냄", reason
    assert [g["target"] for g in info["legs"]] == [P0 + D] and info["legs"][0]["reached"] == P0 + D
    assert [h["ended_by"] for h in info["holds"]] == ["시간"] and len(keys.calls) == 1
    rows = after(c.real.trace, keys.calls[0])
    assert len(rows) > 100 and not any(r[1] & BIT4 for r in rows)                     # 머문 뒤 새 목표 없음
    assert {r[6] for r in rows} == {P0 + D} and info["final"]["pos"] == P0 + D        # 슬라이더는 +10 mm 에 남는다
    assert c.cws[-3:] == [m2.QUICK_STOP, m2.SHUTDOWN, 0] and info["final"]["state"] == "SWITCH_ON_DISABLED"
    assert "interrupted_leg" not in info and not c.bad
    assert m.opens == ["bogus-no-adapter"] and info["closed"] == "INIT"
    assert signal.getsignal(signal.SIGINT) == before


# ---------------------------------------------------------------- ③ 머무는 동안에도 매 주기 검사가 돈다

@pytest.mark.parametrize("kind, rule", [("window", "창"), ("ferr", "추종 오차"), ("fault", "fault 비트"), ("err", "0x603F")])
def test_hold_keeps_auto_quick_stop_rules(tmp_path, kind, rule):
    ax, drive, _ = _servo_out(tmp_path)
    left = [3]      # fault · err 는 3주기만 — 가짜 드라이브의 상태표에는 Fault 가 없어, 계속 켜 두면 종료 순서를 볼 수 없다

    def mutate(v):
        if kind == "window":
            return v._replace(pos=P0 + W + 500)
        if kind == "ferr":
            return v._replace(ferr=safety.FERR_MAX + 500)
        if left[0] <= 0:
            return v
        left[0] -= 1
        return v._replace(sw=v.sw | safety.FAULT_BIT) if kind == "fault" else v._replace(err=0x7500)

    calls = []

    def done():
        calls.append(time.perf_counter_ns())
        if len(calls) == 2:                  # 머무는 중(두 번째 확인)에 이상을 끼운다
            drive.mutate = mutate
        return False

    try:
        n0 = len(ax.trace)
        with pytest.raises(m2.Abort, match=rule):
            m2.hold(ax, 5.0, done)
        did = m2.finish(ax)
        end = state(ax.last.sw)
    finally:
        drive.go = False
        drive.join(1)
    assert len(calls) == 2 and rule in ax.qs                        # 끼운 바로 다음 기다림에서 멈췄다
    assert m2.QUICK_STOP in [r[1] for r in after(ax.trace[n0:], calls[1])]   # 0x02 가 실제로 나갔다
    assert drive.cws[-4:] == [m2.PP_RUN, m2.QUICK_STOP, m2.SHUTDOWN, 0]
    assert end == "SWITCH_ON_DISABLED", did
    assert not drive.bad


# ---------------------------------------------------------------- ④ 3초 카운트다운

def test_hold_mode_full_run_counts_down_before_return(rm, monkeypatch, tmp_path):
    _marker(tmp_path)
    box = wire(rm, monkeypatch)
    keys = Keys(0.3, 0.3)
    monkeypatch.setattr(rm, "_enter_key", keys)
    reason, info, m, took, before = run_move(rm, "hold", hold_s=10)
    c = box["cyc"]
    assert reason == rm.DONE_M2, reason
    assert [g["target"] for g in info["legs"]] == [P0 + D, P0]
    assert [h["ended_by"] for h in info["holds"]] == ["Enter", "Enter"] and [h["pos"] for h in info["holds"]] == [P0 + D, P0]
    enter1 = keys.enters[0]
    t4 = next(r[0] for r in c.real.trace if r[0] >= enter1 and r[1] & BIT4)          # 돌아오는 이동의 첫 bit4
    assert 2.9 <= (t4 - enter1) / 1e9 < 4.0, (t4 - enter1) / 1e9
    assert not any(r[1] & BIT4 for r in c.real.trace if keys.calls[0] <= r[0] < enter1)
    assert not any(r[1] & BIT4 for r in after(c.real.trace, keys.calls[1]))
    assert c.cws[-3:] == [m2.QUICK_STOP, m2.SHUTDOWN, 0] and info["final"]["state"] == "SWITCH_ON_DISABLED"
    assert signal.getsignal(signal.SIGINT) == before and not c.bad


def test_hold_mode_ctrl_c_in_countdown_moves_no_further(rm, monkeypatch, tmp_path):
    _marker(tmp_path)
    keys = Keys(0.2, 0.2)
    fired = []

    def on_tick(c, v):                       # 주기 스레드는 move() 안에서만 산다(시작 · join 이 move 안) — 밖으로 새지 않는다
        if keys.enters and not fired and time.perf_counter_ns() - keys.enters[0] >= 1e9:
            fired.append(time.perf_counter_ns())
            _thread.interrupt_main()         # 카운트다운 1초째에 Ctrl+C

    box = wire(rm, monkeypatch, on_tick=on_tick)
    monkeypatch.setattr(rm, "_enter_key", keys)
    reason, info, m, took, before = run_move(rm, "hold", hold_s=10)
    c = box["cyc"]
    assert fired and reason == "Ctrl+C", reason
    assert [g["target"] for g in info["legs"]] == [P0 + D] and len(info["holds"]) == 1 and len(keys.calls) == 1
    assert not any(r[1] & BIT4 for r in after(c.real.trace, keys.enters[0]))        # 돌아오는 이동 없음
    assert "interrupted_leg" not in info and info["ctrl_c"]["pos"] == info["final"]["pos"] == P0 + D
    assert c.cws[-3:] == [m2.QUICK_STOP, m2.SHUTDOWN, 0] and info["final"]["state"] == "SWITCH_ON_DISABLED"
    assert signal.getsignal(signal.SIGINT) == before
    assert info["ctrl_c"]["moving"] is False                         # 카운트다운 중 — 움직이는 중이 아니었다
    info["exit"] = reason                                            # main 이 하는 것처럼
    text = "\n".join(rm._summary(info, rm.DATA / "result.json"))
    assert "판정 불가" in text and "④ 통과" not in text, text


# ---------------------------------------------------------------- ⑤ slow 중 통신 두절

def _pull_at_2mm(c, v):
    if not c.pulled and c.real.target == P0 + D and v.pos >= P0 + 2000:
        c.pull_t, c.pull_pos = time.monotonic(), v.pos
        c.pulled = True


def test_slow_cable_pull_reports_comms_loss(rm, monkeypatch, tmp_path):
    _marker(tmp_path)
    box = wire(rm, monkeypatch, speed=2, on_tick=_pull_at_2mm)        # 2 mm/s(기본) 로 가다가 2 mm 에서 랜선을 뽑는다
    monkeypatch.setattr(rm, "_enter_key", Keys(0.1))
    reason, info, m, took, before = run_move(rm, "slow", hold_s=10, mm_s=2.0)
    c = box["cyc"]
    assert c.pulled and time.monotonic() - c.pull_t <= 20
    assert "WKC" in reason, reason
    x = info["comms_lost"]
    assert x["source"] == "WKC" and (x["wkc"], x["exp"]) == (-1, 3)
    assert P0 < x["last_pos"] < P0 + D and x["last_pos"] == c.pull_pos and x["target"] == P0 + D
    assert x["at_ts"] and x["last_ts"]
    g = info["interrupted_leg"]
    assert g["name"] == "가기" and g["target"] == P0 + D and P0 < g["last_pos"] < P0 + D
    assert info["legs"] == [] and info["final"]["stale"] is True and info["final"]["input_at"]
    assert isinstance(info["sdo_restored"], str) and info["sdo_restored"].startswith("실패")
    assert info["closed"] == "INIT" and m.closes == 1
    assert signal.getsignal(signal.SIGINT) == before


def test_slow_cable_pull_through_main_writes_its_own_json(rm, monkeypatch, tmp_path):
    _marker(tmp_path)
    wire(rm, monkeypatch, speed=2, on_tick=_pull_at_2mm)
    monkeypatch.setattr(rm, "_enter_key", Keys(0.1))
    monkeypatch.setattr(rm.proc, "safe_stdio", lambda: None)          # pytest 의 화면 포착을 건드리지 않게
    monkeypatch.setattr(sys, "argv", ["run_motion.py", "slow", "--hold-s", "10"])
    sw, before = sys.getswitchinterval(), signal.getsignal(signal.SIGINT)
    try:
        code = rm.main()
    except KeyboardInterrupt:
        pytest.fail("KeyboardInterrupt 가 main() 밖으로 샜다")
    finally:
        sys.setswitchinterval(sw)
    data = rm.DATA
    found = list(data.glob("motion_m2_slow_*.json"))
    assert code == 1 and len(found) == 1, sorted(p.name for p in data.iterdir())
    assert not (data / "motion_m2.json").exists()
    doc = json.loads(found[0].read_text(encoding="utf-8"))
    stamp = found[0].stem.removeprefix("motion_m2_slow_")
    assert doc["stamp"] == stamp and doc["mode"] == "slow" and doc["ifname"] == "bogus-no-adapter"
    assert "WKC" in doc["exit"]
    x = doc["comms_lost"]
    assert x["source"] == "WKC" and P0 < x["last_pos"] < P0 + D and x["target"] == P0 + D
    assert doc["interrupted_leg"]["name"] == "가기" and doc["final"]["stale"] is True
    assert doc["sdo_restored"].startswith("실패")
    assert (data / f"motion_m2_slow_{stamp}.csv").exists() and (data / f"motion_m2_slow_{stamp}_trace.csv").exists()
    assert [mm.opens for mm in FakeMaster.made] == [["bogus-no-adapter"]]
    assert signal.getsignal(signal.SIGINT) == before


def test_slow_cycle_thread_exception_reports_comms_loss(rm, monkeypatch, tmp_path):
    _marker(tmp_path)
    boom = OSError("[WinError 22] 장치를 찾을 수 없다 (흉내)")
    why = f"주기 스레드 예외 OSError: {boom}"

    def on_tick(c, v):
        if not c.die and c.real.target == P0 + D and v.pos >= P0 + 2000:
            c.die = boom

    box = wire(rm, monkeypatch, speed=10, on_tick=on_tick)
    monkeypatch.setattr(rm, "_enter_key", Keys(0.1))
    reason, info, m, took, before = run_move(rm, "slow", hold_s=10, mm_s=10.0)
    seen = (reason, info.get("alarms"), info.get("final"))           # 다시 깨지면 무엇이 먼저 걸렸는지 바로 보이게
    assert "주기 스레드" in reason and not box["cyc"].is_alive(), seen
    x = info.get("comms_lost")
    assert x and x["source"] == "주기 스레드 예외" and x["why"] == why, (seen, x)
    assert P0 < x["last_pos"] < P0 + D and x["target"] == P0 + D and x["last_ts"], x
    assert info["interrupted_leg"]["name"] == "가기", seen
    assert info["final"]["stale"] is True, seen
    assert info["closed"] == "INIT" and signal.getsignal(signal.SIGINT) == before


# ---------------------------------------------------------------- ⑥ 범위 밖 값은 어댑터 전에 거부

BAD_HOLD = [0, -1, 300.01, math.nan, math.inf]
BAD_MM = [0, 0.99, 10.01, 11, math.nan, math.inf]


def test_attended_args_rejected_before_adapter(rm, monkeypatch, tmp_path):
    _marker(tmp_path)                        # 표지가 유효해도 — 거부는 그보다 앞이다
    box = wire(rm, monkeypatch)
    cases = ([("hold", h, 2.0) for h in BAD_HOLD] + [("slow", h, 2.0) for h in BAD_HOLD]
             + [("slow", 120.0, v) for v in BAD_MM] + [("jog", 120.0, 2.0)])
    for mode, hold_s, mm_s in cases:
        m, info = FakeMaster(), {}
        reason = rm.move(m, info, mode, hold_s, mm_s)
        assert reason.startswith("거부"), (mode, hold_s, mm_s, reason)
        assert m.opens == [] and "marker_start" not in info and "cyc" not in box, (mode, hold_s, mm_s)
    monkeypatch.setattr(rm, "_console_ok", lambda: False)
    for mode in ("hold", "slow"):
        m, info = FakeMaster(), {}
        reason = rm.move(m, info, mode, 120.0, 2.0)
        assert reason.startswith("거부") and "콘솔" in reason and m.opens == [] and "marker_start" not in info


def test_attended_args_in_limits_reach_the_marker_check(rm, monkeypatch, tmp_path):
    box = wire(rm, monkeypatch)              # 표지 파일이 없다 — 검증을 통과하면 표지 거부에서 멈춘다
    for mode, hold_s, mm_s in [("hold", 300, 2.0), ("hold", 0.01, 2.0), ("slow", 300, 1), ("slow", 120.0, 10),
                               ("slow", 0.5, 1.5)]:
        m, info = FakeMaster(), {}
        reason = rm.move(m, info, mode, hold_s, mm_s)
        assert reason.startswith("입회 표지 무효 — 서보 온 거부"), (mode, hold_s, mm_s, reason)
        assert info["marker_start"]["valid"] is False and m.opens == [] and "cyc" not in box


def test_main_rejects_nan_speed_before_adapter(rm, monkeypatch, tmp_path):
    _marker(tmp_path)
    wire(rm, monkeypatch)
    monkeypatch.setattr(rm.proc, "safe_stdio", lambda: None)
    monkeypatch.setattr(sys, "argv", ["run_motion.py", "slow", "--mm-s", "nan"])
    sw = sys.getswitchinterval()
    try:
        code = rm.main()
    finally:
        sys.setswitchinterval(sw)
    found = list(rm.DATA.glob("motion_m2_slow_*.json"))
    doc = json.loads(found[0].read_text(encoding="utf-8"))
    assert code == 1 and doc["exit"].startswith("거부 — --mm-s nan")
    assert [mm.opens for mm in FakeMaster.made] == [[]] and not (rm.DATA / "motion_m2.json").exists()


# ---------------------------------------------------------------- ⑦ slow 중 Ctrl+C

def test_slow_ctrl_c_mid_move_stops_and_ignores_second(rm, monkeypatch, tmp_path):
    _marker(tmp_path)
    keys = Keys(0.1)
    ev = {}

    def on_tick(c, v):                       # 주기 스레드는 move() 안에서만 산다 — 두 번 다 move() 가 도는 동안이다
        ax = c.real
        if "first" not in ev:
            if keys.enters and ax.target == P0 + D and v.pos >= P0 + D // 3:
                ev["first"], ev["first_pos"] = time.perf_counter_ns(), v.pos
                _thread.interrupt_main()     # 가는 길 1/3 에서 Ctrl+C
        elif "second" not in ev and ax.sent[0] == m2.QUICK_STOP:
            ev["second"], ev["handler"] = time.perf_counter_ns(), signal.getsignal(signal.SIGINT)
            _thread.interrupt_main()         # 종료 순서 도중 두 번째 Ctrl+C — 순서를 끊으면 안 된다

    box = wire(rm, monkeypatch, speed=10, on_tick=on_tick)
    monkeypatch.setattr(rm, "_enter_key", keys)
    reason, info, m, took, before = run_move(rm, "slow", hold_s=10, mm_s=10.0)
    c, cc = box["cyc"], info["ctrl_c"]
    assert reason == "Ctrl+C", reason
    assert "second" in ev and ev["handler"] == signal.SIG_IGN
    assert isinstance(cc["qs_sent_ms"], float) and 0 <= cc["qs_sent_ms"] < 100, cc
    assert cc["stop_distance_mm"] is not None and cc["stop_distance_mm"] <= 5, cc
    assert P0 + D // 3 <= cc["pos"] < P0 + D and cc["target"] == P0 + D
    assert c.cws[-3:] == [m2.QUICK_STOP, m2.SHUTDOWN, 0]
    assert info["final"]["state"] == "SWITCH_ON_DISABLED" and info["final"]["stale"] is False
    assert info["interrupted_leg"]["name"] == "가기" and any("Controlword 0" in s for s in info["finish"])
    assert info["closed"] == "INIT" and isinstance(info["sdo_restored"], dict)
    assert signal.getsignal(signal.SIGINT) == before
    assert cc["moving"] is True, (cc, info["interrupted_leg"])      # 가는 길 1/3 — 출발점 · 목표에서 1 mm 넘게 떨어짐
    info["exit"] = reason                                            # main 이 하는 것처럼
    text = "\n".join(rm._summary(info, rm.DATA / "result.json"))
    assert "④ 통과 (" in text and "판정 불가" not in text, text
