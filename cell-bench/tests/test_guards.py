"""안전망 (FMEA P4) — 엔진이 스스로 잡는 고장. 장비 없이 돈다.

순수 판정(guards.py · cells.extract_problem · net.ip_start_problem) → 엔진 배선(가짜 플러그·가짜 셀) →
셀 통신(이 PC 안의 소켓 짝) → 한 사이클 전체를 가짜로 끝까지 돌리는 순서다.
"""
import asyncio
import csv
import json
import re
import socket
import struct
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_cycle
import cellbench.cycle as cycle_mod
import cellbench.plug as plug_mod
from cellbench import guards, net
from cellbench import protocol as P
from cellbench.cells import CellLink, CellLive, CellResult, LiveListener, extract_problem
from cellbench.config import Config
from cellbench.control import StopRequested
from cellbench.cycle import CycleRunner, CycleState
from cellbench.guards import PowerWatch
from cellbench.plug import Plug, PlugReading
from cellbench.record import ALERT_KINDS, EVENT_LIGHT, Recorder

GiB = 1024 ** 3


# ---------- 가짜 장비 ----------

class FakeLive:
    def __init__(self, cells=None):
        self.cells = cells or {}
        self.ip_changes = 0

    def snapshot(self):
        return dict(self.cells)

    def wait_for(self, serials, timeout_s):
        return [s for s in serials if s not in self.cells]


class FakePlug:
    """reads: 읽기마다 꺼낼 값 — W(켜짐) 또는 (켜짐, W). 다 쓰면 마지막 값을 되풀이한다."""

    def __init__(self, reads=(68.0,)):
        self.calls, self.gaps, self.reads = [], [], list(reads)

    def _r(self, v):
        on, w = v if isinstance(v, tuple) else (True, v)
        return PlugReading(on, w, time.time())

    def read(self):
        self.calls.append("read")
        return self._r(self.reads.pop(0) if len(self.reads) > 1 else self.reads[0])

    def on(self):
        self.calls.append("on"); return PlugReading(True, 68.0, time.time())

    def off(self):
        self.calls.append("off"); return PlugReading(False, 0.0, time.time())

    def recharge(self, gap_s=10.0):
        self.calls.append("recharge"); self.gaps.append(gap_s); return PlugReading(True, 68.0, time.time())


class FakeLink:
    def __init__(self, live=None, results=None, charge_to=None):
        self.live, self.results, self.charge_to = live, results or {}, charge_to
        self.resumed = []

    def extract(self, serials, out_dir):
        if self.charge_to is not None:                 # 추출하는 동안 충전이 됐다
            for c in self.live.cells.values():
                c.battery = self.charge_to
        return dict(self.results)

    def resume(self, serials):
        self.resumed.append(list(serials))
        return {s: CellResult(s, "?", resume_s=18.0) for s in serials}


def cell(batt=60, ip="10.0.0.1", t=None, t_wait=0.0):
    return CellLive(ip, batt, 0, 4, -10, t=time.time() + 3600 if t is None else t, t_wait=t_wait)


def mk(tmp_path, live=None, plug=None, link=None, cloud=None, **over):
    over.setdefault("serials", [1, 2, 3])
    cfg = Config(data_dir=str(tmp_path), wifi_reconnect=False, **over)
    r = CycleRunner(cfg, live or FakeLive(), plug or FakePlug(), link, Recorder(tmp_path, cloud=cloud))
    r._disk_free = lambda: 500 * GiB
    return r, cfg


