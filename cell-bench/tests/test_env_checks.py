"""제어 PC 점검(tools/check_env.py)의 판정 순수 함수 검사 — 운영체제를 읽지도 바꾸지도 않는다.

FMEA P2(운영체제 고정)가 덮는 고장: 업데이트 자동 재시작(10-08 21:15 실제), 배터리 절전, 블루투스의 Wi-Fi 간섭(10-07 실제),
보안 프로그램, 재시작 뒤 자동 로그온. tools/install.ps1 이 설정하고, check_env.py 가 그 설정이 살아 있는지 매번 본다.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import check_env as ce  # noqa: E402

OK, WARN, BAD = True, None, False


def test_import_runs_no_checks():
    """불러오기만으로는 아무 점검도 돌지 않는다 — 셀 포트(UDP 60222)·플러그를 건드리지 않는다는 뜻이다."""
    assert ce.results == []


# --- powercfg /qh 출력 읽기 -------------------------------------------------

KO = """전원 구성표 GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (균형 조정)
  하위 그룹 GUID: 238c9fa8-0aad-41ed-83f4-97be242c8f20  (절전)
    전원 설정 GUID: 29f6c1db-86da-48c5-9fdb-f2b67b1f44da  (다음 시간 후 절전 모드로 전환)
      가능한 설정 단위: 초
    현재 AC 전원 설정 색인: 0x00000000
    현재 DC 전원 설정 색인: 0x00000384
"""
EN = """Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)
  Subgroup GUID: 4f971e89-eebd-4455-a8de-9e59040e7347  (Power buttons and lid)
    Power Setting GUID: 5ca83367-6e45-459f-a27b-476b1d01c936  (Lid close action)
      Possible Setting Index: 000
      Possible Setting Friendly Name: Do nothing
    Current AC Power Setting Index: 0x00000000
    Current DC Power Setting Index: 0x00000001
