"""시험대가 사람 없이 놀지 않게 (2026-10-11 사고, PR #8) — 세 가지를 순수 판정과 가짜 장비 끝까지로 본다.

  1. 자동 이어 가기 — 시험 목표 target_cycles(500)에 닿기 전에는 done 으로 끝나지 않고, 닿으면 끝난다. --cycles 는 '이번 실행 최소'.
     누적이 progress_notify_every 의 배수에 닿으면 progress(노랑 · Slack 1건). 감시자는 남은 사이클을 target − 누적으로 세고,
     done 인데 누적 < 목표면 되살린다(옛 코드 엔진).
  2. 예비 충전 조건 — 모든 셀 95% 이상이면 건너뛴다 · 바닥 평탄 + 95% 이상이면 만충으로 · 바닥 평탄 + 95% 미만이면 dock_no_recharge.
  3. 진척 없음 경고 — 단계 체류가 기대의 1.5배면 노랑, 3배면 빨강. 감시자가 Slack 1건.

10-11 사고: 12:20 사이클 4 끝 → exit=done 으로 4시간 21분 공회전. 16:42 --precharge 시작 → 셀 96~97% 에서 Dock 이 재충전하지 않아
(27.7 W 평탄) 2시간 39분 PRECHARGE 에 머무는 동안 9차선 전부 '정상'. 19:21 사람이 플러그 끄기로 겨우 넘겼다.
"""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sim_world as sw
import test_health as th
import test_supervisor as ts
from cellbench import health as H
from cellbench.cells import CellLive
from cellbench.config import HEALTH, Config
from cellbench.cycle import CycleRunner, CycleState, cells_at_least, floor_flat, precharge_verdict
from cellbench.plug import PlugReading
from cellbench.record import Recorder, avg_discharge_h
from cellbench.supervisor import Plan, remaining_cycles, restart_plan, tick


def _flat(start, minutes, w, step=20):
    return [(start + i * step, w) for i in range(int(minutes * 60 / step) + 1)]


# ---------- 2. 예비 충전 — 순수 판정 ----------

def test_floor_flat_needs_window_and_all_samples_at_floor():
    t0 = 1000.0
    assert floor_flat(_flat(t0, 12, 27.7), t0 + 12 * 60, 10, 35.0) is True
    assert floor_flat(_flat(t0, 12, 36.0), t0 + 12 * 60, 10, 35.0) is False        # 바닥보다 높다 (충전 중)
    assert floor_flat(_flat(t0, 5, 27.7), t0 + 5 * 60, 10, 35.0) is False          # 아직 10분이 안 됐다
    mixed = _flat(t0, 12, 27.7)[:-2] + [(t0 + 12 * 60 - 20, 68.0), (t0 + 12 * 60, 27.7)]
    assert floor_flat(mixed, t0 + 12 * 60, 10, 35.0) is False                       # 창 안에 충전 표본이 하나라도 있으면 아니다
    assert floor_flat([(t0, 27.7), (t0 + 20, 27.7)], t0 + 12 * 60, 10, 35.0) is False   # 표본 3개 미만


def test_cells_at_least_requires_every_cell_heard():
    assert cells_at_least([96, 97, 95], 95, 3) is True
    assert cells_at_least([96, 97, 94], 95, 3) is False
    assert cells_at_least([96, 97], 95, 3) is False                                  # 한 대가 안 들린다 — 판단 보류
    assert cells_at_least([], 95, 0) is True                                         # 기대 셀이 없으면 참 (쓰지 않는 경우)


def test_precharge_verdict_three_ways():
    cfg = Config()
    t0 = 1000.0; now = t0 + 12 * 60
    assert precharge_verdict(_flat(t0, 12, 31.0), [100] * 24, now, cfg) == "full"                 # 사이클과 같은 만충
    assert precharge_verdict(_flat(t0, 12, 27.7), [96] * 23 + [97], now, cfg) == "floor_full"     # 10-11 — 더 기다려도 소용없다
    assert precharge_verdict(_flat(t0, 12, 27.7), [96] * 23 + [94], now, cfg) == "dock_no_recharge"
    assert precharge_verdict(_flat(t0, 12, 68.0), [80] * 24, now, cfg) is None                    # 충전 중
    assert precharge_verdict(_flat(t0, 5, 27.7), [96] * 24, now - 7 * 60, cfg) is None             # 아직 창이 안 찼다
    assert precharge_verdict(_flat(t0, 12, 27.7), [96] * 23, now, cfg) == "dock_no_recharge"      # 한 대가 안 들리면 95% 이상이라 할 수 없다


