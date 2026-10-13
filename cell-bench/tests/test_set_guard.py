"""세트 지키기 — 엔진이 도는 동안 운전하지 않는 등록 세트를 감시자가 지킨다 (supervisor.set_guard · 10-13 사고).

10-13 에 세트 2 가 등록만 되고 운전하지 않는 동안 아무도 그 세트를 보지 않아 Dock 이 충전을 멈추고 셀 24대가 방전돼 꺼졌다.
  · 10분마다 그 세트 플러그를 읽어 꺼짐이면 켠다
  · 셀이 15분 넘게 하나도 안 들리면 경고 set_silent(노랑 · Slack) — 이때 플러그를 끊었다 켜지 않는다(꺼진 셀을 더 해칠 수 있다)
  · 셀이 들리고 전력이 20 W 이하(충전도 만충 바닥도 아님)가 3번 연속이면 30초 끊었다 켠다(1시간에 2번)
  · 엔진이 운전하는 세트 · 검증 중인 세트 · 자동 합류 보정/검증 중인 세트는 건드리지 않는다
"""
import json
from pathlib import Path

import pytest

import test_supervisor as ts
from cellbench import config as configmod
from cellbench import supervisor as sup
from cellbench.config import Config
from cellbench.supervisor import tick

P2 = "AA:BB:CC:DD:EE:02"
ENTRY2 = {"id": 2, "plug_mac": P2, "serials": "11594-11617", "registered_at": 1760000000, "plug_ip": "192.168.1.120"}
T = ts.T


def cfg_for(tmp):
    return Config(data_dir=str(tmp), auto_continue=False, sets=[dict(configmod.DEFAULT_SET), dict(ENTRY2)])


class Rig:
    def __init__(self, tmp, engine=None):
        self.tmp, self.engine = tmp, engine or ts.eng(target_last_cycle=4)
        self.world = ts.World(alive={4242: T - 3600})
        self.d = self.world.deps()
        self.reads, self.ons, self.kicks = [], [], []
        self.plug = {"on": True, "w": 68.0}
        self.d.plug_read_mac = lambda mac, ip: (self.reads.append(mac), (self.plug["on"], self.plug["w"]))[1]
        self.d.plug_on_mac = lambda mac, ip: (self.ons.append(mac), self.plug.update(on=True), "켜짐 · 60 W")[2]
        self.d.plug_recharge_mac = lambda mac, ip: (self.kicks.append(mac), self.plug.update(on=True, w=68.0), "켜짐 · 68 W")[2]
        self.prev = None
        self.cfg = cfg_for(tmp)

    def step(self, t, heard=24, med=70.0, alive=True, extra=None):
        now = {"t": t - 5, "beat": t - 5, "phase": "DISCHARGE", "metrics": {}}
        if heard is not None:
            now["metrics"] = {"registered_heard": {"2": heard}, "registered_batt_med": {"2": med}}
        ts.put(self.tmp, self.engine, now, cycles=1)
        if extra:
            extra(self.tmp)
        self.prev, lines = tick(self.cfg, self.tmp, self.prev, self.d, t)
        return self.prev, lines

    def signal(self):
        return self.prev["signals"]["plug_on"]


def test_off_plug_of_unrun_set_is_turned_on_every_ten_minutes(tmp_path):
    r = Rig(tmp_path)
    r.plug.update(on=False, w=0.0)
    rep, lines = r.step(T)
    assert r.ons == [P2] and any("세트 2 플러그 지키기" in l and "켰다" in l for l in lines)
    assert rep["set_guard"]["2"]["action"] == "plug_on" and r.signal()[0] == "yellow"
    r.step(T + 60)
    assert len(r.reads) == 1                                       # 10분 안에는 다시 읽지 않는다
    r.plug.update(on=False, w=0.0)
    r.step(T + 601)
    assert r.ons == [P2, P2] and len(r.reads) == 2


