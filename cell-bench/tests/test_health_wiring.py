"""신호등 배선 — 감시자가 health.json · osinfo.json 을 쓰고 클라우드에 올리는지, 결과판 서버가 /api/health 를 판정하고
'확인'을 받는지, 클라우드가 bench_health 에 한 줄을 덮어쓰는지. 장비·Slack·Supabase·자격 증명 관리자에는 닿지 않는다
(서버는 임의 포트의 127.0.0.1, 클라우드는 가짜 _req)."""
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cellbench import supervisor as sup
from cellbench.alert import Alerter
from cellbench.config import Config
from cellbench.health import LANE_KEYS
from test_cloud import PumpCloud
from test_supervisor import T, World, eng, now_json, put


def run_once(tmp_path, w, dry=False, t=T, logs=None):
    load = lambda path: Config(data_dir=str(tmp_path))
    return sup.run_once(tmp_path, None, lambda c, d: w.deps(), dry, lambda d, m: (logs or []).append(m), now_t=t, load=load)


def test_run_once_writes_health_and_osinfo_and_publishes(tmp_path):
    put(tmp_path, eng(exit="done", plug_on=True), now_json(4000, "DONE"), cycles=4)
    w = World()
    rep = run_once(tmp_path, w)
    h = json.loads((tmp_path / sup.HEALTH_FILE).read_text(encoding="utf-8"))
    assert [l["key"] for l in h["lanes"]] == list(LANE_KEYS) and h["light"] == "green" and h["bench_id"] == "hq-bench-1"
    assert w.published == [h]                                              # 클라우드에 같은 판정을 올린다
    o = json.loads((tmp_path / sup.OSINFO_FILE).read_text(encoding="utf-8"))
    assert o["t"] == T and o["wifi"]["signal_pct"] == 99
    st = json.loads((tmp_path / sup.STATE_FILE).read_text(encoding="utf-8"))
    assert "health" not in st and "osinfo" not in st                       # supervisor.json 에는 넣지 않는다
    assert st["signals"]["engine"] == ["green", "목표 사이클을 다 돌고 끝남"] and st["disk_free_gb"] == 500.0
    assert set(st["alerts"]) == set(LANE_KEYS) and rep["slack"] is True


def test_dry_run_writes_nothing_and_publishes_nothing(tmp_path):
    put(tmp_path, eng(exit="done", plug_on=True), now_json(4000, "DONE"), cycles=4)
    w = World()
    run_once(tmp_path, w, dry=True)
    assert not any((tmp_path / f).exists() for f in (sup.STATE_FILE, sup.HEALTH_FILE, sup.OSINFO_FILE)) and w.published == []


def test_osinfo_is_collected_every_ten_minutes(tmp_path):
    put(tmp_path, eng(exit="done", plug_on=True), now_json(4000, "DONE"), cycles=4)
    w = World()
    n = {"calls": 0}
    base = w.deps

    def deps():
        d = base()
        inner = d.osinfo
        d.osinfo = lambda: (n.__setitem__("calls", n["calls"] + 1), inner())[1]
        return d
    w.deps = deps
    for dt in (0, 60, 599):
        run_once(tmp_path, w, t=T + dt)
    assert n["calls"] == 1
    run_once(tmp_path, w, t=T + 600)
    assert n["calls"] == 2


def test_osinfo_failure_keeps_old_and_power_lane_shows_it(tmp_path):
    put(tmp_path, eng(exit="done", plug_on=True), now_json(4000, "DONE"), cycles=4)
    w = World()
    base = w.deps

    def deps():
        d = base()
        d.osinfo = lambda: 1 / 0                    # 모으다 예외 — 감시자는 멈추지 않는다
        return d
    w.deps = deps
    rep, _ = sup.tick(Config(data_dir=str(tmp_path)), tmp_path, None, w.deps(), T)
    lane = {l["key"]: l for l in rep["health"]["lanes"]}["power"]
    assert lane["light"] == "unknown" and "osinfo" not in rep
    assert "미확인 · 전원·OS" in w.alerts[0]                                # 감시자가 있는데 OS 정보를 못 모음 = 회색, 알린다


def test_retired_keys_are_forgotten_silently(tmp_path):
    put(tmp_path, eng(exit="done", plug_on=True), now_json(4000, "DONE"), cycles=4)
    w = World()
    w.alerter.update({"engine": ("red", "옛 빨강"), "net": ("yellow", "옛 노랑")}, T - 600)
    w.alerts.clear()
    rep, _ = sup.tick(Config(data_dir=str(tmp_path)), tmp_path, None, w.deps(), T)
    assert "engine" not in rep["alerts"] and "net" not in rep["alerts"] and w.alerts == []    # 복구 알림 없이 사라진다


