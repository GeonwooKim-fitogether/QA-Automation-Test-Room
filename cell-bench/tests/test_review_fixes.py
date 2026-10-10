"""적대 안전 검토(2026-10-10)가 찾은 엔진·감시자 결함 F1~F15 — 검토의 재현 스크립트(sim_power · sim_sup · sim_storage ·
spawn_test)를 검사로 옮긴 것이다. 검사 이름 앞의 fN 이 검토 보고서의 번호다.

장비·실제 프로세스에는 닿지 않는다. F1 만 이 PC 에서 무해한 파이썬 자식 하나를 창 없이 띄워 '—' 를 로그하게 하고 끝을 기다린다.
"""
import csv
import io
import json
import os
import socket
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_cycle
import cellbench.cycle as cycle_mod
import cellbench.plug as plug_mod
from cellbench import guards, proc, record
from cellbench import supervisor as sup
from cellbench.cells import CellLink, CellLive, CellResult
from cellbench.config import Config
from cellbench.cycle import CycleRunner, CycleState
from cellbench.guards import PowerWatch
from cellbench.plug import PlugReading
from cellbench.record import ALERT_KINDS, EVENT_LIGHT, Recorder
from test_supervisor import T, World, cfg_for, eng, lights, now_json, put

ROOT = Path(__file__).resolve().parents[1]
GiB = 1024 ** 3


def events(tmp_path):
    with open(tmp_path / "events.csv", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def kinds(tmp_path):
    return [e["kind"] for e in events(tmp_path)]


class SeqPlug:
    """읽기마다 reads 에서 (켜짐, W) 를 꺼낸다(다 쓰면 마지막 값). 켜기·끊었다 켜기의 성공 여부를 고를 수 있다."""

    def __init__(self, reads=((True, 68.0),), on_ok=True):
        self.reads, self.on_ok = list(reads), on_ok
        self.calls, self.gaps = [], []

    def read(self):
        self.calls.append("read")
        on, w = self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]
        return PlugReading(on, w, time.time())

    def on(self):
        self.calls.append("on")
        if not self.on_ok:
            raise RuntimeError("플러그 on 실패 (3회): 20초 안에 응답 없음")
        return PlugReading(True, 68.0, time.time())

    def off(self):
        self.calls.append("off")
        return PlugReading(False, 0.0, time.time())

    def recharge(self, gap_s=10.0):
        self.calls.append("recharge"); self.gaps.append(gap_s)
        if not self.on_ok:
            raise RuntimeError("플러그 on 실패 (3회): 20초 안에 응답 없음")
        return PlugReading(True, 68.0, time.time())


class Live:
    def __init__(self, cells=None):
        self.cells, self.ip_changes = cells or {}, 0

    def snapshot(self):
        return dict(self.cells)

    def wait_for(self, serials, timeout_s):
        return []


def runner(tmp_path, plug=None, live=None, link=None, **over):
    over.setdefault("serials", [1, 2, 3])
    cfg = Config(data_dir=str(tmp_path), wifi_reconnect=False, **over)
    r = CycleRunner(cfg, live or Live(), plug or SeqPlug(), link, Recorder(tmp_path))
    r._disk_free = lambda: 500 * GiB
    return r


# ---------- F1 감시자가 띄운 엔진이 '—' 로그에서 죽는다 ----------

