"""셀 vs 모터 — 위치 · 시계 · 패킷 손실. 순수 함수, 표준 라이브러리만. 기획 dynamic-bench/design/long-run.md 2-4.

위치: 셀 GPS 위치는 멈춰 있어도 10분에 표준편차 약 1.1 m 떠돈다(2026-10-28 실측). 그래서 왕복 한 번보다 느린 떠돎을 빼고(highpass),
2분 덩어리마다 '모터 1 mm 에 셀이 몇 mm 움직였나'(기울기 벡터)를 구해, 덩어리끼리 같은 방향 · 크기로 모일 때만 '보인다'로 판정한다.
상관 하나만 보면 한 방향 이동과 느린 떠돎이 우연히 닮아 높게 나온다(15:19 실행의 0.97 — 정지 기록도 0.94~0.98).
"""
from __future__ import annotations

import bisect
import math
import statistics
from itertools import accumulate

from .align import interp

M_PER_DEG = 111_320.0     # 위도 1도의 길이 (구 근사) — 수 m 안의 상대 위치에는 충분하다
BLOCK_S = 120.0           # 위치 덩어리 길이 (초)
SEEN_SIGMA = 3.0          # 평균 기울기가 표준오차의 이 배수를 넘어야 '보인다' [제안값]
MIN_BLOCKS = 6            # 덩어리가 이보다 적으면 판정하지 않는다 — 검토 실측: 떠돎만 있어도 3덩어리 5.7 % · 5덩어리 1.0 % 가 '보인다'


