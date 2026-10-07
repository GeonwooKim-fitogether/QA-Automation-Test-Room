"""셀과의 통신 — 라이브 수신기 하나, 명령 통로 하나.

LiveListener: UDP 60222 를 혼자 열어 24셀의 배터리·심박·상태를 최신값으로 들고 있는다.
CellLink: 셀을 깨워(0x10) 들어온 TCP 접속에서 상태(0x11)·추출(0x12)·삭제(0x13)·복귀(0x26)를 수행한다.
          추출은 cfg.extract_batch 대씩 묶어서 한다.

둘 다 같은 포트(60222)를 쓰지만 하나는 UDP, 하나는 TCP 라 충돌하지 않는다.
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import protocol as P
from .config import Config


@dataclass
class CellLive:
    ip: str
    battery: int
    hr: int
    state: int
    rssi: int
    t: float                  # 마지막 라이브(0x09) 수신 시각
    t_wait: float = 0.0       # 마지막 0x16 수신 시각

    @property
    def waiting(self) -> bool:
        """측정을 멈추고 TCP 만 기다리는 중인가.

        정상 측정 중에도 셀은 0x09 를 초당 2번, 0x16 을 초당 1번 함께 보낸다(2026-10-07 실측).
        그래서 0x16 만으로는 판단할 수 없고, 0x16 은 오는데 0x09 가 3초 넘게 끊겼을 때만 대기로 본다.
        """
        return self.t_wait - self.t > 3.0


class PortBusy(RuntimeError):
    """다른 프로그램(보통 run_cycle.py)이 이미 셀 라이브 포트를 받고 있다."""


def bind_live_udp(port: int) -> socket.socket:
    """셀 라이브 포트를 '독점'으로 연다.

    Windows 에서 SO_REUSEADDR 로 두 프로그램이 같은 UDP 포트를 열면, 셀 신호가 둘 중 한쪽으로만
    제멋대로 간다. 시험 중에 도구를 하나 띄웠다가 시험 쪽이 신호를 못 받아 '셀 끊김'으로 기록되는 일을
    막으려고, 두 번째로 여는 쪽은 조용히 나눠 받지 않고 PortBusy 로 실패하게 한다.
    (TCP 명령 포트는 추출 묶음마다 다시 열어야 해서 독점으로 잡지 않는다.)
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        s.bind(("0.0.0.0", port))
    except OSError as e:
        s.close()
        raise PortBusy(f"UDP {port} 를 이미 다른 프로그램이 쓰고 있다 — run_cycle.py 가 돌고 있으면 "
                       f"data/now.json 으로 상태를 보라 ({e})") from e
    return s


class LiveListener(threading.Thread):
    """셀 라이브(UDP) 수신기. PC 전체에서 하나만 둔다 (두 번째는 PortBusy)."""

    def __init__(self, cfg: Config):
        super().__init__(daemon=True, name="live-listener")
        self.cfg = cfg
        self.cells: dict[int, CellLive] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sock = bind_live_udp(cfg.port)
        self._sock.settimeout(0.5)

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                d, a = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if d[:4] != P.HEADER:
                continue
            now = time.time()
            live = P.parse_live(d)
            if live:
                with self._lock:
                    prev = self.cells.get(live.serial)
                    self.cells[live.serial] = CellLive(a[0], live.battery, live.hr, live.state, live.rssi, now,
                                                       prev.t_wait if prev else 0.0)
                continue
            if len(d) == 9 and d[4] == P.MSG_WAIT_FOR_TCP:
                # 0x16 에는 시리얼이 없다. IP 로 기존 항목을 찾아 시각만 남긴다.
                with self._lock:
                    for c in self.cells.values():
                        if c.ip == a[0]:
                            c.t_wait = now
        self._sock.close()

    def stop(self) -> None:
        self._stop.set()

    # --- 조회 ---
    def snapshot(self) -> dict[int, CellLive]:
        with self._lock:
            return {k: CellLive(**vars(v)) for k, v in self.cells.items()}

    def alive(self, within_s: float = 5.0) -> list[int]:
        now = time.time()
        return sorted(s for s, c in self.snapshot().items() if now - c.t <= within_s and not c.waiting)

    def ip_of(self, serial: int) -> str | None:
        c = self.snapshot().get(serial)
        return c.ip if c else None

    def wait_for(self, serials: list[int], timeout_s: float) -> list[int]:
        """serials 가 모두 들릴 때까지 기다리고, 끝내 안 들린 것을 돌려준다."""
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            missing = [s for s in serials if s not in self.alive()]
            if not missing:
                return []
            time.sleep(0.3)
        return [s for s in serials if s not in self.alive()]

    def wait_resume(self, serial: int, since: float, timeout_s: float) -> float | None:
        """since 이후 첫 라이브 수신까지 걸린 초. 시간 안에 안 오면 None."""
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            c = self.snapshot().get(serial)
            if c and c.t > since and not c.waiting:
                return c.t - since
            time.sleep(0.3)
        return None


@dataclass
class CellResult:
    serial: int
    ip: str
    battery: int | None = None
    size: int | None = None
    got: int = 0
    bad_blocks: int = 0
    ended: bool = False
    deleted: bool = False
    file: str | None = None
    t_start: float = 0.0
    t_end: float = 0.0
    resume_s: float | None = None
    error: str | None = None


