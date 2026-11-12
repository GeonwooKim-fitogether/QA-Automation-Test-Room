"""IMU-2 · IMU 레일 시험 실행기 — 정지 장면 아래 긴 거리 운전(150 ↔ 1,550 mm · 300 mm/s · 500 mm/s²) N번, 끝나면 셀 기록 받기.
준비(플러그 끔 · 정지 장면 · 감쇠 0 · 위치 잡기 · 배터리 확인)와 마무리 · 받기는 run_common.run 이 tools/qc_round.py 로 한다.
운전 부분(drive)은 묶음 실행기(qc_bundle.py)도 부른다 — FIRMWARE-1 과 함께 돌리면 준비 · 받기가 한 번이다.
결과: data/IMU-2_<날짜>[꼬리표]/long/runs.json · run.log · summary.json · tap.log, 받은 셀 기록 data/ftg_y_<시각>
판정: imu2_judge.py IMU-2_<날짜>[꼬리표] --lag -0.3
사용: python -X utf8 imu2_run.py [--n 10] [--layout Y-shift] [--tag 꼬리표] [--no-extract]"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_common as RC  # noqa: E402

TEST = "IMU-2"
PRESET = "IMU-2_long"         # 승인 프리셋 — 150 ↔ 1,550 mm · 300 mm/s · 500 mm/s² · 왕복 3번 · 끝 멈춤 5초


def add_options(ap) -> None:
    pass


def check(a) -> None:
    if not (RC.Q.run_rail.PRESETS / f"{PRESET}.json").exists():
        raise SystemExit(f"IMU-2 승인 프리셋 {PRESET}.json 이 없다")


def tap_minutes(a) -> float:
    return 4.0 * a.n


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