def events(tmp_path):
    with open(tmp_path / "events.csv", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def kinds(tmp_path):
    return [e["kind"] for e in events(tmp_path)]


def now_json(tmp_path):
    return json.loads((tmp_path / "now.json").read_text(encoding="utf-8"))


# ---------- 순수 판정: Dock 저전력 ----------

def test_power_stuck_reading():
    assert guards.power_stuck(False, float("nan"), 10) is True          # 꺼짐
    assert guards.power_stuck(True, 1.4, 10) is True                    # 켜졌는데 Dock 이 안 끌어 씀 (10-10 세트 2)
    assert guards.power_stuck(True, 31.0, 10) is False                  # 만충 뒤 바닥 전력은 정상
    assert guards.power_stuck(True, float("nan"), 10) is False          # 전력을 못 읽은 켜짐은 막힘이 아니다
    assert guards.power_stuck(None, 0.0, 10) is None                    # 못 읽음 = 모름


def test_power_watch_three_tries_then_gives_up_once():
    w = PowerWatch(10.0, 60.0, 30.0, 3)
    assert w.feed(0, True, 1.5) is None                  # 처음 보임 — 시계 시작
    assert w.feed(59, True, 1.5) is None
    assert w.feed(60, True, 1.5) == "recharge" and w.tries == 1
    acts = []
    for k in range(2, 6):
        acts += [w.feed(100 * k, False, 0.0), w.feed(100 * k + 60, False, 0.0)]
    # 한도를 다 쓴 뒤에도 꺼짐이 이어지면 한도와 무관하게 '켜라'(검토 F4) — 예전에는 None 으로 꺼진 채 두었다
    assert acts == [None, "recharge", None, "recharge", None, "give_up", None, "plug_on"]
    assert w.gave_up
    assert w.feed(700, True, 1.5) is None and w.feed(760, True, 1.5) is None   # 켜짐인데 저전력이면 포기한 뒤라 그대로 둔다(dock_power 가 알렸다)


def test_power_watch_resets_when_healthy_and_ignores_unknown():
    w = PowerWatch(10.0, 60.0, 30.0, 3)
    w.feed(0, True, 1.5)
    w.feed(30, True, 68.0)                               # 돌아옴 → 시계를 되돌린다
    assert w.feed(61, True, 1.5) is None                 # 새로 막힌 지 0초
    w2 = PowerWatch(10.0, 60.0, 30.0, 3)
    w2.feed(0, True, 1.5)
    w2.feed(30, None, float("nan"))                      # 못 읽은 표본은 판정을 바꾸지 않는다
    assert w2.feed(60, True, 1.5) == "recharge"


# ---------- 순수 판정: 방전 중 켜진 플러그 · 접촉 불량 · 대기 셀 · 디스크 ----------

def test_manual_plug_action_margin():
    assert guards.manual_plug_action(50, 30, 10) == "revert"
    assert guards.manual_plug_action(41, 30, 10) == "revert"
    assert guards.manual_plug_action(40, 30, 10) == "charge"           # 기준선+10%p 이하 → 그대로 충전
    assert guards.manual_plug_action(None, 30, 10) == "charge"         # 모르면 충전 쪽


def test_not_charging_waits_for_others_then_flags_stuck_cell():
    start = {1: 30, 2: 30, 3: 30, 4: 31, 5: 95}
    early = {1: 35, 2: 34, 3: 30, 4: 35, 5: 96}
    assert guards.not_charging(start, early, 10, 2, 95) == []          # 다른 셀들도 아직 덜 올랐다
    later = {1: 45, 2: 44, 3: 31, 4: 96, 5: 96, 6: 10}
    assert guards.not_charging(start, later, 10, 2, 95) == [3]         # 5 는 이미 95% 이상, 6 은 시작 값이 없다
    assert guards.not_charging({}, later, 10, 2, 95) == []


def test_waiting_due_after_60s_and_once_per_10min():
    waiting = {1: 0.0, 2: 50.0}
    assert guards.waiting_due(waiting, {}, 70.0, 60, 600) == [1]
    assert guards.waiting_due(waiting, {1: 0.0}, 70.0, 60, 600) == []
    assert guards.waiting_due(waiting, {1: 0.0}, 620.0, 60, 600) == [1, 2]


def test_disk_level():
    assert [guards.disk_level(x, 20, 5) for x in (25, 19.9, 4.9)] == [0, 1, 2]


# ---------- 순수 판정: 셀 저장량 ----------

def test_storage_estimate_and_crossings():
    assert guards.storage_now({1: (100.0, 0.0)}, 3600.0, 3.45) == {1: pytest.approx(103.45)}
    new, told = guards.storage_crossings({1: 79, 2: 81, 3: 96}, {}, 80, 95)
    assert new == {2: 1, 3: 2} and told == {1: 0, 2: 1, 3: 2}           # 0 → 빨강은 빨강만
    new, told = guards.storage_crossings({1: 79, 2: 82, 3: 97}, told, 80, 95)
    assert new == {}                                                    # 셀당 한 번
    new, told = guards.storage_crossings({2: 5, 3: 97}, told, 80, 95)    # 2 는 지웠다
    new, told = guards.storage_crossings({2: 85, 3: 97}, told, 80, 95)
    assert new == {2: 1}                                                # 다시 넘으면 또 알린다


def test_storage_full_needs_gap_and_high_estimate():
    pct = {1: 96.0, 2: 96.0, 3: 50.0}
    gaps = {1: 100.0, 2: 1.0, 3: 100.0}
    assert guards.storage_full_cells(pct, gaps, 95, 15) == [1]


def test_storage_summary():
    s = guards.storage_summary({1: (128.0, 0.0), 2: (0.0, 0.0)}, 0.0, 160.0, 3.45)
    assert s["max_pct"] == 80.0 and s["max_serial"] == 1
    assert s["est_full_at"] == round(32 / 3.45 * 3600)
    assert guards.storage_summary({}, 0.0, 160.0, 3.45) == {"max_pct": None, "max_serial": None, "est_full_at": None}


def test_storage_after_extract():
    known = {1: (150.0, 0.0), 2: (150.0, 0.0), 3: (150.0, 0.0)}
    res = {1: CellResult(1, "a", size=150 * guards.MB, deleted=True, t_end=500.0),
           2: CellResult(2, "b", size=120 * guards.MB, t_start=400.0),
           3: CellResult(3, "c", error="라이브 신호 없음")}
    out = guards.storage_after_extract(known, res, 1000.0)
    assert out == {1: (0.0, 500.0), 2: (120.0, 400.0), 3: (150.0, 0.0)}


def test_storage_from_cells_csv_resumes_from_previous_cycles(tmp_path):
    rec = Recorder(tmp_path)
    t_file = time.mktime(time.strptime("20261010_120000", "%Y%m%d_%H%M%S"))
    rec.cells(1, {1: CellResult(1, "a", size=100 * guards.MB, got=100 * guards.MB, ended=True,
                                file="ftg/0001/ftg_1_20261010_120000.bin", t_start=1.0, t_end=2.0),
                  3: CellResult(3, "c", size=90 * guards.MB, got=0, file="ftg/0001/ftg_3_20261010_120000.bin")})
    rec.cells(2, {1: CellResult(1, "a", size=150 * guards.MB, got=150 * guards.MB, ended=True, deleted=True,
                                file="ftg/0002/ftg_1_20261010_120000.bin", t_start=10.0, t_end=70.0),
                  2: CellResult(2, "b", size=0),                         # 크기 0 도 '아는 값'이다
                  3: CellResult(3, "c", error="라이브 신호 없음")})         # 못 받음 → 앞 사이클에서 찾는다
    known = guards.storage_from_cells_csv(tmp_path, [1, 2, 3, 4])
    assert known[1] == (0.0, t_file + 60)                               # 지웠다 — 추출 시작 + 걸린 초
    assert known[2][0] == 0.0
    assert known[3] == (90.0, t_file)
    assert 4 not in known


# ---------- 순수 판정: 추출 · 시작 검사 · 설정 ----------

def test_extract_problem_requires_all_three():
    ok = CellResult(1, "a", size=10000, got=12288, ended=True)
    assert extract_problem(ok) is None
    assert "끝 표지" in extract_problem(CellResult(1, "a", size=10000, got=8192, ended=False))
    assert "오류 블록" in extract_problem(CellResult(1, "a", size=10000, got=12288, ended=True, bad_blocks=1))
    assert "모자람" in extract_problem(CellResult(1, "a", size=10000, got=8192, ended=True))
    assert extract_problem(CellResult(1, "a", error="30초 무응답")) == "30초 무응답"
    assert extract_problem(CellResult(1, "a", size=0)) is None          # 받을 것 없음


def test_ip_start_problem_only_refuses_duplicate():
    assert "다른 PC" in net.ip_start_problem("Duplicate", "192.168.1.100")
    for s in ("Preferred", "Tentative", "", None):
        assert net.ip_start_problem(s, "192.168.1.100") is None


def test_start_problem_checks_config_then_address(tmp_path):
    called = []
    ok = lambda ip: called.append(ip) or "Preferred"
    assert run_cycle.start_problem(Config(), ip_state=ok) is None and called == ["192.168.1.100"]
    assert "다른 PC" in run_cycle.start_problem(Config(), ip_state=lambda ip: "Duplicate")
    called.clear()
    why = run_cycle.start_problem(Config(serials=[11733, 11733]), ip_state=ok)
    assert why.startswith("설정 문제") and "중복" in why and called == []
    run_cycle.config_error(tmp_path, SimpleNamespace(cycles=1, config=None), why, bench_id="b-9")
    e = json.loads((tmp_path / "engine.json").read_text(encoding="utf-8"))
    assert e["exit"] == "config_error" and e["error"] == why and e["bench_id"] == "b-9"


def test_config_defaults_and_old_names(tmp_path):
    cfg = Config()
    assert cfg.delete_after_extract is True                             # 10-10 셀 저장 상한 실측으로 켬
    assert cfg.stuck_rule() == (10.0, 60.0, 30.0, 3)
    assert Config(precharge_stuck_s=5.0).stuck_rule()[1] == 5.0
    old = tmp_path / "run.json"
    old.write_text(json.dumps({"precharge_recover_gap_s": 45, "stuck_max": 2}), encoding="utf-8")
    loaded = Config.load(old, bench_path=None)                          # 옛 --config 파일이 그대로 돈다
    assert loaded.stuck_rule() == (10.0, 60.0, 45, 2)


def test_every_event_kind_has_a_light():
    src = (Path(cycle_mod.__file__)).read_text(encoding="utf-8")
    used = set(re.findall(r'(?:rec\.event\([^,]+, [^,]+, |_ev\(st, |_ev_cells\(st, )"(\w+)"', src))
    used |= {"crash", "aborted", "manual_plug", "plug", "disk", "disk_critical", "cell_storage", "cell_storage_critical"}
    assert used <= set(EVENT_LIGHT), used - set(EVENT_LIGHT)
    assert ALERT_KINDS <= set(EVENT_LIGHT)
    assert set(EVENT_LIGHT.values()) <= {"yellow", "red"}


# ---------- 엔진 배선: Dock 저전력 ----------

def test_charge_stuck_dock_recharges_three_times_then_dock_power(tmp_path):
    r, cfg = mk(tmp_path, stuck_s=0.0)
    st = CycleState(cycle=1); r._phase(st, "CHARGE")
    for _ in range(5):
        r._last_read = PlugReading(True, 1.4, time.time())
        r._guard_power(st)
    assert r.plug.calls.count("recharge") == 3 and r.plug.gaps == [30.0] * 3
    assert kinds(tmp_path).count("plug") == 3 and kinds(tmp_path).count("dock_power") == 1
    assert "충전 중 플러그 1.4 W — 30초 끊었다 켬 (1/3)" in events(tmp_path)[0]["detail"]
    r._publish(st)
    m = now_json(tmp_path)["metrics"]
    assert m["recover_cycle"] == 3 and m["dock_power_fail"] == 1


def test_guard_leaves_plug_alone_after_remote_off_and_in_discharge(tmp_path):
    r, _ = mk(tmp_path, stuck_s=0.0)
    st = CycleState(cycle=1); r._phase(st, "CHARGE")
    r._force = "discharge"                                   # 사람이 원격으로 껐다
    r._last_read = PlugReading(False, 0.0, time.time())
    r._guard_power(st)
    r._force = None; r._phase(st, "DISCHARGE")               # 방전은 꺼짐이 정상
    r._guard_power(st)
    assert r.plug.calls == []


def test_plug_on_settles_stuck_dock_before_extract(tmp_path):
    live = FakeLive({s: cell() for s in (1, 2, 3)})
    r, _ = mk(tmp_path, live=live, plug=FakePlug(reads=[68.0]), stuck_s=0.0, poll_s=0.0)
    st = CycleState(cycle=1); r._phase(st, "PLUG_ON")
    r._last_read = PlugReading(True, 1.4, time.time())      # 켠 뒤 5초 읽기가 1.4 W
    r._settle_power(st)
    assert r.plug.calls == ["recharge", "read"]              # 한 번 끊었다 켜고, 읽어 보니 68 W → 추출로


def test_plug_on_settle_is_bounded_when_dock_never_draws(tmp_path):
    live = FakeLive({s: cell() for s in (1, 2, 3)})
    r, _ = mk(tmp_path, live=live, plug=FakePlug(reads=[1.4]), stuck_s=0.0, poll_s=0.0)
    st = CycleState(cycle=1); r._phase(st, "PLUG_ON")
    r._last_read = PlugReading(True, 1.4, time.time())
    r._settle_power(st)                                      # 영원히 돌지 않고 끝나야 한다
    assert r.plug.calls.count("recharge") == 3 and "dock_power" in kinds(tmp_path)


def test_plug_on_healthy_does_not_wait(tmp_path):
    r, _ = mk(tmp_path)
    st = CycleState(cycle=1); r._phase(st, "PLUG_ON")
    r._last_read = PlugReading(True, 68.0, time.time())
    r._settle_power(st)
    assert r.plug.calls == []


def test_recover_waiting_room_also_recharges(tmp_path, monkeypatch):
    monkeypatch.setattr(cycle_mod.time, "sleep", lambda s: None)
    live = FakeLive({s: cell() for s in (1, 2, 3)})
    r, _ = mk(tmp_path, live=live, plug=FakePlug(reads=[1.4]), stuck_s=0.0, poll_s=0.0)
    n = {"i": 0}

    def control(st):
        n["i"] += 1
        if n["i"] >= 2:
            raise StopRequested()

    r._handle_control = control
    with pytest.raises(StopRequested):
        r._recover(CycleState(cycle=1))
    assert r.plug.calls.count("recharge") == 3               # 안전 상태 1 + 막힌 Dock 복구 2
    assert "복구 대기 중 플러그 1.4 W" in events(tmp_path)[0]["detail"]


def test_precharge_ends_on_remote_plug_off(tmp_path):
    live = FakeLive({s: cell(batt=50) for s in (1, 2, 3)})
    r, _ = mk(tmp_path, live=live, plug=FakePlug(reads=[(False, 0.0)]), stuck_s=0.0, poll_s=0.0)
    r._force = "discharge"                                   # 예비 충전 중 원격 '플러그 끄기'
    r.precharge()
    assert r.plug.calls == ["recharge", "read"] and r._force is None   # 도로 켜지 않고 예비 충전을 끝낸다


# ---------- 엔진 배선: 방전 중 켜진 플러그 ----------

def test_discharge_plug_turned_on_by_someone_is_reverted(tmp_path):
    live = FakeLive({s: cell(batt=60) for s in (1, 2, 3)})
    r, _ = mk(tmp_path, live=live)
    st = CycleState(cycle=1); r._phase(st, "DISCHARGE")
    r._last_read = PlugReading(True, 68.0, time.time())
    r._guard_discharge_plug(st)
    assert r.plug.calls == ["off"] and r._force is None
    e = events(tmp_path)[-1]
    assert e["kind"] == "manual_plug" and "다시 껐다" in e["detail"]
    r._publish(st)
    m = now_json(tmp_path)["metrics"]
    assert m["manual_plug_1h"] == 1 and abs(m["manual_plug_last"] - time.time()) < 60   # 신호등 '사람 조작' 의 마지막 시각


def test_discharge_plug_on_near_threshold_goes_to_charge(tmp_path):
    live = FakeLive({s: cell(batt=b) for s, b in ((1, 60), (2, 38), (3, 70))})
    r, _ = mk(tmp_path, live=live)
    st = CycleState(cycle=1); r._phase(st, "DISCHARGE")
    r._last_read = PlugReading(True, 68.0, time.time())
    r._guard_discharge_plug(st)
    assert r.plug.calls == [] and r._force == "charge" and "manual_plug_charge" in st.note


def test_discharge_plug_on_by_remote_command_or_own_failed_off(tmp_path):
    live = FakeLive({s: cell(batt=60) for s in (1, 2, 3)})
    r, _ = mk(tmp_path, live=live)
    st = CycleState(cycle=1); r._phase(st, "DISCHARGE")
    r._last_read = PlugReading(True, 68.0, time.time())
    r._force = "charge"                                      # 원격 '플러그 켜기' — 사람이 시킨 것
    r._guard_discharge_plug(st)
    assert r.plug.calls == []
    r._force = None; r._cmd_failed = "off"                   # 엔진의 끄기가 실패했던 것 — 사람 탓이 아니다
    r._guard_discharge_plug(st)
    assert r.plug.calls == ["off"] and kinds(tmp_path)[-1] == "plug"


# ---------- 엔진 배선: 대기 셀 · 접촉 불량 · 저장량 · 디스크 · 지표 ----------

def test_waiting_cell_is_resumed_once_per_10_min(tmp_path):
    now = time.time()
    live = FakeLive({1: cell(ip="10.0.0.1", t=now - 120, t_wait=now - 1),      # 2분째 0x09 없음 · 0x16 은 옴
                     2: cell(ip="10.0.0.2"),                                     # 정상
                     3: cell(ip="10.0.0.3", t=now - 120, t_wait=now - 60),      # 0x16 도 끊김 → 꺼진 것, 대기 아님
                     4: cell(ip="10.0.0.4", t=now - 120, t_wait=now - 1)})      # 대기 — 추정 저장량은 가득 참
    link = FakeLink(live)
    r, _ = mk(tmp_path, live=live, link=link, serials=[1, 2, 3, 4])
    r._storage_known = {4: (158.0, now)}
    st = CycleState(cycle=1); r._phase(st, "DISCHARGE")
    r._watch_waiting(st)
    r._watch_waiting(st)
    # 추정만으로는 복귀를 막지 않는다(검토 F7) — 추정은 엔진 밖에서 지운 셀을 모른다. 남의 셀을 깨우는 것은 신원 확인이 막는다
    assert link.resumed == [[1, 4]]
    e = [x for x in events(tmp_path) if x["kind"] == "cell_waiting"]
    assert len(e) == 1 and e[0]["serial"] == "-" and "[1, 4]" in e[0]["detail"]
    r._publish(st)
    assert now_json(tmp_path)["metrics"]["waiting"] == [1, 4]


def test_cell_not_charging_reported_once(tmp_path):
    live = FakeLive({1: cell(batt=45), 2: cell(batt=44), 3: cell(batt=31)})
    r, _ = mk(tmp_path, live=live)
    st = CycleState(cycle=1, charge_start_batt={1: 30, 2: 30, 3: 30}); r._phase(st, "CHARGE")
    r._watch_charging(st); r._watch_charging(st)
    e = [x for x in events(tmp_path) if x["kind"] == "cell_not_charging"]
    assert len(e) == 1 and e[0]["serial"] == "3" and "30→31%" in e[0]["detail"]
    r._publish(st)
    assert now_json(tmp_path)["metrics"]["not_charging"] == [3]


def test_storage_warnings_once_per_cell_and_full_cell(tmp_path):
    now = time.time()
    live = FakeLive({1: cell(), 2: cell(t=now - 100), 3: cell()})
    r, _ = mk(tmp_path, live=live)
    r._storage_known = {1: (130.0, now), 2: (153.0, now), 3: (10.0, now)}
    st = CycleState(cycle=1); r._phase(st, "DISCHARGE")
    r._watch_storage(st); r._watch_storage(st)
    by = {(e["kind"], e["serial"]) for e in events(tmp_path)}
    assert by == {("cell_storage", "1"), ("cell_storage_critical", "2"), ("cell_storage_full", "2")}
    r._storage_known[1] = (0.0, now)                         # 추출 뒤 지웠다
    r._watch_storage(st)
    r._storage_known[1] = (131.0, now)                       # 다시 찼다 → 또 알린다
    r._watch_storage(st)
    assert kinds(tmp_path).count("cell_storage") == 2
    r._publish(st)
    cs = now_json(tmp_path)["metrics"]["cell_storage"]
    assert cs["max_serial"] == 2 and cs["max_pct"] >= 95 and cs["est_full_at"] >= now


def test_engine_start_picks_up_storage_from_previous_csv(tmp_path):
    # 1시간 전 추출, 삭제가 켜져 있던 추출(같은 파일에 지운 셀이 있다) — 믿을 수 있는 기록이라 이어받는다
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(time.time() - 3600))
    Recorder(tmp_path).cells(3, {1: CellResult(1, "a", size=140 * guards.MB, got=0, file=f"ftg_1_{ts}.bin"),
                                 2: CellResult(2, "b", size=150 * guards.MB, deleted=True, file=f"ftg_2_{ts}.bin")})
    r, _ = mk(tmp_path)
    assert r._storage_known[1][0] == 140.0 and r._storage_known[2][0] == 0.0


