"""라이브허브 와이파이 점검(motionbench/wifi.py) — netsh 출력 읽기만 시험한다(장비 없이)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motionbench import wifi  # noqa: E402

IF_ON = "    이름                   : Wi-Fi\n    상태                   : 연결됨\n    SSID                   : FTG-3D9F\n    AP BSSID               : 78:22:88:a5:3d:a0\n"
IF_OFF = "    이름                   : Wi-Fi\n    상태                   : 연결되지 않음\n    송수신 장치 상태           : 하드웨어 켬\n"
NETS = "인터페이스 이름 : Wi-Fi\n\nSSID 1 : Fitogether-3E9F\n    네트워크 종류 : 인프라\nSSID 2 : \nSSID 3 : FTG-3D9F\n"


def test_current_ssid_skips_bssid_and_none_when_disconnected():
    assert wifi.parse_current(IF_ON) == "FTG-3D9F"
    assert wifi.parse_current(IF_OFF) is None


def test_visible_networks_drop_blank_names():
    assert wifi.parse_visible(NETS) == ["Fitogether-3E9F", "FTG-3D9F"]
