"""시험망(LiveHub) 연결 확인과 재연결.

2026-10-07 22:43 에 노트북 Wi-Fi 가 LiveHub 에서 끊긴 뒤 '자동 연결' 프로필인데도 다시 붙지 않았다.
그러면 셀도 플러그도 안 보여 프로그램이 아무것도 할 수 없다. 그래서 프로그램이 직접
이미 저장된 프로필로 다시 연결한다(`netsh wlan connect`). 주소·SSID·경로 같은 설정은 바꾸지 않는다.
"""
from __future__ import annotations

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
