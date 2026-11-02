"""입회 표지 만들기 — 레일 옆에 있는 운전자가 직접 확인하고 60분짜리 표지를 만든다(모터 시험 C · B 전에 한 번).

  python cell-bench/tools/attend.py --by 홍길동

확인 세 가지를 읽고 '현장확인' 을 그대로 입력해야 만들어진다. 표지는 cell-bench/data/motion_attended 이고,
모터 실행기(run_motion.py · tools/run_rail.py)는 이 표지의 남은 시간 안에서만 레일을 움직인다.
원격(채팅 · 원격 데스크톱)으로는 만들지 않는다 — 레일 옆에 사람이 있는 것이 이 표지의 뜻이다.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

MARKER = Path(__file__).resolve().parents[1] / "data" / "motion_attended"
CHECKS = ["나는 지금 레일 옆에 있고, 모터 전원 스위치에 손이 닿는다",
          "레일 위와 왕복 구간(150 ~ 1,650 mm)에 손 · 물건이 없다",
          "셀 독이 레일 위에 단단히 고정돼 있다"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--by", required=True, help="운전자 이름")
    ap.add_argument("--minutes", type=int, default=60, choices=range(10, 241), metavar="10~240",
                    help="표지 유효 시간. 한 라운드(C 10번 + B 30번 ≈ 80분)를 멈춤 없이 돌리려면 120 이상")
    a = ap.parse_args(argv)
    print("모터 시험 전 확인 — 하나라도 아니면 Ctrl+C 로 멈춘다")
    for i, c in enumerate(CHECKS, 1):
        print(f"  {i}. {c}")
    if input("모두 맞으면 '현장확인' 을 입력: ").strip() != "현장확인":
        print("표지를 만들지 않았다")
        return 1
    now = time.time()
    MARKER.write_text(json.dumps({"until": int(now + a.minutes * 60), "by": a.by, "created": int(now),
                                  "reason": f"QC 레일 시험 — 운전자 현장 확인({a.minutes}분)"}, ensure_ascii=False), encoding="utf-8")
    print(f"입회 표지를 만들었다 — {time.strftime('%H:%M', time.localtime(now + a.minutes * 60))} 까지 모터 시험을 할 수 있다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
