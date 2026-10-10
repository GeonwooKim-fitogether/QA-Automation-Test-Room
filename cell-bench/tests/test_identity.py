"""삭제 전 신원 확인 (FMEA P4) — 삭제 명령이 주소만 보고 엉뚱한 셀로 가는 틈을 막는다.

10-10 수동 작업에서 삭제 명령이 주소만 보고 다른 셀로 갔다. 상태 응답(0x11)에는 시리얼이 없어서, 셀이 재부팅해 DHCP 주소가
바뀌는 순간 프로그램도 B 의 접속을 A 로 알고 B 의 데이터를 A 이름으로 받아 지울 수 있다.

순수 판정(identity_problem) → 수신기의 '주소 → 마지막 0x09 를 보낸 셀' 표 → 이 PC 안의 소켓 짝(127.0.0.1 임의 포트)으로
깨우기 · 접속 · 추출 · 삭제 전체 순서다. 실제 셀 · 실제 주소 · 60222 포트는 쓰지 않는다.
"""
import socket
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench import protocol as P
from cellbench.cells import (IDENTITY_FRESH_S, CellLink, CellLive, CellResult, LiveListener, Owner, extract_problem,
                             identity_problem)
from cellbench.config import Config
from cellbench.record import ALERT_KINDS, EVENT_LIGHT, Recorder

A, B = 11594, 11595
ADDR = "127.0.0.1"
W = 1000.0                                   # 순수 판정의 깨우기 시각


# ---------- 순수 판정 ----------

def live(ip=ADDR, t=W - 1):
    return CellLive(ip, 80, 0, 4, -40, t)


def test_consistent_and_fresh_is_trusted():
    assert identity_problem(A, ADDR, live(), Owner(A, W - 1), W, W + 5) is None


def test_1_serial_last_heard_from_another_address():
    why = identity_problem(A, ADDR, live(ip="10.0.0.9"), Owner(A, W - 1), W, W + 5)
    assert why and "10.0.0.9" in why and "다름" in why
    assert "받은 적 없음" in identity_problem(A, ADDR, None, None, W, W + 5)


def test_2_address_last_used_by_another_serial():
    why = identity_problem(A, ADDR, live(), Owner(B, W - 0.5, A, W - 1), W, W + 5)
    assert why and str(B) in why and "깨우기 뒤" not in why
    assert "깨우기 뒤" in identity_problem(A, ADDR, live(), Owner(B, W + 2, A, W - 1), W, W + 5)


def test_3_stale_mapping_before_wake():
    stale = W - IDENTITY_FRESH_S - 1
    why = identity_problem(A, ADDR, live(t=stale), Owner(A, stale), W, W + 5)
    assert why and f"기준 {IDENTITY_FRESH_S:.0f}초" in why
    assert identity_problem(A, ADDR, live(t=W - IDENTITY_FRESH_S + 1), Owner(A, W - IDENTITY_FRESH_S + 1), W, W + 5) is None
    # 대기 모드 셀 복귀는 정의상 라이브가 끊긴 셀이 대상이라 ③을 보지 않는다
    assert identity_problem(A, ADDR, live(t=stale), Owner(A, stale), W, W + 5, need_fresh=False) is None


def test_4_another_serial_on_the_address_after_or_just_before_wake():
    # 마지막 수신은 A 라 ②는 통과하지만, 깨우기 뒤에 B 도 이 주소에서 보냈다 — 두 셀이 한 주소를 오간다
    why = identity_problem(A, ADDR, live(t=W + 3), Owner(A, W + 3, B, W + 2), W, W + 5)
    assert why and str(B) in why and "깨우기 뒤" in why
    assert "깨우기 5초 전" in identity_problem(A, ADDR, live(), Owner(A, W - 1, B, W - 5), W, W + 5)
    assert identity_problem(A, ADDR, live(), Owner(A, W - 1, B, W - 60), W, W + 5) is None   # 오래전 넘겨받은 주소는 괜찮다
    assert identity_problem(A, ADDR, live(t=W - 100), Owner(A, W - 100, B, W + 1), W, W + 5, need_fresh=False)  # 복귀도 본다


# ---------- 수신기의 주소 주인 표 ----------

def _pkt(serial):
    d = bytearray(P.LIVE_LEN); d[:4] = P.HEADER; d[4] = P.MSG_LIVE
    struct.pack_into("<H", d, 11, serial); d[52] = 80
    return bytes(d)


WAIT = P.HEADER + bytes([P.MSG_WAIT_FOR_TCP]) + b"\x00" * 4


def bare_listener():
    L = LiveListener.__new__(LiveListener)                   # 포트를 열지 않는다
    L.cells, L.ip_owner, L._lock, L.ip_changes = {}, {}, threading.Lock(), 0
    return L