# ---------- 2. 예비 충전 — 가짜 장비로 끝까지 ----------

class _FakeLive:
    def __init__(self, cells):
        self.cells = cells

    def snapshot(self):
        return self.cells


class _FakePlug:
    def __init__(self, watts=27.7):
        self.calls, self.gaps, self.watts = [], [], watts

    def read(self):
        self.calls.append("read")
        return PlugReading(True, self.watts, time.time())

    def on(self):
        self.calls.append("on")
        return PlugReading(True, self.watts, time.time())

    def recharge(self, gap_s=10.0):
        self.calls.append("recharge"); self.gaps.append(gap_s)
        return PlugReading(True, self.watts, time.time())


def _runner(tmp_path, batt, watts=27.7, **over):
    over.setdefault("stuck_s", 0.0)
    cfg = Config(data_dir=str(tmp_path), wifi_reconnect=False, full_flat_min=0.002, poll_s=0.03, **over)
    cells = {s: CellLive("1.1.1.1", batt, 0, 4, -10, t=time.time() + 3600) for s in cfg.serials}
    return CycleRunner(cfg, _FakeLive(cells), _FakePlug(watts), None, Recorder(tmp_path)), cfg


def test_precharge_skipped_when_all_cells_at_or_above_95(tmp_path):
    r, _ = _runner(tmp_path, 95)
    r.precharge()
    assert r.plug.calls == []                                                        # 플러그에 손대지 않았다
    log = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "예비 충전 건너뜀 — 모든 셀 95% 이상" in log and r.current is None
    r, _ = _runner(tmp_path / "b", 94)
    r.precharge()
    assert r.plug.calls[0] == "recharge"                                             # 94% 면 예비 충전을 한다


def test_precharge_ends_on_floor_flat_when_cells_at_least_95(tmp_path):
    """10-11 — 셀 96~97% · 27.7 W 평탄. 예전에는 '모든 셀 100%' 를 영영 기다렸다(charge_timeout_h 4시간까지)."""
    r, _ = _runner(tmp_path, 96, watts=27.7)
    r.live.cells[r.cfg.serials[0]] = CellLive("1.1.1.1", 94, 0, 4, -10, t=time.time() + 3600)   # 시작 땐 한 대가 94 → 건너뛰지 않는다
    reads = {"n": 0}

    def read():
        reads["n"] += 1
        if reads["n"] == 2:
            r.live.cells[r.cfg.serials[0]] = CellLive("1.1.1.1", 96, 0, 4, -10, t=time.time() + 3600)
        return PlugReading(True, 27.7, time.time())
    r.plug.read = read
    r.precharge()
    log = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "만충으로 친다" in log and "dock_no_recharge" not in (tmp_path / "events.csv").read_text(encoding="utf-8-sig")
    assert r.plug.calls.count("recharge") == 1                                       # 시작 때 한 번뿐


def test_precharge_dock_no_recharge_kicks_once_then_goes_to_cycle(tmp_path):
    r, cfg = _runner(tmp_path, 90, watts=27.7)
    r.precharge()
    ev = (tmp_path / "events.csv").read_text(encoding="utf-8-sig")
    assert ev.count("dock_no_recharge") == 1 and "Dock 이 재충전하지 않는다" in ev
    assert r.plug.calls.count("recharge") == 2 and r.plug.gaps[1] == cfg.stuck_rule()[2]   # 시작 + 끊었다 켬 1회 (30초)
    log = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "Dock 이 재충전하지 않아 사이클로 넘어간다" in log
    assert "charge_timeout" not in ev                                                 # 4시간 한도까지 기다리지 않았다


# ---------- 1. 자동 이어 가기 — 실행기 ----------

class _DonePlug(_FakePlug):
    def __init__(self):
        super().__init__(60.0)


def _loop_runner(tmp_path, **over):
    over.setdefault("stuck_s", 0.0)
    cfg = Config(data_dir=str(tmp_path), wifi_reconnect=False, **over)
    r = CycleRunner(cfg, _FakeLive({}), _DonePlug(), None, Recorder(tmp_path))
    n = {"i": 0}

    def one():
        n["i"] += 1
        r.current = CycleState(cycle=r.rec.next_cycle_no())
        r.rec.cycle({"cycle": r.current.cycle, "discharge_h": f"{3.0 + n['i']:.3f}", "note": "ok"})
    r.run_cycle = one
    return r, cfg


