"""FIRMWARE-1 · IMU 펌웨어 시험 실행기 — 정지 장면 아래 등속 구간 없는 왕복을 가속도 단계로 올렸다가 다시 내린다(기본 0.5 → 8 → 0.5 m/s²).
보는 것: 셀 펌웨어의 IMU 움직임 판정이 제때 켜지고 꺼지는가. 상태는 '가설, 프로토콜 설계 중'(dynamic-bench/design/qc-tests.md FIRMWARE-1).
단계마다 승인 프리셋 FIRMWARE-1_step-a<가속도> 하나(왕복 5번 · 끝 멈춤 0 · 속도 상한 2 m/s 는 닿지 않는 거리 · 운전 동안만 급정지 감속 10 m/s²)를
--step-n 번(기본 1) 돈다. 단계 사이 정지는 실행 사이 준비 시간(10초 이상)이다.
단계마다 추종 오차가 문턱(motionbench/safety.ferr_limit(2000))의 90 % 를 넘거나, 알람 · 비정상 종료면 다음 단계로 가지 않는다.
준비(플러그 끔 · 정지 장면 · 감쇠 0 · 위치 잡기 · 배터리 확인)와 마무리 · 셀 기록 받기는 run_common.run 이 tools/qc_round.py 로 한다.
GPS 는 판정에 쓰지 않는다 — 판정은 셀 IMU 기록 종류(움직임 판정이면 0x0E, 아니면 0x0F)로 본다. 2026-11-12 첫 운전은 위치 잡기를 기다리지 않았다.
결과: data/FIRMWARE-1_<날짜>[꼬리표]/<차례>_a<가속도>/runs.json · run.log · summary.json(results 에 단계마다 추종 오차 · 최고 속도),
     받은 셀 기록 data/ftg_y_<시각>
판정: firmware1_step_judge.py <기록 폴더>
첫 운전(2026-11-12)은 이 파일을 만들기 전의 임시 실행기로 돌았고 기록은 data/imuStep_20261112-1744 에 있다.
옛 조건(150 ↔ 750 mm · 100 mm/s 등속 왕복)은 firmware1_run.py(이력).
사용: python -X utf8 firmware1_step_run.py [--steps 500,1000,2000,4000,8000,4000,2000,1000,500] [--step-n 1] [--layout Y-shift] [--tag 꼬리표] [--no-extract]"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_common as RC  # noqa: E402
from motionbench import safety  # noqa: E402

TEST = "FIRMWARE-1"
STEPS = "500,1000,2000,4000,8000,4000,2000,1000,500"     # 가속도 mm/s² — 올렸다가 다시 내린다
PRESET = "FIRMWARE-1_step-a{}"                            # 승인 프리셋 이름(dynamic-bench/dev/presets) — 구간은 프리셋 파일에 있다
FERR_V_MM_S = 2000                                       # 추종 오차 문턱을 잡을 속도 — 프리셋의 속도 상한
FERR_STOP = 0.9                                          # 문턱의 이만큼을 넘으면 다음 단계로 가지 않는다


def add_options(ap) -> None:
    ap.add_argument("--steps", default=STEPS, help=f"FIRMWARE-1 가속도 단계 mm/s², 쉼표로 (기본 {STEPS})")
    ap.add_argument("--step-n", type=int, default=1, help="단계마다 운전 횟수(한 번 = 왕복 5번, 기본 1). --n 은 이 시험에 쓰지 않는다")


def steps(a) -> list[int]:
    return [int(x) for x in a.steps.split(",") if x.strip()]


def check(a) -> None:
    for acc in sorted(set(steps(a))):
        if not (RC.Q.run_rail.PRESETS / f"{PRESET.format(acc)}.json").exists():
            raise SystemExit(f"FIRMWARE-1 가속도 {acc} mm/s² 의 승인 프리셋 {PRESET.format(acc)}.json 이 없다")


def tap_minutes(a) -> float:
    return 2.0 * a.step_n * len(steps(a)) + 10       # 단계 한 번 1분 안팎(2026-11-12) + 준비 시간, 넉넉히


def motor_result(sub: Path) -> dict:
    """단계 폴더의 runs.json → 첫 실행의 모터 기록 요약(추종 오차 최대 · 최고 속도 · 종료 · 알람)."""
    runs = json.loads((sub / "runs.json").read_text(encoding="utf-8")) if (sub / "runs.json").exists() else []
    mj = Path(runs[0]["motor_csv"]).with_suffix(".json") if runs and runs[0].get("motor_csv") else None
    m = json.loads(mj.read_text(encoding="utf-8")) if mj and mj.exists() else {}
    legs = m.get("legs") or []
    return {"exit": m.get("exit"), "alarms": m.get("alarms"),
            "ferr_max_um": max((abs(x.get("ferr_max") or 0) for x in legs), default=None),
            "speed_max_mm_s": max((x.get("speed_max_mm_s") or 0 for x in legs), default=None),
            "motor_json": str(mj) if mj else None}


def drive(rec, say, ls, tap, a):
    """운전만 — 가속도 단계마다 step_n 번. 멈추는 기준에 걸리면 다음 단계로 가지 않는다. (코드, 실시간 기록)을 돌려준다."""
    lim = safety.ferr_limit(FERR_V_MM_S)
    rec.summary.update({"steps_mm_s2": steps(a), "step_n": a.step_n, "ferr_limit_um": lim, "results": []})
    say(f"── {TEST} 가속도 단계 {steps(a)} mm/s² · 단계마다 {a.step_n}번(왕복 5번씩)")
    rc = 0
    for i, acc in enumerate(steps(a), 1):
        sub = rec.out / f"{i:02d}_a{acc}"
        t0 = time.time()
        rc = RC.Q.run_rail.main([TEST, "--n", str(a.step_n), "--out", str(sub), "--preset", PRESET.format(acc)])
        r = {"a_mm_s2": acc, "rc": rc, "from": t0, "to": time.time(), "preset": PRESET.format(acc)} | motor_result(sub)
        rec.summary["results"].append(r)
        rec.save()
        say(f"가속도 {acc} mm/s² 끝 · 코드 {rc} · {r['exit']} · 추종 오차 최대 {r['ferr_max_um']} µm(문턱 {lim}) · "
            f"최고 속도 {r['speed_max_mm_s']} mm/s · 알람 {r['alarms']}")
        if rc != 0 or r["alarms"] or r["ferr_max_um"] is None or r["ferr_max_um"] > lim * FERR_STOP:
            say("멈추는 기준에 걸려 다음 단계로 가지 않는다")
            return rc or 1, tap
    return rc, tap


def main(argv=None) -> int:
    return RC.run([TEST], argv, __doc__)


if __name__ == "__main__":
    raise SystemExit(main())
