"""셀 무선 프로토콜 — iOS Live 앱(OhCoachSingleton.connectSendData)과 같은 틀.

명령 프레임 54B: 'FITO' + type(1) + 47 + serial(2, 항상 0) + data(45, 0 패딩) + checksum(sum % 256)
라이브 1320B(UDP, type 0x09): serial @11(u16 LE) · rssi @50(i8) · hr @51 · battery @52 · state @53
                             GPS 는 parse_gps: hspeed @41 · fix @47 · 위성 @48 · C/N0 @49 · validity @55 · speed_history @57–1256
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


def ble_id_ok(ack: bytes) -> bool:
    """0x20(심박대 이름) 응답이 '받았다'인가 — 셀은 'FITO' 20 01 … 로 답한다 (2026-10-15 실측 24/24: FITO 20 01 01 54)."""
    return len(ack) >= 6 and ack[:4] == HEADER and ack[4] == MSG_SET_BLE_ID and ack[5] == 0x01


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


# --- GPS 칸 (라이브 0x09). 오프셋은 parse_live 와 같은 기준(패킷 첫 바이트 'F' = 0)이다.
# 펌웨어 fitogether-org/cell-y4-ESP32-S3-firmware master 08fe785e (양산 Y4 계열 v0.99.71, v0.99.61 이후 변경 없음):
#   41 hspeed — 단위 [확인 필요], 폭도 적혀 있지 않아 1 바이트로 읽는다 · 47 fix mode ASCII N/A/D/F/R · 48 사용 위성 수 = 상위 5비트(>> 3)
#   49 평균 C/N0 dBHz u8 · 55 gps_validity 비트(KINEXON=0 빌드, 비트 뜻 [확인 필요]) · 57–1256 speed_history 600 × 16비트 mm/s, 10 Hz × 60 s
# speed_history 의 엔디안 · 부호 · 순서는 [확인 필요] — 기록된 실제 패킷이 아직 없다. 기본 가정은 리틀 엔디안(ESP32, 시리얼 칸과 같다) ·
# 부호 없음 · 0번이 가장 오래된 것(끝이 최신 — 기본값)이고, 셋 다 parse_gps 인자로 바꿔 돌려 볼 수 있다.
GPS_SPEED_AT, GPS_SPEED_N = 57, 600


@dataclass(frozen=True)
class Gps:
    serial: int
    fix: str                          # byte 47 — N 없음 · A 단독 · D DGPS · F RTK float · R RTK fix (글자가 아니면 0xNN)
    sats: int                         # byte 48 >> 3 — 펌웨어의 뜻
    sats_ios: int                     # byte 48 & 0x1F — iOS Packet.swift 의 해석(틀렸다는 의심). 두 해석을 나란히 보려고 둔다
    cn0: int                          # byte 49 평균 C/N0 dBHz
    validity: int                     # byte 55 비트 묶음
    hspeed_raw: int                   # byte 41 원값
    speed_history: tuple[int, ...]    # mm/s · 600개 · 100 ms 간격 · 오래된 것 → 최신
    hspeed_unit: str = "unit_unverified"   # 단위를 확정하면 바꾼다 — speed_history 와 대조해서


def parse_gps(d: bytes, speed_fmt: str = "<H", newest_first: bool = False) -> Gps | None:
    """1320B 라이브(0x09)의 GPS 칸 — parse_live 가 받는 패킷이 아니면 None.
    speed_fmt: speed_history 한 칸의 struct 형식("<H" 리틀·부호 없음 · ">H" 빅 · "<h" 부호 있음).
    newest_first: 0번이 최신이라고 보고 뒤집는다 — 돌려주는 speed_history 는 늘 오래된 것 → 최신."""
    live = parse_live(d)
    if live is None:
        return None
    hist = struct.unpack_from(f"{speed_fmt[0]}{GPS_SPEED_N}{speed_fmt[1:]}", d, GPS_SPEED_AT)
    return Gps(serial=live.serial, fix=chr(d[47]) if 32 < d[47] < 127 else f"0x{d[47]:02X}",
               sats=d[48] >> 3, sats_ios=d[48] & 0x1F, cn0=d[49], validity=d[55], hspeed_raw=d[41],
               speed_history=hist[::-1] if newest_first else hist)


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
