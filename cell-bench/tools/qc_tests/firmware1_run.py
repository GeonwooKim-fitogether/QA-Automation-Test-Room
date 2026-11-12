"""FIRMWARE-1 · 움직임 판단 시험 실행기 — 정지 장면 아래 레일 150 ↔ 750 mm · 100 mm/s 를 가속도 50 · 500 mm/s² 로 각 N번, 끝나면 셀 기록 받기.
준비(플러그 끔 · 정지 장면 · 감쇠 0 · 위치 잡기 · 배터리 확인)와 마무리 · 받기는 run_common.run 이 tools/qc_round.py 로 한다.
운전 부분(drive)은 묶음 실행기(qc_bundle.py)도 부른다. 입회 표지(tools/attend.py)가 없으면 run_rail 이 기다렸다가, 운전자가 만들면 바로 이어 간다.
결과: data/FIRMWARE-1_<날짜>[꼬리표]/{a50,a500}/runs.json · run.log · summary.json · tap.log, 받은 셀 기록 data/ftg_y_<시각>
판정: firmware1_judge.py FIRMWARE-1_<날짜>[꼬리표] (켜짐 · 꺼짐 시간)
사용: python -X utf8 firmware1_run.py [--n 10] [--accels 50,500] [--layout Y-shift] [--tag 꼬리표] [--no-extract]"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_common as RC  # noqa: E402

TEST = "FIRMWARE-1"
PRESETS = {50: "FIRMWARE-1_a50", 500: "FIRMWARE-1_a500"}     # 가속도 mm/s² → 승인 프리셋(150 ↔ 750 mm · 100 mm/s · 왕복 3번 · 끝 멈춤 5초)


def add_options(ap) -> None:
    ap.add_argument("--accels", default="50,500", help="FIRMWARE-1 가속도 mm/s², 쉼표로 (기본 50,500)")


def accels(a) -> list[int]:
    return [int(x) for x in a.accels.split(",") if x.strip()]


def check(a) -> None:
    for acc in accels(a):
        if acc not in PRESETS or not (RC.Q.run_rail.PRESETS / f"{PRESETS[acc]}.json").exists():
            raise SystemExit(f"FIRMWARE-1 가속도 {acc} mm/s² 의 승인 프리셋이 없다 — 있는 것: {sorted(PRESETS)}")


def tap_minutes(a) -> float:
    return 3.0 * a.n * len(accels(a))          # 운전 한 번 약 95초 + 사이 5초, 넉넉히


def drive(rec, say, ls, tap, a):
    """운전만 — 가속도마다 n 번. 준비 · 받기는 하지 않는다. (코드, 실시간 기록)을 돌려준다."""
    rc = 0
    for acc in accels(a):
        t0 = time.time()
        rc = RC.Q.run_rail.main([TEST, "--n", str(a.n), "--out", str(rec.out / f"a{acc}"), "--preset", PRESETS[acc]])
        rec.summary[f"a{acc}"] = {"rc": rc, "preset": PRESETS[acc], "from": t0, "to": time.time()}
        rec.save()
        say(f"{TEST} 가속도 {acc} mm/s² 끝 · 코드 {rc}")
        if rc != 0:
            break
    return rc, tap


def main(argv=None) -> int:
    return RC.run([TEST], argv, __doc__)


if __name__ == "__main__":
    raise SystemExit(main())
