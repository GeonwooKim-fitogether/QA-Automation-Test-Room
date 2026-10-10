"""세트 등록 — 10초 검사 판정 · bench.json 쓰기 · 감지 재료(엔진 heard · 감시자 plugs.json) · 결과판 API.

장비·실제 bench.json·자격 증명 관리자에는 닿지 않는다. 플러그 찾기·읽기와 셀 라이브 듣기는 가짜 장비(FakeDevices)로 바꾸고,
bench.json 은 늘 임시 폴더의 것을 쓴다(서버는 임의 포트의 127.0.0.1).
"""
import asyncio
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cellbench.plug as plug_mod
from cellbench import health as H
from cellbench import register as R
from cellbench import supervisor as sup
from cellbench.cells import CellLive, PortBusy
from cellbench.config import (DEFAULT_SET, Config, add_set, compact_serials, expand_serials, next_set_id,
                              registered_macs, registered_serials, validate)
from cellbench.cycle import CycleRunner, CycleState
from cellbench.plug import PlugReading
from cellbench.record import Recorder

T = 1_800_000_000.0
SET1 = dict(DEFAULT_SET)                                    # 세트 1 — 11733~11756 · 20:E1:5D:E6:9C:77 (운전 중)
MAC2 = "20:E1:5D:E6:97:5B"                                  # 세트 2 실물 플러그 (Tapo P110M 2)
SER2 = list(range(11594, 11618))                            # 세트 2 실물 셀 24대


def heard_rows(serials=SER2, age=1.0, battery=99, rssi=-58):
    return [{"serial": s, "ip": f"192.168.1.{s % 200}", "battery": battery, "rssi": rssi, "age": age} for s in serials]


class FakeDevices:
    """가짜 장비 — 무엇이 불렸는지 calls 에 남긴다."""

    def __init__(self, plugs=None, reading=(True, 19.9), plug_err=None, listen=None, listen_err=None, scan_err=None):
        self.plugs = plugs if plugs is not None else [{"mac": MAC2, "ip": "192.168.1.104", "alias": "Tapo P110M 2", "on": True, "watts": 19.9, "set": None}]
        self.reading, self.plug_err, self.listen_err, self.scan_err = reading, plug_err, listen_err, scan_err
        self.heard = listen if listen is not None else {r["serial"]: r for r in heard_rows()}
        self.calls = []

    def scan_plugs(self, cfg):
        self.calls.append("scan")
        if self.scan_err:
            raise self.scan_err
        return [dict(p) for p in self.plugs]

    def read_plug(self, cfg, mac, ip):
        self.calls.append(("read", mac, ip))
        if self.plug_err:
            raise self.plug_err
        return PlugReading(self.reading[0], self.reading[1], time.time()), 0.4

    def listen(self, cfg, seconds):
        self.calls.append(("listen", seconds))
        if self.listen_err:
            raise self.listen_err
        return dict(self.heard)


def bench_file(tmp_path, obj=None) -> Path:
    p = tmp_path / "bench.json"
    p.write_text(json.dumps(obj if obj is not None else {"bench_id": "hq-bench-1", "bench_name": "본사 셀 시험대 1호",
                                                          "health": {"wifi_warn_dbm": -75}, "sets": [SET1]},
                            ensure_ascii=False), encoding="utf-8")
    return p


# ---------- 설정: 범위 표기 · 겹침 · bench.json 쓰기 ----------

def test_compact_serials_round_trips_with_expand():
    assert compact_serials(SER2) == "11594-11617"
    assert compact_serials([7, 1, 2, 3, 3]) == "1-3, 7"
    assert expand_serials(compact_serials([5, 9, 10, 11])) == [5, 9, 10, 11]


def test_validate_rejects_one_plug_in_two_sets():
    """같은 플러그 MAC 이 두 세트에 있으면 한 세트를 끄려다 다른 세트 Dock 도 끊는다 (FMEA 8.5)."""
    cfg = Config(sets=[SET1, {"id": 2, "plug_mac": SET1["plug_mac"].lower(), "serials": "11594-11617"}])
    assert any("플러그 MAC 이 같다" in p for p in validate(cfg))
    assert validate(Config(sets=[SET1, {"id": 2, "plug_mac": MAC2, "serials": "11594-11617"}])) == []