def test_alerter_forget_saves_and_clears_yellow_memory(tmp_path):
    sent = []
    a = Alerter(sent.append, tmp_path / "s.json", None)
    a.update({"board": ("yellow", "다운"), "disk": ("yellow", "디스크")}, T)
    assert a.forget(["board"]) is True and a.forget(["board"]) is False
    st = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert "board" not in st["keys"] and "disk" in st["keys"] and not any(k.startswith("board|") for k in st["yellow_sent"])


# ---------- 클라우드 bench_health ----------

def test_cloud_health_upserts_one_row_and_never_resends_older():
    c = PumpCloud(fail=lambda m, t, n: OSError("x") if n == 1 else None)
    c.health({"light": "yellow", "reason": "준비 1건", "lanes": []})
    c.pump(T)                                                                # 실패 → 30초 뒤 재시도 예약
    c.health({"light": "green", "reason": "모든 차선 정상", "lanes": []})
    c.pump(T + 10)
    c.pump(T + 30)                                                           # 옛 판정 재시도는 건너뛴다
    rows = [x[3][0] for x in c.calls if x[2] == "bench_health"]
    assert [r["light"] for r in rows] == ["yellow", "green"] and rows[1]["bench_id"] == "t1"
    assert rows[1]["reason"] == "모든 차선 정상" and rows[1]["payload"]["light"] == "green" and c.stats()["queue"] == 0


def test_nocloud_health_is_silent():
    from cellbench.cloud import NoCloud
    NoCloud().health({"light": "green"})


# ---------- 결과판 서버 ----------

