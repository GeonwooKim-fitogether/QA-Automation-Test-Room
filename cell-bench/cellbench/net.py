"""시험망(LiveHub) 연결 확인과 재연결.

2026-10-07 22:43 에 노트북 Wi-Fi 가 LiveHub 에서 끊긴 뒤 '자동 연결' 프로필인데도 다시 붙지 않았다.
그러면 셀도 플러그도 안 보여 프로그램이 아무것도 할 수 없다. 그래서 프로그램이 직접
이미 저장된 프로필로 다시 연결한다(`netsh wlan connect`). 주소·SSID·경로 같은 설정은 바꾸지 않는다.
"""
from __future__ import annotations

import ipaddress
import subprocess
import time

# 창 없는 감시자(pythonw)가 부를 때 ping·netsh 마다 콘솔 창이 번쩍 뜨지 않게 한다 (콘솔에서 부를 때는 차이 없음)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def hub_reachable(hub_ip: str = "192.168.1.1", timeout_ms: int = 1000) -> bool:
    try:
        r = subprocess.run(["ping", "-n", "1", "-w", str(timeout_ms), hub_ip], capture_output=True, timeout=5,
                           creationflags=_NO_WINDOW)
        return r.returncode == 0 and b"TTL=" in r.stdout.upper()
    except Exception:
        return False


def reconnect(profile: str, hub_ip: str = "192.168.1.1", wait_s: float = 10.0) -> bool:
    """저장된 Wi-Fi 프로필로 다시 연결하고 LiveHub 가 응답하면 True."""
    try:
        subprocess.run(["netsh", "wlan", "connect", f"name={profile}"], capture_output=True, timeout=15,
                       creationflags=_NO_WINDOW)
    except Exception:
        return False
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if hub_reachable(hub_ip):
            return True
        time.sleep(1)
    return False


def ip_state(pc_ip: str, timeout_s: float = 20.0) -> str | None:
    """이 PC 에 걸린 pc_ip 주소의 상태를 읽는다 (읽기 전용 — 아무 설정도 바꾸지 않는다).

    Windows 의 Get-NetIPAddress 가 알려 주는 AddressState 이름('Preferred' · 'Duplicate' · 'Tentative' …)을 돌려준다.
    같은 주소가 어댑터 여럿에 걸려 있으면 하나라도 Duplicate 면 'Duplicate'. 주소가 이 PC 에 없으면 '', 읽지 못하면 None.
    """
    try:
        ip = str(ipaddress.IPv4Address(pc_ip))          # 명령 줄에 넣기 전에 주소 모양인지 확인한다
    except ValueError:
        return None
    # 주소가 없으면 Get-NetIPAddress 가 '못 찾음' 오류를 내 종료 코드가 1 이 된다 — 그것은 '읽지 못함'이 아니라 '없음'이라 exit 0
    cmd = (f"Get-NetIPAddress -IPAddress {ip} -ErrorAction SilentlyContinue | "
           f"ForEach-Object {{ [string]$_.AddressState }}; exit 0")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                           text=True, timeout=timeout_s, creationflags=_NO_WINDOW)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    states = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    if "Duplicate" in states:
        return "Duplicate"
    return states[0] if states else ""


def ip_start_problem(state: str | None, pc_ip: str) -> str | None:
    """시작을 거부할 이유 (없으면 None). 순수 함수 — ip_state 의 결과를 받는다.

    거부하는 것은 Duplicate 하나다. 다른 PC 가 같은 주소로 LiveHub 에 붙어 있으면 셀 라이브가 그쪽으로 가서 이 엔진은
    아무것도 못 듣고, 두 PC 가 셀 명령 포트와 플러그를 두고 다툰다(FMEA 2.4 · 3.2 · 8.4). 주소가 아예 없거나('') 읽지
    못했으면(None) 막지 않는다 — 셀이 하나도 안 들리면 no_cells 로 끝나는 기존 길이 받는다.
    """
    if state == "Duplicate":
        return f"다른 PC 가 {pc_ip} 을 쓰고 있다 — 이 PC 의 시험망 주소가 중복(Duplicate) 상태라 셀 라이브를 받을 수 없다"
    return None