def test_disk_levels_reported_on_change_only(tmp_path):
    r, _ = mk(tmp_path, disk_check_s=0.0)
    st = CycleState(cycle=1); r._phase(st, "DISCHARGE")
    for free in (100, 15, 15, 3, 30):
        r._disk_free = lambda f=free: f * GiB
        r._watch_disk(st)
    assert kinds(tmp_path) == ["disk", "disk_critical"]
    r._publish(st)
    assert now_json(tmp_path)["metrics"]["disk_free_gb"] == 30.0


def test_metrics_contract_keys_and_cloud_stats(tmp_path):
    class StatsCloud:
        def stats(self):
            return {"enabled": False}

        def __getattr__(self, name):
            return lambda *a, **k: None

    live = FakeLive({1: cell()}); live.ip_changes = 5
    r, _ = mk(tmp_path, live=live, cloud=StatsCloud())
    st = CycleState(cycle=1, ip_base=3); r._phase(st, "DISCHARGE")
    m = now_json(tmp_path)["metrics"]
    assert set(m) == {"plug", "recover_cycle", "dock_power_fail", "manual_plug_1h", "manual_plug_last", "ip_changes_cycle", "reconnects_24h",
                      "live_gaps_1h", "events_1h", "disk_free_gb", "cell_storage", "not_charging", "waiting", "cloud"}
    assert m["ip_changes_cycle"] == 2 and m["cloud"] == {"enabled": False} and m["plug"] == {}


