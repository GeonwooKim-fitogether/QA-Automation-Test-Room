"""제어 PC 점검 — 이 PC 가 시험대를 돌릴 준비가 됐는지 하나씩 확인한다. 아무 설정도 바꾸지 않는다.

  python tools/check_env.py

새 노트북으로 옮겼을 때 이것 하나를 돌려 ✗ 가 하나도 없으면 된다(! 는 참고). ✗ 에는 고치는 방법이 함께 나온다
(방법의 자세한 설명은 docs/control-pc-setup.md).
시험이 돌고 있으면 셀 신호 포트를 열지 않고 data/now.json 으로 대신 확인한다.

운영체제 고정 항목(업데이트 재시작 · 절전 · 블루투스 · 자동 로그온 · 감시자 작업)은 tools/install.ps1 이 한 번에
설정하고, 여기서는 그 설정이 아직 살아 있는지 매번 읽기만 한다. 판정은 아래 '판정 순수 함수'가 하고
(tests/test_env_checks.py 가 검사), 실제로 읽는 일은 power_checks()·os_checks() 의 얇은 래퍼가 한다.
"""
from __future__ import annotations

import json
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cellbench.config import Config  # noqa: E402

cfg = Config()
results: list[tuple[str, str, str, str]] = []      # (상태, 항목, 결과, 고치는 법)


def add(ok: bool | None, item: str, got: str, fix: str = "") -> None:
    results.append(("✓" if ok else "!" if ok is None else "✗", item, got, "" if ok else fix))


# ---------------------------------------------------------------------------
# 판정 순수 함수 — 명령 출력이나 레지스트리 값을 받아 (상태, 결과, 고치는 법) 을 돌려준다.
# 운영체제를 읽지도 바꾸지도 않으므로 장비 없이 검사할 수 있다.
# 상태는 add() 의 ok 와 같다: True = ✓ 통과, None = ! 참고, False = ✗ 고쳐야 함.
# ---------------------------------------------------------------------------
Verdict = tuple       # (상태 bool|None, 결과 str, 고치는 법 str)

# 실행 정책이 기본(Restricted)인 PC 에서도 돌도록 -ExecutionPolicy Bypass -File 로 부른다
INSTALL = r"관리자 PowerShell(cell-bench 폴더): powershell -ExecutionPolicy Bypass -File tools\install.ps1 -Only"
TASK_NAME = "CellBench Supervisor"            # install_supervisor.ps1 이 등록하는 감시자 작업 이름
LID = {0: "아무 것도 안 함", 1: "절전", 2: "최대 절전", 3: "시스템 종료"}

# 실행 중이면 파이썬을 막을 수 있는 국내 보안 프로그램 (프로세스 이름, 대소문자 무시). 이름만 보여 준다.
SECURITY_PROGRAMS = {
    "nProtect": re.compile(r"^(nossvc|nosstarter|npkcmsvc|npkcsvc|npupdate|nprotect)", re.I),
    "AhnLab": re.compile(r"^(asdsvc|asdcli|asdup|v3lite|v3lsvc|v3ltray|v3svc|v3tray|ahnlab)", re.I),
    "INISAFE": re.compile(r"(crossex|inisafe)", re.I),
}

# 작업 스케줄러의 마지막 실행 결과 중 '정상'으로 볼 것 (그 밖의 0 아닌 값은 비정상 종료)
TASK_RESULT = {0: "정상 종료", 0x41301: "실행 중", 0x41303: "아직 한 번도 실행 안 됨"}


def power_index(text: str, current: str = "AC") -> int | None:
    """powercfg /qh 출력에서 전원 연결(AC) 또는 배터리(DC) 의 현재 색인을 읽는다. 영문·한글 출력 둘 다 받는다."""
    m = re.search(rf"(?:Current {current} Power Setting Index|{current} 전원 설정 색인)\s*:\s*(0x[0-9a-fA-F]+)", text)
    return int(m.group(1), 16) if m else None


def timeout_text(seconds: int | None, verb: str) -> str:
    """절전·최대 절전 대기 시간(초)을 사람이 읽는 말로. 0 은 '안 함'."""
    if seconds is None:
        return "읽지 못함"
    return "안 함" if seconds == 0 else f"{seconds // 60}분 뒤 {verb}" if seconds % 60 == 0 else f"{seconds}초 뒤 {verb}"


