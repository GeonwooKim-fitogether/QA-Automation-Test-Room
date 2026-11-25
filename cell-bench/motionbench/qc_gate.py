"""단계별 거르기 — 센서 시험에서 떨어진 셀을 펌웨어 시험에 넣지 않는다(2026-11-12 사용자 결정, dynamic-bench/design/qc-tests.md 2-1절).

운영 순서는 ORDER(GNSS-STATIC → GNSS-MOVE → IMU-STATIC → IMU-MOVE → IMU-FW → GNSS-FW)다(2026-11-12 여섯 시험 결정 — 센서 시험 넷, 펌웨어 시험 둘).
보여 주는 차례(qc_ids.TESTS — GNSS 쪽 셋, IMU 쪽 셋)와 다르다: 펌웨어 시험은 두 센서의 시험을 모두 거친 뒤에 한다.
옛 ID(2026-11-12 의 GNSS-1 · IMU-2 …)로 쓰인 거르기 파일은 load 가 지금 ID 로 바꿔 읽는다(qc_ids.RENAMED). 나쁜 센서 위의 펌웨어 값은 해석할 수 없으므로,
뒤 시험에는 앞 시험을 '합격'한 셀만 올린다(GO_ON, 2026-11-25 — 그 전에는 '불합격'만 뺐다). 뺀 셀은 독에는 있어도 된다.

상태 넷(2026-11-25): 합격 · 재측정 · 불합격 · 판정 불가. '판정 불가'는 셀 탓도 셀 덕도 아니다 — 기종 · 펌웨어 형식을 모르거나(기종 확인 필요,
motionbench/cell_models.py) 같은 셀의 실시간 값과 셀 기록 값이 어긋나(읽기 불일치, read_mismatch) 그 값을 믿을 수 없는 경우다.
판정 불가 셀은 시험은 같이 돌려 기록은 남기되, 그 값을 다른 셀의 기준 계산(회차 수준 · 같은 행 중앙값 · 위성 수 비교 대상 · 칸 기준 다시 맞추기)에 섞지 않는다.
통신 실패로 못 읽은 것(수신 없음)은 판정 불가가 아니라 걸림 → 재측정이다 — 통신이 끊기는 것만으로 판정을 피하지 못하게.
셀 번호를 안 쓴 셀(번호 1 등, cell_models.NO_SERIAL)은 거르기 파일에 적지 않는다 — 셀끼리 구별할 수 없어 다른 셀의 기록과 섞인다.

거르기 파일 — 세트마다 하나, data/qc_gate_<세트>.json:
  cells.<시리얼>.<시험 ID> = {status: 합격 · 재측정 · 불합격 · 판정 불가, date, source(근거 폴더, data/ 기준), why, rounds: [회차마다 근거]}
  GNSS-STATIC 의 rounds 에는 회차마다 조건 · 칸 · 신호 세기 · 회차 효과 · 칸 기준값 · 항목별 걸림(checks)이 남는다 — 회차가 쌓이면 fit_effects 로 칸 기준값을 다시 맞출 수 있다.
칸은 "c-r" — 배치 파일 columns 의 c 번째 줄(사람이 '행'이라 부르는 것, 한 줄 6칸)의 r 번째 칸. 조건은 감쇠와 꽂은 행(예 "감쇠18_행1,3").

GNSS-STATIC 칸 기준값(2026-11-12): 칸마다 신호가 −4.6 ~ +5.4 dB 다른데(칸 효과) 칸을 뺀 셀 차이는 ±2 dB 안이었다(1 · 3행 네 회차).
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motionbench import cell_models, qc_ids  # noqa: E402

CRIT_FILE = Path(__file__).resolve().parent / "criteria" / "qc_tests.json"
ORDER = ("GNSS-STATIC", "GNSS-MOVE", "IMU-STATIC", "IMU-MOVE", "IMU-FW", "GNSS-FW")
SENSOR = ("GNSS-STATIC", "GNSS-MOVE", "IMU-STATIC", "IMU-MOVE")
PASS, RETEST, FAIL, UNJUDGED = "합격", "재측정", "불합격", "판정 불가"
STATUSES = (PASS, RETEST, FAIL, UNJUDGED)
# 뒤 시험에 오르는 상태. 2026-11-25 '합격한 셀만'(기준 파일 GNSS-MOVE rule — GNSS-STATIC 합격 셀만 GNSS-MOVE 판정에 오른다).
# '재측정' 셀도 올릴지는 사용자 확인 대기 — 올리기로 하면 (PASS, RETEST) 로 바꾼다.
GO_ON = (PASS,)


def criteria(test: str, path: Path = CRIT_FILE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))["tests"][test]


def path(set_name: str, data: Path) -> Path:
    return Path(data) / f"qc_gate_{set_name}.json"


def load(p: Path) -> dict:
    """거르기 파일을 읽는다. 옛 ID 열쇠(cells.<시리얼>.GNSS-1 등)는 지금 ID 로 바꿔 돌려준다 — 파일은 다음 save 때 새 열쇠로 쓰인다."""
    try:
        doc = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"cells": {}}
    for s, per in (doc.get("cells") or {}).items():
        doc["cells"][s] = {qc_ids.current(t): v for t, v in per.items()}
    return doc


def save(p: Path, doc: dict) -> None:
    doc["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = Path(p).with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def excluded(doc: dict, test: str, unjudged: dict[int, str] | None = None) -> dict[int, str]:
    """test 의 판정 대상에서 뺄 셀 → {시리얼: 이유}.
    - 앞 시험(ORDER 에서 test 앞)의 기록이 있는데 상태가 GO_ON(합격)이 아닌 셀 — 불합격 · 재측정 · 판정 불가.
    - 앞 시험 기록이 아예 없는 셀은 빼지 않는다 — 그 시험을 아직 안 했거나(GNSS-STATIC 은 한 번에 12대), 거르기 파일에 상태를 적지 않는
      시험이다(GNSS-MOVE 는 셀 최종 판정 규칙이 미정이라 적지 않는다). 없다고 빼면 뒤 시험이 모두 막힌다.
    - unjudged: 이번에 판정할 수 없는 셀 {시리얼: 이유} — 기종 · 펌웨어 형식을 모르는 셀(cell_models.unjudgeable). 앞 시험이 없어도 뺀다."""
    before = ORDER[:ORDER.index(test)]
    out = {}
    for s, per in (doc.get("cells") or {}).items():
        bad = [f"{t} {per[t].get('status')}({per[t].get('date')}, {per[t].get('source')})" for t in before
               if per.get(t) and per[t].get("status") not in GO_ON]
        if bad:
            out[int(s)] = " · ".join(bad)
    for s, why in (unjudged or {}).items():
        out[int(s)] = f"{UNJUDGED}({why})" + (f" · {out[int(s)]}" if int(s) in out else "")
    return out


def record(doc: dict, serial: int, test: str, status: str, source: str, why: str, date: str | None = None, **extra) -> None:
    """한 시험의 결과를 셀 칸에 적는다. 같은 근거 폴더로 다시 적으면 바꿔 쓴다(판정을 다시 돌려도 쌓이지 않는다).
    셀 번호를 안 쓴 셀(cell_models.NO_SERIAL)은 적지 않는다."""
    if int(serial) in cell_models.NO_SERIAL:
        return
    cell = doc.setdefault("cells", {}).setdefault(str(serial), {})
    hist = [h for h in (cell.get(test) or {}).get("history", []) if h.get("source") != source]
    entry = {"status": status, "date": date or time.strftime("%Y-%m-%d"), "source": source, "why": why} | extra
    cell[test] = entry | {"history": hist + [{k: v for k, v in entry.items()}]}


def gnss_static_status(rounds: list[dict], hits_to_fail: int) -> str:
    """GNSS-STATIC 회차 기록 → 지금 상태. 판정한 회차 중 걸린 회차가 hits_to_fail 번이면 불합격, 아니면 마지막 회차가 판정 불가면 판정 불가,
    마지막 회차가 걸렸으면 재측정, 아니면 합격."""
    judged = [r for r in rounds if not r.get("unjudged")]          # 판정 불가 회차는 걸림을 세지 않는다
    if sum(1 for r in judged if r["hit"]) >= hits_to_fail:
        return FAIL
    if rounds and rounds[-1].get("unjudged"):
        return UNJUDGED
    return RETEST if judged and judged[-1]["hit"] else PASS


def slot_table(crit: dict, cond: str) -> dict[str, float]:
    """조건(감쇠 · 꽂은 행)의 칸 기준값 표 {칸: dB(중앙 0)}. 없으면 빈 표 — 그때는 임시 기준을 쓴다."""
    return dict(((crit.get("slot_baseline") or {}).get(cond) or {}).get("values") or {})


def gnss_static_round(doc: dict, cells: dict[int, dict], cond: str, source: str, crit: dict | None = None,
                      date: str | None = None, unjudged: dict[int, str] | None = None,
                      read: dict[int, dict] | None = None) -> dict[int, dict]:
    """GNSS-STATIC 한 회차의 1차 거르기. cells = {시리얼: {"slot": "c-r", "cn0": dB-Hz | None, "gps_rx": % | None, "sats": 개 | None, "kind": 셀 종류}} — 독에 꽂은 셀.
    항목마다 따로 본다(checks) — 하나가 다른 하나를 가리지 않게(2026-11-25):
      위치      — 걸림: 위치 잡은 패킷 fix_min_pct 아래 · 수신 없음(통신 실패도 여기 — 판정 불가가 아니라 재측정).
      신호 세기 — 걸림: (세기 − 회차 효과 − 칸 기준값) < −cn0_drop_db. 회차 효과는 이 회차에 위치를 잡은 판정 대상 셀들의 (세기 − 칸 기준값) 중앙값.
                  칸 기준값이 없는 칸은 같은 행 다른 셀 중앙값(임시 기준)과 비교한다.
      위성 수   — 살펴봄(버전 0.3, 2026-11-25): 같은 회차 · 같은 종류 다른 셀 중앙값 대비 % 를 기록만 한다. 버전 0.2 의 sats_min_pct 걸림은
                  잘못 읽은 값(시료 CLBY-2A 의 위성 수를 Y 형식으로 해독)이 유일한 근거였어 합불에서 뺐다.
    unjudged = {시리얼: 이유} — 판정할 수 없는 셀(기종 확인 필요 · 읽기 불일치). 상태 '판정 불가'로 적고 값은 남기되,
      회차 수준 · 같은 행 중앙값 · 위성 수 비교 대상에 넣지 않는다. 셀 번호를 안 쓴 셀은 넘기지 않아도 판정 불가로 보고 거르기 파일에 적지 않는다.
    read = {시리얼: 두 기록 대조 결과}(read_mismatch) — 회차 기록에 함께 남긴다.
    같은 source 로 다시 부르면 그 회차를 바꿔 쓴다. 셀마다 {hit, why, base, base_kind, status, checks, unjudged} 를 돌려주고 doc 를 고친다."""
    crit = crit or criteria("GNSS-STATIC")
    drop, fix_min, to_fail = float(crit["cn0_drop_db"]), float(crit["fix_min_pct"]), int(crit["hits_to_fail"])
    date = date or time.strftime("%Y-%m-%d")
    unjudged = {int(k): v for k, v in (unjudged or {}).items()}
    for s in cells:
        if s in cell_models.NO_SERIAL and s not in unjudged:
            unjudged[s] = f"{cell_models.NEED_MODEL} — 셀 번호를 안 쓴 셀(번호 {s})"
    read = read or {}
    table = slot_table(crit, cond)
    judged = {s: c for s, c in cells.items() if s not in unjudged}           # 기준 계산에 쓰는 셀
    fixed = lambda x: x.get("cn0") is not None and (x.get("gps_rx") or 0) >= fix_min
    lv = [x["cn0"] - table[x["slot"]] for x in judged.values() if fixed(x) and x["slot"] in table]
    level = st.median(lv) if lv else None
    line = lambda slot: slot.split("-")[0]
    out = {}
    for s, c in sorted(cells.items()):
        cn0, rx, slot = c.get("cn0"), c.get("gps_rx"), c["slot"]
        eff = table.get(slot)
        if eff is not None and level is not None:
            base, kind, basis = level + eff, "칸 기준", f"회차 {level:.1f} {eff:+.1f}(칸 {slot})"
        else:
            same = [x["cn0"] for t, x in judged.items() if t != s and line(x["slot"]) == line(slot) and fixed(x)]
            base, kind = (st.median(same), "임시 기준") if same else (None, "기준 없음")
            basis = f"같은 행 다른 셀 중앙값(칸 {slot})"
        sats, kind_c = c.get("sats"), c.get("kind", "")
        peers = [x["sats"] for t, x in judged.items() if t != s and x.get("kind", "") == kind_c and fixed(x) and x.get("sats")]
        sats_pct = 100.0 * sats / st.median(peers) if sats is not None and peers else None
        checks = {
            "위치": {"hit": True, "why": "수신 없음"} if rx is None else
                    {"hit": rx < fix_min or cn0 is None, "why": f"위치 잡은 패킷 {rx:.0f} %" + (f" < {fix_min:g} %" if rx < fix_min else "")},
            "신호 세기": {"hit": False, "why": "값 없음"} if cn0 is None else
                        {"hit": False, "why": f"{cn0:.1f} dB-Hz · 기준 없음"} if base is None else
                        {"hit": cn0 < base - drop, "why": f"{cn0:.1f} dB-Hz 가 {kind} {base:.1f} = {basis} 보다 {base - cn0:.1f} dB 낮음(> {drop:g})"
                         if cn0 < base - drop else f"{cn0:.1f} dB-Hz · {kind} {base:.1f} = {basis}"},
            "위성 수": {"hit": False, "observe": True, "why": "값 없음" if sats is None else f"{sats:g}개"
                       + (f" = 같은 회차 같은 종류 다른 셀 중앙 {st.median(peers):g}개의 {sats_pct:.0f} %" if sats_pct is not None else "")},
        }
        items = " · ".join(f"{k} {'살펴봄' if v.get('observe') else '걸림' if v['hit'] else '통과'}: {v['why']}" for k, v in checks.items())
        if s in unjudged:
            hit, why = None, f"{UNJUDGED}({unjudged[s]}) — 참고 {items}"
        else:
            hit, why = any(v["hit"] for v in checks.values()), items
        cell = (doc.get("cells") or {}).get(str(s), {})
        prev_rounds = [r for r in (cell.get("GNSS-STATIC") or {}).get("rounds", []) if r["source"] != source]
        same_slot = any(r["slot"] == slot and r["hit"] for r in prev_rounds)
        rnd = lambda v: None if v is None else round(v, 2)
        entry = {"source": source, "date": date, "cond": cond, "slot": slot, "cn0": cn0, "gps_rx": rx, "sats": sats, "sats_pct": rnd(sats_pct), "level": rnd(level) if eff is not None else None,
                 "slot_eff": eff, "base": rnd(base), "base_kind": kind, "hit": hit, "why": why, "checks": checks,
                 "unjudged": unjudged.get(s), "read": read.get(s)}
        rounds = prev_rounds + [entry]
        status = gnss_static_status(rounds, to_fail)
        note = " · 같은 칸에서 다시 잼 — 다른 칸에서 재야 칸 탓을 가른다" if hit and same_slot else ""
        if s not in cell_models.NO_SERIAL:
            doc.setdefault("cells", {}).setdefault(str(s), {})["GNSS-STATIC"] = {"status": status, "date": date, "source": source, "why": why + note, "rounds": rounds}
        out[s] = {"hit": hit, "why": why + note, "base": entry["base"], "base_kind": kind, "status": status, "checks": checks,
                  "unjudged": unjudged.get(s)}
    return out


def read_mismatch(live: dict[int, dict], ftg: dict[int, dict], crit: dict | None = None) -> tuple[dict[int, str], dict[int, dict]]:
    """같은 셀의 두 기록 대조(2026-11-25) — 실시간 값(해독기가 읽은 것)과 셀 기록 값(셀이 파일에 적은 것)이 어긋나면 해독을 믿을 수 없다.
    live = {셀: {"sats", "cn0", "gps_rx"}} — 판정 구간의 실시간 패킷(gnss_static_run 의 summary.json cn0).
    ftg  = {셀: {"sats", "cn0", "fix_pct"}} — 같은 구간의 셀 기록(gnss_static_judge.window_items). 값이 None 인 항목은 견주지 않는다.
    허용 차는 기준 파일 read_check(위성 수 개 · 신호 세기 dB · 위치 비율 %p). 하나라도 넘으면 '판정 불가(읽기 불일치)'.
    표에 있는 기종의 셀이 위성을 적게 쓰는 것은 두 기록이 같이 적게 나오므로 여기서 걸리지 않는다 — 그것은 관측이고 위성 수 살펴봄 값으로 남는다.
    셀 기록이 없거나 그 구간이 없는 셀은 대조 기록에 '대조 못 함'만 적고 판정은 바꾸지 않는다.
    돌려주는 것: ({셀: 판정 불가 이유}, {셀: 대조 기록})."""
    tol = (crit or criteria("GNSS-STATIC"))["read_check"]
    pairs = (("위성 수", "sats", "sats", float(tol["sats_max_diff"]), "개"),
             ("신호 세기", "cn0", "cn0", float(tol["cn0_max_diff_db"]), " dB"),
             ("위치 비율", "gps_rx", "fix_pct", float(tol["fix_max_diff_pct"]), " %p"))
    bad, rec = {}, {}
    for s, lv in sorted(live.items()):
        fg = ftg.get(s)
        if not fg:
            rec[s] = {"ok": None, "why": "셀 기록 없음 — 대조 못 함"}
            continue
        diffs, over = {}, []
        for name, kl, kf, lim, unit in pairs:
            a, b = lv.get(kl), fg.get(kf)
            if a is None or b is None:
                continue
            diffs[name] = round(float(a) - float(b), 2)
            if abs(a - b) > lim:
                over.append(f"{name} 실시간 {a:g} · 셀 기록 {b:g}(차 {abs(a - b):g}{unit} > {lim:g}{unit})")
        if not diffs:
            rec[s] = {"ok": None, "why": "견줄 값 없음 — 대조 못 함"}
        elif over:
            rec[s] = {"ok": False, "diff": diffs, "why": " · ".join(over)}
            bad[s] = "읽기 불일치 — " + " · ".join(over)
        else:
            rec[s] = {"ok": True, "diff": diffs, "why": "두 기록 같음(허용 차 안)"}
    return bad, rec


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


def gnss_static_obs(doc: dict, cond: str, fix_min: float) -> list[dict]:
    """거르기 파일에 쌓인 GNSS-STATIC 회차 기록 → fit_effects 의 관측(그 조건 · 위치를 잡은 회차만). 판정 불가 회차는 넣지 않는다."""
    return [{"round": r["source"], "slot": r["slot"], "cell": int(s), "cn0": r["cn0"]}
            for s, per in (doc.get("cells") or {}).items() for r in (per.get("GNSS-STATIC") or {}).get("rounds", [])
            if r.get("cond") == cond and r.get("cn0") is not None and (r.get("gps_rx") or 0) >= fix_min and not r.get("unjudged")]


if __name__ == "__main__":
    # 칸 기준값 다시 맞추기: python -X utf8 -m motionbench.qc_gate <거르기 파일> [조건, 기본 감쇠18_행1,3]
    # 기준 파일은 고치지 않는다 — 나온 값을 보고 사람이 criteria/qc_tests.json 의 slot_baseline 을 고친다.
    gp, cond = Path(sys.argv[1]), (sys.argv[2] if len(sys.argv) > 2 else "감쇠18_행1,3")
    obs = gnss_static_obs(load(gp), cond, float(criteria("GNSS-STATIC")["fix_min_pct"]))
    rnd_e, slot_e, cell_e = fit_effects(obs)
    print(f"{gp.name} · 조건 {cond} · 관측 {len(obs)}개 · 회차 {len(rnd_e)} · 칸 {len(slot_e)} · 셀 {len(cell_e)}")
    print("칸 효과(dB, 중앙 0):", json.dumps({k: round(v, 1) for k, v in sorted(slot_e.items())}, ensure_ascii=False))
    print("회차 효과:", json.dumps({k: round(v, 1) for k, v in rnd_e.items()}, ensure_ascii=False))
    print("셀 효과:", json.dumps({str(k): round(v, 1) for k, v in sorted(cell_e.items())}, ensure_ascii=False))
