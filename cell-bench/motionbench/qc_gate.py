"""단계별 거르기 — 센서 시험에서 떨어진 셀을 펌웨어 시험에 넣지 않는다(2026-11-12 사용자 결정, dynamic-bench/design/qc-tests.md 2-1절).

운영 순서는 ORDER(GNSS-1 → IMU-1 → IMU-2 → FIRMWARE-1 → FIRMWARE-2)다. 나쁜 센서 위의 펌웨어 값은 해석할 수 없으므로,
앞 시험에서 '불합격'한 셀은 뒤 시험의 판정 대상에서 뺀다(독에는 있어도 된다).

거르기 파일 — 세트마다 하나, data/qc_gate_<세트>.json:
  cells.<시리얼>.<시험 ID> = {status: 합격 · 재측정 · 불합격, date, source(근거 폴더, data/ 기준), why, rounds: [회차마다 근거]}
  GNSS-1 의 rounds 에는 회차마다 조건 · 칸 · 신호 세기 · 회차 효과 · 칸 기준값이 남는다 — 회차가 쌓이면 fit_effects 로 칸 기준값을 다시 맞출 수 있다.
칸은 "c-r" — 배치 파일 columns 의 c 번째 줄(사람이 '행'이라 부르는 것, 한 줄 6칸)의 r 번째 칸. 조건은 감쇠와 꽂은 행(예 "감쇠18_행1,3").

GNSS-1 칸 기준값(2026-11-12): 칸마다 신호가 −4.6 ~ +5.4 dB 다른데(칸 효과) 칸을 뺀 셀 차이는 ±2 dB 안이었다(1 · 3행 네 회차).
그래서 셀의 세기를 '그 회차 수준(회차 효과) + 그 칸의 몫(칸 기준값)'과 비교한다. 칸 기준값은 기준 파일의 slot_baseline 표이고,
그 조건의 표가 없으면 그 회차 같은 행의 다른 셀 중앙값을 '임시 기준'으로 쓴다.

기준 숫자는 criteria/qc_tests.json 하나에서 읽는다. 파일 읽기 · 쓰기 말고는 순수 함수다 — 장비에 닿지 않는다.
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
import time
from collections import defaultdict
from pathlib import Path

CRIT_FILE = Path(__file__).resolve().parent / "criteria" / "qc_tests.json"
ORDER = ("GNSS-1", "IMU-1", "IMU-2", "FIRMWARE-1", "FIRMWARE-2")
SENSOR = ("GNSS-1", "IMU-1", "IMU-2")
PASS, RETEST, FAIL = "합격", "재측정", "불합격"


def criteria(test: str, path: Path = CRIT_FILE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))["tests"][test]


def path(set_name: str, data: Path) -> Path:
    return Path(data) / f"qc_gate_{set_name}.json"


def load(p: Path) -> dict:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"cells": {}}


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


def slot_table(crit: dict, cond: str) -> dict[str, float]:
    """조건(감쇠 · 꽂은 행)의 칸 기준값 표 {칸: dB(중앙 0)}. 없으면 빈 표 — 그때는 임시 기준을 쓴다."""
    return dict(((crit.get("slot_baseline") or {}).get(cond) or {}).get("values") or {})


def gnss1_round(doc: dict, cells: dict[int, dict], cond: str, source: str, crit: dict | None = None,
                date: str | None = None) -> dict[int, dict]:
    """GNSS-1 한 회차의 1차 거르기. cells = {시리얼: {"slot": "c-r", "cn0": dB-Hz | None, "gps_rx": % | None}} — 판정 대상 셀만.
    걸림: 위치를 못 잡음(위치 잡은 패킷 fix_min_pct 아래 · 수신 없음) 또는 (세기 − 회차 효과 − 칸 기준값) < −cn0_drop_db.
    회차 효과는 이 회차에 위치를 잡은 셀들의 (세기 − 칸 기준값) 중앙값 하나다. 칸 기준값이 없는 칸은 같은 행 다른 셀 중앙값(임시 기준)과 비교한다.
    셀마다 {hit, why, base, base_kind, status} 를 돌려주고 doc 를 고친다."""
    crit = crit or criteria("GNSS-1")
    drop, fix_min, to_fail = float(crit["cn0_drop_db"]), float(crit["fix_min_pct"]), int(crit["hits_to_fail"])
    date = date or time.strftime("%Y-%m-%d")
    table = slot_table(crit, cond)
    fixed = lambda x: x.get("cn0") is not None and (x.get("gps_rx") or 0) >= fix_min
    lv = [x["cn0"] - table[x["slot"]] for x in cells.values() if fixed(x) and x["slot"] in table]
    level = st.median(lv) if lv else None
    line = lambda slot: slot.split("-")[0]
    out = {}
    for s, c in sorted(cells.items()):
        cn0, rx, slot = c.get("cn0"), c.get("gps_rx"), c["slot"]
        eff = table.get(slot)
        if eff is not None and level is not None:
            base, kind, basis = level + eff, "칸 기준", f"회차 {level:.1f} {eff:+.1f}(칸 {slot})"
        else:
            same = [x["cn0"] for t, x in cells.items() if t != s and line(x["slot"]) == line(slot) and fixed(x)]
            base, kind = (st.median(same), "임시 기준") if same else (None, "기준 없음")
            basis = f"같은 행 다른 셀 중앙값(칸 {slot})"
        if rx is None or rx < fix_min or cn0 is None:
            hit, why = True, ("수신 없음" if rx is None else f"위치 잡은 패킷 {rx:.0f} % < {fix_min:g} %")
        elif base is not None and cn0 < base - drop:
            hit, why = True, f"신호 세기 {cn0:.1f} dB-Hz 가 {kind} {base:.1f} = {basis} 보다 {base - cn0:.1f} dB 낮음(> {drop:g})"
        else:
            hit = False
            why = f"신호 세기 {cn0:.1f} dB-Hz · {kind} {base:.1f} = {basis}" if base is not None else f"신호 세기 {cn0:.1f} dB-Hz · 기준 없음(위치만 봄)"
        cell = doc.setdefault("cells", {}).setdefault(str(s), {})
        prev_rounds = [r for r in (cell.get("GNSS-1") or {}).get("rounds", []) if r["source"] != source]
        same_slot = any(r["slot"] == slot and r["hit"] for r in prev_rounds)
        rnd = lambda v: None if v is None else round(v, 2)
        entry = {"source": source, "date": date, "cond": cond, "slot": slot, "cn0": cn0, "gps_rx": rx, "level": rnd(level) if eff is not None else None,
                 "slot_eff": eff, "base": rnd(base), "base_kind": kind, "hit": hit, "why": why}
        rounds = prev_rounds + [entry]
        status = gnss1_status(rounds, to_fail)
        note = " · 같은 칸에서 다시 잼 — 다른 칸에서 재야 칸 탓을 가른다" if hit and same_slot else ""
        cell["GNSS-1"] = {"status": status, "date": date, "source": source, "why": why + note, "rounds": rounds}
        out[s] = {"hit": hit, "why": why + note, "base": entry["base"], "base_kind": kind, "status": status}
    return out


def fit_effects(obs: list[dict], iters: int = 30) -> tuple[dict, dict, dict]:
    """세기 = 회차 효과 + 칸 효과 + 셀 효과 를 중앙값으로 번갈아 맞춘다(2026-11-12 1 · 3행 네 회차를 나눈 방식 그대로).
    obs = [{"round", "slot", "cell", "cn0"}] — 위치를 잡은 관측만. 칸 · 셀 효과는 중앙을 0 으로 두고 그 몫을 회차 효과로 옮긴다.
    칸과 셀을 가르려면 같은 셀이 여러 칸에, 같은 칸에 여러 셀이 앉아 있어야 한다. 돌려주는 것: (회차 효과, 칸 효과, 셀 효과)."""
    effs = {"round": defaultdict(float), "slot": defaultdict(float), "cell": defaultdict(float)}
    for _ in range(iters):
        for key, eff in effs.items():
            g = defaultdict(list)
            for o in obs:
                g[o[key]].append(o["cn0"] - sum(e[o[k]] for k, e in effs.items() if k != key))
            for kk, v in g.items():
                eff[kk] = st.median(v)
        for key in ("cell", "slot"):
            m = st.median(effs[key].values())
            for kk in effs[key]:
                effs[key][kk] -= m
            for kk in effs["round"]:
                effs["round"][kk] += m
    return dict(effs["round"]), dict(effs["slot"]), dict(effs["cell"])


def gnss1_obs(doc: dict, cond: str, fix_min: float) -> list[dict]:
    """거르기 파일에 쌓인 GNSS-1 회차 기록 → fit_effects 의 관측(그 조건 · 위치를 잡은 회차만)."""
    return [{"round": r["source"], "slot": r["slot"], "cell": int(s), "cn0": r["cn0"]}
            for s, per in (doc.get("cells") or {}).items() for r in (per.get("GNSS-1") or {}).get("rounds", [])
            if r.get("cond") == cond and r.get("cn0") is not None and (r.get("gps_rx") or 0) >= fix_min]


if __name__ == "__main__":
    # 칸 기준값 다시 맞추기: python -X utf8 -m motionbench.qc_gate <거르기 파일> [조건, 기본 감쇠18_행1,3]
    # 기준 파일은 고치지 않는다 — 나온 값을 보고 사람이 criteria/qc_tests.json 의 slot_baseline 을 고친다.
    gp, cond = Path(sys.argv[1]), (sys.argv[2] if len(sys.argv) > 2 else "감쇠18_행1,3")
    obs = gnss1_obs(load(gp), cond, float(criteria("GNSS-1")["fix_min_pct"]))
    rnd_e, slot_e, cell_e = fit_effects(obs)
    print(f"{gp.name} · 조건 {cond} · 관측 {len(obs)}개 · 회차 {len(rnd_e)} · 칸 {len(slot_e)} · 셀 {len(cell_e)}")
    print("칸 효과(dB, 중앙 0):", json.dumps({k: round(v, 1) for k, v in sorted(slot_e.items())}, ensure_ascii=False))
    print("회차 효과:", json.dumps({k: round(v, 1) for k, v in rnd_e.items()}, ensure_ascii=False))
    print("셀 효과:", json.dumps({str(k): round(v, 1) for k, v in sorted(cell_e.items())}, ensure_ascii=False))
