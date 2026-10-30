"""셀 레일 시험 프로토콜 v4 — 판정 파일(protocol_<시각>.json)의 칸을 만드는 순수 함수.

칸 이름의 정본은 dynamic-bench/design/qc-protocol-v4.md 6-3 · 7-6. 결과판(cell-bench/board/index.html 의 adaptV4)은 이 칸을 그대로 그린다.
  cells.<시리얼> = {items: {<id>: {value, unit, verdict, z, basis}}, verdict: {overall, flags, reasons}, dock: {col, row}}
  criteria.<id> = {value, unit, status: 제안값 · 미정 · 확정, kind: 합불 · 살펴봄 · 정보용, rule}
판정 갈래(4절): 합불 항목만 셀 종합에 들어간다. 기준이 '미정'인 항목은 살펴봄으로 돈다 — 무리 점수 |z| ≥ 3 이면 flag.
셀 종합: 합불 항목 하나라도 fail → fail, 합불 항목의 재료가 없으면 hold, 아니면 pass. 장비가 무효면 결과판이 셀 판정을 회색으로 그린다.
"""
from __future__ import annotations

import statistics as st

from motionbench.qc_protocol import robust_z

Z_THR = 3.0
CRITERIA = {
    "rx": {"value": 1.0, "unit": "%", "status": "제안값", "kind": "합불", "rule": "시험 구간에서 빠진 라이브 패킷 ≤ 1 %"},
    "gps_rx": {"value": None, "unit": "%", "status": "미정", "kind": "살펴봄", "rule": "판정 구간에서 위치를 잡은 패킷 비율 — 무리에서 |z| ≥ 3"},
    "lock": {"value": None, "unit": "s", "status": "미정", "kind": "살펴봄", "rule": "장면 시작 → 첫 고정 초 — 무리에서 |z| ≥ 3"},
    "cn0_sim": {"value": None, "unit": "dBHz", "status": "미정", "kind": "살펴봄", "rule": "평균 C/N0 중앙값 — 무리에서 |z| ≥ 3"},
    "gps_move": {"value": None, "unit": "비율", "status": "미정", "kind": "살펴봄", "rule": "움직일 때 셀 속도 ÷ 장면 속도 — 무리에서 |z| ≥ 3"},
    "sog_lag": {"value": None, "unit": "s", "status": "미정", "kind": "살펴봄", "rule": "셀 속도가 장면 속도를 따라오는 늦음 — 무리에서 |z| ≥ 3"},
    "gate_on": {"value": None, "unit": "s", "status": "미정", "kind": "살펴봄", "rule": "레일 출발 → 셀 움직임 켜짐 — 무리에서 |z| ≥ 3"},
    "pos_h": {"value": None, "unit": "m", "status": "미정", "kind": "살펴봄", "rule": "정지 위치 − 장면 좌표 수평 거리 중앙값 — 무리에서 |z| ≥ 3"},
}
FLOOR = {"lock": 1.0, "gps_rx": 1.0, "cn0_sim": 1.0, "gps_move": 0.01, "sog_lag": 0.1, "gate_on": 0.1, "pos_h": 0.2}


def item(value, unit: str, verdict: str, z: float | None = None, basis: str | None = None) -> dict:
    return {"value": value, "unit": unit, "verdict": verdict, "z": None if z is None else round(z, 2), "basis": basis}


def review(vals: dict, iid: str, basis: str | None = None, key=None) -> dict:
    """살펴봄 항목 — {셀: 값} → {셀: item}. key 가 있으면 값(묶음)에서 z 를 잴 숫자를 꺼낸다. 값이 없는 셀은 verdict hold."""
    num = {s: (key(v) if key and v is not None else v) for s, v in vals.items()}
    z = robust_z(num, FLOOR.get(iid, 0.0))
    unit = CRITERIA[iid]["unit"]
    return {s: item(vals[s], unit, "hold", None, "재료 없음") if num[s] is None else
            item(vals[s], unit, "flag" if abs(z[s]) >= Z_THR else "pass", z[s], basis) for s in vals}


def pass_fail(vals: dict, iid: str, basis: str | None = None) -> dict:
    """합불 항목(값이 기준 이하이면 pass) — {셀: 값} → {셀: item}. 값이 없으면 hold."""
    lim, unit = CRITERIA[iid]["value"], CRITERIA[iid]["unit"]
    return {s: item(None, unit, "hold", None, "재료 없음") if v is None else item(round(v, 3), unit, "pass" if v <= lim else "fail", None, basis)
            for s, v in vals.items()}


def overall(items: dict) -> dict:
    """한 셀의 {id: item} → verdict {overall, flags, reasons}. 합불 항목만 종합에 들어간다."""
    pf = [i for i in items if CRITERIA.get(i, {}).get("kind") == "합불"]
    fails = [i for i in pf if items[i]["verdict"] == "fail"]
    holds = [i for i in pf if items[i]["verdict"] == "hold"]
    flags = [i for i, x in items.items() if x["verdict"] == "flag"]
    reasons = [f"{i} {items[i]['value']} {items[i]['unit']} > 기준 {CRITERIA[i]['value']}" for i in fails] + [f"{i} 재료 없음" for i in holds]
    return {"overall": "fail" if fails else "hold" if holds else "pass", "flags": flags, "reasons": reasons}


def group(cells: dict, iid: str, key=None) -> dict | None:
    """무리 요약 {med, n} — 결과판이 그림의 무리 중앙값 선으로 그린다."""
    xs = [key(c["items"][iid]["value"]) if key else c["items"][iid]["value"] for c in cells.values()
          if iid in c.get("items", {}) and c["items"][iid]["value"] is not None]
    xs = [x for x in xs if x is not None]
    return {"med": round(st.median(xs), 4), "n": len(xs)} if xs else None


def build(cell_items: dict, dock: dict) -> dict:
    """{셀: {id: item}} → cells 칸."""
    return {str(s): {"items": it, "verdict": overall(it), "dock": dock.get(s)} for s, it in cell_items.items()}