def test_run_does_not_end_before_target_and_ends_at_target(tmp_path):
    r, _ = _loop_runner(tmp_path, target_cycles=3)
    assert r.run(1) == "done"                                                        # --cycles 1 은 '최소' — 목표 3 까지 돈다
    rows = (tmp_path / "cycles.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(rows) == 4 and r.plug.calls[0] == "recharge" and r.plug_on_at_exit is True   # 끝낼 때 플러그 ON (뒤의 read 는 확인)
    ev = (tmp_path / "events.csv").read_text(encoding="utf-8-sig")
    assert ev.count("target_reached") == 1 and "이번 실행 1사이클 완료 · 누적 1/3" in ev
    log = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "시험 목표 3사이클에 닿았다(누적 3)" in log


def test_run_resumes_from_existing_rows_and_stops_exactly_at_target(tmp_path):
    r, _ = _loop_runner(tmp_path, target_cycles=3)
    r.rec.cycle({"cycle": 1, "note": "old"}); r.rec.cycle({"cycle": 2, "note": "old"})   # 옛 실행이 2사이클을 남겼다
    assert r.run(5) == "done"                                                        # --cycles 5 라도 누적이 3 이면 끝
    assert len((tmp_path / "cycles.csv").read_text(encoding="utf-8-sig").splitlines()) == 4
    assert "target_reached" not in (tmp_path / "events.csv").read_text(encoding="utf-8-sig")   # 이번 실행 몫(5)을 다 돌지 않았다


def test_run_with_auto_continue_off_keeps_old_behaviour(tmp_path):
    r, _ = _loop_runner(tmp_path, auto_continue=False, target_cycles=500)
    assert r.run(2) == "done"
    assert len((tmp_path / "cycles.csv").read_text(encoding="utf-8-sig").splitlines()) == 3


def test_progress_event_every_n_cycles_with_average_discharge(tmp_path):
    r, _ = _loop_runner(tmp_path, target_cycles=5, progress_notify_every=2)
    got = []
    r.rec.alert = got.append
    r.run(1)
    ev = (tmp_path / "events.csv").read_text(encoding="utf-8-sig")
    assert ev.count(",progress,") == 2                                               # 누적 2 · 4 (5 는 배수가 아니다)
    assert "목표 5 중 2사이클 완료 · 누적 방전 평균 4.5h" in ev                      # (4 + 5) / 2
    assert "목표 5 중 4사이클 완료 · 누적 방전 평균 5.5h" in ev
    assert sum(1 for a in got if "progress" in a) == 2                               # Slack 1건씩
    assert not any("target_reached" in a for a in got)                               # target_reached 는 기록만


def test_progress_milestone_already_passed_is_not_repeated_on_restart(tmp_path):
    r, _ = _loop_runner(tmp_path, target_cycles=3, progress_notify_every=2)
    for i in (1, 2):
        r.rec.cycle({"cycle": i, "discharge_h": "3.0"})                              # 되살리기 전 엔진이 2 까지 — 이미 알렸을 것이다
    r.run(1)
    assert ",progress," not in (tmp_path / "events.csv").read_text(encoding="utf-8-sig")


def test_avg_discharge_h_skips_blank_rows():
    assert avg_discharge_h([{"discharge_h": "3.0"}, {"discharge_h": ""}, {"discharge_h": "5.0"}, {}]) == 4.0
    assert avg_discharge_h([]) is None


# ---------- 1. 자동 이어 가기 — 가짜 세상으로 끝까지 ----------

def _bench(**kw):
    return {"bench_id": "hq-bench-1", "auto_continue": True, **kw}


def test_engine_keeps_going_past_cycles_arg_until_target(tmp_path, monkeypatch):
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1)], limit_h=12)
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"], bench=_bench(target_cycles=2, progress_notify_every=1))
    eng = json.loads((tmp_path / "data" / "engine.json").read_text(encoding="utf-8"))
    rows = (tmp_path / "data" / "cycles.csv").read_text(encoding="utf-8-sig").splitlines()
    assert code == 0 and eng["exit"] == "done" and eng["plug_on"] is True and len(rows) == 3   # --cycles 1 인데 2사이클
    ev = (tmp_path / "data" / "events.csv").read_text(encoding="utf-8-sig")
    assert ev.count("target_reached") == 1 and ev.count(",progress,") == 2
    assert sum(1 for a in w.alerts if "progress" in a) == 2 and w.plugs[sw.SET1_MAC] is True
    now = json.loads((tmp_path / "data" / "now.json").read_text(encoding="utf-8"))
    assert now["metrics"]["target_cycles"] == 2 and now["metrics"]["cycles_done"] == 2


