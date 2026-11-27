"""레일 사람 정지 — 정지 표지 · 생명줄 · 정지 확인 · 감속 시간 불변 조건. 판정은 순수 함수라 장비 없이 시험한다.

왜 있나 (2026-11-25 18:28 아차 사고): 사용자가 "멈춰"라고 해 실행기를 강제로 껐는데, 모터 프로그램(run_motion.py long)은
하위 프로세스라 살아남아 1,000 mm/s 로 한 번 더 왕복했다. 그 사이 사용자가 독을 레일에서 떼었다. 빠진 것은 셋이었다 —
정지 수단, 모터 기록으로 확인한 뒤에만 "멈춤"이라 말하기, 멈춘 뒤 다음 운전을 막기. 설계: 팀 제출서
submissions/2026-11-26_17_rail-safe-stop.md (QA 판정서 17 통과, 사용자 확정 D1 ~ D5 추천안).

정지 표지(stop.json)는 PC 하나의 자리(stop_dir — 드라이브 잠금과 같은 폴더)에 둔다. 있으면 정지다 — 깨졌거나 못 읽어도 정지다.
모터 프로그램은 감시 스레드가 POLL_S 마다 표지를 보고, 보면 Quick stop 걸쇠를 건다(run_motion.StopWatch). 표지는 걸쇠다 —
현장 화면(tools/rail_stop_gui.py)에서 '정지 풀기'를 할 때까지 남아 다음 운전을 막는다. 원격으로 풀지 않는다(D2).
생명줄: 실행기가 모터 프로그램을 띄울 때 환경 변수 RAIL_LIFELINE 에 자기 pid:생성시각 을 더해 넘긴다. 모터 프로그램은
그 사슬의 프로세스가 하나라도 사라지면 표지를 '생명줄 — 실행기 꺼짐' 으로 남기고 같은 길로 멈춘다(D1).
프로세스를 끄는 것은 정지 수단이 아니다 — Windows 의 venv 는 런처와 실제 인터프리터 두 프로세스라 런처만 끄면 실행기가 산다.
정지는 늘 tools/rail_stop.py · 레일 옆 '레일 멈춤' 창이다.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime
from pathlib import Path

STOP, ACK, WATCH, HISTORY = "stop.json", "stop_ack.json", "stop_watch.json", "stop_history"
POLL_S = 0.02            # 감시 스레드가 표지를 보는 간격 — 표지 생김 → 0x02 나감 목표 25 ms
LIFELINE_S = 0.2         # 생명줄(실행기 프로세스)을 보는 간격
BEAT_STALE_S = 0.2       # 감시 스레드 박동이 이보다 묵으면 '정지 감시 멈춤' Quick stop (QA 조건 1)
HOLD_S = 0.2             # 사람 정지일 때 끝 순서 첫 단계(서보 꺼짐) 뒤 입력을 받으며 머무는 시간 — 정지 확인 창
STILL_UM = 50            # 머문 동안 위치 최대 − 최소가 이 안이면 '움직임 없음'
ACK_WAIT_S = 3.0         # 정지 명령이 확인 파일을 기다리는 최대 시간
STOP_TIME_MAX_S = 1.5    # 사람 정지 감속 시간(최고 속도 ÷ 0x6085) 상한 — m2.finish 의 2.0초 기다림 안 (QA 조건 4)
ENV_LIFELINE = "RAIL_LIFELINE"
HUMAN = "사람 정지"        # 걸쇠 이유의 머리 — 끝난 이유 · 결과 JSON 에서 이 말로 찾는다
WATCH_DEAD = "정지 감시 멈춤"
CONFIRMED = "멈춤 확인"


def stop_dir() -> Path:
    """정지 표지 자리 — PC 하나. RAIL_LOCK_DIR 은 시험(RAIL_NO_HARDWARE)일 때만 받는다(QA 조건 3). drive_lock.lock_dir 은 그대로다."""
    if os.environ.get("RAIL_NO_HARDWARE") and os.environ.get("RAIL_LOCK_DIR"):
        return Path(os.environ["RAIL_LOCK_DIR"])
    base = os.environ.get("PROGRAMDATA") or (r"C:\ProgramData" if os.name == "nt" else "/tmp")
    return Path(base) / "QA-Automation-Test-Room" / "rail-lock"


def env_ignored() -> str | None:
    """실제 운전에서 RAIL_LOCK_DIR 이 주어졌으면 무시했다는 한 줄 — 모터 프로그램 · 정지 화면이 남긴다."""
    if os.environ.get("RAIL_LOCK_DIR") and not os.environ.get("RAIL_NO_HARDWARE"):
        return f"RAIL_LOCK_DIR({os.environ['RAIL_LOCK_DIR']})은 시험 때만 쓴다 — 정지 표지는 {stop_dir()} 에서 본다"
    return None


def _ts(t: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if t is None else t).strftime("%Y-%m-%d %H:%M:%S")


def _write_atomic(path: Path, doc: dict) -> None:
    """같은 폴더 임시 파일에 쓰고 이름 바꾸기. 실패하면 OSError 그대로 — 부르는 쪽이 크게 알린다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(3)}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8-sig"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- 정지 표지