def usb_suspend_check(ac: int | None, dc: int | None) -> Verdict:
    """USB 선택적 절전 — 켜져 있으면 USB 이더넷·허브가 쉬는 동안 끊길 수 있다."""
    if ac is None or dc is None:
        return None, "읽지 못함 (이 PC 에 없는 설정일 수 있음)", ""
    if ac == 0 and dc == 0:
        return True, "사용 안 함", ""
    return False, f"사용 중 (전원 연결 {'끔' if ac == 0 else '켬'} · 배터리 {'끔' if dc == 0 else '켬'})", f"{INSTALL} power"


def reboot_policy_check(no_auto_reboot: int | None, au_options: int | None) -> Verdict:
    """정책 NoAutoRebootWithLoggedOnUsers — 로그온한 사용자가 있으면 업데이트 뒤 자동 재시작하지 않는다.

    Microsoft 문서는 이 정책이 AUOptions=4(자동 다운로드·예약 설치)일 때만 적용된다고 적는다.
    """
    if no_auto_reboot != 1:
        return False, "정책 없음 — 사용 시간 밖이면 업데이트 뒤 자동 재시작된다", f"{INSTALL} policy"
    if au_options != 4:
        return None, f"정책 있음 · 단 AUOptions={au_options} (문서상 4 일 때만 적용)", f"{INSTALL} policy"
    return True, "로그온 중에는 자동 재시작 안 함 (AUOptions=4)", ""


def pause_check(expiry: str | None, now: datetime, policy_on: bool = False,
                engine_paused: int | None = None, warn_days: int = 7) -> Verdict:
    """업데이트 일시 중지 만료일(ISO 8601 UTC, 예 2026-11-13T15:00:00Z) → 판정.

    만료까지 warn_days 일 이하면 ! (곧 만료), 지났으면 ✗. 일시 중지가 아예 없으면 정책이 있을 때 !, 없을 때 ✗.
    engine_paused 는 Windows Update 가 실제로 받아들였는지(UpdatePolicy\\Settings\\PausedQualityStatus, 1=멈춤).
    """
    fix = f"{INSTALL} pause"
    if not expiry:
        if policy_on:
            return None, "일시 중지 안 함 (자동 재시작은 정책이 막는 중)", fix
        return False, "일시 중지 안 함", fix
    try:
        end = datetime.fromisoformat(expiry.strip().replace("Z", "+00:00"))
    except ValueError:
        return None, f"읽지 못함 ({expiry})", "설정 → Windows 업데이트 화면에서 확인"
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    left = end - now
    day = end.astimezone().strftime("%Y-%m-%d")
    if left <= timedelta(0):
        return False, f"{day} 에 만료됨", fix
    if left <= timedelta(days=warn_days):
        return None, f"{day} 까지 · {left.total_seconds() / 86400:.1f}일 남음 — 곧 만료", f"{fix} 로 갱신"
    if engine_paused == 0:
        return None, f"{day} 까지로 설정됐으나 Windows Update 가 아직 반영하지 않음", "설정 → Windows 업데이트 화면에 '일시 중지됨'이 보이는지 확인"
    return True, f"{day} 까지 ({left.days}일 남음)", ""


def reboot_pending_check(pending: bool) -> Verdict:
    """RebootRequired 키가 있으면 업데이트가 재시작을 기다리는 중이다."""
    if pending:
        return None, "업데이트 재시작이 예약돼 있음", "정책이 자동 재시작을 막는 동안, 사이클 경계에서 사람이 재시작한다"
    return True, "없음", ""