def test_silent_cells_warn_stably_and_never_cycle_the_plug(tmp_path):
    r = Rig(tmp_path)
    r.plug.update(on=True, w=1.5)                                   # 켜짐인데 안 먹이는 Dock — 셀이 안 들리는 동안에는 끊었다 켜지 않는다
    for i in range(0, 14):                                          # 14분 — 아직 15분이 아니다
        r.step(T + i * 60, heard=0, med=None)
    assert not r.prev["set_guard"]["2"]["silent"] and r.signal()[0] == "green"
    r.step(T + 15 * 60 + 5, heard=0, med=None)
    assert r.prev["set_guard"]["2"]["silent"]
    sig = r.signal()
    assert sig == ["yellow", "세트 2 셀이 15분 넘게 안 들림 — 방전돼 꺼졌을 수 있다. Dock 버튼 · 충전 확인"]
    r.step(T + 40 * 60, heard=0, med=None)
    assert r.signal() == sig                                        # 이유 글이 고정 — 점검마다 새 글이 되어 Slack 이 반복되지 않는다
    r.step(T + 3 * 3600, heard=0, med=None)
    assert r.kicks == [] and r.ons == []                            # 끊었다 켜지 않았다 (켜짐이라 켜지도 않았다)
    # 셀이 돌아오면 경고가 풀린다
    r.step(T + 3 * 3600 + 60, heard=24, med=50.0)
    assert not r.prev["set_guard"]["2"]["silent"]
    # 알림기 — 첫 노랑에 Slack 1건
    assert sum("세트 2 셀이 15분 넘게 안 들림" in a for a in r.world.alerts) == 1


def test_silent_set_with_off_plug_is_still_turned_on(tmp_path):
    r = Rig(tmp_path)
    r.plug.update(on=False, w=0.0)
    r.step(T, heard=0, med=None)
    assert r.ons == [P2] and r.kicks == []                          # 꺼짐이면 켜기만 한다


def test_stalled_dock_with_cells_heard_is_kicked_after_three_reads(tmp_path):
    r = Rig(tmp_path)
    kicks_at = []
    for i in range(0, 8):                                           # 10분마다 읽는다 — 3번째 읽기에 끊었다 켜고 세기를 다시 시작한다
        r.plug.update(on=True, w=3.0)
        r.step(T + i * 601, heard=24, med=72.0)
        if len(r.kicks) > len(kicks_at):
            kicks_at.append(i)
    assert kicks_at == [2, 5] and r.signal()[0] == "yellow"
    assert any("끊었다 켰다" in a for a in r.world.alerts)


def test_kick_is_capped_at_two_per_hour_then_red(tmp_path):
    r = Rig(tmp_path)
    t = T + 2 * 601
    for i in range(2):
        r.plug.update(on=True, w=3.0)
        r.step(T + i * 601, heard=24, med=72.0)
    r.prev["memo"]["set_guard"]["2"]["kicks"] = [t - 300, t - 600]    # 최근 1시간에 이미 두 번 끊었다 켰다
    r.plug.update(on=True, w=3.0)
    r.step(t, heard=24, med=72.0)
    assert r.kicks == [] and r.signal()[0] == "red" and "Dock 전원" in r.signal()[1]
    assert r.prev["set_guard"]["2"]["action"] == "capped"


@pytest.mark.parametrize("watts,med,heard", [(1.5, 100.0, 24),      # 만충 바닥 — 셀이 만충이다
                                              (45.0, 70.0, 24),      # 충전 중
                                              (3.0, 70.0, 0),        # 셀이 안 들린다
                                              (3.0, 70.0, None)])    # 들린 수를 모른다(옛 엔진)
def test_no_kick_when_charging_full_or_cells_unknown(tmp_path, watts, med, heard):
    r = Rig(tmp_path)
    for i in range(6):
        r.plug.update(on=True, w=watts)
        r.step(T + i * 601, heard=heard, med=med)
    assert r.kicks == [] and r.ons == []


