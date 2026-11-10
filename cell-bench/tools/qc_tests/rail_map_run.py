"""칸 지도 운전 — 배치를 돌린 뒤 FIRMWARE-1(500 mm/s², 150 ↔ 750 mm) N번과 FIRMWARE-2(100 mm/s 장면 동기) N번, 끝나면 정지 장면으로 되돌리고 셀 기록 받기.
사용: python -X utf8 rail_map_run.py [--n 10] [--layout Y-rot]"""
import sys, time, json
from pathlib import Path
ROOT = Path(r"C:\Dev\dynamic-bench\cell-bench"); sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT))
import qc_round as Q, run_rail
from tools import rail_cells
from motionbench import charge
from motionbench.labsat import clean
N = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 10
LAYOUT = sys.argv[sys.argv.index("--layout") + 1] if "--layout" in sys.argv else "Y-rot"
TAP_MIN = 200
OUT = Q.DATA / time.strftime("railMap_%Y%m%d"); OUT.mkdir(parents=True, exist_ok=True)
say = Q.Log(OUT / "run.log")
summary = {"tests": ["FIRMWARE-1", "FIRMWARE-2"], "layout": LAYOUT, "n": N, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
def save(): (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
say(f"── 칸 지도 · 500 mm/s² {N}번 + 100 mm/s 장면 {N}번 · 배치 {LAYOUT}")
summary["plug_off"] = charge.plug_set(False, say)
ls = Q.LabSat(); Q.ensure_static(ls, say); say(f"감쇠 0 으로 → {clean(ls._one('ATTN:00'))}")
tap = Q.lock_ready("C", TAP_MIN, OUT, say)
rc = 0
try:
    Q.battery_ok(say)
    t0 = time.time()
    rc = run_rail.main(["C", "--n", str(N), "--out", str(OUT / "C500"), "--preset", "C500"])
    summary["C500"] = {"rc": rc, "from": t0, "to": time.time()}; save(); say(f"500 mm/s² 끝 · 코드 {rc}")
    if rc == 0:
        scene, truth, preset = Q.B_SCENES[100]
        got, tap = Q.gps_ready(ls, scene, say, tap, "C", OUT, TAP_MIN)
        summary["B100_gps_fixed"] = got; save()
        if got is None:
            rc = 1; say(f"{scene} 에서 위치를 못 잡아 멈춘다")
        else:
            t0 = time.time()
            rc = run_rail.main(["B", "--n", str(N), "--out", str(OUT / "B100"), "--scene", scene, "--preset", preset])
            summary["B100"] = {"rc": rc, "scene": scene, "truth": str(truth), "from": t0, "to": time.time()}; save()
            say(f"속도 100 끝 · 코드 {rc}")
    say("모터 운전 끝 — 여기부터 현장 입회가 필요 없다")
finally:
    try:
        Q.ensure_static(ls, say)
    except Exception as e:
        say(f"정지 장면 되돌리기 실패 {e}")
    Q.stop_tap(tap)
ftg = rail_cells.extract_all("Y", say); summary["ftg"] = str(ftg); summary["ended"] = time.strftime("%Y-%m-%d %H:%M:%S"); save()
say(f"── 끝 · 코드 {rc} · 기록 {ftg}")
