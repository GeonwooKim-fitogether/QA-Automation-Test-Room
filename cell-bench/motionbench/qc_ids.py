"""레일 QC 시험 ID — 이름의 정본은 dynamic-bench/design/qc-tests.md 1절. 실행기 · 실시간 기록 · 판정 도구가 이 목록 하나를 쓴다.

  GNSS-1      GNSS 수신 시험          셀 정지 · 모터 없음
  GNSS-2      GNSS 레일 시험          레일 등속 운전 · 레일과 같은 속도로 움직이는 장면 — GPS 원값 속도 · 위치를 레일 정답과 비교
  IMU-1       IMU 정지 시험           셀 정지 · 모터 없음
  IMU-2       IMU 레일 시험           레일 운전 · 정지 장면
  FIRMWARE-1  IMU 펌웨어 시험         레일 가속도 단계 운전(등속 없음) · 정지 장면 — IMU 움직임 판정이 제때 켜지고 꺼지는가
  FIRMWARE-2  속도 · 거리 시험        보류 — 사람 같은 운전과 그에 맞춘 장면이 필요하다(LabSat 작업 2026-11-16 주). 실행기 없음

2026-11-12 사용자 결정으로 시험을 다섯에서 여섯으로 나눴다. 그날까지 'FIRMWARE-2' 로 부르던 등속 장면 동기 운전은 GPS 원값을 보는
시험이라 GNSS-2 가 됐다. 그 전 기록에 적힌 'FIRMWARE-2' 는 from_record 가 GNSS-2 로 읽는다(새 FIRMWARE-2 는 아직 돈 적이 없다).

여러 시험을 한 기록에 함께 적을 때는 ' · ' 로 잇는다(예: "IMU-2 · FIRMWARE-1") — 결과판이 이미 이 모양을 나눠 읽는다.

결과판 호환: 결과판(cell-bench/board · deploy/board)은 아직 옛 열쇠 `part`(글자 A · B · C)를 읽는다. 결과판이 `test` 를 읽게
고쳐질 때까지 실시간 기록(qc_now.json)과 판정 파일(protocol_*.json)에 `part` 를 함께 쓰고, 그 글자는 아래 두 표에서만 만든다.
결과판이 고쳐지면 두 표와 그것을 부르는 곳(qc_live.QcLive.frame · protocol_v4.conditions)을 지운다.
"""
from __future__ import annotations

TESTS = ("GNSS-1", "GNSS-2", "IMU-1", "IMU-2", "FIRMWARE-1", "FIRMWARE-2")
STILL = ("GNSS-1", "IMU-1")                      # 셀 정지 — 모터 없음
RAIL = ("GNSS-2", "IMU-2", "FIRMWARE-1")         # 레일 운전 — 지금 실행기가 있는 것
SCENE_SYNC = ("GNSS-2",)                         # 레일과 같은 속도로 움직이는 장면을 함께 튼다
HELD = ("FIRMWARE-2",)                           # 보류 — 목록에는 있지만 실행기 · 프리셋이 없다
RENAMED = {"FIRMWARE-2": "GNSS-2"}               # 2026-11-12 결정 전 기록의 이름 → 지금 이름
SEP = " · "

# 결과판 호환 표 — 지울 때 함께 지운다(위 머리말)
_BOARD_LIVE_PART = {"GNSS-1": "A", "IMU-1": "A", "GNSS-2": "C", "IMU-2": "C", "FIRMWARE-1": "C", "FIRMWARE-2": "C"}   # 실시간 화면: 정지 'A' · 레일 'C'(장면 동기 운전도 'C' 로 띄워 왔다)
_BOARD_JUDGED_PART = {"GNSS-1": "A", "FIRMWARE-1": "C", "GNSS-2": "B"}                                                   # 판정 파일: protocol_v4 가 내는 세 시험


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
    """판정 파일의 conditions 에서 시험 ID 를 읽는다. 2026-11-12 전 파일은 `test` 가 없고 `part` 만 있어 그 글자로 읽는다.
    여섯 시험으로 나누기 전 이름(RENAMED — 장면 동기 운전의 'FIRMWARE-2')은 지금 이름(GNSS-2)으로 읽는다."""
    if conditions.get("test"):
        return RENAMED.get(conditions["test"], conditions["test"])
    return {v: k for k, v in _BOARD_JUDGED_PART.items()}.get(conditions.get("part"))