@pytest.fixture
def board(tmp_path):
    import serve_board
    from cellbench.control import PinGuard
    srv = ThreadingHTTPServer(("127.0.0.1", 0), serve_board.make_handler(tmp_path, PinGuard(None)))
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}{path}", data=data,
                                     headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
        try:
            with opener.open(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    yield call
    srv.shutdown(); srv.server_close()


def test_api_health_judges_from_files_without_supervisor(board, tmp_path):
    import time
    (tmp_path / "now.json").write_text(json.dumps({"t": time.time(), "beat": time.time(), "phase": "DISCHARGE", "cycle": 2,
                                                   "expected": 24, "cells": [], "metrics": {"disk_free_gb": 3.0}}), encoding="utf-8")
    code, h = board("/api/health")
    lanes = {l["key"]: l for l in h["lanes"]}
    assert code == 200 and list(lanes) == list(LANE_KEYS)
    assert lanes["live"]["light"] == "red" and lanes["disk"]["light"] == "red"          # 셀 수신 0 · 디스크 3 GB
    assert lanes["power"]["light"] == "unknown" and h["supervisor"] == "감시자 없음"      # 감시자가 없어도 판정은 된다
    assert h["light"] == "red"


def test_api_alert_ack(board, tmp_path):
    code, j = board("/api/alert/ack", {"key": "program"})
    assert code == 200 and j["ok"] is True and j["key"] == "program"
    assert "program" in json.loads((tmp_path / "alert_ack.json").read_text(encoding="utf-8"))
    assert board("/api/alert/ack", {"key": "engine"})[0] == 400                          # 차선 키만 받는다
    assert board("/api/alert/ack", raw=b"{not json")[0] == 400
    assert board("/api/control", {"cmd": "plug_on", "pin": "1"})[0] == 403              # 명령은 여전히 PIN 이 있어야 한다


def test_api_health_marks_ack_after_red_started(board, tmp_path):
    import time
    now = time.time()
    (tmp_path / "supervisor.json").write_text(json.dumps({"t": now, "checks": {"wifi": "ok"}, "signals": {},
                                                          "slack": True, "disk_free_gb": 1.0,
                                                          "alerts": {"disk": {"light": "red", "since": now - 60}}}), encoding="utf-8")
    board("/api/alert/ack", {"key": "disk"})
    lanes = {l["key"]: l for l in board("/api/health")[1]["lanes"]}
    assert lanes["disk"]["light"] == "red" and lanes["disk"]["acked_at"] >= now - 1


def test_api_ack_is_bound_to_the_cause_without_supervisor(board, tmp_path):
    """QA 3 — 감시자가 없어도 확인은 그 원인에 묶인다. 새 원인이면 '확인'이 다시 선다(ack_old)."""
    import time
    def write_now(disk):
        t = time.time()
        (tmp_path / "now.json").write_text(json.dumps({"t": t, "beat": t, "phase": "DISCHARGE", "cycle": 2, "expected": 24,
                                                       "cells": [], "metrics": {"disk_free_gb": disk}}), encoding="utf-8")
    write_now(3.0)
    disk = {l["key"]: l for l in board("/api/health")[1]["lanes"]}["disk"]
    code, j = board("/api/alert/ack", {"key": "disk", "reason": disk["reason"], "light": disk["light"]})
    assert code == 200 and j["reason"] == disk["reason"]
    saved = json.loads((tmp_path / "alert_ack.json").read_text(encoding="utf-8"))
    assert isinstance(saved["disk"], float) and saved["_reason"]["disk"]["reason"] == disk["reason"]   # 알림기의 꼴(키: 시각)은 그대로
    lanes = {l["key"]: l for l in board("/api/health")[1]["lanes"]}
    assert lanes["disk"]["acked_at"] == saved["disk"] and lanes["disk"]["ack_old"] is False
    code, j = board("/api/alert/ack", {"key": "live", "reason": "화면이 보던 옛 이유"})                  # 그사이 원인이 바뀌었다
    lanes = {l["key"]: l for l in board("/api/health")[1]["lanes"]}
    assert lanes["live"]["light"] == "red" and lanes["live"]["acked_at"] is None and lanes["live"]["ack_old"] is True
    from cellbench.alert import Alerter
    a = Alerter(lambda m: None, None, tmp_path / "alert_ack.json")
    assert a._acked(a.acks(), "disk", saved["disk"] - 1) is True                                    # 알림기도 같은 파일을 읽는다


def test_api_health_reads_engine_json_for_ctrl_c_with_plug_off(board, tmp_path):
    """F14 — 감시자가 없어도 결과판이 engine.json 을 읽어 사람이 멈춘 엔진 + 플러그 꺼짐을 노랑으로 보인다."""
    import time
    t = time.time()
    (tmp_path / "now.json").write_text(json.dumps({"t": t - 30, "beat": t - 30, "phase": "DISCHARGE", "cycle": 2, "expected": 24,
                                                   "cells": [], "plug": {"on": False, "w": 0.3}, "metrics": {}}), encoding="utf-8")
    (tmp_path / "engine.json").write_text(json.dumps({"pid": 1, "started": t - 3600, "exit": "interrupted", "ended": t - 20,
                                                      "plug_on": None}), encoding="utf-8")
    h = board("/api/health")[1]
    hu = {l["key"]: l for l in h["lanes"]}["human"]
    assert h["engine"] == "ended" and hu["light"] == "yellow" and hu["reason"].startswith("엔진이 사람 손으로 멈춤")


def test_real_factory_publishes_to_given_cloud_and_reads_os_without_writing(tmp_path, monkeypatch):
    """실물 통로(모의 아님): publish_health 가 넘겨받은 클라우드로 간다. 통로를 만드는 것만으로는 Cloud(자격 증명 관리자)를 만들지 않는다."""
    import supervise
    from cellbench import cloud as cloudmod
    from cellbench.alert import SlackSender
    made = []
    monkeypatch.setattr(cloudmod, "Cloud", lambda *a, **k: made.append(k) or None)
    sent = []

    class FakeCloud:
        def health(self, h):
            sent.append(h)
    make = supervise.make_deps_factory(False, lambda d, m: None, SlackSender(hook=""), cloud=FakeCloud())
    d = make(Config(data_dir=str(tmp_path)), tmp_path)
    assert made == []
    d.publish_health({"light": "green"})
    assert sent == [{"light": "green"}] and made == []
    assert isinstance(d.disk_free_gb(), float) and d.disk_free_gb() > 0          # data 드라이브 여유 — 읽기만


def test_once_per_hour_log(monkeypatch):
    import supervise
    logs = []
    clock = {"t": T}
    monkeypatch.setattr(supervise.time, "time", lambda: clock["t"])
    f = supervise.once_per_hour(lambda d, m: logs.append(m), Path("."))
    f("클라우드: health 전송 실패 (HTTP 404) — 1번째 재시도를 30초 뒤에")
    f("클라우드: health 전송 실패 (HTTP 404) — 2번째 재시도를 120초 뒤에")
    clock["t"] += 3600
    f("클라우드: health 전송 실패 (HTTP 404) — 1번째 재시도를 30초 뒤에")
    assert len(logs) == 2
