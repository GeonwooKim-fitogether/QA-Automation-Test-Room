"""짝 판정 문턱 자동 도출 — 순수 함수 (자동 합류 · 다중 세트 명세 3-5 의 U2 보정을 엔진이 스스로 끝낸다).

보정 기록(켬 · 끔을 번갈아 적은 셀별 잔량)에서 판정식(3-3)의 문턱을 **보수적으로** 정한다. 장비 · 시계 · 파일에 닿지 않는다.
가장 조심할 것은 오탐(틀린 짝을 통과시킴)이다 — 그래서 애매하면 도출하지 않고 inconclusive 로 멈춰 사람을 부른다.
"""
from __future__ import annotations

import math
from typing import Iterable

from .pair import _median, follows_slope, slope

Rows = list[tuple[float, bool, dict[int, int]]]       # 보정 기록 한 줄 = (시각, 플러그 켬 여부, {시리얼: 배터리 %})


def _quantile(xs: list[float], q: float) -> float:
    """nearest-rank 분위수 (보간하지 않는다 — 관측한 값 중 하나를 쓴다)."""
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, math.ceil(q * len(xs)) - 1))] if xs else math.nan


def _runs(rows: Rows, gap_s: float) -> list[dict]:
    """플러그 상태가 같고 표본 사이가 gap_s 이하인 연속 구간."""
    runs: list[dict] = []
    for t, on, b in rows:
        if runs and runs[-1]["on"] == bool(on) and t - runs[-1]["t1"] <= gap_s:
            runs[-1]["rows"].append((t, b))
            runs[-1]["t1"] = t
        else:
            runs.append({"on": bool(on), "t0": t, "t1": t, "rows": [(t, b)]})
    return runs


def _run_slope(run: dict, serial: int, coverage: float) -> float:
    pts = [(t, b[serial]) for t, b in run["rows"] if serial in b]
    return slope(pts) if len(pts) >= coverage * len(run["rows"]) else math.nan


def _inconclusive(code: str, why: str, evidence: dict | None = None, fault: bool = False) -> dict:
    return {"verdict": "inconclusive", "code": code, "why": why, "fault": fault, "thresholds": None, "evidence": evidence or {}}


