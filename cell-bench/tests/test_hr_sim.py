"""심박대 흉내 펌웨어(firmware/hr-sim)의 이름 규칙 — 굽는 스크립트 · 펌웨어 · 셀 0x20 명령이 같은 이름을 쓰는지."""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cellbench import protocol as P

HR = ROOT / "firmware" / "hr-sim"
_spec = importlib.util.spec_from_file_location("hr_flash_all", HR / "flash_all.py")
F = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(F)


def test_name_is_prefix_plus_last_four_hex_upper():
    assert F.name_for_mac("34:85:18:12:a1:b2") == "HRSIM-A1B2"
    assert F.name_for_mac("348518_12A1B2") == "HRSIM-A1B2"


def test_name_rejects_non_mac():
    for bad in ("", "COM5", "34:85:18:12:a1"):
        try:
            F.name_for_mac(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_name_fits_ble_id_frame_unchanged():
    name = F.name_for_mac("ff:ff:ff:ff:ff:ff")
    f = P.ble_id_frame(name)
    assert f[4] == P.MSG_SET_BLE_ID
    assert f[8:8 + len(name)] == name.encode() and f[8 + len(name)] == 0 and len(name.encode()) <= 24


def test_mac_parsed_from_esptool_v4_and_v5_output():
    v5 = "Chip type:          ESP32-C3 (QFN32) (revision v0.4)\nMAC:                34:85:18:12:A1:B2\n"
    v4 = "Chip is ESP32-C3 (QFN32) (revision v0.4)\nMAC: 34:85:18:12:a1:b2\nUploading stub...\n"
    assert F.mac_from_esptool(v5) == F.mac_from_esptool(v4) == "34:85:18:12:a1:b2"
    assert F.mac_from_esptool("no mac here") is None


def test_firmware_uses_same_name_rule():
    """main.c 의 snprintf 가 접두어 + MAC 마지막 두 바이트 대문자 16진이어야 스크립트 기록과 맞는다."""
    src = (HR / "main" / "main.c").read_text(encoding="utf-8")
    m = re.search(r'snprintf\(s_name, sizeof s_name, "([^"]+)", mac\[4\], mac\[5\]\)', src)
    assert m and m.group(1) == F.NAME_PREFIX + "%02X%02X"


def test_flash_offsets_match_esp32c3_layout():
    a = F.FLASH_ARGS
    offsets = {a[i]: Path(a[i + 1]).name for i in range(len(a) - 1) if a[i].startswith("0x")}
    assert offsets == {"0x0": "bootloader.bin", "0x8000": "partition-table.bin", "0x10000": "hr-sim.bin"}
    assert a[a.index("--chip") + 1] == "esp32c3" and a[a.index("--flash-size") + 1] == "4MB"
