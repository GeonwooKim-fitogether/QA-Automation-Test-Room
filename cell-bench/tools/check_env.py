"""제어 PC 점검 — 이 PC 가 시험대를 돌릴 준비가 됐는지 하나씩 확인한다. 아무 설정도 바꾸지 않는다.

  python tools/check_env.py

새 노트북으로 옮겼을 때 이것 하나를 돌려 모두 ✓ 가 나오면 된다. ✗ 에는 고치는 방법이 함께 나온다
(방법의 자세한 설명은 docs/control-pc-setup.md).
시험이 돌고 있으면 셀 신호 포트를 열지 않고 data/now.json 으로 대신 확인한다.
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cellbench.config import Config  # noqa: E402

cfg = Config()
results: list[tuple[str, str, str, str]] = []      # (상태, 항목, 결과, 고치는 법)


def add(ok: bool | None, item: str, got: str, fix: str = "") -> None:
    results.append(("✓" if ok else "!" if ok is None else "✗", item, got, "" if ok else fix))


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

# 8. 무인 운전 — 절전 · 덮개 · 전원
def ac_index(args: str) -> int | None:
    """powercfg 의 '전원 연결 시(AC)' 값. 덮개 설정은 숨김 항목이라 /qh 로 읽어야 한다(/query 로는 안 보임)."""
    out = run_text(["powercfg", "/qh", *args.split()])
    m = re.search(r"(?:Current AC Power Setting Index|AC 전원 설정 색인)\s*:\s*(0x[0-9a-fA-F]+)", out)
    return int(m.group(1), 16) if m else None


sleep_ac = ac_index("SCHEME_CURRENT SUB_SLEEP STANDBYIDLE")
add(sleep_ac == 0, "절전 (전원 연결 시)", "안 함" if sleep_ac == 0 else f"{sleep_ac}초 뒤 절전" if sleep_ac else "읽지 못함",
    "powercfg /change standby-timeout-ac 0")
lid = ac_index("SCHEME_CURRENT SUB_BUTTONS LIDACTION")
add(lid == 0, "덮개 닫을 때 (전원 연결 시)", {0: "아무 것도 안 함", 1: "절전", 2: "최대 절전", 3: "시스템 종료"}.get(lid, "읽지 못함"),
    "시작 → '덮개' 검색 → 덮개를 닫을 때 수행할 작업 변경 → 전원 사용: 아무 것도 안 함 → 저장")
bat = ps("(Get-CimInstance Win32_Battery | Select-Object -First 1).BatteryStatus")
add(bat in ("", "2", "6", "7", "8", "9"), "충전기 연결", "연결됨" if bat != "1" else "배터리로 동작 중", "노트북 충전기 연결")

# 9. 디스크 — 사이클당 약 500 MB(셀 파일) 쌓인다
data = ROOT / cfg.data_dir; data.mkdir(exist_ok=True)
free = shutil.disk_usage(data).free / 1e9
add(free >= 20, "디스크 여유", f"{free:.0f} GB (사이클당 약 0.5 GB)", "data_dir 을 여유 있는 드라이브로 (config)")

# 출력
w = max(len(r[1]) for r in results)
for st, item, got, fix in results:
    print(f" {st}  {item:<{w}}  {got}")
    if fix:
        print(f"    {'':<{w}}  → {fix}")
bad = sum(1 for r in results if r[0] == "✗")
print(f"\n{'준비됨' if not bad else f'고칠 것 {bad}건'}  (✓ 통과 · ✗ 고쳐야 함 · ! 참고)")
sys.exit(1 if bad else 0)
