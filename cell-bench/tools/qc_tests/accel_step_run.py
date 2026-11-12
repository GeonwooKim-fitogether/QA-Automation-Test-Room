"""가속도 · 속도 올리기 단계 시험 — 긴 거리(150 ↔ 1,550 mm)에서 (속도, 가속도) 단계를 하나씩, 단계마다 운전 1번(왕복 3번).
단계가 끝날 때마다 모터 기록의 추종 오차 최대값을 그 속도의 정지 문턱(safety.ferr_limit)과 비교해, 문턱의 90 % 를 넘거나 알람 · 비정상 종료면 다음 단계로 가지 않는다.
QC 시험이 아니다(상한 시험) — run_rail 에는 시험 ID 대신 cap 으로 넘긴다. 준비는 레일 시험과 같아 qc_round.rail_prep 을 쓰고, 실시간 화면에는 같은 긴 거리 운전인 IMU-2 로 띄운다.
드라이브 파라미터(게인 · 피드포워드)는 바꾸지 않는다. 프리셋 이름: 300 mm/s 는 cap-<구간>a<가속도>, 그 밖의 속도는 cap-<구간>v<속도>a<가속도>
(구간 d1400 = 150 ↔ 1,550 mm, d1200 = 250 ↔ 1,450 mm). 프리셋은 사용자 승인 뒤에 만든다.
사용: python -X utf8 accel_step_run.py [--steps 300:1000,300:2000,300:3000] [--span d1400]   (속도 mm/s : 가속도 mm/s², 속도를 빼면 300)"""
import sys, time, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT))   # <작업 트리>/cell-bench
import qc_round as Q, run_rail
from motionbench import safety
RAW = sys.argv[sys.argv.index("--steps") + 1].split(",") if "--steps" in sys.argv else ["1000", "2000", "3000"]
STEPS = [tuple(int(y) for y in x.split(":")) if ":" in x else (300, int(x)) for x in RAW]
SPAN = sys.argv[sys.argv.index("--span") + 1] if "--span" in sys.argv else "d1400"   # 프리셋 이름의 구간. 2단계 고속은 d1200(250 ↔ 1,450 mm)
OUT = Q.DATA / time.strftime("accelStep_%Y%m%d"); OUT.mkdir(parents=True, exist_ok=True)
say = Q.Log(OUT / "run.log")
summary = {"steps": STEPS, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "results": []}
def save(): (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
say(f"── 가속도 · 속도 단계 시험 · (속도 mm/s, 가속도 mm/s²) {STEPS} · 멈추는 기준 = 그 속도 정지 문턱의 90 %")
ls, tap, summary["plug_off"] = Q.rail_prep(["IMU-2"], 120, OUT, say)   # 플러그 끔 · 정지 장면 · 감쇠 0 · 위치 잡기 · 배터리 확인
try:
    for V, a in STEPS:
        lim = safety.ferr_limit(V)
        name = f"cap-{SPAN}a{a}" if V == 300 and SPAN == "d1400" else f"cap-{SPAN}v{V}a{a}"
        sub = OUT / (f"a{a}" if V == 300 else f"v{V}a{a}")
        rc = run_rail.main(["cap", "--n", "1", "--out", str(sub), "--preset", name])
        runs = json.load(open(sub / "runs.json", encoding="utf-8"))
        mj = Path(runs[0]["motor_csv"]).with_suffix(".json") if runs and runs[0].get("motor_csv") else None
        m = json.load(open(mj, encoding="utf-8")) if mj and mj.exists() else {}
        legs = m.get("legs") or []
        fmax = max((abs(l.get("ferr_max") or 0) for l in legs), default=None)
        smax = max((l.get("speed_max_mm_s") or 0 for l in legs), default=None)
        res = {"v_mm_s": V, "a_mm_s2": a, "rc": rc, "exit": m.get("exit"), "alarms": m.get("alarms"), "ferr_max_um": fmax, "ferr_limit_um": lim,
               "speed_max_mm_s": smax, "reach_err_um": max((l.get("err_um") or 0 for l in legs), default=None), "motor_json": str(mj) if mj else None}
        summary["results"].append(res); save()
        say(f"속도 {V} mm/s · 가속도 {a} mm/s² 끝 · 코드 {rc} · {m.get('exit')} · 추종 오차 최대 {fmax} µm(문턱 {lim}) · 최고 속도 {smax} mm/s · 알람 {m.get('alarms')}")
        if rc != 0 or m.get("alarms") or fmax is None or fmax > lim * 0.9:
            say("멈추는 기준에 걸려 다음 단계로 가지 않는다"); break
    say("모터 운전 끝")
finally:
    Q.stop_tap(tap)
say("── 끝")
