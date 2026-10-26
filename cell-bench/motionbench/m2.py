"""M2 — 입회 10 mm 왕복의 명령 쪽. pysoem 을 가져오지 않는다(장비 없이 시험한다).

주기 스레드(ecat.Cycler)가 매 주기 Axis.output() 으로 출력 PDO 를 만들고 Axis.step() 으로 입력을 넘긴다.
명령 스레드(run_motion.py move)는 cw · target 을 바꾸고 Axis.wait() 로 Statusword 를 기다린다.
Quick stop 은 걸쇠(qs)다 — 한 번 걸리면 cw 와 상관없이 Controlword 0x0002(bit2 = 0)를 보낸다. Fault reset(0x80)은 어디에도 없다.
"""
from __future__ import annotations

import time

from . import safety
from .cia402 import OUT, state

SHUTDOWN, SWITCH_ON, ENABLE, QUICK_STOP = 0x06, 0x07, 0x0F, 0x02
PP = 1                     # 0x6060 Profile Position
# 새 목표 핸드셰이크 (plan-v1 3.3): 0x3F(bit4 new set-point) → bit12 ack 확인 → 0x2F(bit4 내림) → bit12 = 0 확인.
# bit5(change set immediately)는 늘 1 — 조시우가 쓰던 값 그대로. 이동이 끝난 뒤에만 다음 목표를 주므로 bit5 의 효과는 없다. bit6 = 0 → 절대 위치.
PP_RUN, NEW_SETPOINT = 0x2F, 0x10
ACK, REACHED = 0x1000, 0x0400


class Abort(Exception):
    """이번 실행을 멈추는 이유. 부르는 쪽이 finish() 로 내린다."""


class Axis:
    def __init__(self, p0: int, window: int, deadline: float, mode: int = PP):
        self.p0, self.window, self.deadline = p0, window, deadline
        self.cw, self.mode, self.target = 0, mode, p0
        self.follow = True         # 서보 온 전에는 Target = 방금 읽은 실제 위치 (점프 금지)
        self.qs: str | None = None  # 걸린 Quick stop 의 이유
        self.closing = False       # 종료 순서에서만 걸쇠를 비켜 0x06 · 0 을 보낸다
        self.last = None
        self.sent = (0, p0)
        self.prev_t = None
        self.trace: list[tuple] = []   # (t_mono_ns, 보낸 cw, 보낸 target, sw, err, mode, pos, ferr)
        self.alarms: list[tuple] = []  # 자동 Quick stop 규칙에 걸린 (시각, 이유)
        self.idle = lambda: None   # 기다리는 동안 부를 것 (기록 비우기) — 부르는 쪽이 채운다
        self.dead = lambda: None   # 주기 스레드가 죽었으면 이유

    # ---- 주기 스레드 쪽 (ecat.Cycler 가 부른다) ----
    def output(self) -> bytes:
        cw = QUICK_STOP if self.qs and not self.closing else self.cw
        self.sent = (cw, self.target)
        return OUT.pack(cw, self.mode, self.target, 0)

    def step(self, prev, v, t_mono: int) -> None:
        gap = None if self.prev_t is None else (t_mono - self.prev_t) / 1e6
        self.prev_t = t_mono
        if self.follow:
            self.target = v.pos
        self.last = v
        self.trace.append((t_mono, *self.sent, v.sw, v.err, v.mode, v.pos, v.ferr))
        self._alarm(safety.auto_qs(v, gap, self.p0, self.window, self.deadline, time.time()))

    def lost(self, wkc: int, exp: int) -> None:
        self._alarm(f"WKC 3주기 연속 불일치 (마지막 {wkc}, 기대 {exp})")

    def _alarm(self, why: str | None) -> None:
        """자동 Quick stop. 이미 걸려 있어도(시험 · 종료) 이유는 따로 남긴다 — 시험 중의 이상이 가려지지 않게."""
        if not why:
            return
        if not self.alarms or self.alarms[-1][1] != why:
            self.alarms.append((time.time(), why))
        if not self.qs:
            self.qs = why

    # ---- 명령 스레드 쪽 ----
    def wait(self, cond, timeout: float, what: str = "", allow_qs: bool = False):
        """cond(입력)이 참이 될 때까지 기다린다. cond 가 None 이면 timeout 만큼 쉰다. Quick stop · 주기 스레드 멈춤이면 Abort."""
        end = time.monotonic() + timeout
        while True:
            self.idle()
            if self.qs and not allow_qs:
                raise Abort(f"Quick stop — {self.qs}")
            if self.dead():
                raise Abort(f"주기 스레드 멈춤 — {self.dead()}")
            v = self.last
            if cond and v is not None and cond(v):
                return v
            if time.monotonic() > end:
                if cond is None:
                    return v
                raise Abort(f"{what} — {timeout}초 안에 안 됨 (statusword {f'0x{v.sw:04X}' if v else '-'})")
            time.sleep(0.004)

    def pause(self, seconds: float, allow_qs: bool = False):
        return self.wait(None, seconds, allow_qs=allow_qs)


def servo_on(ax: Axis, marker) -> dict:
    """입회 표지를 다시 보고, 유효할 때만 CiA402 정상 전이로 서보를 켠다. 무효면 Controlword 를 건드리지 않고 거부."""
    why, deadline = safety.check_marker(marker, time.time())
    if why:
        raise Abort(f"입회 표지 무효 — 서보 온 거부: {why}")
    ax.deadline = deadline
    ax.wait(lambda v: v.mode == ax.mode, 1.0, "0x6061 이 PP(1) 가 아니다")
    ax.cw = SHUTDOWN
    ax.wait(lambda v: state(v.sw) == "READY_TO_SWITCH_ON", 1.0, "Shutdown 0x06 → Ready to switch on")
    ax.cw = SWITCH_ON
    ax.wait(lambda v: state(v.sw) == "SWITCHED_ON", 1.0, "Switch on 0x07 → Switched on")
    ax.follow = False                # Target 은 바로 직전의 실제 위치에서 얼린다
    ax.cw = ENABLE
    v = ax.wait(lambda v: state(v.sw) == "OPERATION_ENABLED", 1.0, "Enable 0x0F → Operation enabled")
    return {"target_at_enable": ax.target, "pos_at_enable": v.pos, "marker_deadline": deadline}


