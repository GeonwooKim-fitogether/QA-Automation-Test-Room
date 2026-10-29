"""레일 시험 결과판 표본 생성기 — 이 폴더의 가짜 판정 파일(protocol_<시각>.json, _sample: true)을 만든다.

  python tests/fixtures/rail/make_samples.py                 # 이 폴더의 가짜 표본을 다시 쓴다
  python tests/fixtures/rail/make_samples.py --js <파일>      # 시안 화면용 rail-data.js(window.RAIL_RAW)도 같은 표본으로 쓴다

서버(serve_board.py 의 /api/rail/*) · 검사(tests/test_rail_board.py) · 화면 시안이 같은 표본을 쓰게 하려고 하나로 모았다.
디자이너가 시안을 그릴 때 쓴 생성기(rail-design/_tools/gen_samples.py)를 옮겨 와 아래를 고쳤다.
  · 표본 표시는 _sample: true (서버 요약이 sample: true 로 넘긴다)
  · criteria.<id>.kind = 합불 · 살펴봄 · 정보용 · 보류 (7-6 의 7). 셀 항목 verdict 는 이 갈래를 따른다 —
    합불 pass · fail · hold, 살펴봄 pass · flag · hold, 정보용 늘 info, 보류 늘 hold. z 는 살펴봄 항목에만 숫자
  · sog_band 경계 criteria.sog_band.value = [b1, b2, b3, b4] 또는 null, kind 정보용 (7-6 의 8)
  · 독 배치는 실제 데이터 폴더가 아니라 이 폴더의 v1 실제 파일의 grid 에서 읽는다(dock_layout.json 의 Y 세트와 같다)

칸 이름의 정본은 dynamic-bench/design/qc-protocol-v4.md 7-6. 값은 그럴듯하게만 맞춘 가짜다 — 실측이 아니다.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
V1_NAME = "protocol_20261029-155408.json"          # 실제 v1 시험 파일 — 읽기만 한다
GRID = json.loads((HERE / V1_NAME).read_text(encoding="utf-8"))["grid"]   # 6행 × 4열, 위에서부터
COLS = [[row[c] for row in GRID] for c in range(len(GRID[0]))]            # 열마다 위에서부터
SERIALS = [s for col in COLS for s in col]
ACC = [20, 50, 100, 200, 500]
BANDS = ["0–7", "7–15", "15–20", "20–25", "25 이상"]
NOTE = "가짜 표본 — 결과판 화면 · 검사용(tests/fixtures/rail/make_samples.py). 실제 시험이 아니다. 실제 데이터 폴더로 옮기지 않는다."
HOLD_LOCK = "300초 안에 장면 고정 못 함 (파트 A)"
HOLD_FTG = "FTG 못 받음 — 펌웨어 · IMU · 위치 · 속도 재료 없음"
REPEATER = "중계기 조건 — 셀은 옥상 안테나를 잰다(정보용)"

# 항목마다 갈래 — 4-1 · 4-2. 기준이 아직 없는 합불 후보는 '미정 — 첫 실측 뒤' 동안 살펴봄으로 돈다(4절 머리말)
KIND = {"rx": "합불", "clock": "합불", "fw": "합불", "complete": "합불", "imu": "합불",
        "gate_on": "살펴봄", "gate_off": "살펴봄", "gate_share": "살펴봄", "gps_rx": "살펴봄", "gps_move": "살펴봄",
        "lock": "살펴봄", "pos_h": "살펴봄", "cn0_sim": "살펴봄",
        "sog_band": "정보용", "pos_move": "정보용", "sog_lag": "정보용", "turn": "정보용"}
CRIT = {"rx": ("≤ 1 %", "확정"), "clock": ("무리 중앙값 ± 20 ms", "제안값"), "fw": ("세트 다수 버전", "확정"),
        "complete": ("≥ 99 %", "제안값"), "imu": ([0.85, 1.15], "제안값"),
        "gate_on": ("|z| ≥ 3", "미정"), "gate_off": ("|z| ≥ 3", "미정"), "gate_share": ("|z| ≥ 3", "미정"),
        "gps_rx": ("|z| ≥ 3", "제안값"), "gps_move": ("|z| ≥ 3 — 기준 미정, 첫 실측 뒤", "미정"),
        "lock": ("|z| ≥ 3", "미정"), "pos_h": ("|z| ≥ 3", "미정"), "cn0_sim": ("|z| ≥ 3", "미정"),
        "pos_move": (None, "미정"), "sog_lag": (None, "확정"), "turn": (None, "확정")}


def r2(x, n=2):
    return round(x, n)


def it(value, unit, verdict, z=None, basis=None):
    return {"value": value, "unit": unit, "verdict": verdict, "z": z, "basis": basis}


def stats(vals):
    v = sorted(x for x in vals if isinstance(x, (int, float)) and not isinstance(x, bool))
    if not v:
        return None
    m = len(v)
    med = v[m // 2] if m % 2 else (v[m // 2 - 1] + v[m // 2]) / 2
    return {"med": r2(med, 3), "min": r2(v[0], 3), "max": r2(v[-1], 3), "n": m}


def dock_of(s):
    for c, col in enumerate(COLS):
        if s in col:
            return {"col": c + 1, "row": col.index(s) + 1}


def criteria(ids, **over):
    """항목 id 마다 {value, kind, status}. over 로 그 시험만 다른 것을 바꾼다(예: sog_band 경계, 중계기의 gps_rx)."""
    out = {}
    for k in ids:
        if k == "sog_band":
            out[k] = {"value": None, "unit": "mm/s", "kind": "정보용", "status": "미정"}
        else:
            v, st = CRIT[k]
            out[k] = {"value": v, "kind": KIND[k], "status": st}
        out[k].update(over.get(k, {}))
    return out


def finish(items, overall, reasons):
    """셀 종합 — flags 는 verdict 가 flag 인 항목 id 를 그대로 모은다(판정을 새로 하지 않는다)."""
    return {"overall": overall, "flags": [k for k, v in items.items() if v["verdict"] == "flag"], "reasons": reasons}


def accel_vals(rng, accels, imu_low=None, gate_dead=False):
    imu, on, off, share = {}, {}, {}, {}
    for a in accels:
        k = str(a)
        imu[k] = r2(rng.gauss(1.0, 0.035 if a >= 50 else 0.07), 3)
        on[k] = r2(max(0.08, rng.gauss(1.3 if a == 20 else 0.6 if a == 50 else 0.22, 0.12)), 2)
        off[k] = r2(max(0.2, rng.gauss(0.48, 0.06)), 2)
        share[k] = r2(min(100.0, rng.gauss(99.3, 0.6)), 1)
    if imu_low:
        imu["500"] = imu_low
    if gate_dead:
        share = {k: 0.0 for k in share}
        on = {k: None for k in on}
        off = {k: None for k in off}
    return imu, on, off, share


def common_items(rng, clock_fail=False, rx_fail=False, imu_low=None, imu_fail=False, gate_dead=False, accels=ACC):
    clock = 24.1 if clock_fail else r2(rng.gauss(0, 5), 1)
    rx = 1.42 if rx_fail else r2(abs(rng.gauss(0.08, 0.06)), 3)
    imu, on, off, share = accel_vals(rng, accels, imu_low, gate_dead)

    def zf():
        return r2(rng.gauss(0, 0.7), 1)
    return {
        "rx": it(rx, "%", "fail" if rx_fail else "pass", basis="≤ 1 %"),
        "clock": it(clock, "ms", "fail" if clock_fail else "pass", basis="무리 중앙값 ± 20 ms"),
        "fw": it("2.3.1", None, "pass", basis="세트 다수 버전 2.3.1"),
        "complete": it(r2(min(100, rng.gauss(99.7, 0.2)), 1), "%", "pass", basis="≥ 99 %"),
        "imu": it(imu, "비율", "fail" if imu_fail else "pass", basis="0.85 – 1.15 (가속도 단계마다)"),
        "gate_on": it(on, "s", "flag" if gate_dead else "pass", -9.8 if gate_dead else zf(), "|z| ≥ 3"),
        "gate_off": it(off, "s", "flag" if gate_dead else "pass", -9.8 if gate_dead else zf(), "|z| ≥ 3"),
        "gate_share": it(share, "%", "flag" if gate_dead else "pass", -9.8 if gate_dead else zf(), "|z| ≥ 3"),
        "gps_rx": it(r2(min(100, rng.gauss(99.6, 0.3)), 1), "%", "pass", zf(), "|z| ≥ 3"),
    }


def part_a_items(rng, s, pos_h_odd=None, cn0_odd=None, hold_why=None):
    """파트 A 항목 — lock · pos_h · cn0_sim(살펴봄), sog_band · pos_move · sog_lag · turn(정보용).
    hold_why 가 있으면 재료가 없다: 살펴봄은 hold, 정보용은 info + 값 null(정보용은 늘 info — 7-6 의 7)."""
    def zf():
        return r2(rng.gauss(0, 0.7), 1)
    if hold_why:
        items = {k: it(None, u, "hold", None, hold_why) for k, u in (("lock", "s"), ("pos_h", "m"), ("cn0_sim", "dBHz"))}
        items.update({k: it(None, u, "info", None, hold_why) for k, u in (("pos_move", "m"), ("sog_lag", "s"), ("turn", "s"))})
        items["sog_band"] = it([{"band": b, "rmsd_mm_s": None, "ratio": None, "n": 0} for b in BANDS], "mm/s", "info", None, hold_why)
        return items
    cn0 = cn0_odd[0] if cn0_odd else r2(rng.gauss(41, 0.8), 1)
    pos_h = pos_h_odd[0] if pos_h_odd else r2(abs(rng.gauss(0.55, 0.12)), 2)
    items = {
        "lock": it(r2(rng.gauss(48, 9), 0), "s", "pass", zf(), "|z| ≥ 3 · 고정 종류 A"),
        "pos_h": it(pos_h, "m", "flag" if pos_h_odd else "pass", pos_h_odd[1] if pos_h_odd else zf(), "|z| ≥ 3 · 중앙값"),
        "cn0_sim": it(cn0, "dBHz", "flag" if cn0_odd else "pass", cn0_odd[1] if cn0_odd else zf(), "|z| ≥ 3 · −130 dBm"),
        "pos_move": it(r2(abs(rng.gauss(0.9, 0.2)), 2), "m", "info"),
        "sog_lag": it(r2(rng.gauss(0.32, 0.04), 2), "s", "info"),
        "turn": it(r2(rng.gauss(0.45, 0.06), 2), "s", "info"),
    }
    bands = []
    for i, b in enumerate(BANDS):
        rm = 520.0 if (s == 10699 and i == 4) else abs(rng.gauss(95 + 22 * i, 22))
        bands.append({"band": b, "rmsd_mm_s": r2(rm, 1), "ratio": r2(rng.gauss(0.995 - 0.004 * i, 0.012), 3), "n": 200})
    items["sog_band"] = it(bands, "mm/s", "info", None, "정보용 — 등급 경계(criteria.sog_band.value)로 1~5등급")
    if s != 10699:                 # 시안 표본과 숫자를 같게 — 예전 sog_band 의 z 자리 난수를 버린다(정보용이라 z 는 null)
        rng.gauss(0, 0.7)
    return items


def group_of(cells, accels):
    def vals(k):
        return [c["items"][k]["value"] for c in cells.values() if k in c["items"]]
    g = {}
    for k in ("rx", "clock", "complete", "gps_rx", "lock", "pos_h", "cn0_sim", "pos_move", "sog_lag", "turn"):
        st = stats(vals(k))
        if st:
            g[k] = st
    for k in ("imu", "gate_on", "gate_off", "gate_share"):
        if vals(k):
            g[k] = {str(a): stats([(v or {}).get(str(a)) for v in vals(k)]) for a in accels}
    if any("sog_band" in c["items"] for c in cells.values()):
        g["sog_band"] = {b: {"rmsd_mm_s": stats([v[i]["rmsd_mm_s"] for v in vals("sog_band")]),
                             "ratio": stats([v[i]["ratio"] for v in vals("sog_band")])} for i, b in enumerate(BANDS)}
    if any("gps_move" in c["items"] for c in cells.values()):
        g["gps_move"] = {"v_ratio": {v: stats([x["v_ratio"][v] for x in vals("gps_move")]) for v in ("100", "300", "500")}}
    return g


def sim_conditions(**over):
    c = {"protocol": "v4", "sky": "simulator", "part": ["A", "C"], "site": "lab", "repeater": "off",
         "corrections": "off", "set": "Y", "cell_type": "CLBY", "fw": "2.3.1", "dock": "DKPY 4×6",
         "scene": {"name": "stadium-01", "mode": "file", "file": "scene_stadium-01.csv", "t0_gps": "미확인",
                   "origin": {"lat": 37.4979, "lon": 127.0276, "h": 38.0}},
         "simulator": {"model": "LabSat 3 Wideband", "serial": "미확인", "firmware": "미확인",
                       "constellations": ["GPS", "Galileo", "BeiDou"]},
         "rf": {"method": "radiated", "level_dbm": -130, "antenna": "미확인", "shield": "없음"},
         "speeds_mm_s": [], "accels_mm_s2": ACC, "still_s": 300, "dwell_s": 5, "judge_commit": "a1f3c9e"}
    c.update(over)
    return c


def doc(stamp, cond, steps, rig, cells, crit, accels):
    return {"_sample": True, "_note": NOTE, "stamp": stamp, "conditions": cond, "steps": steps, "rig": rig,
            "cells": cells, "group": group_of(cells, accels), "criteria": crit, "grid": GRID}


def a_steps():
    return [{"key": f"A{i}"} for i in range(6)]


def c_steps(accels):
    return [{"key": f"C{i}", "v_mm_s": 100, "a_mm_s2": a} for i, a in enumerate(accels)]


# ---- 시험마다 ------------------------------------------------------------------------------------------------------------
def build_v4_sim():
    """20261029-172000 — 시뮬레이터 · 파트 A · C · 실험실. 합격 20 · 불합격 2(clock · imu) · 보류 2(장면 고정 못 함),
    살펴봄 벗어남(gate_* · pos_h · cn0_sim), sog_band 등급 경계 4개(제안값)."""
    rng = random.Random(4101)
    cells = {}
    for s in SERIALS:
        items = common_items(rng, clock_fail=s == 11620, imu_low=0.78 if s == 10690 else None, imu_fail=s == 10690,
                             gate_dead=s == 10681)
        hold = s in (10693, 10697)
        items.update(part_a_items(rng, s, pos_h_odd=(1.32, 3.1) if s == 10682 else None,
                                  cn0_odd=(36.5, -3.2) if s == 10686 else None, hold_why=HOLD_LOCK if hold else None))
        reasons = []
        if s == 11620:
            reasons.append("clock 24.1 ms — 무리 중앙값 ± 20 ms 밖")
        if s == 10690:
            reasons.append("imu 500 mm/s² 0.78 — 0.85 – 1.15 밖")
        overall = "hold" if hold else "fail" if reasons else "pass"
        cells[str(s)] = {"dock": dock_of(s), "items": items, "verdict": finish(items, overall, [HOLD_LOCK] if hold else reasons)}
    crit = criteria(next(iter(cells.values()))["items"], sog_band={"value": [150, 300, 450, 600], "status": "제안값"})
    return doc("20261029-172000", sim_conditions(), a_steps() + c_steps(ACC),
               {"유효": True, "이유": [], "도달_최대_um": 412}, cells, crit, ACC)


def build_v4_terrace():
    """20261029-184000 — 테라스(실제 하늘) · 파트 C · B. 합격 22 · 불합격 2(clock · rx), gps_move(살펴봄, 기준 미정)."""
    rng = random.Random(4202)
    cells = {}
    for s in SERIALS:
        items = common_items(rng, clock_fail=s == 11620, rx_fail=s == 10684, imu_low=0.88 if s == 10690 else None)
        vr = {str(v): r2(rng.gauss(0.97, 0.03), 3) for v in (100, 300, 500)}
        items["gps_move"] = it({"v_ratio": vr, "leg_pos_ratio": r2(rng.gauss(0.98, 0.02), 3),
                                "leg_app_ratio": r2(rng.gauss(0.95, 0.03), 3)}, "비율", "pass",
                               r2(random.Random(f"gps_move:{s}").gauss(0, 0.7), 1),     # 시안 표본과 숫자를 같게 — 따로 뽑는다
                               "기준 미정 — 첫 실측 뒤 · 그때까지 |z| ≥ 3")
        reasons = []
        if s == 11620:
            reasons.append("clock 22.8 ms — 무리 중앙값 ± 20 ms 밖")
            items["clock"]["value"] = 22.8
        if s == 10684:
            reasons.append("rx 1.42 % — ≤ 1 % 밖")
        cells[str(s)] = {"dock": dock_of(s), "items": items, "verdict": finish(items, "fail" if reasons else "pass", reasons)}
    cond = {"protocol": "v4", "sky": "terrace", "part": ["C", "B"], "site": "terrace", "repeater": "off",
            "corrections": "on", "set": "Y", "cell_type": "CLBY", "fw": "2.3.1", "dock": "DKPY 4×6",
            "scene": {"name": "none", "mode": "none", "file": "미확인", "t0_gps": "미확인", "origin": "미확인"},
            "speeds_mm_s": [100, 300, 500], "accels_mm_s2": ACC, "still_s": 300, "dwell_s": 5, "judge_commit": "b72e014"}
    steps = c_steps(ACC) + [{"key": f"B{i}", "v_mm_s": v, "a_mm_s2": 500} for i, v in enumerate((100, 300, 500))]
    crit = criteria(next(iter(cells.values()))["items"])
    return doc("20261029-184000", cond, steps, {"유효": True, "이유": [], "도달_최대_um": 388}, cells, crit, ACC)


def build_invalid(base):
    """20261029-193000 — 테라스 시험을 파트 C 만으로 줄이고 장비 무효(rig.유효 false + 이유)로. 셀 24대 모두 void."""
    d = json.loads(json.dumps(base))
    d["stamp"] = "20261029-193000"
    d["conditions"]["part"] = ["C"]
    d["conditions"]["speeds_mm_s"] = []
    d["steps"] = [s for s in d["steps"] if s["key"].startswith("C")]
    d["rig"] = {"유효": False, "이유": ["추종 오차 2,140 µm — 문턱 2,000 µm (가속도 500 단계)"], "도달_최대_um": 2140}
    for c in d["cells"].values():
        c["items"].pop("gps_move", None)
        c["verdict"] = {"overall": "void", "flags": [], "reasons": ["rig 무효"]}
    d["group"].pop("gps_move", None)
    d["criteria"].pop("gps_move", None)
    return d


def build_unsupported():
    """20261029-210000 — 결과판이 모르는 판(v5). 화면은 '이 판은 아직 지원 안 함'을 보이고 멈추지 않아야 한다."""
    return {"_sample": True, "_note": NOTE + " 결과판이 모르는 판(v5) 상태를 보이려는 것이다.", "stamp": "20261029-210000",
            "conditions": {"protocol": "v5", "sky": "simulator", "part": ["A"], "site": "lab", "set": "Y",
                           "cell_type": "CLBY", "judge_commit": "c90d5aa"},
            "cells": {str(s): {"dock": dock_of(s), "verdict": {"overall": "pass", "flags": [], "reasons": []}} for s in SERIALS},
            "grid": GRID}


def build_v4_factory():
    """20261030-090000 — 시뮬레이터 · 파트 A · C · 공장(다른 날 같은 세트). sog_band 등급 경계 없음(value null · 미정),
    gate_* 는 갈래 '보류'(움직임 판단 프록시 확인 전 — 4-1 gate). 합격 20 · 불합격 2(clock · imu) ·
    보류 2(장면 고정 못 함 10684, FTG 못 받음 10699), 살펴봄 벗어남(pos_h · cn0_sim)."""
    rng = random.Random(4303)
    gate_why = "움직임 판단 재료 미확인 — 정지 장면에서 프록시(filtered_sog ≠ 0)가 살아 있는지 확인 전(4-1 gate)"
    cells = {}
    for s in SERIALS:
        items = common_items(rng, clock_fail=s == 11620, imu_low=0.81 if s == 10696 else None, imu_fail=s == 10696)
        for k, u in (("gate_on", "s"), ("gate_off", "s"), ("gate_share", "%")):
            items[k] = it(None, u, "hold", None, gate_why)
        lock_fail, ftg = s == 10684, s == 10699
        items.update(part_a_items(rng, s, pos_h_odd=(2.9, 4.6) if s == 10681 else None,
                                  cn0_odd=(33.0, -3.8) if s == 10681 else None, hold_why=HOLD_LOCK if lock_fail else None))
        if ftg:                                            # FTG 기반 항목만 재료가 없다 — 라이브 기반(rx · clock · lock · cn0_sim)은 남는다
            for k, u in (("fw", None), ("complete", "%"), ("imu", "비율")):
                items[k] = it(None, u, "hold", None, HOLD_FTG)
            items["pos_h"] = it(None, "m", "hold", None, HOLD_FTG)
            for k, u in (("pos_move", "m"), ("sog_lag", "s"), ("turn", "s")):
                items[k] = it(None, u, "info", None, HOLD_FTG)
            items["sog_band"] = it([{"band": b, "rmsd_mm_s": None, "ratio": None, "n": 0} for b in BANDS], "mm/s", "info",
                                   None, HOLD_FTG)
        reasons = []
        if s == 11620:
            items["clock"]["value"] = 22.7
            reasons.append("clock 22.7 ms — 무리 중앙값 ± 20 ms 밖")
        if s == 10696:
            reasons.append("imu 500 mm/s² 0.81 — 0.85 – 1.15 밖")
        if lock_fail or ftg:
            overall, reasons = "hold", [HOLD_LOCK if lock_fail else HOLD_FTG]
        else:
            overall = "fail" if reasons else "pass"
        cells[str(s)] = {"dock": dock_of(s), "items": items, "verdict": finish(items, overall, reasons)}
    gate = {"value": "프록시 확인 전", "kind": "보류", "status": "미정"}
    crit = criteria(next(iter(cells.values()))["items"], gate_on=gate, gate_off=gate, gate_share=gate)
    cond = sim_conditions(site="factory", judge_commit="d4e8a21",
                          scene={"name": "factory-5band", "mode": "file", "file": "scene_factory-5band.csv", "t0_gps": "미확인",
                                 "origin": {"lat": 37.4012, "lon": 127.1098, "h": 52.0}})
    return doc("20261030-090000", cond, a_steps() + c_steps(ACC),
               {"유효": True, "이유": [], "도달_최대_um": 405}, cells, crit, ACC)


def build_v4_repeater():
    """20261030-140000 — 중계기 · 파트 C(50 · 500 두 단계) · 실험실 · 장면 없음. gps_rx 는 갈래 '정보용'(7-2 — 중계기 조건의
    GPS 숫자는 정확도가 아니다). 합격 23 · 불합격 1(imu), 살펴봄 벗어남(gate_share — 10696 이 낮은 가속도에서 안 켜짐)."""
    rng = random.Random(4404)
    acc = [50, 500]
    cells = {}
    for s in SERIALS:
        items = common_items(rng, imu_low=0.83 if s == 10690 else None, imu_fail=s == 10690, accels=acc)
        items["gps_rx"].update({"verdict": "info", "z": None, "basis": REPEATER})
        if s == 10696:
            items["gate_share"].update({"value": {"50": 0.0, "500": 99.1}, "verdict": "flag", "z": -9.9,
                                        "basis": "|z| ≥ 3 · 50 mm/s² 에서 켜지지 않음"})
            items["gate_on"]["value"]["50"] = None
            items["gate_off"]["value"]["50"] = None
        reasons = ["imu 500 mm/s² 0.83 — 0.85 – 1.15 밖"] if s == 10690 else []
        cells[str(s)] = {"dock": dock_of(s), "items": items, "verdict": finish(items, "fail" if reasons else "pass", reasons)}
    crit = criteria(next(iter(cells.values()))["items"], gps_rx={"value": REPEATER, "kind": "정보용", "status": "확정"})
    cond = {"protocol": "v4", "sky": "repeater", "part": "C", "site": "lab", "repeater": "on", "corrections": "on",
            "set": "Y", "cell_type": "CLBY", "fw": "2.3.1", "dock": "DKPY 4×6",
            "scene": {"name": None, "mode": "none", "file": None, "t0_gps": None, "origin": None},
            "speeds_mm_s": [], "accels_mm_s2": acc, "still_s": 300, "dwell_s": 5, "judge_commit": "d4e8a21"}
    return doc("20261030-140000", cond, c_steps(acc), {"유효": True, "이유": [], "도달_최대_um": 236}, cells, crit, acc)


def samples() -> dict:
    """{파일 이름: 내용} — 가짜 표본 전부(실제 v1 파일은 빼고)."""
    ter = build_v4_terrace()
    docs = [build_v4_sim(), ter, build_invalid(ter), build_unsupported(), build_v4_factory(), build_v4_repeater()]
    return {f"protocol_{d['stamp']}.json": d for d in docs}


def dumps(d) -> str:
    return json.dumps(d, ensure_ascii=False, indent=1, allow_nan=False) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="레일 결과판 가짜 표본을 이 폴더에 다시 쓴다")
    ap.add_argument("--js", help="시안 화면용 rail-data.js 도 쓴다(window.RAIL_SAMPLE · window.RAIL_RAW, v1 실제 파일 포함)")
    a = ap.parse_args(argv)
    raw = samples()
    for name, d in raw.items():
        (HERE / name).write_text(dumps(d), encoding="utf-8", newline="\n")
    if a.js:
        allr = {V1_NAME: json.loads((HERE / V1_NAME).read_text(encoding="utf-8")), **raw}
        Path(a.js).write_text("// 레일 결과판 표본 — cell-bench/tests/fixtures/rail/make_samples.py 가 쓴다. 서버에서는 /api/rail/* 로 바뀐다.\n"
                              f"window.RAIL_SAMPLE={json.dumps(sorted(raw))};\n"
                              f"window.RAIL_RAW={json.dumps(allr, ensure_ascii=False, allow_nan=False)};\n", encoding="utf-8")
    print("썼다:", ", ".join(sorted(raw)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
