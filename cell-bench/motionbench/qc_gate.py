"""단계별 거르기 — 센서 시험에서 떨어진 셀을 펌웨어 시험에 넣지 않는다(2026-11-12 사용자 결정, dynamic-bench/design/qc-tests.md 2-1절).

운영 순서는 ORDER(GNSS-1 → GNSS-2 → IMU-1 → IMU-2 → FIRMWARE-1 → FIRMWARE-2)다(2026-11-12 여섯 시험 결정 — 센서 시험 넷, 펌웨어 시험 둘). 나쁜 센서 위의 펌웨어 값은 해석할 수 없으므로,
앞 시험에서 '불합격'한 셀은 뒤 시험의 판정 대상에서 뺀다(독에는 있어도 된다).

거르기 파일 — 세트마다 하나, data/qc_gate_<세트>.json:
  cells.<시리얼>.<시험 ID> = {status: 합격 · 재측정 · 불합격, date, source(근거 폴더, data/ 기준), why, rounds: [회차마다 근거]}
  slot_cn0.<조건>.<칸> = [그 칸에 앉았던 정상 셀의 신호 세기 기록] — GNSS-1 칸 기준값이 여기서 나온다(기록이 쌓이며 갱신).
칸은 "c-r" — 배치 파일 columns 의 c 번째 줄(사람이 '행'이라 부르는 것, 한 줄 6칸)의 r 번째 칸. 조건은 감쇠와 꽂은 행(예 "감쇠18_행1,3").

기준 숫자는 criteria/qc_tests.json 하나에서 읽는다. 파일 읽기 · 쓰기 말고는 순수 함수다 — 장비에 닿지 않는다.
"""
from __future__ import annotations

import json
import os
import statistics as st
import time
from pathlib import Path

CRIT_FILE = Path(__file__).resolve().parent / "criteria" / "qc_tests.json"
ORDER = ("GNSS-1", "GNSS-2", "IMU-1", "IMU-2", "FIRMWARE-1", "FIRMWARE-2")
SENSOR = ("GNSS-1", "GNSS-2", "IMU-1", "IMU-2")
PASS, RETEST, FAIL = "합격", "재측정", "불합격"


def criteria(test: str, path: Path = CRIT_FILE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))["tests"][test]


def path(set_name: str, data: Path) -> Path:
    return Path(data) / f"qc_gate_{set_name}.json"


def load(p: Path) -> dict:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"cells": {}, "slot_cn0": {}}


