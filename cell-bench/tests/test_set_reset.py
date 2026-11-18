"""규칙 D-2 — 세트 일부 꺼짐 → 전체 방전 리셋 (자동 복구 기획 5절 · 커밋 6). 장비 없이 돈다.

한 세트는 모두 같은 상태로 움직인다(사용자 결정 3). 일부가 15분 넘게 안 들리면 — 스위치가 켜져 있으면 그 세트를 모두 꺼지게 한 뒤
Dock 으로 전체를 켜고(리셋, 목표 사이클에 세지 않는다), 스위치가 꺼져 있으면(지금 운영 기본값) 그 세트만 세트 정지 + 사람 호출
"꺼진 셀의 전원 버튼을 하나씩(Dock 버튼은 누르지 않는다)". 사람이 켜서 다 들리면 저절로 이어진다.
감독 루프(CycleRunner.run)를 가짜 세상에서 끝까지 돌린다.
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
from cellbench.config import SAFETY, Config
from cellbench.cycle import CycleRunner
from cellbench.record import ALERT_KINDS, EVENT_LIGHT, Recorder, cycles_counted, cycles_done
from test_cell_wake import DOCK_ON, FakeDock, OffLive, OffWorld
from test_supervisor import T, World as SupWorld, eng

OFF = (11740, 11751)                       # 처음부터 꺼져 있는 셀 둘 — 기획 E 표의 예와 같은 시리얼


# ---------- 순수 판정 ----------

def test_partial_off_needs_silent_and_alive_and_skips_full_storage():
    seen = {1: 1000.0, 2: 1000.0, 3: 0.0, 4: 50.0}
    assert guards.partial_off(seen, 1000.0, 900, 300) == ([3, 4], [1, 2])
    assert guards.partial_off(seen, 1000.0, 900, 300, skip={3, 4}) == ([], [1, 2])     # 저장 가득 참 — 켜진 셀이다(가7)
    assert guards.partial_off({1: 0.0, 2: 0.0}, 1000.0, 900, 300) == ([], [])          # 모두 안 들림은 복구 대기의 몫
    assert guards.partial_off({1: 200.0, 2: 1000.0}, 1000.0, 900, 300) == ([], [2])    # 15분이 안 됐다


def test_reset_verdict_order():
    assert guards.reset_verdict(True, [], 0, SAFETY) == "reset"
    assert guards.reset_verdict(True, [11740], 0, SAFETY) == "contact"                 # 접촉 고장 — 리셋해도 충전되지 않는다
    assert guards.reset_verdict(False, [], 0, SAFETY) == "switch"
    assert guards.reset_verdict(True, [], 1, SAFETY) == "day"                          # 하루 1번 (I12)


def test_reset_all_off_needs_silence_and_low_last_battery_except_first_off():
    seen = {1: 0.0, 2: 0.0, 3: 0.0}
    assert guards.reset_all_off(seen, {1: 2, 2: 4}, [3], 1000.0, 300, 5) == (True, "")
    ok, why = guards.reset_all_off(seen, {1: 2, 2: 40}, [3], 1000.0, 300, 5)
    assert not ok and "2 40 %" in why and "망 문제일 수 있다" in why                    # 40 % 에서 사라졌다 — 망 끊김과 가른다
    ok, why = guards.reset_all_off({1: 0.0, 2: 900.0, 3: 0.0}, {1: 2, 2: 2}, [3], 1000.0, 300, 5)
    assert not ok and why == "1대가 아직 들린다"


def test_cycles_counted_excludes_reset_rows_only(tmp_path):
    p = tmp_path / "cycles.csv"
    p.write_text("cycle,start,full_at,note\n1,x,y,\n2,x,,partial_off\n3,x,,reset\n4,x,,reset deep_discharge\n5,x,y,"
                 "reset_cancelled_typo\n", encoding="utf-8-sig")
    assert cycles_done(p) == 5 and cycles_counted(p) == 3                               # 번호는 줄 수 그대로, 목표 셈만 뺀다
    assert cycles_counted(tmp_path / "없음.csv") == 0


def test_supervisor_counts_like_the_engine(tmp_path):
    (tmp_path / "engine.json").write_text(json.dumps(eng(exit="done", target_last_cycle=4)), encoding="utf-8")
    (tmp_path / "cycles.csv").write_text("cycle,start,note\n1,x,\n2,x,reset deep_discharge\n3,x,\n", encoding="utf-8-sig")
    rep, _ = sup.tick(Config(data_dir=str(tmp_path), auto_continue=False), tmp_path, None, SupWorld().deps(), T)
    assert rep["engine"]["cycles_done"] == 2


def test_kinds():
    assert EVENT_LIGHT["set_reset"] == EVENT_LIGHT["set_reset_done"] == "yellow"
    assert {"set_reset", "set_reset_done"} <= ALERT_KINDS


# ---------- 감독 루프를 가짜 세상에서 ----------

class DyingWorld(OffWorld):
    """셀이 잔량 0.5 % 이하로 내려가면 꺼진다(라이브가 끊긴다)."""

    def integrate(self, dt):
        super().integrate(dt)
        for s, b in self.docks[0].batt.items():
            if b <= 0.5:
                self.off.add(s)


def reset_world(monkeypatch, tmp_path, switch=True, limit_h=12.0):
    w = DyingWorld([sw.Dock(sw.SET1_MAC, sw.SET1)], rate_pct_min=2.0, discharge_rate=1.0, limit_h=limit_h)
    w.off = set(OFF)
    for s in OFF:
        w.docks[0].batt[s] = 0.0
    sw.patch_world(monkeypatch, w)
    data = tmp_path / "data"
    cfg = Config(data_dir=str(data), wifi_reconnect=False, auto_continue=False,
                 sets=[{"id": 1, "plug_mac": sw.SET1_MAC, "serials": "11733-11756", "dock": dict(DOCK_ON, power_on=switch)}])
    live = OffLive(w)
    r = CycleRunner(cfg, live, sw.FakePlug(w, cfg), sw.FakeLink(w, cfg, live), Recorder(data))
    made = []

    def factory():
        made.append(w.clock.t)
        return FakeDock(w, live)
    r._dock_factory = factory
    return w, r, data, made


def rows(data, name):
    with open(data / name, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_switch_on_partial_off_resets_the_set_then_wakes_all_and_continues(tmp_path, monkeypatch):
    """기획 8절 — 일부 꺼짐 15분 → 리셋(플러그 끈 채 모두 꺼질 때까지, 기준선 30 % 를 넘어) → 모두 꺼짐 확인 → D-1 켜기 → 예비 충전 → 재개.
    리셋 사이클은 'reset deep_discharge' 줄이고 목표에 세지 않는다."""
    w, r, data, made = reset_world(monkeypatch, tmp_path)
    assert r.run(1) == "done"
    ev = rows(data, "events.csv")
    ks = [e["kind"] for e in ev if e["kind"] not in ("live_gap", "missing_cells")]
    assert ks[:5] == ["set_reset", "set_reset_done", "cells_off", "dock_wake", "cells_woke"]
    assert len(w.wakes) == 1 and w.wakes[0][3] == []                     # 모두 꺼진 뒤에만 눌렀다 (I8)
    d = {e["kind"]: e["detail"] for e in ev}
    assert "셀 11740 · 11751 가 15분 넘게 안 들린다 — 나머지 22대는 들린다" in d["set_reset"]
    assert "목표 사이클에 세지 않는다" in d["set_reset"] and "사람이 할 일: 없음" in d["set_reset"]
    assert "24대가 모두 꺼졌다 — 리셋 방전" in d["set_reset_done"]
    assert "리셋으로 모두 꺼짐을 확인했다" in d["cells_off"]                    # 단일 세트 — 망 증거 대신 리셋의 확인을 쓴다
    assert "24/24대가 다시 들린다" in d["cells_woke"]
    assert not [e for e in ev if e["kind"] == "live_gap" and e["phase"] == "RESET"]   # 꺼지는 것이 목적 — 셀마다 끊김을 남기지 않는다
    cyc = rows(data, "cycles.csv")
    assert [c["note"] for c in cyc] == ["reset deep_discharge", ""]
    assert cyc[0]["full_at"] == "" and cyc[0]["discharge_end"] and cyc[1]["full_at"]
    assert r.rec.cycles_done() == 1                                      # 리셋 줄은 목표에 세지 않는다 (G12)
    holds = [(h["event"], h["phase"]) for h in rows(data, "holds.csv")]
    assert holds == [("enter", "RESET"), ("leave", "RESET"), ("enter", "RECOVER"), ("leave", "RECOVER")]
    reset_samples = [s for s in rows(data, "samples_0001.csv") if s["phase"] == "RESET"]
    assert reset_samples and min(int(s["batt_min"] or 99) for s in reset_samples) < SAFETY["off_low_pct"]   # 30 % 를 넘어 방전했다
    assert all(s["plug"] == "off" for s in reset_samples)


def test_switch_off_holds_only_this_set_and_asks_for_power_buttons_then_continues(tmp_path, monkeypatch):
    """지금 운영의 기본 길 — Dock 은 건드리지 않는다. 지금 사이클을 접고(partial_off) 세트 정지(충전 유지) + 사람 호출.
    사람이 두 셀을 켜면 저절로 예비 충전부터 이어 간다."""
    w, r, data, made = reset_world(monkeypatch, tmp_path, switch=False)
    w.at(60 * 60, lambda: w.off.difference_update(OFF))                  # 사람이 두 셀의 전원 버튼을 눌렀다
    assert r.run(1) == "done"
    assert made == [] and w.wakes == []
    ev = [e for e in rows(data, "events.csv") if e["kind"] not in ("live_gap", "missing_cells")]
    assert [e["kind"] for e in ev][:2] == ["need_human", "cells_woke"]
    text = ev[0]["detail"]
    assert "셀 11740 · 11751 가 15분 넘게 안 들린다 — 나머지 22대는 켜져 있다" in text
    assert "세트 정지(플러그 켜짐 · 충전 유지)" in text and "Dock 명령 스위치(dock.power_on)가 꺼져 있다" in text
    assert "사람이 할 일: 셀 11740 · 11751 의 전원 버튼을 하나씩 눌러 켠다(Dock 버튼은 누르지 않는다 — 켜진 셀이 꺼진다)" in text
    cyc = rows(data, "cycles.csv")
    assert [c["note"] for c in cyc] == ["partial_off", ""] and cyc[0]["full_at"] == ""
    holds = [(h["event"], h["phase"]) for h in rows(data, "holds.csv")]
    assert holds == [("enter", "HOLD"), ("leave", "HOLD")]
    assert "set_reset" not in [e["kind"] for e in ev]


def test_reset_cancelled_by_a_human_plug_on_then_no_second_reset_for_24h(tmp_path, monkeypatch):
    """리셋 중 사람이 플러그를 켜면 사람 뜻대로 취소하고 충전으로 간다. 같은 셀이 그대로면 24시간 리셋하지 않고 세트 정지 + 사람 호출."""
    w, r, data, made = reset_world(monkeypatch, tmp_path)
    w.at(30 * 60, lambda: w.plugs.__setitem__(sw.SET1_MAC, True))
    w.at(4 * 3600, lambda: w.off.difference_update(OFF))
    assert r.run(1) == "done"
    ev = [e for e in rows(data, "events.csv") if e["kind"] not in ("live_gap", "missing_cells")]
    ks = [e["kind"] for e in ev]
    assert ks[:4] == ["set_reset", "set_reset_done", "need_human", "cells_woke"] and ks.count("set_reset") == 1
    assert "리셋 취소" in ev[1]["detail"] and "24시간 리셋하지 않는다" in ev[1]["detail"]
    assert "이미 리셋했다" in ev[2]["detail"] and "Dock 버튼은 누르지 않는다(켜진 22대가 꺼진다)" in ev[2]["detail"]
    assert made == []
    assert [c["note"] for c in rows(data, "cycles.csv")] == ["reset deep_discharge reset_cancelled", ""]


def test_cells_coming_back_late_after_a_long_outage_are_not_partial_off(tmp_path, monkeypatch):
    """망이 15분 넘게 끊겼다 돌아올 때 늦게 붙는 셀을 일부 꺼짐으로 읽지 않는다 — 세트가 다시 들린 지 15분이 지나야 판정한다.
    (복구 대기는 한 대라도 30초 들리면 끝나므로, 이 조건이 없으면 늦게 붙는 셀 때문에 곧바로 리셋 · 사람 호출이 났다)"""
    from cellbench.cycle import CycleState, PartialOff
    w, r, data, made = reset_world(monkeypatch, tmp_path)
    st = CycleState(cycle=1, phase="DISCHARGE")
    w.off = set()
    r._watch_partial(st)                                                   # 모두 들린다
    w.off = set(sw.SET1)
    w.clock.sleep(20 * 60)                                                 # 20분 동안 모두 안 들림 (망 끊김)
    r._note_cells(w.clock.t)
    w.off = set(OFF)                                                       # 22대가 먼저 붙었다 — 두 대는 20분째 안 들림
    r._watch_partial(st)
    w.clock.sleep(60)
    r._watch_partial(st)
    w.off = set()                                                          # 마저 붙었다 — 아무 일도 없다
    w.clock.sleep(60)
    r._watch_partial(st)
    w.off = set(OFF)                                                       # 이번에는 정말 두 대가 꺼졌다
    w.clock.sleep(16 * 60)
    with pytest.raises(PartialOff):
        r._watch_partial(st)


def test_contact_fault_cell_is_not_reset(tmp_path, monkeypatch):
    w, r, data, made = reset_world(monkeypatch, tmp_path, limit_h=0.5)
    r._not_charging_t[11740] = sw.T0                                       # 이 셀은 충전이 안 오르던 셀이다(Dock 자리 접촉)
    with pytest.raises(sw.Stop):
        r.run(1)
    human = [e["detail"] for e in rows(data, "events.csv") if e["kind"] == "need_human"]
    assert len(human) == 1 and "Dock 자리 접촉 의심" in human[0] and "리셋하지 않고" in human[0]
    assert made == [] and "set_reset" not in [e["kind"] for e in rows(data, "events.csv")]


# ---------- 검토 보강 (커밋 9) ----------

def _stuck_off(w, r, after_s, n):
    """after_s 뒤의 끄기 n 번은 '꺼짐'으로 답하지만 릴레이가 그대로다(켜짐으로 읽힌다). n 이 None 이면 끝까지."""
    real = r.plug.off
    left = {"n": n}

    def off():
        if w.clock.t - sw.T0 >= after_s and (left["n"] is None or left["n"] > 0):
            if left["n"] is not None:
                left["n"] -= 1
            r.plug._log("off")
            from cellbench.plug import PlugReading
            return PlugReading(False, 0.0, w.clock.t)
        return real()
    r.plug.off = off


def test_reset_from_charge_side_is_not_cancelled_by_a_relay_that_did_not_turn_off(tmp_path, monkeypatch):
    """충전 중 일부 꺼짐 → 리셋. 끄기가 '꺼짐'으로 답했는데 릴레이가 안 바뀌어 켜짐으로 읽혀도, 꺼짐을 한 번도 못 봤으므로 사람이 켠 것으로
    보지 않고 다시 끈다. 하루 1번 리셋을 헛되이 쓰지 않는다."""
    w, r, data, made = reset_world(monkeypatch, tmp_path)
    w.off = set()
    for c in OFF:
        w.docks[0].batt[c] = 80.0                                         # 처음에는 모두 켜져 있다
    w.at(55 * 60, lambda: w.off.update(OFF))                               # 충전에 들어선 뒤 두 대가 꺼졌다
    _stuck_off(w, r, 60 * 60, 2)
    assert r.run(1) == "done"
    ev = [e for e in rows(data, "events.csv") if e["kind"] in ("set_reset", "set_reset_done")]
    assert [e["kind"] for e in ev] == ["set_reset", "set_reset_done"] and "모두 꺼졌다" in ev[1]["detail"]
    assert ev[0]["phase"] == "CHARGE"
    notes = [c["note"] for c in rows(data, "cycles.csv")]
    assert notes[:2] == ["reset", "reset deep_discharge"] and not any("cancel" in n for n in notes)


def test_reset_with_a_plug_that_never_turns_off_holds_the_set_and_calls_a_human(tmp_path, monkeypatch):
    w, r, data, made = reset_world(monkeypatch, tmp_path)
    w.off = set()
    for c in OFF:
        w.docks[0].batt[c] = 80.0                                         # 처음에는 모두 켜져 있다
    w.at(55 * 60, lambda: w.off.update(OFF))
    _stuck_off(w, r, 60 * 60, None)
    w.at(4 * 3600, lambda: w.off.difference_update(OFF))                   # 사람이 와서 고치고 두 셀을 켰다
    assert r.run(1) == "done"
    ev = [e for e in rows(data, "events.csv") if e["kind"] in ("set_reset", "set_reset_done", "need_human", "cells_woke")]
    assert [e["kind"] for e in ev] == ["set_reset", "set_reset_done", "need_human", "cells_woke"]
    assert "리셋 멈춤 — 플러그를 3번 더 껐는데도 켜짐으로 읽힌다" in ev[1]["detail"]
    t = ev[2]["detail"]
    assert t.index("자동으로 한 일") < t.index("사람이 할 일") and "플러그(릴레이)" in t and "Dock 버튼은 누르지 않는다" in t
    assert any("reset_plug_stuck" in c["note"] for c in rows(data, "cycles.csv"))


def test_contact_fault_cells_survive_an_engine_restart(tmp_path, monkeypatch):
    """접촉 고장 셀 기억을 그 세트의 events.csv 지난 24시간에서 이어받는다 — 엔진이 자주 다시 떠도 고칠 수 없는 셀 때문에 리셋하지 않게."""
    import time as _t
    data = tmp_path / "data"
    data.mkdir(parents=True)
    ts = lambda dt: _t.strftime("%Y-%m-%d %H:%M:%S", _t.gmtime(sw.T0 + dt + 9 * 3600))   # 가상 시계는 KST
    (data / "events.csv").write_text(
        "﻿time,cycle,phase,kind,serial,detail\n"
        f"{ts(-3600)},4,CHARGE,cell_not_charging,-,\"충전 중 다른 셀들은 10%p 넘게 올랐는데 11740 31→32%, 11751 30→30% — 2%p 도 못 오름\"\n"
        f"{ts(-48 * 3600)},2,CHARGE,cell_not_charging,11745,충전 중 다른 셀들은 10%p 넘게 올랐는데 11745 31→31% — 2%p 도 못 오름\n",
        encoding="utf-8")
    w, r, data, made = reset_world(monkeypatch, tmp_path, limit_h=0.5)
    assert set(r._not_charging_t) == {11740, 11751}                        # 48시간 전 줄은 빠진다
    with pytest.raises(sw.Stop):
        r.run(1)
    human = [e["detail"] for e in rows(data, "events.csv") if e["kind"] == "need_human"]
    assert len(human) == 1 and "Dock 자리 접촉 의심" in human[0]
    assert made == [] and "set_reset" not in [e["kind"] for e in rows(data, "events.csv")]