def test_metrics_failure_never_stops_now_json(tmp_path):
    r, _ = mk(tmp_path)
    r._metrics = lambda st: 1 / 0
    r._publish(CycleState(cycle=1))
    assert "ZeroDivisionError" in now_json(tmp_path)["metrics"]["error"]     # now.json 은 써졌다 (감시자의 심박)


def test_recorder_counts_recent_events_across_restart(tmp_path):
    now = time.time()
    rows = [(now - 600, "manual_plug"), (now - 7200, "live_gap"), (now - 30 * 3600, "wifi_reconnect")]
    rec = Recorder(tmp_path)
    with open(tmp_path / "events.csv", "a", newline="", encoding="utf-8-sig") as f:
        for t, k in rows:
            csv.writer(f).writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), 1, "DISCHARGE", k, "-", "x"])
    rec2 = Recorder(tmp_path)                                 # 엔진이 다시 떴다
    assert rec2.count({"manual_plug"}, 3600) == 1
    assert rec2.count({"live_gap"}, 3600) == 0 and rec2.count({"live_gap"}, 86400) == 1
    assert rec2.count(None, 86400) == 2                       # 30시간 전 것은 24시간 창 밖
    rec2.event(1, "CHARGE", "wifi_reconnect", "-", "재연결 성공")
    assert rec2.count({"wifi_reconnect"}, 86400) == 1


