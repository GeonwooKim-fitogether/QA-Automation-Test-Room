"""parse_gps — 라이브 0x09 의 GPS 칸. 바이트를 박아 둔 패킷으로만 본다(실제 기록 패킷은 아직 없다)."""
import struct
from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench import protocol as P


def _pkt(hist=range(600), fmt="<600H", fix=b"R", sats_byte=(9 << 3) | 0b101):
    d = bytearray(1320); d[:4] = b"FITO"; d[4] = 0x09
    struct.pack_into("<H", d, 11, 11746); struct.pack_into("<b", d, 50, -45); d[51] = 72; d[52] = 98; d[53] = 4
    d[41] = 37; d[47] = fix[0]; d[48] = sats_byte; d[49] = 41; d[55] = 0b1011
    struct.pack_into(fmt, d, 57, *hist)
    return bytes(d)


def test_fields_at_firmware_offsets():
    g = P.parse_gps(_pkt())
    assert (g.serial, g.fix, g.sats, g.cn0, g.validity, g.hspeed_raw, g.hspeed_unit) == (11746, "R", 9, 41, 0b1011, 37, "unit_unverified")
    assert g.speed_history == tuple(range(600))                 # 기본 가정: 리틀 엔디안 · 부호 없음 · 끝이 최신
    assert P.parse_live(_pkt()) == P.Live(11746, -45, 72, 98, 4)  # 같은 패킷의 기존 칸은 그대로


def test_both_satellite_readings():
    g = P.parse_gps(_pkt(sats_byte=0b01001101))                 # 상위 5비트 9 · 하위 5비트 13
    assert (g.sats, g.sats_ios) == (9, 13)


def test_speed_history_assumptions_can_be_switched():
    assert P.parse_gps(_pkt(fmt=">600H"), speed_fmt=">H").speed_history == tuple(range(600))
    assert P.parse_gps(_pkt(fmt=">600H")).speed_history[1] == 256          # 엔디안을 잘못 고르면 값이 256 배로 뛴다
    assert P.parse_gps(_pkt(hist=[-5] * 600, fmt="<600h"), speed_fmt="<h").speed_history[0] == -5
    assert P.parse_gps(_pkt(), newest_first=True).speed_history[:3] == (599, 598, 597)


def test_rejects_what_parse_live_rejects_and_marks_odd_fix_bytes():
    assert P.parse_gps(_pkt()[:-1]) is None
    assert P.parse_gps(b"FITO" + bytes([0x16]) + bytes(1315)) is None
    assert P.parse_gps(_pkt(fix=b"\x00")).fix == "0x00"


def test_hspeed_is_two_bytes_little_endian():
    """펌웨어 new_live_message_t: hspeed 는 uint16 @41–42 (cell-y4 master 0d4099f5 include/types.h) — 255 를 넘는 값도 그대로."""
    d = bytearray(_pkt()); struct.pack_into("<H", d, 41, 300)
    assert P.parse_gps(bytes(d)).hspeed_raw == 300
    assert P.parse_gps(bytes(d)).speed_history == tuple(range(600))   # 옆 칸(vspeed @43 · speed_history @57)은 그대로


def test_position_time_and_sequence_at_firmware_offsets():
    """펌웨어 new_live_message_t(cell-y4 master 0d4099f5): 순번 epoch u32 @7 · UTC 연 u16 @13 · 월 @15 · 일 @16 · 시각 u32 @17(hhmmsscc)
    · 위도 · 경도 int64 @21 · @29 — util.c convert_degree_to_nmea 형식(도 × 10¹⁰ + 분 × 10⁸)."""
    d = bytearray(_pkt())
    struct.pack_into("<I", d, 7, 4362)
    struct.pack_into("<HBB", d, 13, 2026, 10, 28)
    struct.pack_into("<I", d, 17, 5_553_974)                        # 05:55:39.74
    struct.pack_into("<qq", d, 21, 37 * 10**10 + 30 * 10**8, -(127 * 10**10 + 15 * 10**8))   # 37°30′ · −127°15′
    g = P.parse_gps(bytes(d))
    assert g.seq == 4362
    assert g.utc == datetime(2026, 10, 28, 5, 55, 39, 740000, tzinfo=timezone.utc).timestamp()
    assert (g.lat_deg, g.lon_deg) == (37.5, -127.25)


def test_utc_is_none_when_cell_has_no_date():
    assert P.parse_gps(_pkt()).utc is None                          # 날짜 칸이 0 — 시각을 지어내지 않는다


def test_sequence_is_four_bytes():
    d = bytearray(_pkt()); struct.pack_into("<I", d, 7, 70_000)
    assert P.parse_gps(bytes(d)).seq == 70_000
