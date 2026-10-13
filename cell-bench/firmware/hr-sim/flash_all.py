"""hr-sim 펌웨어를 ESP32-C3 보드 여러 개에 굽고, 보드마다 광고 이름을 기록한다.

    python flash_all.py            # 지금 꽂혀 있는 보드 전부를 한꺼번에 굽는다
    python flash_all.py --watch    # 꽂는 대로 하나씩 굽는다 (Ctrl+C 로 끝)

보드는 USB-Serial/JTAG 장치(USB VID 303A, PID 1001)로 찾는다. 굽고 나면 보드가 다시 켜져
"HRSIM name=..." 줄을 내는지 확인하고, 결과를 hr-sim-boards.csv 에 한 줄씩 덧붙인다
(시각 · 포트 · MAC · 이름 · 결과). 셀에 0x20 으로 쓸 이름은 이 파일의 name 칸이다.

필요한 것: esptool 5 (pip install esptool — pyserial 이 함께 깔린다).
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE / "bin"
LOG = HERE / "hr-sim-boards.csv"
USB_VID, USB_PID = 0x303A, 0x1001
NAME_PREFIX = "HRSIM-"

# 굽는 명령의 정본. README 의 한 줄과 같은 인자다.
FLASH_ARGS = [
    "--chip", "esp32c3", "-b", "460800", "--before", "default-reset", "--after", "hard-reset",
    "write-flash", "--flash-mode", "dio", "--flash-freq", "80m", "--flash-size", "4MB",
    "0x0", str(BIN / "bootloader.bin"),
    "0x8000", str(BIN / "partition-table.bin"),
    "0x10000", str(BIN / "hr-sim.bin"),
]

_print_lock = threading.Lock()
_log_lock = threading.Lock()


def name_for_mac(mac: str) -> str:
    """기본 MAC 끝 4자리로 광고 이름을 만든다 — 펌웨어 main.c 의 snprintf 와 같은 규칙."""
    hexdigits = re.sub(r"[^0-9A-Fa-f]", "", mac)
    if len(hexdigits) != 12:
        raise ValueError(f"MAC 형식이 아님: {mac!r}")
    return NAME_PREFIX + hexdigits[-4:].upper()


def mac_from_esptool(output: str) -> str | None:
    m = re.search(r"MAC:\s*((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})", output)
    return m.group(1).lower() if m else None


def say(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def boards() -> dict[str, str]:
    """{포트: USB 시리얼 번호}. ESP32-C3 의 USB 시리얼 번호는 그 보드의 MAC 이다."""
    from serial.tools import list_ports
    return {p.device: (p.serial_number or "") for p in list_ports.comports()
            if p.vid == USB_VID and p.pid == USB_PID}


PANIC_MARKS = ("Guru Meditation", "abort() was called", "Backtrace:")


def boot_ok(lines: list[str], name: str) -> bool:
    """상태 줄('HRSIM name=<이름> bpm=')이 두 번 나오고 그 사이 멈춤 흔적이 없어야 정상이다.
    이름 줄 한 번만 보면 부팅하자마자 멈추고 다시 켜지기를 되풀이하는 보드도 통과한다(2026-10-13 실측)."""
    beats = 0
    for line in lines:
        if any(m in line for m in PANIC_MARKS):
            return False
        if f"HRSIM name={name} bpm=" in line:
            beats += 1
    return beats >= 2


def wait_for_boot(port: str, name: str, timeout: float = 20.0) -> bool:
    """다시 켜진 보드가 상태 줄을 5초 간격으로 두 번 내는지 본다. 재열거 중이면 포트가 잠깐 없다."""
    import serial
    deadline = time.monotonic() + timeout
    lines: list[str] = []
    while time.monotonic() < deadline:
        try:
            with serial.Serial(port, 115200, timeout=1) as s:
                while time.monotonic() < deadline:
                    lines.append(s.readline().decode("utf-8", "replace"))
                    if any(m in lines[-1] for m in PANIC_MARKS):
                        return False
                    if boot_ok(lines, name):
                        return True
        except (OSError, serial.SerialException):
            time.sleep(0.5)
    return False


def flash_one(port: str, usb_serial: str) -> tuple[str, str, str]:
    """(mac, name, 결과) — 결과는 ok · no-boot · flash-failed."""
    say(f"[{port}] 굽는 중 …")
    r = subprocess.run([sys.executable, "-m", "esptool", "-p", port, *FLASH_ARGS],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = r.stdout + r.stderr
    mac = mac_from_esptool(out) or usb_serial.lower()
    try:
        name = name_for_mac(mac)
    except ValueError:
        name = "?"
    if r.returncode != 0:
        tail = " / ".join(out.strip().splitlines()[-3:])
        result = "flash-failed"
        say(f"[{port}] 실패 — {tail}")
    elif wait_for_boot(port, name):
        result = "ok"
        say(f"[{port}] OK  {name}  (MAC {mac}) — 뽑아도 됩니다")
    else:
        result = "no-boot"
        say(f"[{port}] 구웠지만 20초 안에 '{name}' 이 정상 동작하는 것을 못 봄(멈춤 또는 무응답) — 시리얼 모니터로 확인")
    with _log_lock:
        new = not LOG.exists()
        with LOG.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "port", "mac", "name", "result"])
            w.writerow([datetime.now().isoformat(timespec="seconds"), port, mac, name, result])
    return mac, name, result


def check_bins() -> None:
    missing = [p.name for p in (BIN / "bootloader.bin", BIN / "partition-table.bin", BIN / "hr-sim.bin")
               if not p.exists()]
    if missing:
        sys.exit(f"bin/ 에 없음: {', '.join(missing)}")


def run_all(workers: int) -> int:
    found = boards()
    if not found:
        say("ESP32-C3 보드(USB 303A:1001)가 안 보입니다. 케이블이 데이터용인지, 보드가 켜졌는지 확인하세요.")
        return 1
    say(f"보드 {len(found)}개: {', '.join(sorted(found))}")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda kv: flash_one(*kv), sorted(found.items())))
    ok = [n for _, n, r in results if r == "ok"]
    say(f"\n끝: {len(ok)}/{len(results)} 성공 — 기록 {LOG.name}")
    for _, n, r in results:
        say(f"  {n:12s} {r}")
    return 0 if len(ok) == len(results) else 2


def run_watch() -> int:
    say("꽂는 대로 굽습니다. 'OK … 뽑아도 됩니다' 가 뜨면 다음 보드를 꽂으세요. 끝내려면 Ctrl+C.")
    done: set[str] = set()      # 이미 구운 보드의 USB 시리얼 번호(= MAC)
    busy: set[str] = set()
    failed: set[str] = set()    # 실패한 보드 — 뽑았다 다시 꽂아야 재시도한다(꽂힌 채로 무한 반복 방지)
    ex = ThreadPoolExecutor(max_workers=8)

    def job(port: str, sn: str) -> None:
        try:
            (done if flash_one(port, sn)[2] == "ok" else failed).add(sn)
        finally:
            busy.discard(sn)

    try:
        while True:
            present = {sn or port: port for port, sn in boards().items()}
            failed.intersection_update(present)
            for key, port in present.items():
                if key not in done and key not in busy and key not in failed:
                    busy.add(key)
                    ex.submit(job, port, key)
            time.sleep(1)
    except KeyboardInterrupt:
        say(f"\n멈춤 — 이번에 성공 {len(done)}개, 기록 {LOG.name}")
    finally:
        ex.shutdown(wait=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch", action="store_true", help="꽂는 대로 하나씩 굽는다")
    ap.add_argument("--workers", type=int, default=8, help="한꺼번에 굽는 보드 수 (기본 8)")
    a = ap.parse_args()
    check_bins()
    return run_watch() if a.watch else run_all(a.workers)


if __name__ == "__main__":
    sys.exit(main())