"""


@pytest.mark.parametrize("text,ac,dc", [(KO, 0, 900), (EN, 0, 1)], ids=["korean", "english"])
def test_power_index_reads_ac_and_dc_in_both_languages(text, ac, dc):
    assert ce.power_index(text, "AC") == ac
    assert ce.power_index(text, "DC") == dc


def test_power_index_missing_or_max():
    assert ce.power_index("", "DC") is None
    assert ce.power_index("Current DC Power Setting Index: 0xffffffff", "DC") == 0xFFFFFFFF


def test_timeout_text():
    assert ce.timeout_text(0, "절전") == "안 함"
    assert ce.timeout_text(900, "절전") == "15분 뒤 절전"
    assert ce.timeout_text(45, "절전") == "45초 뒤 절전"
    assert ce.timeout_text(None, "절전") == "읽지 못함"


def test_usb_suspend():
    assert ce.usb_suspend_check(0, 0)[0] is OK
    assert ce.usb_suspend_check(1, 1)[0] is BAD                # 이 PC 의 지금 값
    assert ce.usb_suspend_check(0, 1)[0] is BAD
    assert ce.usb_suspend_check(None, 0)[0] is WARN            # 설정이 숨겨진 PC


# --- 업데이트 ---------------------------------------------------------------

def test_reboot_policy():
    assert ce.reboot_policy_check(1, 4)[0] is OK
    v = ce.reboot_policy_check(1, 3)                            # 이 PC 의 지금 값: 정책은 있으나 문서상 4 에서만 적용
    assert v[0] is WARN and "AUOptions=3" in v[1]
    assert ce.reboot_policy_check(None, None)[0] is BAD
    assert ce.reboot_policy_check(0, 4)[0] is BAD


NOW = datetime(2026, 10, 10, 0, 0, 0, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")                   # Windows 가 쓰는 꼴 (예 2026-11-13T15:00:00Z)


@pytest.mark.parametrize("left,expect", [
    (timedelta(days=35), OK),
    (timedelta(days=7, seconds=1), OK),                         # 7일 넘게 남음 → 통과
    (timedelta(days=7), WARN),                                   # 7일 이내 → 곧 만료
    (timedelta(seconds=1), WARN),
    (timedelta(0), BAD),                                         # 만료 시각 = 지금 → 만료
    (timedelta(days=-1), BAD),
])
def test_pause_boundaries(left, expect):
    v = ce.pause_check(iso(NOW + left), NOW, policy_on=True)
    assert v[0] is expect
    if expect is WARN:
        assert "곧 만료" in v[1] and "-Only pause" in v[2]
    if expect is BAD:
        assert "만료" in v[1] and "-Only pause" in v[2]


def test_pause_real_value_from_this_pc():
    assert ce.pause_check("2026-11-13T15:00:00Z", NOW)[0] is OK


def test_pause_absent_depends_on_policy():
    assert ce.pause_check(None, NOW, policy_on=True)[0] is WARN
    assert ce.pause_check(None, NOW, policy_on=False)[0] is BAD
    assert ce.pause_check("", NOW, policy_on=False)[0] is BAD


def test_pause_unreadable_and_not_yet_applied():
    assert ce.pause_check("다음 달", NOW)[0] is WARN
    far = iso(NOW + timedelta(days=30))
    assert ce.pause_check(far, NOW, engine_paused=0)[0] is WARN  # 값은 썼는데 Windows Update 가 아직 안 받음
    assert ce.pause_check(far, NOW, engine_paused=1)[0] is OK


def test_reboot_pending():
    assert ce.reboot_pending_check(True)[0] is WARN
    assert ce.reboot_pending_check(False)[0] is OK


# --- 블루투스 · 네트워크 어댑터 ----------------------------------------------

THIS_PC_BT = [   # Get-PnpDevice -Class Bluetooth -PresentOnly (10-10, 이 PC)
    {"Status": "OK", "FriendlyName": "인텔(R) 무선 Bluetooth(R)", "InstanceId": "USB\\VID_8087&PID_0AAA\\5&20FCD7A4&0&10"},
    {"Status": "OK", "FriendlyName": "Microsoft Bluetooth LE Enumerator", "InstanceId": "BTH\\MS_BTHLE\\6&183D2029&0&3"},
    {"Status": "OK", "FriendlyName": "Microsoft Bluetooth Enumerator", "InstanceId": "BTH\\MS_BTHBRB\\6&183D2029&0&1"},
]


def test_bluetooth_radio_on_is_bad():
    v = ce.bluetooth_check(THIS_PC_BT)
    assert v[0] is BAD and "인텔" in v[1] and "Enumerator" not in v[1] and "-Only bluetooth" in v[2]


def test_bluetooth_radio_disabled_or_absent_is_ok():
    off = [{"Status": "Error", "FriendlyName": "인텔(R) 무선 Bluetooth(R)", "InstanceId": "USB\\VID_8087&PID_0AAA\\5"}]
    assert ce.bluetooth_check(off)[0] is OK
    assert ce.bluetooth_check([])[0] is OK
    assert ce.bluetooth_check(THIS_PC_BT[1:])[0] is OK          # 열거자만 남은 것은 판정하지 않는다


@pytest.mark.parametrize("cap,expect", [(None, BAD), (0, BAD), (0x110, BAD), (24, OK), (0x118, OK), (8, OK)])
def test_nic_power_bit(cap, expect):
    assert ce.nic_power_check([{"Name": "Wi-Fi", "Cap": cap}])[0] is expect


def test_nic_power_lists_only_the_bad_adapter():
    v = ce.nic_power_check([{"Name": "Wi-Fi", "Cap": 24}, {"Name": "이더넷 3", "Cap": None}])
    assert v[0] is BAD and "이더넷 3" in v[1] and "Wi-Fi" not in v[1]
    assert ce.nic_power_check([])[0] is WARN


# --- 자동 재시작 로그온 ------------------------------------------------------

def test_arso():
    assert ce.arso_check(1, None, 1)[0] is BAD                  # 정책으로 꺼짐
    assert ce.arso_check(None, None, 1, opted_out=True)[0] is BAD
    assert ce.arso_check(0, 1, 2)[0] is OK                      # 항상 모드면 BitLocker 무관
    assert ce.arso_check(0, None, 1)[0] is OK
    v = ce.arso_check(0, None, 2)                               # 이 PC: BitLocker 꺼짐(2) → 실제로는 자동 로그온 안 함
    assert v[0] is WARN and "-ArsoAlways" in v[2]
    assert ce.arso_check(0, None, None)[0] is WARN
    v = ce.arso_check(None, None, 1)
    assert v[0] is OK and "기본값" in v[1]


# --- 감시자 작업 -------------------------------------------------------------

@pytest.mark.parametrize("info,expect", [
    (None, BAD),
    ({"State": "Running", "LastTaskResult": 267009, "LastRunTime": "10-10 07:50"}, OK),
    ({"State": "Ready", "LastTaskResult": 0, "LastRunTime": "10-10 07:50"}, WARN),
    ({"State": "Ready", "LastTaskResult": 267011, "LastRunTime": ""}, WARN),     # 아직 실행 안 됨
    ({"State": "Ready", "LastTaskResult": 2147942402, "LastRunTime": "10-10 07:50"}, BAD),  # 0x80070002
    ({"State": "Disabled", "LastTaskResult": 0, "LastRunTime": ""}, BAD),
])
def test_task(info, expect):
    assert ce.task_check(info)[0] is expect


def test_task_shows_hex_code_on_failure():
    v = ce.task_check({"State": "Ready", "LastTaskResult": 2147942402, "LastRunTime": ""})
    assert "0x80070002" in v[1]


# --- Slack 웹훅 · 보안 프로그램 ----------------------------------------------

def test_webhook():
    v = ce.webhook_check(True)
    assert v[0] is OK and "hooks.slack.com" not in v[1]
    v = ce.webhook_check(True, "cloud")                             # keyring 에 없어도 Vault 에서 받으면 통과 — 출처만 보인다
    assert v[0] is OK and "클라우드 Vault" in v[1] and "hooks.slack.com" not in v[1]
    v = ce.webhook_check(False)
    assert v[0] is BAD and "remote_setup.py" in v[2]
    assert ce.webhook_check(None)[0] is WARN


def test_security_programs_grouped_by_product():
    names = ["python", "chrome", "nossvc", "nosstarter.npe", "ASDSvc", "CrossEXService", "ObCrossEXService", "delfino"]
    found = ce.security_programs(names)
    assert found == ["nProtect: nosstarter.npe, nossvc", "AhnLab: ASDSvc", "INISAFE: CrossEXService, ObCrossEXService"]
    v = ce.security_check(names)
    assert v[0] is WARN and "nProtect" in v[1]


def test_security_none_found():
    assert ce.security_check(["python", "explorer"]) == (OK, "감지 안 됨", "")
