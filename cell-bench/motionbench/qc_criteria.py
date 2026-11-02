"""레일 QC 1차 기준 — 같은 셀을 여러 번 잰 판정 파일(protocol_<시각>.json)로 항목마다 기준 자격과 기준값을 낸다.

규칙은 데이터를 보기 전에 정했다(2026-11-02 사용자 결정 — 공식 스펙이 없으므로 지금 시료로 충분히 모은 뒤 1차 기준을 세운다).
  1. 자격: 같은 셀을 다시 잰 흔들림(셀마다 실행 사이 표준편차의 중앙값)이 셀 사이 흔들림(셀별 중앙값의 MAD × 1.4826)의
     절반 이하일 때만 합불 항목이 된다. 아니면 '살펴봄'으로 남긴다 — 그 항목은 셀보다 시험 자체의 흔들림이 크다는 뜻이다.
  2. 선: 셀별 중앙값의 무리 중앙값에서 셀 사이 흔들림의 K(3)배 밖이 불합격. 나쁜 쪽만 본다(DIRECTION).
  3. 확인: 그 선으로 각 실행을 다시 판정해 셀마다 불합격 비율을 남긴다. 매번 다른 셀이 걸리면 선이 촘촘한 것이다.
기준 상태는 늘 '제안값'이다 — 바깥 대조와 다른 세트로 확인되기 전까지(design/qc-criteria-v1.md).
순수 함수만 있다. 파일 읽기 · 쓰기는 tools/qc_criteria.py 가 한다.
"""
from __future__ import annotations

import statistics as st

from motionbench.qc_v4 import CRITERIA, FLOOR

K = 3.0                 # 무리 흔들림의 몇 배 밖이면 불합격
ELIGIBLE_RATIO = 0.5    # 같은 셀 흔들림 ≤ 셀 사이 흔들림 × 이 값이어야 합불 자격
MIN_RUNS = 3            # 셀마다 이만큼은 다시 재야 같은 셀 흔들림을 잰다
DIRECTION = {"cn0_sim": "low", "gps_rx": "low", "pos_h": "high", "lock": "high",
             "gate_on": "high", "sog_lag": "high", "gps_move": "both"}


def per_cell(docs: list[dict], iid: str, key=None) -> dict[str, list[float]]:
    """판정 파일 여러 장 → {셀: [실행마다 값]}. 장비가 무효인 실행과 값이 없는 칸은 뺀다. key 는 묶음 값에서 숫자를 꺼낸다."""
    out: dict[str, list[float]] = {}
    for d in docs:
        if not (d.get("rig") or {}).get("유효", True):
            continue
        for s, c in (d.get("cells") or {}).items():
            it = (c.get("items") or {}).get(iid)
            if not it or it.get("value") is None:
                continue
            v = key(it["value"]) if key else it["value"]
            if v is not None:
                out.setdefault(str(s), []).append(float(v))
    return out


def _mad_sd(xs: list[float]) -> float:
    m = st.median(xs)
    return st.median(abs(x - m) for x in xs) * 1.4826


def derive(per: dict[str, list[float]], iid: str, k: float = K, ratio_max: float = ELIGIBLE_RATIO,
           min_runs: int = MIN_RUNS) -> dict:
    """{셀: [값]} → 기준 한 칸. 재료가 모자라면 kind '살펴봄' 과 이유를 남긴다."""
    cells = {s: v for s, v in per.items() if v}
    meds = {s: st.median(v) for s, v in cells.items()}
    runs = sorted(len(v) for v in cells.values())
    base = {"unit": CRITERIA[iid]["unit"], "direction": DIRECTION[iid], "n_cells": len(cells),
            "runs_per_cell": runs[len(runs) // 2] if runs else 0}
    if len(cells) < 5 or base["runs_per_cell"] < min_runs:
        return base | {"kind": "살펴봄", "status": "미정", "why": f"재료 부족 — 셀 {len(cells)}대 · 셀마다 {base['runs_per_cell']}번(필요 {min_runs}번)"}
    center = st.median(meds.values())
    between = max(_mad_sd(list(meds.values())), FLOOR.get(iid, 0.0))
    within = st.median(st.pstdev(v) for v in cells.values() if len(v) >= 2)
    ratio = within / between if between else float("inf")
    lo = round(center - k * between, 4) if DIRECTION[iid] in ("low", "both") else None
    hi = round(center + k * between, 4) if DIRECTION[iid] in ("high", "both") else None
    out = base | {"center": round(center, 4), "within": round(within, 4), "between": round(between, 4),
                  "ratio": round(ratio, 3), "lo": lo, "hi": hi}
    if ratio > ratio_max:
        return out | {"kind": "살펴봄", "status": "미정",
                      "why": f"같은 셀 흔들림 {within:.3g} 이 셀 사이 흔들림 {between:.3g} 의 {ratio:.2f}배 — {ratio_max} 보다 커서 셀을 가르지 못한다"}
    fails = {}
    for s, v in cells.items():
        bad = sum(1 for x in v if not inside(x, lo, hi))
        if bad:
            fails[s] = {"fail_runs": bad, "runs": len(v), "median": round(meds[s], 4)}
    return out | {"kind": "합불", "status": "제안값",
                  "why": f"같은 셀 흔들림이 셀 사이 흔들림의 {ratio:.2f}배 — 셀을 가른다", "fails": fails}


def inside(x: float, lo: float | None, hi: float | None) -> bool:
    return (lo is None or x >= lo) and (hi is None or x <= hi)


def rule_text(iid: str, c: dict) -> str:
    """결과판 기준 줄에 그대로 보일 한 줄."""
    if c.get("kind") != "합불":
        return f"{CRITERIA[iid]['rule']} · {c.get('why', '')}"
    u = c["unit"]
    side = (f"{c['lo']:g} {u} 이상" if c["lo"] is not None else "") + (" · " if c["lo"] is not None and c["hi"] is not None else "") + \
           (f"{c['hi']:g} {u} 이하" if c["hi"] is not None else "")
    return f"{side} (무리 중앙 {c['center']:g} ± 셀 사이 흔들림 {c['between']:g} × {K:g})"