def move(ax: Axis, target: int, marker, v_pps: float, a_pps2: float, a_qs: float, qs_after: int | None = None) -> dict:
    """PP 이동 하나. 매 이동 직전 표지 · 창을 본다. qs_after 를 주면 그만큼 간 뒤 프로그램이 스스로 Quick stop 을 건다."""
    why, deadline = safety.check_marker(marker, time.time())
    if why:
        ax.qs = f"입회 표지 무효: {why}"
        raise Abort(ax.qs)
    ax.deadline = deadline
    why = safety.target_problem(ax.p0, target, ax.window, v_pps, a_qs)
    if why:
        raise Abort(f"전송 없이 거부 — {why}")
    start = ax.wait(lambda v: not v.sw & ACK, 0.5, "set-point acknowledge 가 0 이 아니다").pos
    t0 = time.perf_counter_ns()
    ax.target = target               # 목표를 먼저, bit4 는 그 다음 — 그 사이 한 주기가 나가도 새 목표 없이 0x2F 일 뿐
    ax.cw = PP_RUN | NEW_SETPOINT
    ax.wait(lambda v: v.sw & ACK, 0.5, "set-point acknowledge(bit12) 가 안 온다")
    ax.cw = PP_RUN
    ax.wait(lambda v: not v.sw & ACK, 0.5, "bit4 를 내렸는데 bit12 가 안 내려간다")
    dist = abs(target - start)
    limit = 2 * (dist / v_pps + v_pps / a_pps2) + 2       # 프로파일 시간의 2배 + 2초
    out = {"start": start, "target": target}
    if qs_after is not None:
        v = ax.wait(lambda v: abs(v.pos - start) >= qs_after, limit, "Quick stop 시험 지점에 못 감")
        ax.qs = "Quick stop 시험 (프로그램이 이동 도중 스스로 건 것)"
        t_qs = time.perf_counter_ns()
        ax.wait(lambda v: state(v.sw) == "SWITCH_ON_DISABLED", 2.0, "Quick stop 뒤 Switch on disabled", allow_qs=True)
        ax.pause(0.3, allow_qs=True)
        stop = ax.last.pos
        out.update(qs_at=v.pos, qs_stop=stop, qs_distance=abs(stop - v.pos), qs_ms=round((time.perf_counter_ns() - t_qs) / 1e6))
    else:
        ax.wait(lambda v: v.sw & REACHED and abs(v.pos - target) <= 1000, limit, "Target reached 가 안 온다")
        ax.pause(0.3)                # 멈춘 뒤 위치
        out.update(reached=ax.last.pos, err_um=abs(ax.last.pos - target))
    out.update(_leg_stats(ax.trace, t0, time.perf_counter_ns()))
    return out


def resume(ax: Axis, marker) -> dict:
    """Quick stop 시험 뒤에만 쓴다 — 자동 규칙에 걸린 것이 하나라도 있으면 풀지 않는다.
    걸쇠를 풀기 전에 Controlword 를 0 으로 내려 두고, 정상 전이로 다시 켠다."""
    if ax.alarms:
        raise Abort(f"Quick stop 시험 중 자동 규칙에 걸림 — {ax.alarms[-1][1]}")
    ax.follow = True
    ax.cw = 0
    ax.qs = None
    return servo_on(ax, marker)


def finish(ax: Axis) -> list[str]:
    """종료 순서(예외 · Ctrl+C 포함): Quick stop → Shutdown 0x06 → Controlword 0. SAFE-OP · INIT · 닫기는 부르는 쪽."""
    did = []
    ax.follow = True
    if not ax.qs:
        ax.qs = "종료"
    try:
        ax.wait(lambda v: state(v.sw) not in ("OPERATION_ENABLED", "QUICK_STOP_ACTIVE"), 2.0, "Quick stop 뒤 정지", allow_qs=True)
        did.append(f"Quick stop → {state(ax.last.sw)}")
        ax.cw = SHUTDOWN
        ax.closing = True
        ax.pause(0.1, allow_qs=True)
        did.append(f"Shutdown 0x06 → {state(ax.last.sw)}")
        ax.cw = 0
        ax.wait(lambda v: state(v.sw) == "SWITCH_ON_DISABLED", 1.0, "Controlword 0 → Switch on disabled", allow_qs=True)
        did.append(f"Controlword 0 → {state(ax.last.sw)} (statusword 0x{ax.last.sw:04X})")
    except Abort as e:
        ax.cw, ax.closing = 0, True
        did.append(f"종료 순서 중 {e}")
    return did


def _leg_stats(trace, t0: int, t1: int) -> dict:
    """이 구간의 최대 추종 오차와 최고 속도(20 ms 창의 위치 변화로 잰 mm/s)."""
    rows = [r for r in trace if t0 <= r[0] <= t1]
    if len(rows) < 11:
        return {}
    vmax = max(abs(b[6] - a[6]) / ((b[0] - a[0]) / 1e9) for a, b in zip(rows, rows[10:]))
    return {"ferr_max": max(abs(r[7]) for r in rows), "speed_max_mm_s": round(vmax / safety.PULSE_PER_MM, 2),
            "ms": round((t1 - t0) / 1e6)}