def test_stop_safe_is_still_a_way_out_while_continuing(tmp_path, monkeypatch):
    from cellbench.control import ControlInbox
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1)], limit_h=12)
    w.at(2 * 3600, lambda: ControlInbox(tmp_path / "data").post("stop_safe", "t"))
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"], bench=_bench(target_cycles=100))
    eng = json.loads((tmp_path / "data" / "engine.json").read_text(encoding="utf-8"))
    rows = (tmp_path / "data" / "cycles.csv").read_text(encoding="utf-8-sig").splitlines()
    assert code == 0 and eng["exit"] == "stopped" and len(rows) >= 2 and w.plugs[sw.SET1_MAC] is True


class _StuckDock(sw.World):
    """Dock 이 재충전하지 않는 세상 — 셀은 cap 까지만 차고, 플러그가 켜져 있어도 바닥 전력(27.7 W)만 흐른다 (10-11 실측)."""

    def __init__(self, cap: float, start: float | None = None, **kw):
        super().__init__([sw.Dock(sw.SET1_MAC, sw.SET1, batt={s: (cap if start is None else start) for s in sw.SET1})], **kw)
        self.cap = cap

    def integrate(self, dt):
        super().integrate(dt)
        for d in self.docks:
            for s in d.batt:
                d.batt[s] = min(self.cap, d.batt[s])

    def watts(self, mac):
        if not self.plugs.get(mac):
            return 0.0
        return 68.0 if any(b < self.cap for d in self.docks for b in d.batt.values()) else 27.7   # cap 에 닿으면 바닥만 흐른다


def test_world_precharge_floor_flat_at_96_goes_to_cycle_within_minutes(tmp_path, monkeypatch):
    w = _StuckDock(96.0, start=94.0, limit_h=12)                                        # 94% 에서 시작(건너뛰지 않는다) → 96% 에서 Dock 이 멈춘다
    code = sw.run_main(monkeypatch, tmp_path, w, ["--precharge", "--cycles", "1"], bench=_bench(target_cycles=1, charge_timeout_h=0.5))
    log = (tmp_path / "data" / "run.log").read_text(encoding="utf-8")
    assert code == 0 and "만충으로 친다" in log and "dock_no_recharge" not in log and "건너뜀" not in log
    pre = [l for l in log.splitlines() if "예비 충전 끝 ·" in l]
    assert pre and int(pre[0].split("예비 충전 끝 · ")[1].split("분")[0]) <= 12            # 10-11 엔 2시간 39분이었다
    assert len((tmp_path / "data" / "cycles.csv").read_text(encoding="utf-8-sig").splitlines()) == 2


def test_world_precharge_dock_no_recharge_at_90_kicks_once_then_cycles(tmp_path, monkeypatch):
    w = _StuckDock(90.0, limit_h=12)
    code = sw.run_main(monkeypatch, tmp_path, w, ["--precharge", "--cycles", "1"], bench=_bench(target_cycles=1, charge_timeout_h=0.5))
    ev = (tmp_path / "data" / "events.csv").read_text(encoding="utf-8-sig")
    log = (tmp_path / "data" / "run.log").read_text(encoding="utf-8")
    assert code == 0 and ev.count("dock_no_recharge") == 1 and "사이클로 넘어간다" in log
    assert w.plug_calls[sw.SET1_MAC].count("recharge") == 2 + 1                     # 시작 + 끊었다 켬 1회 + 끝낼 때
    assert any("dock_no_recharge" in a for a in w.alerts)                             # 사람에게 1건


def test_world_precharge_skipped_at_100(tmp_path, monkeypatch):
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1, batt={s: 100.0 for s in sw.SET1})], limit_h=12)
    code = sw.run_main(monkeypatch, tmp_path, w, ["--precharge", "--cycles", "1"], bench=_bench(target_cycles=1))
    log = (tmp_path / "data" / "run.log").read_text(encoding="utf-8")
    assert code == 0 and "예비 충전 건너뜀" in log
    assert "PRECHARGE" not in (tmp_path / "data" / "samples_0001.csv").read_text(encoding="utf-8-sig")


