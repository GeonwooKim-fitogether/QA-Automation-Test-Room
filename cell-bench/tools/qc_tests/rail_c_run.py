"""IMU-2 · FIRMWARE-1 레일 운전 — 정지 장면 아래 가속도 50 · 500 mm/s² 각 N번, 이어서 긴 거리(150 ↔ 1,550 mm · 300 mm/s · 500 mm/s²) N번, 끝나면 셀 기록 받기.
입회 표지(tools/attend.py)가 없으면 run_rail 이 기다렸다가, 운전자가 만들면 바로 이어 간다.
사용: python -X utf8 rail_c_run.py [--n 10] [--layout Y-shift]"""
import sys, time, json
from pathlib import Path
ROOT = Path(r"C:\Dev\dynamic-bench\cell-bench"); sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT))
import qc_round as Q, run_rail
from tools import rail_cells
from motionbench import charge
N = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 10
LAYOUT = sys.argv[sys.argv.index("--layout") + 1] if "--layout" in sys.argv else "Y-shift"
OUT = Q.DATA / time.strftime("railC_%Y%m%d"); OUT.mkdir(parents=True, exist_ok=True)
say = Q.Log(OUT / "run.log")
summary = {"tests": ["IMU-2", "FIRMWARE-1"], "layout": LAYOUT, "n": N, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
def save(): (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
say(f"── IMU-2 · FIRMWARE-1 · 가속도 50 · 500 · 긴 거리 1400 각 {N}번 · 배치 {LAYOUT}")
summary["plug_off"] = charge.plug_set(False, say)
from motionbench.labsat import clean
ls = Q.LabSat(); Q.ensure_static(ls, say); say(f"감쇠 0 으로 → {clean(ls._one('ATTN:00'))}")
say(f"LabSat {ls.status()} · 감쇠 {clean(ls._one('ATTN:?'))}")
tap = Q.lock_ready("C", 120, OUT, say)
try:
    Q.battery_ok(say)
    for preset, acc in (("C", 50), ("C500", 500), ("C1400", "1400")):
        t0 = time.time()
        rc = run_rail.main(["C", "--n", str(N), "--out", str(OUT / f"C{acc}"), "--preset", preset])
        summary[f"C{acc}"] = {"rc": rc, "from": t0, "to": time.time()}; save()
        say(f"가속도 {acc} 끝 · 코드 {rc}")
        if rc != 0: break
    say("모터 운전 끝 — 여기부터 현장 입회가 필요 없다")
finally:
    Q.stop_tap(tap)
ftg = rail_cells.extract_all("Y", say); summary["ftg"] = str(ftg); summary["ended"] = time.strftime("%Y-%m-%d %H:%M:%S"); save()
say(f"── 끝 · 기록 {ftg}")