def bluetooth_check(devices: list[dict]) -> Verdict:
    """Get-PnpDevice -Class Bluetooth 목록 → 판정. 무선 장치(어댑터)가 켜져 있으면 ✗.

    BTH·SWD 로 시작하는 것은 어댑터 아래에 붙는 열거자라 판정에서 뺀다 — 어댑터를 끄면 함께 사라진다.
    """
    radios = [d for d in devices if not str(d.get("InstanceId") or "").upper().startswith(("BTH", "SWD"))]
    if not radios:
        return True, "블루투스 장치 없음", ""
    on = [d for d in radios if d.get("Status") in ("OK", "Degraded")]
    if on:
        return False, "켜짐: " + ", ".join(str(d.get("FriendlyName") or "?") for d in on), \
            f"{INSTALL} bluetooth (PC 블루투스가 Wi-Fi 를 끊은 적이 있다, 10-07)"
    return True, "꺼짐 (장치 사용 안 함)", ""


def nic_power_check(adapters: list[dict]) -> Verdict:
    """네트워크 어댑터 [{Name, Cap(PnPCapabilities)}] → '전원 절약을 위해 끌 수 있음'이 해제됐는지.

    PnPCapabilities 의 0x08 비트(NDIS_DEVICE_DISABLE_PM)가 서 있으면 해제된 것이다. 값이 없으면 기본(끌 수 있음).
    """
    if not adapters:
        return None, "어댑터를 읽지 못함", ""
    allowed = [str(a.get("Name")) for a in adapters if not (int(a.get("Cap") or 0) & 0x08)]
    if allowed:
        return False, "절전으로 꺼질 수 있음: " + ", ".join(allowed), f"{INSTALL} nicpower (다음 재부팅부터 적용)"
    return True, "꺼지지 않게 설정됨: " + ", ".join(str(a.get("Name")) for a in adapters), ""


def arso_check(disable: int | None, mode: int | None, bitlocker: int | None, opted_out: bool = False) -> Verdict:
    """자동 재시작 로그온(ARSO) — 업데이트 재시작 뒤 마지막 사용자로 자동 로그온해 잠근다.

    disable  : 정책 DisableAutomaticRestartSignOn (0 켬 · 1 끔 · 없음 = 기본 켬)
    mode     : 정책 AutomaticRestartSignOnConfig (1 = BitLocker 와 무관하게 항상. 없으면 'BitLocker 가 켜져 있을 때만')
    bitlocker: 시스템 드라이브 System.Volume.BitLockerProtection 값. 1 만 '켜짐'으로 본다(그 밖의 값은 문서가 없어 보수적으로).
    opted_out: 사용자가 '로그인 정보를 사용해 업데이트 후 자동 설정 완료'를 끈 경우(UserARSO OptOut=1)
    """
    fix = f"{INSTALL} arso"
    if disable == 1:
        return False, "꺼짐 (정책)", fix
    if disable is None and opted_out:
        return False, "꺼짐 (사용자 설정)", fix
    base = "켜짐" if disable == 0 else "켜짐 (정책 없음 · 기본값)"
    if mode == 1:
        return True, base + " · BitLocker 와 무관하게 항상", ""
    if bitlocker == 1:
        return True, base + " · BitLocker 켜짐", ""
    why = "BitLocker 상태를 읽지 못함" if bitlocker is None else f"BitLocker 켜짐이 확인되지 않음(값 {bitlocker})"
    return None, f"{base} · 그러나 {why} — 이대로면 업데이트 재시작 뒤 자동 로그온하지 않는다", \
        f"사람이 정할 일: {INSTALL} arso -ArsoAlways (docs/control-pc-setup.md '자동 로그온')"


def task_check(info: dict | None) -> Verdict:
    """감시자 작업 {State, LastTaskResult, LastRunTime} → 판정. 없으면(None) 등록 안 된 것."""
    if not info:
        return False, "등록 안 됨", f"{INSTALL} supervisor"
    state = str(info.get("State") or "")
    code = int(info.get("LastTaskResult") or 0) & 0xFFFFFFFF
    last = f" · 마지막 실행 {info['LastRunTime']}" if info.get("LastRunTime") else ""
    if state == "Disabled":
        return False, "등록됐으나 '사용 안 함' 상태", "작업 스케줄러 → CellBench Supervisor → 사용"
    if state == "Running":
        return True, "실행 중" + last, ""
    if code in TASK_RESULT:
        return None, f"등록됨 · 지금은 실행 중 아님 ({TASK_RESULT[code]}){last}", "다음 로그온에 시작된다. 지금 띄우려면 작업 스케줄러에서 실행"
    return False, f"등록됨 · 실행 중 아님 · 마지막 결과 0x{code:X} (비정상 종료){last}", \
        "작업 스케줄러 → CellBench Supervisor → 기록 탭, 그리고 data/ 의 감시자 로그 확인"


