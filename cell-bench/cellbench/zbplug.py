"""지그비 스마트 플러그(아카라 SP-EUC01) — ESP32-C6 코디네이터(firmware/zb-plug)를 USB 시리얼로 부른다.

Tapo 플러그(cellbench.plug.Plug)와 같은 모양(read · on · off · recharge · _call · metrics)이라 부르는 쪽은 바꿀 것이 없다.
왜 바꿨나: 레일 독 Tapo 플러그가 2026-11-05 펌웨어부터 PC 로 켜고 끌 수 없게 됐다(TPAP 인증). 지그비는 허브 · 계정 ·
와이파이 없이 이 PC 에 꽂힌 보드가 직접 명령을 보낸다.

주고받는 법 — 명령 한 줄을 보내고 같은 표지(#...)가 붙은 답 한 줄을 기다린다. 보드는 PC 가 포트를 열지 않은 동안에도
EVT 줄을 쌓아 두므로, 표지가 없으면 옛 줄을 답으로 잘못 읽는다. 포트는 부를 때만 열고 바로 닫는다 — 충전 유지(keep)와
레일 실행기가 번갈아 부를 수 있게. 동시에 열지 않도록 잠금 파일(data/zbplug.lock)로 순서를 세운다.
포트를 열 때 DTR · RTS 를 내린 채 연다. ESP32-C6 의 USB 시리얼은 RTS 를 올리면 보드가 다시 켜진다.
"""
from __future__ import annotations

import itertools
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "data" / "zbplug.lock"

DONGLE_VID, DONGLE_PID = 0x303A, 0x1001       # ESP32-C6 USB-Serial/JTAG
DONGLE_SERIAL = "14:C1:9F:E6:6E:E8"           # 제어 PC2 에 꽂힌 보드(USB 일련번호 = 보드 MAC, 2026-11-09)

_seq = itertools.count(1)


@dataclass(frozen=True)
class ZbReading:
    on: bool | None          # None = 이번에 못 읽었고 아는 값도 없다
    watts: float             # 못 읽으면 nan
    t: float
    src: str = ""            # 전력 출처: em(전기 측정) · ai(아날로그 입력) · none
    ack: int = 0             # 켜기 · 끄기 명령에 플러그가 성공으로 답했나 (1 예, 0 답 없음, -1 거절)


_KV = re.compile(r"(\w+)=(\S+)")


def parse(line: str) -> tuple[str, str, dict[str, str], str]:
    """'STATE #12 on=1 w=3.0 ...' → ('STATE', '#12', {'on': '1', 'w': '3.0', ...}, 나머지 글)."""
    parts = line.strip().split()
    kind = parts[0] if parts else ""
    tok = next((p for p in parts[1:] if p.startswith("#")), "")
    rest = " ".join(p for p in parts[1:] if p != tok)
    return kind, tok, dict(_KV.findall(rest)), rest


def reading(kv: dict[str, str], now: float | None = None) -> ZbReading:
    """STATE 답의 값 → ZbReading. 켜짐을 모르면 on=None."""
    on = {"1": True, "0": False}.get(kv.get("on", "?"))
    try:
        w = float(kv.get("w", "nan"))
    except ValueError:
        w = float("nan")
    return ZbReading(on, w, time.time() if now is None else now, kv.get("src", ""), int(kv.get("ack", "0") or 0))


def find_port(serial_no: str | None = DONGLE_SERIAL) -> str:
    """보드의 COM 포트. 일련번호가 맞는 것을 먼저, 없으면 같은 종류(303A:1001)가 하나뿐일 때 그것."""
    from serial.tools import list_ports
    ports = [p for p in list_ports.comports() if p.vid == DONGLE_VID and p.pid == DONGLE_PID]
    if serial_no:
        for p in ports:
            if (p.serial_number or "").upper() == serial_no.upper():
                return p.device
    if len(ports) == 1:
        return ports[0].device
    raise RuntimeError(f"지그비 보드(USB {DONGLE_VID:04X}:{DONGLE_PID:04X}, {serial_no})를 찾지 못함 — "
                       f"보이는 같은 종류 {len(ports)}개. 보드가 꽂혀 있는지 본다")


