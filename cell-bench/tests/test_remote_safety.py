"""원격 명령 안전 수정 (2026-10-10 QA 되돌림) — 오래된 명령 버림 · 시작 시 잔여 명령 버림 · 안전 정지 잠금 제외 ·
PIN 잠금 서버 전체 하나 · 헤더 위조 · 동시 쓰기 · 노랑 확인."""
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
from cellbench import health as healthmod
from cellbench.control import ControlInbox, PinGuard, StopRequested, command_expired, write_json_atomic
from cellbench.config import Config
from cellbench.cycle import CycleRunner, CycleState
from cellbench.record import Recorder
from test_remote import _Live, _Plug, _runner


# ---- 명령의 나이 (C-1) ----

def test_command_expired_reads_local_and_cloud_times():
    now = 10_000.0
    assert command_expired({"cmd": "plug_on", "t": now - 30}, 120, now) is None
    assert "초 지남" in command_expired({"cmd": "plug_on", "t": now - 121}, 120, now)
    iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - 3600))
    assert "초 지남" in command_expired({"cmd": "plug_off", "requested_at": iso}, 120, now)
    assert command_expired({"cmd": "plug_off"}, 120, now) == "보낸 시각을 모름"     # 나이를 잴 수 없으면 버린다


def test_expired_local_command_is_dropped_not_run(tmp_path):
    r = _runner(tmp_path); st = CycleState(cycle=1); st.phase = "CHARGE"
    r.ctrl.post("plug_off", "phone")
    p = tmp_path / "control.json"; cmd = json.loads(p.read_text(encoding="utf-8"))
    cmd["t"] -= 3600                                    # 엔진이 멈춘 동안 받은 명령 — 한 시간 전
    p.write_text(json.dumps(cmd), encoding="utf-8")
    r._handle_control(st)
    assert r.plug.calls == [] and r._force is None       # 플러그를 건드리지 않았다
    assert "버림" in r.ctrl.last_ack()["result"]
    assert "manual_expired" in (tmp_path / "events.csv").read_text(encoding="utf-8-sig")


def test_expired_stop_safe_does_not_stop(tmp_path):
    r = _runner(tmp_path); st = CycleState(cycle=1)
    (tmp_path / "control.json").write_text(json.dumps({"cmd": "stop_safe", "source": "x", "t": time.time() - 600}),
                                           encoding="utf-8")
    r._handle_control(st)                                 # StopRequested 가 나오지 않아야 한다
    assert r.plug.calls == []


def test_fresh_command_still_runs(tmp_path):
    r = _runner(tmp_path); st = CycleState(cycle=1); st.phase = "DISCHARGE"
    r.ctrl.post("plug_on", "phone")
    r._handle_control(st)
    assert r.plug.calls == ["on"] and r._force == "charge"


def test_leftover_command_is_dropped_at_engine_start(tmp_path):
    ControlInbox(tmp_path).post("plug_off", "phone")    # 엔진이 꺼져 있던 동안 받은 명령 (방금 보냈어도)
    r = _runner(tmp_path)
    assert not (tmp_path / "control.json").exists()      # 시작하며 집어 갔다
    assert "엔진 시작" in r.ctrl.last_ack()["result"]
    r._handle_control(CycleState(cycle=1))
    assert r.plug.calls == []


class _Cloud:
    def __init__(self, cmd): self.cmd, self.acks = cmd, []
    def poll_command(self):
        c, self.cmd = self.cmd, None
        return c
    def ack(self, cid, result): self.acks.append((cid, result))
    def __getattr__(self, name): return lambda *a, **k: None


def test_expired_cloud_command_is_dropped(tmp_path):
    old = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 7200))
    rec = Recorder(tmp_path); rec.cloud = _Cloud({"id": 7, "cmd": "plug_off", "requested_by": "a@b.c", "requested_at": old})
    cfg = Config(data_dir=str(tmp_path), wifi_reconnect=False, stuck_s=0.0)
    r = CycleRunner(cfg, _Live(), _Plug(), None, rec); st = CycleState(cycle=1); st.phase = "CHARGE"
    r._handle_control(st)
    assert r.plug.calls == [] and rec.cloud.acks and rec.cloud.acks[0][0] == 7 and "버림" in rec.cloud.acks[0][1]


# ---- PIN 잠금 (H-2 · H-3) ----

def test_safe_stop_passes_lock_with_right_pin_only():
    g = PinGuard("1234"); t = 1000.0
    for i in range(5):
        g.check("attacker", "0000", now=t + i)
    assert g.locked(now=t + 10)
    assert g.check("me", "1234", now=t + 10) is False                         # 켜기·끄기·등록은 잠김
    assert g.check("me", "9999", now=t + 10, allow_locked=True) is False      # 안전 정지라도 틀린 PIN 은 거부
    assert g.check("me", "1234", now=t + 11, allow_locked=True) is True       # 맞는 PIN 의 안전 정지는 통과


@pytest.fixture
def server(tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), __import__("serve_board").make_handler(tmp_path, PinGuard("1234")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path
    srv.shutdown()


def _post(base, path, body, headers=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_changing_forwarded_header_does_not_reset_lock(server):
    base, _ = server
    for i in range(5):
        _post(base, "/api/control", {"cmd": "plug_on", "pin": "0000"}, {"X-Forwarded-For": f"10.0.0.{i}"})
    code, j = _post(base, "/api/control", {"cmd": "plug_on", "pin": "1234"}, {"X-Forwarded-For": "10.9.9.9"})
    assert code == 429 and j["ok"] is False                                   # 헤더를 바꿔도 잠금은 그대로
    code, j = _post(base, "/api/control", {"cmd": "stop_safe", "pin": "1234"})
    assert code == 200 and j["ok"] is True                                    # 안전 정지는 맞는 PIN 으로 통과


def test_concurrent_command_posts_all_answer(server):
    base, data = server
    out = []
    def go():
        try:
            out.append(_post(base, "/api/control", {"cmd": "plug_on", "pin": "1234"})[0])
        except Exception as e:                                                # 연결이 끊기면 실패로 센다
            out.append(repr(e))
    ts = [threading.Thread(target=go) for _ in range(90)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert out.count(200) == 90, out
    assert json.loads((data / "control.json").read_text(encoding="utf-8"))["cmd"] == "plug_on"
    assert not list(data.glob("*.tmp"))                                       # 임시 파일이 남지 않는다


def test_write_json_atomic_parallel_same_target(tmp_path):
    target = tmp_path / "x.json"; ok = []
    def w(i): ok.append(write_json_atomic(target, {"i": i}))
    ts = [threading.Thread(target=w, args=(i,)) for i in range(60)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert all(ok) and isinstance(json.loads(target.read_text(encoding="utf-8"))["i"], int)


# ---- 노랑 확인 (M-3) ----

def test_yellow_lane_takes_ack():
    lane = {"key": "disk", "light": "yellow", "reason": "디스크 여유 18 GB"}
    acks = healthmod.ack_record({}, "disk", lane["reason"], "yellow", 2000.0)
    out = healthmod.mark_acks({"lanes": [dict(lane)]}, acks)["lanes"][0]
    assert out["acked_at"] == 2000.0 and out["ack_old"] is False
    red = healthmod.mark_acks({"lanes": [{**lane, "light": "red"}]}, acks)["lanes"][0]
    assert red["acked_at"] is None and red["ack_old"] is True                 # 노랑 때 누른 확인은 빨강을 덮지 않는다
