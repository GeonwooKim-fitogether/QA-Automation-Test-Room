"""Dock 명령 — 세트의 Dock 에 직렬 명령(ASCII 한 줄)을 보내 셀 24대의 버튼을 누른다 (셀 전체 켜기).

근거: Dock 펌웨어 fitogether-org/dock-dkp2-1-firmware 의 Core/Src/pc_interface.c (master 4158199 — 읽기만, recovery-plan 4절).
  · PC 가 보낸 바이트를 하나씩 그대로 되돌리고(echo) 바이트마다 100 ms 쉰다. \r 은 버리고 \n 에서 한 줄을 끝낸다. 모르는 줄은 무시한다.
  · CELL_AON     → "OK!" 뒤 24칸 버튼 1.5초 (켜진 셀은 2.0초 미만 누름을 무시하고, 꺼진 셀은 약 3.0초 눌려야 켜진 채 남는다)
  · CELL_EN_INIT → "INITIAL OK!" 뒤 버튼 출력을 켜고 10 ms 누름 20번 — 켜진 셀은 이 패턴으로 꺼진다
  · 명령 판정이 strncmp(받은 줄, 명령, 받은 길이) 라 명령의 앞부분도 그 명령이 된다 — 빈 줄은 CELL_EN_INIT, "CELL_O" 는 CELL_OFF.
시험대의 Dock 이 이 펌웨어와 같은 계열인지는 실물로 확인하지 않았다(정체 확인 = probe).

이 모듈이 지키는 것
  1. 허용 목록(config.DOCK["allowed"] — CELL_AON · CELL_EN_INIT)의 명령과 정체 확인용 무해한 줄(PROBE) 말고는 보내지 않는다. 빈 줄도 보내지 않는다.
  2. 누름 명령은 호출자가 all_off_confirmed=True 를 넘길 때만 보낸다. '그 세트의 셀이 모두 꺼져 있다'는 판정(불변식 I8 · I9 · I13)은
     호출자(엔진 · 사람)의 몫이다 — 켜진 셀이 하나라도 있는 세트를 누르면 그 셀이 꺼질 수 있다.
  3. 스위치(세트 줄 dock.power_on)가 꺼져 있으면 엔진 경로는 포트를 열지도 않는다(I10). 사람 도구(tools/dock_cli.py)만 manual=True 로
     스위치와 무관하게 보낸다 — 실물 확인(recovery-plan 4-4)은 스위치를 켜기 전에 하는 일이라서다. 엔진은 manual 을 넘기지 않는다.
  4. 포트는 보내는 동안만 연다. 열기 전에 DTR · RTS 를 내린다(hr.read_board 와 같은 방식).
  5. 한 바이트씩 보내고 그 echo 를 확인한 뒤 다음 바이트를 보낸다. echo 가 없거나 다르면 거기서 멈춘다 — 줄 끝(\n)이 가지 않으므로
     Dock 은 아무것도 실행하지 않는다(echo 하지 않는 다른 장치에는 첫 바이트 하나만 간다). 한꺼번에 보내면 Dock 앞 변환기의 버퍼가 작을 때
     가운데 바이트를 잃어 "CELL_AON" 이 "CELL_"(= CELL_EN_INIT)이나 "CELL_O"(= CELL_OFF)로 읽힐 수 있다.
     멈추면 Dock 의 줄 버퍼에 앞부분이 남는데, 다음 줄과 이어 붙으면 어떤 명령과도 맞지 않아 무시되고 버퍼가 비워진다(probe 가 그 정리도 한다).
결과는 예외 대신 DockResult · WakeResult 로 돌려준다 (못 찾음 · 못 엶 · echo 없음 · 응답 없음 · 거부).

엔진이 부르는 꼴 (커밋 5 — 세트가 모두 꺼졌다고 판정한 뒤):
    r = Dock(cfg, set_id).wake(all_off_confirmed=True)     # r.ok · r.reason · r.steps[i].wrote(0 이면 아무것도 안 감)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .config import DOCK, dock_problems, dock_settings, set_entry

PROBE = "PING"        # 정체 확인용 무해한 줄 — 어떤 Dock 명령의 앞부분도 아니라(P 로 시작하는 명령이 없다) 펌웨어가 무시한다
READ_S = 0.05         # 포트 읽기 한 번의 대기 — 짧게 끊어 읽으며 시각 상한을 본다


@dataclass
class DockResult:
    """명령(또는 PROBE) 하나의 결과."""
    ok: bool                  # 누름 명령: echo 일치 + 응답에 "OK!". PROBE: echo 일치
    cmd: str
    reason: str = ""          # 실패 · 거부 이유 (ok 면 "")
    port: str | None = None   # 쓴 포트 (못 찾았으면 None)
    found_by: str = ""        # "usb_serial" · "port"
    device: str = ""          # 찾은 장치 (VID:PID 설명) — 사람이 보고 맞는 장치인지 가린다
    wrote: int = 0            # Dock 에 쓴 바이트 수. 0 이면 아무것도 가지 않았다 · 줄 길이와 같아야 Dock 이 줄을 실행할 수 있다
    echo_ok: bool = False     # 쓴 바이트가 모두 그대로 되돌아왔나
    reply: str = ""           # echo 뒤에 온 응답 원문
    ok_mark: bool = False     # 응답에 표지("OK!")가 있나
    seconds: float = 0.0      # 포트를 연 뒤 닫을 때까지
    raw: bytes = b""          # 받은 바이트 전부 (echo + 응답) — 실물 확인 때 사람이 본다


@dataclass
class WakeResult:
    """순서(세트 줄 dock.wake) 실행의 결과. 하나라도 실패하면 거기서 멈춘다 — steps 는 실제로 시도한 것까지."""
    ok: bool
    reason: str = ""
    steps: list[DockResult] = field(default_factory=list)


def _make_port():
    import serial
    return serial.Serial()          # 열지 않은 포트 — 열기 전에 DTR · RTS 를 걸 수 있다


def _list_ports():
    from serial.tools import list_ports
    return list_ports.comports()


def _serial_match(port_sn, want: str) -> bool:
    """USB 일련번호가 같은가. Windows FTDI 드라이버는 일련번호 뒤에 채널 글자 A 를 붙인다
    (2026-11-17 제어 PC 실측: COM28 의 FTDI 일련번호 A50285BI 를 pyserial 이 'A50285BIA' 로 준다) — 그것도 같은 것으로 친다."""
    a, b = str(port_sn or "").strip().upper(), want.strip().upper()
    return bool(a) and (a == b or a == b + "A")


def find_port(dock: dict, ports) -> tuple[str | None, str, str, str]:
    """(포트, 찾은 방법, 장치 설명, 못 찾은 이유). 포트를 열지 않는다.
    usb_serial 이 있으면 그 일련번호의 포트만 쓴다 — 없으면 못 찾음이다(port 이름으로 물러서지 않는다: USB 자리가 바뀌면 그 이름은 다른 장치일 수 있다).
    usb_serial 이 없으면 port 이름이 지금 있는 포트일 때만 쓴다 (X Dock 처럼 일련번호가 없는 장치 — pyserial 은 임시 ID 조각 '7' 을 준다)."""
    def desc(p) -> str:
        vid, pid = getattr(p, "vid", None), getattr(p, "pid", None)
        ids = f"{vid:04X}:{pid:04X} " if isinstance(vid, int) and isinstance(pid, int) else ""
        return f"{ids}{getattr(p, 'description', '') or ''}".strip()

    sn = dock.get("usb_serial")
    if isinstance(sn, str) and sn.strip():
        hit = [p for p in ports if _serial_match(getattr(p, "serial_number", None), sn)]
        if len(hit) == 1:
            return hit[0].device, "usb_serial", desc(hit[0]), ""
        if not hit:
            return None, "", "", f"USB 일련번호 {sn} 인 포트가 없다 — Dock 전원 · USB 케이블 확인"
        return None, "", "", f"USB 일련번호 {sn} 인 포트가 {len(hit)}개다({', '.join(p.device for p in hit)}) — 어느 것인지 모른다"
    name = str(dock.get("port") or "").strip()
    hit = [p for p in ports if str(getattr(p, "device", "")).upper() == name.upper()]
    if hit:
        return hit[0].device, "port", desc(hit[0]), ""
    return None, "", "", f"포트 {name} 가 없다 — 일련번호가 없는 Dock 은 USB 자리가 바뀌면 포트 번호도 바뀐다(장치 관리자에서 확인)"


class Dock:
    """세트 하나의 Dock. 만들 때는 장비에 닿지 않는다 — 설정 문제는 저장해 두고 부를 때마다 거부 결과로 돌려준다.
    make_port · list_ports · sleep · clock 은 검사가 가짜로 바꿔 끼우는 자리다."""

    def __init__(self, cfg, set_id: int, *, make_port: Callable[[], object] = _make_port,
                 list_ports: Callable[[], list] = _list_ports, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.set_id = set_id
        self.make_port, self.list_ports, self.sleep, self.clock = make_port, list_ports, sleep, clock
        sets = getattr(cfg, "sets", None)
        entry = set_entry(cfg, set_id)
        self.settings = dock_settings(entry)
        self.problems: list[str] = []
        if entry is None:
            self.problems.append(f"세트 {set_id} 가 설정에 없다")
        elif "dock" not in entry:
            self.problems.append(f"세트 {set_id} 에 dock 블록이 없다 — bench.json 세트 줄에 \"dock\": {{\"port\": …}} 를 적는다")
        # 어느 세트의 dock 블록이든 문제가 있으면 보내지 않는다 — validate 와 같은 기준이다(같은 Dock 이 두 세트에 적힌 것도 여기서 잡는다)
        self.problems += dock_problems(sets)

    # ---------- 공개 ----------

    def plan(self, cmd: str) -> bytes:
        """보낼 바이트 (줄 + 줄 끝). 보내지 않는다 — 사람 도구의 미리 보기용."""
        return (cmd + str(DOCK["eol"])).encode("ascii")

    def locate(self) -> tuple[str | None, str, str, str]:
        """find_port 를 지금 포트 목록으로. 포트를 열지 않는다. 설정 문제가 있으면 (None, "", "", 이유)."""
        if self.problems:
            return None, "", "", "; ".join(self.problems)
        try:
            ports = list(self.list_ports())
        except Exception as e:                      # pyserial 이 없거나 장치 목록을 못 읽음
            return None, "", "", f"포트 목록을 읽지 못함 — {type(e).__name__}: {e}"
        return find_port(self.settings, ports)

    def probe(self) -> DockResult:
        """정체 확인 — 무해한 줄(PROBE)을 보내 echo 만 본다. 스위치(power_on)와 무관하다: 펌웨어가 모르는 줄이라 셀에 닿지 않는다.
        사람 도구만 부른다(recovery-plan 4-4 의 1번). 그 Dock 이 남긴 줄 버퍼도 비운다."""
        if self.problems:
            return DockResult(False, PROBE, reason="; ".join(self.problems))
        return self._run(PROBE, want_mark=False)

    def send(self, cmd: str, *, all_off_confirmed: bool = False, manual: bool = False) -> DockResult:
        """누름 명령 하나. 허용 목록 밖 · all_off_confirmed 없음 · 스위치 꺼짐(manual 이 아닐 때) · 설정 문제면 포트를 열지 않고 거부한다."""
        why = self._refuse(cmd, all_off_confirmed, manual)
        if why:
            return DockResult(False, str(cmd), reason=why)
        return self._run(cmd, want_mark=True)

    def wake(self, *, all_off_confirmed: bool = False, manual: bool = False) -> WakeResult:
        """세트 줄 dock.wake 의 순서를 명령 사이 gap_s(2초)를 두고 차례로 보낸다. 하나라도 실패하면 멈추고 그때까지의 결과를 돌려준다.
        거부 조건은 send 와 같고, 순서 전체를 보내기 전에 한 번에 본다(중간에 거부되어 반만 누르는 일이 없게)."""
        if self.problems:
            return WakeResult(False, "; ".join(self.problems))
        wake = list(self.settings.get("wake") or [])
        if not wake:
            return WakeResult(False, "dock.wake 순서가 비어 있다 — 실물 확인으로 정한 순서를 적는다")
        for cmd in wake:
            why = self._refuse(cmd, all_off_confirmed, manual)
            if why:
                return WakeResult(False, why)
        steps: list[DockResult] = []
        for k, cmd in enumerate(wake):
            if k:
                self.sleep(float(DOCK["gap_s"]))
            r = self._run(cmd, want_mark=True)
            steps.append(r)
            if not r.ok:
                return WakeResult(False, f"{cmd} 실패 — {r.reason}", steps)
        return WakeResult(True, "", steps)

    # ---------- 안쪽 ----------

    def _refuse(self, cmd, all_off_confirmed: bool, manual: bool) -> str:
        if self.problems:
            return "; ".join(self.problems)
        if not isinstance(cmd, str) or cmd not in DOCK["allowed"]:
            return f"허용 목록 밖의 명령이다: {cmd!r} (보낼 수 있는 것: {' · '.join(DOCK['allowed'])})"
        if all_off_confirmed is not True:
            return "그 세트의 셀이 모두 꺼져 있다는 확인(all_off_confirmed)이 없다 — 켜진 셀을 누르면 꺼질 수 있다"
        if not manual and not self.settings.get("power_on"):
            return f"세트 {self.set_id} 의 Dock 명령 스위치(dock.power_on)가 꺼져 있다 — 실물 확인 전에는 켜지 않는다"
        return ""

    def _run(self, line: str, want_mark: bool) -> DockResult:
        port_name, how, device, why = self.locate()
        if port_name is None:
            return DockResult(False, line, reason=why)
        res = DockResult(False, line, port=port_name, found_by=how, device=device)
        t0 = self.clock()
        try:
            p = self.make_port()
            p.port, p.baudrate, p.timeout, p.write_timeout = port_name, int(self.settings["baud"]), READ_S, 1.0
            p.dtr = False                       # 열기 전에 — 여는 순간 선이 올라가지 않게
            p.rts = False
            p.open()
        except Exception as e:                  # 포트 점유(다른 프로그램) · 장치가 그 순간 빠짐
            res.reason, res.seconds = f"포트 {port_name} 를 열지 못함 — {type(e).__name__}: {e}", round(self.clock() - t0, 3)
            return res
        try:
            p.reset_input_buffer()              # 연 순간 남아 있던 바이트는 이 명령의 echo 가 아니다
            self._talk(p, line, want_mark, res)
        except Exception as e:
            res.reason = f"주고받다 끊김 — {type(e).__name__}: {e}"
        finally:
            try:
                p.close()
            except Exception:
                pass
            res.seconds = round(self.clock() - t0, 3)
        if not res.reason:
            res.ok = res.echo_ok and (res.ok_mark or not want_mark)
            if not res.ok:
                res.reason = f"응답에 {DOCK['ok_mark']} 가 없다 ({DOCK['reply_s']}초 안)"
        return res

    def _talk(self, p, line: str, want_mark: bool, res: DockResult) -> None:
        """한 바이트 쓰고 → 그 echo 를 기다려 확인 → 다음 바이트. 다 맞으면 응답을 모은다. 결과는 res 에 채운다."""
        data = self.plan(line)
        s, clock = DOCK, self.clock
        end = clock() + float(s["reply_s"])
        rx = bytearray()
        try:
            for i in range(len(data)):
                p.write(data[i:i + 1])
                res.wrote = i + 1
                until = min(end, clock() + float(s["byte_s"]))
                while len(rx) < i + 1 and clock() < until:
                    rx += p.read(1)
                if len(rx) < i + 1:
                    res.reason = f"echo 가 없다 — {i + 1}번째 바이트 {data[i:i + 1]!r} 뒤 멈춤(줄 끝을 보내지 않았다)"
                    return
                if bytes(rx[:i + 1]) != data[:i + 1]:
                    res.reason = f"echo 가 다르다 — 보낸 {data[:i + 1]!r} · 받은 {bytes(rx[:i + 1])!r} (줄 끝 전이면 보내지 않았다)"
                    return
            res.echo_ok = True
            mark = str(s["ok_mark"]).encode("ascii")
            if want_mark:
                while mark not in rx[len(data):] and clock() < end:
                    rx += p.read(1)
            else:                               # 무해한 줄 — 응답이 없어야 정상. 그래도 오는 것이 있나 잠깐 더 듣는다
                until = min(end, clock() + float(s["byte_s"]))
                while clock() < until:
                    rx += p.read(1)
        finally:
            res.raw = bytes(rx)
            res.reply = bytes(rx[len(data):]).decode("ascii", "replace") if len(rx) > len(data) else ""
            res.ok_mark = str(s["ok_mark"]) in res.reply