class ZbPlug:
    """레일 독 충전 플러그. 생성자는 아무 데도 접속하지 않는다(부를 때 연다)."""
    stats_path: Path | None = None
    toggles_total: int = 0
    last_call_s: float | None = None
    _fails: tuple[float, ...] = ()
    _last_on: bool | None = None

    def __init__(self, port: str | None = None, serial_no: str | None = DONGLE_SERIAL,
                 retries: int = 3, timeout_s: float = 12.0, lock_path: Path = LOCK, lock_wait_s: float = 30.0):
        self.port = port
        self.serial_no = serial_no
        self.retries = retries
        self.timeout_s = timeout_s
        self.lock_path = Path(lock_path)
        self.lock_wait_s = lock_wait_s

    # --- 내부: 포트 ---
    @contextmanager
    def _turn(self):
        """다른 프로세스가 포트를 쓰는 중이면 lock_wait_s 까지 기다린다."""
        from .proc import acquire_lock
        end = time.monotonic() + self.lock_wait_s
        while True:
            f = acquire_lock(self.lock_path)
            if f is not None:
                break
            if time.monotonic() > end:
                raise TimeoutError(f"다른 프로그램이 지그비 보드를 {self.lock_wait_s:.0f}초 넘게 쓰고 있다 ({self.lock_path.name})")
            time.sleep(0.2)
        try:
            yield
        finally:
            f.close()

    def _open(self):
        import serial
        s = serial.Serial()
        s.port = self.port or find_port(self.serial_no)
        s.baudrate = 115200
        s.timeout = 0.1
        s.write_timeout = 2
        s.dtr = False          # 열 때 보드를 다시 켜지 않게
        s.rts = False
        s.open()
        return s

    def ask(self, cmd: str, timeout_s: float | None = None, on_line=None) -> tuple[str, dict[str, str], str]:
        """명령 한 줄 → 같은 표지의 답 (종류, 값, 나머지 글). ERR 이면 RuntimeError, 시간을 넘기면 TimeoutError.
        on_line 을 주면 기다리는 동안 들어온 다른 줄(EVT · LOG)을 넘긴다."""
        tok = f"#{os.getpid() % 10000}-{next(_seq)}"
        limit = self.timeout_s if timeout_s is None else timeout_s
        with self._turn():
            s = self._open()
            try:
                s.write(f"\n{cmd} {tok}\n".encode("ascii"))
                end, buf = time.monotonic() + limit, b""
                while time.monotonic() < end:
                    buf += s.read(512)
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        line = raw.decode("utf-8", "replace").strip()
                        if not line:
                            continue
                        kind, t, kv, rest = parse(line)
                        if t == tok:
                            if kind == "ERR":
                                raise RuntimeError(f"보드가 '{cmd}' 를 거절: {rest}")
                            return kind, kv, rest
                        if on_line:
                            on_line(line)
                raise TimeoutError(f"보드가 '{cmd}' 에 {limit:.0f}초 안에 답하지 않음")
            finally:
                s.close()

    def listen(self, seconds: float, on_line=print) -> None:
        """포트를 seconds 동안 열어 들어오는 줄을 그대로 넘긴다(짝짓기를 지켜볼 때). on_line 이 True 를 돌려주면 바로 끝낸다."""
        with self._turn():
            s = self._open()
            try:
                end, buf = time.monotonic() + seconds, b""
                while time.monotonic() < end:
                    buf += s.read(512)
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        line = raw.decode("utf-8", "replace").strip()
                        if line and on_line(line) is True:
                            return
            finally:
                s.close()

    # --- Tapo Plug 와 같은 모양 ---
    def _call(self, action: str) -> ZbReading:
        cmd = {"read": "STATE", "on": "ON", "off": "OFF"}[action]
        last: Exception | None = None
        t0 = time.monotonic()
        try:
            for i in range(self.retries):
                try:
                    _, kv, _ = self.ask(cmd)
                    r = reading(kv)
                    if action in ("on", "off") and r.on is not (action == "on"):
                        raise RuntimeError(f"{'켜기' if action == 'on' else '끄기'} 뒤 읽은 상태가 "
                                           f"{'모름' if r.on is None else '켜짐' if r.on else '꺼짐'} (ack={r.ack})")
                    if action == "read" and r.on is None:
                        raise RuntimeError("플러그가 켜짐 여부에 답하지 않음")
                    self._count(action, r)
                    return r
                except Exception as e:
                    last = e
                    self._failed()
                    if i + 1 < self.retries:
                        time.sleep(2)
            raise RuntimeError(f"지그비 플러그 {action} 실패 ({self.retries}회): {last}")
        finally:
            self.last_call_s = round(time.monotonic() - t0, 1)

    def _failed(self) -> None:
        now = time.time()
        self._fails = tuple(t for t in self._fails if now - t < 3600) + (now,)

    def _count(self, action: str, r: ZbReading) -> None:
        if action in ("on", "off") and (self._last_on is None or self._last_on != r.on):
            self.toggles_total += 1
        self._last_on = r.on

    def metrics(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        return {"retries_1h": sum(1 for t in self._fails if now - t < 3600),
                "last_call_s": self.last_call_s, "toggles_total": self.toggles_total}

    def read(self) -> ZbReading:
        return self._call("read")

    def on(self) -> ZbReading:
        return self._call("on")

    def off(self) -> ZbReading:
        return self._call("off")

    def recharge(self, gap_s: float = 10.0) -> ZbReading:
        """OFF → gap_s 초 → ON (독이 완충 뒤 다시 충전을 시작하게)."""
        self._call("off")
        time.sleep(gap_s)
        return self._call("on")
