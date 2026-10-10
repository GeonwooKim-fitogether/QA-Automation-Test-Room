"""운영체제 정보 — 신호등의 '전원 · OS' 차선과 '무선' 차선의 재료. 읽기만 하고 아무 설정도 바꾸지 않는다.

감시자가 Config.health["osinfo_every_s"](10분)마다 collect() 를 불러 data/osinfo.json 에 쓴다(supervisor.tick → run_once).
감시자는 창 없이(pythonw) 돌므로 명령(netsh)은 CREATE_NO_WINDOW 로 띄운다 — 10분마다 콘솔 창이 번쩍이지 않게.
전원은 명령 없이 Windows API(GetSystemPowerStatus)로, 업데이트는 레지스트리로 읽는다.

판정은 tools/check_env.py 의 순수 함수(pause_check · reboot_policy_check · reboot_pending_check)와 레지스트리 읽기(reg · reg_has)를
그대로 쓴다 — 제어 PC 점검과 신호등이 같은 기준으로 본다. 어느 하나를 읽지 못해도 예외를 올리지 않고 그 칸만 {"error": ...} 로 둔다.
"""
from __future__ import annotations

import ctypes
import functools
import importlib.util
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CHECK_ENV = Path(__file__).resolve().parents[1] / "tools" / "check_env.py"

AU = r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
UX = r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
UPOL = r"SOFTWARE\Microsoft\WindowsUpdate\UpdatePolicy\Settings"
REBOOT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"
STATE = {True: "ok", None: "warn", False: "bad"}      # check_env 의 판정(✓ · ! · ✗) → 신호등이 읽는 글자


@functools.lru_cache(maxsize=1)
def check_env():
    """tools/check_env.py 를 모듈로 불러온다 (tools 는 패키지가 아니다). 불러오기만으로는 아무 점검도 돌지 않는다."""
    spec = importlib.util.spec_from_file_location("cellbench_check_env", CHECK_ENV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def collect(now_t: float | None = None, warn_days: int = 7) -> dict:
    """{t, power, update, wifi} — 칸마다 따로 읽고, 읽지 못한 칸은 {"error": ...}."""
    return {"t": time.time() if now_t is None else now_t, "power": _safe(power),
            "update": _safe(lambda: update(warn_days=warn_days)), "wifi": _safe(wifi)}


def _safe(fn) -> dict:
    try:
        return fn()
    except Exception as e:                      # 신호등 재료 하나 때문에 감시자가 멈추면 안 된다
        return {"error": f"{type(e).__name__}: {e}"}


# ---------- 전원 ----------

class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


def power() -> dict:
    s = _PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return {"ac": None, "battery_pct": None, "has_battery": None}
    return parse_power(s.ACLineStatus, s.BatteryFlag, s.BatteryLifePercent)


def parse_power(ac_line: int, flag: int, pct: int) -> dict:
    """SYSTEM_POWER_STATUS → {ac, battery_pct, has_battery}.
    ACLineStatus 0 = 배터리 · 1 = 전원 연결 · 255 = 모름. BatteryFlag 128 = 배터리 없음 · 255 = 모름. 퍼센트 255 = 모름."""
    return {"ac": {0: False, 1: True}.get(ac_line), "battery_pct": None if pct == 255 else int(pct),
            "has_battery": None if flag == 255 else not (flag & 128)}


# ---------- 업데이트 ----------

def update(now: datetime | None = None, reg=None, reg_has=None, warn_days: int = 7) -> dict:
    """업데이트 재시작 대기 · 일시 중지 만료 — check_env 와 같은 레지스트리 값을 같은 함수로 판정한다."""
    ce = check_env()
    reg, reg_has = reg or ce.reg, reg_has or ce.reg_has
    now = now or datetime.now(timezone.utc)
    policy = ce.reboot_policy_check(reg(AU, "NoAutoRebootWithLoggedOnUsers"), reg(AU, "AUOptions"))
    expiry = reg(UX, "PauseUpdatesExpiryTime")
    ok, text, _ = ce.pause_check(expiry, now, policy_on=policy[0] is not False,
                                 engine_paused=reg(UPOL, "PausedQualityStatus"), warn_days=warn_days)
    pending = bool(reg_has(REBOOT))
    return {"reboot_pending": pending, "reboot_text": ce.reboot_pending_check(pending)[1],
            "pause": {"state": STATE[ok], "text": text, "expiry": expiry, "days_left": _days_left(expiry, now)},
            "policy": STATE[policy[0]]}


def _days_left(expiry, now: datetime) -> float | None:
    try:
        end = datetime.fromisoformat(str(expiry).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return round((end - now).total_seconds() / 86400, 2)


# ---------- Wi-Fi ----------

def wifi() -> dict:
    r = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, timeout=15, creationflags=_NO_WINDOW)
    return parse_netsh(_decode(r.stdout))


def _decode(raw: bytes) -> str:
    """콘솔 코드페이지가 PC 마다 달라(UTF-8 / CP949) 둘 다 시도한다 (check_env.run_text 와 같은 방식)."""
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def parse_netsh(text: str) -> dict:
    """netsh wlan show interfaces 출력 → {connected, state, ssid, signal_pct, rssi_dbm}. 한글·영문 출력 둘 다.
    인터페이스가 여럿이면 첫 것만 본다. Rssi 줄은 새 Windows 에만 있다(없으면 None — 신호등이 % 로 근사한다)."""
    f: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            f.setdefault(k.strip().lower(), v.strip())
    state = f.get("상태") or f.get("state")
    sig = re.match(r"(\d+)", f.get("신호") or f.get("signal") or "")
    rssi = re.match(r"(-?\d+)", f.get("rssi") or "")
    return {"connected": None if state is None else state.lower() in ("연결됨", "connected"), "state": state,
            "ssid": f.get("ssid"), "signal_pct": int(sig.group(1)) if sig else None,
            "rssi_dbm": int(rssi.group(1)) if rssi else None}
