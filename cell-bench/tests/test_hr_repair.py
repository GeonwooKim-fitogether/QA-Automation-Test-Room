"""심박 자동 재연결 — 짝 보드는 켜져 있고 연결 0 인데 셀 심박이 10분 넘게 0 이면 그 셀에 이름(0x20)을 다시 보낸다. 장비 없이 돈다.

배경(2026-10-15 실측): 19:57 셀 11746 심박 0 · 짝 보드 연결 0 → 이름을 다시 보내 35초 뒤 심박 61. 같은 날 20:50 에도 셀 11740 · 11742 가
같은 상태(보드 켜짐 · 연결 0)로 남아 있었다. 손으로 하던 일(.claude/state/pair_cells.py)을 엔진이 조건을 지켜 스스로 한다.

순수 조건(hr.repair_due) → 셀 통신(이 PC 안의 소켓 짝으로 0x20 · 응답 · 0x26) → 엔진 배선(CycleRunner._watch_hr) 순서다.
"""
import json
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench import hr
from cellbench import protocol as P
from cellbench.cells import CellLink, CellLive, CellResult, LiveListener
from cellbench.config import HR, Config
from cellbench.cycle import CycleRunner, CycleState
from cellbench.record import EVENT_LIGHT, Recorder

T = 1_800_000_000.0
TH = dict(HR)
PAIRS = {11733: {"board": "HRSIM-0001", "no": 1, "hub": "A", "port": 1}, 11734: {"board": "HRSIM-0002", "no": 2, "hub": "A", "port": 2}}


def boards(**st):
    """보드 이름 끝자리 → 상태: free(연결 0) · joined · off"""
    out = {}
    for n, s in st.items():
        b = {"alive": s != "off", "conns": 0 if s == "free" else 1, "t_seen": T - 10, "port": None if s == "off" else "COM1"}
        out[f"HRSIM-000{n[-1]}"] = b
    return {"t": T - 10, "boards": out}


def due(cells, bd, last=None, phase="DISCHARGE", busy=False, th=TH):
    return hr.repair_due(PAIRS, cells, bd, T, last or {}, th, phase, busy, 15.0)


ZERO = {"hr": 0, "age": 0.3, "waiting": False, "hr0_s": 700}


# ---------- 순수 조건 ----------

def test_repair_due_only_when_every_condition_holds():
    bd = boards(b1="free", b2="free")
    assert due({11733: ZERO, 11734: ZERO}, bd) == [11733, 11734]
    assert due({11733: {**ZERO, "hr0_s": 599}}, bd) == []                         # 10분이 안 됐다
    assert due({11733: {**ZERO, "hr": 61}}, bd) == []                             # 심박이 있다
    assert due({11733: {**ZERO, "age": 30}}, bd) == []                            # 라이브가 끊겼다 (깨울 주소를 믿을 수 없다)
    assert due({11733: {**ZERO, "waiting": True}}, bd) == []                      # 대기 모드 — 대기 셀 복귀의 일
    assert due({11733: ZERO}, boards(b1="joined")) == []                          # 다른 기기가 붙어 있다 — 보내도 소용없다
    assert due({11733: ZERO}, boards(b1="off")) == []                             # 보드 꺼짐 — 사람이 할 일
    assert due({11733: ZERO}, None) == []                                         # 보드 기록 없음 — 모르면 보내지 않는다
    assert due({11733: ZERO}, {**bd, "t": T - 999}) == []                         # 보드 기록이 오래됨
    assert due({11733: ZERO}, bd, phase="EXTRACT") == []                          # 추출 중
    assert due({11733: ZERO}, bd, busy=True) == []                                # 다른 세트가 추출 문을 잡고 있다
    assert due({11733: ZERO}, bd, th={**TH, "hr_repair": False}) == []            # 설정으로 끔
    assert due({11733: ZERO}, bd, last={11733: T - 1799}) == []                   # 30분에 한 번
    assert due({11733: ZERO}, bd, last={11733: T - 1800}) == [11733]


def test_ble_ack_shape():
    assert P.ble_id_ok(b"FITO\x20\x01\x01\x54") is True
    assert not P.ble_id_ok(b"FITO\x20\x00") and not P.ble_id_ok(b"") and not P.ble_id_ok(b"FITO\x11\x01")
    assert EVENT_LIGHT["hr_repair"] == "yellow" and EVENT_LIGHT["hr_repair_fail"] == "yellow"


# ---------- 셀 통신 (이 PC 안의 소켓 짝) ----------

def _pkt(serial):
    import struct
    d = bytearray(P.LIVE_LEN); d[:4] = P.HEADER; d[4] = P.MSG_LIVE
    struct.pack_into("<H", d, 11, serial); d[52] = 80
    return bytes(d)


