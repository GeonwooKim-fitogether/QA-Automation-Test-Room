"""규칙 D-1 — 세트 전체 꺼짐 → Dock 으로 자동 켜기 (자동 복구 기획 5절 · 커밋 5). 장비 없이 돈다.

순수 판정(guards.net_ok · wake_verdict) → 복구 대기(RECOVER)를 가짜 세상에서 끝까지 — 세트 1 셀 24대가 꺼져 있고 세트 2 는 들린다.
Dock 은 가짜(FakeDock)라 포트를 열지 않는다. 스위치(dock.power_on)가 꺼진 길이 지금 운영의 기본값이라 그 길도 1급으로 본다.
감시자의 '두 세트 모두 0대(no_cells)' 조용한 재시도도 여기서 본다.
"""
import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sim_world as sw
from cellbench import guards
from cellbench import supervisor as sup
from cellbench.config import SAFETY, Config, expand_serials
from cellbench.cycle import CycleRunner, CycleState
from cellbench.dock import DockResult, WakeResult
from cellbench.record import ALERT_KINDS, EVENT_LIGHT, Recorder
from test_supervisor import T, World as SupWorld, eng, put

SET2 = expand_serials("11594-11617")
SET2_MAC = "AA:BB:CC:DD:EE:02"
DOCK_ON = {"port": "COM28", "usb_serial": "A50285BI", "power_on": True, "wake": ["CELL_EN_INIT", "CELL_AON"]}


# ---------- 순수 판정 ----------

def test_net_ok_needs_hub_and_other_set_or_low_last_batteries():
    ser = [1, 2, 3]
    assert guards.net_ok(False, {2: 24}, {}, ser, 12, 5) == (False, "LiveHub 가 이 PC 에서 닿지 않는다")
    ok, why = guards.net_ok(True, {2: 24}, {}, ser, 12, 5)
    assert ok and "세트 2 셀 24대" in why
    assert guards.net_ok(True, {2: 11}, {}, ser, 12, 5)[0] is False                 # 11대는 모자란다
    assert guards.net_ok(True, {}, {1: 3, 2: 0, 3: 5}, ser, 12, 5)[0] is True       # 단일 세트 — 마지막 잔량이 모두 5 % 이하
    assert guards.net_ok(True, {}, {1: 3, 2: 0, 3: 6}, ser, 12, 5)[0] is False      # 하나라도 넘으면 망 문제일 수 있다
    assert guards.net_ok(True, {}, {1: 3, 2: 0}, ser, 12, 5)[0] is False            # 모르는 셀이 있으면 그 증거는 쓰지 않는다


def test_wake_verdict_blocks_in_order_and_sends_only_when_all_pass():
    go = dict(silent_s=300, net=True, switch=True, full_cells=[], tries=0, day_n=1, charged=True)

    def v(**kw):
        return guards.wake_verdict(**{**go, **kw}, rule=SAFETY)
    assert v() == "send"
    assert v(silent_s=299) == "silent"                     # 지난 5분 안에 한 대라도 들렸다 (I8)
    assert v(net=False) == "net"                           # 두 세트 0대 · LiveHub 안 닿음 (I9)
    assert v(switch=False) == "switch"
    assert v(full_cells=[11740]) == "storage"              # 저장이 가득 차 신호만 멈춘 켜진 셀 (가7)
    assert v(tries=2) == "tries" and v(day_n=4) == "day"   # 사건당 2번 · 하루 3사건 (I11)
    assert v(day_n=3) == "send"
    assert v(charged=False) == "charging"
    assert v(silent_s=10, net=False, switch=False, charged=False) == "silent"      # 앞의 까닭이 이긴다


def test_new_kinds_have_lights_and_alerts():
    assert {EVENT_LIGHT[k] for k in ("cells_off", "dock_wake", "cells_woke")} == {"yellow"}
    assert {"cells_off", "cells_woke"} <= ALERT_KINDS and "dock_wake" not in ALERT_KINDS
    assert SAFETY["wake_silent_s"] == 300 and SAFETY["wake_charge_s"] == 1200 and SAFETY["wake_confirm_s"] == 180
    assert not hasattr(Config(), "wake_charge_s")         # Config 칸이 늘면 run.log '설정:' 줄이 바뀐다(I7)