def test_registered_lookups_and_next_id():
    cfg = Config(sets=[SET1, {"id": 2, "plug_mac": MAC2, "serials": "11594-11617"}, {"id": 3, "serials": "말이 안 됨"}])
    reg = registered_serials(cfg)
    assert reg[11733] == 1 and reg[11600] == 2 and len(reg) == 48          # 읽을 수 없는 세트 항목은 건너뛴다 (예외 없음)
    assert registered_macs(cfg) == {SET1["plug_mac"]: 1, MAC2: 2}
    assert next_set_id(cfg.sets) == 4 and next_set_id([]) == 1


def test_add_set_appends_keeps_other_keys_and_set1(tmp_path):
    p = bench_file(tmp_path)
    before = json.loads(p.read_text(encoding="utf-8"))
    sets = add_set({"id": 2, "plug_mac": MAC2, "serials": "11594-11617"}, p)
    after = json.loads(p.read_text(encoding="utf-8"))
    assert {k: v for k, v in after.items() if k != "sets"} == {k: v for k, v in before.items() if k != "sets"}
    assert after["sets"][0] == before["sets"][0] == SET1 and after["sets"][1]["id"] == 2 and sets == after["sets"]
    cfg = Config.load(None, bench_path=p)
    assert cfg.serials == list(range(11733, 11757)) and cfg.plug_mac == SET1["plug_mac"]      # 엔진은 여전히 세트 1
    assert not (tmp_path / "bench.json.tmp").exists()


def test_add_set_without_sets_in_bench_keeps_running_set_first(tmp_path):
    """bench.json 에 sets 가 없으면 새 세트가 sets[0] 이 되면 안 된다 — 그러면 엔진이 그 세트로 바뀐다."""
    p = bench_file(tmp_path, {"bench_id": "b-1"})
    add_set({"id": 2, "plug_mac": MAC2, "serials": "11594-11617"}, p)
    cfg = Config.load(None, bench_path=p)
    assert cfg.bench_id == "b-1" and cfg.sets[0] == DEFAULT_SET and cfg.serials[0] == 11733
    missing = tmp_path / "새" / "bench.json"
    missing.parent.mkdir()
    add_set({"id": 2, "plug_mac": MAC2, "serials": "11594-11617"}, missing)              # 파일이 없어도 같은 규칙
    assert [s["id"] for s in json.loads(missing.read_text(encoding="utf-8"))["sets"]] == [1, 2]


def test_add_set_refuses_overlap_and_leaves_file_untouched(tmp_path):
    p = bench_file(tmp_path)
    raw = p.read_bytes()
    with pytest.raises(ValueError, match="겹친다"):
        add_set({"id": 2, "plug_mac": MAC2, "serials": "11750-11773"}, p)
    with pytest.raises(ValueError, match="플러그 MAC 이 같다"):
        add_set({"id": 2, "plug_mac": SET1["plug_mac"], "serials": "11594-11617"}, p)
    assert p.read_bytes() == raw and not (tmp_path / "bench.json.tmp").exists()


def test_add_set_reads_bom(tmp_path):
    p = tmp_path / "bench.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"sets": [SET1]}).encode())
    add_set({"id": 2, "plug_mac": MAC2, "serials": "11594-11617"}, p)
    assert len(Config.load(None, bench_path=p).sets) == 2


# ---------- 요청 해석 ----------

@pytest.mark.parametrize("body,err", [
    ({"plug_mac": "20-E1-5D", "serials": SER2}, "MAC"),
    ({"plug_mac": MAC2, "serials": SER2[:23]}, "정확히 24대"),
    ({"plug_mac": MAC2, "serials": SER2[:23] + [SER2[0]]}, "중복"),
    ({"plug_mac": MAC2, "serials": [True] * 24}, "숫자"),
    ({"plug_mac": MAC2, "serials": ["abc"] * 24}, "읽을 수 없다"),
    ({"plug_mac": MAC2}, "정확히 24대"),
])
def test_parse_request_rejects_before_touching_devices(body, err):
    with pytest.raises(ValueError, match=err):
        R.parse_request(body, Config())


def test_parse_request_accepts_list_or_range():
    assert R.parse_request({"plug_mac": MAC2.lower(), "serials": list(reversed(SER2))}, Config()) == (MAC2, SER2)
    assert R.parse_request({"plug_mac": MAC2, "serials": "11594-11617"}, Config())[1] == SER2


