"""CiA402 — Statusword 해석과 이 드라이브(MBDLN25BL)의 PDO 배치. 순수 함수라 장비 없이 시험한다.

M1 은 읽기 전용이다. 출력 PDO 는 m1_output() 하나로만 만들고, 그 안의 Controlword 는 늘 0 이다(Fault reset 비트도 없다).
서보를 켜는 Controlword 값은 이 파일 어디에도 없다.
"""
from __future__ import annotations

import struct
from typing import NamedTuple

# 드라이브에서 scan 으로 읽은 PDO 매핑(dynamic-bench/README.md PDO) — (index, subindex, bit 수). 순서가 곧 바이트 배치다.
RX_MAP = [(0x6040, 0, 16), (0x6060, 0, 8), (0x607A, 0, 32), (0x60B8, 0, 16)]                  # 출력 9 byte
TX_MAP = [(0x603F, 0, 16), (0x6041, 0, 16), (0x6061, 0, 8), (0x6064, 0, 32),
          (0x60B9, 0, 16), (0x60BA, 0, 32), (0x60F4, 0, 32), (0x60FD, 0, 32)]               # 입력 23 byte
OUT = struct.Struct("<HbiH")        # Controlword · Mode of operation · Target position · Touch probe function
IN = struct.Struct("<HHbiHiiI")     # TX_MAP 순서 그대로


class Inputs(NamedTuple):
    err: int         # 0x603F Error code
    sw: int          # 0x6041 Statusword
    mode: int        # 0x6061 Modes of operation display
    pos: int         # 0x6064 Position actual value (약 1 pulse = 1 µm)
    tp_status: int   # 0x60B9 Touch probe status
    tp_pos1: int     # 0x60BA Touch probe pos1 pos value
    ferr: int        # 0x60F4 Following error actual value
    di: int          # 0x60FD Digital inputs


def decode(raw: bytes) -> Inputs:
    return Inputs(*IN.unpack_from(raw))


def m1_output(pos: int) -> bytes:
    """M1 의 출력 PDO — Controlword 0 · Mode 0 · Target = 지금 위치 · 0x60B8 0. 이 밖의 출력은 만들지 않는다."""
    return OUT.pack(0, 0, pos, 0)


# CiA402 상태표 (mask, 값, 이름) — 위에서부터 맞는 첫 줄. 어느 줄에도 안 맞으면 UNKNOWN.
_STATES = (
    (0x4F, 0x00, "NOT_READY_TO_SWITCH_ON"),
    (0x4F, 0x40, "SWITCH_ON_DISABLED"),
    (0x6F, 0x21, "READY_TO_SWITCH_ON"),
    (0x6F, 0x23, "SWITCHED_ON"),
    (0x6F, 0x27, "OPERATION_ENABLED"),
    (0x6F, 0x07, "QUICK_STOP_ACTIVE"),
    (0x4F, 0x0F, "FAULT_REACTION_ACTIVE"),
    (0x4F, 0x08, "FAULT"),
)
# Controlword 를 0 으로만 보내는 M1 에서 드라이브가 있을 수 있는 상태는 이 둘뿐이다. 나머지(Fault 포함)는 전부 예상 밖이다.
M1_OK = ("NOT_READY_TO_SWITCH_ON", "SWITCH_ON_DISABLED")


def state(sw: int) -> str:
    for mask, val, name in _STATES:
        if sw & mask == val:
            return name
    return "UNKNOWN"


def m1_problem(prev: str | None, v: Inputs) -> str | None:
    """M1 에서 즉시 멈춰야 하면 이유 한 줄, 아니면 None. 지시문 안전 규칙 5 — 판단이 애매하면 멈추는 쪽."""
    cur = state(v.sw)
    if cur not in M1_OK:
        return f"예상 밖 상태 {cur} (statusword 0x{v.sw:04X})"
    if prev == "SWITCH_ON_DISABLED" and cur == "NOT_READY_TO_SWITCH_ON":
        return f"예상 밖 전이 {prev} → {cur} (statusword 0x{v.sw:04X})"
    if v.err:
        return f"에러 코드 0x603F = 0x{v.err:04X} (statusword 0x{v.sw:04X})"
    return None
