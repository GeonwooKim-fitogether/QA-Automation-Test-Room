"""칸 지도 — 오늘 세 배치(Y-shift 아침 · Y-shift-b 칸 가르기 · Y-rot 칸 지도)의 FIRMWARE-2 100 mm/s 거른 속도 비와
FIRMWARE-1 500 mm/s² 켜짐 시간을 셀 · 칸으로 나눠 본다. 첫 운전(레일이 장면보다 먼저 출발)은 뺀다.
값 = 회차들의 셀별 중앙값. 칸 효과 = 그 칸에 앉았던 셀들의 값에서 그 셀의 다른 칸 값을 뺀 것의 평균(셀이 두 칸 이상 앉았을 때).
사용: python -X utf8 slot_map.py"""
import sys, json, glob, statistics as st, collections
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
DATA = Path(r"C:\Dev\dynamic-bench\cell-bench\data")
LAY = json.load(open(Path(__file__).parent / "dock_layout_a.json", encoding="utf-8"))
slot_of = lambda name: {s: (c + 1, r + 1) for c, col in enumerate(LAY[name]["columns"]) for r, s in enumerate(col)}

first = set()
for rp in glob.glob(str(DATA / "railB_20261110*" / "B*" / "runs.json")) + glob.glob(str(DATA / "railMap_20261110" / "B*" / "runs.json")):
    rs = json.load(open(rp, encoding="utf-8"))
    if rs and rs[0].get("motor_csv"):
        first.add(Path(rs[0]["motor_csv"]).name)


def fw2(folder, layout):
    vals = collections.defaultdict(list)
    for f in sorted(glob.glob(str(DATA / folder / "judged" / "protocol_*.json"))):
        j = json.load(open(f, encoding="utf-8"))
        if j["conditions"]["speeds_mm_s"][0] != 100 or any(n in json.dumps(j.get("source"), ensure_ascii=False) for n in first):
            continue
        for s, c in j["cells"].items():
            x = c["items"].get("ftg_move", {}).get("value")
            if isinstance(x, (int, float)):
                vals[int(s)].append(x)
    sl = slot_of(layout)
    return {s: (sl.get(s), st.median(v), len(v)) for s, v in vals.items()}


def fw1(path, layout, key="C500"):
    if not Path(path).exists():
        return {}
    sl = slot_of(layout)
    return {r["cell"]: (sl.get(r["cell"]), r[key]["lag_on"], r[key]["n_on"]) for r in json.load(open(path, encoding="utf-8")) if r.get(key) and r[key].get("lag_on") is not None}


def table(title, sets):
    print(f"\n== {title}")
    by_slot = collections.defaultdict(list); by_cell = collections.defaultdict(list)
    for lab, d in sets:
        for s, (slot, v, n) in d.items():
            by_slot[slot].append((lab, s, v)); by_cell[s].append((lab, slot, v))
    cell_med = {s: st.median(v for _, _, v in L) for s, L in by_cell.items()}
    print("칸별 (배치: 셀 값) · 칸 효과 = 셀 자신의 중앙값과의 차")
    eff = {}
    for slot in sorted(k for k in by_slot if k):
        L = by_slot[slot]
        e = [v - cell_med[s] for _, s, v in L if len(by_cell[s]) > 1]
        eff[slot] = st.mean(e) if e else None
        print(f"  {slot}: " + "  ".join(f"{lab}:{s} {v:.2f}" for lab, s, v in L) + (f"  → 칸 효과 {eff[slot]:+.2f}" if eff[slot] is not None else ""))
    return eff


A = fw2("railB_20261110", "Y-shift"); B = fw2("railB_20261110_swap", "Y-shift-b"); C = fw2("railMap_20261110", "Y-rot")
e2 = table("FIRMWARE-2 · 100 mm/s 거른 속도 비", [("아침", A), ("가르기", B), ("지도", C)])
F1a = fw1(DATA / "railC_20261110" / "judge_items.json", "Y-shift"); F1c = fw1(DATA / "railMap_20261110" / "judge_items.json", "Y-rot")
e1 = table("FIRMWARE-1 · 500 mm/s² 켜지기까지(초)", [("아침", F1a), ("지도", F1c)])
print("\n칸 효과가 큰 칸 — FIRMWARE-2 0.10 넘게 낮음:", sorted((k, round(v, 2)) for k, v in e2.items() if v is not None and v < -0.10))
print("칸 효과가 큰 칸 — FIRMWARE-1 0.20초 넘게 늦음:", sorted((k, round(v, 2)) for k, v in e1.items() if v is not None and v > 0.20))