def webhook_check(present: bool | None, source: str | None = None) -> Verdict:
    """Slack 웹훅이 있는지와 출처(자격 증명 관리자 · 클라우드 Vault)만 본다. 값은 보여 주지 않는다."""
    if present is None:
        return None, "읽지 못함 (keyring 오류)", ""
    if present:
        return True, "있음" + (" · 클라우드 Vault 에서 받음" if source == "cloud" else "") + " (값은 표시하지 않음)", ""
    return (False, "없음 — 자격 증명 관리자에도 클라우드 Vault 에도 없다. 이상이 나도 휴대폰 알림이 가지 않는다",
            "Vault 에 cell_bench_slack_webhook 을 넣는다(docs/remote-access.md 7절) · 또는 python tools/remote_setup.py")


def security_programs(names: list[str]) -> list[str]:
    """실행 중인 프로세스 이름 목록 → 'nProtect: nossvc, nosstarter.npe' 처럼 제품별로 묶은 목록."""
    out = []
    for label, pat in SECURITY_PROGRAMS.items():
        hit = sorted({n for n in names if pat.search(n)}, key=str.lower)
        if hit:
            out.append(f"{label}: {', '.join(hit)}")
    return out


def security_check(names: list[str]) -> Verdict:
    found = security_programs(names)
    if not found:
        return True, "감지 안 됨", ""
    return None, " / ".join(found), "이 프로그램이 python.exe 를 막지 않는지 — 각 프로그램의 예외(허용) 목록에 파이썬 등록 확인"


