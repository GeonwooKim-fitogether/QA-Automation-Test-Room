"""짝 검증 판정 — 순수 함수 (다중 세트 명세 3-3). 장비 · 시계 · 파일에 닿지 않는다.

검증기(8절 6단계)가 10초마다 적은 표본 [(시각, {시리얼: 배터리 %})] 과 플러그를 켜고 끈 구간을 넘기면, 여기서 통과 · 실패를 정한다.
문턱은 config.PAIR (전부 가안 — U2 보정 실측으로 정한다).

  빠른 검증   r_c = (켬 구간 기울기) − (끔 구간 기울기). 무리 중앙값으로 '플러그 ↔ Dock' 만 본다
  패턴 검증   구간마다 기울기와 플러그 상태의 상관 ρ 와 켬 · 끔 평균 차 Δ — '따른다' = ρ ≥ follow_rho 이고 Δ ≥ follow_delta
              기울기를 못 구한 셀(NaN)은 '따르지 않는다'. 상관만 보면 구간이 다섯뿐이라 우연히 ρ > 0.5 일 확률이 약 0.2 다(검토 A-M7)

판정 방식은 갈아 끼울 수 있다(U6): 라이브 state 바이트가 충전 중을 알린다고 확인되면 follows 자리에 그것으로 판정하는 함수를 넘긴다.
"""
from __future__ import annotations

import math
import statistics
from typing import Callable, Iterable

Samples = list[tuple[float, dict[int, int]]]


