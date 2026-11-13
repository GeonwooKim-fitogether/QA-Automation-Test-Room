"""묶음 실행기 — 시험 여러 개를 준비 한 번 · 셀 기록 받기 한 번으로 이어 돈다. 운전 부분만 시험마다 차례로 부른다.

  python -X utf8 qc_bundle.py --tests IMU-STATIC,IMU-MOVE                          # 센서 시험 둘 — 판정한 뒤
  python -X utf8 qc_bundle.py --tests IMU-FW,GNSS-MOVE                    # IMU 펌웨어 시험과 GNSS 레일 시험(앞 시험 불합격 셀은 판정에서 빠진다)
  python -X utf8 qc_bundle.py --tests GNSS-MOVE --speeds 100 --layout Y-rot --tag _map   # 칸 지도(11-10 에는 FIRMWARE-2 이름으로 돌렸다)

단계별 거르기(motionbench/qc_gate.py): 시작할 때 data/qc_gate_<세트>.json 을 읽어 앞 시험에서 불합격한 셀을 시험마다 판정 대상에서 뺀다
(독에는 있어도 된다, --include-failed 로 끈다). 묶음 안에서는 앞 시험의 판정이 아직 없으므로, 센서 시험 결과로 펌웨어 시험을 거르려면
센서 시험 묶음 → 판정 스크립트 → 펌웨어 시험 묶음 차례로 나눠 돈다.

차례는 주는 순서와 상관없이 IMU-STATIC → IMU-MOVE → IMU-FW → GNSS-MOVE 다(운영 순서를 따르되 장면을 바꾸는 GNSS-MOVE 는 맨 끝). GNSS-FW 는 보류라 받지 않는다.
기록은 시험마다 제 폴더(data/<시험 ID>_<날짜><꼬리표>)에 쓰고, 실행 기록(run.log)은 모든 폴더에 같은 줄을 쓴다.
셀 기록은 한 번 받아 모든 폴더의 summary.json 에 적는다 — 판정 스크립트는 시험 폴더 하나만 보면 된다.
시험마다의 옵션(--accels · --speeds · --boots …)은 그 시험 실행기와 같다. 엮는 방법은 run_common.py 머리말.
GNSS-STATIC 은 함께 돌리지 않는다 — 독 1 · 3행에만 꽂고 장면을 정해진 자리부터 다시 틀어야 해서 다른 시험과 독 · 장면이 다르다(gnss_static_run.py 혼자).
모터를 움직이는 시험이 있으면 제어 PC 의 C:\\Dev\\dynamic-bench\\.venv\\Scripts\\python.exe 로 띄운다(모터 통신 모듈 pysoem).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_common as RC  # noqa: E402
from motionbench import qc_ids  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--tests")
    known, _ = ap.parse_known_args(argv)
    if not known.tests:
        print(__doc__)
        raise SystemExit("--tests 로 함께 돌릴 시험 ID 를 준다 — 예 --tests IMU-MOVE,IMU-FW")
    try:
        tests = qc_ids.parse(known.tests)
    except ValueError as e:
        raise SystemExit(str(e))
    return RC.run(tests, argv, __doc__, bundle=True)


if __name__ == "__main__":
    raise SystemExit(main())