# ---------- 감지 재료 ----------

def test_heard_from_now_reasons_and_ages():
    cfg = Config()
    assert R.heard_from_now({"t": T}, cfg, T, False)[0] is None
    lst, why = R.heard_from_now({"t": T, "phase": "DISCHARGE"}, cfg, T, True)
    assert lst is None and "새 코드" in why                                  # 옛 엔진은 heard 를 쓰지 않는다
    now = {"t": T - 10, "heard": heard_rows([11594], age=5) + heard_rows([11595], age=25) + heard_rows([11733], age=1)}
    lst, why = R.heard_from_now(now, cfg, T, True)
    assert [c["serial"] for c in lst] == [11594] and lst[0]["age"] == 15.0  # 나이 + 그 뒤 흐른 10초 · 30초 넘은 것 · 등록된 것 빠짐


def test_plug_rows_unregistered_first_and_never_contacts_registered():
    cfg = Config(sets=[SET1])
    doc = {"t": T, "plugs": [{"mac": SET1["plug_mac"], "ip": "192.168.1.103", "alias": None, "on": None, "watts": None, "set": 1},
                             {"mac": MAC2.lower(), "ip": "192.168.1.104", "alias": "Tapo P110M 2", "on": True, "watts": 19.9}]}
    rows = R.plug_rows(cfg, doc, {"plug": {"on": True, "w": 34.2}}, True)
    assert [(r["mac"], r["set"]) for r in rows] == [(MAC2, None), (SET1["plug_mac"], 1)]
    assert rows[1]["on"] is True and rows[1]["watts"] == 34.2 and rows[1]["ip"] == "192.168.1.103"   # 세트 1 상태는 엔진의 값
    assert R.plug_rows(cfg, None, None, False)[0]["on"] is None


# ---------- 검사 넷 (순수) ----------

def test_cells_item_boundary_and_message():
    cfg = Config()
    heard = {r["serial"]: r for r in heard_rows(age=15.0)}
    assert R.cells_item(SER2, heard, cfg)["state"] == "ok" and "평균 -58 dBm" in R.cells_item(SER2, heard, cfg)["detail"]
    heard[11601]["age"] = 15.1
    del heard[11605]
    it = R.cells_item(SER2, heard, cfg)
    assert it["state"] == "bad" and it["detail"] == "22/24 · 11601, 11605 미수신 15초"
    assert R.cells_item(SER2, None, cfg, "포트 사용 중")["detail"] == "포트 사용 중"


def test_overlap_item():
    cfg = Config(sets=[SET1])
    assert R.overlap_item(cfg, MAC2, SER2, 2)["state"] == "ok"
    bad = R.overlap_item(cfg, MAC2, SER2[:23] + [11740], 2)
    assert bad["state"] == "bad" and "세트 1 과 공통 시리얼 1대" in bad["detail"] and "11740" in bad["detail"]
    assert "세트 1 의 것" in R.overlap_item(cfg, SET1["plug_mac"].lower(), SER2, 2)["detail"]
    assert R.overlap_item(Config(sets=[{**SET1, "id": 2}]), MAC2, SER2, 3)["detail"].startswith("세트 2 와 공통 시리얼 0")


@pytest.mark.parametrize("reading,state", [
    ((True, 5.0), "ok"), ((True, 4.9), "warn"), ((True, 120.0), "ok"), ((True, 120.1), "warn"),
    ((True, 1.4), "warn"), ((False, 0.0), "warn"), ((True, float("nan")), "warn"), (None, "warn"),
])
def test_power_item_is_a_draft_and_never_blocks(reading, state):
    r = None if reading is None else PlugReading(reading[0], reading[1], T)
    it = R.power_item(r, Config())
    assert it["state"] == state and it["tag"] == "가안"
    if reading == (True, 1.4):
        assert "운전 안전망(P4)" in it["detail"] and "저장은 막지 않음" in it["detail"]


