"""심박(블루투스) 연결 — 짝 표 · 보드 상태 줄 · 보드 읽기 · 신호등 '심박' 차선 · 엔진의 심박 시간 (cellbench/hr.py). 장비 없이 돈다.

배경(2026-10-15 실측): 세트 1 의 셀 24대가 심박대 흉내 보드 24개(허브 A 16 · B 8)에 짝지어 있다. 19:57 셀 11746 의 심박이 0 이 되고 짝 보드의
연결이 0 이 됐는데 아무 데도 보이지 않았다 — 이름(0x20)을 다시 보내 35초 뒤 돌아왔다. 그래서 (1) 감시자가 보드 상태 줄을 읽고,
(2) 신호등이 끊긴 셀과 원인을 가르고, (3) 결과판이 세트·셀·상세에 보인다.
"""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench import health as H
from cellbench import hr
from cellbench.cells import LIVE_RUN_GAP_S, CellLive, LiveListener
from cellbench.config import HR, Config, set_hr_pairs, validate

ROOT = Path(__file__).resolve().parents[1]
T = 1_800_000_000.0
TH = dict(HR)


def pairs24():
    return {str(11732 + n): {"board": f"HRSIM-{n:04X}", "no": n, "hub": "A" if n <= 16 else "B", "port": n if n <= 16 else n - 16}
            for n in range(1, 25)}


def cfg_with(pairs=None, **kw):
    s1 = {"id": 1, "plug_mac": "20:E1:5D:E6:9C:77", "serials": "11733-11756"}
    if pairs is not None:
        s1["hr_pairs"] = pairs
    return Config(sets=[s1], **kw)


# ---------- 짝 표 ----------

def test_pairs_of_reads_objects_and_plain_names_and_skips_bad():
    e = {"hr_pairs": {"11733": {"board": "HRSIM-4CF0", "no": 1, "hub": "A", "port": 1}, "11734": "HRSIM-6D54",
                      "x": "HRSIM-0001", "11735": {"board": "hrsim-1"}}}
    p = hr.pairs_of(e)
    assert p == {11733: {"board": "HRSIM-4CF0", "no": 1, "hub": "A", "port": 1},
                 11734: {"board": "HRSIM-6D54", "no": None, "hub": "", "port": None}}
    assert hr.pairs_of({"id": 2}) == {} and hr.pairs_of(None) == {}


def test_validate_accepts_full_table_and_rejects_mistakes():
    assert validate(cfg_with(pairs24())) == []
    assert validate(cfg_with()) == []                                  # 짝 표가 없으면 아무것도 보지 않는다
    bad = pairs24()
    bad["11733"]["board"] = "HRSIM-0002"                               # 보드 하나가 두 셀에
    bad["11734"]["port"] = 1                                           # 허브 A 포트 1 두 번
    bad["11999"] = {"board": "HRSIM-AAAA"}                             # 이 세트의 셀이 아니다
    bad["11740"] = {"board": "HRSIM-12"}                               # 이름 형식
    probs = " / ".join(validate(cfg_with(bad)))
    assert "HRSIM-0002 가 두 셀에" in probs and "허브 A 포트 1 가 두 번" in probs
    assert "셀 11999 은 이 세트의 셀이 아니다" in probs and "'HRSIM-12'" in probs
    assert validate(cfg_with([1, 2])) and "객체여야" in validate(cfg_with([1, 2]))[0]


def test_example_bench_file_is_the_real_table():
    cfg = Config.load(None, bench_path=ROOT / "bench.example.json")
    p = hr.pairs_of(cfg.sets[0])
    assert validate(cfg) == [] and len(p) == 24
    assert p[11733]["board"] == "HRSIM-4CF0" and p[11756] == {"board": "HRSIM-B020", "no": 24, "hub": "B", "port": 8}


def test_set_hr_pairs_writes_only_that_set_and_validates(tmp_path):
    b = tmp_path / "bench.json"
    b.write_text(json.dumps({"bench_id": "x", "sets": [{"id": 1, "plug_mac": "20:E1:5D:E6:9C:77", "serials": "11733-11756"},
                                                       {"id": 2, "plug_mac": "AA:BB:CC:DD:EE:02", "serials": "11594-11617", "run": True}]}),
                 encoding="utf-8")
    set_hr_pairs(1, pairs24(), b)
    d = json.loads(b.read_text(encoding="utf-8"))
    assert d["bench_id"] == "x" and len(d["sets"][0]["hr_pairs"]) == 24 and d["sets"][1] == {
        "id": 2, "plug_mac": "AA:BB:CC:DD:EE:02", "serials": "11594-11617", "run": True}
    with pytest.raises(ValueError):
        set_hr_pairs(1, {"11733": "HRSIM-XYZ"}, b)                     # 문제가 있으면 쓰지 않는다
    assert len(json.loads(b.read_text(encoding="utf-8"))["sets"][0]["hr_pairs"]) == 24
    set_hr_pairs(1, None, b)
    assert "hr_pairs" not in json.loads(b.read_text(encoding="utf-8"))["sets"][0]


