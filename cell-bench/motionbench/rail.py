"""레일 기준 — 순수 함수 (pysoem · 시계 · 파일 없음). 기획 dynamic-bench/design/rail-recovery.md.

드라이브는 인크리멘털이라 전원을 넣은 자리가 0 이다. 그래서 이 프로그램은 그 0 을 믿지 않고, 끝에 닿아 잡은 양 끝
(레일 파일 cell-bench/data/rail-motor1.json)만 믿는다. 위치는 전부 이번 전원의 pulse(1 pulse = 1 µm).

같은 전원인가: 드라이브 0x607D 가 레일 파일의 limits 와 같을 때만. 0x607D 는 RAM 이라 전원이 꺼지면 기본값으로
돌아간다(실측 2026-10-27 13:53 → 15:56). 인크리멘털 모드에서는 원점 복귀 없이 이 값이 보호로 켜지지 않으므로
(매뉴얼 SX-DSV03242 PDF 107쪽) 표시로만 쓴다. 0x1010 저장은 하지 않는다 — 하면 이 표시가 죽는다.
"""
from __future__ import annotations

from datetime import datetime

LENGTH = 1_815_913      # 지그만 실었을 때 양 끝 차이 (2026-10-27 19:28 · 19:32 끝 터치, 각 3회 차이 0)
MARGIN = 20_000         # 모든 목표는 양 끝에서 20 mm 안쪽 (2026-10-28 김건우 — 100 mm/s 급정지 5 mm 보다 넉넉)
FRESH_MS = 500          # 첫 서보 온이 이보다 오래 걸리면 전원 재투입 뒤로 본다 (실측 1,310~1,551 ms · 평소 52~58 ms)
TOL = 2_000             # 빠른 확인 · 길이 확인 허용 오차 2 mm
SEARCH_EXTRA = 30_000   # 끝을 찾으러 갈 때 예상 거리보다 더 가 보는 거리
HOME_CAPS = {"creep_mm_s": 10, "torque_permil": 500, "back_mm": 20}
HOME_FERR_UM = (100, 1999)   # 자동 Quick stop 문턱(safety.FERR_MAX 2,000) 아래 — 같으면 닿음과 알람이 함께 걸린다
GOTO_CAPS = {"vmax_mm_s": 100, "amax_mm_s2": 500}   # M3 3단계에서 실측 통과한 값까지
LONG_CAPS = {"vmax_mm_s": 500, "amax_mm_s2": 500, "minutes": 30}   # 오래 왕복 — 기획 long-run.md (입회 표지는 60분 안에만 유효)
# vmax 500 mm/s (2026-10-28 김건우 승인 — 셀이 속도를 구별하는 지점 찾기): 급정지 거리 v²/2a(0x6085 = 1,000 mm/s² 실측)가 125 mm 로
# 양 끝 여유 안이고, 추종 오차 문턱은 safety.ferr_limit 가 속도에 맞춘다. 모터 최대 2,000 mm/s(E-RAM8-S, README 1절)
LONG_SPEEDS_MAX, LONG_CYCLES_MAX = 6, 10   # 속도 계단(speeds_mm_s) — 속도 6개까지, 속도마다 왕복 10번까지
LONG_DWELL_MAX_S = 10    # 오래 왕복의 끝 멈춤(dwell_s, 없으면 0) 상한 — 2차부터: 셀 움직임 판정의 꺼짐과 멈춘 자리 평균을 재려고


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float("inf")


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _common(a, fields) -> str | None:
    if not isinstance(a, dict):
        return "JSON 객체가 아니다"
    missing = [k for k in ("approved_by", "approved_at", *fields) if k not in a]
    if missing:
        return f"필드 빠짐 {missing}"
    if not isinstance(a["approved_by"], str) or not a["approved_by"].strip():
        return "approved_by 가 비었다"
    try:
        if datetime.fromisoformat(a["approved_at"]).utcoffset() is None:
            return "approved_at 에 시간대가 없다"
    except (TypeError, ValueError):
        return f"approved_at {a['approved_at']!r} 가 ISO8601 이 아니다"
    return None