def slope(points: Iterable[tuple[float, float]]) -> float:
    """최소제곱 기울기 (단위 / 분). 점이 3개 미만이거나 시각이 다 같으면 NaN."""
    pts = [(t, v) for t, v in points if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(pts) < 3:
        return math.nan
    mt = sum(t for t, _ in pts) / len(pts)
    mv = sum(v for _, v in pts) / len(pts)
    den = sum((t - mt) ** 2 for t, _ in pts)
    if den == 0:
        return math.nan
    return sum((t - mt) * (v - mv) for t, v in pts) / den * 60.0


def cell_slope(samples: Samples, serial: int, t0: float, t1: float) -> float:
    return slope((t, b[serial]) for t, b in samples if t0 <= t < t1 and serial in b)


def _median(xs: list[float]) -> float:
    xs = [x for x in xs if not math.isnan(x)]
    return float(statistics.median(xs)) if xs else math.nan


def quick_judge(samples: Samples, on: tuple[float, float], off: tuple[float, float], gk: Iterable[int],
                groups: dict, rule: dict, skip_groups: Iterable = ()) -> dict:
    """빠른 검증 — 켬 구간(on)과 끔 구간(off)의 기울기 차 r 로.

    통과 = ① Gk 의 r 중앙값 ≥ quick_r_median ② Gk 중 r ≥ quick_r_cell 인 셀 ≥ quick_cells_min ③ 다른 무리마다 r 중앙값 < quick_other_max.
    skip_groups — 검증 창 안에 자기 플러그 상태가 바뀐 무리. ③ 에서 뺀다(그 무리의 기울기는 남의 단계 전환으로 흔들린다, 명세 3-2).
    verdict: pass · not_feeding(①② 실패 — Pk 가 Dock k 를 먹이지 않는다) · feeds_other(③ 실패 — Pk 가 남의 Dock 을 먹인다).
    """
    gk = list(gk)
    r = {s: cell_slope(samples, s, *on) - cell_slope(samples, s, *off) for s in gk}
    med = _median(list(r.values()))
    n_ok = sum(1 for v in r.values() if not math.isnan(v) and v >= rule["quick_r_cell"])
    others = {}
    skip = set(skip_groups)
    for name, ser in groups.items():
        if name in skip:
            continue
        others[name] = _median([cell_slope(samples, s, *on) - cell_slope(samples, s, *off) for s in ser])
    feeds = sorted(n for n, v in others.items() if not math.isnan(v) and v >= rule["quick_other_max"])
    if feeds:
        verdict, why = "feeds_other", f"다른 무리 {feeds} 가 따라 움직였다 (r 중앙값 {', '.join(f'{others[n]:.2f}' for n in feeds)})"
    elif math.isnan(med) or med < rule["quick_r_median"] or n_ok < rule["quick_cells_min"]:
        verdict, why = "not_feeding", f"세트 셀의 r 중앙값 {med:.2f} · 따른 셀 {n_ok}대 — 이 플러그가 이 Dock 을 먹이지 않는다"
    else:
        verdict, why = "pass", f"r 중앙값 {med:.2f} · 따른 셀 {n_ok}대 · 다른 무리 움직임 없음"
    return {"verdict": verdict, "why": why, "r_median": med, "cells_ok": n_ok, "others": others, "r": r}


def follows_slope(slopes: list[float], states: list[int], rule: dict) -> tuple[bool, float, float]:
    """'따른다' — (따르나, ρ, Δ). 구간 기울기 중 하나라도 NaN 이면 따르지 않는다."""
    if any(math.isnan(x) for x in slopes) or len(slopes) < 3:
        return False, math.nan, math.nan
    on = [x for x, p in zip(slopes, states) if p]
    off = [x for x, p in zip(slopes, states) if not p]
    if not on or not off:
        return False, math.nan, math.nan
    delta = sum(on) / len(on) - sum(off) / len(off)
    ms, mp = sum(slopes) / len(slopes), sum(states) / len(states)
    num = sum((x - ms) * (p - mp) for x, p in zip(slopes, states))
    den = math.sqrt(sum((x - ms) ** 2 for x in slopes) * sum((p - mp) ** 2 for p in states))
    rho = num / den if den else math.nan
    ok = not math.isnan(rho) and rho >= rule["follow_rho"] and delta >= rule["follow_delta"]
    return ok, rho, delta


def pattern_judge(samples: Samples, segments: list[tuple[float, float, bool]], gk: Iterable[int], rule: dict,
                  follows: Callable[[list[float], list[int], dict], tuple[bool, float, float]] = follows_slope) -> dict:
    """패턴 검증 — 들린 모든 셀에 대해 '따른다'를 본다.

    통과 = ① Gk 의 모든 셀이 따른다 ② Gk 밖에서 따르는 셀이 없다. Gk 중 끝 구간 배터리가 pattern_full_pct 이상인 셀은
    '판정 불가'로 빼고, 그런 셀이 pattern_unknown_max 를 넘으면 검증 전체가 unknown 이다.
    verdict: pass · fail · unknown. not_following = Gk 인데 안 따름(다른 Dock · 시리얼 틀림), foreign = Gk 밖인데 따름.
    """
    gk = set(gk)
    states = [1 if on else 0 for _, _, on in segments]
    heard = sorted({s for _, b in samples for s in b})
    last = {}
    for _, b in samples:
        last.update(b)
    unknown = sorted(s for s in gk if last.get(s, 0) >= rule["pattern_full_pct"])
    detail, not_following, foreign = {}, [], []
    for s in heard:
        if s in unknown:
            continue
        sl = [cell_slope(samples, s, t0, t1) for t0, t1, _ in segments]
        ok, rho, delta = follows(sl, states, rule)
        detail[s] = {"follows": ok, "rho": rho, "delta": delta}
        if s in gk and not ok:
            not_following.append(s)
        if s not in gk and ok:
            foreign.append(s)
    missing = sorted(gk - set(heard))
    not_following = sorted(set(not_following) | set(missing))
    if len(unknown) > rule["pattern_unknown_max"]:
        verdict = "unknown"
    elif not_following or foreign:
        verdict = "fail"
    else:
        verdict = "pass"
    return {"verdict": verdict, "not_following": not_following, "foreign": sorted(foreign), "unknown": unknown,
            "missing": missing, "cells": detail}