class BleCell(threading.Thread):
    """셀 흉내 — 깨우기를 받으면 붙어 인사하고, 0x20 이면 이름을 남기고 ack 를 돌려준다. 0x26 이면 끝."""

    def __init__(self, tcp_port, ack=b"FITO\x20\x01\x01\x54"):
        super().__init__(daemon=True)
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.udp.bind(("127.0.0.1", 0)); self.udp.settimeout(3)
        self.wake_port, self.tcp_port, self.ack = self.udp.getsockname()[1], tcp_port, ack
        self.seen, self.name = [], None

    def run(self):
        try:
            self.udp.recvfrom(64)
        except socket.timeout:
            return
        finally:
            self.udp.close()
        c = socket.create_connection(("127.0.0.1", self.tcp_port), timeout=10)
        c.sendall(b"\x00" * 46)
        while True:
            got = b""
            while len(got) < 54:
                x = c.recv(54 - len(got))
                if not x:
                    break
                got += x
            if len(got) < 54:
                break
            self.seen.append(got[4])
            if got[4] == P.MSG_SET_BLE_ID:
                self.name = got[8:32].rstrip(b"\0").decode()
                if self.ack:
                    c.sendall(self.ack)
            if got[4] == P.MSG_RESET_NORMAL:
                break
        c.close()


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def run_ble(ack=b"FITO\x20\x01\x01\x54"):
    L = LiveListener.__new__(LiveListener)
    L.cells, L.ip_owner, L._lock, L.ip_changes = {}, {}, threading.Lock(), 0
    L._handle(_pkt(11733), "127.0.0.1", time.time())
    port = _port()
    cell = BleCell(port, ack); cell.start()
    link = CellLink(Config(pc_ip="127.0.0.1", port=port, wake_port=cell.wake_port, resume_timeout_s=0.3), L, log=lambda m: None)
    res = link.set_ble_id({11733: "HRSIM-4CF0"})[11733]
    cell.join(5)
    return res, cell


def test_set_ble_id_sends_name_then_resets():
    res, cell = run_ble()
    assert cell.seen == [P.MSG_SET_BLE_ID, P.MSG_RESET_NORMAL] and cell.name == "HRSIM-4CF0"   # 상태·추출·삭제 없이
    assert res.ble_ok and res.error is None and res.identity is None


def test_set_ble_id_without_ack_still_resets_and_says_so():
    res, cell = run_ble(ack=None)
    assert cell.seen == [P.MSG_SET_BLE_ID, P.MSG_RESET_NORMAL]
    assert not res.ble_ok and res.error.startswith("이름(0x20) 응답 없음")


# ---------- 엔진 배선 ----------

class FakeLink:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def set_ble_id(self, names):
        self.calls.append(dict(names))
        return {s: CellResult(s, "?", ble_ok=self.ok, resume_s=20.0, error=None if self.ok else "이름(0x20) 응답 없음") for s in names}


def mk(tmp_path, link, cells, pairs=None, **kw):
    s1 = {"id": 1, "plug_mac": "20:E1:5D:E6:9C:77", "serials": "11733-11756",
          "hr_pairs": {str(k): v for k, v in (PAIRS if pairs is None else pairs).items()}}
    cfg = Config(sets=[s1], data_dir=str(tmp_path), serials=[11733, 11734], wifi_reconnect=False, **kw)
    live = SimpleNamespace(snapshot=lambda: dict(cells), ip_changes=0)
    r = CycleRunner(cfg, live, SimpleNamespace(metrics=lambda t: {}), link, Recorder(tmp_path))
    r._disk_free = lambda: 500 * 1024 ** 3
    return r


def write_boards(tmp_path, **st):
    now = time.time()
    b = {f"HRSIM-000{n[-1]}": {"alive": s != "off", "conns": 0 if s == "free" else 1, "t_seen": now, "port": "COM1"} for n, s in st.items()}
    (tmp_path / hr.BOARDS_FILE).write_text(json.dumps({"t": now, "boards": b}), encoding="utf-8")


def silent_cells(hr_=0):
    now = time.time()
    return {11733: CellLive("10.0.0.1", 80, hr_, 4, -10, now, t_up=now - 3600, t_hr=now - 700 if hr_ == 0 else now),
            11734: CellLive("10.0.0.2", 80, 70, 4, -10, now, t_up=now - 3600, t_hr=now)}


def kinds(tmp_path):
    import csv
    with open(tmp_path / "events.csv", encoding="utf-8-sig", newline="") as f:
        return [(e["kind"], e["serial"]) for e in csv.DictReader(f)]


def test_engine_repairs_once_per_interval_and_warns_after_three(tmp_path):
    link = FakeLink(ok=True)
    cells = silent_cells()
    r = mk(tmp_path, link, cells)
    write_boards(tmp_path, b1="free", b2="joined")
    st = CycleState(cycle=1); r._phase(st, "DISCHARGE")
    r._watch_hr(st); r._watch_hr(st)
    assert link.calls == [{11733: "HRSIM-0001"}]                                  # 30분에 한 번
    assert kinds(tmp_path) == [("hr_repair", "11733")]
    for _ in range(3):                                                            # 심박이 돌아오지 않은 채 시간이 흐른다
        r._hr_last_try[11733] -= 1801
        r._watch_hr(st)
    assert len(link.calls) == 4
    assert [k for k, _ in kinds(tmp_path)] == ["hr_repair"] * 3 + ["hr_repair_fail", "hr_repair"]   # 3번 보내고도 → 경고 한 번
    r._publish(st)
    assert json.loads((tmp_path / "now.json").read_text(encoding="utf-8"))["metrics"]["hr_repair_fail"] == [11733]
    cells.update(silent_cells(hr_=61))                                            # 심박이 돌아오면 풀린다
    r._watch_hr(st)
    assert r._hr_tries == {} and r._hr_fail_told == set()