def _caps(a, caps) -> str | None:
    for k, cap in caps.items():
        x = a[k]
        if not _num(x) or x <= 0:
            return f"{k} = {x!r} — 양수가 아니다"
        if x > cap:
            return f"{k} = {x} 가 절대 상한 {cap} 을 넘는다"
    return None


def home_approval_problem(a) -> str | None:
    """home 승인 — 적재물 이름 · 탐색 속도 · 힘 상한 · 닿음 문턱 · 물러나는 거리."""
    why = _common(a, ("payload", "touch_ferr_um", *HOME_CAPS))
    if why:
        return why
    if not isinstance(a["payload"], str) or not a["payload"].strip():
        return "payload(적재물 이름)가 비었다"
    why = _caps(a, HOME_CAPS)
    if why:
        return why
    lo, hi = HOME_FERR_UM
    if not _num(a["touch_ferr_um"]) or not lo <= a["touch_ferr_um"] <= hi:
        return f"touch_ferr_um = {a['touch_ferr_um']!r} — {lo}~{hi} µm 이어야 한다"
    return None


def goto_approval_problem(a) -> str | None:
    why = _common(a, ("to_from_left_mm", *GOTO_CAPS))
    if why:
        return why
    if not _num(a["to_from_left_mm"]) or a["to_from_left_mm"] < 0:
        return f"to_from_left_mm = {a['to_from_left_mm']!r} — 0 이상이어야 한다"
    return _caps(a, GOTO_CAPS)


def long_approval_problem(a) -> str | None:
    """오래 왕복 승인 — 왼쪽 끝에서 잰 두 자리 A · B(1 mm 넘게 떨어져야), 속도 · 가속도 · 시간. 레일 한계는 실행 때 레일 파일로 본다."""
    why = _common(a, ("a_from_left_mm", "b_from_left_mm", *LONG_CAPS))
    if why:
        return why
    for k in ("a_from_left_mm", "b_from_left_mm"):
        if not _num(a[k]) or a[k] < 0:
            return f"{k} = {a[k]!r} — 0 이상의 숫자여야 한다"
    if abs(a["a_from_left_mm"] - a["b_from_left_mm"]) <= 1:
        return f"A {a['a_from_left_mm']} mm 와 B {a['b_from_left_mm']} mm 가 1 mm 넘게 떨어져 있어야 한다"
    dw = a.get("dwell_s", 0)
    if not _num(dw) or not 0 <= dw <= LONG_DWELL_MAX_S:
        return f"dwell_s = {dw!r} — 0 ~ {LONG_DWELL_MAX_S}초 숫자여야 한다"
    why = _caps(a, LONG_CAPS)
    if why or "speeds_mm_s" not in a:
        return why
    sp = a["speeds_mm_s"]                    # 속도 계단 — 한 번 실행에 올라가는 속도들(2026-10-28 김건우 "한번에 해줘")
    if not isinstance(sp, list) or not 1 <= len(sp) <= LONG_SPEEDS_MAX or not all(_num(v) and v > 0 for v in sp):
        return f"speeds_mm_s = {sp!r} — 양수 1~{LONG_SPEEDS_MAX}개 목록이어야 한다"
    if any(b <= x for x, b in zip(sp, sp[1:])):
        return f"speeds_mm_s = {sp} — 올라가기만 해야 한다(빨라지기 전에 추종 오차 문턱을 올리므로)"
    if max(sp) > a["vmax_mm_s"]:
        return f"speeds_mm_s 의 가장 빠른 {max(sp)} 가 승인 vmax_mm_s {a['vmax_mm_s']} 를 넘는다 — 사전 검사는 vmax 로 한다"
    c = a.get("cycles_per_speed")
    if not _int(c) or not 1 <= c <= LONG_CYCLES_MAX:
        return f"cycles_per_speed = {c!r} — 1~{LONG_CYCLES_MAX} 정수여야 한다"
    return None