def test_import_csv_maps_board_no_to_first_serial(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import hr_pair
    f = tmp_path / "map.csv"
    f.write_text("no,hub,port,name,note\n1,A,1,HRSIM-4CF0,\n17,B,1,hrsim-b070,메모\n", encoding="utf-8")
    assert hr_pair.pairs_from_csv(f, 11733) == {"11733": {"board": "HRSIM-4CF0", "no": 1, "hub": "A", "port": 1},
                                                "11749": {"board": "HRSIM-B070", "no": 17, "hub": "B", "port": 1}}


# ---------- 상태 줄 · 이름 ----------

def test_status_line_parse():
    assert hr.parse_line("HRSIM name=HRSIM-4CF0 bpm=60 conns=1\r") == {"name": "HRSIM-4CF0", "bpm": 60, "conns": 1}
    assert hr.parse_line("noise HRSIM name=HRSIM-a1b2 bpm=155 conns=0") == {"name": "HRSIM-A1B2", "bpm": 155, "conns": 0}
    for x in ("HRSIM boot name=HRSIM-4CF0 base_mac=E8:3D", "HRSIM connect handle=1 conns=1", "", None):
        assert hr.parse_line(x) is None


def test_name_rule_is_the_flash_script_one():
    assert hr.name_for_mac("E8:3D:C1:93:4C:F0") == "HRSIM-4CF0"
    with pytest.raises(ValueError):
        hr.name_for_mac("COM5")


class FakeSerial:
    """pyserial 흉내 — 열 때 DTR · RTS 가 내려가 있었는지 남긴다."""
    opened = []

    def __init__(self):
        self.dtr = self.rts = True
        self.chunks = [b"PS name=HRSIM-4CF0 bp", b"m=60 conns=0\r\nHRSIM name=HRSIM-4CF0 bpm=61 conns=1\r\n"]

    def open(self):
        FakeSerial.opened.append((self.port, self.dtr, self.rts))

    def read(self, n):
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        pass


def test_read_board_opens_without_reset_lines_and_skips_partial_line(monkeypatch):
    serial = pytest.importorskip("serial")
    monkeypatch.setattr(serial, "Serial", FakeSerial)
    FakeSerial.opened = []
    r = hr.read_board("COM9", 1.0)
    assert FakeSerial.opened == [("COM9", False, False)]               # 여는 순간 리셋 선을 올리지 않는다
    assert r == {"name": "HRSIM-4CF0", "bpm": 61, "conns": 1}          # 첫 조각은 줄 중간이라 버린다


def test_scan_marks_alive_unread_missing_and_keeps_last_seen():
    names = {"HRSIM-4CF0", "HRSIM-6D54", "HRSIM-7030"}
    ports = {"E8:3D:C1:93:4C:F0": "COM4", "E8:3D:C1:94:6D:54": "COM5", "48:F6:EE:15:71:80": "COM3"}   # 7030 은 USB 에 없다 · 7180 은 짝 밖
    seen = []

    def reader(port, t):
        seen.append(port)
        if port == "COM5":
            raise OSError("access denied")
        return {"name": "HRSIM-4CF0", "bpm": 60, "conns": 1}

    prev = {"t": T - 60, "boards": {"HRSIM-6D54": {"t_seen": T - 60, "conns": 0, "bpm": 99}, "HRSIM-7030": {"mac": "AA", "t_seen": T - 900}}}
    doc = hr.scan(names, prev, T, 1.0, ports=ports, reader=reader)
    b = doc["boards"]
    assert sorted(seen) == ["COM4", "COM5"]                            # 짝 표에 없는 보드(COM3)는 열지 않는다
    assert b["HRSIM-4CF0"] == {"mac": "E8:3D:C1:93:4C:F0", "port": "COM4", "t_seen": T, "bpm": 60, "conns": 1, "alive": True}
    assert b["HRSIM-6D54"]["alive"] is False and b["HRSIM-6D54"]["t_seen"] == T - 60 and "access denied" in b["HRSIM-6D54"]["error"]
    assert b["HRSIM-7030"] == {"mac": "AA", "port": None, "t_seen": T - 900, "bpm": None, "conns": None, "alive": False,
                               "error": "USB 장치 없음"}


def test_board_watch_writes_file_only_with_pairs(tmp_path):
    calls = []

    def scanner(names, prev, now, t):
        calls.append(sorted(names))
        return {"t": now, "boards": {n: {"alive": True, "conns": 1, "t_seen": now} for n in names}}

    w = hr.BoardWatch(tmp_path, lambda: cfg_with(), scanner=scanner, clock=lambda: T)
    assert w.once() == HR["hr_board_every_s"] and calls == [] and not (tmp_path / hr.BOARDS_FILE).exists()
    logs = []
    w = hr.BoardWatch(tmp_path, lambda: cfg_with({"11733": "HRSIM-4CF0"}), logs.append, scanner=scanner, clock=lambda: T)
    w.once()
    assert calls == [["HRSIM-4CF0"]] and json.loads((tmp_path / hr.BOARDS_FILE).read_text(encoding="utf-8"))["t"] == T
    assert logs == ["심박 보드 1/1 응답"]
    w.once()
    assert logs == ["심박 보드 1/1 응답"]                             # 같으면 다시 남기지 않는다


# ---------- 라이브 수신기의 심박 시간 ----------

def _pkt(serial, hr_=0):
    import struct
    from cellbench import protocol as P
    d = bytearray(P.LIVE_LEN); d[:4] = P.HEADER; d[4] = P.MSG_LIVE
    struct.pack_into("<H", d, 11, serial); d[51] = hr_; d[52] = 80
    return bytes(d)


def test_listener_tracks_live_run_start_and_last_heart_rate():
    import threading
    L = LiveListener.__new__(LiveListener)
    L.cells, L.ip_owner, L._lock, L.ip_changes = {}, {}, threading.Lock(), 0
    L._handle(_pkt(1, 70), "10.0.0.1", 100.0)
    L._handle(_pkt(1, 0), "10.0.0.1", 100.5)
    L._handle(_pkt(1, 0), "10.0.0.1", 101.0)
    c = L.cells[1]
    assert c.t_up == 100.0 and c.t_hr == 100.0
    assert hr.times(c, 300.0) == (200.0, 200.0)                        # 라이브 200초째 · 심박 0 은 마지막 심박 뒤로 200초
    L._handle(_pkt(1, 0), "10.0.0.1", 101.0 + LIVE_RUN_GAP_S + 20)    # 재부팅 — 25초 끊겼다 다시
    c = L.cells[1]
    assert c.t_up == 126.0 and hr.times(c, 136.0) == (10.0, 10.0)      # 재부팅 전의 0 과 이어 붙이지 않는다
    L._handle(_pkt(1, 80), "10.0.0.1", 127.0)
    assert hr.times(L.cells[1], 130.0)[1] == 0.0


# ---------- 판정 ----------

def boards_doc(**over):
    b = {f"HRSIM-{n:04X}": {"alive": True, "conns": 1, "t_seen": T - 10, "port": f"COM{n}", "mac": "x"} for n in range(1, 25)}
    for k, v in over.items():
        b[k.replace("_", "-")] = {**b[k.replace("_", "-")], **v}
    return {"t": T - 10, "boards": b}


def now_doc(phase="DISCHARGE", **cells):
    out = []
    for n in range(1, 25):
        s = 11732 + n
        c = {"serial": s, "hr": 70, "age": 0.3, "waiting": False, "up_s": 9000, "hr0_s": 0}
        c.update(cells.get(f"c{s}", {}))
        out.append(c)
    return {"t": T, "beat": T, "phase": phase, "phase_since": T - 600, "cells": out, "expected": 24, "metrics": {}}


def judge(now, boards, th=TH):
    return hr.judge(hr.pairs_of({"hr_pairs": pairs24()}), now, boards, T, th, 15.0)


def test_judge_all_ok():
    j = judge(now_doc(), boards_doc())
    assert j["ok"] == 24 and j["paired"] == 24 and j["lost"] == [] and j["timed"] and not j["extract"]


def test_judge_causes_by_board_state():
    n = now_doc(c11733={"hr": 0, "hr0_s": 200}, c11734={"hr": 0, "hr0_s": 200}, c11735={"hr": 0, "hr0_s": 200},
                c11736={"hr": 0, "hr0_s": 200}, c11737={"hr": 0, "hr0_s": 200})
    b = boards_doc(HRSIM_0001={"alive": False, "port": None},          # 보드 꺼짐
                   HRSIM_0002={"conns": 0},                            # 셀이 안 붙음
                   HRSIM_0003={"conns": 2},                            # 다른 기기
                   HRSIM_0004={"alive": False, "port": "COM4"},        # USB 는 있는데 응답 없음
                   HRSIM_0005={"t_seen": T - 1000, "port": None})      # 오래 안 봄 · USB 없음 → 꺼짐
    j = judge(n, b)
    by = {x["serial"]: x for x in j["lost"]}
    assert {s: x["cause"] for s, x in by.items()} == {11733: "off", 11734: "unjoined", 11735: "other", 11736: "unread", 11737: "off"}
    assert by[11734]["why"] == hr.CAUSE["unjoined"] and by[11734]["conns"] == 0 and by[11734]["hub"] == "A" and by[11734]["port"] == 2
    assert j["ok"] == 19
    j = judge(n, {"t": T - 999, "boards": b["boards"]})                # 보드 기록이 오래됨 → 모름
    assert {x["cause"] for x in j["lost"]} == {"unknown"}
    assert {x["cause"] for x in judge(n, None)["lost"]} == {"unknown"}


def test_judge_waits_short_zero_rejoin_extract_and_old_engine():
    n = now_doc(c11733={"hr": 0, "hr0_s": 179}, c11734={"hr": 0, "hr0_s": 400, "up_s": 60},
                c11735={"hr": 0, "age": 30}, c11736={"hr": 0, "waiting": True, "hr0_s": 999})
    j = judge(n, boards_doc())
    assert j["lost"] == [] and j["wait"] == [11733, 11734] and j["skip"] == [11735, 11736]   # 끊긴 라이브는 '셀 수신' 차선의 일
    j = judge(now_doc("EXTRACT", c11733={"hr": 0, "hr0_s": 999}), boards_doc())
    assert j["extract"] and j["lost"] == [] and j["wait"] == [11733]
    old = now_doc(c11733={"hr": 0})
    for c in old["cells"]:
        c.pop("hr0_s"); c.pop("up_s")
    j = judge(old, boards_doc())
    assert not j["timed"] and j["lost"] == [] and j["wait"] == [11733]       # 옛 엔진은 시간을 안 적는다 — 알리지 않는다


# ---------- 신호등 '심박' 차선 ----------

def lane(h, k="hr"):
    return next(l for l in h["lanes"] if l["key"] == k)


def compute(cfg, now, boards=None, **kw):
    return H.compute(now, {"t": T, "slack": True}, None, None, cfg, T, sets_data={"running": [1], "nows": {}, "cycles": {},
                                                                                 "info": {}, "hr_boards": boards}, **kw)


def test_lane_off_without_pairs_is_gray_and_quiet():
    h = compute(cfg_with(), now_doc())
    l = lane(h)
    assert l["light"] == "green" and l.get("off") is True and l["reason"] == H.HR_OFF_REASON and "심박 없음" in l["value"]
    assert h["hr"]["sets"] == {} and h["hr"]["hubs"] == {}
    assert "hr" in H.LANE_KEYS and "hr" in H.SET_LANES and "hr" in H.ENGINE_ONLY


def test_lane_yellow_then_red_with_causes_and_board_summary():
    cfg = cfg_with(pairs24())
    h = compute(cfg, now_doc(c11746={"hr": 0, "hr0_s": 400}), boards_doc(HRSIM_000E={"conns": 0}))
    l = lane(h)
    assert l["light"] == "yellow" and l["reason"] == "심박이 180초 넘게 0 인 셀 (짝 보드 있음)" and l["cells"] == [11746]
    assert l["value"] == "심박 23/24 · 끊김 1" and "셀이 안 붙음 1" in l["issues"][0]["short"]
    assert "엔진이 10분 뒤" in l["ai"] and "tools/hr_pair.py" in l["human"]
    assert h["sets"][0]["light"] == "yellow"                           # 세트 불이 따라간다
    assert ("hr", ("yellow", l["reason"])) in H.alert_lights(h).items()   # 알림기(Slack)도 따라간다
    s = h["hr"]["sets"]["1"]
    assert s["ok"] == 23 and s["lost"][0]["board"] == "HRSIM-000E" and s["lost"][0]["cause"] == "unjoined"
    hubs = h["hr"]["hubs"]
    assert hubs["A"]["total"] == 16 and hubs["A"]["alive"] == 16 and hubs["B"]["total"] == 8
    assert hubs["A"]["boards"][0] == {"no": 1, "port": 1, "board": "HRSIM-0001", "serial": 11733, "set": 1, "state": "joined",
                                      "conns": 1, "bpm": None}
    three = {f"c{s}": {"hr": 0, "hr0_s": 400} for s in (11733, 11734, 11735)}
    h = compute(cfg, now_doc(**three), boards_doc(HRSIM_0001={"alive": False, "port": None}))
    assert lane(h)["light"] == "red" and lane(h)["reason"] == "심박 끊긴 셀 3대 이상" and "보드 꺼짐 1" in lane(h)["issues"][0]["short"]
    h = compute(cfg_with(pairs24(), health={"hr_repair": False}), now_doc(c11746={"hr": 0, "hr0_s": 400}), boards_doc())
    assert "꺼져 있다" in lane(h)["ai"]


def test_lane_repair_fail_metric_and_engine_states():
    cfg = cfg_with(pairs24())
    n = now_doc()
    n["metrics"] = {"hr_repair_fail": [11740]}
    l = lane(compute(cfg, n, boards_doc()))
    assert l["light"] == "yellow" and "3번 연속 실패" in l["reason"] and l["cells"] == [11740]
    assert lane(compute(cfg, None))["light"] == "none"                 # 엔진 기록 없음
    stale = now_doc(); stale["beat"] = T - 4000; stale["t"] = T - 4000
    l = lane(compute(cfg, stale))
    assert l["light"] == "unknown" and l["hold"] is True                # 엔진 기록이 멈추면 다른 엔진 차선과 같이


def test_set_materials_reads_board_file(tmp_path):
    (tmp_path / hr.BOARDS_FILE).write_text(json.dumps({"t": T, "boards": {}}), encoding="utf-8")
    assert H.set_materials(tmp_path, cfg_with(), None)["hr_boards"] == {"t": T, "boards": {}}


# ---------- 엔진 now.json ----------

def test_engine_adds_heart_rate_times_only_for_paired_sets(tmp_path):
    from cellbench.cycle import CycleRunner, CycleState
    from cellbench.record import Recorder
    now = time.time()
    cells = {11733: CellLive("10.0.0.1", 80, 0, 4, -10, now, t_up=now - 500, t_hr=now - 300),
             11734: CellLive("10.0.0.2", 80, 75, 4, -10, now, t_up=now - 500, t_hr=now)}
    live = SimpleNamespace(snapshot=lambda: dict(cells), ip_changes=0, wait_for=lambda s, t: [])
    plug = SimpleNamespace(metrics=lambda t: {})
    for pairs, want in ((None, False), ({"11733": "HRSIM-4CF0", "11734": "HRSIM-6D54"}, True)):
        d = tmp_path / str(want)
        cfg = cfg_with(pairs, data_dir=str(d), serials=[11733, 11734], wifi_reconnect=False)
        r = CycleRunner(cfg, live, plug, None, Recorder(d))
        r._disk_free = lambda: 500 * 1024 ** 3
        st = CycleState(cycle=1); r._phase(st, "DISCHARGE")
        doc = json.loads((d / "now.json").read_text(encoding="utf-8"))
        c = {x["serial"]: x for x in doc["cells"]}
        assert ("hr0_s" in c[11733]) is want and ("hr_repair_fail" in doc["metrics"]) is want
        if want:
            assert 299 <= c[11733]["hr0_s"] <= 301 and c[11734]["hr0_s"] == 0 and 499 <= c[11733]["up_s"] <= 501


# ---------- 결과판 (원문 — 화면 동작은 Playwright 로 찍어 확인했다. 이 저장소 CI 에는 브라우저가 없다) ----------

def test_board_draws_heart_rate_from_health_summary():
    src = (ROOT / "board" / "index.html").read_text(encoding="utf-8")
    assert "const hrOf=k=>(HL&&HL.hr&&HL.hr.sets&&HL.hr.sets[String(k)])||null;" in src     # 판정은 서버 — 화면은 HL.hr 만 그린다
    assert "function hrCardLine(k){" in src and "${hrCardLine(s.id)}" in src and "심박 없음</div>" in src   # 세트 카드
    assert "<span class=\"hr off\" title=\"${esc(`심박 끊김 · ${hl[s].why}`)}\">" in src                   # 셀 카드 경고
    assert "['link','짝 보드',hb.board" in src and "['usb','허브 포트'" in src and "['bt','보드 연결'" in src  # 상세 패널
    assert "심박 끊김 ${esc(dur(hlo.zero_s))}" in src
    assert "hr:'heart'" in src and "const lc=l.off?'none':" in src                                         # 점검 — 짝 없음은 회색