# ---------------------------------------------------------------------------
# 읽기 래퍼 — 운영체제에서 값을 읽기만 한다
# ---------------------------------------------------------------------------
def run_text(args: list[str]) -> str:
    """콘솔 코드페이지가 PC 마다 달라(UTF-8 / CP949) 둘 다 시도해 읽는다."""
    try:
        raw = subprocess.run(args, capture_output=True, timeout=20).stdout
    except Exception:
        return ""
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def ps(cmd: str) -> str:
    try:
        return subprocess.run(["powershell", "-NoProfile", "-Command", "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + cmd], capture_output=True, text=True,
                              timeout=20, encoding="utf-8", errors="replace").stdout.strip()
    except Exception:
        return ""


def ps_json(cmd: str):
    """PowerShell 결과를 JSON 으로 받는다. 비었거나 깨졌으면 None."""
    out = ps(cmd)
    try:
        return json.loads(out) if out else None
    except ValueError:
        return None


def power_pair(args: str) -> tuple[int | None, int | None]:
    """powercfg 의 (전원 연결 AC, 배터리 DC) 값. 덮개 설정은 숨김 항목이라 /qh 로 읽어야 한다(/query 로는 안 보임)."""
    out = run_text(["powercfg", "/qh", *args.split()])
    return power_index(out, "AC"), power_index(out, "DC")


def reg(path: str, name: str):
    """HKLM 아래 값 하나를 읽기 전용으로 연다(64비트 보기). 키나 값이 없으면 None."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            return winreg.QueryValueEx(k, name)[0]
    except (OSError, ImportError):
        return None


def reg_has(path: str) -> bool:
    try:
        import winreg
        winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY).Close()
        return True
    except (OSError, ImportError):
        return False


def main() -> int:
    # 1. 파이썬 · 패키지
    v = sys.version_info
    add(v >= (3, 11), "파이썬", platform.python_version(), "python.org 에서 3.11 이상 설치")
    for mod, pkg in [("kasa", "python-kasa"), ("keyring", "keyring")]:
        try:
            __import__(mod); add(True, f"패키지 {pkg}", "설치됨")
        except ImportError:
            add(False, f"패키지 {pkg}", "없음", "pip install -r requirements.txt")

    # 2. 시험망 주소 — 이 PC 에 192.168.1.100 이 붙어 있어야 셀이 신호를 보낸다
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind((cfg.pc_ip, 0)); s.close()
        add(True, "시험망 주소", f"{cfg.pc_ip} 이 이 PC 에 있음")
    except OSError:
        add(False, "시험망 주소", f"{cfg.pc_ip} 없음",
            "Wi-Fi 를 LiveHub(FTG-3D93-5G)에 붙이고 고정 IP 192.168.1.100 / 255.255.255.0, 게이트웨이 비움 (문서 2절)")

    # 3. Wi-Fi 이름 · 자동 연결
    wl = run_text(["netsh", "wlan", "show", "interfaces"])
    ssid = next((ln.split(":", 1)[1].strip() for ln in wl.splitlines() if ln.strip().startswith("SSID") and "BSSID" not in ln), "")
    add(ssid.startswith("FTG-"), "Wi-Fi 연결", ssid or "연결 안 됨", "LiveHub Wi-Fi(FTG-3D93-5G)에 연결")
    if ssid:
        prof = run_text(["netsh", "wlan", "show", "profile", f"name={ssid}"])
        auto = any(k in ln for ln in prof.splitlines() for k in ("Connect automatically", "자동 연결", "자동으로 연결"))
        add(auto, "Wi-Fi 자동 재연결", "자동" if auto else "수동",
            f'netsh wlan set profileparameter name="{ssid}" connectionmode=auto')

    # 4. 인터넷 출구가 시험망 쪽이 아닌가 (Wi-Fi 에 게이트웨이가 있으면 셀 망으로 인터넷을 찾으며 끊긴다)
    gw = ps("Get-NetRoute -AddressFamily IPv4 -DestinationPrefix 0.0.0.0/0 | ForEach-Object { $_.InterfaceAlias + '=' + $_.NextHop }")
    bad_gw = [g for g in gw.splitlines() if "192.168.1." in g]
    add(not bad_gw, "인터넷 경로", gw.replace("\n", ", ") or "없음",
        "Wi-Fi(시험망) 설정에서 게이트웨이를 비우고, 인터넷은 이더넷으로")

    # 5. 셀 신호 — 시험 중이면 now.json, 아니면 5초 직접 수신 (방화벽이 막으면 0대)
    nowp = ROOT / cfg.data_dir / "now.json"
    running = nowp.exists() and time.time() - json.loads(nowp.read_text(encoding="utf-8")).get("t", 0) < 90
    if running:
        now = json.loads(nowp.read_text(encoding="utf-8"))
        alive = [c for c in now.get("cells", []) if c["age"] <= 15]
        add(len(alive) == len(cfg.serials), "셀 신호 (시험 중)", f"{len(alive)}/{len(cfg.serials)}대 · 단계 {now.get('phase')}",
            "안 들리는 셀이 켜져 있는지 확인")
    else:
        from cellbench.cells import LiveListener, PortBusy
        try:
            lv = LiveListener(cfg); lv.start(); time.sleep(5); lv.stop()
            n = len([s for s in lv.alive() if s in cfg.serials])
            add(n == len(cfg.serials), "셀 신호 (5초 수신)", f"{n}/{len(cfg.serials)}대",
                "0대면 방화벽이 python 수신을 막는 경우가 많다 (문서 3절). 일부면 그 셀 전원 확인")
        except PortBusy as e:
            add(None, "셀 신호", "다른 프로그램이 포트 사용 중", str(e))

    # 6. 방화벽 규칙 (정보) — 위 5번이 실제 판정, 이건 원인 찾기용
    fw = ps("Get-NetFirewallApplicationFilter | Where-Object { $_.Program -like '*python*' } | Measure-Object | Select-Object -ExpandProperty Count")
    add(None if not fw else (int(fw) > 0), "방화벽 python 허용 규칙", f"{fw or '?'}개", "문서 3절의 New-NetFirewallRule")

    # 7. 플러그 계정 · 연결
    try:
        import keyring
        has = bool(keyring.get_password(cfg.keyring_service, "username")) and bool(keyring.get_password(cfg.keyring_service, "password"))
        add(has, "플러그 계정", "자격 증명 관리자에 있음" if has else "없음", "python tools/plug_cli.py setup")
        if has:
            from cellbench.plug import Plug
            try:
                r = Plug(cfg).read(); add(True, "플러그 연결", f"{'켜짐' if r.on else '꺼짐'} · {r.watts:.1f} W")
            except Exception as e:
                add(False, "플러그 연결", str(e)[:80], "Tapo 앱 → Tapo Lab → Third-Party Compatibility 켜짐 확인, 플러그가 LiveHub 2.4 GHz 에 있는지")
    except ImportError:
        pass

    power_checks()

    # 9. 디스크 — 사이클당 약 500 MB(셀 파일) 쌓인다
    data = ROOT / cfg.data_dir; data.mkdir(exist_ok=True)
    free = shutil.disk_usage(data).free / 1e9
    add(free >= 20, "디스크 여유", f"{free:.0f} GB (사이클당 약 0.5 GB)", "data_dir 을 여유 있는 드라이브로 (config)")

    os_checks()

    # 출력
    w = max(len(r[1]) for r in results)
    for st, item, got, fix in results:
        print(f" {st}  {item:<{w}}  {got}")
        if fix:
            print(f"    {'':<{w}}  → {fix}")
    bad = sum(1 for r in results if r[0] == "✗")
    print(f"\n{'준비됨' if not bad else f'고칠 것 {bad}건'}  (✓ 통과 · ✗ 고쳐야 함 · ! 참고)")
    return 1 if bad else 0


def power_checks() -> None:
    """8. 무인 운전 — 절전 · 덮개 · 전원. 전원 연결(AC)과 배터리(DC) 둘 다 본다 — 충전기가 빠지면 배터리 설정이 적용된다."""
    sleep_ac, sleep_dc = power_pair("SCHEME_CURRENT SUB_SLEEP STANDBYIDLE")
    add(sleep_ac == 0, "절전 (전원 연결 시)", "안 함" if sleep_ac == 0 else f"{sleep_ac}초 뒤 절전" if sleep_ac else "읽지 못함",
        "powercfg /change standby-timeout-ac 0")
    add(sleep_dc == 0, "절전 (배터리)", timeout_text(sleep_dc, "절전"), f"{INSTALL} power")
    hib_ac, hib_dc = power_pair("SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE")
    add(hib_ac == 0 and hib_dc == 0, "최대 절전 (전원 연결 · 배터리)",
        f"{timeout_text(hib_ac, '최대 절전')} · {timeout_text(hib_dc, '최대 절전')}", f"{INSTALL} power")
    lid, lid_dc = power_pair("SCHEME_CURRENT SUB_BUTTONS LIDACTION")
    add(lid == 0, "덮개 닫을 때 (전원 연결 시)", LID.get(lid, "읽지 못함"),
        "시작 → '덮개' 검색 → 덮개를 닫을 때 수행할 작업 변경 → 전원 사용: 아무 것도 안 함 → 저장")
    add(lid_dc == 0, "덮개 닫을 때 (배터리)", LID.get(lid_dc, "읽지 못함"), f"{INSTALL} power")
    add(*_named("USB 선택적 절전", usb_suspend_check(*power_pair(
        "SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226"))))
    bat = ps("(Get-CimInstance Win32_Battery | Select-Object -First 1).BatteryStatus")
    add(bat in ("", "2", "6", "7", "8", "9"), "충전기 연결", "연결됨" if bat != "1" else "배터리로 동작 중", "노트북 충전기 연결")
    nics = ps_json(
        "$cls='HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e972-e325-11ce-bfc1-08002be10318}';"
        "$keys=@(Get-ChildItem $cls -ErrorAction SilentlyContinue | ForEach-Object { Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue });"
        "ConvertTo-Json -Compress -InputObject @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.PnPDeviceID -notlike 'BTH*' } |"
        " ForEach-Object { $g=$_.InterfaceGuid; $k=$keys | Where-Object { $_.NetCfgInstanceId -eq $g } | Select-Object -First 1;"
        " [pscustomobject]@{ Name=$_.Name; Cap=$k.PnPCapabilities } })")
    add(*_named("네트워크 어댑터 절전", nic_power_check(nics or [])))


def os_checks() -> None:
    """10~15. 운영체제 고정 — tools/install.ps1 이 설정한 것이 아직 살아 있는지. 레지스트리·명령 출력을 읽기만 한다."""
    # 10. Windows 업데이트 — 자동 재시작이 1순위 고장(10-08 21:15 재시작 → 프로그램 소멸 → 플러그 OFF 고정 → 셀 방전)
    au = r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
    policy = reboot_policy_check(reg(au, "NoAutoRebootWithLoggedOnUsers"), reg(au, "AUOptions"))
    add(*_named("업데이트 자동 재시작 정책", policy))
    add(*_named("업데이트 일시 중지", pause_check(
        reg(r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings", "PauseUpdatesExpiryTime"), datetime.now(timezone.utc),
        policy_on=policy[0] is not False,
        engine_paused=reg(r"SOFTWARE\Microsoft\WindowsUpdate\UpdatePolicy\Settings", "PausedQualityStatus"))))
    add(*_named("업데이트 재시작 대기", reboot_pending_check(
        reg_has(r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"))))

    # 11. 블루투스 — PC 블루투스 광고가 Wi-Fi 를 끊었다(10-07, 인텔 Wi-Fi·BT 겸용 칩)
    bt = ps_json("ConvertTo-Json -Compress -InputObject @(Get-PnpDevice -Class Bluetooth -PresentOnly -ErrorAction SilentlyContinue |"
                 " Select-Object @{n='Status';e={[string]$_.Status}}, FriendlyName, InstanceId)")
    add(*_named("블루투스", bluetooth_check(bt) if bt is not None else (None, "읽지 못함", "")))

    # 12. 자동 재시작 로그온(ARSO) — 감시자 작업은 로그온 때 뜨므로, 재시작 뒤 세션이 열려야 한다
    sysp = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
    who = ps_json("$sid=([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value;"
                  "$o=(Get-ItemProperty ('HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon\\UserARSO\\'+$sid) -ErrorAction SilentlyContinue).OptOut;"
                  "$b=(New-Object -ComObject Shell.Application).NameSpace($env:SystemDrive).Self.ExtendedProperty('System.Volume.BitLockerProtection');"
                  "[pscustomobject]@{ BitLocker=$b; OptOut=$o } | ConvertTo-Json -Compress") or {}
    add(*_named("자동 재시작 로그온 (ARSO)", arso_check(
        reg(sysp, "DisableAutomaticRestartSignOn"), reg(sysp, "AutomaticRestartSignOnConfig"),
        who.get("BitLocker"), who.get("OptOut") == 1)))

    # 13. 감시자 작업 — 프로그램이 죽으면 플러그를 충전 쪽으로 돌려놓고 되살린다 (P1)
    task = ps_json(f"$t=Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue;"
                   "if ($t) { $i=$t | Get-ScheduledTaskInfo; $r=if ($i.LastRunTime.Year -gt 2000) { $i.LastRunTime.ToString('MM-dd HH:mm') } else { '' };"
                   " [pscustomobject]@{ State=[string]$t.State; LastTaskResult=$i.LastTaskResult; LastRunTime=$r } | ConvertTo-Json -Compress }")
    add(*_named(f"감시자 작업 ({TASK_NAME})", task_check(task)))

    # 14. Slack 웹훅 — 있는지와 출처만 본다(자격 증명 관리자, 없으면 클라우드 Vault — 감시자·엔진과 같은 순서). 값은 보여 주지 않는다
    try:
        from cellbench.alert import find_webhook
        source = find_webhook()[1]
        present = bool(source)
    except Exception:
        present, source = None, None
    add(*_named("Slack 알림 웹훅", webhook_check(present, source)))

    # 15. 보안 프로그램 (정보) — 파이썬 실행·수신을 막을 수 있다
    names = ps("Get-Process | Select-Object -ExpandProperty Name").splitlines()
    add(*_named("보안 프로그램", security_check(names)))


def _named(item: str, v: Verdict) -> tuple[bool | None, str, str, str]:
    """판정 (상태, 결과, 고치는 법) 에 항목 이름을 끼워 add() 인자 순서로."""
    return v[0], item, v[1], v[2]


if __name__ == "__main__":
    sys.exit(main())