def active(d: Path | None = None) -> dict | None:
    """지금 정지 표지가 있으면 그 내용(깨졌으면 broken = True), 없으면 None. 있으면 정지다."""
    p = (d or stop_dir()) / STOP
    if not p.exists():
        return None
    doc = _read(p)
    return doc if doc is not None else {"id": None, "broken": True, "by": "?", "why": "정지 표지 파일을 읽지 못함(깨짐) — 정지로 본다", "at": "?"}


def request(by: str, why: str = "사람 요청", d: Path | None = None) -> dict:
    """정지 표지를 만든다 → 표지 내용. 이미 있으면 덮지 않고 그것을 돌려준다(existing = True) — 처음 요청이 남는다.
    만들지 못하면 OSError 가 그대로 나간다 — 부르는 쪽이 '정지 요청 실패 — 드라이브 전원 스위치'를 크게 띄운다(QA 조건 2)."""
    d = d or stop_dir()
    now = active(d)
    if now is not None:
        return {**now, "existing": True}
    doc = {"id": secrets.token_hex(8), "t": time.time(), "at": _ts(), "by": by, "why": why, "pid": os.getpid()}
    _write_atomic(d / STOP, doc)
    return doc


def clear(by: str, d: Path | None = None) -> Path | None:
    """정지 풀기 — 표지를 stop_history/<id>.json 으로 옮기고 cleared_by · cleared_at 을 더한다. 표지가 없으면 None.
    현장 화면만 부른다(D2). 모터 프로그램이 도는지는 부르는 쪽이 먼저 본다."""
    d = d or stop_dir()
    p = d / STOP
    if not p.exists():
        return None
    doc = _read(p) or {"broken": True}
    doc.update(cleared_by=by, cleared_at=_ts())
    name = doc.get("id") or f"broken-{int(time.time())}"
    dst = d / HISTORY / f"{name}.json"
    _write_atomic(dst, doc)
    p.unlink()
    return dst


def refuse_line(st: dict) -> str:
    """정지 표지가 있을 때 운전을 거부하는 한 줄."""
    return (f"정지 표지가 있다 — {st.get('at', '?')} {st.get('by', '?')} · {st.get('why', '?')}. "
            "레일 옆 '레일 멈춤' 창에서 정지 풀기 뒤에 다시 띄운다")


# ---------------------------------------------------------------- 생명줄

def lifeline_env(env: dict | None = None, created=None) -> dict:
    """자식(모터 프로그램)에게 넘길 환경 — 물려받은 사슬 뒤에 이 프로세스(실제 인터프리터, os.getpid())와 생성 시각을 붙인다."""
    if created is None:
        from cellbench.proc import created
    env = dict(os.environ if env is None else env)
    me = os.getpid()
    c = created(me) or 0.0
    chain = [x for x in env.get(ENV_LIFELINE, "").split(";") if x]
    if not any(x.split(":")[0] == str(me) for x in chain):
        chain.append(f"{me}:{c:.3f}")
    env[ENV_LIFELINE] = ";".join(chain)
    return env