# ---------- 1. 자동 이어 가기 — 감시자 ----------

def test_remaining_cycles_counts_to_test_target_when_auto_continue():
    cfg = Config(target_cycles=500)
    assert remaining_cycles(ts.eng(target_last_cycle=5), 3, 4, cfg) == 497             # target_last_cycle 은 보지 않는다
    assert remaining_cycles(None, 499, 4, cfg) == 1 and remaining_cycles(None, 500, 4, cfg) == 0
    assert remaining_cycles(ts.eng(target_last_cycle=5), 3, 4, Config(auto_continue=False)) == 2   # 옛 규칙


def test_plan_revives_finished_engine_below_target_but_not_at_target():
    cfg = Config(target_cycles=500)
    p = restart_plan("finished", ts.eng(exit="done", plug_on=True), ts.now_json(4000, "DONE"), 4, [], ts.T, cfg)
    assert p.plug_on and p.restart == 496 and not p.call_human and "시험대가 놀지 않게" in p.why
    p = restart_plan("finished", ts.eng(exit="done", plug_on=True), ts.now_json(4000, "DONE"), 500, [], ts.T, cfg)
    assert p == Plan(why=p.why) and "시험 목표 500사이클을 다 돌고 끝남" in p.why
    p = restart_plan("finished", ts.eng(exit="done", plug_on=False), ts.now_json(4000, "DONE"), 500, [], ts.T, cfg)
    assert p.plug_on and p.restart == 0                                                # 다 돌았는데 플러그를 못 켰으면 켠다


def test_plan_dead_engine_restarts_with_cycles_to_target():
    cfg = Config(target_cycles=10)
    p = restart_plan("dead", ts.eng(target_last_cycle=4), ts.now_json(4000), 4, [], ts.T, cfg)
    assert p.plug_on and p.restart == 6                                                # 옛 규칙이면 0 (다 돌아 안 띄움) 이었다
    p = restart_plan("dead", ts.eng(target_last_cycle=4), ts.now_json(4000), 10, [], ts.T, cfg)
    assert p.restart == 0 and "시험 목표 10사이클을 이미 다 돌아" in p.why


def test_tick_revives_finished_engine_once_and_waits_for_it(tmp_path):
    """10-11 12:20 — 옛 코드 엔진이 사이클 4 를 돌고 done 으로 끝났다. 새 감시자는 플러그 ON 뒤 남은 사이클로 다시 띄우고, 겹쳐 띄우지 않는다."""
    ts.put(tmp_path, ts.eng(exit="done", plug_on=True), ts.now_json(4000, "DONE"), cycles=4)
    cfg = Config(data_dir=str(tmp_path), target_cycles=500)
    w = ts.World()
    rep, _ = tick(cfg, tmp_path, None, w.deps(), ts.T)
    assert w.acts() == ["plug_on", "engine:496"] and rep["plug_guard"]["active"] is False
    assert ts.lights(rep)["program"] == "yellow" and len(w.alerts) == 1 and "놀지 않게" in w.alerts[0]
    rep, _ = tick(cfg, tmp_path, rep, w.deps(), ts.T + 60)                              # 새 엔진이 아직 engine.json 을 안 썼다
    assert w.acts() == ["plug_on", "engine:496"] and "시작하는 중" in rep["why"]
    ts.put(tmp_path, ts.eng(exit="done", plug_on=True), ts.now_json(4000, "DONE"), cycles=500)   # 시험 목표를 다 돈 엔진은 그대로 둔다
    w2 = ts.World()
    rep, _ = tick(cfg, tmp_path, None, w2.deps(), ts.T)
    assert w2.acts() == [] and ts.lights(rep)["program"] == "green"


# ---------- 3. 진척 없음 경고 ----------