class CellLink:
    """깨우기 → TCP 접속 → 명령 → 0x26 복귀. 한 번에 여러 셀을 병렬로 다룬다."""

    def __init__(self, cfg: Config, listener: LiveListener, log: Callable[[str], None] = print):
        self.cfg, self.live, self.log = cfg, listener, log

    # 한 접속에서 할 일
    def _serve(self, conn: socket.socket, res: CellResult, extract: bool, out_dir: Path | None) -> None:
        s = res.serial
        def recv_until(sec: float, done: Callable[[bytes], bool]) -> bytes:
            conn.settimeout(0.5); buf = b""; t = time.time()
            while time.time() - t < sec:
                try:
                    x = conn.recv(65536)
                    if not x:
                        break
                    buf += x
                    if done(buf):
                        break
                except socket.timeout:
                    pass
            return buf
        try:
            recv_until(6, lambda b: len(b) >= 46)                 # 셀의 첫 인사
            conn.sendall(P.frame(P.MSG_STATUS))                   # 0x11 은 0x12 보다 먼저여야 한다
            st = P.parse_status(recv_until(6, lambda b: len(b) >= P.STATUS_LEN))
            if not st:
                res.error = "상태 응답 없음"; return
            res.battery, res.size = st.battery, st.size
            self.log(f"[{s}] 상태: 배터리 {st.battery}% · 파일 {st.size/1048576:.2f} MB")
            if extract and st.size > 0 and out_dir is not None:
                res.t_start = time.time()
                path = out_dir / f"ftg_{s}_{time.strftime('%Y%m%d_%H%M%S')}.bin"
                conn.sendall(P.upload_frame(0)); conn.settimeout(30)
                buf = bytearray(); expect = 0
                with open(path, "wb") as fo:
                    while True:
                        try:
                            x = conn.recv(262144)
                        except socket.timeout:
                            res.error = "30초 무응답"; break
                        if not x:
                            res.error = "연결 끊김"; break
                        buf += x
                        while len(buf) >= P.BLOCK_LEN and buf[:len(P.MARK)] != P.MARK:
                            pos, data, ok = P.check_block(bytes(buf[:P.BLOCK_LEN]), expect)
                            del buf[:P.BLOCK_LEN]
                            if not ok:
                                res.bad_blocks += 1
                            expect = pos + P.BLOCK_DATA
                            fo.write(data); res.got += P.BLOCK_DATA
                        if P.MARK in buf:
                            res.ended = True; break
                res.t_end = time.time(); res.file = str(path)
                dt = max(res.t_end - res.t_start, 0.001)
                self.log(f"[{s}] {'완료' if res.ended else '미완료'}: {res.got/1048576:.2f}/{st.size/1048576:.2f} MB"
                         f" · {dt:.0f}초 · {res.got/1048576/dt:.2f} MB/s · 오류 {res.bad_blocks}")
                if res.ended and res.bad_blocks == 0 and self.cfg.delete_after_extract:
                    conn.sendall(P.frame(P.MSG_DELETE))
                    ack = recv_until(6, lambda b: len(b) >= 6)
                    res.deleted = len(ack) >= 6 and ack[4] == P.MSG_DELETE
                    self.log(f"[{s}] 삭제 {'완료' if res.deleted else '응답 없음'}")
        except OSError as e:
            res.error = f"소켓 오류 {e}"
        finally:
            reset_at = time.time()
            try:
                conn.sendall(P.frame(P.MSG_RESET_NORMAL)); conn.settimeout(3); conn.recv(64)
            except OSError:
                pass
            conn.close()
            res.resume_s = self.live.wait_resume(s, reset_at, self.cfg.resume_timeout_s)
            self.log(f"[{s}] 측정 복귀 {res.resume_s:.0f}초" if res.resume_s is not None
                     else f"[{s}] {self.cfg.resume_timeout_s:.0f}초 안에 측정 미복귀")

    def _run_group(self, serials: list[int], extract: bool, out_dir: Path | None) -> dict[int, CellResult]:
        ip_of = {s: self.live.ip_of(s) for s in serials}
        results = {s: CellResult(s, ip_of[s] or "?") for s in serials}
        targets = [s for s in serials if ip_of[s]]
        for s in serials:
            if not ip_of[s]:
                results[s].error = "라이브 신호 없음"
        if not targets:
            return results
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.cfg.pc_ip, self.cfg.port)); srv.listen(len(targets) + 2); srv.settimeout(25)
        snd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); snd.bind((self.cfg.pc_ip, 0))
        for s in targets:
            snd.sendto(P.wake_frame(), (ip_of[s], self.cfg.wake_port))
        snd.close()
        by_ip = {ip_of[s]: s for s in targets}
        threads = []
        for _ in targets:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                break
            s = by_ip.get(addr[0])
            if s is None:
                conn.close(); continue
            th = threading.Thread(target=self._serve, args=(conn, results[s], extract, out_dir), daemon=True)
            th.start(); threads.append(th)
        srv.close()
        for th in threads:
            th.join()
        for s in targets:
            if results[s].battery is None and results[s].error is None:
                results[s].error = "TCP 접속 없음"
        return results

    def status(self, serials: list[int]) -> dict[int, CellResult]:
        """깨워서 0x11 만 묻고 0x26 으로 복귀. 측정이 복귀 시간(약 20초)만큼 멈춘다."""
        out: dict[int, CellResult] = {}
        for i in range(0, len(serials), self.cfg.extract_batch):
            out.update(self._run_group(serials[i:i + self.cfg.extract_batch], extract=False, out_dir=None))
        return out

    def extract(self, serials: list[int], out_dir: Path) -> dict[int, CellResult]:
        """배치 단위로 받는다. 삭제는 cfg.delete_after_extract 가 True 일 때만."""
        out_dir.mkdir(parents=True, exist_ok=True)
        out: dict[int, CellResult] = {}
        for i in range(0, len(serials), self.cfg.extract_batch):
            group = serials[i:i + self.cfg.extract_batch]
            self.log(f"추출 {i // self.cfg.extract_batch + 1}번째 묶음: {group}")
            out.update(self._run_group(group, extract=True, out_dir=out_dir))
        return out
