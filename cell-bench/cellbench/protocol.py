"""셀 무선 프로토콜 — iOS Live 앱(OhCoachSingleton.connectSendData)과 같은 틀.

명령 프레임 54B: 'FITO' + type(1) + 47 + serial(2, 항상 0) + data(45, 0 패딩) + checksum(sum % 256)
라이브 1320B(UDP, type 0x09): serial @11(u16 LE) · rssi @50(i8) · hr @51 · battery @52 · state @53
상태 응답 13B: 'FITO' 11 06 rssi(i8) battery size(i32 LE) cs
업로드 블록 4104B: pos(u32 LE) + checksum(u32 LE, 바이트 합) + 4096 data · 마지막은 '@' 패딩 · 끝 표식 MARK
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

MSG_LIVE = 0x09
MSG_WAKE = 0x10
MSG_STATUS = 0x11
MSG_UPLOAD = 0x12
MSG_DELETE = 0x13
MSG_POWER_OFF = 0x14
MSG_RESET_TCP = 0x15          # 쓰지 않는다: TCP 대기 모드로 켜져 측정을 안 한다 (2026-10-07 실측)
MSG_WAIT_FOR_TCP = 0x16       # 셀이 깨워 주길 기다릴 때 9B 로 보냄
MSG_SET_BLE_ID = 0x20         # 심박대 이름 24B
MSG_RESET_NORMAL = 0x26       # 측정으로 돌아가는 재부팅. 재부팅은 항상 이것

LIVE_LEN = 1320
STATUS_LEN = 13
BLOCK_LEN = 4104
BLOCK_DATA = 4096
MARK = b"GPSENDGPSENDGPSEND"
HEADER = b"FITO"


def frame(msg_type: int, data: bytes = b"") -> bytes:
    """54B 명령 프레임을 만든다."""
    if len(data) > 45:
        raise ValueError("data 는 45B 이하")
    b = bytearray(HEADER) + bytes([msg_type, 47, 0, 0]) + data.ljust(45, b"\0")
    b.append(sum(b) % 256)
    return bytes(b)


def wake_frame() -> bytes:
    return frame(MSG_WAKE, b"\x01")


def upload_frame(offset: int = 0) -> bytes:
    return frame(MSG_UPLOAD, struct.pack("<I", offset))


def ble_id_frame(name: str) -> bytes:
    raw = name.encode("utf-8")[:24]
    return frame(MSG_SET_BLE_ID, raw)


@dataclass(frozen=True)
class Live:
    serial: int
    rssi: int
    hr: int
    battery: int
    state: int


def parse_live(d: bytes) -> Live | None:
    """1320B 라이브 메시지면 Live, 아니면 None."""
    if len(d) != LIVE_LEN or d[:4] != HEADER or d[4] != MSG_LIVE:
        return None
    return Live(
        serial=struct.unpack_from("<H", d, 11)[0],
        rssi=struct.unpack_from("<b", d, 50)[0],
        hr=d[51],
        battery=d[52],
        state=d[53],
    )


@dataclass(frozen=True)
class Status:
    rssi: int
    battery: int
    size: int


def parse_status(d: bytes) -> Status | None:
    if len(d) < STATUS_LEN or d[:4] != HEADER or d[4] != MSG_STATUS:
        return None
    return Status(
        rssi=struct.unpack_from("<b", d, 6)[0],
        battery=d[7],
        size=struct.unpack_from("<i", d, 8)[0],
    )


def check_block(block: bytes, expect_pos: int) -> tuple[int, bytes, bool]:
    """업로드 블록 하나를 풀어 (pos, data, ok) 를 돌려준다. ok = 위치와 체크섬이 맞음."""
    pos, cs = struct.unpack_from("<II", block, 0)
    data = block[8:BLOCK_LEN]
    ok = pos == expect_pos and (sum(data) & 0xFFFFFFFF) == cs
    return pos, data, ok