def test_judge_order_and_power_warning_still_passes():
    ok = R._item("plug", "a", "ok", ""), R._item("cells", "b", "ok", ""), R._item("overlap", "c", "ok", "")
    res = R.judge([R._item("power", "d", "warn", "")] + list(reversed(ok)))
    assert res["ok"] is True and [i["key"] for i in res["checks"]] == list(R.ORDER)
    res = R.judge([ok[0], R._item("cells", "b", "bad", ""), ok[2], R._item("power", "d", "ok", "")])
    assert res["ok"] is False and "미수신" in res["note"]


# ---------- 검사 실행 (가짜 장비) ----------

def test_run_check_engine_live_uses_heard_and_does_not_listen():
    dev = FakeDevices()
    now = {"t": T, "heard": heard_rows()}
    res = R.run_check(Config(), MAC2, SER2, dev, now, True, plug_ip="192.168.1.104", now_t=T)
    assert res["ok"] is True and res["set_id"] == 2 and res["source"] == "engine"
    assert dev.calls == [("read", MAC2, "192.168.1.104")]
    assert [i["state"] for i in res["checks"]] == ["ok", "ok", "ok", "ok"] and res["checks"][0]["detail"] == f"{MAC2} · 0.4초"


def test_run_check_without_engine_listens_and_reports_port_busy():
    dev = FakeDevices()
    res = R.run_check(Config(), MAC2, SER2, dev, None, False)
    assert res["ok"] is True and ("listen", 10.0) in dev.calls and res["source"] == "listen"
    dev = FakeDevices(listen_err=PortBusy("UDP 60222 를 이미 다른 프로그램이 쓰고 있다"))
    res = R.run_check(Config(), MAC2, SER2, dev, None, False)
    cells = res["checks"][1]
    assert res["ok"] is False and cells["state"] == "bad" and "60222" in cells["detail"]


def test_run_check_never_contacts_a_registered_plug():
    dev = FakeDevices()
    res = R.run_check(Config(), SET1["plug_mac"], SER2, dev, {"t": T, "heard": heard_rows()}, True, now_t=T)
    assert res["ok"] is False and not any(c[0] == "read" for c in dev.calls if isinstance(c, tuple))
    assert "접속하지 않음" in res["checks"][0]["detail"] and res["checks"][2]["state"] == "bad"


def test_run_check_plug_failure_blocks_but_power_only_warns():
    dev = FakeDevices(plug_err=RuntimeError("플러그 응답 없음"))
    res = R.run_check(Config(), MAC2, SER2, dev, {"t": T, "heard": heard_rows()}, True, now_t=T)
    assert res["ok"] is False and res["checks"][0]["state"] == "bad" and res["checks"][3]["state"] == "warn"
    dev = FakeDevices(reading=(True, 1.4))                                    # 10-10 세트 2 처럼 Dock 이 멈춰 있어도
    assert R.run_check(Config(), MAC2, SER2, dev, {"t": T, "heard": heard_rows()}, True, now_t=T)["ok"] is True


# ---------- 장비 통로 (plug.py — 가짜 python-kasa 로) ----------

class _Dev:
    def __init__(self, mac, alias):
        self.mac, self.alias, self.is_on, self.updated, self.closed = mac, alias, True, 0, 0
        self.modules = {"Energy": type("E", (), {"current_consumption": 19.94})()}

    async def update(self):
        self.updated += 1

    async def disconnect(self):
        self.closed += 1


def test_discover_plugs_does_not_log_in_to_registered_plug(monkeypatch):
    monkeypatch.setattr(plug_mod.keyring, "get_password", lambda svc, name: "x")     # 실제 자격 증명은 읽지 않는다
    devs = {"192.168.1.103": _Dev(SET1["plug_mac"].replace(":", "-"), None), "192.168.1.104": _Dev(MAC2, "Tapo P110M 2")}
    seen = {}

    async def discover(**kw):
        seen.update(kw)
        return devs
    monkeypatch.setattr(plug_mod.Discover, "discover", staticmethod(discover))
    rows = plug_mod.discover_plugs(Config())
    assert seen["target"] == "192.168.1.255"
    assert devs["192.168.1.103"].updated == 0 and devs["192.168.1.104"].updated == 1     # 세트 1 플러그에는 로그인하지 않는다
    assert [r["mac"] for r in rows] == [MAC2, SET1["plug_mac"]]
    assert rows[0] == {"mac": MAC2, "ip": "192.168.1.104", "alias": "Tapo P110M 2", "on": True, "watts": 19.9, "set": None}
    assert rows[1]["set"] == 1 and rows[1]["alias"] is None
    assert all(d.closed == 1 for d in devs.values())