def test_engine_does_not_repair_in_extract_with_gate_held_disabled_or_unpaired(tmp_path):
    for case in ("extract", "gate", "off", "nopairs", "joined"):
        d = tmp_path / case; d.mkdir()
        link = FakeLink()
        r = mk(d, link, silent_cells(), pairs={} if case == "nopairs" else None,
               health={"hr_repair": False} if case == "off" else {})
        write_boards(d, b1="joined" if case == "joined" else "free")
        st = CycleState(cycle=1); r._phase(st, "EXTRACT" if case == "extract" else "DISCHARGE")
        if case == "gate":
            r.gate = SimpleNamespace(holder=SimpleNamespace(owner="2"))
        r._watch_hr(st)
        assert link.calls == [], case


def test_engine_takes_and_releases_the_extract_gate(tmp_path):
    from cellbench.shared import ExtractGate
    link = FakeLink()
    r = mk(tmp_path, link, silent_cells())
    write_boards(tmp_path, b1="free")
    r.gate = ExtractGate()
    st = CycleState(cycle=1); r._phase(st, "CHARGE")
    r._watch_hr(st)
    assert link.calls == [{11733: "HRSIM-0001"}] and r.gate.holder is None         # 보낸 뒤 문을 돌려준다


# ---------- 사이클 기록: 셀별 심박 수신 비율 (hr_####.csv) ----------

def test_discharge_samples_tally_into_hr_file(tmp_path):
    cells = silent_cells()
    r = mk(tmp_path, FakeLink(), cells)
    st = CycleState(cycle=7); r._phase(st, "DISCHARGE")
    now = time.time()
    r._hr_tally(st, now); r._hr_tally(st, now)
    cells[11733] = CellLive("10.0.0.1", 80, 0, 4, -10, now - 60)                  # 라이브가 끊긴 표본
    r._hr_tally(st, now)
    r._write_hr_ratio(st)
    import csv
    with open(tmp_path / "hr_0007.csv", encoding="utf-8-sig", newline="") as f:
        rows = {int(x["serial"]): x for x in csv.DictReader(f)}
    assert rows[11733] == {"cycle": "7", "serial": "11733", "board": "HRSIM-0001", "samples": "3", "live": "2", "hr_ok": "0", "hr_ratio": "0.000"}
    assert rows[11734]["hr_ok"] == "3" and rows[11734]["hr_ratio"] == "1.000"


def test_full_cycle_with_pairs_writes_hr_file_and_times(tmp_path, monkeypatch):
    """진짜 run_cycle.main 을 가짜 세상에서 한 사이클 — 짝 표가 있으면 hr_0001.csv 가 생기고 now.json 셀 칸에 심박 시간이 붙는다.
    (짝 표가 없는 세트의 기록이 그대로인 것은 골든 검사 test_golden_single 이 본다.)"""
    import sim_world as sw
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1)])
    pairs = {str(s): {"board": f"HRSIM-{i:04X}", "no": i + 1, "hub": "A", "port": i + 1} for i, s in enumerate(sw.SET1)}
    bench = {"bench_id": "hq-bench-1", "sets": [{**sw.configmod.DEFAULT_SET, "hr_pairs": pairs}]}
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"], bench=bench)
    data = tmp_path / "data"
    assert code == 0, code
    lines = (data / "hr_0001.csv").read_text(encoding="utf-8-sig").strip().splitlines()
    assert lines[0] == "cycle,serial,board,samples,live,hr_ok,hr_ratio" and len(lines) == 1 + len(sw.SET1)
    assert all(l.endswith(",0.000") for l in lines[1:])                         # 가짜 세상의 셀은 심박 0
    now = json.loads((data / "now.json").read_text(encoding="utf-8"))
    assert all("hr0_s" in c for c in now["cells"]) and now["metrics"]["hr_repair_fail"] == []


def test_manual_tool_pair_one_matches_engine_sequence():
    """tools/hr_pair.py --go (옛 .claude/state/pair_cells.py 를 옮긴 것) — 깨우기 → 그 주소 접속 → 0x20 → 응답 → 0x26."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import hr_pair
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(2)
    cell = BleCell(srv.getsockname()[1]); cell.start()
    try:
        res = hr_pair.pair_one(Config(pc_ip="127.0.0.1", wake_port=cell.wake_port), srv, "127.0.0.1", "HRSIM-4CF0", say_=lambda *a: None)
    finally:
        srv.close()
    cell.join(5)
    assert res == ("ok", "46 49 54 4f 20 01 01 54") and cell.seen == [P.MSG_SET_BLE_ID, P.MSG_RESET_NORMAL] and cell.name == "HRSIM-4CF0"
