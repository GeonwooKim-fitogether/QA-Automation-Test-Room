"""레일 QC 시험 ID — 이름의 정본은 dynamic-bench/design/qc-tests.md 1절. 실행기 · 실시간 기록 · 판정 도구가 이 목록 하나를 쓴다.

  GNSS-1      GNSS 수신 시험     셀 정지 · 모터 없음
  IMU-1       IMU 정지 시험      셀 정지 · 모터 없음
  IMU-2       IMU 레일 시험      레일 운전 · 정지 장면
  FIRMWARE-1  움직임 판단 시험   레일 운전 · 정지 장면
  FIRMWARE-2  속도 · 거리 시험   레일 운전 · 레일과 같은 속도로 움직이는 장면

여러 시험을 한 기록에 함께 적을 때는 ' · ' 로 잇는다(예: "IMU-2 · FIRMWARE-1") — 결과판이 이미 이 모양을 나눠 읽는다.

결과판 호환: 결과판(cell-bench/board · deploy/board)은 아직 옛 열쇠 `part`(글자 A · B · C)를 읽는다. 결과판이 `test` 를 읽게
고쳐질 때까지 실시간 기록(qc_now.json)과 판정 파일(protocol_*.json)에 `part` 를 함께 쓰고, 그 글자는 아래 두 표에서만 만든다.
결과판이 고쳐지면 두 표와 그것을 부르는 곳(qc_live.QcLive.frame · protocol_v4.conditions)을 지운다.
"""
from __future__ import annotations

TESTS = ("GNSS-1", "IMU-1", "IMU-2", "FIRMWARE-1", "FIRMWARE-2")
STILL = ("GNSS-1", "IMU-1")                      # 셀 정지 — 모터 없음
RAIL = ("IMU-2", "FIRMWARE-1", "FIRMWARE-2")     # 레일 운전
SCENE_SYNC = ("FIRMWARE-2",)                     # 레일과 같은 속도로 움직이는 장면을 함께 튼다
SEP = " · "

# 결과판 호환 표 — 지울 때 함께 지운다(위 머리말)
_BOARD_LIVE_PART = {"GNSS-1": "A", "IMU-1": "A", "IMU-2": "C", "FIRMWARE-1": "C", "FIRMWARE-2": "C"}   # 실시간 화면: 정지 'A' · 레일 'C'(속도 · 거리 시험도 'C' 로 띄워 왔다)
_BOARD_JUDGED_PART = {"GNSS-1": "A", "FIRMWARE-1": "C", "FIRMWARE-2": "B"}                              # 판정 파일: protocol_v4 가 내는 세 시험


def parse(text: str) -> list[str]:
    """'IMU-2,FIRMWARE-1' · 'IMU-2 · FIRMWARE-1' → ['IMU-2', 'FIRMWARE-1']. 목록에 없는 이름이면 ValueError."""
    parts = [x.strip() for x in str(text).replace(SEP.strip(), ",").split(",") if x.strip()]
    bad = [x for x in parts if x not in TESTS]
    if not parts or bad:
        raise ValueError(f"시험 ID {bad or text!r} — {', '.join(TESTS)} 중에서 고른다")
    return parts


def join(tests: list[str]) -> str:
    return SEP.join(tests)


def board_live_part(tests: list[str]) -> str:
    """결과판 호환 — 실시간 기록의 옛 `part` 글자. 정지 시험만이면 'A', 레일 시험이 하나라도 있으면 'C'."""
    return "C" if any(_BOARD_LIVE_PART[t] == "C" for t in tests) else "A"


def board_judged_part(test: str) -> str:
    """결과판 호환 — 판정 파일의 옛 `part` 글자."""
    return _BOARD_JUDGED_PART[test]


def from_record(conditions: dict) -> str | None:
    """판정 파일의 conditions 에서 시험 ID 를 읽는다. 2026-11-12 전 파일은 `test` 가 없고 `part` 만 있어 그 글자로 읽는다."""
    if conditions.get("test"):
        return conditions["test"]
    return {v: k for k, v in _BOARD_JUDGED_PART.items()}.get(conditions.get("part"))