def test_listener_keeps_last_sender_per_address():
    L = bare_listener()
    L._handle(_pkt(B), "10.0.0.5", 1.0)
    L._handle(_pkt(A), "10.0.0.5", 2.0)                      # 주소가 A 에게 넘어감
    L._handle(_pkt(A), "10.0.0.5", 3.0)
    L._handle(_pkt(B), "10.0.0.6", 4.0)                      # B 는 새 주소로
    L._handle(WAIT, "10.0.0.5", 5.0)                         # 0x16 에는 시리얼이 없다 — 주인 표를 바꾸지 않는다
    assert L.ip_owner["10.0.0.5"] == Owner(A, 3.0, B, 1.0)
    assert L.ip_owner["10.0.0.6"] == Owner(B, 4.0, None, 0.0)
    c, o = L.identity_view(A, "10.0.0.5")
    assert c.ip == "10.0.0.5" and c.t == 3.0 and c.t_wait == 5.0 and o.serial == A


def test_listener_built_without_init_still_records_owner():
    L = LiveListener.__new__(LiveListener)                   # test_guards 처럼 표를 다 갖추지 않은 수신기
    L.cells, L._lock, L.ip_changes = {}, threading.Lock(), 0
    L._handle(_pkt(A), "10.0.0.5", 1.0)
    assert L.ip_owner["10.0.0.5"] == Owner(A, 1.0)


# ---------- 소켓 짝으로 깨우기 · 접속 · 추출 · 삭제 ----------

def _blocks(n):
    data = bytes(range(256)) * 16
    return b"".join(struct.pack("<II", i * P.BLOCK_DATA, sum(data)) + data for i in range(n))


def _free_tcp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.bind((ADDR, 0))
    port = s.getsockname()[1]; s.close()
    return port


class FakeCell(threading.Thread):
    """셀 흉내 — 깨우기(UDP)를 받으면 TCP 로 붙어 인사 · 상태 · 블록 · 끝 표지를 보내고, PC 명령 종류를 seen 에 남긴다.

    on_wake: 깨우기를 받은 뒤 붙기 전에 부른다. on_upload: 업로드(0x12) 요청을 받은 뒤 블록을 보내기 전에 부른다.
    """

    def __init__(self, tcp_port, size=3 * P.BLOCK_DATA - 50, nblocks=3, on_wake=None, on_upload=None):
        super().__init__(daemon=True)
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.udp.bind((ADDR, 0)); self.udp.settimeout(2)
        self.wake_port = self.udp.getsockname()[1]
        self.tcp_port, self.size, self.blocks = tcp_port, size, _blocks(nblocks)
        self.on_wake, self.on_upload = on_wake, on_upload
        self.woken, self.seen = False, []

    def _frame(self, c):
        got = b""
        while len(got) < 54:                                  # PC 명령 프레임은 54 바이트
            x = c.recv(54 - len(got))
            if not x:
                return None
            got += x
        self.seen.append(got[4]); return got[4]

    def run(self):
        try:
            d, _ = self.udp.recvfrom(64)
        except socket.timeout:
            return                                            # 깨우지 않았다
        finally:
            self.udp.close()
        self.woken = d[4] == P.MSG_WAKE
        if self.on_wake:
            self.on_wake()
        c = socket.create_connection((ADDR, self.tcp_port), timeout=10)
        c.sendall(b"\x00" * 46)                               # 첫 인사
        while True:
            kind = self._frame(c)
            if kind is None or kind == P.MSG_RESET_NORMAL:
                break
            if kind == P.MSG_STATUS:
                c.sendall(P.HEADER + bytes([P.MSG_STATUS, 0, 0xF0, 88]) + struct.pack("<i", self.size) + b"\x00")
            elif kind == P.MSG_UPLOAD:
                if self.on_upload:
                    self.on_upload()
                c.sendall(self.blocks); c.sendall(P.MARK)
            elif kind == P.MSG_DELETE:
                c.sendall(P.HEADER + bytes([P.MSG_DELETE, 0]))
        c.close()


def run_link(L, op, tmp_path, **cell_kw):
    """가짜 셀 하나와 CellLink 하나를 127.0.0.1 임의 포트로 잇고 op('extract'|'resume')를 A 에 돌린다."""
    port = _free_tcp_port()
    cell = FakeCell(port, **cell_kw); cell.start()
    cfg = Config(pc_ip=ADDR, port=port, wake_port=cell.wake_port, resume_timeout_s=0.3)
    link = CellLink(cfg, L, log=lambda m: None)
    res = link.extract([A], tmp_path)[A] if op == "extract" else link.resume([A])[A]
    cell.join(5)
    return res, cell


def test_consistent_cell_is_extracted_and_deleted(tmp_path):
    L = bare_listener(); L._handle(_pkt(A), ADDR, time.time())
    res, cell = run_link(L, "extract", tmp_path)
    assert cell.seen == [P.MSG_STATUS, P.MSG_UPLOAD, P.MSG_DELETE, P.MSG_RESET_NORMAL]
    assert res.deleted and res.identity is None and res.error is None
    assert [f.name for f in tmp_path.glob("*.bin")] == [Path(res.file).name] and "_unverified" not in res.file


