"""IMU-MOVE · IMU 레일 시험 실행기 — 운전-가속: 등속 구간 없이 가속 직후 감속하는 왕복을 가속도 0.5 · 1 · 2 · 4 · 8 m/s² 차례로 올리기만,
단계마다 승인 프리셋 RUN-ACCEL_a<가속도>(왕복 5번 · 끝 멈춤 0 · 속도 상한 2 m/s)를 --n 번(기본 10). 2026-11-20 사용자 합의 절차
(dynamic-bench/design/qc-tests.md 3절 IMU-이동 ①~⑦). 2026-11-19 까지는 옛 운전(긴 거리 IMU-MOVE_long 10번)이었다.
단계마다 추종 오차가 문턱(motionbench/safety.ferr_limit(2000))의 90 % 를 넘거나, 알람 · 비정상 종료면 다음 단계로 가지 않는다(IMU-FW 와 같은 기준).
준비(플러그 끔 · 신호원 · 위치 잡기 · 독 위 대수 · 배터리 확인)와 마무리 · 셀 기록 받기는 run_common.run 이 tools/qc_round.py 로 한다.
신호원: --src labsat(기본)이면 정지 장면 · 감쇠 0 을 걸고, --src repeater 면 LabSat 을 건드리지 않는다(GPS 중계기 날). IMU 판정 값에는
GPS 값이 들어가지 않는다 — 시험 전 위치를 잡았는지만 보고, 시험 중에는 신호원을 바꾸지 않는다.
독 위 대수: --dock-cells(기본 12 = 배치-12) 대가 실시간 데이터를 보내야 시작한다. 0 이면 세기만 한다.
결과: data/IMU-MOVE_<날짜>[꼬리표]/<차례>_a<가속도>/runs.json · run.log · summary.json(results 에 단계마다 정상 횟수 · 추종 오차 · 최고 속도),
     받은 셀 기록 data/ftg_y_<시각>
판정: 운전-가속 기록용 판정 도구는 아직 없다(판정 항목 합의 대기). 옛 운전 기록의 판정은 imu_move_judge.py.
사용: python -X utf8 imu_move_run.py [--src labsat|repeater] [--n 10] [--move-steps 500,1000,2000,4000,8000] [--dock-cells 12]
      [--layout Y-shift] [--tag 꼬리표] [--no-extract]"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_common as RC  # noqa: E402
from motionbench import safety  # noqa: E402

TEST = "IMU-MOVE"
STEPS = "500,1000,2000,4000,8000"                       # 가속도 mm/s² — 올리기만(2026-11-20 합의)
PRESET = "RUN-ACCEL_a{}"                                 # 승인 프리셋 이름(dynamic-bench/dev/presets) — 구간은 프리셋 파일에 있다
FERR_V_MM_S = 2000                                       # 추종 오차 문턱을 잡을 속도 — 프리셋의 속도 상한
FERR_STOP = 0.9                                          # 문턱의 이만큼을 넘으면 다음 단계로 가지 않는다


def add_options(ap) -> None:
    # 이름이 IMU-FW 의 --steps 와 겹치지 않게 --move-steps — 묶음 실행기가 두 시험의 옵션을 한 파서에 모은다
    ap.add_argument("--move-steps", default=STEPS, help=f"IMU-MOVE 가속도 단계 mm/s², 쉼표로 (기본 {STEPS}). 단계마다 --n 번")
    ap.add_argument("--src", choices=["labsat", "repeater"], default="labsat",
                    help="신호원 — labsat: 정지 장면 · 감쇠 0 을 건다 / repeater: GPS 중계기, LabSat 을 건드리지 않는다")
    ap.add_argument("--dock-cells", type=int, default=12, help="독 위에서 실시간 데이터를 보내야 하는 대수(기본 12 = 배치-12, 0 이면 세기만)")


def steps(a) -> list[int]:
    return [int(x) for x in a.move_steps.split(",") if x.strip()]


def check(a) -> None:
    if not steps(a):
        raise SystemExit("IMU-MOVE 가속도 단계가 비었다 (--move-steps)")
    for acc in sorted(set(steps(a))):
        if not (RC.Q.run_rail.PRESETS / f"{PRESET.format(acc)}.json").exists():
            raise SystemExit(f"IMU-MOVE 가속도 {acc} mm/s² 의 승인 프리셋 {PRESET.format(acc)}.json 이 없다")


def tap_minutes(a) -> float:
    return 1.0 * a.n * len(steps(a)) + 15               # 운전 한 번 약 40초(2026-11-20) + 준비 시간, 넉넉히


def expected_cells(a) -> int | None:
    return a.dock_cells or None


def conditions(a) -> dict:
    """이 실행기가 실제로 쓰는 조건 모듈 값 — summary.json 의 run · src · sat · gps · att · dock (qc-tests.md 1-1절)."""
    dock = "DOCK-12" if a.dock_cells == 12 else "DOCK-24" if a.dock_cells == 24 else f"독 위 {a.dock_cells or '세기만'}대"
    if a.src == "repeater":
        return RC.qc_ids.conditions(run="RUN-ACCEL", src="SRC-REPEATER", sat="중계기 — 실외 안테나의 실제 하늘(위성 수는 실시간 기록)",
                                     gps="GPS-STILL", att="ATT-NONE", dock=dock)
    return RC.qc_ids.conditions(run="RUN-ACCEL", src="SRC-LABSAT",
                                 sat="S01-Static-GLE-8h 처음부터(장면 구간을 고정하지 않음 — 위성 9 ~ 12개, SAT-12 아님)",
                                 gps="GPS-STILL", att="ATT-0", dock=dock)


def stage_result(sub: Path) -> dict:
    """단계 폴더의 runs.json → 모든 실행의 모터 기록 요약(정상 횟수 · 추종 오차 최대 · 최고 속도 · 알람 · 종료)."""
    runs = json.loads((sub / "runs.json").read_text(encoding="utf-8")) if (sub / "runs.json").exists() else []
    ferr, speed, alarms, exits = [], [], [], []
    for r in runs:
        mj = Path(r["motor_csv"]).with_suffix(".json") if r.get("motor_csv") else None
        m = json.loads(mj.read_text(encoding="utf-8")) if mj and mj.exists() else {}
        legs = m.get("legs") or []
        ferr += [abs(x.get("ferr_max") or 0) for x in legs]
        speed += [x.get("speed_max_mm_s") or 0 for x in legs]
        alarms += [m["alarms"]] if m.get("alarms") else []
        exits.append(m.get("exit"))
    return {"runs": len(runs), "ok": sum(1 for r in runs if r.get("rc") == 0), "ferr_max_um": max(ferr, default=None),
            "speed_max_mm_s": max(speed, default=None), "alarms": alarms, "exits": exits}


def drive(rec, say, ls, tap, a):
    """운전만 — 가속도 단계마다 n 번. 멈추는 기준에 걸리면 다음 단계로 가지 않는다. (코드, 실시간 기록)을 돌려준다."""
    lim = safety.ferr_limit(FERR_V_MM_S)
    rec.summary.update({"steps_mm_s2": steps(a), "n_per_step": a.n, "ferr_limit_um": lim, "results": []})
    say(f"── {TEST} 운전-가속 {steps(a)} mm/s² · 단계마다 {a.n}번(왕복 5번씩) · 신호원 {a.src}")
    rc = 0
    for i, acc in enumerate(steps(a), 1):
        sub = rec.out / f"{i:02d}_a{acc}"
        t0 = time.time()
        rc = RC.Q.run_rail.main([TEST, "--n", str(a.n), "--out", str(sub), "--preset", PRESET.format(acc)])
        r = {"a_mm_s2": acc, "rc": rc, "from": t0, "to": time.time(), "preset": PRESET.format(acc)} | stage_result(sub)
        rec.summary["results"].append(r)
        rec.save()
        say(f"가속도 {acc} mm/s² 끝 · 코드 {rc} · 정상 {r['ok']}/{r['runs']}번 · 추종 오차 최대 {r['ferr_max_um']} µm(문턱 {lim}) · "
            f"최고 속도 {r['speed_max_mm_s']} mm/s · 알람 {r['alarms'] or '없음'}")
        if rc != 0 or r["alarms"] or r["ferr_max_um"] is None or r["ferr_max_um"] > lim * FERR_STOP:
            say("멈추는 기준에 걸려 다음 단계로 가지 않는다")
            return rc or 1, tap
    return rc, tap


def main(argv=None) -> int:
    return RC.run([TEST], argv, __doc__)


if __name__ == "__main__":
    raise SystemExit(main())