def lifeline_parse(s: str | None) -> list[tuple[int, float]]:
    out = []
    for x in (s or "").split(";"):
        try:
            pid, c = x.split(":")
            out.append((int(pid), float(c)))
        except ValueError:
            continue
    return out


def lifeline_broken(chain: list[tuple[int, float]], created=None) -> str | None:
    """사슬의 프로세스가 사라졌으면(또는 같은 pid 를 다른 프로세스가 받았으면) 그 한 줄, 다 살아 있으면 None.
    생성 시각을 권한 때문에 못 읽으면(0.0) 살아 있는 것으로 본다 — 누군지는 모르지만 사라진 것은 아니다."""
    if created is None:
        from cellbench.proc import created
    for pid, c in chain:
        now = created(pid)
        if now is None:
            return f"생명줄 — 실행기 꺼짐 (pid {pid})"
        if now and c and abs(now - c) > 1.0:
            return f"생명줄 — 실행기 꺼짐 (pid {pid} 를 다른 프로세스가 받음)"
    return None


# ---------------------------------------------------------------- 감속 시간 불변 조건 (QA 조건 4)

def stop_time_problem(v_pps: float, a_qs_pps2: float) -> str | None:
    """사람 정지 감속 시간 = 최고 속도 ÷ 0x6085 가 STOP_TIME_MAX_S 이상이면 이유. m2.finish 는 Quick stop 뒤 2.0초만 기다리고,
    넘으면 Controlword 0 으로 빠져 자유 정지(코스팅)가 된다."""
    if not a_qs_pps2 or a_qs_pps2 <= 0:
        return f"Quick stop 감속 {a_qs_pps2!r} — 사람 정지 감속 시간을 셀 수 없다"
    t = abs(v_pps) / a_qs_pps2
    if t >= STOP_TIME_MAX_S:
        return f"사람 정지 감속 시간 {t:.2f}초 ≥ {STOP_TIME_MAX_S}초 (최고 속도 {v_pps / 1000:g} mm/s ÷ 0x6085 {a_qs_pps2 / 1000:g} mm/s²)"
    return None


# ---------------------------------------------------------------- 정지 확인

def still_span(trace_rows, t0_ns: int, t1_ns: int) -> tuple[float, int | None]:
    """trace(m2.Axis.trace: t_mono_ns, cw, target, sw, err, mode, pos, ferr) 의 [t0, t1] 구간 → (그 구간 길이 ms, 위치 최대 − 최소)."""
    import bisect
    rows = trace_rows[bisect.bisect_left(trace_rows, t0_ns, key=lambda r: r[0]):bisect.bisect_right(trace_rows, t1_ns, key=lambda r: r[0])]
    if len(rows) < 2:
        return 0.0, None
    pos = [r[6] for r in rows]
    return (rows[-1][0] - rows[0][0]) / 1e6, max(pos) - min(pos)


def judge_ack(ack: dict) -> tuple[bool, str]:
    """정지 확인 파일 → (멈춤 확인인가, 한 줄). 조건 셋 모두: ① 서보 꺼짐(Switch on disabled 또는 Fault) · 입력이 살아 있음
    ② HOLD_S 이상 머문 동안 위치 변화 STILL_UM 이하 ③ EtherCAT SAFE-OP 이하(보조 — QA 판정서 17)."""
    st, stale = ack.get("state"), ack.get("stale")
    if st not in ("SWITCH_ON_DISABLED", "FAULT"):
        return False, f"서보가 꺼졌다는 입력이 없다(상태 {st})"
    if stale:
        return False, "마지막 입력이 묵었다(통신 두절 · 주기 스레드 멈춤)"
    ms, span = ack.get("still_ms") or 0, ack.get("still_span_um")
    if ms < HOLD_S * 1000 * 0.9 or span is None:
        return False, f"머문 입력이 모자란다({ms:.0f} ms)"
    if span > STILL_UM:
        return False, f"머문 {ms:.0f} ms 동안 {span / 1000:.3f} mm 움직였다"
    if not ack.get("safe_op"):
        return False, "EtherCAT 을 SAFE-OP 로 내리지 못했다"
    alarm = f" · 드라이브 알람 {ack.get('err')}" if ack.get("err") not in (None, "0x0000") else ""
    return True, f"서보 꺼짐 · {ms / 1000:.1f}초 동안 움직임 {span / 1000:.3f} mm 이내{alarm}"


