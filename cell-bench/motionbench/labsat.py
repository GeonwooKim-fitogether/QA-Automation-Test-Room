"""LabSat 3 원격 제어 (Telnet 23) — Windows 네트워크 설정을 바꾸지 않고 랜선에 직접 싣는다.

왜 이렇게 하나 (2026-10-30 실측 · 사용자 결정):
  시험실 LabSat 3(RLL03-1, 시리얼 00071272, 펌웨어 01.09 build 1099)은 화면에서 주소를 바꿔도 192.168.1.225 로 돌아온다.
  192.168.1 대역은 셀 Wi-Fi(PC 192.168.1.100 고정, 셀은 LiveHub DHCP)와 겹쳐, PC 에 그 대역 주소나 경로를 넣으면 세트를
  바꿀 때마다 셀 주소와 부딪칠 수 있다. 그래서 Windows 는 192.168.1.x 를 모르게 두고, 이 모듈이 Npcap(scapy)으로 LabSat 전용
  랜(IFACE)에만 '192.168.1.250' 이라는 이름으로 TCP 를 연다. LabSat 이 .250 의 MAC 을 물으면 이 모듈이 대답하고, Windows 는
  .250 을 자기 주소로 모르므로 들어온 패킷을 무시한다(끊지 않는다).
명령은 공식 목록(https://en.racelogic.support/labsat/ls03/remote-control/commands/)의 것만 쓴다. 이 펌웨어에서 MEDIA:LIST 와
재생 전 ATTN:? 는 ERR 를 돌려준다(실측).

  python cell-bench/motionbench/labsat.py type
  python cell-bench/motionbench/labsat.py status
  python cell-bench/motionbench/labsat.py play S01-Static-GLE-8h [--from 초] [--for 초]
  python cell-bench/motionbench/labsat.py stop
"""
from __future__ import annotations

import argparse
import queue
import random
import re
import sys
import time

IFACE = r"\Device\NPF_{FFC627E7-2809-460D-AA8C-AB0F6FC82E1E}"   # 제어 PC 의 '이더넷 4'(ASIX AX88772B) — PC 가 바뀌면 여기를 고친다
MY_MAC = "00:50:b6:10:73:83"                                   # 그 어댑터의 MAC
LAB_MAC = "02:00:00:aa:4d:28"                                  # LabSat 3 랜
SRC_IP, LAB_IP, PORT = "192.168.1.250", "192.168.1.225", 23
NAME_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,64}$")              # 재생 파일 이름 — 명령 구분자(:)나 줄바꿈이 들어가지 않게

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def clean(raw: str) -> str:
    """LabSat 응답 → 글자만. 색 코드 · 프롬프트(LABSATV3 >) · 빈 줄을 뺀다."""
    lines = [_ANSI.sub("", x).strip() for x in raw.replace("\r", "\n").split("\n")]
    return "\n".join(x for x in lines if x and not x.endswith(">"))


def is_err(text: str) -> bool:
    return clean(text).strip() == "ERR"


def parse_type(text: str) -> dict:
    """TYPE 응답 → {product, serial, firmware, options{이름: bool}}. 실측 순서: 제품 · 시리얼 · 펌웨어 · … · -OPTIONS- · 'Record -NO' …"""
    lines = clean(text).split("\n")
    out: dict = {"product": None, "serial": None, "firmware": None, "options": {}}
    head = [x for x in lines if not re.match(r"^\S+\s+-(YES|NO)$", x) and x != "-OPTIONS-"]
    out["product"], out["serial"], out["firmware"] = (head + [None] * 3)[:3]
    for x in lines:
        m = re.match(r"^(\S+)\s+-(YES|NO)$", x)
        if m:
            out["options"][m.group(1)] = m.group(2) == "YES"
    return out


def parse_status(text: str) -> tuple[str, int] | None:
    """PLAY:? 응답 → (재생 중인 파일, 재생한 초). 재생 중이 아니면(ERR) None. 실측 예: 'PLAY:S01-Static-GLE-8h:DUR:00:00:21'."""
    m = re.search(r"PLAY:(.+?):DUR:(\d+):(\d+):(\d+)", clean(text))
    if not m:
        return None
    h, mi, s = (int(x) for x in m.groups()[1:])
    return m.group(1), h * 3600 + mi * 60 + s