def test_safety_check_errors_do_not_break_the_cycle(tmp_path):
    r, _ = mk(tmp_path)
    st = CycleState(cycle=1)

    def boom(st):
        raise OSError("disk gone")

    r._safe(st, boom)                                        # 예외가 밖으로 나오면 안 된다
    assert "안전망 boom 건너뜀" in (tmp_path / "run.log").read_text(encoding="utf-8")
    with pytest.raises(StopRequested):
        r._safe(st, lambda st: (_ for _ in ()).throw(StopRequested()))


# ---------- 셀 통신 ----------

def _live_packet(serial, battery=77):
    d = bytearray(P.LIVE_LEN); d[:4] = P.HEADER; d[4] = P.MSG_LIVE
    struct.pack_into("<H", d, 11, serial); d[52] = battery
    return bytes(d)


def test_live_listener_counts_ip_changes():
    L = LiveListener.__new__(LiveListener)                   # 포트를 열지 않는다
    L.cells, L._lock, L.ip_changes = {}, threading.Lock(), 0
    pkt = _live_packet(11733)
    L._handle(pkt, "192.168.1.120", 1.0)
    L._handle(pkt, "192.168.1.120", 2.0)
    L._handle(pkt, "192.168.1.121", 3.0)                     # DHCP 로 주소가 바뀜
    L._handle(b"FITO" + bytes([P.MSG_WAIT_FOR_TCP]) + b"\x00" * 4, "192.168.1.121", 4.0)
    assert L.ip_changes == 1 and L.cells[11733].ip == "192.168.1.121" and L.cells[11733].t_wait == 4.0


