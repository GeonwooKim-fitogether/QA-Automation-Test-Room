"""IMU 정지 시험 — 셀을 독에 둔 채 원격 재부팅을 여러 번 하고, 부팅마다 정지 구간을 기록한다. 모터 없음.
부팅마다: 라이브 기록 끔 → 들리는 셀 전부 재부팅 → 라이브 기록 켬 → 안정 대기 → 기록 구간.
끝나면 셀 기록을 받는다(받으면서 비우기). 판정은 a_imu 방식(부팅마다 중력 크기 · 자이로스코프 영점 오차).
사용: python -X utf8 imu_still_run.py [--boots 6] [--settle-min 3] [--rec-min 5] [--layout Y-shift] [--min-batt 50]"""
import sys, time, json
from pathlib import Path
ROOT = Path(r"C:\Dev\dynamic-bench\cell-bench"); sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT))
import qc_round as Q
from tools import rail_cells
from motionbench import charge

def opt(name, default):
    return float(sys.argv[sys.argv.index(f"--{name}") + 1]) if f"--{name}" in sys.argv else default
BOOTS = int(opt("boots", 6)); SETTLE = opt("settle-min", 3); REC = opt("rec-min", 5); MIN_BATT = opt("min-batt", 50)
LAYOUT = sys.argv[sys.argv.index("--layout") + 1] if "--layout" in sys.argv else "Y-shift"
OUT = Q.DATA / time.strftime("imuStill_%Y%m%d"); OUT.mkdir(parents=True, exist_ok=True)
say = Q.Log(OUT / "run.log")
layouts = json.load(open(ROOT / "motionbench" / "dock_layout.json", encoding="utf-8")) | json.load(open(Path(__file__).parent / "dock_layout_a.json", encoding="utf-8"))
summary = {"test": "IMU 정지 시험", "layout": LAYOUT, "columns": layouts[LAYOUT]["columns"], "boots": [], "settle_min": SETTLE, "rec_min": REC,
           "started": time.strftime("%Y-%m-%d %H:%M:%S")}
def save(): (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

say(f"── IMU 정지 시험 · 부팅 {BOOTS}번 · 안정 {SETTLE:g}분 + 기록 {REC:g}분 · 배치 {LAYOUT}")
summary["plug_off"] = charge.plug_set(False, say)
tap = Q.start_tap("A", 2, OUT / "tap.log"); time.sleep(50)
cells = Q.qc_now().get("cells") or {}
heard = sorted(int(k) for k, x in cells.items() if x.get("age_s") is not None and x["age_s"] < 10)
batt = {int(k): x.get("battery") for k, x in cells.items() if int(k) in heard}
low = min((b for b in batt.values() if b is not None), default=None)
Q.stop_tap(tap)
say(f"들리는 셀 {len(heard)}대 {heard} · 가장 낮은 배터리 {low}%")
summary["heard"] = heard; summary["battery_start"] = batt; save()
if len(heard) < 20: raise SystemExit(f"들리는 셀이 {len(heard)}대뿐 — 멈춘다")
if low is not None and low < MIN_BATT: raise SystemExit(f"가장 낮은 배터리 {low}% < {MIN_BATT:g}% — 충전 뒤 다시")

for k in range(1, BOOTS + 1):
    t_rb = time.time(); Q.reboot(heard, say)
    tap = Q.start_tap("A", SETTLE + REC + 3, OUT / "tap.log")
    say(f"부팅 {k}/{BOOTS} · 재부팅 {time.time() - t_rb:.0f}초 · 안정 {SETTLE:g}분"); time.sleep(SETTLE * 60)
    t0 = time.time(); say(f"부팅 {k} 기록 {REC:g}분 시작"); time.sleep(REC * 60); t1 = time.time()
    cells = Q.qc_now().get("cells") or {}
    alive = sorted(int(s) for s, x in cells.items() if x.get("age_s") is not None and x["age_s"] < 10)
    Q.stop_tap(tap)
    summary["boots"].append({"k": k, "reboot_at": t_rb, "from": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
                             "to": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t1)), "alive": alive}); save()
    say(f"부팅 {k} 끝 · 기록 중 들린 셀 {len(alive)}대")

say("받기 시작(받으면서 비우기)")
ftg = rail_cells.extract_all("Y", say); summary["ftg"] = str(ftg); summary["ended"] = time.strftime("%Y-%m-%d %H:%M:%S"); save()
say(f"── 끝 · 기록 {ftg} · 플러그는 꺼 둔 채로 둔다(다음 레일 시험도 배터리로)")
