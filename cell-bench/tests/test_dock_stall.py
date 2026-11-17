"""규칙 A — 엔진이 도는 중 Dock 충전 멈춤(dock_stall) (자동 복구 기획 5절 · 커밋 2). 장비 없이 돈다.

순수 판정(guards.StallWatch) → 운영 표본을 고정 자료로 되돌려 넣기(tests/fixtures/recovery) → 엔진 배선(가짜 플러그 · 가짜 셀) →
진짜 run_cycle.main 을 가짜 세상(sim_world)에서 끝까지 — 켜짐 · 24 W · 셀이 내려가는 Dock 을 끊었다 켜서 살리거나, 끝내 못 살리고 멈춘다.
"""
import csv
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sim_world as sw
from cellbench import guards
from cellbench.bench import BenchRunner
from cellbench.config import SAFETY, Config
from cellbench.cycle import CycleRunner, CycleState, DockStallFail, PairMismatch
from cellbench.plug import PlugReading
from cellbench.record import ALERT_KINDS, EVENT_LIGHT, Recorder
from test_review_fixes import Live, SeqPlug, events, kinds

FIX = Path(__file__).resolve().parent / "fixtures" / "recovery"
CELLS = range(11733, 11757)


def watch(**kw):
    a = dict(window_s=SAFETY["stall_window_s"], drop_pct=SAFETY["stall_drop_pct"], stuck_w=10.0, floor_w=SAFETY["stall_w_max"],
             full_pct=95, min_cells=SAFETY["min_cells"], max_kicks=3)
    a.update(kw)
    return guards.StallWatch(**a)


def cells(v, n=24):
    return {s: v for s in list(CELLS)[:n]}


def feed_series(wt, series, phase="CHARGE", t0=0.0, step=20.0):
    """series = [(W, 셀 값)] — step 초 간격으로 넣고 (시각, 판정) 중 None 이 아닌 것을 돌려준다."""
    out = []
    for i, (w, v) in enumerate(series):
        a = wt.feed(t0 + i * step, phase, cells(v) if not isinstance(v, dict) else v, True, w)
        if a:
            out.append((t0 + i * step, a))
    return out


# ---------- 순수 판정 ----------

def test_values_live_in_safety_not_config():
    """새 값 둘은 SAFETY 에 — Config 칸이 늘면 run.log 의 '설정:' 줄이 바뀐다(불변식 I7)."""
    assert SAFETY["stall_window_s"] == 180 and SAFETY["stall_drop_pct"] == 1.0
    assert not hasattr(Config(), "stall_window_s")


def test_fires_when_floor_three_minutes_and_cells_drop():
    """사고 3 의 꼴 — 25 W 바닥 3분에 63 → 62 %."""
    s = [(88.8, 63)] + [(25.0, 63)] * 6 + [(25.0, 62)] * 4
    got = feed_series(watch(), s)
    assert got == [(200.0, "kick")]                       # 첫 바닥 표본(20초)에서 180초 — 앞 끝은 바닥이어야 한다


def test_fires_when_below_95_and_flat_but_not_when_rising():
    assert feed_series(watch(), [(30.0, 80)] * 11) == [(180.0, "kick")]          # 95 % 미만인데 오르지 않음
    assert feed_series(watch(), [(32.4, 46 + i) for i in range(30)]) == []        # X 약한 포트 — 32 W 에서도 오른다


