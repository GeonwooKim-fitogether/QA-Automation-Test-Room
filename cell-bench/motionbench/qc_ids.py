"""레일 QC 시험 ID — 이름의 정본은 dynamic-bench/design/qc-tests.md 1절. 실행기 · 실시간 기록 · 판정 도구가 이 목록 하나를 쓴다.

  ID           한글 이름    판정 대상                    운전
  GNSS-STATIC  GNSS-정지    GNSS 신호 세기               셀 정지 · 모터 없음
  GNSS-MOVE    GNSS-이동    GPS 원값 속도 · 위치          운전-등속(RUN-CONST) + 같은 속도로 움직이는 장면
  GNSS-FW      GNSS-펌웨어  IMU 판정을 거친 속도의 거리   보류 — 사람 같은 운전과 그에 맞춘 장면(LabSat 작업 2026-11-16 주). 실행기 없음
  IMU-STATIC   IMU-정지     IMU 영점 · 중력 크기          셀 정지 · 모터 없음
  IMU-MOVE     IMU-이동     IMU 속도 변화                 지금은 긴 거리 운전(IMU-MOVE_long). 운전-가속 기록으로 판정하는 안은 승인 대기
  IMU-FW       IMU-펌웨어   IMU 움직임 판정               운전-가속(RUN-ACCEL) · 정지 장면

이름은 2026-11-13 사용자 결정 — 센서(GNSS · IMU)와 층(정지 · 이동 · 펌웨어)이 이름에 드러난다. 화면과 문서에는 한글 이름(KO),
코드 · 파일 · 폴더에는 영문 ID 를 쓴다. 옛 ID(2026-11-12 의 GNSS-1 · GNSS-2 · IMU-1 · IMU-2 · FIRMWARE-1 · FIRMWARE-2)로 적힌
기록은 RENAMED 로 지금 ID 로 읽는다. 단 'FIRMWARE-2' 는 11-12 까지 등속 장면 동기 운전(지금 GNSS-MOVE)의 이름이었고 그 뒤 보류된
시험은 한 번도 돌지 않았으므로, 기록의 'FIRMWARE-2' 는 GNSS-MOVE 로 읽는다.

여러 시험을 한 기록에 함께 적을 때는 ' · ' 로 잇는다(예: "IMU-MOVE · IMU-FW") — 결과판이 이미 이 모양을 나눠 읽는다.

결과판 호환: 결과판(cell-bench/board · deploy/board)은 아직 옛 열쇠 `part`(글자 A · B · C)를 읽는다. 결과판이 `test` 를 읽게
고쳐질 때까지 실시간 기록(qc_now.json)과 판정 파일(protocol_*.json)에 `part` 를 함께 쓰고, 그 글자는 아래 두 표에서만 만든다.
결과판이 고쳐지면 두 표와 그것을 부르는 곳(qc_live.QcLive.frame · protocol_v4.conditions)을 지운다.
"""
from __future__ import annotations

TESTS = ("GNSS-STATIC", "GNSS-MOVE", "GNSS-FW", "IMU-STATIC", "IMU-MOVE", "IMU-FW")    # 보여 주는 차례 — GNSS 쪽 셋, IMU 쪽 셋
KO = {"GNSS-STATIC": "GNSS-정지", "GNSS-MOVE": "GNSS-이동", "GNSS-FW": "GNSS-펌웨어",
      "IMU-STATIC": "IMU-정지", "IMU-MOVE": "IMU-이동", "IMU-FW": "IMU-펌웨어"}            # 화면 · 문서에 쓰는 이름
STILL = ("GNSS-STATIC", "IMU-STATIC")            # 셀 정지 — 모터 없음
RAIL = ("GNSS-MOVE", "IMU-MOVE", "IMU-FW")       # 레일 운전 — 지금 실행기가 있는 것
SCENE_SYNC = ("GNSS-MOVE",)                      # 레일과 같은 속도로 움직이는 장면을 함께 튼다
HELD = ("GNSS-FW",)                              # 보류 — 목록에는 있지만 실행기 · 프리셋이 없다
RUNS = {"RUN-ACCEL": "운전-가속 — 등속 구간 없이 가속도 단계를 올렸다 내리는 운전(IMU-FW · 승인되면 IMU-MOVE · GNSS-FW 도 쓴다)",
        "RUN-CONST": "운전-등속 — 구간 1,500 mm, 속도 단계별 등속 운전(GNSS-MOVE)"}       # 운전 이름 — 프리셋 이름의 앞 낱말