def test_read_plug_points_a_copy_at_the_new_mac_with_one_try(monkeypatch):
    monkeypatch.setattr(plug_mod.keyring, "get_password", lambda svc, name: "x")
    got = []

    async def do(self, action):
        got.append((self.cfg.plug_mac, self.cfg.plug_ip_hint, self.cfg.plug_retries, self.cfg.plug_call_timeout_s, action))
        return PlugReading(True, 19.9, time.time())
    monkeypatch.setattr(plug_mod.Plug, "_do", do)
    cfg = Config()
    r, secs = plug_mod.read_plug(cfg, MAC2, "192.168.1.104")
    assert r.watts == 19.9 and secs is not None
    assert got == [(MAC2, "192.168.1.104", 1, 8.0, "read")]
    assert cfg.plug_mac == SET1["plug_mac"] and cfg.plug_retries == 3                   # 원래 설정은 그대로


# ---------- 엔진: now.json 의 heard ----------

class _Live:
    def __init__(self, cells):
        self.cells, self.ip_changes = cells, 0

    def snapshot(self):
        return dict(self.cells)


def test_engine_publishes_unregistered_heard_cells(tmp_path):
    now = time.time()
    cells = {1: CellLive("10.0.0.1", 60, 0, 4, -50, now),                     # 운전 중 (serials)
             11733: CellLive("10.0.0.2", 60, 0, 4, -50, now),                 # 세트 1 (등록됨)
             11594: CellLive("10.0.0.3", 99, 0, 4, -55, now - 3),             # 미등록 · 3초 전
             11595: CellLive("10.0.0.4", 99, 0, 4, -55, now - 60)}            # 미등록이지만 30초 넘음
    cfg = Config(data_dir=str(tmp_path), wifi_reconnect=False, serials=[1, 2, 3])
    r = CycleRunner(cfg, _Live(cells), None, None, Recorder(tmp_path))
    r._publish(CycleState(cycle=1))
    heard = json.loads((tmp_path / "now.json").read_text(encoding="utf-8"))["heard"]
    assert [c["serial"] for c in heard] == [11594] and set(heard[0]) == {"serial", "ip", "battery", "rssi", "age"}
    assert 2.5 <= heard[0]["age"] <= 5


# ---------- 신호등: 세트 목록 ----------

def _now(**kw):
    n = {"t": T - 5, "beat": T - 5, "phase": "DISCHARGE", "phase_since": T - 600, "cycle": 1, "expected": 24,
         "cells": [], "plug": {"on": False, "w": 0.0}, "metrics": {}}
    n.update(kw)
    return n


def test_detection_is_a_yellow_set_but_never_raises_the_bench_light():
    cfg = Config(sets=[SET1])
    base = H.compute(_now(), None, None, [], cfg, T)
    plugs = {"t": T - 60, "plugs": [{"mac": MAC2}, {"mac": SET1["plug_mac"]}]}
    h = H.compute(_now(heard=heard_rows()), None, None, [], cfg, T, plugs)
    det = h["sets"][-1]
    assert det == {"id": 2, "label": "", "running": False, "light": "yellow", "word": "등록 필요",
                   "reason": "미등록 감지 · 셀 24 · 플러그 1", "detect": {"cells": 24, "plugs": 1}}
    assert h["light"] == base["light"] and h["lanes"] == base["lanes"]               # 차선 · 전체 불 · Slack 은 그대로


def test_detection_ignores_stale_engine_stale_plugs_and_registered():
    cfg = Config(sets=[SET1, {"id": 2, "plug_mac": MAC2, "serials": "11594-11617"}])
    h = H.compute(_now(heard=heard_rows()), None, None, [], cfg, T, {"t": T, "plugs": [{"mac": MAC2}]})
    assert [s["id"] for s in h["sets"]] == [1, 2] and h["sets"][1]["word"] == "대기"   # 등록 직후 — 엔진이 옛 설정으로 적어도 감지 안 함
    one = Config(sets=[SET1])
    dead = _now(heard=heard_rows(), t=T - 3600, beat=T - 3600)
    assert len(H.compute(dead, None, None, [], one, T)["sets"]) == 1                      # 멈춘 엔진의 heard 는 믿지 않는다
    old = {"t": T - 3 * 600 - 1, "plugs": [{"mac": MAC2}]}
    assert len(H.compute(_now(), None, None, [], one, T, old)["sets"]) == 1                # 30분 넘게 묵은 탐색


