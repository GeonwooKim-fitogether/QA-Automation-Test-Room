"""FIRMWARE-2 레일 운전 — 레일과 같은 속도로 움직이는 LabSat 장면을 함께 틀고 100 · 300 · 500 mm/s 각 N번, 끝나면 정지 장면으로 되돌리고 셀 기록 받기.
qc_round.run_motor 의 B 부분과 같은 순서(장면 바꾸면 gps_ready 로 위치 다시 잡기). 입회 표지는 run_rail 이 확인한다.
사용: python -X utf8 rail_b_run.py [--n 10] [--layout Y-shift]"""
import sys, time, json
from pathlib import Path
ROOT = Path(r"C:\Dev\dynamic-bench\cell-bench"); sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT))
import qc_round as Q, run_rail
from tools import rail_cells
from motionbench import charge
N = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 10
LAYOUT = sys.argv[sys.argv.index("--layout") + 1] if "--layout" in sys.argv else "Y-shift"
SPEEDS = tuple(int(x) for x in sys.argv[sys.argv.index("--speeds") + 1].split(",")) if "--speeds" in sys.argv else (100, 300, 500)
TAG = sys.argv[sys.argv.index("--tag") + 1] if "--tag" in sys.argv else ""
TAP_MIN = 200          # 라이브 기록(배터리 값)이 운전 내내 살아 있게 — 11-10 15:38 에 50분으로 걸어 500 mm/s 6번째에서 끊겼다
OUT = Q.DATA / (time.strftime("railB_%Y%m%d") + TAG); OUT.mkdir(parents=True, exist_ok=True)
say = Q.Log(OUT / "run.log")
summary = {"tests": ["FIRMWARE-2"], "layout": LAYOUT, "n": N, "speeds": SPEEDS, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
def save(): (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
say(f"── FIRMWARE-2 · 속도 {' · '.join(map(str, SPEEDS))} mm/s 각 {N}번 · 배치 {LAYOUT}")
summary["plug_off"] = charge.plug_set(False, say)
from motionbench.labsat import clean
ls = Q.LabSat(); Q.ensure_static(ls, say); say(f"감쇠 0 으로 → {clean(ls._one('ATTN:00'))}")
tap = Q.lock_ready("C", 240, OUT, say)
rc = 0
try:
    Q.battery_ok(say)
    for v in SPEEDS:
        scene, truth, preset = Q.B_SCENES[v]
        got, tap = Q.gps_ready(ls, scene, say, tap, "C", OUT, TAP_MIN)
        summary[f"B{v}_gps_fixed"] = got; save()
        if got is None:
            rc = 1; say(f"{scene} 에서 위치를 못 잡아 멈춘다"); break
        t0 = time.time()
        rc = run_rail.main(["B", "--n", str(N), "--out", str(OUT / f"B{v}"), "--scene", scene, "--preset", preset])
        summary[f"B{v}"] = {"rc": rc, "scene": scene, "truth": str(truth), "from": t0, "to": time.time()}; save()
        say(f"속도 {v} 끝 · 코드 {rc}")
        if rc != 0:
            break
    say("모터 운전 끝 — 여기부터 현장 입회가 필요 없다")
finally:
    try:
        Q.ensure_static(ls, say)
    except Exception as e:
        say(f"정지 장면 되돌리기 실패 {e}")
    Q.stop_tap(tap)
ftg = rail_cells.extract_all("Y", say); summary["ftg"] = str(ftg); summary["ended"] = time.strftime("%Y-%m-%d %H:%M:%S"); save()
say(f"── 끝 · 코드 {rc} · 기록 {ftg}")