def limits(plus_end: int, minus_end: int, margin: int = MARGIN) -> list[int]:
    return [minus_end + margin, plus_end - margin]


def make(plus_end: int, minus_end: int, payload: str, measured: list, lengths: dict, enable_ms, at: str,
         last_pos=None, method: str = "full") -> dict:
    """레일 파일 한 장. lengths = 적재물마다 그때 잰 양 끝 차이(전원이 바뀌어도 남긴다)."""
    return {"plus_end": plus_end, "minus_end": minus_end, "length": plus_end - minus_end, "payload": payload,
            "measured": list(measured), "lengths": dict(lengths), "limits": limits(plus_end, minus_end),
            "enable_ms": enable_ms, "at": at, "last_pos": last_pos, "method": method}


def problem(doc) -> str | None:
    """레일 파일이 쓸 만한가."""
    if not isinstance(doc, dict):
        return "레일 파일이 없다"
    p, m = doc.get("plus_end"), doc.get("minus_end")
    if not _int(p) or not _int(m):
        return f"plus_end {p!r} · minus_end {m!r} 가 정수가 아니다"
    if p <= m:
        return f"plus_end {p} 가 minus_end {m} 보다 커야 한다"
    lim = doc.get("limits")
    if not isinstance(lim, list) or len(lim) != 2 or lim != limits(p, m):
        return f"limits {lim!r} 가 양 끝에서 {MARGIN // 1000} mm 안쪽이 아니다"
    if not isinstance(doc.get("payload"), str) or not doc["payload"].strip():
        return "payload 가 비었다"
    if not isinstance(doc.get("lengths"), dict):
        return "lengths 가 없다"
    return None


def same_power(doc, drive_limits: tuple) -> bool:
    """드라이브 0x607D (min, max) 가 레일 파일의 limits 와 같으면 같은 전원 — 그 밖은 전부 home."""
    return problem(doc) is None and list(drive_limits) == doc["limits"]


def quick_plan(doc, now_pos: int, payload: str):
    """② 빠른 확인 계획 — 기억한 last_pos 에 지금도 있다고 보고 가까운 끝 쪽으로. 쓸 수 없으면 None(③ 전체)."""
    if problem(doc) or doc.get("payload") != payload or payload not in doc["lengths"] or not _int(doc.get("last_pos")):
        return None
    to_plus, to_minus = doc["plus_end"] - doc["last_pos"], doc["last_pos"] - doc["minus_end"]
    side, dist = (1, to_plus) if to_plus <= to_minus else (-1, to_minus)
    return {"side": side, "expect": now_pos + side * dist, "reach": dist + SEARCH_EXTRA}


def length_problem(measured: int, known: int) -> str | None:
    if abs(measured - known) > TOL:
        return (f"양 끝 길이 {measured / 1000:.3f} mm 가 같은 적재물의 길이 {known / 1000:.3f} mm 와 "
                f"{TOL // 1000} mm 넘게 다르다 — 끝이 아닌 것에 닿았을 수 있다")
    return None


def from_left(doc, mm, plus_is_left: bool) -> int:
    """왼쪽 끝에서 mm 떨어진 자리의 위치 (이번 전원의 pulse)."""
    d = round(mm * 1000)
    return doc["plus_end"] - d if plus_is_left else doc["minus_end"] + d


def target_problem(target: int, doc, stop: int = 0) -> str | None:
    """목표 + 급정지 거리(stop = v²/2a)가 레일 한계 안이어야 한다."""
    lo, hi = doc["limits"]
    if not lo + stop <= target <= hi - stop:
        return (f"목표 {target} 가 레일 한계 {lo} ~ {hi} 밖이다 (양 끝에서 {MARGIN // 1000} mm 안쪽만"
                + (f", 급정지 거리 {stop / 1000:g} mm 포함)" if stop else ")"))
    return None