# ---------- 감시자: 10분마다 plugs.json ----------

def test_supervisor_scans_plugs_every_ten_minutes_and_survives_failure(tmp_path):
    from test_supervisor import World, eng, now_json, put
    put(tmp_path, eng(exit="done", plug_on=True), now_json(4000, "DONE"), cycles=4)
    w = World()
    n = {"calls": 0, "fail": False}
    base = w.deps

    def deps():
        d = base()

        def scan():
            n["calls"] += 1
            if n["fail"]:
                raise OSError("시험망 없음")
            return [{"mac": MAC2, "ip": "192.168.1.104", "alias": "Tapo P110M 2", "on": True, "watts": 19.9, "set": None}]
        d.scan_plugs = scan
        return d
    w.deps = deps
    logs = []
    load = lambda path: Config(data_dir=str(tmp_path))
    once = lambda t: sup.run_once(tmp_path, None, lambda c, d: w.deps(), False, lambda d, m: logs.append(m), now_t=t, load=load)
    once(T)
    doc = json.loads((tmp_path / sup.PLUGS_FILE).read_text(encoding="utf-8"))
    assert doc["t"] == T and doc["plugs"][0]["mac"] == MAC2 and n["calls"] == 1
    h = json.loads((tmp_path / sup.HEALTH_FILE).read_text(encoding="utf-8"))
    assert h["sets"][-1]["detect"] == {"cells": 0, "plugs": 1}                           # 감시자의 신호등에도 실린다(클라우드)
    once(T + 60); once(T + 599)
    assert n["calls"] == 1
    n["fail"] = True
    once(T + 600)
    doc = json.loads((tmp_path / sup.PLUGS_FILE).read_text(encoding="utf-8"))
    assert n["calls"] == 2 and "시험망 없음" in doc["error"] and any("플러그 탐색 실패" in m for m in logs)
    once(T + 660)
    assert n["calls"] == 2                                                               # 실패해도 1분마다 다시 방송하지 않는다
    sup.run_once(tmp_path, None, lambda c, d: w.deps(), True, lambda d, m: None, now_t=T + 2000, load=load)
    assert json.loads((tmp_path / sup.PLUGS_FILE).read_text(encoding="utf-8"))["t"] == T + 600   # 모의는 쓰지 않는다


# ---------- 결과판 서버 ----------

@pytest.fixture
def server(tmp_path):
    import serve_board
    from cellbench.control import PinGuard
    made = {}

    def start(pin="1234", devices=None, bench=None):
        data = tmp_path / "data"
        data.mkdir(exist_ok=True)
        b = bench or bench_file(tmp_path)
        dev = devices or FakeDevices()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), serve_board.make_handler(data, PinGuard(pin), b, dev))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        made["srv"] = srv
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def call(path, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}{path}",
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
            try:
                with opener.open(req, timeout=10) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        return call, data, b, dev
    yield start
    if "srv" in made:
        made["srv"].shutdown(); made["srv"].server_close()


def live_now(data, heard=None):
    t = time.time()
    (data / "now.json").write_text(json.dumps({"t": t, "beat": t, "phase": "DISCHARGE", "cycle": 1, "expected": 24, "cells": [],
                                               "plug": {"on": False, "w": 0.0}, "metrics": {},
                                               "heard": heard_rows() if heard is None else heard}), encoding="utf-8")


def test_api_register_view(server):
    call, data, _, _ = server()
    live_now(data)
    (data / "plugs.json").write_text(json.dumps({"t": time.time(), "plugs": [{"mac": MAC2, "ip": "192.168.1.104", "alias": "Tapo P110M 2",
                                                                                "on": True, "watts": 19.9}], "by": "supervisor"}), encoding="utf-8")
    code, j = call("/api/register")
    assert code == 200 and j["engine_running"] is True and j["next_id"] == 2 and j["cells_per_set"] == 24
    assert len(j["heard"]) == 24 and [p["set"] for p in j["plugs"]] == [None, 1] and j["enabled"] is True
    assert j["sets"][0]["serials"] == "11733-11756" and j["sets"][0]["running"] is True and 11733 in j["registered_serials"]


