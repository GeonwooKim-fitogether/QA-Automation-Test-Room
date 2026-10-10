"""다중 세트 6단계 — 엔진 안 짝 검증기 · 관리자 · engine.json verifying · 감시자 보호 (명세 3-2 ~ 3-5, B-L6)."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

import sim_world as sw
import test_supervisor as ts
from cellbench import config as configmod
from cellbench import verifier as V
from cellbench.config import PAIR, Config
from cellbench.record import Recorder
from cellbench.shared import ExtractGate
from cellbench.supervisor import tick

S2 = list(range(11594, 11618))
P2 = "AA:BB:CC:DD:EE:02"
ENTRY2 = {"id": 2, "plug_mac": P2, "serials": "11594-11617", "registered_at": 1760000000}


def _cfg(tmp):
    return Config(data_dir=str(tmp / "data"), sets=[dict(configmod.DEFAULT_SET), dict(ENTRY2)])


def _verifier(tmp, world, mode="verify", feed_k=P2):
    cfg = _cfg(tmp)
    kcfg = configmod.set_cfg(cfg, 2)
    live = sw.FakeLive(world)
    rec = Recorder(Path(kcfg.data_dir), set_id=2)
    faults = []
    v = V.PairVerifier(cfg, 2, live, sw.FakePlug(world, kcfg), sw.FakeLink(world, kcfg, live), rec,
                       gate=ExtractGate(clock=world.clock.time), mode=mode, groups={"세트 1": sw.SET1},
                       on_fault=faults.append, now=world.clock.time, sleep=world.clock.sleep)
    return v, faults


def _world(set2_feeder=P2, start2=60.0, limit_h=8.0):
    return sw.World([sw.Dock(sw.SET1_MAC, sw.SET1, batt={s: 50.0 for s in sw.SET1}),
                     sw.Dock(set2_feeder, S2, batt={s: start2 + (s % 3) * 0.4 for s in S2})],
                    rate_pct_min=0.4, discharge_rate=0.34, limit_h=limit_h, plugs={P2: True})


def test_fingerprint_changes_with_registration_nonce():
    a = V.fingerprint(ENTRY2)
    assert a == V.fingerprint(dict(ENTRY2)) and a != V.fingerprint({**ENTRY2, "registered_at": 1760000001})   # D11 · A-H5


def test_judged_verification_refused_before_calibration(tmp_path):
    w = _world()
    v, _ = _verifier(tmp_path, w)
    doc = v.run()
    assert doc["state"] == "refused" and "보정" in doc["why"] and w.plugs[P2] is True   # 세트 2 운전 차단이 기본
    assert not V.pair_ok(_cfg(tmp_path), 2)


def test_calibration_records_without_verdict(tmp_path):
    w = _world()
    v, faults = _verifier(tmp_path, w, mode="calib")
    doc = v.run()
    calib = list((tmp_path / "data" / "set2").glob("calib_*.csv"))
    assert doc["state"] == "calib_done" and calib and not faults and w.plugs[P2] is True
    rows = calib[0].read_text(encoding="utf-8-sig").splitlines()
    assert len(rows) > 150 and rows[0].startswith("t,plug_on,watts,") and str(sw.SET1[0]) in rows[0]   # 세트 1 무리도 적는다(r 분포 · U2)


@pytest.fixture
def calibrated(monkeypatch):
    monkeypatch.setitem(PAIR, "calibrated", True)


def test_correct_pair_passes_and_unlocks_gate(tmp_path, calibrated):
    w = _world()
    v, faults = _verifier(tmp_path, w)
    doc = v.run()
    assert doc["state"] == "passed", doc["why"]
    assert doc["quick"]["verdict"] == "pass" and doc["pattern"]["verdict"] == "pass" and not faults
    assert w.plugs[P2] is True and V.pair_ok(_cfg(tmp_path), 2)
    assert "extract" in json.dumps(doc) or (tmp_path / "data" / "set2" / "ftg" / "pair").is_dir()   # 검증 전 추출 · 삭제
    assert doc["off_minutes"] > 0


def test_plug_feeding_set1_dock_fails_and_raises_fault(tmp_path, calibrated):
    """P2 가 세트 1 의 Dock 을 먹인다(세트 2 Dock 은 아무도 안 먹임) — 빠른 검증 ③ 이 세트 1 무리의 움직임을 잡는다."""
    w = sw.World([sw.Dock(P2, sw.SET1, batt={s: 50.0 for s in sw.SET1}),
                  sw.Dock("00:00:00:00:00:00", S2, batt={s: 60.0 for s in S2})], rate_pct_min=0.4, discharge_rate=0.34,
                 limit_h=8.0, plugs={P2: True, "00:00:00:00:00:00": True})
    v, faults = _verifier(tmp_path, w)
    doc = v.run()
    assert doc["state"] == "failed" and faults and "세트 1" in doc["why"]
    assert w.plugs[P2] is True and not V.pair_ok(_cfg(tmp_path), 2)


def test_prep_discharge_requires_3pct_in_30_min(tmp_path, calibrated):
    """세트 2 셀이 90 % — 준비 방전을 하는데 P2 가 세트 2 Dock 을 먹이지 않으면(꺼도 안 내려감) 30분에 짝 이상."""
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1, batt={s: 50.0 for s in sw.SET1}),
                  sw.Dock("00:00:00:00:00:00", S2, batt={s: 90.0 for s in S2})], rate_pct_min=0.0, discharge_rate=0.34,
                 limit_h=8.0, plugs={P2: True, "00:00:00:00:00:00": True})
    v, faults = _verifier(tmp_path, w)
    doc = v.run()
    assert doc["state"] == "failed" and "준비 방전" in doc["why"] and faults and w.plugs[P2] is True


def test_prep_discharge_then_pass(tmp_path, calibrated):
    w = _world(start2=85.0)
    v, _ = _verifier(tmp_path, w)
    doc = v.run()
    assert doc["state"] == "passed", doc["why"]
    assert doc["median_start"] > 70


def test_abort_turns_plug_on(tmp_path, calibrated):
    w = _world()
    v, faults = _verifier(tmp_path, w)
    w.at(20 * 60, v.cancelled.set)                      # 끔 구간 어딘가에서 사람이 '멈춤'
    doc = v.run()
    assert doc["state"] == "inconclusive" and "멈춤" in doc["why"] and w.plugs[P2] is True and not faults


# ---------- 관리자 ----------

class FakeRunner:
    def __init__(self, cfg):
        self.cfg, self.gate, self.current, self.external_fault = cfg, None, None, None

    def _batts(self):
        return []


def _manager(tmp_path, world, now):
    cfg = _cfg(tmp_path)
    rec0 = Recorder(tmp_path / "data")
    rec0.engine_start({"pid": 1, "started": 0})
    plugs = ["plug1"]
    made = []

    def make_plug(kcfg):
        p = sw.FakePlug(world, kcfg); made.append(p); return p
    m = V.VerifyManager(cfg, sw.FakeLive(world), rec0, make_plug, lambda kcfg, r: sw.FakeLink(world, kcfg, None),
                        lambda k, kcfg: Recorder(Path(kcfg.data_dir), set_id=k), plugs, now=now, start_thread=False)
    return m, rec0, plugs, made


def _engine(tmp):
    return json.loads((tmp / "data" / "engine.json").read_text(encoding="utf-8"))


def test_manager_starts_one_verifier_records_verifying_and_turns_gate_on(tmp_path):
    w = _world(); t = [100.0]
    m, rec0, plugs, _ = _manager(tmp_path, w, lambda: t[0])
    r = FakeRunner(m.cfg)
    (tmp_path / "data" / "set2").mkdir(parents=True)
    (tmp_path / "data" / "set2" / "pair_request.json").write_text(json.dumps({"action": "start", "mode": "calib"}))
    m.tick(r)
    assert m.active is not None and m.active.mode == "calib" and r.gate is not None      # 단일 모드에서도 추출 문을 켠다
    assert _engine(tmp_path)["verifying"] == [{"id": 2, "mac": P2, "ip": ""}] and len(plugs) == 2
    assert not (tmp_path / "data" / "set2" / "pair_request.json").exists()
    m.finish(m.active)
    assert "verifying" not in _engine(tmp_path) and plugs == ["plug1"]


def test_manager_stall_turns_plug_on(tmp_path):
    w = _world(); t = [100.0]
    m, _, _, made = _manager(tmp_path, w, lambda: t[0])
    r = FakeRunner(m.cfg)
    (tmp_path / "data" / "set2").mkdir(parents=True)
    (tmp_path / "data" / "set2" / "pair_request.json").write_text(json.dumps({"action": "start"}))
    m.tick(r)
    v = m.active
    w.plugs[P2] = False                                   # 검증기가 끔 구간에서 멈췄다
    v.beat = 100.0; t[0] = 100.0 + V.STALL_S + 1
    m.tick(r)
    assert v.stalled and v.cancelled.is_set() and w.plugs[P2] is True and "recharge" in w.plug_calls[P2]


def test_external_fault_makes_runner_turn_all_plugs_on(tmp_path, monkeypatch):
    """검증기가 찾은 짝 이상 → 실행기가 다음 표본에서 '모든 플러그 켬' + failsafe (명세 3-6)."""
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1), sw.Dock(P2, S2)], plugs={P2: False})
    real = sw.run_cycle.CycleRunner
    box = {}

    def make(*a, **k):
        box["r"] = real(*a, **k); return box["r"]
    monkeypatch.setattr(sw.run_cycle, "CycleRunner", make)
    w.at(10 * 60, lambda: setattr(box["r"], "external_fault", "세트 2 짝 검증 실패 — 시험"))
    bench = {"sets": [dict(configmod.DEFAULT_SET), dict(ENTRY2)]}
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"], bench=bench)
    ev = (tmp_path / "data" / "events.csv").read_text(encoding="utf-8-sig")
    assert code == 0 and "세트 2 짝 검증 실패" in ev and _engine(tmp_path)["exit"] == "failsafe" and w.plugs[P2] is True


# ---------- 감시자 (B-L6 — verifying 과 같은 단계) ----------

def test_supervisor_turns_on_verifying_plug_once_per_incident(tmp_path):
    ts.put(tmp_path, ts.eng(target_last_cycle=4, verifying=[{"id": 2, "mac": P2, "ip": "192.168.1.120"}]),
           ts.now_json(4000), cycles=1)
    world = ts.World()
    d = world.deps()
    seen = []
    d.plug_on_mac = lambda mac, ip: (seen.append((mac, ip)), "켜짐")[1]
    rep, lines = tick(ts.cfg_for(tmp_path), tmp_path, None, d, ts.T)
    assert seen == [(P2, "192.168.1.120")] and any("엔진이 쥐었던 세트 2" in l for l in lines)
    rep2, _ = tick(ts.cfg_for(tmp_path), tmp_path, rep, d, ts.T + 60)
    assert len(seen) == 1                                  # 같은 사건에서 한 번만


def test_supervisor_leaves_verifying_plug_alone_while_engine_ok(tmp_path):
    ts.put(tmp_path, ts.eng(target_last_cycle=4, verifying=[{"id": 2, "mac": P2, "ip": ""}]), ts.now_json(10), cycles=1)
    world = ts.World(alive={4242: ts.T - 3600})        # engine.json 의 started 와 같은 프로세스
    d = world.deps(); seen = []
    d.plug_on_mac = lambda mac, ip: seen.append(mac) or "x"
    tick(ts.cfg_for(tmp_path), tmp_path, None, d, ts.T)
    assert seen == []                                      # 엔진이 살아 있으면 검증기 · 관리자의 몫이다


def test_g13_set2_records_send_nothing_before_v2_and_slack_is_labelled(tmp_path):
    """G13 — 단일 모드에서 세트 2 검증 기록은 판 1 이면 클라우드에 0건(세트 1 의 행을 덮지 않는다), Slack 은 '세트 2' 가 붙은 글만 더해진다."""
    w = _world()
    cloud = sw.capture_cloud(w)(bench_id="hq-bench-1")
    cloud.schema = 1
    alerts = []
    rec0 = Recorder(tmp_path / "data", alert=alerts.append, cloud=cloud)
    m = sw.run_cycle.verify_manager(_cfg(tmp_path), sw.FakeLive(w), rec0, alerts.append, [])
    r2 = m.make_rec(2, configmod.set_cfg(_cfg(tmp_path), 2))
    r2.event(0, "PAIR", "pair_mismatch", "-", "시험")
    r2.now({"cycle": 0, "phase": "PAIR"})
    assert w.requests == [] and alerts and all("세트 2" in a for a in alerts)
    assert (tmp_path / "data" / "set2" / "events.csv").exists() and not (tmp_path / "data" / "events.csv").read_text(
        encoding="utf-8-sig").count("pair_mismatch")


# ---------- 코드 검토 2 — 준비 방전의 시간 울타리 · 고리 밖 감시 ----------

def test_prep_discharge_ends_inconclusive_when_cells_go_silent(tmp_path, calibrated):
    """준비 방전 중 세트 2 셀이 들리지 않게 됐다 — 고치기 전에는 'continue' 가 3시간 울타리까지 건너뛰어 P2 가 꺼진 채 끝없이 돌았다.
    이제 PREP_BLIND_S(10분) 안에 판정 불가로 끝나고 P2 를 켠다."""
    w = _world(start2=90.0, limit_h=30.0)
    v, faults = _verifier(tmp_path, w)
    real_set = v._set
    at = {}

    def set_then_blind(on):
        r = real_set(on)
        if not on and "t" not in at:
            at["t"] = w.clock.t
            w.blind_from, w.blind_until = w.clock.t + 120, w.clock.t + 40 * 3600
        return r
    v._set = set_then_blind
    doc = v.run()
    assert doc["state"] == "inconclusive" and "들리지 않음" in doc["why"], doc["why"]
    assert w.plugs[P2] is True and not faults
    assert w.clock.t - at["t"] < 120 + V.PREP_BLIND_S + 180                 # 3시간이 아니라 10분 남짓


def test_prep_discharge_time_fence_is_checked_even_without_cells(tmp_path, calibrated, monkeypatch):
    """울타리는 셀이 들리는지와 무관하게 먼저 본다 — 못 읽는 상한을 울타리보다 길게 해도 3시간에 끝난다."""
    monkeypatch.setattr(V, "PREP_BLIND_S", 10 * 3600.0)
    w = _world(start2=90.0, limit_h=30.0)
    v, _ = _verifier(tmp_path, w)
    real_set = v._set

    def set_then_blind(on):
        r = real_set(on)
        if not on and w.blind_until < 0:
            w.blind_from, w.blind_until = w.clock.t + 120, w.clock.t + 40 * 3600
        return r
    v._set = set_then_blind
    v.run()
    assert w.clock.t - sw.T0 < 4 * 3600 and w.plugs[P2] is True


def test_manager_watch_handles_abort_without_runner_tick(tmp_path):
    """단일 모드에서 세트 1 이 RECOVER · 추출 중이면 tick 이 오지 않는다 — 감시 스레드가 멈춤 요청과 심박을 본다."""
    import threading
    import time as _t
    w = _world(); t = [100.0]
    cfg = _cfg(tmp_path)
    rec0 = Recorder(tmp_path / "data"); rec0.engine_start({"pid": 1, "started": 0})
    started = threading.Event()

    def run(self):
        started.set()
        self.cancelled.wait(5)
        return {"state": "inconclusive"}
    m = V.VerifyManager(cfg, sw.FakeLive(w), rec0, lambda kcfg: sw.FakePlug(w, kcfg),
                        lambda kcfg, r: sw.FakeLink(w, kcfg, None), lambda k, kcfg: Recorder(Path(kcfg.data_dir), set_id=k),
                        ["plug1"], now=lambda: t[0], start_thread=True, sleep=lambda s: _t.sleep(0.01))
    V.PairVerifier.run, real = run, V.PairVerifier.run
    try:
        (tmp_path / "data" / "set2").mkdir(parents=True)
        req = tmp_path / "data" / "set2" / "pair_request.json"
        req.write_text(json.dumps({"action": "start", "mode": "calib"}))
        m.tick(FakeRunner(cfg))                                   # 시작만 tick 이 한다 — 이 뒤로 tick 은 부르지 않는다
        v = m.active
        assert started.wait(2) and m.watcher is not None and m.watcher.is_alive()
        req.write_text(json.dumps({"action": "abort"}))
        m.thread.join(3)
        assert v.cancelled.is_set() and not req.exists() and m.active is None
    finally:
        V.PairVerifier.run = real


def test_manager_watch_turns_stalled_verifier_plug_on(tmp_path):
    w = _world(); t = [100.0]
    m, _, _, _ = _manager(tmp_path, w, lambda: t[0])
    (tmp_path / "data" / "set2").mkdir(parents=True)
    (tmp_path / "data" / "set2" / "pair_request.json").write_text(json.dumps({"action": "start"}))
    m.tick(FakeRunner(m.cfg))
    v = m.active
    w.plugs[P2] = False
    v.beat = 100.0; t[0] = 100.0 + V.STALL_S + 1
    m.watch()                                                      # tick 없이
    assert v.stalled and w.plugs[P2] is True