def _fake_cell(cell_sock, status_size, blocks, seen):
    """셀 흉내 — 인사 · 상태 · 블록 · 끝 표지를 보내고 PC 의 명령 종류를 seen 에 남긴다. 0x26 을 받으면 끊는다."""
    def frame():
        got = b""
        while len(got) < 54:                                  # PC 명령 프레임은 54 바이트
            x = cell_sock.recv(54 - len(got))
            if not x:
                return None
            got += x
        seen.append(got[4]); return got[4]

    cell_sock.sendall(b"\x00" * 46)
    while True:
        kind = frame()
        if kind is None or kind == P.MSG_RESET_NORMAL:
            break
        if kind == P.MSG_STATUS:
            cell_sock.sendall(P.HEADER + bytes([P.MSG_STATUS, 0, 0xF0, 88]) + struct.pack("<i", status_size) + b"\x00")
        elif kind == P.MSG_UPLOAD:
            cell_sock.sendall(blocks); cell_sock.sendall(P.MARK)
        elif kind == P.MSG_DELETE:
            cell_sock.sendall(P.HEADER + bytes([P.MSG_DELETE, 0]))
    cell_sock.close()


def _blocks(n):
    data = bytes(range(256)) * 16
    return b"".join(struct.pack("<II", i * P.BLOCK_DATA, sum(data)) + data for i in range(n))