def test_other_serial_connecting_from_the_address_is_not_extracted_or_deleted(tmp_path):
    """깨울 때는 주소표가 A 였는데, 그 사이 B 가 A 의 옛 주소를 받아 라이브를 보내고 깨우기에 B 가 붙었다."""
    L = bare_listener(); L._handle(_pkt(A), ADDR, time.time())
    res, cell = run_link(L, "extract", tmp_path, on_wake=lambda: L._handle(_pkt(B), ADDR, time.time()))
    assert cell.woken and cell.seen == [P.MSG_RESET_NORMAL]          # 상태 · 추출 · 삭제 없이 0x26 으로 돌려보냄
    assert res.error == "주소-시리얼 불일치 — 받지 않음" and str(B) in res.identity
    assert not res.deleted and res.size is None and list(tmp_path.glob("*.bin")) == []


def test_stale_address_table_is_not_even_woken(tmp_path):
    """주소표가 이미 낡았다 — A 가 마지막으로 들린 주소에서 그 뒤 B 가 들렸다."""
    L = bare_listener(); now = time.time()
    L._handle(_pkt(A), ADDR, now - 5); L._handle(_pkt(B), ADDR, now - 1)
    res, cell = run_link(L, "extract", tmp_path)
    assert not cell.woken and cell.seen == []
    assert res.error == "주소-시리얼 불일치 — 깨우지 않음" and str(B) in res.identity


def test_serial_heard_elsewhere_during_upload_keeps_data_unverified(tmp_path):
    """다 받았는데 그동안 A 가 다른 주소에서 들렸다 — 지금 받은 상대는 A 가 아니다. 지우지 않고 파일은 표시해 남긴다."""
    L = bare_listener(); L._handle(_pkt(A), ADDR, time.time())
    res, cell = run_link(L, "extract", tmp_path, on_upload=lambda: L._handle(_pkt(A), "10.0.0.99", time.time()))
    assert cell.seen == [P.MSG_STATUS, P.MSG_UPLOAD, P.MSG_RESET_NORMAL]   # 0x13 이 가지 않았다
    assert res.ended and not res.deleted and "10.0.0.99" in res.identity
    assert res.error.startswith("주소-시리얼 불일치") and extract_problem(res) == res.error
    files = list(tmp_path.glob("*.bin"))
    assert len(files) == 1 and files[0].name.endswith("_unverified.bin") and res.file == str(files[0])


def test_cell_silent_too_long_is_not_extracted(tmp_path):
    L = bare_listener(); L._handle(_pkt(A), ADDR, time.time() - IDENTITY_FRESH_S - 10)
    res, cell = run_link(L, "extract", tmp_path)
    assert not cell.woken and res.error == "주소-시리얼 불일치 — 깨우지 않음" and "기준" in res.identity


def test_resume_still_wakes_waiting_cell(tmp_path):
    """대기 모드 셀은 라이브가 끊긴 셀이라 ③(신선도)을 보지 않는다 — 복귀가 막히면 안 된다."""
    L = bare_listener(); now = time.time()
    L._handle(_pkt(A), ADDR, now - 100); L._handle(WAIT, ADDR, now)
    res, cell = run_link(L, "resume", tmp_path)
    assert cell.woken and cell.seen == [P.MSG_RESET_NORMAL] and res.identity is None


def test_resume_does_not_wake_a_cell_measuring_on_that_address(tmp_path):
    """A 는 대기 모드였는데 지금 그 주소에서는 B 가 측정 중이다 — 깨우면 B 의 측정을 끊는다."""
    L = bare_listener(); now = time.time()
    L._handle(_pkt(A), ADDR, now - 100); L._handle(_pkt(B), ADDR, now - 1)
    res, cell = run_link(L, "resume", tmp_path)
    assert not cell.woken and res.error == "주소-시리얼 불일치 — 깨우지 않음" and str(B) in res.identity


def test_send_back_only_resets():
    pc, cell_sock = socket.socketpair()
    seen = []

    def fake():
        cell_sock.sendall(b"\x00" * 46)
        got = b""
        while len(got) < 54:
            x = cell_sock.recv(54 - len(got))
            if not x:
                break
            got += x
        seen.append(got[4] if len(got) >= 5 else None); cell_sock.close()

    th = threading.Thread(target=fake); th.start()
    CellLink._send_back(pc)
    th.join(5)
    assert seen == [P.MSG_RESET_NORMAL]


# ---------- 이상 종류 ----------

def test_identity_event_is_red_and_alerts(tmp_path):
    assert EVENT_LIGHT["identity"] == "red" and "identity" in ALERT_KINDS
    sent = []
    rec = Recorder(tmp_path, alert=sent.append)
    rec.event(3, "EXTRACT", "identity", A, "주소-시리얼 불일치 — 받지 않음 · x")
    assert len(sent) == 1 and "identity" in sent[0]