def to_mm(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """위도 · 경도 → 기준점에서 (동쪽 x, 북쪽 y) mm."""
    return ((lon - lon0) * M_PER_DEG * 1000 * math.cos(math.radians(lat0)), (lat - lat0) * M_PER_DEG * 1000)


def highpass(t, v, width: float) -> tuple[list[float], list[float]]:
    """가운데 맞춘 width 초 이동 평균을 뺀다 — 주기 width 의 왕복은 남고 그보다 느린 떠돎은 빠진다. 창이 다 차지 않는 양 끝은 버린다."""
    s = [0.0, *accumulate(v)]
    ht, hv = [], []
    for i, x in enumerate(t):
        if x - width / 2 < t[0] or x + width / 2 > t[-1]:
            continue
        j0, j1 = bisect.bisect_left(t, x - width / 2), bisect.bisect_right(t, x + width / 2)
        ht.append(x)
        hv.append(v[i] - (s[j1] - s[j0]) / (j1 - j0))
    return ht, hv


def motor_period(t, p, dt: float = 0.1, v_min: float = 1.0) -> float | None:
    """모터 왕복 주기(초) — 방향이 바뀐 시각들의 간격 중앙값 × 2. 방향 바뀜이 3번보다 적으면 None."""
    n = int((t[-1] - t[0]) / dt)
    turns, last = [], 0
    for i in range(1, n):
        x = t[0] + i * dt
        v = (interp(t, p, x + dt / 2) - interp(t, p, x - dt / 2)) / dt
        sign = 1 if v > v_min else -1 if v < -v_min else 0
        if sign and last and sign != last:
            turns.append(x)
        last = sign or last
    if len(turns) < 3:
        return None
    return 2 * statistics.median(b - a for a, b in zip(turns, turns[1:]))


def _cov(a, b):
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    return statistics.fmean((x - ma) * (y - mb) for x, y in zip(a, b))


def position_fit(ct, cx, cy, mt, mp, period: float, block_s: float = BLOCK_S, max_lag: float = 3.0, step: float = 0.1) -> dict:
    """셀 위치(ct 시각의 x · y mm)와 모터 위치(mt · mp mm) — 늦음(lag: 셀이 lag 초 늦게 보인다)을 찾고 덩어리마다 기울기를 구한다.
    → {lag_s, r2, blocks, k(셀 mm / 모터 mm), dir_deg(동쪽에서 반시계), se, seen(True · False · 덩어리 MIN_BLOCKS 개 미만이면 None)}."""
    ht, hx = highpass(ct, cx, period)
    _, hy = highpass(ct, cy, period)
    if len(ht) < 20:
        return {"lag_s": None, "r2": None, "blocks": 0, "k": None, "dir_deg": None, "se": None, "seen": None}
    vxy = statistics.pvariance(hx) + statistics.pvariance(hy)

    def motor_hp(lag):
        return highpass(ct, [interp(mt, mp, x - lag) for x in ct], period)[1]

    def r2(hm):
        vm = statistics.pvariance(hm)
        return 0.0 if vm == 0 or vxy == 0 else (_cov(hx, hm) ** 2 + _cov(hy, hm) ** 2) / (vm * vxy)
    lags = [round(-max_lag + i * step, 6) for i in range(int(round(2 * max_lag / step)) + 1)]
    best = max(((r2(hm), lag, hm) for lag in lags for hm in [motor_hp(lag)]), key=lambda z: z[0])
    score, lag, hm = best
    ks = []
    for b in range(int((ht[-1] - ht[0]) // block_s) + 1):
        lo, hi = ht[0] + b * block_s, ht[0] + (b + 1) * block_s
        idx = [i for i, x in enumerate(ht) if lo <= x < hi]
        if len(idx) < 20 or ht[idx[-1]] - ht[idx[0]] < block_s / 2:
            continue                                          # 끝의 반도 안 되는 조각은 버린다
        m = [hm[i] for i in idx]
        vm = statistics.pvariance(m)
        if vm == 0:
            continue
        ks.append((_cov([hx[i] for i in idx], m) / vm, _cov([hy[i] for i in idx], m) / vm))
    out = {"lag_s": lag, "r2": score, "blocks": len(ks), "k": None, "dir_deg": None, "se": None, "seen": None}
    if not ks:
        return out
    kx, ky = statistics.fmean(k[0] for k in ks), statistics.fmean(k[1] for k in ks)
    out |= {"k": math.hypot(kx, ky), "dir_deg": math.degrees(math.atan2(ky, kx))}
    if len(ks) >= 2:
        se = math.hypot(statistics.stdev(k[0] for k in ks), statistics.stdev(k[1] for k in ks)) / math.sqrt(len(ks))
        out |= {"se": se, "seen": out["k"] > SEEN_SIGMA * se if len(ks) >= MIN_BLOCKS else None}
    return out


def clock(t, utc) -> dict:
    """PC 수신 시각 − 셀 GPS UTC (네트워크 지연 포함) — 중앙값(ms)과 기울기(ppm, + 면 셀 시계가 점점 늦어진다)."""
    off = [a - b for a, b in zip(t, utc)]
    if not off:
        return {"offset_ms": None, "drift_ppm": None, "n": 0}
    drift = statistics.linear_regression(t, off).slope * 1e6 if len(off) >= 2 and t[-1] > t[0] else None
    return {"offset_ms": statistics.median(off) * 1000, "drift_ppm": drift, "n": len(off)}


def loss(t, seq) -> dict:
    """셀 순번(펌웨어 epoch, 패킷마다 1씩)으로 센 빠진 패킷. 순번이 줄면(셀 재부팅) 거기서 새로 센다. 같은 순번은 하나로."""
    if not seq:
        return {"expected": 0, "got": 0, "lost": 0, "lost_pct": None, "restarts": 0}
    runs, last = [[]], None
    for _, q in sorted(zip(t, seq)):
        if last is not None and q < last:
            runs.append([])
        runs[-1].append(q)
        last = q
    exp = sum(max(r) - min(r) + 1 for r in runs)
    got = sum(len(set(r)) for r in runs)
    return {"expected": exp, "got": got, "lost": exp - got, "lost_pct": 100.0 * (exp - got) / exp, "restarts": len(runs) - 1}


def segments(t, seconds: float) -> list[list[int]]:
    """시각 목록을 seconds 초 덩어리로 — 덩어리마다 인덱스 목록."""
    out: list[list[int]] = []
    for i, x in enumerate(t):
        k = int((x - t[0]) // seconds)
        while len(out) <= k:
            out.append([])
        out[k].append(i)
    return [x for x in out if x]