# 시험 조건 모듈(2026-11-13 사용자 결정) — 한 칸에 조건 하나. 시험은 모듈 값의 조합(COMBOS)이다. 정본 qc-tests.md 1-1 · 1-2절.
MODULES = {
    "run": {"RUN-STILL": "운전-정지", "RUN-ACCEL": "운전-가속", "RUN-CONST": "운전-등속"},
    "src": {"SRC-LABSAT": "신호-LabSat", "SRC-SKY": "신호-야외"},
    "sat": {"SAT-12": "위성-12", "SAT-6": "위성-6", "SAT-SKY": "위성-하늘"},
    "gps": {"GPS-STILL": "GPS-정지", "GPS-SYNC": "GPS-동기", "GPS-REAL": "GPS-실제"},
    "att": {"ATT-0": "감쇠-0", "ATT-18": "감쇠-18", "ATT-NONE": "감쇠-없음"},
    "dock": {"DOCK-12": "배치-12", "DOCK-24": "배치-24"},
}
COMBOS = [   # (시험, 장소, run, src, sat, gps, att, dock) — 목표 조합. 실행기가 실제로 쓴 값은 기록 summary.json 에 따로 남는다
    ("GNSS-STATIC", "실내", "RUN-STILL", "SRC-LABSAT", "SAT-12", "GPS-STILL", "ATT-18", "DOCK-12"),
    ("GNSS-MOVE", "실내", "RUN-CONST", "SRC-LABSAT", "SAT-12", "GPS-SYNC", "ATT-0", "DOCK-12"),
    ("GNSS-FW", "실내", "RUN-ACCEL", "SRC-LABSAT", "SAT-12", "GPS-SYNC", "ATT-0", "DOCK-12"),
    ("IMU-STATIC", "실내", "RUN-STILL", "SRC-LABSAT", "SAT-12", "GPS-STILL", "ATT-0", "DOCK-24"),
    ("IMU-MOVE", "실내", "RUN-ACCEL", "SRC-LABSAT", "SAT-12", "GPS-STILL", "ATT-0", "DOCK-12"),
    ("IMU-FW", "실내", "RUN-ACCEL", "SRC-LABSAT", "SAT-12", "GPS-STILL", "ATT-0", "DOCK-12"),
    ("GNSS-STATIC", "야외", "RUN-STILL", "SRC-SKY", "SAT-SKY", "GPS-REAL", "ATT-NONE", "DOCK-12"),
    ("GNSS-MOVE", "야외", "RUN-CONST", "SRC-SKY", "SAT-SKY", "GPS-REAL", "ATT-NONE", "DOCK-12"),
    ("GNSS-FW", "야외", "RUN-ACCEL", "SRC-SKY", "SAT-SKY", "GPS-REAL", "ATT-NONE", "DOCK-12"),
]
KEYS = tuple(MODULES)


def combo(test: str, place: str = "실내") -> dict:
    """목표 조합 {run, src, sat, gps, att, dock}."""
    for row in COMBOS:
        if row[:2] == (test, place):
            return dict(zip(KEYS, row[2:]))
    raise KeyError(f"{test} · {place} 조합이 없다")


def conditions(**kw) -> dict:
    """실행기가 실제로 쓴 모듈 값 → summary.json 칸(run · src · sat · gps · att · dock). 여섯 칸을 모두 받는다.
    값은 MODULES 의 ID 이거나, 모듈 값으로 말할 수 없을 때(옛 운전 · 실행기가 정하지 않는 칸) 실제 상태를 적은 글이다."""
    if missing := [k for k in KEYS if not kw.get(k)]:
        raise ValueError(f"조건 칸이 빠졌다: {missing}")
    return {k: kw[k] for k in KEYS}


RENAMED = {"GNSS-1": "GNSS-STATIC", "GNSS-2": "GNSS-MOVE", "IMU-1": "IMU-STATIC", "IMU-2": "IMU-MOVE",
           "FIRMWARE-1": "IMU-FW", "FIRMWARE-2": "GNSS-MOVE"}                              # 옛 기록의 ID → 지금 ID (머리말)
SEP = " · "

# 결과판 호환 표 — 지울 때 함께 지운다(위 머리말)
_BOARD_LIVE_PART = {"GNSS-STATIC": "A", "IMU-STATIC": "A", "GNSS-MOVE": "C", "IMU-MOVE": "C", "IMU-FW": "C", "GNSS-FW": "C"}   # 실시간 화면: 정지 'A' · 레일 'C'(장면 동기 운전도 'C' 로 띄워 왔다)
_BOARD_JUDGED_PART = {"GNSS-STATIC": "A", "IMU-FW": "C", "GNSS-MOVE": "B"}                                                   # 판정 파일: protocol_v4 가 내는 세 시험


def parse(text: str) -> list[str]:
    """'IMU-MOVE,IMU-FW' · 'IMU-MOVE · IMU-FW' → ['IMU-MOVE', 'IMU-FW']. 목록에 없는 이름이면 ValueError."""
    parts = [x.strip() for x in str(text).replace(SEP.strip(), ",").split(",") if x.strip()]
    bad = [x for x in parts if x not in TESTS]
    if not parts or bad:
        raise ValueError(f"시험 ID {bad or text!r} — {', '.join(TESTS)} 중에서 고른다")
    return parts


def current(test: str) -> str:
    """옛 ID 면 지금 ID 로, 아니면 그대로."""
    return RENAMED.get(test, test)


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
    옛 ID(RENAMED)로 적힌 파일은 지금 ID 로 읽는다."""
    if conditions.get("test"):
        return RENAMED.get(conditions["test"], conditions["test"])
    return {v: k for k, v in _BOARD_JUDGED_PART.items()}.get(conditions.get("part"))