# ---------- 복구 대기를 가짜 세상에서 ----------

class OffWorld(sw.World):
    """셀이 꺼질 수 있는 세상 — off 에 든 셀은 라이브가 오지 않는다. Dock 은 꺼진 셀도 충전한다(플러그가 켜져 있으면)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.off: set[int] = set()
        self.wakes: list[tuple] = []


class OffLive(sw.FakeLive):
    def snapshot(self):
        return {s: c for s, c in super().snapshot().items() if s not in self.w.off}


class FakeDock:
    """가짜 Dock — 부르면 세상의 꺼진 세트 1 셀을 켠다(keep_off 는 그대로). 부른 순간 세트 1 셀이 하나라도 들렸는지 남긴다(I8)."""

    def __init__(self, w, live, on=True, keep_off=()):
        self.w, self.live, self.on, self.keep_off = w, live, on, set(keep_off)

    def wake(self, all_off_confirmed=False, manual=False):
        heard = sorted(s for s in self.live.snapshot() if s in sw.SET1)
        self.w.wakes.append((self.w.clock.t, all_off_confirmed, manual, heard))
        if self.on:
            self.w.off -= set(sw.SET1) - self.keep_off
        return WakeResult(True, "", [DockResult(True, "CELL_EN_INIT", wrote=14), DockResult(True, "CELL_AON", wrote=10)])


def wake_world(monkeypatch, tmp_path, switch=True, set2_heard=True, dock_on=True, keep_off=(), batt=2.0, cls=None, **wkw):
    """세트 1 셀 24대가 모두 꺼진(잔량 batt) 세상과 그 세트의 실행기. 세트 2 는 같은 PC 에 등록돼 들린다(set2_heard)."""
    w = (cls or OffWorld)([sw.Dock(sw.SET1_MAC, sw.SET1), sw.Dock(SET2_MAC, SET2)], rate_pct_min=0.6, discharge_rate=0.33, **wkw)
    for s in sw.SET1:
        w.docks[0].batt[s] = batt
    w.off = set(sw.SET1) | (set() if set2_heard else set(SET2))
    sw.patch_world(monkeypatch, w)
    data = tmp_path / "data"
    dock = dict(DOCK_ON, power_on=switch)
    cfg = Config(data_dir=str(data), wifi_reconnect=False,
                 sets=[{"id": 1, "plug_mac": sw.SET1_MAC, "serials": "11733-11756", "dock": dock},
                       {"id": 2, "plug_mac": SET2_MAC, "serials": "11594-11617"}])
    live = OffLive(w)
    r = CycleRunner(cfg, live, sw.FakePlug(w, cfg), sw.FakeLink(w, cfg, live), Recorder(data))
    made = []

    def factory():
        made.append(w.clock.t)
        return FakeDock(w, live, on=dock_on, keep_off=keep_off)
    r._dock_factory = factory
    return w, r, data, made


def evs(data):
    with open(data / "events.csv", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def kinds(data):
    return [e["kind"] for e in evs(data)]


def test_all_off_with_other_set_heard_charges_20_min_wakes_once_then_precharges(tmp_path, monkeypatch):
    """기획 8절 엔진 통합 1 — Y 0대 · X 들림이면 20분 충전 뒤 켜기 1번, 24/24 뒤 예비 충전."""
    w, r, data, made = wake_world(monkeypatch, tmp_path)
    r._recover(CycleState(cycle=3))
    ks = kinds(data)
    assert ks[:3] == ["cells_off", "dock_wake", "cells_woke"] and "need_human" not in ks
    assert len(w.wakes) == 1
    t, confirmed, manual, heard = w.wakes[0]
    assert confirmed is True and manual is False and heard == []          # 한 대도 안 들릴 때만 보냈다 (I8)
    assert t - sw.T0 >= SAFETY["wake_charge_s"]                           # 최소 충전 20분 뒤
    e = {x["kind"]: x["detail"] for x in evs(data)}
    assert "24대가 모두 꺼졌다" in e["cells_off"] and "세트 2 셀 24대는 들린다" in e["cells_off"]
    assert "충전 20분 뒤 Dock 켜기 명령" in e["cells_off"] and "사람이 할 일: 없음" in e["cells_off"]
    assert "1/2" in e["dock_wake"] and "CELL_EN_INIT → CELL_AON" in e["dock_wake"]
    assert "24/24대가 다시 들린다" in e["cells_woke"] and "Dock 켜기 명령 1번" in e["cells_woke"] and "사람이 할 일: 없음" in e["cells_woke"]
    holds = (data / "holds.csv").read_text(encoding="utf-8-sig")
    assert "enter,RECOVER" in holds and "leave,RECOVER" in holds
    samples = (data / "samples_0001.csv").read_text(encoding="utf-8-sig")
    assert ",PRECHARGE," in samples                                       # 복구 대기 뒤 예비 충전부터 (G7)
    assert min(w.battery(s) for s in sw.SET1) >= 95                       # 0초 방전이 아니라 거의 만충에서 사이클로


def test_both_sets_silent_never_sends_and_calls_about_the_network(tmp_path, monkeypatch):
    """기획 8절 엔진 통합 2 — X 도 0대면 켜기 0번. 30분 뒤 사람 호출은 LiveHub 를 먼저 보라고 한다."""
    w, r, data, made = wake_world(monkeypatch, tmp_path, set2_heard=False, limit_h=0.6)
    with pytest.raises(sw.Stop):
        r._recover(CycleState(cycle=3))
    assert made == [] and w.wakes == []
    assert "cells_off" not in kinds(data)
    human = [e["detail"] for e in evs(data) if e["kind"] == "need_human"]
    assert len(human) == 1 and "망 문제인지 가릴 수 없다" in human[0] and "LiveHub 전원과 연결을 먼저" in human[0]
    assert "Dock 명령은 보내지 않았다" in human[0] and "켜진 셀이 하나라도 있으면 Dock 버튼을 누르지 않는다" in human[0]


def test_switch_off_never_touches_the_dock_and_asks_for_one_dock_press_then_continues(tmp_path, monkeypatch):
    """지금 운영의 기본 길 — 스위치가 꺼져 있으면 Dock 을 만들지도 않는다(I10). 30분 뒤 'Dock 버튼 한 번(모두 꺼져 있어 안전)'.
    사람이 켜면 cells_woke 를 남기고 예비 충전부터 저절로 이어 간다."""
    w, r, data, made = wake_world(monkeypatch, tmp_path, switch=False)
    w.at(45 * 60, lambda: w.off.clear())                                  # 사람이 Dock 버튼을 눌렀다
    r._recover(CycleState(cycle=3))
    assert made == [] and w.wakes == []
    ks = kinds(data)
    assert ks[:3] == ["cells_off", "need_human", "cells_woke"]
    e = {x["kind"]: x["detail"] for x in evs(data)}
    assert "Dock 켜기 명령은 쓰지 않는다(Dock 명령 스위치(dock.power_on)가 꺼져 있다" in e["cells_off"]
    assert "자동으로 한 일: 플러그를 켜 두고" in e["need_human"]
    assert "사람이 할 일: Dock 버튼을 한 번(3초) 눌러 전체를 켠다 — 셀이 모두 꺼져 있어 안전하다" in e["need_human"]
    assert "2초 이상" not in e["need_human"]
    assert "사람이 켰거나 저절로 돌아왔다" in e["cells_woke"]
    assert ",PRECHARGE," in (data / "samples_0001.csv").read_text(encoding="utf-8-sig")


def test_zero_after_wake_retries_once_after_30_min_then_calls_human(tmp_path, monkeypatch):
    w, r, data, made = wake_world(monkeypatch, tmp_path, dock_on=False, limit_h=1.6)
    with pytest.raises(sw.Stop):
        r._recover(CycleState(cycle=3))
    assert len(w.wakes) == 2 and w.wakes[1][0] - w.wakes[0][0] >= SAFETY["wake_retry_s"]
    assert kinds(data) == ["cells_off", "dock_wake", "dock_wake", "need_human"]
    human = evs(data)[-1]["detail"]
    assert "Dock 켜기 명령 2번 — 셀이 하나도 켜지지 않았다" in human and "Dock 버튼을 한 번(3초)" in human


def test_partial_wake_is_not_pressed_again(tmp_path, monkeypatch):
    """기획 8절 엔진 통합 3 의 앞부분 — 일부만 켜지면 다시 누르지 않는다(켜진 셀이 꺼진다)."""
    w, r, data, made = wake_world(monkeypatch, tmp_path, keep_off=(11740, 11751), limit_h=3)
    try:
        r._recover(CycleState(cycle=3))
    except sw.Stop:
        pass
    assert len(w.wakes) == 1
    woke = [e["detail"] for e in evs(data) if e["kind"] == "cells_woke"][0]
    assert "22/24대가 다시 들린다" in woke and "[11740, 11751]" in woke and "다시 누르지 않는다" in woke
    assert "일부 꺼짐 처리(리셋 또는 세트 정지)로 넘긴다" in woke                  # 그 뒤는 규칙 D-2 (tests/test_set_reset.py)


def test_send_rechecks_live_right_before_the_command(tmp_path, monkeypatch):
    """I8 — 보내기 직전에 그 세트 셀이 한 대라도 들리면 보내지 않는다(시도로 세지도 않는다)."""
    w, r, data, made = wake_world(monkeypatch, tmp_path)
    w.off.discard(11745)
    wk = {"tries": 0, "sent": None, "last_try": None, "why": "", "charge_s": 9999.0}
    r._wake_send(CycleState(cycle=3), wk)
    assert made == [] and wk["tries"] == 0 and kinds(data) == []
    assert "보내기 직전에 이 세트 셀이 들려" in (data / "run.log").read_text(encoding="utf-8")


def test_recover_keeps_charge_floor_recycle_and_refills_low_power_limit(tmp_path, monkeypatch):
    """G6 — 셀이 안 들리는 복구 대기에서 켜짐 · 바닥(24 W)이 30분 이어지면 끊었다 켜고, 저전력(1.4 W) 한도는 1시간마다 되채운다."""

    class Floor(OffWorld):
        w_on = 24.0

        def watts(self, mac):
            return self.w_on if self.plugs.get(mac) and mac == sw.SET1_MAC else super().watts(mac)

    w, r, data, _ = wake_world(monkeypatch, tmp_path, switch=False, limit_h=1.1, cls=Floor)
    with pytest.raises(sw.Stop):
        r._recover(CycleState(cycle=3))
    log = (data / "run.log").read_text(encoding="utf-8")
    assert log.count("바닥이 30분 이어져 30초 끊었다 켬") == 2                   # 30분 · 60분
    calls = w.plug_calls[sw.SET1_MAC]
    assert calls.count("recharge") == 3                                     # 들어갈 때 1 + 바닥 2

    class Dead(OffWorld):
        def watts(self, mac):
            return 1.4 if self.plugs.get(mac) and mac == sw.SET1_MAC else super().watts(mac)
    w2, r2, data2, _ = wake_world(monkeypatch, tmp_path / "b", switch=False, limit_h=1.2, cls=Dead)
    with pytest.raises(sw.Stop):
        r2._recover(CycleState(cycle=3))
    assert w2.plug_calls[sw.SET1_MAC].count("recharge") == 1 + 3 + 3         # 들어갈 때 1 + 한도 3 + 1시간 뒤 되채운 3


def test_recover_back_precharges_only_after_all_off_or_near_threshold(tmp_path, monkeypatch):
    """G7 — 모두 꺼졌던 세트이거나 가장 낮은 셀이 기준선 + 10 %p 이하일 때만 예비 충전부터. 망이 잠깐 끊긴 복구(골든 g2)는 지금처럼 곧바로."""
    w, r, data, _ = wake_world(monkeypatch, tmp_path)
    w.off.clear()
    for v, off, want in ((60, None, False), (40, None, True), (60, 1.0, True)):
        for s in sw.SET1:
            w.docks[0].batt[s] = v
        r._recover_back(CycleState(cycle=3), {"off": off, "tries": 0, "charge_s": 0.0}, w.clock.t)
        assert r._pre_after_recover is want, (v, off)


# ---------- 감시자 — 두 세트 모두 0대(no_cells) ----------

def test_no_cells_quiet_retry_backs_off_and_never_moves_the_plug(tmp_path):
    """1시간 3번 뒤 사람을 부르고, 그 뒤 30분 · 1시간 · 2시간 · 4시간 간격으로 조용히. 조용한 재시도는 플러그를 끊었다 켜지 않는다."""
    put(tmp_path, eng(exit="no_cells", pid=4242), None, cycles=1)
    w = SupWorld()
    cfg = Config(data_dir=str(tmp_path), auto_continue=False)
    rep, spawned, reasons = None, [], []
    plug_after = []
    for i in range(0, 700):
        before = w.next_pid
        n_plug = w.calls.count("plug_on")
        rep, _ = sup.tick(cfg, tmp_path, rep, w.deps(), T + 60 * i)
        if w.next_pid != before and any(c.startswith("engine:") for c in w.calls[-3:]):
            spawned.append(i)
            pid = w.next_pid
            w.alive.pop(pid, None)                                         # 띄운 엔진은 셀을 못 들어 몇 초 만에 끝난다
            (tmp_path / "engine.json").write_text(json.dumps(eng(exit="no_cells", pid=pid, started=T + 60 * i)), encoding="utf-8")
        if i >= 3:
            reasons.append(tuple(rep["signals"]["engine"]))
            plug_after.append(w.calls.count("plug_on") - n_plug)
    assert spawned == [0, 1, 2, 32, 92, 212, 452, 692]                     # 1시간 3번 → 30분 · 1시간 · 2시간 · 4시간 · 4시간
    assert sum(plug_after) == 0                                            # 조용한 재시도는 플러그를 움직이지 않는다(이틀이면 96번이던 것)
    assert set(reasons) == {("red", sup.NO_CELLS_HUMAN)}                    # 같은 빨강 · 같은 이유 — 조용한 재시도는 새 알림을 만들지 않는다
    assert "Dock 명령은 보내지 않는다" in sup.NO_CELLS_HUMAN and "사람이 할 일: LiveHub" in sup.NO_CELLS_HUMAN
    assert rep["memo"]["no_cells_quiet"] == {"n": 5, "t": T + 60 * 692}


def test_no_cells_quiet_flag_clears_when_an_engine_really_runs(tmp_path):
    put(tmp_path, eng(exit=None, pid=4242, started=T - 60), {"t": T, "beat": T, "phase": "DISCHARGE"}, cycles=1)
    w = SupWorld(alive={4242: T - 60})
    rep, _ = sup.tick(Config(data_dir=str(tmp_path), auto_continue=False), tmp_path, {"memo": {"no_cells_quiet": True}}, w.deps(), T)
    assert "no_cells_quiet" not in rep["memo"]


def test_plan_no_cells_quiet_is_pure():
    cfg = Config(auto_continue=False)
    e = eng(exit="no_cells")
    p = sup.restart_plan("dead", e, None, 1, [T - 300, T - 200, T - 100], T, cfg)
    assert p.restart == 0 and p.call_human and p.why == sup.NO_CELLS_HUMAN
    p = sup.restart_plan("dead", e, None, 1, [], T, cfg, quiet={"n": 0, "t": T - 1800})
    assert p.restart == 3 and p.call_human and not p.plug_on and p.quiet and p.why == sup.NO_CELLS_HUMAN
    assert sup.restart_plan("dead", e, None, 1, [], T, cfg, quiet={"n": 0, "t": T - 1799}).restart == 0
    assert sup.restart_plan("dead", e, None, 1, [], T, cfg, quiet={"n": 2, "t": T - 7199}).restart == 0     # 세 번째 뒤는 2시간
    assert sup.restart_plan("dead", e, None, 1, [], T, cfg, quiet={"n": 9, "t": T - 14400}).restart == 3    # 그 뒤로는 4시간
    p = sup.restart_plan("dead", e, None, 1, [T - 100], T, cfg)               # 한도 전 — 지금처럼 곧바로 되살린다(노랑)
    assert p.restart == 3 and not p.call_human
