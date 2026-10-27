"""M2 안전 판정 — 순수 함수라 장비 없이 시험한다.

수치는 승인 파일(dynamic-bench/dev/m2-approval.json)에서만 온다. 코드에 있는 것은 '넘으면 거부'하는 절대 상한뿐이다.
이 파일은 승인 파일과 입회 표지(cell-bench/data/motion_attended)를 읽기만 한다 — 쓰거나 고치는 코드는 없다.
"""
from __future__ import annotations

import json
import math
from datetime import datetime

CAPS = {"distance_mm": 10, "window_mm": 15, "vmax_mm_s": 20, "amax_mm_s2": 100, "cycles": 5}   # M2 절대 상한
# M3 계단 — 단계마다 사람이 새로 승인하고, 앞 단계가 깨끗이 끝난 기록이 있어야 다음 단계 (2026-10-27 김건우)
M3_STAGES = {
    1: {"distance_mm": 60, "window_mm": 75, "vmax_mm_s": 20, "amax_mm_s2": 100, "cycles": 3},
    2: {"distance_mm": 150, "window_mm": 165, "vmax_mm_s": 50, "amax_mm_s2": 250, "cycles": 3},
    3: {"distance_mm": 300, "window_mm": 315, "vmax_mm_s": 100, "amax_mm_s2": 500, "cycles": 3},
}
FIELDS = ("approved_by", "approved_at", "first_move_dir", *CAPS)
PULSE_PER_MM = 1000       # 1 pulse = 1 µm (dynamic-bench/README.md 스케일). units_problem 이 통과해야만 이 환산을 믿는다
FERR_MAX = 2000           # 추종 오차 0x60F4 문턱 2 mm [제안값]
LATE_MS = 50              # 주기 간격이 이보다 길면 Quick stop
FRESH_S, MAX_S = 3600, 4 * 3600   # 표지: created 60분 안 · until 은 created 로부터 4시간 안
SERIAL = 0x25010217       # 0x1018:04 — M1 기록 2.3 · 명판 시리얼 25010217
QS_OPTION = 2             # 0x605A — Quick stop 경사로로 멈춘 뒤 Switch on disabled (M1 기록 2.3 에서 읽은 값)
# 전자 기어 — PANATERM 객체 덤프(2025-04-25)의 값과 같아야 위치 단위 = 엔코더 1 pulse, 0x6081 = pulse/s, 0x6083 · 0x6084 = pulse/s²
GEAR = {"0x608F:01": 1_000_000, "0x608F:02": 1000, "0x6091:01": 1000, "0x6091:02": 1000,
        "0x6092:01": 1_000_000, "0x6092:02": 1000}
FAULT_BIT = 0x0008