def play_cmd(name: str, from_s: int | None = None, for_s: int | None = None) -> str:
    """재생 명령 글자. 이름과 초를 먼저 검사해 이상하면 ValueError (장비에 보내기 전에 막는다)."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(f"파일 이름 {name!r} — 영문 · 숫자 · _ - . 1~64자만")
    for k, v in (("from", from_s), ("for", for_s)):
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
            raise ValueError(f"{k} = {v!r} — 0 이상의 정수 초여야 한다")
    cmd = f"PLAY:FILE:{name}"
    if from_s is not None:
        cmd += f":FROM:{from_s}"
    if for_s is not None:
        cmd += f":FOR:{for_s}"
    return cmd


class RawTelnet:
    """LabSat 전용 랜에서만 도는 아주 작은 TCP 클라이언트 — 짧은 명령 한 줄, 짧은 응답에 맞춘 것이다(재전송 · 혼잡 제어 없음)."""

    def __init__(self, iface: str = IFACE):
        from scapy.all import AsyncSniffer  # 쓸 때만 — 판정 함수 시험에는 scapy 가 필요 없다
        self.iface, self.q = iface, queue.Queue()
        self.sport, self.seq, self.ack = random.randint(41000, 60000), random.randint(1, 2**31), 0
        self.sn = AsyncSniffer(iface=iface, lfilter=lambda p: p.src.lower() == LAB_MAC, prn=self._rx, store=False)

    def _rx(self, p):
        from scapy.all import ARP, TCP
        if p.haslayer(ARP) and p[ARP].op == 1 and p[ARP].pdst == SRC_IP:
            self._arp_reply()
        elif p.haslayer(TCP) and p[TCP].dport == self.sport:
            self.q.put(p)

    def _arp_reply(self):
        from scapy.all import ARP, Ether, sendp
        sendp(Ether(src=MY_MAC, dst=LAB_MAC) / ARP(op=2, hwsrc=MY_MAC, psrc=SRC_IP, hwdst=LAB_MAC, pdst=LAB_IP),
              iface=self.iface, verbose=False)

    def _send(self, flags: str, payload: bytes = b""):
        from scapy.all import IP, TCP, Ether, sendp
        sendp(Ether(src=MY_MAC, dst=LAB_MAC) / IP(src=SRC_IP, dst=LAB_IP) /
              TCP(sport=self.sport, dport=PORT, flags=flags, seq=self.seq, ack=self.ack, window=8192) / payload,
              iface=self.iface, verbose=False)
        self.seq += len(payload) + (1 if ("S" in flags or "F" in flags) else 0)

    def connect(self, timeout: float = 3.0) -> None:
        self.sn.start()
        time.sleep(0.4)
        self._arp_reply()
        self._send("S")
        end = time.time() + timeout
        while time.time() < end:
            try:
                t = self.q.get(timeout=0.2)["TCP"]
            except queue.Empty:
                continue
            if int(t.flags) & 0x12 == 0x12:
                self.ack = t.seq + 1
                self._send("A")
                self.read(idle=0.6, total=1.5)                   # 인사(Telnet 협상 바이트)는 읽고 버린다
                return
        self.sn.stop()
        raise ConnectionError(f"LabSat {LAB_IP}:{PORT} 이 {timeout:g}초 안에 응답하지 않았다 — 랜선 · 전원 · 주소를 본다")

    def read(self, idle: float = 1.0, total: float = 4.0) -> bytes:
        out, end, last = b"", time.time() + total, time.time()
        while time.time() < end and time.time() - last < idle:
            try:
                p = self.q.get(timeout=0.1)
            except queue.Empty:
                continue
            ip, t = p["IP"], p["TCP"]
            data = bytes(t.payload)[:max(0, ip.len - ip.ihl * 4 - t.dataofs * 4)]   # 이더넷 채움 바이트를 뺀다
            if data and t.seq == self.ack:
                self.ack += len(data)
                out += data
                last = time.time()
                self._send("A")
            if int(t.flags) & 0x01:
                self.ack += 1
                self._send("A")
                break
        return bytes(b for b in out if b < 0xF0)                 # Telnet 협상 바이트(IAC 0xFF · 0xFB~0xFE)를 버린다

    def cmd(self, text: str) -> str:
        self._send("PA", (text + "\r").encode("ascii"))
        return self.read().decode("ascii", "replace")

    def close(self) -> None:
        try:
            self._send("FA")
            self.read(idle=0.5, total=1.5)
        finally:
            self.sn.stop()


class LabSat:
    """명령 하나마다 접속 · 명령 · 끊기. transport_factory 는 시험이 가짜로 바꿔 끼운다."""

    def __init__(self, transport_factory=RawTelnet):
        self.make = transport_factory

    def _one(self, text: str) -> str:
        t = self.make()
        t.connect()
        try:
            return t.cmd(text)
        finally:
            t.close()

    def type_info(self) -> dict:
        return parse_type(self._one("TYPE"))

    def status(self) -> tuple[str, int] | None:
        return parse_status(self._one("PLAY:?"))

    def play(self, name: str, from_s: int | None = None, for_s: int | None = None) -> float:
        """재생을 시작하고 명령을 보낸 시각(벽시계 epoch 초)을 돌려준다 — 레일 출발과 맞출 때 기록한다. ERR 면 RuntimeError."""
        cmd = play_cmd(name, from_s, for_s)
        t = self.make()
        t.connect()
        try:
            t_sent = time.time()
            reply = t.cmd(cmd)
        finally:
            t.close()
        if is_err(reply):
            raise RuntimeError(f"LabSat 이 {cmd} 를 거부했다 (ERR) — 파일 이름을 PLAY:? 나 화면에서 확인한다")
        return t_sent

    def stop(self) -> str:
        return clean(self._one("PLAY:STOP"))


def main() -> int:
    ap = argparse.ArgumentParser(description="LabSat 3 원격 제어 — 랜선 직접(Npcap)")
    ap.add_argument("what", choices=["type", "status", "play", "stop"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--from", dest="from_s", type=int)
    ap.add_argument("--for", dest="for_s", type=int)
    a = ap.parse_args()
    ls = LabSat()
    if a.what == "type":
        print(ls.type_info())
    elif a.what == "status":
        st = ls.status()
        print(f"재생 중: {st[0]} · {st[1]}초" if st else "재생 중이 아님")
    elif a.what == "play":
        t = ls.play(a.name, a.from_s, a.for_s)
        print(f"재생 명령 보냄 {time.strftime('%H:%M:%S', time.localtime(t))}.{int(t % 1 * 1000):03d}")
    else:
        print(ls.stop())
    return 0


if __name__ == "__main__":
    sys.exit(main())
