"""셀별 비교 — 셀 하나가 모터(정답)의 거리 · 속도 · 가속도 · 움직임을 어떻게 읽었나. 순수 함수, 표준 라이브러리만.

정답은 모터다(M3 · 오래 왕복 1차로 검증). 앱 방식은 결과판 세션의 Live 앱 1.14.0 대조표를 따른다:
거리 = 속도 이력 × 0.1초의 합(무효 표시 0xFFFD~F 는 0, Fundamental.swift), 가속도 = 속도 이력에 11칸 3차 Savitzky–Golay 1차 미분.
움직임 판정은 셀 펌웨어가 한다(IMU 흔들림 > 문턱 7 이면 속도 이력에 값, 아니면 0 — gps.c is_moving). 그래서 '이력 > 0' 을 판정이 켜진 것으로 본다.
"""
from __future__ import annotations

import math
import statistics

from .align import NO_VALUE, interp

APP_DT = 0.1                                   # 속도 이력 간격 (10 Hz)
SG = [k / 110 for k in range(-5, 6)]           # 11칸 · 2~3차 Savitzky–Golay 1차 미분 계수 (Σk² = 110)
SETTLE_S = 2.0                                 # 멈춘 뒤 이만큼 지난 때부터를 '멈춤 중'으로 센다
LAG_MAX_S = 5.0                                # 켜짐 · 꺼짐 늦음을 이만큼까지만 찾는다


def dwells(t, p, v_min: float = 1.0, min_s: float = 1.0, dt: float = 0.1) -> list[tuple[float, float, float]]:
    """모터가 min_s 초 넘게 멈춘 구간 [(시작, 끝, 위치 mm)] — |속도| < v_min mm/s (dt 격자, 앞뒤 dt/2 차분)."""
    out, cur = [], []
    for i in range(int((t[-1] - t[0]) / dt) + 1):
        x = t[0] + i * dt
        v = (interp(t, p, x + dt / 2) - interp(t, p, x - dt / 2)) / dt
        if abs(v) < v_min:
            cur.append(x)
            continue
        if cur and cur[-1] - cur[0] >= min_s:
            out.append((cur[0], cur[-1], statistics.fmean(interp(t, p, y) for y in cur)))
        cur = []
    if cur and cur[-1] - cur[0] >= min_s:
        out.append((cur[0], cur[-1], statistics.fmean(interp(t, p, y) for y in cur)))
    return out


def leg_list(dw, min_mm: float = 100.0) -> list[tuple[tuple, tuple]]:
    """이어진 두 멈춤 사이에서 min_mm 넘게 움직인 것 — 한 번 가기."""
    return [(a, b) for a, b in zip(dw, dw[1:]) if abs(b[2] - a[2]) > min_mm]


def _mean_xy(ct, cx, cy, a: float, b: float):
    z = [(x, y) for c, x, y in zip(ct, cx, cy) if a <= c <= b]
    return (statistics.fmean(x for x, _ in z), statistics.fmean(y for _, y in z)) if z else None


def leg_vectors(ct, cx, cy, legs, edge: float = 1.0) -> list[tuple[float, float, float]]:
    """한 번 가기마다 (셀 Δx, 셀 Δy, 모터 Δ) — 셀 위치는 멈춘 구간(앞 edge 초 뺌)의 평균끼리."""
    out = []
    for (a0, a1, pa), (b0, b1, pb) in legs:
        sa, sb = _mean_xy(ct, cx, cy, a0 + edge, a1), _mean_xy(ct, cx, cy, b0 + edge, b1)
        if sa and sb:
            out.append((sb[0] - sa[0], sb[1] - sa[1], pb - pa))
    return out


def pooled_bearing(per_cell: list[list[tuple[float, float, float]]]) -> float | None:
    """셀마다 '모터 방향으로 맞춘 평균 이동 벡터'를 더한 방위 — 레일 방위(라디안, 동쪽에서 반시계).
    벡터 그대로 더한다(길이로 무게): 움직임을 못 보는 셀은 평균 벡터가 짧아 방위를 흔들지 못한다. 방위만 평균하면 한 대가 반대로 가리켜도 상쇄된다."""
    sx = sy = 0.0
    for vs in per_cell:
        if vs:
            sx += statistics.fmean(dx * math.copysign(1, m) for dx, _, m in vs)
            sy += statistics.fmean(dy * math.copysign(1, m) for _, dy, m in vs)
    return None if sx == sy == 0 else math.atan2(sy, sx)


def position_legs(ct, cx, cy, legs, bearing: float, edge: float = 1.0) -> list[tuple[float, float]]:
    """한 번 가기마다 (셀이 레일 방향으로 읽은 이동 mm, 모터 이동 mm) — 둘 다 부호 있음."""
    c, s = math.cos(bearing), math.sin(bearing)
    return [(dx * c + dy * s, m) for dx, dy, m in leg_vectors(ct, cx, cy, legs, edge)]


def clean(vs) -> list[float]:
    """앱처럼 무효 표시(0xFFFD~F)를 0 으로."""
    return [0 if v >= NO_VALUE else v for v in vs]


def app_distance(ts, vs, a: float, b: float) -> float:
    """앱 방식 거리 — [a, b] 안의 속도 이력 × 0.1초의 합 (무효 표시는 0)."""
    return sum(v for x, v in zip(ts, clean(vs)) if a <= x <= b) * APP_DT


def sg_accel(vs, dt: float = APP_DT) -> list[float | None]:
    """앱 방식 가속도 — 11칸 Savitzky–Golay 1차 미분 (mm/s²). 창이 다 차지 않는 양 끝 5칸은 None."""
    n, h = len(vs), len(SG) // 2
    return [None if i < h or i >= n - h else sum(c * vs[i + k - h] for k, c in enumerate(SG)) / dt for i in range(n)]


def flag_stats(ts, vs, dw, settle: float = SETTLE_S) -> dict:
    """움직임 판정(이력 > 0) — 움직이는 동안 켜진 비율, 오래 멈춘 동안 켜진 비율, 출발 뒤 켜지기까지 · 멈춘 뒤 꺼지기까지(중앙값 초)."""
    on = [0 < v < NO_VALUE for v in vs]

    def pct(spans):
        z = [f for x, f in zip(ts, on) if any(a <= x <= b for a, b in spans)]
        return 100.0 * sum(z) / len(z) if z else None

    def first(t0, want):
        for x, f in zip(ts, on):
            if t0 <= x <= t0 + LAG_MAX_S and f == want:
                return x - t0
        return None
    moves = [(a[1], b[0]) for a, b in zip(dw, dw[1:])]
    long_dw = [d for d in dw[1:] if d[1] - d[0] >= settle + 0.5]
    lag_on = [x for x in (first(b, True) for (_, b, _) in dw[:-1]) if x is not None]
    lag_off = [x for x in (first(d[0], False) for d in long_dw) if x is not None]
    return {"on_moving_pct": pct([(a + 1.0, b) for a, b in moves]), "on_dwell_pct": pct([(d[0] + settle, d[1]) for d in long_dw]),
            "lag_on_s": statistics.median(lag_on) if lag_on else None, "lag_off_s": statistics.median(lag_off) if lag_off else None}