def save(p: Path, doc: dict) -> None:
    doc["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = Path(p).with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def excluded(doc: dict, test: str) -> dict[int, str]:
    """test 보다 앞 시험에서 '불합격'한 셀 → {시리얼: 이유}. 앞 시험이 없으면(GNSS-1) 빈 표."""
    before = ORDER[:ORDER.index(test)]
    out = {}
    for s, per in (doc.get("cells") or {}).items():
        bad = [f"{t} 불합격({per[t].get('date')}, {per[t].get('source')})" for t in before if (per.get(t) or {}).get("status") == FAIL]
        if bad:
            out[int(s)] = " · ".join(bad)
    return out


def record(doc: dict, serial: int, test: str, status: str, source: str, why: str, date: str | None = None, **extra) -> None:
    """한 시험의 결과를 셀 칸에 적는다. 같은 근거 폴더로 다시 적으면 바꿔 쓴다(판정을 다시 돌려도 쌓이지 않는다)."""
    cell = doc.setdefault("cells", {}).setdefault(str(serial), {})
    hist = [h for h in (cell.get(test) or {}).get("history", []) if h.get("source") != source]
    entry = {"status": status, "date": date or time.strftime("%Y-%m-%d"), "source": source, "why": why} | extra
    cell[test] = entry | {"history": hist + [{k: v for k, v in entry.items()}]}


def gnss1_status(rounds: list[dict], hits_to_fail: int) -> str:
    """GNSS-1 회차 기록 → 지금 상태. 걸린 회차가 hits_to_fail 번이면 불합격, 아니면 마지막 회차가 걸렸으면 재측정, 아니면 합격."""
    hits = sum(1 for r in rounds if r["hit"])
    if hits >= hits_to_fail:
        return FAIL
    return RETEST if rounds and rounds[-1]["hit"] else PASS


def gnss1_round(doc: dict, cells: dict[int, dict], cond: str, source: str, crit: dict | None = None,
                date: str | None = None) -> dict[int, dict]:
    """GNSS-1 한 회차의 1차 거르기. cells = {시리얼: {"slot": "c-r", "cn0": dB-Hz | None, "gps_rx": % | None}} — 판정 대상 셀만.
    칸 기준값은 이 회차 앞까지 쌓인 같은 조건의 기록으로 정하고, 이 회차에 걸리지 않은 셀의 값을 기록에 더한다.
    셀마다 {hit, why, base, base_kind, status} 를 돌려주고 doc 를 고친다."""
    crit = crit or criteria("GNSS-1")
    drop, fix_min, to_fail = float(crit["cn0_drop_db"]), float(crit["fix_min_pct"]), int(crit["hits_to_fail"])
    date = date or time.strftime("%Y-%m-%d")
    hist = doc.setdefault("slot_cn0", {}).setdefault(cond, {})
    line = lambda slot: slot.split("-")[0]
    out = {}
    for s, c in sorted(cells.items()):
        cn0, rx, slot = c.get("cn0"), c.get("gps_rx"), c["slot"]
        prev = [h["cn0"] for h in hist.get(slot, []) if h.get("cell") != s and h.get("source") != source]   # 다른 셀 · 앞 회차 것만
        if prev:
            base, kind = st.median(prev), "칸 기준"
        else:
            same = [x["cn0"] for t, x in cells.items() if t != s and line(x["slot"]) == line(slot) and x.get("cn0") is not None
                    and (x.get("gps_rx") or 0) >= fix_min]
            base, kind = (st.median(same), "임시 기준") if same else (None, "기준 없음")
        if rx is None or rx < fix_min or cn0 is None:
            hit, why = True, ("수신 없음" if rx is None else f"위치 잡은 패킷 {rx:.0f} % < {fix_min:g} %")
        elif base is not None and cn0 < base - drop:
            hit, why = True, f"신호 세기 {cn0:.1f} dB-Hz 가 {kind} {base:.1f}(칸 {slot}) 보다 {base - cn0:.1f} dB 낮음(> {drop:g})"
        else:
            hit = False
            why = f"신호 세기 {cn0:.1f} dB-Hz · {kind} {base:.1f}(칸 {slot})" if base is not None else f"신호 세기 {cn0:.1f} dB-Hz · 칸 기준 없음(위치만 봄)"
        cell = doc.setdefault("cells", {}).setdefault(str(s), {})
        prev_rounds = [r for r in (cell.get("GNSS-1") or {}).get("rounds", []) if r["source"] != source]
        same_slot = any(r["slot"] == slot and r["hit"] for r in prev_rounds)
        rnd = {"source": source, "date": date, "slot": slot, "cn0": cn0, "gps_rx": rx, "base": None if base is None else round(base, 2),
               "base_kind": kind, "hit": hit, "why": why}
        rounds = prev_rounds + [rnd]
        status = gnss1_status(rounds, to_fail)
        note = " · 같은 칸에서 다시 잼 — 다른 칸에서 재야 칸 탓을 가른다" if hit and same_slot else ""
        cell["GNSS-1"] = {"status": status, "date": date, "source": source, "why": why + note, "rounds": rounds}
        out[s] = {"hit": hit, "why": why + note, "base": rnd["base"], "base_kind": kind, "status": status}
    for s, r in out.items():                   # 기준값은 이 회차 판정이 끝난 뒤에 갱신 — 같은 회차 안에서 서로를 기준으로 쓰지 않는다
        if not r["hit"]:
            hist.setdefault(cells[s]["slot"], [])
            hist[cells[s]["slot"]] = [h for h in hist[cells[s]["slot"]] if h.get("source") != source or h.get("cell") != s]
            hist[cells[s]["slot"]].append({"cell": s, "cn0": cells[s]["cn0"], "source": source, "date": date})
    return out
