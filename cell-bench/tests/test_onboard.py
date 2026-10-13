"""자동 합류 — PIN 한 번으로 보정 → 문턱 도출 → 짝 검증 → 운전 켜기 → 합류까지 (cellbench/onboard.py · VerifyManager).

가짜 세상(tests/sim_world.py)으로 본다. 장비 · 실제 시계 · 실제 data 폴더에 닿지 않는다.
  맞는 짝이 끝까지 간다 / 틀린 짝이 멈추고 운전을 켜지 않는다 / 일부 셀 무반응 · 노이즈는 inconclusive 로 멈춘다 /
  세트 1 단계 전환과 겹치면 미룬다(한도를 넘으면 중단) / 엔진이 다시 시작되면 aborted / 운전 관문이 닫혀 있으면 켜지 않는다 /
  이미 통과한 짝은 운전 켜기부터 이어 간다 / 합류 기다림(경계 재구성 · 첫 방전)을 본다.
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import sim_world as sw
from cellbench import config as configmod
from cellbench import onboard as OB
from cellbench import verifier as V
from cellbench.config import PAIR, Config
from cellbench.control import write_json_atomic
from cellbench.record import ALERT_KINDS, EVENT_LIGHT, Recorder

S2 = list(range(11594, 11618))
P2 = "AA:BB:CC:DD:EE:02"
ENTRY2 = {"id": 2, "plug_mac": P2, "serials": "11594-11617", "registered_at": 1760000000}


def _world(feeder=P2, start2=60.0, limit_h=8.0, set1_feeder=sw.SET1_MAC, extra_docks=()):
    return sw.World([sw.Dock(set1_feeder, sw.SET1, batt={s: 50.0 for s in sw.SET1}),
                     sw.Dock(feeder, S2, batt={s: start2 + (s % 3) * 0.4 for s in S2}), *extra_docks],
                    rate_pct_min=0.4, discharge_rate=0.34, limit_h=limit_h, plugs={P2: True})


class FakeRunner:
    """세트 1 실행기 — 짝 검증기가 보는 것(단계 · 셀 잔량 · 추출 문 · 외부 이상)만."""

    def __init__(self, cfg, phase="DISCHARGE"):
        self.cfg, self.gate, self.external_fault = cfg, None, None
        self.current = SimpleNamespace(phase=phase)

    def _batts(self):
        return [60]


class Rig:
    def __init__(self, tmp, world, schema=True, phase="DISCHARGE"):
        self.tmp, self.world = tmp, world
        tmp.mkdir(parents=True, exist_ok=True)
        self.bench = tmp / "bench.json"
        self.bench.write_text(json.dumps({"sets": [dict(configmod.DEFAULT_SET), dict(ENTRY2)]}, ensure_ascii=False), encoding="utf-8")
        self.cfg = Config(data_dir=str(tmp / "data"), sets=[dict(configmod.DEFAULT_SET), dict(ENTRY2)])
        self.alerts: list[str] = []
        rec0 = Recorder(tmp / "data", alert=self.alerts.append)
        rec0.engine_start({"pid": 1, "started": 0})
        self.rec0, self.plugs = rec0, ["plug1"]
        self.m = V.VerifyManager(self.cfg, sw.FakeLive(world), rec0, lambda kcfg: sw.FakePlug(world, kcfg),
                                 lambda kcfg, r: sw.FakeLink(world, kcfg, None),
                                 lambda k, kcfg: Recorder(Path(kcfg.data_dir), alert=self.alerts.append, set_id=k),
                                 self.plugs, now=world.clock.time, start_thread=False, sleep=world.clock.sleep)
        self.m.bench_path = self.bench
        self.m.schema_v2 = lambda: schema
        self.runner = FakeRunner(self.cfg, phase)

    def run(self) -> dict:
        self.m._start(self.runner, 2, "auto")
        job = self.m.onboard
        try:
            return job.run()
        finally:
            self.m.finish_auto(job)

    def doc(self, name="onboard.json"):
        return json.loads((self.tmp / "data" / "set2" / name).read_text(encoding="utf-8"))

    def has(self, name):
        return (self.tmp / "data" / "set2" / name).exists()

    def kinds(self):
        rows = (self.tmp / "data" / "set2" / "events.csv").read_text(encoding="utf-8-sig").splitlines()
        return [r.split(",")[3] for r in rows[1:]]

    def run_on(self) -> bool:
        return json.loads(self.bench.read_text(encoding="utf-8"))["sets"][1].get("run") is True


def test_onboard_event_kinds_are_registered():
    kinds = {"onboard_start", "onboard_calib_done", "onboard_verified", "onboard_run_on", "onboard_joined", "onboard_failed"}
    assert kinds <= ALERT_KINDS and kinds <= set(EVENT_LIGHT)                 # Slack 으로 나가고 색이 있다
    assert EVENT_LIGHT["onboard_failed"] == "red" and all(EVENT_LIGHT[k] == "yellow" for k in kinds - {"onboard_failed"})
    assert EVENT_LIGHT["set_silent"] == "yellow"


# ---------- 맞는 짝이 끝까지 ----------

def test_correct_pair_goes_through_to_run_on_and_waits_for_the_boundary(tmp_path):
    rig = Rig(tmp_path, _world())
    doc = rig.run()
    assert doc["step"] == "joining" and doc["result"] is None, doc                 # 핸드오프 — 아직 끝난 것이 아니다
    assert [s["step"] for s in doc["steps"]] == ["calib", "derive", "verify", "enable"]
    # (b) 보정 기록 · (c) 문턱 도출 · (d) 판정 검증 · (e) 운전 켜기 — 한 번에
    assert rig.doc("pair.json")["state"] == "passed" and rig.doc("pair.json")["mode"] == "verify"
    th = rig.doc("pair_thresholds.json")
    assert th["state"] == "derived" and th["fingerprint"] == V.fingerprint(ENTRY2) and th["evidence"]["resp_floor"] > 0.5
    assert th["calib_file"].startswith("calib_") and (tmp_path / "data" / "set2" / th["calib_file"]).exists()
    assert rig.run_on() and V.load_thresholds(rig.cfg, 2) == th["thresholds"]
    assert rig.kinds().count("onboard_start") == 1 and {"onboard_calib_done", "onboard_verified", "onboard_run_on"} <= set(rig.kinds())
    assert not any(k in rig.kinds() for k in ("onboard_failed", "onboard_joined"))
    assert [re.search(r"\] (\w+)", a).group(1) for a in rig.alerts if "onboard_" in a] == ["onboard_start", "onboard_calib_done", "onboard_verified", "onboard_run_on"]
    assert all("세트 2" in a for a in rig.alerts if "onboard_" in a)
    # 플러그 k 는 켠 채 · 세트 1 은 건드리지 않았다 · 짝 이상 신호 없음
    assert rig.world.plugs[P2] is True and rig.runner.external_fault is None
    assert sw.SET1_MAC not in rig.world.plug_calls
    assert rig.m.onboard is None and "verifying" not in json.loads((tmp_path / "data" / "engine.json").read_text(encoding="utf-8"))
    # 같은 데이터로 운전 관문이 모두 열렸다 — 세트 2 가 다음 경계에서 운전 목록에 든다
    from cellbench.bench import desired_ids
    assert desired_ids(rig.m.reload(), True)[0] == [1, 2]


def test_calibration_gate_is_per_set_and_global_flag_is_unchanged(tmp_path):
    rig = Rig(tmp_path, _world())
    assert PAIR["calibrated"] is False and not V.calibrated_for(rig.cfg, 2)
    rig.run()
    assert V.calibrated_for(rig.cfg, 2) and PAIR["calibrated"] is False       # 전역 상수는 그대로 — 수동 경로가 달라지지 않는다
    assert V.rule_for(rig.cfg, 2)["quick_r_cell"] == rig.doc("pair_thresholds.json")["thresholds"]["quick_r_cell"] and PAIR["quick_r_cell"] == 0.15
    # 재등록(지문이 바뀜)하면 도출한 문턱도 무효다
    other = Config(data_dir=str(tmp_path / "data"), sets=[dict(configmod.DEFAULT_SET), {**ENTRY2, "registered_at": 1760000999}])
    assert not V.calibrated_for(other, 2)


# ---------- 틀린 짝 · 애매함은 멈춘다 ----------

def test_wrong_pair_stops_and_never_turns_run_on(tmp_path):
    """P2 가 세트 1 의 Dock 을 먹인다 — 대조군이 같이 내려가고 올라간다. 도출하지 않고 짝 이상(모든 플러그 켬)으로 올리고 운전을 켜지 않는다."""
    w = sw.World([sw.Dock(P2, sw.SET1, batt={s: 50.0 for s in sw.SET1}),
                  sw.Dock("00:00:00:00:00:00", S2, batt={s: 60.0 for s in S2})], rate_pct_min=0.4, discharge_rate=0.34, limit_h=8.0,
                 plugs={P2: True, "00:00:00:00:00:00": True})
    rig = Rig(tmp_path, w)
    doc = rig.run()
    assert doc["result"] == "failed" and "짝 이상" in doc["why"] and doc["derive"]["code"] == "foreign_follows"
    assert rig.runner.external_fault and "자동 합류" in rig.runner.external_fault      # 엔진이 '모든 플러그 켬'을 한다
    assert not rig.run_on() and not rig.has("pair_thresholds.json") and not V.calibrated_for(rig.cfg, 2)
    assert w.plugs[P2] is True and "onboard_failed" in rig.kinds() and "onboard_verified" not in rig.kinds()
    assert any("onboard_failed" in a and "세트 2" in a for a in rig.alerts)


def test_some_cells_without_response_stops_inconclusive(tmp_path):
    """세트 2 셀 3대가 다른 Dock(아무도 안 먹임)에 꽂혀 있다 — 한 대라도 무반응이면 정하지 않는다."""
    odd = sw.Dock("00:00:00:00:00:00", S2[:3], batt={s: 60.0 for s in S2[:3]})
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1, batt={s: 50.0 for s in sw.SET1}),
                  sw.Dock(P2, S2[3:], batt={s: 60.0 for s in S2[3:]}), odd], rate_pct_min=0.4, discharge_rate=0.34, limit_h=8.0,
                 plugs={P2: True, "00:00:00:00:00:00": True})
    rig = Rig(tmp_path, w)
    doc = rig.run()
    assert doc["result"] == "inconclusive" and doc["derive"]["code"] == "cell_no_response", doc
    assert not rig.has("pair_thresholds.json") and not rig.run_on() and w.plugs[P2] is True
    assert rig.runner.external_fault is None                                   # 짝이 틀렸다는 증거는 아니다 — 사람이 보고 판단


def test_nothing_responds_noise_only_stops_inconclusive(tmp_path):
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1, batt={s: 50.0 for s in sw.SET1}),
                  sw.Dock("00:00:00:00:00:00", S2, batt={s: 60.0 for s in S2})], rate_pct_min=0.4, discharge_rate=0.34, limit_h=8.0,
                 plugs={P2: True, "00:00:00:00:00:00": True})
    rig = Rig(tmp_path, w)
    doc = rig.run()
    # 플러그 P2 가 아무 Dock 도 안 먹이면 켠 뒤 충전 전력(40 W)이 안 보여 보정 기록 단계에서 멈춘다 (응답이 없는 기록으로 도출하지 않는다)
    assert doc["result"] == "inconclusive" and "충전 전력" in doc["why"] and not rig.run_on() and w.plugs[P2] is True and not rig.has("pair_thresholds.json")


def test_control_group_switching_phase_during_calibration_stops(tmp_path):
    """세트 1 이 보정 도중 방전으로 돌아섰다(단계 전환) — 대조군이 흔들린 기록으로는 기준을 정하지 않는다."""
    w = _world()
    flip = sw.T0 + 40 * 60

    def set1_off():
        w.plugs[sw.SET1_MAC] = False                       # 세트 1 의 단계 전환 — 엔진이 세트 1 플러그를 껐다
    w.at(40 * 60, set1_off)
    rig = Rig(tmp_path, w)
    doc = rig.run()
    assert doc["result"] == "inconclusive" and doc["derive"]["code"] in ("control_unstable", "indistinct"), doc
    assert not rig.run_on() and not rig.has("pair_thresholds.json") and w.plugs[P2] is True


def test_busy_set1_transition_defers_then_continues(tmp_path):
    """세트 1 이 단계 전환 중(THRESHOLD)이면 검증 창에 겹치지 않게 미룬다 — 풀리면 그대로 이어 간다."""
    w = _world()
    rig = Rig(tmp_path, w, phase="THRESHOLD")
    w.at(25 * 60, lambda: setattr(rig.runner.current, "phase", "DISCHARGE"))
    t0 = w.clock.time()
    doc = rig.run()
    assert doc["step"] == "joining" and doc["result"] is None
    assert w.clock.time() - t0 > 25 * 60 + 60 * 60 * 0.5                     # 미룬 시간 + 보정 + 검증
    assert rig.doc("pair.json")["state"] == "passed"


def test_busy_forever_aborts_with_alert_after_limit(tmp_path):
    w = _world(limit_h=10.0)
    rig = Rig(tmp_path, w, phase="PLUG_OFF")
    doc = rig.run()
    assert doc["result"] == "inconclusive" and "겹쳐" in doc["why"] and not rig.run_on() and w.plugs[P2] is True
    assert any("onboard_failed" in a for a in rig.alerts)


def test_precheck_refusal_when_cells_not_heard(tmp_path):
    w = _world()
    w.blind_from, w.blind_until = sw.T0 - 10, sw.T0 + 10 ** 6          # 셀이 하나도 안 들린다
    rig = Rig(tmp_path, w)
    doc = rig.run()
    assert doc["result"] == "refused" and "들린다" in doc["why"] and doc["step"] == "calib" and not rig.run_on()
    assert w.plugs[P2] is True and "onboard_failed" in rig.kinds()


def test_manual_abort_stops_between_steps_and_leaves_plug_on(tmp_path):
    w = _world()
    rig = Rig(tmp_path, w)
    rig.m._start(rig.runner, 2, "auto")
    job = rig.m.onboard
    w.at(20 * 60, job.cancelled.set)                                     # 보정 기록 도중 사람이 '검증 멈춤'
    try:
        # 검증기는 자기 cancelled 를 본다 — 관리자의 abort 처리와 같게 둘 다 세운다
        w.at(20 * 60, lambda: job.verifier is not None and job.verifier.cancelled.set())
        doc = job.run()
    finally:
        rig.m.finish_auto(job)
    assert doc["result"] == "aborted" and not rig.run_on() and w.plugs[P2] is True


# ---------- 운전 관문 · 이어 가기 ----------

def test_closed_gate_stops_before_run_on_and_retry_resumes_at_enable(tmp_path):
    """클라우드 판 2(U1)가 닫혀 있으면 운전을 켜지 않는다. 짝 검증 통과는 남고, 관문이 열린 뒤 다시 누르면 운전 켜기부터 이어 간다."""
    rig = Rig(tmp_path, _world(), schema=False)
    doc = rig.run()
    assert doc["result"] == "failed" and doc["step"] == "enable" and "클라우드 스키마 판 2" in doc["why"] and "U1" in doc["why"]
    assert not rig.run_on() and rig.doc("pair.json")["state"] == "passed" and V.pair_ok(rig.cfg, 2)
    started = rig.world.clock.time()
    calls = len(rig.world.plug_calls.get(P2, []))
    rig.m.schema_v2 = lambda: True                                       # 관문이 열렸다
    doc2 = rig.run()
    assert doc2["step"] == "joining" and doc2["result"] is None and rig.run_on()
    assert [s["step"] for s in doc2["steps"]] == ["enable"]               # 처음부터 다시 하지 않았다
    assert rig.world.clock.time() - started < 60 and len(rig.world.plug_calls.get(P2, [])) == calls   # 플러그를 만지지 않았다


def test_old_pass_is_not_reused(tmp_path):
    rig = Rig(tmp_path, _world(), schema=False)
    rig.run()
    old = rig.doc("pair.json")
    old["ended"] = rig.world.clock.time() - 3 * 86400                    # 사흘 전 통과
    write_json_atomic(tmp_path / "data" / "set2" / "pair.json", old)
    rig.m.schema_v2 = lambda: True
    doc = rig.run()
    assert [s["step"] for s in doc["steps"]][:2] == ["calib", "derive"]


# ---------- 합류 기다림 (경계 재구성 · 첫 방전) ----------

def _joining(rig, since=None):
    (rig.tmp / "data" / "set2").mkdir(parents=True, exist_ok=True)
    write_json_atomic(tmp := rig.tmp / "data" / "set2" / "onboard.json",
                      {"set": 2, "step": "joining", "result": None, "why": "", "started": 1.0, "ended": None, "steps": [],
                       "joining_since": since if since is not None else rig.world.clock.time()})
    return tmp


def test_poll_waits_for_boundary_then_reports_join_at_first_discharge(tmp_path):
    rig = Rig(tmp_path, _world())
    rig.run()                                                           # 운전을 켠 상태까지
    # 단일 모드에서 경계를 기다리는 동안 — 진행 중
    OB.poll_joining(rig.m)
    assert OB.is_running(rig.doc()) and rig.doc()["step"] == "joining"
    # 엔진이 다중 모드로 재시작했다 — 운전 목록에 들었고 예비 충전 중
    rig.m.running = {1, 2}
    write_json_atomic(tmp_path / "data" / "set2" / "now.json", {"phase": "PRECHARGE", "cycle": 1})
    OB.poll_joining(rig.m)
    assert OB.is_running(rig.doc()) and "예비 충전" in rig.doc()["why"] and rig.doc().get("in_run_list")
    assert "onboard_joined" not in rig.kinds()
    # 첫 방전 시작 = 합류 완료
    write_json_atomic(tmp_path / "data" / "set2" / "now.json", {"phase": "DISCHARGE", "cycle": 1})
    OB.poll_joining(rig.m)
    d = rig.doc()
    assert d["result"] == "joined" and d["step"] == "joined" and "첫 방전" in d["why"]
    assert rig.kinds().count("onboard_joined") == 1 and any("onboard_joined" in a for a in rig.alerts)
    OB.poll_joining(rig.m)                                               # 끝난 것은 다시 보지 않는다
    assert rig.kinds().count("onboard_joined") == 1


def test_poll_fails_and_reverts_run_when_gate_closes_or_timeout(tmp_path):
    rig = Rig(tmp_path, _world())
    rig.run()
    rig.m.schema_v2 = lambda: False                                      # 기다리는 동안 관문이 다시 닫혔다
    OB.poll_joining(rig.m)
    d = rig.doc()
    assert d["result"] == "failed" and "관문이 닫혔다" in d["why"] and not rig.run_on() and "운전(run)을 껐다" in d["why"]
    # 시간 초과
    rig2 = Rig(tmp_path / "b", _world())
    rig2.run()
    rig2.world.clock.sleep(13 * 3600) if False else None
    d2 = rig2.doc()
    d2["joining_since"] = rig2.world.clock.time() - 13 * 3600
    write_json_atomic(tmp_path / "b" / "data" / "set2" / "onboard.json", d2)
    OB.poll_joining(rig2.m)
    assert rig2.doc()["result"] == "failed" and "재구성되지 않았다" in rig2.doc()["why"] and not rig2.run_on()


def test_poll_aborts_when_human_turns_run_off(tmp_path):
    rig = Rig(tmp_path, _world())
    rig.run()
    configmod.set_run(2, False, rig.bench)
    OB.poll_joining(rig.m)
    assert rig.doc()["result"] == "aborted" and "운전이 꺼졌다" in rig.doc()["why"]


def test_joined_set_that_goes_to_hold_is_a_failure(tmp_path):
    rig = Rig(tmp_path, _world())
    rig.run()
    rig.m.running = {1, 2}
    write_json_atomic(tmp_path / "data" / "set2" / "now.json", {"phase": "HOLD", "cycle": 1})
    OB.poll_joining(rig.m)
    assert rig.doc()["result"] == "failed" and "HOLD" in rig.doc()["why"]


# ---------- 엔진 재시작 ----------

@pytest.mark.parametrize("step", ["precheck", "calib", "derive", "verify", "enable"])
def test_engine_restart_aborts_every_step_except_joining(tmp_path, step):
    rig = Rig(tmp_path, _world())
    (tmp_path / "data" / "set2").mkdir(parents=True)
    write_json_atomic(tmp_path / "data" / "set2" / "onboard.json", {"set": 2, "step": step, "result": None, "started": 1.0, "steps": []})
    write_json_atomic(tmp_path / "data" / "set2" / "pair.json", {"state": "running", "step": "quick"})
    rig.m.recover([1])
    d = rig.doc()
    assert d["result"] == "aborted" and "다시 시작" in d["why"] and rig.doc("pair.json")["state"] == "inconclusive"
    assert "onboard_failed" in rig.kinds() and any("onboard_failed" in a for a in rig.alerts)


def test_engine_restart_keeps_joining_wait(tmp_path):
    rig = Rig(tmp_path, _world())
    _joining(rig)
    (tmp_path / "data" / "set2" / "events.csv").unlink(missing_ok=True)
    rig.m.recover([1, 2])
    assert OB.is_running(rig.doc()) and rig.doc()["step"] == "joining"


# ---------- 관리자 — 요청 · 거절 · 멈춤 ----------

def _request(tmp, **req):
    (tmp / "data" / "set2").mkdir(parents=True, exist_ok=True)
    write_json_atomic(tmp / "data" / "set2" / "pair_request.json", req)


def test_tick_starts_auto_records_verifying_and_blocks_others(tmp_path):
    rig = Rig(tmp_path, _world())
    _request(tmp_path, action="start", mode="auto", by="tester")
    rig.m.tick(rig.runner)
    assert rig.m.onboard is not None and rig.m.onboard.k == 2 and rig.m.busy_ids() == {2}
    eng = json.loads((tmp_path / "data" / "engine.json").read_text(encoding="utf-8"))
    assert eng["verifying"] == [{"id": 2, "mac": P2, "ip": ""}] and len(rig.plugs) == 2
    assert not (tmp_path / "data" / "set2" / "pair_request.json").exists()
    # 도는 동안 다른 요청은 받지 않는다 — 자동 합류의 요청이면 이유가 onboard.json 에 남는다(진행 중 기록은 덮지 않는다)
    _request(tmp_path, action="start", mode="calib")
    rig.m.tick(rig.runner)
    assert rig.m.onboard.k == 2 and rig.m.active is None
    _request(tmp_path, action="abort")
    rig.m.tick(rig.runner)
    assert rig.m.onboard.cancelled.is_set()
    rig.m.finish_auto(rig.m.onboard)
    assert rig.m.onboard is None and "verifying" not in json.loads((tmp_path / "data" / "engine.json").read_text(encoding="utf-8"))


def test_tick_refuses_auto_for_running_set_and_writes_reason(tmp_path):
    rig = Rig(tmp_path, _world())
    rig.m.running = {1, 2}
    _request(tmp_path, action="start", mode="auto")
    rig.m.tick(rig.runner)
    assert rig.m.onboard is None and rig.doc()["result"] == "refused" and "운전 목록" in rig.doc()["why"]


def test_tick_second_auto_while_first_runs_does_not_overwrite_progress(tmp_path):
    rig = Rig(tmp_path, _world())
    _request(tmp_path, action="start", mode="auto")
    rig.m.tick(rig.runner)
    write_json_atomic(tmp_path / "data" / "set2" / "onboard.json", {"set": 2, "step": "calib", "result": None, "started": 5.0, "steps": []})
    _request(tmp_path, action="start", mode="auto")
    rig.m.tick(rig.runner)
    assert rig.doc()["step"] == "calib" and rig.doc()["result"] is None


def test_manual_verify_after_auto_uses_derived_thresholds(tmp_path):
    """자동 합류가 문턱을 도출한 세트는 수동 '짝 검증(판정)'도 열린다 — 전역 보정(PAIR)은 그대로 False."""
    rig = Rig(tmp_path, _world(), schema=False)
    rig.run()
    assert PAIR["calibrated"] is False
    rig.world.plugs[P2] = True
    v = rig.m.new_verifier(rig.runner, 2, "verify", Recorder(tmp_path / "data" / "set2", set_id=2), sw.FakePlug(rig.world, configmod.set_cfg(rig.cfg, 2)),
                           sw.FakeLink(rig.world, configmod.set_cfg(rig.cfg, 2), None))
    assert v.rule["calibrated"] is True and v.rule["quick_r_cell"] == rig.doc("pair_thresholds.json")["thresholds"]["quick_r_cell"]
    doc = v.run()
    assert doc["state"] == "passed", doc["why"]


# ---------- 진짜 엔진(run_cycle.main · 다중 모드)에서 합류 확인 ----------

def test_real_engine_reports_joined_when_set_enters_run_list_and_discharges(tmp_path, monkeypatch):
    """자동 합류가 운전을 켜고 경계에서 엔진이 다시 떴다 — 관문(보정 · 짝 · 클라우드 판)을 실제 함수로 지나 다중 모드가 되고,
    세트 2 의 첫 방전에서 onboard.json 이 joined 로 닫히며 Slack(onboard_joined)이 간다. 세트 1 은 그대로 돈다."""
    import run_cycle
    from cellbench import cloud as cloudmod
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1), sw.Dock(P2, S2, batt={s: 70.0 for s in S2})], limit_h=10)
    w.clock.lockstep = True
    monkeypatch.setattr(cloudmod.Cloud, "v2", lambda self: True)                       # 클라우드 판 2 확인됨(U1 적용 뒤)
    entry = {**ENTRY2, "run": True, "target_cycles": 1}
    bench = {"bench_id": "hq-bench-1", "sets": [{**configmod.DEFAULT_SET, "target_cycles": 1}, entry]}
    d2 = tmp_path / "data" / "set2"
    d2.mkdir(parents=True)
    fp = V.fingerprint(entry)
    write_json_atomic(d2 / "pair.json", {"state": "passed", "mode": "verify", "fingerprint": fp, "ended": sw.T0 - 600})
    write_json_atomic(d2 / "pair_thresholds.json", {"state": "derived", "fingerprint": fp,
                                                    "thresholds": {"quick_r_cell": 0.38, "quick_r_median": 0.38}})
    write_json_atomic(d2 / "onboard.json", {"set": 2, "step": "joining", "result": None, "started": sw.T0 - 900, "steps": [],
                                           "joining_since": sw.T0 - 600})
    code = sw.run_main(monkeypatch, tmp_path, w, ["--precharge", "--cycles", "1"], bench=bench)
    eng = json.loads((tmp_path / "data" / "engine.json").read_text(encoding="utf-8"))
    assert code == 0 and eng.get("mode") == "multi" and [s["id"] for s in eng["sets"]] == [1, 2]
    d = json.loads((d2 / "onboard.json").read_text(encoding="utf-8"))
    assert d["result"] == "joined" and d["step"] == "joined" and "첫 방전" in d["why"], d
    assert "onboard_joined" in (d2 / "events.csv").read_text(encoding="utf-8-sig")
    assert any("onboard_joined" in a and "세트 2" in a for a in w.alerts)
    assert "pair_mismatch" not in (tmp_path / "data" / "events.csv").read_text(encoding="utf-8-sig")


def test_real_engine_start_aborts_unfinished_onboarding_and_turns_plug_on(tmp_path, monkeypatch):
    """엔진이 보정 도중 죽었다 — 새 엔진은 그 단계를 이어 가지 않고 aborted(플러그 k 켬 · 알림)로 끝내고 세트 1 은 그대로 돈다."""
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1), sw.Dock(P2, S2, batt={s: 70.0 for s in S2})], plugs={P2: False}, limit_h=10)
    d2 = tmp_path / "data" / "set2"
    d2.mkdir(parents=True)
    write_json_atomic(d2 / "onboard.json", {"set": 2, "step": "calib", "result": None, "started": sw.T0 - 900, "steps": []})
    write_json_atomic(tmp_path / "data" / "engine.json", {"pid": 1, "exit": None, "verifying": [{"id": 2, "mac": P2, "ip": ""}]})
    bench = {"bench_id": "hq-bench-1", "sets": [dict(configmod.DEFAULT_SET), dict(ENTRY2)]}
    code = sw.run_main(monkeypatch, tmp_path, w, ["--precharge", "--cycles", "1"], bench=bench)
    d = json.loads((d2 / "onboard.json").read_text(encoding="utf-8"))
    assert code == 0 and d["result"] == "aborted" and w.plugs[P2] is True
    assert any("onboard_failed" in a and "세트 2" in a for a in w.alerts)


# ---------- 안전 검토 지적(M1 · M3 · Minor 3 · Minor 4) 회귀 ----------

def _fail_onboard_writes(monkeypatch, only_step=None):
    """onboard.json 쓰기만 실패시킨다(write_json_atomic 이 False) — only_step 이 있으면 그 step 을 쓸 때만."""
    real = OB.write_json_atomic

    def fake(path, payload, *a, **kw):
        if Path(path).name == "onboard.json" and (only_step is None or payload.get("step") == only_step):
            return False
        return real(path, payload, *a, **kw)
    monkeypatch.setattr(OB, "write_json_atomic", fake)


def test_m1_joining_record_failure_never_turns_run_on(tmp_path, monkeypatch):
    """운전 켜기 전에 onboard.json 에 joining 을 먼저 쓰고, 그것이 안 써지면 run:true 를 켜지 않고 실패로 닫는다."""
    rig = Rig(tmp_path, _world())
    _fail_onboard_writes(monkeypatch, only_step="joining")
    doc = rig.run()
    assert doc["result"] == "failed" and "운전을 켜지 않았다" in doc["why"] and not rig.run_on()


def test_m1_order_joining_is_recorded_before_run_true(tmp_path, monkeypatch):
    rig = Rig(tmp_path, _world(), schema=True)
    seen = {}
    real = OB.set_run

    def spy(k, on, bp):
        seen["step_at_run"] = json.loads((tmp_path / "data" / "set2" / "onboard.json").read_text(encoding="utf-8")).get("step")
        return real(k, on, bp)
    monkeypatch.setattr(OB, "set_run", spy)
    rig.run()
    assert seen["step_at_run"] == "joining"


def test_m1_write_is_retried_before_giving_up(tmp_path, monkeypatch):
    rig = Rig(tmp_path, _world())
    real, n = OB.write_json_atomic, {"fail": 0}

    def flaky(path, payload, *a, **kw):
        if Path(path).name == "onboard.json" and payload.get("step") == "joining" and n["fail"] < 2:
            n["fail"] += 1
            return False
        return real(path, payload, *a, **kw)
    monkeypatch.setattr(OB, "write_json_atomic", flaky)
    doc = rig.run()
    assert n["fail"] == 2 and doc["step"] == "joining" and rig.run_on()


def test_m1_recover_turns_run_off_for_non_joining_steps(tmp_path):
    rig = Rig(tmp_path, _world())
    configmod.set_run(2, True, rig.bench)
    (tmp_path / "data" / "set2").mkdir(parents=True)
    write_json_atomic(tmp_path / "data" / "set2" / "onboard.json", {"set": 2, "step": "enable", "result": None, "started": 1.0, "steps": []})
    rig.m.recover([1])
    assert rig.doc()["result"] == "aborted" and not rig.run_on() and "운전(run)을 껐다" in rig.doc()["why"]


def test_m1_failed_finish_turns_run_off_and_failed_record_write_is_still_announced(tmp_path, monkeypatch):
    rig = Rig(tmp_path, _world())
    configmod.set_run(2, True, rig.bench)
    _joining(rig)
    doc = rig.doc()
    _fail_onboard_writes(monkeypatch)
    OB.finish_doc(rig.m, 2, doc, "failed", "시험")
    assert not rig.run_on() and "onboard_failed" in rig.kinds()          # 기록은 못 썼어도 run 은 꺼졌고 알렸다


def test_m1_joined_with_unwritable_record_stays_joining_for_retry(tmp_path, monkeypatch):
    rig = Rig(tmp_path, _world())
    _joining(rig)
    doc = rig.doc()
    _fail_onboard_writes(monkeypatch)
    out = OB.finish_doc(rig.m, 2, doc, "joined", "시험")
    assert out["result"] is None and out["step"] == "joining" and not rig.has("events.csv")


def test_m3_set1_effective_plug_mac_overlap_stops_before_calibration(tmp_path):
    """--config 로 세트 1 의 플러그 MAC 을 세트 2 의 것으로 덮었으면 보정(플러그 끄기)을 시작하지 않는다."""
    from dataclasses import replace
    rig = Rig(tmp_path, _world())
    rig.m.cfg = replace(rig.cfg, plug_mac=P2)
    doc = rig.run()
    assert doc["result"] == "failed" and doc["step"] == "precheck" and "플러그 MAC" in doc["why"] and "보정을 시작하지 않았다" in doc["why"]
    assert "off" not in rig.world.plug_calls.get(P2, []) and not rig.has("pair.json") and not rig.run_on()


def test_m3_set1_effective_serial_overlap_stops_before_calibration(tmp_path):
    from dataclasses import replace
    rig = Rig(tmp_path, _world())
    rig.m.cfg = replace(rig.cfg, serials=[11594, 11595])
    doc = rig.run()
    assert doc["result"] == "failed" and "겹친다" in doc["why"] and "off" not in rig.world.plug_calls.get(P2, [])


def test_minor3_load_thresholds_rejects_nonfinite_and_loose_values(tmp_path):
    rig = Rig(tmp_path, _world())
    rig.run()
    path = tmp_path / "data" / "set2" / "pair_thresholds.json"
    good = json.loads(path.read_text(encoding="utf-8"))
    assert V.load_thresholds(rig.cfg, 2) == good["thresholds"]
    for key, bad in (("quick_r_cell", float("nan")), ("quick_other_max", float("inf")), ("quick_cells_min", 5),
                     ("quick_other_max", 0.5), ("quick_r_cell", 0.01), ("quick_other_max", 0.0)):
        doc = json.loads(json.dumps(good))
        doc["thresholds"][key] = bad
        path.write_text(json.dumps(doc), encoding="utf-8")                 # json.dumps 는 NaN · Infinity 도 쓴다
        assert V.load_thresholds(rig.cfg, 2) is None, (key, bad)


def test_minor4_abort_during_joining_turns_run_off_and_closes(tmp_path):
    rig = Rig(tmp_path, _world())
    rig.run()
    assert rig.run_on() and rig.doc()["step"] == "joining"
    _request(tmp_path, action="abort")
    rig.m.tick(rig.runner)
    d = rig.doc()
    assert d["result"] == "aborted" and "멈췄다" in d["why"] and not rig.run_on()
    assert not (tmp_path / "data" / "set2" / "pair_request.json").exists()


def test_minor4_abort_without_anything_running_is_still_harmless(tmp_path):
    rig = Rig(tmp_path, _world())
    _request(tmp_path, action="abort")
    rig.m.tick(rig.runner)
    assert not (tmp_path / "data" / "set2" / "onboard.json").exists()