@pytest.mark.parametrize("phase,hours,want", [
    ("PRECHARGE", 1.5, "green"), ("PRECHARGE", 1.5 + 1 / 3600, "yellow"), ("PRECHARGE", 3.0, "yellow"), ("PRECHARGE", 3.0 + 1 / 3600, "red"),
    ("PLUG_ON", 0.15, "green"), ("PLUG_ON", 0.15 + 1 / 3600, "yellow"), ("PLUG_ON", 0.3 + 1 / 3600, "red"),
    ("EXTRACT", 0.75, "green"), ("EXTRACT", 0.75 + 1 / 3600, "yellow"), ("EXTRACT", 1.5 + 1 / 3600, "red"),
    ("CHARGE", 6.0, "green"), ("CHARGE", 6.0 + 1 / 3600, "yellow"), ("CHARGE", 12.0 + 1 / 3600, "red"),
    ("RECOVER", 0.75, "green"), ("RECOVER", 0.75 + 1 / 3600, "yellow"), ("RECOVER", 1.5 + 1 / 3600, "red"),
    ("DISCHARGE", 7.5, "green"), ("DISCHARGE", 15.0 + 1 / 3600, "red"),
    ("FULL", 9.0, "green"), ("PLUG_OFF", 9.0, "green"),                                  # 바꾸는 순간의 단계는 기대가 없다
])
def test_dwell_boundaries(phase, hours, want):
    assert HEALTH["dwell_expect_h"] == {"PRECHARGE": 1.0, "DISCHARGE": 5.0, "PLUG_ON": 0.1, "EXTRACT": 0.5, "CHARGE": 4.0, "RECOVER": 0.5}
    h = th.run(now=th.now_(phase=phase, since=hours * 3600))
    assert th.L(h, "program") == want
    if want != "green":
        p = th.lane(h, "program")
        assert "단계 체류가 기대" in p["reason"] and "진척 없음" in p["reason"] and "플러그 끄기" in p["human"]
        assert p["issues"][0]["short"].startswith(f"단계 체류 기대의 {'3' if want == 'red' else '1.5'}배 — {hours * 60:.0f}분째 {phase}")


def test_dwell_reason_is_stable_while_minutes_change():
    a = th.lane(th.run(now=th.now_(phase="PRECHARGE", since=2 * 3600)), "program")
    b = th.lane(th.run(now=th.now_(phase="PRECHARGE", since=2.5 * 3600)), "program")
    assert a["reason"] == b["reason"] and a["issues"][0]["short"] != b["issues"][0]["short"]   # 알림기가 같은 사건으로 본다


def test_program_value_shows_progress_toward_target():
    h = th.run(now=th.now_(metrics=th.metrics(cycles_done=123, target_cycles=500)))
    assert "누적 123 / 목표 500" in th.lane(h, "program")["value"]
    assert "누적" not in th.lane(th.run(), "program")["value"]                           # 옛 now.json(칸 없음)은 그대로


def test_supervisor_sends_one_slack_for_dwell_then_red(tmp_path):
    """10-11 16:42 ~ 19:21 PRECHARGE 2시간 39분 — 이제 1.5시간에 노랑 1건, 3시간에 빨강(확인까지 반복)."""
    def now(hours, t=ts.T):                                                              # 심박은 늘 10초 전 (엔진이 돌고 있다)
        return {"t": t - 10, "beat": t - 10, "phase": "PRECHARGE", "phase_since": t - hours * 3600,
                "metrics": th.metrics(cycles_done=4, target_cycles=500)}
    ts.put(tmp_path, ts.eng(started=ts.T - 3 * 3600), now(2.65), cycles=4)
    w = ts.World(alive={4242: ts.T - 3 * 3600})
    cfg = Config(data_dir=str(tmp_path))
    rep, _ = tick(cfg, tmp_path, None, w.deps(), ts.T)
    assert rep["checks"]["engine"] == "ok" and ts.lights(rep)["program"] == "yellow"
    assert len(w.alerts) == 1 and "예비 충전 단계 체류가 기대(1시간)의 1.5배를 넘김" in w.alerts[0]
    assert "누적 4 / 목표 500" in next(l for l in rep["health"]["lanes"] if l["key"] == "program")["value"]
    ts.put(tmp_path, ts.eng(started=ts.T - 3 * 3600), now(2.7, ts.T + 60), cycles=4)
    rep, _ = tick(cfg, tmp_path, rep, w.deps(), ts.T + 60)
    assert len(w.alerts) == 1                                                            # 같은 이유는 1시간에 한 번
    ts.put(tmp_path, ts.eng(started=ts.T - 4 * 3600), now(3.1, ts.T + 120), cycles=4)
    rep, _ = tick(cfg, tmp_path, rep, w.deps(), ts.T + 120)
    assert ts.lights(rep)["program"] == "red" and len(w.alerts) == 2 and "3배" in w.alerts[1]