def test_does_not_touch_sets_the_engine_runs_or_a_verifier_holds(tmp_path):
    r = Rig(tmp_path, engine=ts.eng(target_last_cycle=4, mode="multi", sets=[{"id": 1, "mac": "20:E1:5D:E6:9C:77"}, {"id": 2, "mac": P2, "ip": ""}]))
    r.plug.update(on=False, w=0.0)
    r.step(T)
    assert r.reads == [] and r.ons == []                            # 엔진이 운전하는 세트
    v = Rig(tmp_path / "v", engine=ts.eng(target_last_cycle=4, verifying=[{"id": 2, "mac": P2, "ip": ""}]))
    v.plug.update(on=False, w=0.0)
    v.step(T)
    assert v.reads == [] and v.ons == []                            # 검증기가 그 플러그를 쥔다


def _onboard(step, result=None):
    def f(tmp):
        d = tmp / "set2"
        d.mkdir(exist_ok=True)
        (d / "onboard.json").write_text(json.dumps({"set": 2, "step": step, "result": result}), encoding="utf-8")
    return f


@pytest.mark.parametrize("step,guarded", [("calib", False), ("verify", False), ("derive", False), ("joining", True)])
def test_onboarding_steps_hold_the_plug_except_while_waiting_for_the_boundary(tmp_path, step, guarded):
    r = Rig(tmp_path)
    r.plug.update(on=False, w=0.0)
    r.step(T, extra=_onboard(step))
    assert (r.ons == [P2]) is guarded


def test_finished_onboarding_does_not_block_the_guard(tmp_path):
    r = Rig(tmp_path)
    r.plug.update(on=False, w=0.0)
    r.step(T, extra=_onboard("verify", result="failed"))
    assert r.ons == [P2]


def test_set_guard_state_resets_when_engine_stops(tmp_path):
    r = Rig(tmp_path)
    r.step(T)
    assert "set_guard" in r.prev["memo"]
    ts.put(tmp_path, ts.eng(target_last_cycle=4), ts.now_json(4000), cycles=1)
    r.world.alive.clear()
    rep, _ = tick(r.cfg, tmp_path, r.prev, r.d, T + 600)
    assert "set_guard" not in rep["memo"]                           # 엔진이 안 돌면 plug_guard 가 지킨다


def test_stranded_onboarding_plug_is_turned_on_by_supervisor_when_engine_is_dead(tmp_path):
    """자동 합류가 보정 · 검증 단계에서 엔진이 죽었다 — onboard.json 을 읽어 그 플러그를 켠다(engine.json verifying 이 없어도)."""
    ts.put(tmp_path, ts.eng(target_last_cycle=4), ts.now_json(4000), cycles=1)
    _onboard("calib")(tmp_path)
    w = ts.World()
    d = w.deps()
    seen = []
    d.plug_on_mac = lambda mac, ip: (seen.append(mac), "켜짐")[1]
    tick(cfg_for(tmp_path), tmp_path, None, d, T)
    assert P2 in seen
    # 합류 기다림(joining)은 엔진이 멈춰도 '끊긴 단계'가 아니다 — 이 길로는 켜지 않는다(다른 지키기가 본다)
    assert sup.onboard_stranded(tmp_path, cfg_for(tmp_path)) != []
    _onboard("joining")(tmp_path)
    assert sup.onboard_stranded(tmp_path, cfg_for(tmp_path)) == []


@pytest.mark.parametrize("heard", [1, 11])
def test_few_cells_heard_never_cycle_the_plug(tmp_path, heard):
    """Minor 6 — 셀이 SAFETY['min_cells'](12)대보다 적게 들리면 끊었다 켜지 않는다(켜기만)."""
    r = Rig(tmp_path)
    for i in range(8):
        r.plug.update(on=True, w=3.0)
        r.step(T + i * 601, heard=heard, med=70.0)
    assert r.kicks == []


def test_twelve_cells_heard_are_enough_to_kick(tmp_path):
    r = Rig(tmp_path)
    for i in range(4):
        r.plug.update(on=True, w=3.0)
        r.step(T + i * 601, heard=12, med=70.0)
    assert len(r.kicks) == 1