@pytest.mark.parametrize("size,deleted", [(3 * P.BLOCK_DATA - 50, True), (3 * P.BLOCK_DATA + 100, False)])
def test_delete_only_when_received_bytes_cover_status_size(tmp_path, size, deleted):
    link = CellLink(Config(resume_timeout_s=1), SimpleNamespace(wait_resume=lambda s, since, t: 1.0), log=lambda m: None)
    pc, cell_sock = socket.socketpair()
    seen = []
    th = threading.Thread(target=_fake_cell, args=(cell_sock, size, _blocks(3), seen)); th.start()
    res = CellResult(11733, "127.0.0.1")
    link._serve(pc, res, extract=True, out_dir=tmp_path, verify=lambda: None)   # 신원 확인은 필수 인자다 — 검사는 허용을 명시로
    th.join(5)
    assert res.ended and res.bad_blocks == 0
    assert res.deleted is deleted and (P.MSG_DELETE in seen) is deleted
    assert seen[-1] == P.MSG_RESET_NORMAL


def test_resume_path_sends_only_reset(tmp_path):
    link = CellLink(Config(resume_timeout_s=1), SimpleNamespace(wait_resume=lambda s, since, t: 2.0), log=lambda m: None)
    pc, cell_sock = socket.socketpair()
    seen = []
    th = threading.Thread(target=_fake_cell, args=(cell_sock, 1000, b"", seen)); th.start()
    res = CellResult(11733, "127.0.0.1")
    link._serve(pc, res, extract=False, out_dir=None, status=False, verify=lambda: None)
    th.join(5)
    assert seen == [P.MSG_RESET_NORMAL] and res.resume_s == 2.0 and res.error is None


def test_cell_link_resume_runs_groups_without_status():
    link = CellLink(Config(extract_batch=2), SimpleNamespace(), log=lambda m: None)
    calls = []
    link._run_group = lambda serials, extract, out_dir, status=True: (calls.append((serials, extract, status))
                                                                      or {s: CellResult(s, "?") for s in serials})
    out = link.resume([1, 2, 3])
    assert calls == [([1, 2], False, False), ([3], False, False)] and sorted(out) == [1, 2, 3]


# ---------- 플러그 기록 ----------

