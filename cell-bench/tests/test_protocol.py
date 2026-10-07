import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench import protocol as P


def test_frame_is_54_bytes_with_checksum():
    f = P.wake_frame()
    assert len(f) == 54
    assert f[:4] == b"FITO" and f[4] == 0x10 and f[5] == 47 and f[8] == 1
    assert f[-1] == sum(f[:-1]) % 256


def test_upload_frame_offset_little_endian():
    f = P.upload_frame(0x01020304)
    assert f[8:12] == b"\x04\x03\x02\x01"


def test_ble_id_frame_truncates_to_24():
    f = P.ble_id_frame("A" * 40)
    assert f[8:32] == b"A" * 24 and f[32] == 0


def test_parse_live_offsets():
    d = bytearray(1320); d[:4] = b"FITO"; d[4] = 0x09
    struct.pack_into("<H", d, 11, 11746); struct.pack_into("<b", d, 50, -45); d[51] = 72; d[52] = 98; d[53] = 4
    live = P.parse_live(bytes(d))
    assert live and live.serial == 11746 and live.rssi == -45 and live.hr == 72 and live.battery == 98 and live.state == 4


def test_parse_live_rejects_other_lengths_and_types():
    assert P.parse_live(b"FITO" + bytes([0x16]) + b"\0\0\0\0") is None
    d = bytearray(1320); d[:4] = b"FITO"; d[4] = 0x16
    assert P.parse_live(bytes(d)) is None


def test_parse_status():
    d = b"FITO" + bytes([0x11, 0x06]) + struct.pack("<b", -50) + bytes([99]) + struct.pack("<i", 32_000_000) + b"\x00"
    st = P.parse_status(d)
    assert st and st.rssi == -50 and st.battery == 99 and st.size == 32_000_000


def test_check_block_unsigned_sum():
    data = bytes([0xFF]) * 4096                      # 합이 2^20 을 넘어 unsigned 32 비트가 필요하다
    block = struct.pack("<II", 8192, sum(data) & 0xFFFFFFFF) + data
    pos, got, ok = P.check_block(block, 8192)
    assert ok and pos == 8192 and got == data
    assert P.check_block(block, 0)[2] is False


def test_waiting_only_when_live_stops():
    from cellbench.cells import CellLive
    c = CellLive("1.2.3.4", 70, 0, 4, -10, t=100.0, t_wait=100.4)
    assert c.waiting is False                       # 정상: 0x09 와 0x16 이 섞여 옴
    c = CellLive("1.2.3.4", 70, 0, 4, -10, t=100.0, t_wait=104.0)
    assert c.waiting is True                        # 0x09 는 끊기고 0x16 만 옴