def test_counterexamples_from_plan_do_not_fire():
    """기획 5절의 반례 — 만충 직전 전력 감소 · 만충 유지 · 예비 충전 96~97 % 평탄 · 켠 직후 낮은 한 번 · 1.5 W 멈춤 · 충전 중."""
    assert feed_series(watch(), [(34.8, 99)] * 5 + [(33.0, 100)] * 10) == []
    assert feed_series(watch(), [(29.0, 100)] * 60) == []
    assert feed_series(watch(), [(27.7, 96)] * 60, phase="PRECHARGE") == []
    assert feed_series(watch(), [(68.0, 60), (38.0, 60)] + [(68.0, 60)] * 20) == []
    assert feed_series(watch(), [(1.5, 70 - i // 5) for i in range(60)]) == []
    assert feed_series(watch(), [(88.0, 50 + i // 3) for i in range(60)]) == []


def test_unknown_samples_and_too_few_cells_do_not_fire():
    """I6 — 못 읽은 표본이 창에 있거나 12대 미만이면 판정하지 않는다."""
    w = watch()
    for i in range(12):
        assert w.feed(i * 20.0, "CHARGE", cells(60 - i // 3), None if i == 5 else True, 25.0) is None
    w = watch()
    assert [w.feed(i * 20.0, "CHARGE", cells(60 - i // 3, n=11), True, 25.0) for i in range(12)] == [None] * 12


def test_phase_change_restarts_window():
    w = watch()
    for i in range(8):
        w.feed(i * 20.0, "EXTRACT_WAIT", cells(60 - i // 3), True, 25.0)
    assert w.feed(160.0, "CHARGE", cells(57), True, 25.0) is None                # 앞 단계의 표본으로 판정하지 않는다
    assert [w.feed(160.0 + i * 20, "CHARGE", cells(57 - i // 3), True, 25.0) for i in range(1, 10)][-1] == "kick"


def test_kick_limit_then_fail_or_capped():
    """1시간 3번. 그 뒤 사건 시작보다 1 %p 이상 내려가 있으면 fail(한 번), 아니면 capped(한 번)."""
    w = watch()
    got = []
    for i in range(150):                                   # 3분에 1 %p 씩 계속 내려감 — 끊었다 켜도 낫지 않는다 (1시간 안)
        a = w.feed(i * 20.0, "CHARGE", cells(60 - i // 9), True, 25.0)
        if a:
            got.append(a)
        if a == "fail":
            assert w.last["drop"] == -4.0                  # 사건 시작 60 % 에서 56 %
    assert got == ["kick", "kick", "kick", "fail"]
    w = watch()
    s = [(25.0, 80)] * 150                                 # 95 % 미만 · 평탄 — 내려가지는 않는다
    assert [a for _, a in feed_series(w, s)] == ["kick", "kick", "kick", "capped"]


def test_limit_refills_after_an_hour_and_refund():
    w = watch()
    assert [a for _, a in feed_series(w, [(25.0, 80)] * 150)] == ["kick", "kick", "kick", "capped"]
    assert w.feed(4300.0, "CHARGE", cells(80), True, 25.0) == "kick" and w.last["n"] == 1     # 지난 1시간의 시도가 비었다 — 새 사건
    w.refund()
    assert w.kicks == [] and w.kicked_at is None


def test_recovery_after_kick_reports_ok_once():
    w = watch()
    got = feed_series(w, [(25.0, 63)] * 3 + [(25.0, 62)] * 8 + [(84.0, 62 + i // 4) for i in range(20)])
    assert [a for _, a in got] == ["kick", "ok"]
    assert w.last["ch"] > 0


# ---------- 운영 표본 되돌려 넣기 (평균을 중앙값 대신 — 표본 파일에는 셀별 값이 없다) ----------

def replay(name):
    w = watch()
    out = []
    with open(FIX / name, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            t = time.mktime(time.strptime(r["time"], "%Y-%m-%d %H:%M:%S"))
            n, avg = int(r["cells"] or 0), float(r["batt_avg"]) if r["batt_avg"] else None
            a = w.feed(t, r["phase"], {} if avg is None else cells(avg, n), {"on": True, "off": False}.get(r["plug"]),
                       float(r["watts"]) if r["watts"] else float("nan"))
            if a:
                out.append((r["time"][11:], a))
    return out


def test_replay_incident3_fires_at_234618():
    """사고 3 (11-16, Y Dock 88.8 W → 25.6 W). 실제로는 20분 뒤 짝 이상으로 두 세트가 멈췄다."""
    got = replay("incident3_y_0136.csv")
    assert got[0] == ("23:46:18", "kick")
    assert [a for _, a in got] == ["kick", "kick", "kick", "fail"]        # 끊었다 켜도 낫지 않았다면 13분 뒤 멈춘다
    assert got[-1][0] == "23:56:25"


def test_replay_incident1_low_power_is_not_this_rule():
    """사고 1 (11-13, 70.8 W → 1.5 W) — 10 W 아래라 저전력 복구가 61초 만에 잡았다. 새 규칙은 발동하지 않는다."""
    assert replay("incident1_y_0134.csv") == []


def test_replay_normal_full_charge_never_fires():
    """정상 만충 (11-13 14:40 ~ 15:44) — 만충 직전 34.8 W 아래로 내려가도 셀이 99 % 이상이고 오른다."""
    assert replay("normal_full_y_0134.csv") == []


def test_replay_x_1114_fires_at_013235():
    """X Dock 11-14 — 32.4 W 그대로 98.8 % 에서 내려가기 시작했다."""
    got = replay("x_1114_0113.csv")
    assert got[0] == ("01:32:35", "kick")


# ---------- 엔진 배선 (가짜 플러그 · 가짜 셀) ----------

class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def time(self):
        return self.t


def stall_runner(tmp_path, monkeypatch, plug, batt):
    import cellbench.cycle as cyc
    from cellbench.cells import CellLive
    clk = Clock()
    monkeypatch.setattr(cyc.time, "time", clk.time)
    monkeypatch.setattr(cyc.time, "sleep", lambda s: None)
    live = Live()
    cfg = Config(data_dir=str(tmp_path), wifi_reconnect=False, serials=list(CELLS))
    r = CycleRunner(cfg, live, plug, None, Recorder(tmp_path))

    def step(phase, w, v, on=True):
        clk.t += 20
        live.cells = {s: CellLive("192.168.1.2", v, 0, 8, -55, clk.t) for s in CELLS}
        r._last_read = PlugReading(on, w, clk.t)
        st.phase = phase
        r._guard_stall(st)
    st = CycleState(cycle=7, phase="CHARGE")
    return r, st, step, clk


def test_engine_kicks_records_and_resets_direction_window(tmp_path, monkeypatch):
    plug = SeqPlug()
    r, st, step, clk = stall_runner(tmp_path, monkeypatch, plug, 63)
    r._dir.start_charge(clk.t - 3000)
    for v in [63] * 5 + [62] * 5:
        step("CHARGE", 25.0, v)
    assert plug.calls == ["recharge"] and plug.gaps == [30.0]
    ev = events(tmp_path)
    assert [e["kind"] for e in ev] == ["dock_stall"]
    assert "25.0 W" in ev[0]["detail"] and "1/3" in ev[0]["detail"] and "사람이 할 일: 없음" in ev[0]["detail"]
    assert r._dir.armed > clk.t                             # 방향 대조 (a) 의 창을 새로 열었다
    assert st.power_recovers == 1
    for v in [62 + i // 3 for i in range(12)]:
        step("CHARGE", 84.0, v)
    assert kinds(tmp_path) == ["dock_stall", "dock_stall_ok"]


def test_engine_not_wired_in_discharge_or_extract_or_forced_discharge(tmp_path, monkeypatch):
    """I1 · I2 — 방전 · 추출에는 배선하지 않는다. 원격 '플러그 끄기'로 끈 동안은 물러난다."""
    plug = SeqPlug()
    r, st, step, _ = stall_runner(tmp_path, monkeypatch, plug, 63)
    for ph in ("DISCHARGE", "EXTRACT", "PLUG_ON", "THRESHOLD", "FULL", "DONE"):
        for i in range(15):
            step(ph, 25.0, 63 - i // 3)
    r._force = "discharge"
    for i in range(15):
        step("CHARGE", 25.0, 63 - i // 3)
    assert plug.calls == [] and kinds(tmp_path) == []


def test_engine_failed_kick_is_refunded(tmp_path, monkeypatch):
    plug = SeqPlug(on_ok=False)
    r, st, step, _ = stall_runner(tmp_path, monkeypatch, plug, 63)
    for v in [63] * 5 + [62] * 5:
        step("CHARGE", 25.0, v)
    assert plug.calls == ["recharge"] and r._stall.kicks == [] and kinds(tmp_path) == []


def test_engine_fail_raises_dock_stall_fail_through_pair_mismatch_path(tmp_path, monkeypatch):
    plug = SeqPlug()
    r, st, step, _ = stall_runner(tmp_path, monkeypatch, plug, 63)
    r.fault_plugs = []
    with pytest.raises(DockStallFail) as e:
        for i in range(150):
            step("CHARGE", 25.0, 63 - i // 9)
    assert isinstance(e.value, PairMismatch)
    assert kinds(tmp_path) == ["dock_stall"] * 3 + ["dock_stall_fail"]
    assert "모든 플러그" in events(tmp_path)[-1]["detail"]


def test_engine_fail_in_hold_records_without_raising(tmp_path, monkeypatch):
    """세트 정지(HOLD)는 이미 플러그를 켜 둔 채 선 상태 — 남기기만 하고 다른 세트를 세우지 않는다."""
    plug = SeqPlug()
    r, st, step, _ = stall_runner(tmp_path, monkeypatch, plug, 63)
    called = []
    r.on_pair_fault = called.append
    for i in range(150):
        step("HOLD", 25.0, 63 - i // 9)
    assert kinds(tmp_path) == ["dock_stall"] * 3 + ["dock_stall_fail"] and called == []


def test_event_kinds_and_lights():
    assert EVENT_LIGHT["dock_stall"] == "yellow" and EVENT_LIGHT["dock_stall_fail"] == "red"
    assert "dock_stall_fail" in ALERT_KINDS and not {"dock_stall", "dock_stall_ok"} & ALERT_KINDS   # 사람이 할 일 없음 — 기록 · 결과판만


# ---------- 다중 세트 — 끝난 이유 모으기 ----------

def test_bench_end_why_rules():
    from types import SimpleNamespace as NS
    b = BenchRunner.__new__(BenchRunner)
    b.verify_failed = False

    def sets(*pairs):
        return {i: NS(kind=k, runner=NS(end_why=w)) for i, (k, w) in enumerate(pairs, 1)}
    b.sets = sets(("failsafe", "dock_stall"), ("stopped", None))
    assert b.end_why() == "dock_stall"                      # 다른 세트는 HOLD 요청으로 섰을 뿐
    b.sets = sets(("failsafe", "dock_stall"), ("failsafe", "pair_mismatch"))
    assert b.end_why() == "pair_mismatch"
    b.sets = sets(("failsafe", "dock_stall"), ("failsafe", "failures"))
    assert b.end_why() == "failures"                        # 하나라도 다른 이유면 되살리지 않는 쪽
    b.sets = sets(("done", None), ("done", None))
    assert b.end_why() is None
    b.sets, b.verify_failed = sets(("stopped", None), ("stopped", None)), True
    assert b.end_why() == "pair_mismatch"


# ---------- 진짜 run_cycle.main 을 가짜 세상에서 ----------

class StallWorld(sw.World):
    """Dock 이 켜짐인 채 24 W 로 멈추고 셀이 내려간다. cure 면 플러그가 한 번 꺼지면(끊었다 켜기) 낫는다."""

    def __init__(self, *a, cure=True, **k):
        super().__init__(*a, **k)
        self.cure, self.stalled = cure, False

    def stall(self):
        self.stalled = True

    def integrate(self, dt):
        for d in self.docks:
            if not self.plugs.get(d.feeder) and self.cure:
                self.stalled = False
        if not self.stalled:
            return super().integrate(dt)
        for d in self.docks:
            for s in d.batt:
                d.batt[s] = max(0.0, d.batt[s] - 0.35 * dt / 60)

    def watts(self, mac):
        if self.stalled and self.plugs.get(mac):
            return 24.6
        return super().watts(mac)


def run_stall(tmp_path, monkeypatch, cure):
    w = StallWorld([sw.Dock(sw.SET1_MAC, sw.SET1)], cure=cure, rate_pct_min=0.6, discharge_rate=2.0)
    w.at(40 * 60, w.stall)                                  # 방전 25분 · 추출 5분 뒤 충전 중
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"])
    data = tmp_path / "data"
    rows = list(csv.DictReader(open(data / "events.csv", encoding="utf-8-sig", newline="")))
    return code, w, data, [r["kind"] for r in rows], rows


def test_world_stall_is_kicked_and_cycle_completes(tmp_path, monkeypatch):
    code, w, data, ks, rows = run_stall(tmp_path, monkeypatch, cure=True)
    assert code == 0
    assert ks.count("dock_stall") == 1 and ks.count("dock_stall_ok") == 1 and "pair_mismatch" not in ks
    eng = json.loads((data / "engine.json").read_text(encoding="utf-8"))
    assert eng["exit"] == "done" and "why" not in eng
    cyc = list(csv.DictReader(open(data / "cycles.csv", encoding="utf-8-sig", newline="")))
    assert cyc[-1]["full_at"] and "dock_stall" not in cyc[-1]["note"]


def test_world_stubborn_stall_ends_failsafe_with_why(tmp_path, monkeypatch):
    code, w, data, ks, rows = run_stall(tmp_path, monkeypatch, cure=False)
    assert [k for k in ks if k.startswith("dock_stall")] == ["dock_stall"] * 3 + ["dock_stall_fail"]
    assert "pair_mismatch" not in ks
    eng = json.loads((data / "engine.json").read_text(encoding="utf-8"))
    assert eng["exit"] == "failsafe" and eng["why"] == "dock_stall" and eng["plug_on"] is True
    cyc = list(csv.DictReader(open(data / "cycles.csv", encoding="utf-8-sig", newline="")))
    assert "dock_stall" in cyc[-1]["note"]
    assert "Dock 충전 멈춤" in (data / "run.log").read_text(encoding="utf-8")


# ---------- 플러그가 서로 바뀌어 꽂혔고 상대 Dock 이 만충 — 커밋 3 의 결정 '기획대로 둔다(가)'의 근거를 확인한다 ----------

def test_swapped_plugs_stall_then_resume_is_caught_as_pair_mismatch_and_not_revived(tmp_path, monkeypatch):
    """세트 1 의 셀은 세트 2 의 플러그(P2)가, 세트 2 의 셀은 세트 1 의 플러그(P1)가 먹인다. 상대 Dock(세트 2)은 만충, P2 는 꺼져 있다.
    세트 1 엔진에서 보면 '내 플러그 켜짐 · 31 W 바닥 · 내 셀 하락' — Dock 충전 멈춤과 같은 모양이라 dock_stall_fail 로 멈추고(why=dock_stall),
    감시자가 자동 재개한다. 근거: 재개된 엔진의 다음 방전 시작 30분 안에 방향 대조가 pair_mismatch 로 잡고, 그 이유는 되살리지 않는다.
    (단일 모드에서 확인했다. 두 세트가 함께 도는 다중 모드에서는 이렇게 되지 않는다 — 커밋 7 의 보고 참고.)"""
    import time as _t
    from cellbench import supervisor as sup
    p2, s2 = "AA:BB:CC:DD:EE:02", list(range(11594, 11618))
    w = sw.World([sw.Dock(p2, sw.SET1, batt={s: 60.0 for s in sw.SET1}), sw.Dock(sw.SET1_MAC, s2, batt={s: 100.0 for s in s2})],
                 limit_h=40, rate_pct_min=0.6, discharge_rate=0.32)
    w.plugs[p2] = False
    bench = {"bench_id": "hq-bench-1", "sets": [dict(sw.configmod.DEFAULT_SET), {"id": 2, "plug_mac": p2, "serials": "11594-11617"}]}
    data = tmp_path / "data"

    def engine():
        return json.loads((data / "engine.json").read_text(encoding="utf-8"))
    sw.run_main(monkeypatch, tmp_path, w, ["--precharge", "--cycles", "1"], bench=bench)
    first = engine()
    assert first["exit"] == "failsafe" and first["why"] == "dock_stall"
    plan = sup.restart_plan("paused", first, None, 0, [], first["ended"] + 600, Config(auto_continue=False))
    assert plan.restart > 0 and plan.resume                                 # 감시자는 10분 뒤 다시 띄운다
    w.clock.sleep(600)
    sw.run_main(monkeypatch, tmp_path, w, ["--precharge", "--cycles", "1"], bench=bench)
    second = engine()
    assert second["exit"] == "failsafe" and second["why"] == "pair_mismatch"
    log = (data / "run.log").read_text(encoding="utf-8").splitlines()
    start = [l for l in log if "방전 시작" in l][-1]
    caught = [r for r in csv.DictReader(open(data / "events.csv", encoding="utf-8-sig", newline="")) if r["kind"] == "pair_mismatch"][-1]
    t = lambda s: _t.mktime(_t.strptime(s[:19], "%Y-%m-%d %H:%M:%S"))
    assert 0 < t(caught["time"]) - t(start) <= 30 * 60                       # 다음 방전 시작 30분 안
    assert caught["phase"] == "DISCHARGE"
    plan = sup.restart_plan("paused", second, None, 0, [], second["ended"] + 99999, Config(auto_continue=False))
    assert plan.restart == 0 and plan.call_human                             # 짝 이상은 되살리지 않는다


# ---------- 검토 보강 (커밋 9) ----------

def test_recovered_incident_is_closed_so_a_second_stall_within_the_hour_can_fail():
    """멈춤 → 끊었다 켬 → 회복(ok) → 20분 뒤 다시 멈춤 · 안 풀림. 회복이 사건을 닫지 않으면 두 번째 하락을 첫 사건 시작(63 %)부터 재
    양수가 되어 capped 로 빠지고, 방향 대조 (a) 가 짝 이상으로 세워 자동 재개를 못 받았다. 1시간 한도(3번)는 이어 센다."""
    s = ([(25.0, 63)] * 3 + [(25.0, 62)] * 8                      # 첫 멈춤 → kick
         + [(84.0, 62 + i // 3) for i in range(24)]                 # 회복 → ok (70 % 까지)
         + [(84.0, 70)] * 36                                        # 12분 만충 쪽 평탄 충전
         + [(25.0, 70 - i // 9) for i in range(90)])               # 다시 멈춤 · 끊었다 켜도 안 풀림
    got = [a for _, a in feed_series(watch(), s)]
    assert got == ["kick", "ok", "kick", "kick", "fail"]


def test_rule_a_band_reaches_45_w_but_the_other_floor_rules_keep_35():
    """자리 이동 뒤 Y 는 만충 직후 38 W 를 읽는다(11-17) — 그 높이에서 멈춰도 규칙 A 가 본다. 셀을 모르는 쪽은 35 W 그대로."""
    assert SAFETY["stall_w_max"] == 45.0 and Config().floor_w_max == 35.0
    assert [a for _, a in feed_series(watch(), [(38.0, 80 - i // 9) for i in range(12)])] == ["kick"]
    assert feed_series(watch(floor_w=35.0), [(38.0, 80 - i // 9) for i in range(12)]) == []
    assert feed_series(watch(), [(42.0, 96 + i // 30) for i in range(60)]) == []      # 만충 직전 — 셀이 95 % 넘게 오르면 보지 않는다


def test_engine_uses_the_rule_a_band(tmp_path, monkeypatch):
    plug = SeqPlug()
    r, st, step, _ = stall_runner(tmp_path, monkeypatch, plug, 63)
    assert r._stall.floor_w == SAFETY["stall_w_max"]
    for v in [63] * 5 + [62] * 5:
        step("CHARGE", 38.5, v)
    assert plug.calls == ["recharge"] and kinds(tmp_path) == ["dock_stall"]