def test_f1_spawned_child_logs_em_dash_and_survives(tmp_path, monkeypatch):
    """검토의 spawn_test.py — proc.spawn 으로 띄운 자식(표준 출력 DEVNULL)이 Recorder.log 로 '—' 를 쓰고 끝까지 간다.
    이 검사 프로세스의 환경에 UTF-8 설정이 있어도 결과가 같도록 지우고 띄운다 — spawn 이 스스로 넘겨야 통과한다."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    out = tmp_path / "out.txt"
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from cellbench.record import Recorder; "
            "rec = Recorder(sys.argv[2]); rec.log('셀 신호 복구 — 다음 사이클을 시작한다'); "
            "open(sys.argv[3], 'w', encoding='utf-8').write(sys.stdout.encoding + '|ok')")
    pid = proc.spawn([proc.console_python(), "-c", code, str(ROOT), str(tmp_path / "d"), str(out)], tmp_path,
                     tmp_path / "err.txt")
    for _ in range(300):
        if proc.created(pid) is None:
            break
        time.sleep(0.1)
    err = (tmp_path / "err.txt").read_text(encoding="utf-8", errors="replace") if (tmp_path / "err.txt").exists() else ""
    assert out.exists(), err
    assert out.read_text(encoding="utf-8").lower().replace("-", "") == "utf8|ok"
    assert "다음 사이클을 시작한다" in (tmp_path / "d" / "run.log").read_text(encoding="utf-8")


def test_f1_child_env_has_utf8():
    env = proc.child_env()
    assert env["PYTHONIOENCODING"] == "utf-8" and env["PYTHONUTF8"] == "1"


def test_f1_recorder_log_never_raises_on_console(tmp_path, monkeypatch):
    """화면이 코드 페이지 949 · strict 여도(감시자가 띄운 엔진의 표준 출력과 같은 꼴) 예외 없이 지나가고 run.log 에는 그대로 남는다."""
    buf = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buf, encoding="cp949", errors="strict"))
    rec = Recorder(tmp_path)
    rec.log("셀 신호 복구 — 다음 사이클을 시작한다")
    rec.event(1, "CHARGE", "plug", "-", "충전 중 플러그 1.4 W — 30초 끊었다 켬 (1/3)")      # _safe 의 예외 처리 줄과 같은 길
    sys.stdout.flush()
    assert "\\u2014" in buf.getvalue().decode("cp949")
    assert (tmp_path / "run.log").read_text(encoding="utf-8").count("—") == 2


def test_f1_safe_stdio_turns_encoding_errors_into_escapes(monkeypatch):
    buf = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buf, encoding="cp949", errors="strict"))
    proc.safe_stdio()
    print("되살림 — 1시간에 1번째", flush=True)
    assert "\\u2014" in buf.getvalue().decode("cp949")
    monkeypatch.setattr(sys, "stdout", None)                 # pythonw — 화면이 없다
    proc.safe_stdio(); proc.console("—")                     # 아무 일도 없이 지나간다


# ---------- F2 설정 오류로 시작을 거부하면 감시자가 플러그를 켜지 않는다 ----------

def test_f2_config_error_turns_plug_on_once_and_calls_human(tmp_path):
    """검토의 sim_sup.py ① — 경계에서 바꾼 새 엔진이 설정 오류로 거부, 플러그는 방전 시작(꺼짐) 그대로."""
    put(tmp_path, eng(exit="config_error", error="Duplicate", started=T - 30), now_json(40, "DISCHARGE"), cycles=3)
    w = World()
    rep, _ = tick(tmp_path, w, None, T)
    rep, _ = tick(tmp_path, w, rep, T + 60)
    assert w.acts() == ["plug_on"]                                          # 같은 사건에 한 번
    assert lights(rep)["program"] == "red" and "플러그 ON" in rep["why"]


def test_f2_plug_on_even_when_supervisor_config_is_invalid(tmp_path):
    put(tmp_path, eng(exit="config_error", error="겹침"), now_json(40), cycles=3)
    bad = cfg_for(tmp_path, sets=[{"id": 1, "plug_mac": "20:E1:5D:E6:9C:77", "serials": "11733-11756"},
                                  {"id": 2, "plug_mac": "AA:BB:CC:DD:EE:FF", "serials": "11750-11760"}])
    w = World()
    rep, _ = sup.tick(bad, tmp_path, None, w.deps(), T)
    assert rep["config_problems"] and w.acts() == ["plug_on"]


def tick(tmp_path, w, prev, now_t, **cfg_over):
    return sup.tick(cfg_for(tmp_path, **cfg_over), tmp_path, prev, w.deps(), now_t)


# ---------- F3 끝난 뒤 '안전 상태'가 검증되지 않는다 ----------

def test_f3_safe_end_rechecks_after_stuck_s_and_kicks_again(tmp_path, monkeypatch):
    monkeypatch.setattr(cycle_mod.time, "sleep", lambda s: None)
    plug = SeqPlug(reads=[(True, 1.4), (False, 0.0), (True, 68.0)])
    r = runner(tmp_path, plug=plug)
    st = CycleState(cycle=4)
    assert r._safe_end(st) is True
    assert plug.calls == ["recharge", "read", "recharge", "read", "recharge", "read"]
    assert plug.gaps == [30.0, 30.0, 30.0]                                  # 10초가 아니라 stuck_gap_s
    ev = events(tmp_path)
    assert [e["kind"] for e in ev] == ["plug", "plug"]
    assert "끝낸 뒤 60초 확인: 플러그 1.4 W — 30초 끊었다 켬 (1/3)" in ev[0]["detail"]
    assert "플러그 꺼짐" in ev[1]["detail"] and st.last_on is True


def test_f3_safe_end_gives_up_with_dock_power_and_reports_off(tmp_path, monkeypatch):
    monkeypatch.setattr(cycle_mod.time, "sleep", lambda s: None)
    r = runner(tmp_path, plug=SeqPlug(reads=[(True, 1.4)]))
    assert r._safe_end(CycleState(cycle=1)) is True                        # 켜져는 있다 — 감시자가 이어서 지킨다
    assert kinds(tmp_path) == ["plug", "plug", "plug", "dock_power"] and r.plug.calls.count("recharge") == 4
    r2 = runner(tmp_path / "b", plug=SeqPlug(reads=[(False, 0.0)]))
    assert r2._safe_end(CycleState(cycle=1)) is False                      # 끝내 꺼짐 → engine.json plug_on=False → 감시자가 켠다


def test_f3_last_gasp_uses_stuck_gap(tmp_path):
    rec = Recorder(tmp_path); rec.engine_start({"pid": 1, "started": 1.0})
    plug = SeqPlug()
    run_cycle.last_gasp(rec, [plug], 30.0)
    assert plug.gaps == [30.0]
    run_cycle.last_gasp(rec, [plug])                                        # 기본값도 30초 (Config.stuck_gap_s)
    assert plug.gaps == [30.0, 30.0]


def test_f3_supervisor_plug_on_uses_stuck_gap(tmp_path, monkeypatch):
    import supervise
    from cellbench.alert import SlackSender
    got = []

    class FakePlug:
        def __init__(self, cfg, stats_path=None):
            pass

        def recharge(self, gap_s=10.0):
            got.append(gap_s); return PlugReading(True, 61.0, time.time())

        def read(self):
            return PlugReading(True, 61.0, time.time())

    monkeypatch.setattr(plug_mod, "Plug", FakePlug)
    d = supervise.make_deps_factory(False, lambda d, m: None, SlackSender(hook=""))(Config(data_dir=str(tmp_path)), tmp_path)
    assert d.plug_on() == "켜짐 · 61.0 W" and got == [30.0] and d.plug_read() == (True, 61.0)


def guard_world(reads, plug_ok=True):
    """플러그 지키기용 가짜 세상 — plug_read 가 reads 를 차례로 돌려준다(다 쓰면 마지막 값)."""
    w = World(plug_ok=plug_ok)
    q = list(reads)
    base = w.deps

    def deps():
        def read():
            w.calls.append("read")
            return q.pop(0) if len(q) > 1 else q[0]
        return replace(base(), plug_read=read)

    w.deps = deps
    return w


def test_f3_guard_turns_plug_on_when_found_off_after_done(tmp_path):
    put(tmp_path, eng(exit="done", plug_on=True), now_json(4000, "DONE"), cycles=4)
    w = guard_world([(False, 0.0), (True, 61.0)])
    rep, _ = tick(tmp_path, w, None, T)
    assert w.acts() == [] and rep["plug_guard"]["active"] is True and rep["plug_guard"]["next_t"] == T + 600
    rep, lines = tick(tmp_path, w, rep, T + 600)                            # 10분 뒤 처음 읽는다
    assert w.acts() == ["read", "plug_on"] and rep["plug_guard"]["action"] == "plug_on"
    assert rep["signals"]["plug_on"] == ["yellow", sup.GUARD_ON] and any("플러그 지키기" in l for l in lines)
    rep, _ = tick(tmp_path, w, rep, T + 660)                                # 켠 뒤 60초에 확인 — 켜짐 · 61 W
    assert w.acts() == ["read", "plug_on", "read"] and rep["plug_guard"]["action"] == "ok"
    assert sum(sup.GUARD_ON in a for a in w.alerts) == 1


def test_f3_guard_kicks_low_power_with_hourly_cap(tmp_path):
    put(tmp_path, eng(exit="stopped", plug_on=True), now_json(4000, "STOPPED"), cycles=2)
    w = guard_world([(True, 1.4)])
    rep = None
    for i in range(0, 41):                                                  # 10분 + 30분, 1분마다 점검
        rep, _ = tick(tmp_path, w, rep, T + 60 * i)
    assert w.acts().count("plug_on") == 3                                   # 1시간에 stuck_max(3)번까지
    g = rep["plug_guard"]
    assert g["action"] == "capped" and g["recharges_1h"] == 3
    assert rep["signals"]["plug_on"] == ["red", sup.GUARD_CAPPED] and lights(rep)["plug"] == "red"


def test_f3_guard_failing_plug_on_turns_red_after_three(tmp_path):
    put(tmp_path, eng(exit="interrupted"), now_json(4000, "CHARGE"), cycles=2)   # 사람 Ctrl+C 뒤에도 지킨다
    w = guard_world([(False, 0.0)], plug_ok=False)
    rep = None
    for i in range(0, 14):
        rep, _ = tick(tmp_path, w, rep, T + 60 * i)
    assert w.acts().count("plug_on") >= 3 and rep["signals"]["plug_on"] == ["red", sup.GUARD_STUCK]


def test_f3_guard_respects_pause_running_engine_and_strays(tmp_path):
    put(tmp_path, eng(exit="done", plug_on=True), now_json(4000, "DONE"), cycles=4,
        pause=json.dumps({"reason": "수동 작업", "until": T + 7200}))
    w = guard_world([(False, 0.0)])
    for i in range(0, 12):
        rep, _ = tick(tmp_path, w, None if i == 0 else rep, T + 60 * i)
    assert "read" not in w.acts() and "plug_on" not in w.acts() and rep["plug_guard"]["active"] is False
    put(tmp_path / "b", eng(), now_json(10), cycles=1)                      # 엔진이 돈다
    w = guard_world([(False, 0.0)]); w.alive[4242] = T - 3601
    rep, _ = sup.tick(cfg_for(tmp_path / "b"), tmp_path / "b", None, w.deps(), T)
    assert rep["plug_guard"]["active"] is False and "memo" in rep and "guard" not in rep["memo"]
    put(tmp_path / "c", None, None, cycles=0)                               # 기록 없음 — 그런데 기록에 없는 엔진 프로세스가 있다
    w = guard_world([(False, 0.0)]); w.scan_result = {"watchdog": [], "engine": [777]}
    rep, _ = sup.tick(cfg_for(tmp_path / "c"), tmp_path / "c", None, w.deps(), T)
    assert rep["plug_guard"]["active"] is False
    put(tmp_path / "d", eng(exit="done", plug_on=True), now_json(4000, "DONE"), cycles=4)   # 다 돈 뒤 다른 data 폴더의 엔진이 같은 PC 에서 돈다
    w = guard_world([(False, 0.0)]); w.scan_result = {"watchdog": [], "engine": [888]}
    rep = None
    for i in range(0, 12):
        rep, _ = sup.tick(cfg_for(tmp_path / "d"), tmp_path / "d", rep, w.deps(), T + 60 * i)
    assert "read" not in w.acts() and "plug_on" not in w.acts() and rep["plug_guard"]["active"] is False


# ---------- F4 저전력 복구를 포기할 때 플러그가 꺼져 있으면 그대로 둔다 ----------

def test_f4_failed_on_does_not_count_and_is_reported_once(tmp_path):
    """검토의 sim_power.py — 켜기가 늘 실패하는 플러그. 예전에는 3번 '시도'로 세고 포기해 꺼진 채 30분이 지나도 아무도 켜지 않았다."""
    plug = SeqPlug(reads=[(True, 1.4)], on_ok=False)
    r = runner(tmp_path, plug=plug, stuck_s=0.0)
    st = CycleState(cycle=1); r._phase(st, "CHARGE")
    for _ in range(10):
        r._last_read = PlugReading(False, 0.0, time.time())
        r._guard_power(st)
    assert plug.calls.count("recharge") == 10 and r._power.tries == 0 and not r._power.gave_up
    assert kinds(tmp_path) == ["plug"] and "한도에 세지 않고" in events(tmp_path)[0]["detail"]
    plug.on_ok = True                                                       # 플러그가 돌아오면 그 시도는 센다
    r._last_read = PlugReading(False, 0.0, time.time())
    r._guard_power(st)
    assert r._power.tries == 1 and r._power.fails == 0 and kinds(tmp_path)[-1] == "plug"


def test_f4_after_give_up_an_off_plug_is_still_turned_on(tmp_path):
    plug = SeqPlug(reads=[(True, 1.4)])
    r = runner(tmp_path, plug=plug, stuck_s=0.0)
    st = CycleState(cycle=1); r._phase(st, "CHARGE")
    for _ in range(5):                                                      # Dock 이 끌어 쓰지 않음 — 3번 끊었다 켜고 포기
        r._last_read = PlugReading(True, 1.4, time.time())
        r._guard_power(st)
    assert r._power.gave_up and kinds(tmp_path).count("dock_power") == 1
    r._last_read = PlugReading(False, 0.0, time.time())                     # 그 뒤 누가 끔
    r._guard_power(st)
    assert plug.calls[-1] == "on" and "한도를 다 쓴 뒤라도" in events(tmp_path)[-1]["detail"]


def test_f4_power_watch_refund():
    w = PowerWatch(10.0, 60.0, 30.0, 3)
    w.feed(0, False, 0.0)
    assert w.feed(60, False, 0.0) == "recharge" and w.tries == 1
    w.refund()
    assert w.tries == 0 and w.fails == 1 and w.attempts == 1
    w.feed(61, False, 0.0)
    assert w.feed(121, False, 0.0) == "recharge" and w.tries == 1 and w.attempts == 2


def test_f4_settle_power_is_bounded_even_when_on_always_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(cycle_mod.time, "sleep", lambda s: None)
    live = Live({s: CellLive(f"10.0.0.{s}", 30, 0, 4, -10, t=time.time() + 3600) for s in (1, 2, 3)})
    plug = SeqPlug(reads=[(False, 0.0)], on_ok=False)
    r = runner(tmp_path, plug=plug, live=live, stuck_s=0.0, poll_s=0.0)
    st = CycleState(cycle=1); r._phase(st, "PLUG_ON")
    r._last_read = PlugReading(False, 0.0, time.time())
    r._settle_power(st)                                                     # 영원히 돌지 않고 추출로 넘어간다
    assert plug.calls.count("recharge") == 4


# ---------- F5 일시 중지 동안 모든 알림이 꺼진다 ----------

def test_f5_pause_without_until_has_max_life():
    now = 1_800_000_000.0
    assert sup.pause_state('{"reason": "manual"}', now, now - 3600, 2.0) == (True, "manual")
    paused, why = sup.pause_state('{"reason": "manual"}', now, now - 3 * 3600, 2.0)
    assert not paused and "오래된 표지 무시" in why
    assert sup.pause_state("{깨짐", now, now - 3 * 3600, 2.0)[0] is False           # 읽을 수 없는 표지도 수명이 있다
    assert sup.pause_state("", now, now - 60, 2.0)[0] is True
    assert sup.pause_state(json.dumps({"until": now + 9999}), now, now - 3 * 3600, 2.0)[0] is True   # until 이 있으면 그것을 따른다


def test_f5_paused_dead_engine_still_alerts_red(tmp_path):
    """검토의 sim_sup.py ② — 사람이 남긴 일시 중지 표지(until 없음) 동안 엔진이 죽음. 조치는 하지 않되 빨강은 알린다."""
    put(tmp_path, eng(started=T - 9000, target_last_cycle=9), now_json(3000), cycles=4, pause='{"reason": "manual"}')
    os.utime(tmp_path / "supervisor_pause", (T - 60, T - 60))
    w = World()
    rep, _ = tick(tmp_path, w, None, T)
    assert w.acts() == [] and rep["paused"] == "manual"
    assert len(w.alerts) == 1 and "조치 · 프로그램" in w.alerts[0]


def test_f5_forgotten_pause_marker_is_ignored_after_max_life(tmp_path):
    put(tmp_path, eng(), now_json(4000), cycles=1, pause='{"reason": "manual"}')
    os.utime(tmp_path / "supervisor_pause", (T - 3 * 3600, T - 3 * 3600))
    w = World()
    rep, _ = tick(tmp_path, w, None, T)
    assert rep["paused"] is None and w.acts() == ["plug_on", "engine:3"]


# ---------- F6 추출 중 클라우드 심박이 끊겨 거짓 경보 ----------

class SpyCloud:
    enabled = False

    def __init__(self):
        self.states = []

    def state(self, payload):
        self.states.append(payload)

    def __getattr__(self, name):
        return lambda *a, **k: None


def test_f6_beat_uploads_cloud_state_once_per_minute(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "BEAT_MIN_GAP_S", 0.0)
    cloud = SpyCloud()
    rec = Recorder(tmp_path, cloud=cloud)
    rec.now({"phase": "EXTRACT", "cycle": 3})
    rec.beat()
    assert len(cloud.states) == 1                                           # 방금 올렸다 — 60초 안에는 다시 올리지 않는다
    rec._cloud_t = time.time() - 61
    time.sleep(0.01); rec.beat()
    assert len(cloud.states) == 2 and cloud.states[-1]["phase"] == "EXTRACT" and "beat" in cloud.states[-1]
    rec.beat()
    assert len(cloud.states) == 2


# ---------- F7 엔진 밖에서 지운 셀의 저장량 추정이 낡는다 ----------

def _cells_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("cycle,serial,ip,battery,size_mb,got_mb,bad_blocks,ended,deleted,seconds,resume_s,error,file\n")
        for serial, size, ts, dl in rows:
            f.write(f"12,{serial},10.0.0.{serial},90,{size:.2f},{size:.2f},0,1,{dl},60,20,,ftg_{serial}_{ts}.bin\n")


def _ts(ago_s):
    return time.strftime("%Y%m%d_%H%M%S", time.localtime(time.time() - ago_s))


def test_f7_old_and_pre_delete_rows_are_unknown(tmp_path):
    now = time.time()
    _cells_csv(tmp_path / "cells_0012.csv", [(1, 158.0, _ts(20 * 3600), 0), (2, 120.0, _ts(3600), 0)])
    got = guards.storage_from_cells_csv(tmp_path, [1, 2], now=now, max_age_s=12 * 3600, need_delete=False)
    assert 1 not in got and got[2][0] == 120.0                              # 20시간 전 기록은 '모름'
    assert guards.storage_from_cells_csv(tmp_path, [1, 2], now=now, max_age_s=12 * 3600, need_delete=True) == {}   # 삭제 켜기 전
    _cells_csv(tmp_path / "cells_0013.csv", [(2, 130.0, _ts(600), 0), (3, 150.0, _ts(600), 1)])
    got = guards.storage_from_cells_csv(tmp_path, [1, 2, 3], now=now, max_age_s=12 * 3600, need_delete=True)
    assert got[2][0] == 130.0 and got[3][0] == 0.0 and 1 not in got       # 1 은 20시간 전 기록뿐 — 여전히 모름


def test_f7_stale_estimate_raises_no_red_and_does_not_block_resume(tmp_path):
    """검토의 sim_storage.py — 20시간 전(삭제 없이) 추출한 기록 158 MB, 그 뒤 사람이 손으로 지웠다. 셀은 대기 모드.
    예전: 추정 142% → cell_storage_critical · cell_storage_full(빨강 · Slack), 대기 셀 복귀도 막힘."""
    _cells_csv(tmp_path / "cells_0012.csv", [(1, 158.0, _ts(20 * 3600), 0)])
    now = time.time()
    live = Live({1: CellLive("10.0.0.1", 80, 0, 4, -10, t=now - 120, t_wait=now)})
    resumed = []
    link = SimpleNamespace(resume=lambda s: resumed.append(list(s)) or {x: CellResult(x, "?", resume_s=10.0) for x in s})
    r = runner(tmp_path, live=live, link=link, serials=[1])
    st = CycleState(cycle=13); st.phase = "DISCHARGE"
    assert r._storage_pct(time.time()) == {}
    r._watch_storage(st); r._watch_waiting(st)
    assert not {"cell_storage", "cell_storage_critical", "cell_storage_full"} & set(kinds(tmp_path))
    assert resumed == [[1]]
    r._storage_known = {1: (158.0, now - 13 * 3600)}                        # 이번 실행에서 잰 값도 12시간이 넘으면 '모름'
    assert r._metrics_or_error(st)["cell_storage"]["max_pct"] is None


# ---------- F8 감시자가 엔진의 --config 를 읽지 않는다 ----------

def test_f8_supervisor_layers_engine_config_and_warns_once(tmp_path):
    put(tmp_path, eng(exit="config_error", args={"cycles": 4, "config": "run.json"}), now_json(40), cycles=1)
    mine = {"plug_mac": "20:E1:5D:E6:9C:77"}

    def load(path):
        if path is None:
            return Config(data_dir=str(tmp_path), **mine)
        assert Path(path) == tmp_path / "run.json"                          # 상대 경로는 cell-bench(root) 기준
        return Config(data_dir="elsewhere", plug_mac="AA:BB:CC:DD:EE:01")

    seen, logs = [], []
    w = World()

    def make(cfg, data):
        seen.append(cfg); return w.deps()

    rep = sup.run_once(tmp_path, None, make, False, lambda d, m: logs.append(m), now_t=T, load=load)
    assert seen[-1].plug_mac == "AA:BB:CC:DD:EE:01" and seen[-1].data_dir == str(tmp_path)
    assert rep["config_warning"] and sum("경고: 감시자와 엔진의 설정이 다름" in m for m in logs) == 1
    sup.run_once(tmp_path, None, make, False, lambda d, m: logs.append(m), now_t=T + 60, load=load)
    assert sum("경고:" in m for m in logs) == 1                             # 같은 감시자 프로세스에서는 다시 남기지 않는다


def test_f8_unreadable_engine_config_falls_back_and_still_plugs_on(tmp_path):
    put(tmp_path, eng(exit="config_error", error="깨진 JSON", args={"cycles": 4, "config": str(tmp_path / "bad.json")}),
        now_json(40), cycles=1)

    def load(path):
        if path is None:
            return Config(data_dir=str(tmp_path))
        raise ValueError("Expecting value: line 1 column 1")

    w, logs = World(), []
    rep = sup.run_once(tmp_path, None, lambda c, d: w.deps(), False, lambda d, m: logs.append(m), now_t=T, load=load)
    assert "읽지 못해 감시자 설정만 쓴다" in rep["config_warning"] and w.acts() == ["plug_on"]


# ---------- F9 시계가 앞으로 튀면 건강한 엔진을 끝낼 수 있다 ----------

class Mono:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_f9_clock_jump_does_not_kill_healthy_engine(tmp_path):
    put(tmp_path, eng(), now_json(30, "EXTRACT"), cycles=1)                # 추출 중 — 심박이 30초 전
    w = World(alive={4242: T - 3601})
    mono, seen = Mono(), {}
    deps = lambda: replace(w.deps(), mono=mono, beat_seen=seen)
    rep, _ = sup.tick(Config(data_dir=str(tmp_path)), tmp_path, None, deps(), T)
    assert rep["checks"]["engine"] == "ok"
    for i in (1, 2, 3):                                                     # 벽시계가 2시간 앞으로 튀었다 — 단조 시계는 1분씩
        mono.t += 60
        rep, _ = sup.tick(Config(data_dir=str(tmp_path)), tmp_path, rep, deps(), T + 7200 + 60 * i)
        assert rep["checks"]["engine"] == "ok", i                          # 예전: 벽시계로 7290초 → 멈춤 → 두 번째 점검에서 끝냈다
    assert w.acts() == [] and rep["engine"]["beat_age"] == 210.0


def test_f9_a_truly_stuck_beat_is_still_caught(tmp_path):
    put(tmp_path, eng(), now_json(30, "CHARGE"), cycles=1)
    w = World(alive={4242: T - 3601})
    mono, seen = Mono(), {}
    deps = lambda: replace(w.deps(), mono=mono, beat_seen=seen)
    rep = None
    for i in range(0, 7):                                                   # 벽시계·단조 시계 모두 1분씩 — 심박은 그대로
        rep, _ = sup.tick(Config(data_dir=str(tmp_path)), tmp_path, rep, deps(), T + 60 * i)
        mono.t += 60
    assert w.acts()[:3] == ["kill:4242", "plug_on", "engine:3"]


# ---------- F10 신원 확인 기본값이 열림 ----------

def test_f10_serve_requires_verify_and_none_never_deletes(tmp_path):
    from test_guards import _blocks, _fake_cell
    import threading
    from cellbench import protocol as P
    link = CellLink(Config(resume_timeout_s=1), SimpleNamespace(wait_resume=lambda s, since, t: 1.0), log=lambda m: None)
    pc, cell_sock = socket.socketpair()
    with pytest.raises(TypeError):
        link._serve(pc, CellResult(1, "127.0.0.1"), extract=True, out_dir=tmp_path)
    seen = []
    th = threading.Thread(target=_fake_cell, args=(cell_sock, 3 * P.BLOCK_DATA - 50, _blocks(3), seen)); th.start()
    res = CellResult(11733, "127.0.0.1")
    link._serve(pc, res, extract=True, out_dir=tmp_path, verify=None)
    th.join(5)
    assert res.ended and not res.deleted and P.MSG_DELETE not in seen and "신원 확인 함수 없이" in res.identity


# ---------- F11 끄기 응답이 켜짐이면 실패 ----------

def test_f11_off_answered_on_is_a_plug_failure_not_manual(tmp_path):
    class StuckOn(SeqPlug):
        def off(self):
            self.calls.append("off"); return PlugReading(True, 68.0, time.time())

    live = Live({s: CellLive(f"10.0.0.{s}", 60, 0, 4, -10, t=time.time() + 3600) for s in (1, 2, 3)})
    r = runner(tmp_path, plug=StuckOn(), live=live)
    st = CycleState(cycle=1); r._phase(st, "DISCHARGE")
    assert r._plug("off", st) is None and r._cmd_failed == "off" and r.plug_known is True
    assert kinds(tmp_path) == ["plug"] and "실패로 본다" in events(tmp_path)[0]["detail"]
    r._last_read = PlugReading(True, 68.0, time.time())
    r._guard_discharge_plug(st)
    assert "manual_plug" not in kinds(tmp_path) and kinds(tmp_path)[-1] == "plug"


# ---------- F14 Ctrl+C 로 끝날 때 플러그가 꺼져 있으면 알린다 ----------

def test_f14_interrupt_with_plug_off_leaves_yellow_event(tmp_path):
    assert EVENT_LIGHT["interrupted_plug_off"] == "yellow" and "interrupted_plug_off" in ALERT_KINDS
    r = runner(tmp_path)
    st = CycleState(cycle=5); st.phase = "DISCHARGE"; r.current = st
    r._plug("off", st)                                                      # 방전 시작 — 마지막으로 안 상태는 꺼짐
    run_cycle.note_interrupt(r.rec, r)
    e = events(tmp_path)[-1]
    assert e["kind"] == "interrupted_plug_off" and e["cycle"] == "5" and e["phase"] == "DISCHARGE"
    assert "셀이 방전 중" in (tmp_path / "run.log").read_text(encoding="utf-8")
    r2 = runner(tmp_path / "b")
    r2._plug("on", CycleState(cycle=1))
    run_cycle.note_interrupt(r2.rec, r2)                                    # 켜짐이면 남기지 않는다
    run_cycle.note_interrupt(r2.rec, None)                                  # 엔진을 만들기 전 Ctrl+C
    assert "interrupted_plug_off" not in kinds(tmp_path / "b")


# ---------- F15 신원 확인 실패를 셀마다 빨강 한 건씩 ----------

def test_f15_identity_failures_grouped_per_extraction_and_stale_is_yellow(tmp_path, monkeypatch):
    monkeypatch.setattr(cycle_mod.time, "sleep", lambda s: None)
    live = Live({s: CellLive(f"10.0.0.{s}", 30, 0, 4, -10, t=time.time() + 3600) for s in (1, 2, 3, 4)})
    stale = dict(error="주소-시리얼 불일치 — 깨우지 않음", identity="셀 의 마지막 라이브가 깨우기 40초 전(기준 20초)", identity_stale=True)
    results = {1: CellResult(1, "10.0.0.1", **stale), 2: CellResult(2, "10.0.0.2", **stale),
               3: CellResult(3, "10.0.0.3", error="주소-시리얼 불일치 — 받지 않음", identity="10.0.0.3 의 마지막 라이브는 셀 9 것"),
               4: CellResult(4, "10.0.0.4", size=0, resume_s=17.0)}

    class Link:
        def extract(self, serials, out_dir):
            for c in live.cells.values():
                c.battery = 100
            return dict(results)

    r = runner(tmp_path, plug=SeqPlug(reads=[(True, 31.0)]), live=live, link=Link(), serials=[1, 2, 3, 4],
               full_flat_min=0.002, poll_s=0.0)
    r.run_cycle()
    ev = [e for e in events(tmp_path) if e["kind"].startswith("identity")]
    assert [(e["kind"], e["serial"]) for e in ev] == [("identity", "3"), ("identity_stale", "-")]
    assert "[1, 2]" in ev[1]["detail"] and EVENT_LIGHT["identity_stale"] == "yellow" and "identity_stale" not in ALERT_KINDS


def test_f15_cell_link_marks_freshness_only_failures(tmp_path):
    from test_identity import A, ADDR, B, _pkt, bare_listener, run_link
    L = bare_listener(); L._handle(_pkt(A), ADDR, time.time() - 60)       # 라이브가 60초 끊긴 셀 — ③ 만 걸린다
    res, _ = run_link(L, "extract", tmp_path)
    assert res.identity and res.identity_stale is True
    L = bare_listener(); L._handle(_pkt(A), ADDR, time.time() - 60); L._handle(_pkt(B), ADDR, time.time() - 1)
    res, _ = run_link(L, "extract", tmp_path / "b")                         # 그 주소를 다른 셀이 쓴다 — 주소 충돌
    assert res.identity and res.identity_stale is False