def test_api_scan_writes_plugs_json_and_keeps_old_on_failure(server):
    call, data, _, dev = server()
    code, j = call("/api/register/plugs/scan", {})
    assert code == 200 and j["ok"] is True and j["plugs"][0]["mac"] == MAC2 and dev.calls == ["scan"]
    doc = json.loads((data / "plugs.json").read_text(encoding="utf-8"))
    assert doc["by"] == "board" and doc["plugs"][0]["alias"] == "Tapo P110M 2"
    dev.scan_err = OSError("방송 실패")
    code, j = call("/api/register/plugs/scan", {})
    assert code == 502 and "방송 실패" in j["error"]
    assert json.loads((data / "plugs.json").read_text(encoding="utf-8")) == doc        # 옛 결과를 지우지 않는다


def test_api_register_passes_and_appends_set(server):
    call, data, bench, dev = server()
    live_now(data)
    (data / "plugs.json").write_text(json.dumps({"t": time.time(), "plugs": [{"mac": MAC2, "ip": "192.168.1.104", "alias": "Tapo P110M 2"}]}),
                                     encoding="utf-8")
    code, j = call("/api/register", {"pin": "1234", "plug_mac": MAC2, "serials": SER2})
    assert code == 200 and j["ok"] is True and j["saved"] is True and j["set"]["id"] == 2
    assert [i["key"] for i in j["checks"]] == ["plug", "cells", "overlap", "power"]
    assert dev.calls == [("read", MAC2, "192.168.1.104")]                               # 엔진이 돌면 직접 듣지 않는다
    b = json.loads(bench.read_text(encoding="utf-8"))
    assert b["sets"][0] == SET1 and b["sets"][1]["serials"] == "11594-11617" and b["sets"][1]["plug_alias"] == "Tapo P110M 2"
    assert b["health"] == {"wifi_warn_dbm": -75} and b["bench_id"] == "hq-bench-1"
    sets = call("/api/health")[1]["sets"]
    assert [(s["id"], s["word"]) for s in sets] == [(1, sets[0]["word"]), (2, "대기")]   # 감지가 사라지고 '대기'로
    assert call("/api/register")[1]["next_id"] == 3


def test_api_register_failure_saves_nothing(server):
    call, data, bench, _ = server()
    live_now(data, heard=heard_rows(SER2[:22]))
    raw = bench.read_bytes()
    code, j = call("/api/register", {"pin": "1234", "plug_mac": MAC2, "serials": SER2})
    assert code == 200 and j["ok"] is False and j["saved"] is False and j["set"] is None
    assert j["checks"][1]["detail"] == "22/24 · 11616, 11617 미수신 15초" and "선택은 유지됨" in j["note"]
    assert bench.read_bytes() == raw


def test_api_register_pin_lock_and_bad_body_never_touch_devices(server):
    call, data, bench, dev = server()
    live_now(data)
    raw = bench.read_bytes()
    assert call("/api/register", {"pin": "0000", "plug_mac": MAC2, "serials": SER2})[0] == 403
    assert call("/api/register", {"pin": "1234", "plug_mac": MAC2, "serials": SER2[:23]}) == (400, {"ok": False, "error": "셀은 정확히 24대여야 한다 (지금 23대)"})
    for _ in range(5):                                                                   # 맞는 PIN 이 위에서 실패 횟수를 지웠다
        call("/api/register", {"pin": "9999", "plug_mac": MAC2, "serials": SER2})
    assert call("/api/register", {"pin": "1234", "plug_mac": MAC2, "serials": SER2})[0] == 429   # 원격 명령과 같은 잠금
    assert call("/api/register")[1]["locked"] is True
    assert dev.calls == [] and bench.read_bytes() == raw


def test_api_register_disabled_without_pin(server):
    call, data, _, dev = server(pin=None)
    code, j = call("/api/register", {"pin": "", "plug_mac": MAC2, "serials": SER2})
    assert code == 403 and "remote_setup" in j["error"] and dev.calls == []
    assert call("/api/register")[1]["enabled"] is False