def test_plug_counts_real_toggles_and_failed_tries(tmp_path, monkeypatch):
    monkeypatch.setattr(plug_mod.keyring, "get_password", lambda svc, name: "x")    # 실제 자격 증명은 읽지 않는다
    monkeypatch.setattr(plug_mod.time, "sleep", lambda s: None)
    stats = tmp_path / "plug_stats.json"
    stats.write_text(json.dumps({"toggles_total": 10}), encoding="utf-8")
    p = Plug(Config(plug_retries=2, plug_call_timeout_s=1), stats_path=stats)
    state = {"on": True, "fail": 0}

    async def do(action):
        if state["fail"]:
            state["fail"] -= 1
            raise OSError("놓침")
        if action in ("on", "off"):
            state["on"] = action == "on"
        return PlugReading(state["on"], 50.0 if state["on"] else 0.0, time.time())

    p._do = do
    p.read()                    # 켜져 있음을 안다
    p.on()                      # 이미 켜짐 — 릴레이가 움직이지 않는다
    p.recharge(gap_s=0)         # 끔 + 켬 = 2
    state["fail"] = 1
    p.off()                     # 한 번 놓치고 두 번째에 꺼짐 = 1
    assert p.toggles_total == 13
    assert json.loads(stats.read_text(encoding="utf-8"))["toggles_total"] == 13
    m = p.metrics()
    assert m["retries_1h"] == 1 and m["toggles_total"] == 13 and m["last_call_s"] is not None
    state["fail"] = 5
    with pytest.raises(RuntimeError):
        p.read()
    assert p.metrics()["retries_1h"] == 3


# ---------- 한 사이클 전체 (가짜 장비로 끝까지) ----------

def test_full_cycle_wiring_with_fakes(tmp_path, monkeypatch):
    """방전 첫 표본에서 누가 켠 플러그(최저 38% → 그대로 충전으로) → 플러그 ON → 추출(1 지움 · 2 덜 받음 · 3 안 들림) → 만충."""
    monkeypatch.setattr(cycle_mod.time, "sleep", lambda s: None)
    live = FakeLive({s: cell(batt=38, ip=f"10.0.0.{s}") for s in (1, 2, 3)})
    results = {1: CellResult(1, "10.0.0.1", size=4000, got=4096, ended=True, deleted=True, resume_s=17.0, t_end=time.time()),
               2: CellResult(2, "10.0.0.2", size=10000, got=8192, ended=True, resume_s=17.0, t_start=time.time()),
               3: CellResult(3, "?", error="라이브 신호 없음")}
    link = FakeLink(live, results, charge_to=100)
    plug = FakePlug(reads=[(True, 60.0), 31.0])
    r, cfg = mk(tmp_path, live=live, plug=plug, link=link, full_flat_min=0.002, poll_s=0.0)
    st = r.run_cycle()
    assert st.note == "manual_plug_charge"
    assert plug.calls[0] == "off" and "recharge" not in plug.calls       # 31 W 는 정상 바닥 — 끊었다 켜지 않는다
    ev = events(tmp_path)
    assert [e["serial"] for e in ev if e["kind"] == "extract"] == ["2", "3"]
    assert "모자람" in [e for e in ev if e["kind"] == "extract"][0]["detail"]
    assert "manual_plug" in kinds(tmp_path)
    assert r._storage_known[1][0] == 0.0 and r._storage_known[2][0] == pytest.approx(10000 / guards.MB)
    row = (tmp_path / "cycles.csv").read_text(encoding="utf-8-sig").splitlines()[1]
    assert "manual_plug_charge" in row
    assert "metrics" in now_json(tmp_path)


def test_full_cycle_recovers_stuck_dock_at_plug_on_and_in_charge(tmp_path, monkeypatch):
    """켠 직후 1.4 W(추출 전에 끊었다 켬) · 추출 뒤 충전 첫 표본 1.4 W(충전 루프가 이어 잡음) — 10-10 세트 2 의 증상."""
    monkeypatch.setattr(cycle_mod.time, "sleep", lambda s: None)
    live = FakeLive({s: cell(batt=30, ip=f"10.0.0.{s}") for s in (1, 2, 3)})
    link = FakeLink(live, {}, charge_to=100)
    plug = FakePlug(reads=[1.4, 68.0, 1.4, 68.0])            # 켠 뒤 읽기 · 복구 뒤 표본 · 충전 첫 표본 · 그 뒤
    r, _ = mk(tmp_path, live=live, plug=plug, link=link, full_flat_min=0.002, poll_s=0.0, stuck_s=0.0)
    r.run_cycle()
    assert plug.calls.count("recharge") == 2
    details = [e["detail"] for e in events(tmp_path) if e["kind"] == "plug"]
    assert any(d.startswith("플러그 켜기 중 플러그 1.4 W — 30초 끊었다 켬 (1/3)") for d in details)
    assert any(d.startswith("충전 중 플러그 1.4 W — 30초 끊었다 켬 (1/3)") for d in details)