def read_json(path):
    """→ (dict, None) 또는 (None, 이유). 없거나 깨졌으면 이유 — 못 읽으면 무효다(cell-bench pause_state 의 '못 읽어도 유효'를 쓰지 않는다)."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return None, "파일 없음"
    except Exception as e:
        return None, f"못 읽음 {type(e).__name__}: {e}"
    return (doc, None) if isinstance(doc, dict) else (None, "JSON 객체가 아니다")


def _num(x) -> bool:
    return type(x) in (int, float) and math.isfinite(x)        # bool 은 int 의 하위형이라 type 으로 거른다


def approval_problem(a: dict, caps: dict = CAPS) -> str | None:
    missing = [k for k in ("approved_by", "approved_at", "first_move_dir", *caps) if k not in a]
    if missing:
        return f"필드 빠짐 {missing}"
    if not isinstance(a["approved_by"], str) or not a["approved_by"].strip():
        return "approved_by 가 비었다"
    try:
        if datetime.fromisoformat(a["approved_at"]).utcoffset() is None:
            return "approved_at 에 시간대가 없다"
    except (TypeError, ValueError):
        return f"approved_at {a['approved_at']!r} 가 ISO8601 이 아니다"
    if type(a["first_move_dir"]) is not int or a["first_move_dir"] not in (1, -1):
        return f"first_move_dir {a['first_move_dir']!r} — 1 또는 -1 이어야 한다"
    for k, cap in caps.items():
        x = a[k]
        if not _num(x) or x <= 0:
            return f"{k} = {x!r} — 양수가 아니다"
        if x > cap:
            return f"{k} = {x} 가 절대 상한 {cap} 을 넘는다"
    if a["cycles"] != int(a["cycles"]):
        return f"cycles = {a['cycles']} 가 정수가 아니다"
    if a["distance_mm"] >= a["window_mm"]:
        return f"distance {a['distance_mm']} mm 가 창 {a['window_mm']} mm 안이 아니다"
    return None


def m3_approval_problem(a: dict, passed: set) -> str | None:
    """M3 승인 — stage 가 1 · 2 · 3 중 하나이고, 그 단계 상한 안이고, 2 · 3 단계는 앞 단계가 passed 에 있어야 한다."""
    st = a.get("stage") if isinstance(a, dict) else None
    if type(st) is not int or st not in M3_STAGES:
        return f"stage {st!r} — 1 · 2 · 3 중 하나여야 한다"
    if st > 1 and st - 1 not in passed:
        return f"{st}단계는 {st - 1}단계 통과 뒤에만 돈다 — {st - 1}단계 통과 기록이 없다"
    return approval_problem(a, M3_STAGES[st])


def m3_passed(results: list, done: str) -> set:
    """결과 기록들 → 깨끗이 끝난 M3 단계 번호들 (끝난 이유가 done 이고 알람이 하나도 없던 m3 실행만)."""
    out = set()
    for r in results:
        ap = r.get("approval") if isinstance(r, dict) else None
        if r.get("mode") == "m3" and r.get("exit") == done and not r.get("alarms", ["없음"])                 and isinstance(ap, dict) and type(ap.get("stage")) is int:
            out.add(ap["stage"])
    return out


def load_m3_approval(path, passed: set):
    """→ (M3 승인 dict, None) 또는 (None, 거부 이유)."""
    a, why = read_json(path)
    why = why or m3_approval_problem(a, passed)
    return (None, f"승인 파일 거부 — {why}") if why else (a, None)


def load_approval(path):
    """→ (승인 dict, None) 또는 (None, 거부 이유)."""
    a, why = read_json(path)
    why = why or approval_problem(a)
    return (None, f"승인 파일 거부 — {why}") if why else (a, None)


def marker_problem(m: dict, now: float) -> str | None:
    by, c, u = m.get("by"), m.get("created"), m.get("until")
    if not isinstance(by, str) or not by.strip():
        return "by 가 비었다"
    if not (_num(c) and _num(u)):
        return "created · until 이 숫자가 아니다"
    if not u > now:
        return "until 이 지났다"
    if not 0 < u - c <= MAX_S:
        return "until 이 created 로부터 4시간 안이 아니다"
    if not 0 <= now - c <= FRESH_S:
        return f"created 가 60분 안이 아니다 ({(now - c) / 60:.0f}분 전)"
    return None


def check_marker(path, now: float):
    """입회 표지 → (무효 이유 또는 None, 유효 마감 epoch). 무효면 마감은 0 — 주기 스레드가 그 자리에서 Quick stop 한다."""
    m, why = read_json(path)
    why = why or marker_problem(m, now)
    return (why, 0.0) if why else (None, min(m["until"], m["created"] + FRESH_S))


def units_problem(gear: dict) -> str | None:
    bad = {k: gear.get(k) for k, want in GEAR.items() if gear.get(k) != want}
    return f"전자 기어가 근거 값과 다르다 {bad} — 0x6081 · 0x6083 · 0x6084 단위를 확신할 수 없다" if bad else None


def speed_problem(vmax_mm_s: float, overspeed_mm_s) -> str | None:
    """Pr5.13 과속도 레벨(0x3513, 리니어라 mm/s) 아래에서만 움직인다. 0 이면 '최대 속도 × 1.2' 라 값을 모르니 거부."""
    if not _num(overspeed_mm_s) or overspeed_mm_s <= 0:
        return f"Pr5.13 을 확인하지 못했다 ({overspeed_mm_s!r})"
    if not vmax_mm_s < overspeed_mm_s:
        return f"vmax {vmax_mm_s} mm/s 가 Pr5.13 과속도 레벨 {overspeed_mm_s} mm/s 아래가 아니다"
    return None


def target_problem(p0: int, target: int, window: int, v: float, a_qs: float) -> str | None:
    """목표 + Quick stop 정지 거리(v²/2a)가 창 p0 ± window 안이어야 보낸다. 밖이면 보내지 않는다(전송 없이 거부)."""
    if not _num(a_qs) or a_qs <= 0:
        return f"Quick stop 감속 {a_qs!r} — 정지 거리를 셀 수 없다"
    reach = abs(target - p0) + v * v / (2 * a_qs)
    if reach > window:
        return f"목표 {target} 가 창 {p0} ± {window} 밖이다 (정지 거리 포함 {reach:.0f} pulse)"
    return None


def start_problem(slaves: int, serial, err: int, sw: int, qs_option) -> str | None:
    """M1 상태 검사 — 하나라도 어긋나면 서보를 켜지 않는다. fault 면 Fault reset 하지 않고 기록 후 끝낸다."""
    if slaves != 1:
        return f"슬레이브 {slaves}대 — 1대여야 한다"
    if serial != SERIAL:
        return f"시리얼 {serial!r} — M1 기록값 0x{SERIAL:08X} 과 다르다"
    if err:
        return f"0x603F = 0x{err:04X} (알람) — Fault reset 하지 않는다"
    if sw & FAULT_BIT:
        return f"fault 비트 (statusword 0x{sw:04X}) — Fault reset 하지 않는다"
    if qs_option != QS_OPTION:
        return f"0x605A = {qs_option!r} — 2 가 아니면 Quick stop 뒤 상태를 확신할 수 없다"
    return None


def auto_qs(v, gap_ms, p0: int, window: int, deadline: float, now: float, ferr_max: int = FERR_MAX) -> str | None:
    """매 주기 자동 Quick stop 규칙(지시문 안전 규칙 4). WKC 3주기 연속 불일치는 주기 스레드가 따로 센다."""
    if gap_ms is not None and gap_ms > LATE_MS:
        return f"주기 지연 {gap_ms:.1f} ms > {LATE_MS} ms"
    if v.sw & FAULT_BIT:
        return f"fault 비트 (statusword 0x{v.sw:04X})"
    if v.err:
        return f"0x603F = 0x{v.err:04X}"
    if abs(v.ferr) > ferr_max:
        return f"추종 오차 {v.ferr} pulse > {ferr_max}"
    if abs(v.pos - p0) > window:
        return f"실제 위치 {v.pos} 가 창 {p0} ± {window} 밖"
    if now >= deadline:
        return "입회 표지 무효 (만료)"
    return None
