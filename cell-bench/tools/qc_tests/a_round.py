"""시험 A 한 바퀴 — 안테나 · 수신 회로 시험(가설판 v1). 모터 없음.
순서: 셀 보기(라이브 1분) → 들리는 셀 전부 한꺼번에 다시 켜기(처음 위치 잡는 시간용) → 라이브 기록 켜고 위치 잡기 기다림
     → 5분 버림 → 30분 기록 → 라이브 기록 끔 → 셀 기록 받기(받으면서 비우기).
사용: python -X utf8 a_round.py <바퀴 번호 1·2·3> <배치 이름 Y · Y-swap · Y-shift> [--attn 18] [--a-min 30] [--skip-min 5] [--no-reboot] [--no-extract] [--min-batt 60] [--min-cells 20]
바퀴 시작에 LabSat 출력 감쇠를 --attn dB 로 두고(11-09 실측: 18 dB 에서 24칸 모두 위치 유지), 기록이 끝나면 0 으로 되돌린다.
결과: C:\\Dev\\dynamic-bench\\cell-bench\\data\\collectA_<날짜>\\r0k\\{summary.json, tap_A.log, round.log} · 받은 기록 폴더 data/ftg_y_<시각>
배치 Y-shift 는 이 폴더의 dock_layout_a.json 에 있다(열을 한 칸씩 민 것). 라이브 화면 이름표는 Y 로 둔다."""
import sys, time, json
from pathlib import Path
ROOT = Path(r"C:\Dev\dynamic-bench\cell-bench"); sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT))
import qc_round as Q
from tools import rail_cells

HERE = Path(__file__).resolve().parent
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
K = int(ARGS[0]); LAYOUT = ARGS[1]
def opt(name, default):
    if f"--{name}" in sys.argv:
        return float(sys.argv[sys.argv.index(f"--{name}") + 1])
    return default
A_MIN = opt("a-min", 30); SKIP_MIN = opt("skip-min", 5); MIN_BATT = opt("min-batt", 60); ATTN = int(opt("attn", 18))
MIN_CELLS = int(opt("min-cells", 20))   # 독에 일부만 꽂는 회차(행 간격 시험 등)는 낮춘다
NO_REBOOT = "--no-reboot" in sys.argv; NO_EXTRACT = "--no-extract" in sys.argv

OUT = Q.DATA / time.strftime("collectA_%Y%m%d") / f"r{K:02d}"; OUT.mkdir(parents=True, exist_ok=True)
say = Q.Log(OUT / "round.log")
layouts = json.load(open(ROOT / "motionbench" / "dock_layout.json", encoding="utf-8")) | json.load(open(HERE / "dock_layout_a.json", encoding="utf-8"))
cols = layouts[LAYOUT]["columns"]
Q.TAP_LAYOUT = LAYOUT if LAYOUT in ("Y", "Y-swap") else "Y"
summary = {"round": K, "layout": LAYOUT, "columns": cols, "a_min": A_MIN, "skip_min": SKIP_MIN, "antenna": "2026-11-09 18시 독 180도 돌려 안테나를 바라보게 둔 자리", "foil": "없음(제거)"}

say(f"── A {K}바퀴 · 배치 {LAYOUT} · 버림 {SKIP_MIN:g}분 + 기록 {A_MIN:g}분")
from motionbench.labsat import clean
ls = Q.LabSat(); Q.ensure_static(ls, say); st = ls.status(); summary["labsat"] = st; say(f"LabSat {st}")
r = clean(ls._one(f"ATTN:{ATTN:02d}")); summary["attn_db"] = ATTN; say(f"LabSat 출력 감쇠 {ATTN} dB → {r}")
if "ERR" in r.upper(): raise SystemExit("감쇠 설정 실패")

# 1. 셀 보기
tap = Q.start_tap("A", 2, OUT / "tap_A.log"); time.sleep(50)
cells = Q.qc_now().get("cells") or {}
heard = sorted(int(k) for k, x in cells.items() if x.get("age_s") is not None and x["age_s"] < 10)
batt = {int(k): x.get("battery") for k, x in cells.items() if int(k) in heard}
low = min((b for b in batt.values() if b is not None), default=None)
say(f"들리는 셀 {len(heard)}대 {heard} · 가장 낮은 배터리 {low}%")
summary["heard"] = heard; summary["battery"] = batt
Q.stop_tap(tap)
if len(heard) < MIN_CELLS:
    raise SystemExit(f"들리는 셀이 {len(heard)}대뿐(필요 {MIN_CELLS}) — 셀 · 와이파이를 보고 다시")
if low is not None and low < MIN_BATT:
    raise SystemExit(f"가장 낮은 배터리 {low}% < {MIN_BATT:g}% — 충전 뒤 다시 (--min-batt 로 낮출 수 있다)")

# 2. 한꺼번에 다시 켜기 — 처음 위치 잡는 시간을 같은 출발선에서 재려고
if not NO_REBOOT:
    summary["reboot_at"] = time.time(); Q.reboot(heard, say); summary["reboot_done"] = time.time()
    say(f"다시 켬 {len(heard)}대 · {time.strftime('%H:%M:%S')}")

# 3. 라이브 기록 켜고 위치 잡기
tap = Q.start_tap("A", A_MIN + SKIP_MIN + Q.LOCK_WAIT_S / 60 + 5, OUT / "tap_A.log"); time.sleep(8)
t_lock0 = time.time()
if not Q.wait_lock(say, need=MIN_CELLS, max_s=Q.LOCK_WAIT_S):
    Q.stop_tap(tap); raise SystemExit(f"위치를 못 잡는 셀이 많다 {Q.unlocked()} — 멈춘다")
summary["lock_20_at"] = time.time(); say(f"{MIN_CELLS}대 위치 잡음까지 {time.time() - t_lock0:.0f}초")

# 4. 버리고 기록
say(f"{SKIP_MIN:g}분 버림"); time.sleep(SKIP_MIN * 60)
t0 = time.time(); say(f"A 기록 {A_MIN:g}분 시작"); time.sleep(A_MIN * 60)
summary["A"] = {"from": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)), "to": time.strftime("%Y-%m-%d %H:%M:%S")}
say("A 끝"); Q.stop_tap(tap)
say(f"감쇠 0 으로 되돌림 → {clean(ls._one('ATTN:00'))}")
(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

# 5. 셀 기록 받기
if not NO_EXTRACT:
    time.sleep(5)
    ftg = rail_cells.extract_all("Y", say); summary["ftg"] = str(ftg)
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"받기 끝 → {ftg}")
say(f"── A {K}바퀴 끝. 다음 바퀴 전에 자리를 바꾼다")