def write_ack(ack: dict, d: Path | None = None) -> None:
    _write_atomic((d or stop_dir()) / ACK, ack)


def read_ack(d: Path | None = None) -> dict | None:
    return _read((d or stop_dir()) / ACK)


def write_watch(pid: int, d: Path | None = None) -> Path:
    """모터 프로그램이 시작 때 '나는 이 자리를 본다'를 남긴다 — 정지 화면이 자기 자리와 같은지 견준다(QA 조건 3)."""
    d = d or stop_dir()
    _write_atomic(d / WATCH, {"pid": pid, "stop_dir": str(d), "since": time.time(), "at": _ts()})
    return d


def watch_matches(holder_pid: int | None, d: Path | None = None) -> tuple[bool | None, str]:
    """정지 화면 둘째 상태 줄 — 드라이브를 쥔 모터 프로그램이 이 화면과 같은 자리를 보나. (같음 True · 다름 False · 모름 None, 한 줄)."""
    d = d or stop_dir()
    w = _read(d / WATCH)
    if holder_pid is None:
        return None, f"정지 표지 자리: {d} (운전 중인 모터 프로그램 없음)"
    if not w or w.get("pid") != holder_pid:
        return False, f"정지 표지 자리가 다르다 — 이 화면으로는 멈추지 못한다. 드라이브 전원 스위치 (화면 {d})"
    if Path(w.get("stop_dir", "")) != d:
        return False, f"정지 표지 자리가 다르다(모터 {w.get('stop_dir')} · 화면 {d}) — 드라이브 전원 스위치"
    return True, "정지 표지 자리: 모터 프로그램과 같음"


def wait_confirm(req: dict, motor_running, d: Path | None = None, wait_s: float = ACK_WAIT_S, sleep=time.sleep) -> tuple[int, str]:
    """정지 요청 뒤 확인 → (코드, 한 줄). 0 = 멈춤 확인 · 3 = 확인 못 함 · 0 은 확인 파일을 읽은 뒤에만.
    motor_running() → (도는가, 쥔 쪽 한 줄). 요청 때 모터 프로그램이 없으면 다음 운전을 막은 것만 알린다."""
    d = d or stop_dir()
    running, who = motor_running()
    if not running:
        return 0, "운전 중인 모터 프로그램 없음 — 정지 표지로 다음 운전을 막았다"
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        ack = read_ack(d)
        if ack and ack.get("id") and ack.get("id") == req.get("id"):
            ok, line = judge_ack(ack)
            at = ack.get("stopped_at") or "?"
            if ok:
                return 0, f"{CONFIRMED} {at} — {line}"
            return 3, f"멈춤 확인 못 함 — {line}. 레일에 손대지 마세요. 드라이브 전원 스위치를 끄세요"
        if not motor_running()[0]:       # 확인 파일 없이 끝났다 — 정지 요청을 못 본 채 끝났을 수 있다(옛 코드 · 끝나던 참)
            return 3, ("멈춤 확인 못 함 — 모터 프로그램이 끝났지만 정지 확인 파일이 없다. "
                       "레일에 손대기 전에 운전 기록(motion_*.json)의 끝 상태를 본다")
        sleep(0.05)
    return 3, (f"멈춤 확인 못 함 — 모터 프로그램이 {wait_s:g}초 안에 답하지 않는다({who}). "
               "레일에 손대지 마세요. 드라이브 전원 스위치를 끄세요(끈 뒤에는 home 이 필요)")
