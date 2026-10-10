"""셀과의 통신 — 라이브 수신기 하나, 명령 통로 하나.

LiveListener: UDP 60222 를 혼자 열어 24셀의 배터리·심박·상태를 최신값으로 들고 있는다.
CellLink: 셀을 깨워(0x10) 들어온 TCP 접속에서 상태(0x11)·추출(0x12)·삭제(0x13)·복귀(0x26)를 수행한다.
          추출은 cfg.extract_batch 대씩 묶어서 한다. resume() 은 상태·추출 없이 깨워서 0x26 만 보낸다(대기 모드 셀).
          깨우기 전 · 접속을 받을 때 · 지우기(0x13) 바로 전에 그 주소가 정말 그 시리얼의 것인지 확인한다(identity_problem).

둘 다 같은 포트(60222)를 쓰지만 하나는 UDP, 하나는 TCP 라 충돌하지 않는다.
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, NamedTuple

from . import protocol as P
from .config import Config

# 주소-시리얼 대응을 '지금 것'으로 믿는 시간 창(초) — identity_problem 의 ③·④. 운영하며 고쳐야 하면 config 로 옮길 후보.
# 이 확인을 끄는 스위치는 일부러 두지 않는다. 막는 사고(다른 셀의 데이터를 이 시리얼 이름으로 받아 지움)는 되살릴 수 없고
# 오류 없이 조용히 일어나서, '잠깐 꺼 둔' 설정이 남아 있으면 바로 그때 사고가 난다. 정상 셀이 막히면(오탐) 끄지 말고 이 창을 고친다.
IDENTITY_FRESH_S = 20.0


class Owner(NamedTuple):
    """한 주소에서 마지막으로 라이브(0x09)를 보낸 셀과 그 시각. prev_* 는 그 앞에 이 주소를 쓴 다른 셀과 그 셀의 마지막 수신 시각."""
    serial: int
    t: float
    prev_serial: int | None = None
    prev_t: float = 0.0


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
        self.ip_owner: dict[str, Owner] = {}    # 주소 → 마지막으로 0x09 를 보낸 셀 (cells 의 거꾸로 — 삭제 전 신원 확인의 재료)
        self.ip_changes = 0       # 같은 시리얼의 주소가 바뀐 횟수 (누적 — 엔진이 사이클 시작 때 값을 기억해 차이로 센다)
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
            self._handle(d, a[0], time.time())
        self._sock.close()

    def _handle(self, d: bytes, ip: str, now: float) -> None:
        """받은 데이터그램 하나를 반영한다 (소켓 없이 검사할 수 있게 run 에서 떼어 냈다)."""
        if d[:4] != P.HEADER:
            return
        live = P.parse_live(d)
        if live:
            with self._lock:
                prev = self.cells.get(live.serial)
                if prev and prev.ip != ip:
                    # 셀 주소는 DHCP 라 바뀔 수 있다(FMEA 3.3). 자주 바뀌면 LiveHub 임대·무선이 불안정하다는 신호다.
                    # 추출은 묶음마다 이 표의 최신 주소로 깨우므로(CellLink._run_group) 주소가 바뀌어도 따라간다.
                    # 바뀌는 그 순간의 틈은 identity_problem 이 깨우기 전 · 접속 · 삭제 전에 막는다.
                    self.ip_changes += 1
                self.cells[live.serial] = CellLive(ip, live.battery, live.hr, live.state, live.rssi, now,
                                                   prev.t_wait if prev else 0.0)
                owners = self.__dict__.setdefault("ip_owner", {})   # __init__ 을 거치지 않고 만든 수신기(검사용)도 받는다
                o = owners.get(ip)
                owners[ip] = (o._replace(t=now) if o and o.serial == live.serial
                              else Owner(live.serial, now, o.serial if o else None, o.t if o else 0.0))
            return
        if len(d) == 9 and d[4] == P.MSG_WAIT_FOR_TCP:
            # 0x16 에는 시리얼이 없다. IP 로 기존 항목을 찾아 시각만 남긴다.
            with self._lock:
                for c in self.cells.values():
                    if c.ip == ip:
                        c.t_wait = now

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

    def identity_view(self, serial: int, ip: str) -> tuple[CellLive | None, Owner | None]:
        """serial 의 마지막 라이브와 ip 의 주인 기록을 한 번의 잠금으로 읽는다 — 따로 읽으면 그 사이에 바뀐 것이 섞인다."""
        with self._lock:
            c = self.cells.get(serial)
            return (CellLive(**vars(c)) if c else None), self.ip_owner.get(ip)

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
    identity: str | None = None   # 주소-시리얼 불일치 이유(identity_problem). 있으면 이 셀로 지우지 않았다 — 엔진이 이상으로 남긴다
    identity_stale: bool = False  # 걸린 것이 ③ 라이브 신선도뿐인가 (대기·가득 참으로 라이브가 끊긴 셀) — 엔진이 identity_stale(노랑)로,
                                  # 아니면(①②④ 주소 충돌) identity(빨강)로 남긴다


def identity_problem(serial: int, addr: str, cell_live: CellLive | None, ip_owner: Owner | None,
                     wake_t: float, now: float, fresh_s: float = IDENTITY_FRESH_S,
                     need_fresh: bool = True) -> str | None:
    """addr 의 접속이 정말 serial 이라고 라이브(0x09)로 믿을 수 있으면 None, 아니면 그 이유.

    TCP 접속과 상태 응답(0x11)에는 시리얼이 없다. 시리얼이 실린 것은 라이브뿐이라, 라이브가 보여 준 '주소 ↔ 시리얼' 대응으로만
    상대를 알 수 있다. 셀이 재부팅해 DHCP 주소가 바뀌는 순간 이 대응이 낡으면 다른 셀의 접속을 serial 로 알고, 그 셀의 데이터를
    serial 이름으로 받아 지우게 된다(10-10 수동 작업의 사고 — 지운 데이터는 되살릴 수 없다). 그래서 하나라도 걸리면 믿지 않는다.

    ① serial 의 마지막 라이브가 addr 에서 오지 않았다 — 셀이 다른 주소로 옮겨 갔다
    ② addr 에서 마지막으로 라이브를 보낸 셀이 serial 이 아니다 — 주소가 다른 셀에게 넘어갔다
    ③ serial 의 마지막 라이브가 깨우기보다 fresh_s 넘게 앞선다 — 그 사이 재부팅해 주소를 놓았을 수 있다.
       need_fresh=False(대기 모드 셀 복귀)면 보지 않는다. 대기 셀은 정의상 라이브가 끊긴 셀이라 늘 걸리고, 복귀는 데이터를 지우지 않으며,
       복귀가 막으려는 것(측정 중인 남의 셀을 깨우기)은 그 셀이 그 주소에서 라이브를 보내므로 ②·④가 잡는다.
    ④ 깨우기 fresh_s 전부터 지금까지 addr 에서 다른 셀의 라이브가 들어왔다 — 두 셀이 한 주소를 오간다. 마지막 수신만 보는 ②는
       그 순간 마침 serial 이 보냈으면 통과하므로 따로 본다.
    now 는 이유 문장에 '몇 초 전'을 적는 데만 쓴다.
    """
    if cell_live is None:
        return f"셀 {serial} 의 라이브를 받은 적 없음"
    if cell_live.ip != addr:
        return (f"셀 {serial} 의 마지막 라이브는 {cell_live.ip} 에서 왔다({now - cell_live.t:.0f}초 전) — "
                f"접속 주소 {addr} 와 다름")
    if ip_owner is None:
        return f"{addr} 에서 받은 라이브 기록 없음"
    if ip_owner.serial != serial:
        return (f"{addr} 의 마지막 라이브는 셀 {ip_owner.serial} 것({now - ip_owner.t:.0f}초 전"
                f"{', 깨우기 뒤' if ip_owner.t > wake_t else ''}) — 기대한 셀은 {serial}")
    if need_fresh and wake_t - cell_live.t > fresh_s:
        return (f"셀 {serial} 의 마지막 라이브가 깨우기 {wake_t - cell_live.t:.0f}초 전(기준 {fresh_s:.0f}초) — "
                f"그 사이 재부팅해 주소가 바뀌었을 수 있다(대기 모드·저장 가득 참으로 라이브가 끊긴 셀도 여기 걸린다)")
    if ip_owner.prev_serial is not None and ip_owner.prev_t > wake_t - fresh_s:
        when = "깨우기 뒤" if ip_owner.prev_t > wake_t else f"깨우기 {wake_t - ip_owner.prev_t:.0f}초 전"
        return f"{addr} 에서 다른 셀 {ip_owner.prev_serial} 의 라이브가 {when}에 들어옴 — 두 셀이 한 주소를 오간다"
    return None


def _mark_unverified(path: Path) -> Path:
    """파일 이름 끝에 _unverified 를 붙인다 — 다른 셀의 데이터일 수 있다는 표시. 이름을 못 바꾸면 원래 경로를 돌려준다."""
    new = path.with_name(f"{path.stem}_unverified{path.suffix}")
    try:
        path.rename(new)
        return new
    except OSError:
        return path


def extract_problem(res: CellResult) -> str | None:
    """추출을 '다 받았다'고 믿을 수 없으면 그 이유, 믿을 수 있으면 None. 삭제(0x13)는 None 일 때만 한다.

    셀 저장이 약 160 MB 에서 가득 차 측정이 멈추므로(2026-10-10 실측) 사이클마다 지우지만, 지운 데이터는 되살릴 수 없다.
    그래서 세 조건이 모두 맞을 때만 지운다: 끝 표지까지 받았다(ended) · 오류 블록 0 · 받은 바이트 ≥ 상태 응답의 크기
    (마지막 블록은 '@' 로 채워 4096 바이트로 오므로 다 받았으면 받은 바이트가 크기 이상이다).
    상태를 받았는데 크기가 0 인 셀은 받을 것도 지울 것도 없어 문제로 보지 않는다.
    """
    if res.error:
        return res.error
    if not res.size:
        return None
    if not res.ended:
        return f"끝 표지 없음 ({res.got / 1048576:.2f}/{res.size / 1048576:.2f} MB)"
    if res.bad_blocks:
        return f"오류 블록 {res.bad_blocks}개"
    if res.got < res.size:
        return f"받은 크기가 모자람 ({res.got} < {res.size} 바이트)"
    return None


class CellLink:
    """깨우기 → TCP 접속 → 명령 → 0x26 복귀. 한 번에 여러 셀을 병렬로 다룬다."""

    def __init__(self, cfg: Config, listener: LiveListener, log: Callable[[str], None] = print,
                 progress: Callable[[], None] | None = None):
        """progress: 추출 중 데이터 묶음을 받을 때마다 부른다 (엔진의 심박). 셀 파일은 지우지 않는 한 계속 커져
        한 묶음의 추출이 몇십 분이 될 수 있는데, 그동안 로그는 묶음 시작·끝에만 찍혀 감시자가 '멈춤'으로 오판한다."""
        self.cfg, self.live, self.log = cfg, listener, log
        self.progress = progress or (lambda: None)

    # 한 접속에서 할 일 (status=False 면 인사만 받고 곧장 0x26 복귀 — 대기 모드 셀 깨우기)
    # verify: 지우기(0x13) 바로 전에 부르는 신원 확인 — 문제가 있으면 그 이유, 믿을 수 있으면 None. 필수다(검토 F10):
    # 빠뜨리면 TypeError, None 을 넘기면 지우지 않는다 — 신원 확인 없이 지우는 길을 남기지 않는다. 검사도 허용 verify 를 명시로 넘긴다.
    def _serve(self, conn: socket.socket, res: CellResult, extract: bool, out_dir: Path | None,
               status: bool = True, *, verify: Callable[[], str | None]) -> None:
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
            if not status:
                return                                            # 상태·추출 없이 — finally 가 0x26 을 보낸다
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
                        self.progress()
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
                if self.cfg.delete_after_extract and extract_problem(res) is None:
                    why = verify() if callable(verify) else "신원 확인 함수 없이 불림 — 지우지 않는다"
                    if why:
                        # 다 받았어도 지금 이 접속이 s 라고 믿을 수 없으면 지우지 않는다. 받은 파일은 다른 셀의 것일 수 있어 표시해 남긴다.
                        res.file = str(_mark_unverified(path))
                        res.identity, res.error = why, ("주소-시리얼 불일치 — 받은 파일은 " +
                                                        ("_unverified 로 남김" if res.file != str(path) else "이름을 못 바꿔 그대로 둠"))
                        self.log(f"[{s}] 삭제 안 함 — 주소-시리얼 불일치: {why} · 파일 {Path(res.file).name}")
                    else:
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

    def _identity(self, serial: int, addr: str, wake_t: float, need_fresh: bool,
                  res: CellResult | None = None) -> str | None:
        """identity_problem 을 한 번의 스냅숏으로 본다. 걸렸으면 res.identity_stale 에 '③ 신선도뿐인가'를 적는다 —
        같은 스냅숏으로 ③을 빼고 다시 봐서 통과하면 주소 충돌(①②④)의 증거는 없다는 뜻이다(검토 F15)."""
        c, owner = self.live.identity_view(serial, addr)
        now = time.time()
        why = identity_problem(serial, addr, c, owner, wake_t, now, need_fresh=need_fresh)
        if why and res is not None:
            res.identity_stale = need_fresh and identity_problem(serial, addr, c, owner, wake_t, now, need_fresh=False) is None
        return why

    def _refuse(self, res: CellResult, why: str, what: str) -> None:
        res.identity, res.error = why, f"주소-시리얼 불일치 — {what}"
        self.log(f"[{res.serial}] 주소-시리얼 불일치 — {what}: {why}")

    @staticmethod
    def _send_back(conn: socket.socket) -> None:
        """누군지 믿을 수 없는 접속은 아무것도 묻지 않고 0x26 으로 측정에 돌려보낸다. 깨우기(0x10)로 이미 측정이 멈췄으므로
        그냥 끊으면 그 셀은 TCP 대기로 남아 측정을 하지 않는다(대기 모드 — FMEA 6.3)."""
        try:
            conn.settimeout(6); conn.recv(64)                                   # 셀의 첫 인사
            conn.sendall(P.frame(P.MSG_RESET_NORMAL)); conn.settimeout(3); conn.recv(64)
        except OSError:
            pass
        finally:
            conn.close()

    def _run_group(self, serials: list[int], extract: bool, out_dir: Path | None,
                   status: bool = True) -> dict[int, CellResult]:
        """한 묶음을 깨워 접속마다 _serve 를 돌린다. 신원(identity_problem)은 세 번 본다 — 깨우기 전(어긋나면 깨우지 않는다),
        접속을 받을 때(어긋나면 받지 않고 0x26 으로 돌려보낸다), 지우기 바로 전(_serve 의 verify — 어긋나면 지우지 않는다).
        status=False 는 대기 모드 셀 복귀(resume)뿐이라 ③ 신선도는 보지 않는다(이유는 identity_problem)."""
        need_fresh = status
        ip_of = {s: self.live.ip_of(s) for s in serials}      # 묶음마다 지금 주소를 다시 읽는다 (DHCP 로 바뀌었을 수 있다)
        results = {s: CellResult(s, ip_of[s] or "?") for s in serials}
        wake_t = time.time()
        targets = []
        for s in serials:
            if not ip_of[s]:
                results[s].error = "라이브 신호 없음"
            elif why := self._identity(s, ip_of[s], wake_t, need_fresh, results[s]):
                self._refuse(results[s], why, "깨우지 않음")
            else:
                targets.append(s)
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
        threads = []; connected: set[int] = set()
        for _ in targets:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                break
            s = by_ip.get(addr[0])
            why = self._identity(s, addr[0], wake_t, need_fresh, results[s]) if s is not None else None
            if s is None or why:
                if why:
                    self._refuse(results[s], why, "받지 않음")
                th = threading.Thread(target=self._send_back, args=(conn,), daemon=True)
            else:
                connected.add(s)
                th = threading.Thread(target=self._serve, args=(conn, results[s], extract, out_dir, status),
                                      kwargs={"verify": lambda s=s, a=addr[0]: self._identity(s, a, wake_t, need_fresh, results[s])},
                                      daemon=True)
            th.start(); threads.append(th)
        srv.close()
        for th in threads:
            th.join()
        for s in targets:
            if s not in connected and results[s].error is None:
                results[s].error = "TCP 접속 없음"
        return results

    def resume(self, serials: list[int]) -> dict[int, CellResult]:
        """대기 모드(0x16 만 오고 측정이 멈춘) 셀만 깨워 0x26 으로 측정에 돌려보낸다 — 상태·추출·삭제 없이.

        결과의 resume_s 가 측정 복귀까지 걸린 초(못 돌아오면 None). 가득 차서 멈춘 셀은 이것으로 돌아오지 않는다(2026-10-10).
        """
        out: dict[int, CellResult] = {}
        for i in range(0, len(serials), self.cfg.extract_batch):
            out.update(self._run_group(serials[i:i + self.cfg.extract_batch], extract=False, out_dir=None, status=False))
        return out

    def status(self, serials: list[int]) -> dict[int, CellResult]:
        """깨워서 0x11 만 묻고 0x26 으로 복귀. 측정이 복귀 시간(약 20초)만큼 멈춘다."""
        out: dict[int, CellResult] = {}
        for i in range(0, len(serials), self.cfg.extract_batch):
            out.update(self._run_group(serials[i:i + self.cfg.extract_batch], extract=False, out_dir=None))
        return out

    def extract(self, serials: list[int], out_dir: Path) -> dict[int, CellResult]:
        """배치 단위로 받는다. 삭제는 cfg.delete_after_extract 가 True 이고 extract_problem 이 None 일 때만."""
        out_dir.mkdir(parents=True, exist_ok=True)
        out: dict[int, CellResult] = {}
        for i in range(0, len(serials), self.cfg.extract_batch):
            group = serials[i:i + self.cfg.extract_batch]
            self.log(f"추출 {i // self.cfg.extract_batch + 1}번째 묶음: {group}")
            out.update(self._run_group(group, extract=True, out_dir=out_dir))
        return out
