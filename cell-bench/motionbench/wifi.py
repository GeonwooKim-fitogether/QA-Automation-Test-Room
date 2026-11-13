"""라이브허브 와이파이 점검 — 제어 PC 가 지금 세트의 라이브허브 SSID 에 붙어 있는지 보고, 끊겼으면 다시 붙는다.
2026-11-05: PC 와이파이가 두 번 끊겼는데(재부팅 뒤 · 19:53) 실행기는 '셀이 하나도 안 들린다'를 '충전 중'으로 읽었다.
그래서 셀이 안 들릴 때 먼저 링크를 본다. 끊긴 와이파이를 같은 프로필로 다시 붙는 것만 한다 — 다른 네트워크 설정은 바꾸지 않는다.
지금 세트의 SSID 는 data/livehub.json {"ssid": "..."} 에 둔다(세트를 바꿀 때 사람이 고친다)."""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

CFG = Path(__file__).resolve().parents[1] / "data" / "livehub.json"


def _netsh(*args: str) -> str:
    r = subprocess.run(["netsh", "wlan", *args], capture_output=True)
    for enc in ("cp949", "utf-8"):
        try:
            return r.stdout.decode(enc)
        except UnicodeDecodeError:
            continue
    return r.stdout.decode("utf-8", "replace")


def parse_current(text: str) -> str | None:
    """netsh wlan show interfaces → 붙어 있는 SSID (없으면 None). BSSID 줄은 건너뛴다."""
    m = re.search(r"^[ \t]*SSID[ \t]*:[ \t]*(\S.*?)[ \t]*\r?$", text, re.M)
    return m.group(1) if m else None


def parse_visible(text: str) -> list[str]:
    """netsh wlan show networks → 보이는 SSID 목록."""
    return [s.strip() for s in re.findall(r"^SSID \d+[ \t]*:[ \t]*(.*?)[ \t]*\r?$", text, re.M) if s.strip()]


def expected() -> str | None:
    try:
        return json.loads(CFG.read_text(encoding="utf-8")).get("ssid")
    except (OSError, ValueError):
        return None


def status() -> dict:
    want = expected()
    cur = parse_current(_netsh("show", "interfaces"))
    return {"want": want, "current": cur, "ok": bool(want) and cur == want}


def ensure(say=print, wait_s: float = 20.0) -> dict:
    """붙어 있으면 그대로. 끊겼고 SSID 가 보이면 다시 붙는다. 안 보이면 라이브허브 쪽 문제로 알린다. 결과 dict 의 why 를 사람에게 보인다."""
    st = status()
    if st["ok"] or not st["want"]:
        return st | {"why": None if st["ok"] else "data/livehub.json 에 라이브허브 SSID 가 없다"}
    vis = parse_visible(_netsh("show", "networks"))
    if st["want"] not in vis:
        return st | {"why": f"라이브허브 와이파이 {st['want']} 가 보이지 않는다 — 라이브허브 전원 · 상태를 본다"}
    _netsh("connect", f"name={st['want']}")
    t0 = time.time()
    while time.time() - t0 < wait_s:
        time.sleep(2)
        if status()["ok"]:
            say(f"와이파이가 끊겨 {st['want']} 에 다시 붙었다 ({time.time() - t0:.0f}초)")
            return status() | {"why": None, "reconnected": True}
    return status() | {"why": f"{st['want']} 에 다시 붙지 못했다"}