def derive_thresholds(rows: Rows, gk: Iterable[int], groups: dict, base: dict, rule: dict) -> dict:
    """보정 기록에서 문턱을 도출한다.

    짝이 맞다 = '끔 구간에서 세트 k 셀의 기울기가 방전 쪽, 켬 구간에서 충전 쪽으로 뚜렷하게 바뀐다' AND '대조군(세트 1 등)은 구간이 바뀌어도 변하지 않는다'.
    도출 문턱 = 세트 k 가 보인 응답 하한(셀 24대 중 최소)의 resp_ratio 배 와 대조군 변동 상한의 noise_mult 배 중 더 엄격한(큰) 쪽, 그리고 절대 하한.

    인자  rows 보정 기록 · gk 세트 k 시리얼 · groups {무리 이름: 시리얼} (세트 k 밖 — 대조군 '세트 1' 포함) ·
          base 지금의 PAIR(밖의 셀이 '따른다'를 가르는 기본 문턱) · rule config.DERIVE.
    돌려주는 것  {verdict: derived | inconclusive, code, why, fault, thresholds, evidence}

    inconclusive 의 code
      samples · no_pattern · cells_missing · control_missing   기록이 모자라다(구간 · 패턴 구간 · 세트 셀 · 대조군)
      foreign_follows   세트 k 밖의 셀이 플러그를 따라 움직인다 = 짝이 틀렸다는 증거 → fault=True (부른 쪽이 짝 이상으로 올린다)
      weak              세트 k 셀의 응답이 너무 약하다(노이즈뿐 · 플러그가 이 Dock 을 안 먹임)
      control_unstable  대조군이 구간 사이에 흔들린다(세트 1 단계 전환 등) — 그 기록으로는 기준을 정할 수 없다
      indistinct        응답이 대조군 변동과 구분되지 않는다
      cell_no_response  일부 셀이 무반응(응답 · 상관 · 켬끔 차 중 하나가 모자람) — 한 대라도 그러면 정하지 않는다
    derived 의 thresholds 는 PAIR 에서 바뀌는 칸만이다(quick_r_cell · quick_r_median · quick_other_max · quick_cells_min · follow_delta).
    """
    gk = list(gk)
    n = len(gk)
    ctrl_name = rule["control_group"]
    runs = _runs(rows, rule["run_gap_s"])
    kept = [r for r in runs if len(r["rows"]) >= rule["min_run_samples"]]
    ev: dict = {"runs": len(runs), "kept_runs": len(kept)}
    if len(kept) < rule["min_runs"]:
        return _inconclusive("samples", f"켬 · 끔 구간이 {len(kept)}개뿐이다(기준 {rule['min_runs']}개 이상, 구간마다 표본 "
                                        f"{rule['min_run_samples']}개 이상) — 표본이 모자라다", ev)
    kept_ids = {id(r) for r in kept}
    # 원래 순서에서 바로 이웃한 두 구간의 켬 · 끔 쌍 = 전환 하나 (켬 구간, 끔 구간)
    trans = [(a, b) if a["on"] else (b, a) for a, b in zip(runs, runs[1:])
             if id(a) in kept_ids and id(b) in kept_ids and a["on"] != b["on"]]
    ev["transitions"] = len(trans)
    if len(trans) < 4:
        return _inconclusive("samples", f"켬 · 끔 전환이 {len(trans)}번뿐이다(4번 이상 필요) — 표본이 모자라다", ev)
    # 패턴 구간 = 끝에서부터 찾은 켬 끔 켬 끔 켬 다섯 구간 (보정 기록의 패턴 검증 부분)
    pat = None
    for i in range(len(runs) - 5, -1, -1):
        w = runs[i:i + 5]
        if all(id(r) in kept_ids for r in w) and [r["on"] for r in w] == [True, False, True, False, True]:
            pat = w
            break
    if pat is None:
        return _inconclusive("no_pattern", "켬 · 끔 · 켬 · 끔 · 켬 다섯 구간(패턴 검증 부분)이 기록에 없다 — 표본이 모자라다", ev)
    states = [1, 0, 1, 0, 1]
    never = {"follow_rho": 9.0, "follow_delta": 1e9}                  # ρ · Δ 값만 얻는다 (판정은 아래에서)

    gset = set(gk)
    serials = sorted(gset | {s for g in groups.values() for s in g})
    cov = rule["coverage"]
    r_by: dict[int, list[float]] = {}                                  # 셀 → 전환마다 r = 켬 기울기 − 끔 기울기
    rho_by: dict[int, tuple[float, float]] = {}                        # 셀 → (ρ, Δ) (패턴 구간)
    pat_sl: dict[int, list[float]] = {}
    for s in serials:
        r_by[s] = [_run_slope(on, s, cov) - _run_slope(off, s, cov) for on, off in trans]
        pat_sl[s] = [_run_slope(r, s, cov) for r in pat]
        _, rho, dl = follows_slope(pat_sl[s], states, never)
        rho_by[s] = (rho, dl)

    def ok_cells(ss) -> list[int]:
        return [s for s in ss if not any(math.isnan(v) for v in r_by.get(s, [math.nan])) and not math.isnan(rho_by.get(s, (math.nan,))[0])]

    miss = sorted(gset - set(ok_cells(gk)))
    ev["gk_cells"] = n
    if miss:
        return _inconclusive("cells_missing", f"세트 셀 {len(miss)}대의 기록이 모자라 응답을 잴 수 없다 {miss[:6]}", ev)
    ctrl = ok_cells(groups.get(ctrl_name, []))
    if len(ctrl) < rule["min_control_cells"]:
        return _inconclusive("control_missing", f"대조군({ctrl_name}) 셀이 {len(ctrl)}대만 잡혔다(기준 {rule['min_control_cells']}대) — 대조할 수 없다", ev)
    others = {g: ok_cells([s for s in ss if s not in gset]) for g, ss in groups.items()}
    others = {g: ss for g, ss in others.items() if ss}
    nj = len(trans)
    g_med = {g: [_median([r_by[s][j] for s in ss]) for j in range(nj)] for g, ss in others.items() if len(ss) >= 3}   # 무리 · 전환별 r 중앙값

    resp_c = {s: _median(r_by[s]) for s in gk}
    resp_floor = min(resp_c.values())
    resp_group = _median(list(resp_c.values()))
    delta_floor = min(rho_by[s][1] for s in gk)
    rho_floor = min(rho_by[s][0] for s in gk)
    pool = [s for ss in others.values() for s in ss]
    noise_cell = _quantile([abs(v) for s in pool for v in r_by[s]], rule["noise_quantile"])
    noise_group = max((abs(v) for ms in g_med.values() for v in ms), default=0.0)
    noise_delta = _quantile([abs(rho_by[s][1]) for s in pool], rule["noise_quantile"])
    ev.update(resp_floor=round(resp_floor, 3), resp_group=round(resp_group, 3), delta_floor=round(delta_floor, 3),
              rho_floor=round(rho_floor, 3), noise_cell=round(noise_cell, 3), noise_group=round(noise_group, 3),
              noise_delta=round(noise_delta, 3), control_cells=len(ctrl), pool_cells=len(pool),
              groups={g: len(ss) for g, ss in others.items()})

    # 세트 밖의 셀이 플러그를 따른다 = 짝이 틀렸다 (세트 1 플러그를 끈 것처럼 대조군이 같이 내려감)
    follow = {}
    for g, ss in others.items():
        fol = sorted(s for s in ss if follows_slope(pat_sl[s], states, base)[0])
        rmed = _median(g_med[g]) if g in g_med else math.nan
        big = not math.isnan(rmed) and rmed >= max(0.5 * resp_group, base["quick_other_max"])
        if (fol and len(fol) >= 0.5 * len(ss)) or big:
            follow[g] = {"cells": fol[:6], "n": len(ss), "r_median": None if math.isnan(rmed) else round(rmed, 3)}
    if follow:
        ev["follow"] = follow
        return _inconclusive("foreign_follows", f"세트 밖의 셀이 플러그를 따라 움직인다 {sorted(follow)} — 이 플러그가 다른 Dock 을 "
                                                f"먹인다는 증거다. 짝이 틀렸을 수 있다", ev, fault=True)

    if resp_group < 2 * rule["min_r_cell"]:
        return _inconclusive("weak", f"세트 셀의 응답이 너무 약하다(r 중앙값 {resp_group:.2f} %/분) — 노이즈뿐이거나 이 플러그가 이 Dock 을 먹이지 않는다", ev)
    if noise_group >= 0.5 * resp_group:
        return _inconclusive("control_unstable", f"대조군이 구간 사이에 흔들린다(무리 r 변동 {noise_group:.2f} %/분, 세트 응답 {resp_group:.2f}) — "
                                                 f"세트 1 단계 전환 등. 조용할 때 다시", ev)
    sep = rule["separation"]
    other_max = max(rule["noise_mult"] * noise_group, rule["min_other_max"])
    if resp_group < sep * noise_group or other_max > 0.5 * resp_group:
        return _inconclusive("indistinct", f"세트 응답({resp_group:.2f})이 대조군 변동({noise_group:.2f})과 구분되지 않는다", ev)
    weak = sorted(s for s in gk if resp_c[s] < max(sep * noise_cell, 2 * rule["min_r_cell"])
                  or rho_by[s][0] < rule["rho_floor"] or rho_by[s][1] < max(sep * noise_delta, 2 * rule["min_delta"]))
    if weak:
        ev["weak_cells"] = weak
        return _inconclusive("cell_no_response", f"세트 셀 {len(weak)}대가 무반응이거나 대조군 변동과 구분되지 않는다 {weak[:6]} — 한 대라도 그러면 정하지 않는다", ev)

    q_cell = max(rule["resp_ratio"] * resp_floor, rule["noise_mult"] * noise_cell, rule["min_r_cell"])
    q_med = max(rule["resp_ratio"] * resp_group, rule["noise_mult"] * noise_group, q_cell, rule["min_r_median"])
    f_delta = max(rule["resp_ratio"] * delta_floor, rule["noise_mult"] * noise_delta, rule["min_delta"])
    # 도출한 문턱이 수동 보정의 기본값(PAIR)보다 느슨해지지 않게 한다 — 응답 하한(r_cell)은 클수록, 다른 무리 상한(other_max)은 작을수록 엄격하다
    q_cell = max(q_cell, base["quick_r_cell"])
    q_med = max(q_med, q_cell)
    other_max = min(other_max, base["quick_other_max"])
    th = {"quick_r_cell": round(q_cell, 3), "quick_r_median": round(q_med, 3), "quick_other_max": round(other_max, 3),
          "quick_cells_min": min(n, max(1, math.ceil(rule["cells_frac"] * n))), "follow_delta": round(f_delta, 3)}
    return {"verdict": "derived", "code": "ok", "fault": False, "thresholds": th, "evidence": ev,
            "why": f"세트 응답 하한 {resp_floor:.2f} · 중앙 {resp_group:.2f} %/분, 대조군 변동 {noise_cell:.2f}(셀) · {noise_group:.2f}(무리) → 문턱 {th}"}
