"""다중 세트 7단계 — 조정자 · 세트 스레드 · HOLD · 재구성 · 울타리 · 감시자 (명세 2-1 · 2-2 · 2-7 · 2-8 · 4절 · D6 · D9).

다중 모드는 세트 2 운전 차단이 기본이라 관문(U1 · U2)을 실제로 열 수 없다. 그래서 여기서는 관문 함수(bench.gate_problems)를
바꿔 끼워 다중 모드로 들어간다 — 관문 자체가 닫혀 있는지는 test_default_state_keeps_set2_blocked 가 본다.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import run_cycle
import sim_world as sw
import test_supervisor as ts
from cellbench import bench as B
from cellbench import config as configmod
from cellbench.supervisor import engine_argv, tick

S2 = list(range(11594, 11618))
P2 = "AA:BB:CC:DD:EE:02"


def _bench(t1=1, t2=1, run2=True):
    return {"bench_id": "hq-bench-1",
            "sets": [{**configmod.DEFAULT_SET, "target_cycles": t1},
                     {"id": 2, "plug_mac": P2, "serials": "11594-11617", "run": run2, "target_cycles": t2, "registered_at": 1}]}


def _world(**kw):
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1), sw.Dock(P2, S2, batt={s: 70.0 for s in S2})], limit_h=kw.pop("limit_h", 10), **kw)
    w.clock.lockstep = True
    return w


@pytest.fixture
def gates_open(monkeypatch):
    monkeypatch.setattr(B, "gate_problems", lambda cfg, k, v2: [])


def _read(tmp, rel):
    p = tmp / "data" / rel
    return p.read_text(encoding="utf-8-sig") if p.exists() else ""


def _engine(tmp):
    return json.loads(_read(tmp, "engine.json"))


# ---------- 기본 상태 — 세트 2 운전 차단 ----------

def test_default_state_keeps_set2_blocked(tmp_path, monkeypatch):
    """G5 — run 을 켜도 짝 · 보정 · 스키마 관문이 닫혀 있어 단일 모드다. set_not_ready 는 사유가 바뀔 때 한 번."""
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1)])
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"], bench=_bench())
    ev = _read(tmp_path, "events.csv")
    assert code == 0 and ev.count("set_not_ready") == 1 and "보정 실측(U2) 전" in ev and "mode" not in _engine(tmp_path)
    assert P2 not in w.plug_calls                                   # 세트 2 플러그에 한 번도 닿지 않았다
    w2 = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1)])
    sw.run_main(monkeypatch, tmp_path, w2, ["--cycles", "1"], bench=_bench())
    assert _read(tmp_path, "events.csv").count("set_not_ready") == 1   # 같은 사유는 다시 남기지 않는다


# ---------- 다중 모드 ----------

def test_two_sets_run_their_own_cycles_and_extract_one_at_a_time(tmp_path, monkeypatch, gates_open):
    w = _world()
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"], bench=_bench())
    eng = _engine(tmp_path)
    assert code == 0 and eng["mode"] == "multi" and eng["exit"] == "done" and eng["plug_on"] is True
    assert [s["id"] for s in eng["sets"]] == [1, 2] and eng["sets"][1]["mac"] == P2 and eng["sets"][1]["target"] == 1
    assert len(_read(tmp_path, "cycles.csv").splitlines()) == 2 and len(_read(tmp_path, "set2/cycles.csv").splitlines()) == 2
    assert (tmp_path / "data" / "beat.json").exists() and (tmp_path / "data" / "set2" / "now.json").exists()
    spans = sorted(w.extracts)
    assert {m for _, _, m in spans} == {sw.SET1_MAC, P2}
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))     # 추출이 겹치지 않았다 (추출 문)
    assert w.plugs[sw.SET1_MAC] and w.plugs[P2]                       # 둘 다 켜 두고 끝났다
    assert "세트 2" in " ".join(a for a in w.alerts if "사이클 1 끝" in a)  # 세트 2 의 글에만 세트 표기


def test_set_stop_holds_only_that_set(tmp_path, monkeypatch, gates_open):
    w = _world()
    w.at(15 * 60, lambda: (tmp_path / "data" / "set2").mkdir(parents=True, exist_ok=True) or
         sw.controlmod.write_json_atomic(tmp_path / "data" / "set2" / "control.json",
                                         {"cmd": "stop_safe", "source": "t", "t": w.clock.t}))
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"], bench=_bench())
    assert code == 0 and _engine(tmp_path)["exit"] == "stopped"
    assert (tmp_path / "data" / "set2" / "hold").exists()             # 재시작을 넘어 유지되는 세트 정지 표지
    assert len(_read(tmp_path, "cycles.csv").splitlines()) == 2        # 세트 1 은 사이클을 끝냈다
    assert "manual_stop_charge" not in _read(tmp_path, "cycles.csv") and w.plugs[P2] is True
    assert "HOLD" in _read(tmp_path, "set2/holds.csv")


def test_untargeted_plug_command_is_rejected_in_multi(tmp_path, monkeypatch, gates_open):
    w = _world()
    w.at(12 * 60, lambda: sw.controlmod.write_json_atomic(tmp_path / "data" / "control.json",
                                                          {"cmd": "plug_off", "source": "t", "t": w.clock.t}))
    sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"], bench=_bench())
    assert "세트를 지정하지 않아 버림" in _read(tmp_path, "events.csv")


def test_pair_fault_in_one_set_turns_on_all_plugs_and_holds_all(tmp_path, monkeypatch, gates_open):
    """플러그가 바뀌어 꽂혔다: 세트 1 의 셀은 P2 가, 세트 2 의 셀은 P1 이 먹인다. 세트 2 는 거의 찼으므로 예비 충전을 먼저 끝내고
    P2 를 끈다 — 그 순간 아직 예비 충전 중인 세트 1 의 셀이 내려간다((a)), 세트 2 의 셀은 안 내려간다((c)) → 모든 플러그 켬 · 모든 세트 HOLD."""
    w = sw.World([sw.Dock(P2, sw.SET1, batt={s: 40.0 for s in sw.SET1}), sw.Dock(sw.SET1_MAC, S2, batt={s: 98.0 for s in S2})],
                 limit_h=10, rate_pct_min=0.5, discharge_rate=0.34)
    w.clock.lockstep = True
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"], bench=_bench(t1=3, t2=3))
    eng = _engine(tmp_path)
    assert code == 0 and eng["exit"] == "failsafe" and w.plugs[sw.SET1_MAC] and w.plugs[P2]
    assert "pair_mismatch" in _read(tmp_path, "set2/events.csv") + _read(tmp_path, "events.csv")
    ev = _read(tmp_path, "events.csv") + _read(tmp_path, "set2/events.csv")
    assert "플러그(1개)" not in ev                                     # 세트 2 실행기도 세트 1 플러그를 켠다(다른 플러그 2개 = 자기 + 상대)
    assert "set_hold" in ev or ev.count("pair_mismatch") >= 2          # 상대 세트는 HOLD 로 가거나, 자기도 같은 순간 짝 이상을 잡았다


def test_end_request_reconfigures_with_all_plugs_on(tmp_path, monkeypatch, gates_open):
    w = _world()
    w.at(5 * 60, lambda: (tmp_path / "data" / "end_request").write_text("t"))
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"], bench=_bench(t1=5, t2=5))
    assert code == 0 and _engine(tmp_path)["exit"] == "reconfigure" and w.plugs[sw.SET1_MAC] and w.plugs[P2]
    assert not (tmp_path / "data" / "end_request").exists()            # 읽는 순간 지웠다
    assert "reconfigure" in _read(tmp_path, "cycles.csv") + _read(tmp_path, "set2/cycles.csv")


def test_single_mode_reconfigures_at_full_with_plug_on(tmp_path, monkeypatch):
    """단일 모드 — 바라는 운전 목록이 바뀌면 세트 1 의 만충 직후(PLUG_OFF 전) 플러그를 켠 채 끝낸다 (D6)."""
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1)])
    monkeypatch.setattr(run_cycle, "desired_now", lambda args, rec: [1, 2])
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "3"], bench=_bench())
    rows = _read(tmp_path, "cycles.csv").splitlines()
    assert code == 0 and _engine(tmp_path)["exit"] == "reconfigure" and w.plugs[sw.SET1_MAC] is True
    assert len(rows) == 2 and "reconfigure" in rows[1] and rows[1].split(",")[16] == ""   # plug_off 비어 있음 — 끄지 않았다


def test_fence_after_two_stale_checks():
    now = [10_000.0]
    rec = SimpleNamespace(log=lambda m: None, event=lambda *a: None)
    b = B.BenchRunner.__new__(B.BenchRunner)
    b.now, b.started, b.rec0 = (lambda: now[0]), 0.0, rec
    r = SimpleNamespace(rec=SimpleNamespace(_last_now={"beat": now[0] - B.FENCE_S - 1, "phase": "CHARGE"}))
    s = B.SetThread(2, r, 1, False, None)
    b.sets = {2: s}
    beats = {"2": {"beat": r.rec._last_now["beat"], "phase": "CHARGE"}}
    assert b._fence_due(beats) is None and b._fence_due(beats) == 2   # 2회 연속에서
    s.stale_ticks = 0
    assert b._fence_due({"2": {"beat": now[0] - 10, "phase": "CHARGE"}}) is None


def test_start_phase_rule(tmp_path):
    cfg = configmod.Config(data_dir=str(tmp_path), sets=_bench()["sets"])
    assert B.start_with_precharge(cfg, 2, False, None) is True                  # 기록 없음
    for ph, prev, want in (("DISCHARGE", None, False), ("CHARGE", None, True), ("FULL", "reconfigure", False), ("FULL", None, True)):
        (tmp_path / "set2").mkdir(exist_ok=True)
        (tmp_path / "set2" / "now.json").write_text(json.dumps({"phase": ph}))
        assert B.start_with_precharge(cfg, 2, False, prev) is want, ph
    assert B.start_with_precharge(cfg, 2, True, None) is True                   # 감시자의 --precharge


def test_completed_cycles_counts_only_full_rows(tmp_path):
    p = tmp_path / "cycles.csv"
    p.write_text("cycle,discharge_start,full_at\n1,a,b\n2,a,\n3,,\n4,a,b\n", encoding="utf-8-sig")
    assert B.completed_cycles(p) == 2


# ---------- 감시자 ----------

def test_supervisor_reconfigure_restarts_without_precharge(tmp_path):
    ts.put(tmp_path, ts.eng(exit="reconfigure", target_last_cycle=4), ts.now_json(30, phase="FULL"), cycles=1)
    w = ts.World()
    rep, lines = tick(ts.cfg_for(tmp_path), tmp_path, None, w.deps(), ts.T)
    assert w.acts() == ["plug_on", "engine:3"] and any("예비 충전 없이" in l or "--cycles 3" in l for l in lines)
    assert "--precharge" not in " ".join(engine_argv("py", Path("."), 3, {}, precharge=False))
    assert any("--precharge" not in l for l in lines if "엔진 다시 시작" in l)


def test_supervisor_fenced_is_revived_like_dead(tmp_path):
    ts.put(tmp_path, ts.eng(exit="fenced", target_last_cycle=4), ts.now_json(30), cycles=1)
    w = ts.World()
    tick(ts.cfg_for(tmp_path), tmp_path, None, w.deps(), ts.T)
    assert w.acts() == ["plug_on", "engine:3"]


def test_supervisor_uses_beat_json_in_multi_and_turns_on_every_set(tmp_path):
    sets = [{"id": 1, "mac": "M1", "ip": "", "target": 5}, {"id": 2, "mac": P2, "ip": "1.2.3.4", "target": 5}]
    ts.put(tmp_path, ts.eng(mode="multi", sets=sets), ts.now_json(4000), cycles=1)   # now.json(세트 1)은 낡았다
    (tmp_path / "beat.json").write_text(json.dumps({"t": ts.T - 10, "beat": ts.T - 10}))
    w = ts.World(alive={4242: ts.T - 3600})
    d = w.deps(); seen = []
    d.plug_on_mac = lambda mac, ip: seen.append(mac) or "켜짐"
    rep, _ = tick(ts.cfg_for(tmp_path), tmp_path, None, d, ts.T)
    assert rep["checks"]["engine"] == "ok" and seen == []              # 조정자가 살아 있다 — 세트 1 now.json 이 낡아도
    (tmp_path / "beat.json").write_text(json.dumps({"t": ts.T - 4000, "beat": ts.T - 4000}))
    w2 = ts.World()
    d2 = w2.deps(); seen2 = []
    d2.plug_on_mac = lambda mac, ip: seen2.append((mac, ip)) or "켜짐"
    rep2, _ = tick(ts.cfg_for(tmp_path), tmp_path, None, d2, ts.T)
    assert rep2["checks"]["engine"] == "dead" and seen2 == [(P2, "1.2.3.4")] and "plug_on" in w2.acts()


def test_g14_stale_beat_json_is_ignored_in_single_mode(tmp_path):
    """다중 → 단일로 돌아온 뒤 낡은 beat.json 이 남아도 판정 열쇠는 engine.json 의 mode 다 (B-M6)."""
    ts.put(tmp_path, ts.eng(), ts.now_json(10), cycles=1)
    (tmp_path / "beat.json").write_text(json.dumps({"t": ts.T - 9999, "beat": ts.T - 9999}))
    w = ts.World(alive={4242: ts.T - 3600})
    rep, _ = tick(ts.cfg_for(tmp_path), tmp_path, None, w.deps(), ts.T)
    assert rep["checks"]["engine"] == "ok" and w.acts() == []


def test_config_error_keeps_sets_for_supervisor(tmp_path):
    (tmp_path / "engine.json").write_text(json.dumps({"exit": "reconfigure", "mode": "multi", "sets": [{"id": 2, "mac": P2}],
                                                      "pid": 1, "started": 0}))
    run_cycle.config_error(tmp_path, SimpleNamespace(cycles=1, config=None), "깨진 설정")
    eng = json.loads((tmp_path / "engine.json").read_text(encoding="utf-8"))
    assert eng["exit"] == "config_error" and eng["sets"] == [{"id": 2, "mac": P2}] and eng["mode"] == "multi"


@pytest.mark.parametrize("b2", [70.0, 80.0, 100.0])
def test_two_running_sets_do_not_cross_watch_each_other(tmp_path, monkeypatch, gates_open, b2):
    """코드 검토 1 (검토자 재현 파일 rv/test_probe_sync.py 를 옮김) — 세트 1 · 2 가 함께 돌면 세트 2 는 자기 방전으로 내려간다.
    세트 1 의 끄기와 시각이 겹쳐도 (d) 교차 감시가 이것을 짝 이상으로 읽으면 안 된다. 운전 세트는 무리에서 빠진다.
    세트 3 은 등록만 돼 있고 운전하지 않는다 — 두 실행기 모두 세트 3 만 본다."""
    seen = {}
    init = B.BenchRunner.__init__

    def spy(self, *a, **k):
        init(self, *a, **k)
        seen.update({kk: set(s.runner._cross.groups) for kk, s in self.sets.items()})

    monkeypatch.setattr(B.BenchRunner, "__init__", spy)
    bench = _bench(2, 2)
    bench["sets"].append({"id": 3, "plug_mac": "AA:BB:CC:DD:EE:03", "serials": "11618-11641", "registered_at": 1})
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1), sw.Dock(P2, S2, batt={s: b2 for s in S2})], limit_h=10)
    w.clock.lockstep = True
    sw.run_main(monkeypatch, tmp_path, w, ["--precharge", "--cycles", "2"], bench=bench)
    ev = _read(tmp_path, "events.csv") + _read(tmp_path, "set2/events.csv")
    assert "pair_mismatch" not in ev
    assert json.loads(_read(tmp_path, "engine.json"))["exit"] == "done"
    assert seen == {1: {3}, 2: {3}}


def test_verify_failure_in_multi_mode_goes_straight_to_coordinator(tmp_path, monkeypatch, gates_open):
    """코드 검토 6 — 다중 모드의 짝 검증 실패는 세트 1 실행기의 external_fault 가 아니라 조정자의 verify_fault 로 곧바로 간다.
    운전하지 않는 세트 3 의 플러그(꺼져 있음)를 끊지 않고 켜고, 운전 세트 1 · 2 를 모두 세트 정지로 보내고, failsafe 로 끝난다."""
    P3 = "AA:BB:CC:DD:EE:03"
    box = {}
    init = B.BenchRunner.__init__

    def spy(self, *a, **k):
        init(self, *a, **k)
        box["b"] = self

    monkeypatch.setattr(B.BenchRunner, "__init__", spy)
    bench = _bench(3, 3)
    bench["sets"].append({"id": 3, "plug_mac": P3, "serials": "11618-11641", "registered_at": 1})
    w = _world(plugs={P3: False})
    w.at(40 * 60, lambda: box["b"].verify_fault("세트 3 짝 검증 실패 — 시험"))
    sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "3"], bench=bench)
    eng = json.loads(_read(tmp_path, "engine.json"))
    assert eng["exit"] == "failsafe"
    assert "세트 3 짝 검증 실패" in _read(tmp_path, "events.csv")
    assert w.plugs[P3] is True and w.plug_calls[P3] == ["on"]                     # 끊지 않고 켰다
    holds = _read(tmp_path, "holds.csv") + _read(tmp_path, "set2/holds.csv")
    assert holds.count("enter,HOLD") >= 2                                         # 세트 1 · 2 모두 세트 정지


def test_manager_uses_fault_sink_when_given(tmp_path):
    from test_verifier import FakeRunner, _manager, _world as vworld
    w = vworld(); t = [100.0]
    m, _, _, _ = _manager(tmp_path, w, lambda: t[0])
    got = []
    m.fault_sink = got.append
    r = FakeRunner(m.cfg)
    (tmp_path / "data" / "set2").mkdir(parents=True)
    (tmp_path / "data" / "set2" / "pair_request.json").write_text(json.dumps({"action": "start"}))
    m.tick(r)
    m.active.on_fault("x")
    assert got == ["x"] and r.external_fault is None
