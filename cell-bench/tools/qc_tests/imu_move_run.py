"""IMU-MOVE · IMU 레일 시험 실행기 — 정지 장면 아래 긴 거리 운전(150 ↔ 1,550 mm · 300 mm/s · 500 mm/s²) N번, 끝나면 셀 기록 받기.
준비(플러그 끔 · 정지 장면 · 감쇠 0 · 위치 잡기 · 배터리 확인)와 마무리 · 받기는 run_common.run 이 tools/qc_round.py 로 한다.
운전 부분(drive)은 묶음 실행기(qc_bundle.py)도 부른다 — IMU-FW 과 함께 돌리면 준비 · 받기가 한 번이다.
결과: data/IMU-MOVE_<날짜>[꼬리표]/long/runs.json · run.log · summary.json · tap.log, 받은 셀 기록 data/ftg_y_<시각>
판정: imu_move_judge.py IMU-MOVE_<날짜>[꼬리표] --lag -0.3
사용: python -X utf8 imu_move_run.py [--n 10] [--layout Y-shift] [--tag 꼬리표] [--no-extract]"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_common as RC  # noqa: E402

TEST = "IMU-MOVE"
PRESET = "IMU-MOVE_long"         # 승인 프리셋 — 150 ↔ 1,550 mm · 300 mm/s · 500 mm/s² · 왕복 3번 · 끝 멈춤 5초


def add_options(ap) -> None:
    pass


def check(a) -> None:
    if not (RC.Q.run_rail.PRESETS / f"{PRESET}.json").exists():
        raise SystemExit(f"IMU-MOVE 승인 프리셋 {PRESET}.json 이 없다")


def tap_minutes(a) -> float:
    return 4.0 * a.n


def conditions(a) -> dict:
    """이 실행기가 실제로 쓰는 조건 모듈 값 — summary.json 의 run · src · sat · gps · att · dock (qc-tests.md 1-1절).
    레일 준비(qc_round.rail_prep)가 정지 장면 · 감쇠 0 을 걸고, 독 배치는 꽉 찬 독(Y-shift 등)이다."""
    return RC.qc_ids.conditions(run="IMU-MOVE_long(옛 운전 — 150 ↔ 1,550 mm 300 mm/s 왕복, 운전-가속 · 운전-등속 어느 쪽도 아님)", src="SRC-LABSAT",
                                 sat="S01-Static-GLE-8h 처음부터(장면 구간을 고정하지 않음 — 위성 9 ~ 12개, SAT-12 아님)", gps="GPS-STILL", att="ATT-0", dock="DOCK-24")


def drive(rec, say, ls, tap, a):
    """운전만 — 긴 거리 n 번. 준비 · 받기는 하지 않는다. (코드, 실시간 기록)을 돌려준다."""
    t0 = time.time()
    rc = RC.Q.run_rail.main([TEST, "--n", str(a.n), "--out", str(rec.out / "long"), "--preset", PRESET])
    rec.summary["long"] = {"rc": rc, "preset": PRESET, "from": t0, "to": time.time()}
    rec.save()
    say(f"{TEST} 긴 거리 끝 · 코드 {rc}")
    return rc, tap


def main(argv=None) -> int:
    return RC.run([TEST], argv, __doc__)


if __name__ == "__main__":
    raise SystemExit(main())
