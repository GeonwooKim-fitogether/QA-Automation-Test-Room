"""PR #8(시험대가 놀지 않게)의 적대 안전 검토가 찾은 결함 F1 ~ F10 의 재현·고침 검사 (F6 은 코드 밖 — 라이브 engine.json 의 args.config 확인).

  F1  셀이 다 들리지 않으면 예비 충전 판정을 보류한다(None) — 예전엔 dock_no_recharge 로 흘러 플러그를 끊었다 켰다
  F2  PRECHARGE 체류 기대가 시작 잔량에 비례한다 (기본 1h + (100 − 시작 최저%)/20 시간, 빨강은 4배)
  F3  놀지 않기 설정(target_cycles · progress_notify_every · precharge_skip_pct · floor_w_max · auto_continue)의 형식을 validate 가 잡고,
      깨진 설정에서 엔진 run() 과 감시자 restart_plan 이 예외 대신 안전한 길로 간다
  F4  충전 한도 초과가 charge_timeout_max(3)사이클 연속이면 failsafe 로 멈춘다 (감시자는 되살리지 않는다)
  F5  감시자가 띄운 엔진의 프로세스가 점검 시각보다 늦게 태어나도(≤ 120초) 같은 엔진으로 보고 기다린다 — 겹쳐 띄우지 않는다
  F7  CHARGE 체류 기대는 charge_timeout_h 를 따른다
  F8  체류 경고의 사람 조치 문구가 단계마다 다르다
  F9  바닥 평탄 판정은 창 안 유효 표본이 기대치의 절반 이상일 때만
  F10 마지막 진척 단계를 events.csv 의 progress 행에서 복원한다
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sim_world as sw
import test_health as th
import test_no_idle as ni
import test_supervisor as ts
from cellbench.config import HEALTH, Config, validate
from cellbench.cycle import CycleState, cells_at_least, floor_flat, precharge_verdict
from cellbench.record import ALERT_KINDS, EVENT_LIGHT, last_progress
from cellbench.supervisor import SPAWN_PID_SLACK_S, pid_matches, remaining_cycles, restart_plan, tick


# ---------- F1 ----------

def test_f1_cells_at_least_is_none_when_cells_are_missing():
    assert cells_at_least([96, 97], 95, 3) is None                                   # 한 대가 안 들린다 — 판단 보류
    assert cells_at_least([], 95, 24) is None                                        # 하나도 안 들린다
    assert cells_at_least([96, 97, 95], 95, 3) is True
    assert cells_at_least([96, 97, 94], 95, 3) is False                              # 다 들렸고 그중 95% 미만이 있다


def test_f1_precharge_verdict_holds_instead_of_dock_no_recharge_while_cells_missing():
    cfg = Config(); t0 = 1000.0; now = t0 + 12 * 60
    assert precharge_verdict(ni._flat(t0, 12, 27.7), [96] * 23, now, cfg) is None       # 24대 중 23대 — 보류
    assert precharge_verdict(ni._flat(t0, 12, 27.7), [], now, cfg) is None              # 0대 — 보류
    assert precharge_verdict(ni._flat(t0, 12, 27.7), [96] * 23 + [94], now, cfg) == "dock_no_recharge"
    assert precharge_verdict(ni._flat(t0, 12, 27.7), [96] * 24, now, cfg) == "floor_full"


def test_f1_precharge_does_not_kick_the_plug_while_a_cell_is_missing(tmp_path):
    """바닥 평탄인데 24대 중 23대만 들린다 — dock_no_recharge 를 남기지 않고 끊었다 켜지도 않는다. 충전 한도(charge_timeout_h)가 끝낸다."""
    r, cfg = ni._runner(tmp_path, 90, watts=27.7, charge_timeout_h=0.0002)           # 0.72초 — 바닥 평탄 창(0.12초)이 여러 번 찬다
    del r.live.cells[cfg.serials[0]]
    r.precharge()
    ev = (tmp_path / "events.csv").read_text(encoding="utf-8-sig")
    assert "dock_no_recharge" not in ev and "charge_timeout" in ev
    assert r.plug.calls.count("recharge") == 1                                       # 시작 때 한 번뿐
    assert "최저 셀" not in ev                                                        # 그 문구는 95% 미만이 있을 때만


def test_f1_precharge_is_not_skipped_when_a_cell_is_missing(tmp_path):
    r, cfg = ni._runner(tmp_path, 100, watts=27.7, charge_timeout_h=0.0002)
    del r.live.cells[cfg.serials[0]]
    r.precharge()
    assert r.plug.calls and r.plug.calls[0] == "recharge"                            # 23대가 100% 라도 건너뛰지 않는다
    assert "건너뜀" not in (tmp_path / "run.log").read_text(encoding="utf-8")


# ---------- F2 ----------

S = 1 / 3600                                                                          # 한 초 (경계 검사의 여유)


@pytest.mark.parametrize("start,hours,want", [
    (82, 2.85 - S, "green"), (82, 2.85 + S, "yellow"), (82, 7.6 - S, "yellow"), (82, 7.6 + S, "red"),     # 기대 1 + 18/20 = 1.9h
    (20, 7.5 - S, "green"), (20, 7.5 + S, "yellow"), (20, 20.0 - S, "yellow"), (20, 20.0 + S, "red"),     # 기대 1 + 80/20 = 5h
    (100, 1.5 - S, "green"), (100, 1.5 + S, "yellow"), (100, 4.0 - S, "yellow"), (100, 4.0 + S, "red"),   # max(1, 1 + 0) = 1h
    (None, 1.5 - S, "green"), (None, 1.5 + S, "yellow"), (None, 4.0 - S, "yellow"), (None, 4.0 + S, "red"),   # 시작 잔량을 모르면 1h
])
def test_f2_precharge_dwell_expectation_scales_with_start_pct(start, hours, want):
    assert HEALTH["dwell_alarm_factor_by_phase"] == {"PRECHARGE": 4.0} and HEALTH["precharge_pct_per_h"] == 20.0
    h = th.run(now=th.now_(phase="PRECHARGE", since=hours * 3600, metrics=th.metrics(precharge_start_min_pct=start)))
    assert th.L(h, "program") == want
    if want != "green":
        p = th.lane(h, "program")
        exp = max(1.0, 1.0 + (100 - start) / 20) if start is not None else 1.0
        assert f"기대({exp:g}시간)의 {'4' if want == 'red' else '1.5'}배" in p["reason"]


def test_f2_other_phases_keep_the_three_times_alarm():
    assert th.L(th.run(now=th.now_(phase="EXTRACT", since=(1.5 + S) * 3600)), "program") == "red"
    assert th.L(th.run(now=th.now_(phase="RECOVER", since=(1.5 - S) * 3600)), "program") == "yellow"


def test_f2_engine_publishes_precharge_start_min_pct_only_during_precharge(tmp_path):
    r, cfg = ni._runner(tmp_path, 90, watts=27.7)
    assert r._metrics(CycleState(cycle=1))["precharge_start_min_pct"] is None
    r.precharge()
    now = json.loads((tmp_path / "now.json").read_text(encoding="utf-8"))
    assert now["phase"] == "PRECHARGE" and now["metrics"]["precharge_start_min_pct"] == 90    # 예비 충전 중 마지막 now.json
    assert r._metrics(CycleState(cycle=1))["precharge_start_min_pct"] is None            # 끝나면 비운다


# ---------- F3 ----------

def test_f3_validate_catches_bad_no_idle_settings():
    assert validate(Config()) == []
    assert validate(Config(progress_notify_every=0, precharge_skip_pct=0, floor_w_max=0.1, auto_continue=False, charge_timeout_max=1)) == []
    bad = Config(target_cycles=0, progress_notify_every=-1, precharge_skip_pct=101, floor_w_max=0, auto_continue="yes", charge_timeout_max=0)
    p = validate(bad)
    for key in ("target_cycles", "progress_notify_every", "precharge_skip_pct", "floor_w_max", "auto_continue", "charge_timeout_max"):
        assert any(x.startswith(key) for x in p), (key, p)
    assert any("target_cycles" in x for x in validate(Config(target_cycles="500")))     # JSON 문자열
    assert any("target_cycles" in x for x in validate(Config(target_cycles=True)))      # bool 은 정수가 아니다
    assert any("target_cycles" in x for x in validate(Config(target_cycles=2.5)))
    assert any("precharge_skip_pct" in x for x in validate(Config(precharge_skip_pct="95")))
    assert any("floor_w_max" in x for x in validate(Config(floor_w_max=float("nan"))))


def test_f3_run_does_not_raise_when_progress_notify_every_is_garbage(tmp_path):
    r, _ = ni._loop_runner(tmp_path, target_cycles=2, progress_notify_every="많이")
    assert r.run(1) == "done"                                                        # 예외 대신 진척 알림만 끈다
    assert ",progress," not in (tmp_path / "events.csv").read_text(encoding="utf-8-sig")
    assert "progress_notify_every 가 정수가 아니다" in (tmp_path / "run.log").read_text(encoding="utf-8")


def test_f3_restart_plan_with_broken_config_calls_human_instead_of_raising():
    cfg = Config(target_cycles="abc", auto_continue="yes")
    problems = validate(cfg)
    assert problems
    for verdict, engine in (("finished", ts.eng(exit="done", plug_on=True)), ("dead", ts.eng(target_last_cycle=4))):
        p = restart_plan(verdict, engine, ts.now_json(4000, "DONE"), 4, [], ts.T, cfg, problems)
        assert p.plug_on and p.call_human and p.restart == 0 and "설정에 문제" in p.why, (verdict, p)
    assert isinstance(remaining_cycles(None, 4, 4, cfg), int)                         # 예외 없이 센다
    # 목표가 정수인데 다른 칸이 깨진 경우 — 목표를 다 돈 엔진은 그대로 '끝남'
    cfg = Config(target_cycles=4, floor_w_max=-1)
    p = restart_plan("finished", ts.eng(exit="done", plug_on=True), ts.now_json(4000, "DONE"), 4, [], ts.T, cfg, validate(cfg))
    assert p.restart == 0 and not p.call_human and "다 돌고 끝남" in p.why


def test_f3_tick_with_broken_config_keeps_plug_on_and_calls_human(tmp_path):
    ts.put(tmp_path, ts.eng(exit="done", plug_on=True), ts.now_json(4000, "DONE"), cycles=4)
    cfg = Config(data_dir=str(tmp_path), target_cycles="abc")
    w = ts.World()
    rep, _ = tick(cfg, tmp_path, None, w.deps(), ts.T)                               # 예외 없이
    assert "engine:" not in " ".join(w.acts()) and rep["config_problems"]
    assert any("설정" in a for a in w.alerts)


# ---------- F4 ----------

def test_f4_event_kind_is_red_and_alerts():
    assert EVENT_LIGHT["charge_timeout_streak"] == "red" and "charge_timeout_streak" in ALERT_KINDS
    assert Config().charge_timeout_max == 3


def test_f4_three_consecutive_charge_timeouts_end_in_failsafe(tmp_path):
    r, cfg = ni._loop_runner(tmp_path, target_cycles=50, charge_timeout_max=3)
    one = r.run_cycle

    def timeout_cycle():
        one(); r._charge_timeouts += 1                                               # 정규 충전이 한도까지 만충되지 못했다
    r.run_cycle = timeout_cycle
    assert r.run(1) == "failsafe" and r.plug_on_at_exit is True
    rows = (tmp_path / "cycles.csv").read_text(encoding="utf-8-sig").splitlines()
    ev = (tmp_path / "events.csv").read_text(encoding="utf-8-sig")
    assert len(rows) == 4 and ev.count("charge_timeout_streak") == 1 and "Dock 이 3사이클 연속 만충 실패" in ev and "사람 확인" in ev


def test_f4_a_successful_charge_resets_the_streak(tmp_path):
    r, cfg = ni._loop_runner(tmp_path, target_cycles=5, charge_timeout_max=3)
    one = r.run_cycle
    n = {"i": 0}

    def cycle():
        one(); n["i"] += 1
        r._charge_timeouts = 0 if n["i"] == 3 else r._charge_timeouts + 1           # 2번 실패 · 만충 · 2번 실패 — 연속 3이 아니다
    r.run_cycle = cycle
    assert r.run(1) == "done" and "charge_timeout_streak" not in (tmp_path / "events.csv").read_text(encoding="utf-8-sig")


class _NeverFull(sw.World):
    """Dock 이 셀을 cap 까지만 채우는 세상 — 정규 충전의 만충(모든 셀 100%)이 영영 오지 않아 사이클마다 charge_timeout 이 난다."""

    def __init__(self, cap: float, **kw):
        super().__init__([sw.Dock(sw.SET1_MAC, sw.SET1, batt={s: cap for s in sw.SET1})], **kw)
        self.cap = cap

    def integrate(self, dt):
        super().integrate(dt)
        for d in self.docks:
            for s in d.batt:
                d.batt[s] = min(self.cap, d.batt[s])

    def watts(self, mac):
        if not self.plugs.get(mac):
            return 0.0
        return 68.0 if any(b < self.cap for d in self.docks for b in d.batt.values()) else 27.7


def test_f4_world_dock_that_never_reaches_full_stops_after_three_cycles(tmp_path, monkeypatch):
    w = _NeverFull(96.0, limit_h=40)
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"], bench=ni._bench(target_cycles=50, charge_timeout_h=0.5))
    eng = json.loads((tmp_path / "data" / "engine.json").read_text(encoding="utf-8"))
    rows = (tmp_path / "data" / "cycles.csv").read_text(encoding="utf-8-sig").splitlines()
    ev = (tmp_path / "data" / "events.csv").read_text(encoding="utf-8-sig")
    assert code == 0 and eng["exit"] == "failsafe" and eng["plug_on"] is True and w.plugs[sw.SET1_MAC] is True
    assert len(rows) == 4 and ev.count(",charge_timeout,") == 3 and ev.count("charge_timeout_streak") == 1
    assert any("charge_timeout_streak" in a for a in w.alerts)
    # 감시자는 failsafe 를 되살리지 않고 사람을 부른다(기존 규칙)
    p = restart_plan("paused", eng, None, 3, [], ts.T, Config(target_cycles=50))
    assert p.restart == 0 and p.call_human


# ---------- F5 ----------

def test_f5_pid_matches_allows_spawn_slack_only_when_asked():
    assert pid_matches(ts.T + 70, ts.T, ts.T - 86400) is False                       # 엔진이 스스로 적은 started 에는 5초
    assert pid_matches(ts.T + 70, ts.T, ts.T - 86400, SPAWN_PID_SLACK_S) is True
    assert pid_matches(ts.T + SPAWN_PID_SLACK_S + 1, ts.T, ts.T - 86400, SPAWN_PID_SLACK_S) is False


def test_f5_spawned_engine_born_later_than_the_tick_is_still_waited_for(tmp_path):
    """플러그 ON 재시도 등으로 점검이 길어져 프로세스가 점검 시각 + 70초에 태어났다 — 다음 점검은 '기다린다' 이고 두 번째 엔진을 띄우지 않는다."""
    ts.put(tmp_path, ts.eng(exit=None), ts.now_json(4000), cycles=2)                 # 심박 4000초 전 · pid 4242 없음 → dead
    cfg = ts.cfg_for(tmp_path)
    w = ts.World()
    rep, _ = tick(cfg, tmp_path, None, w.deps(), ts.T)
    assert w.acts() == ["plug_on", "engine:2"]
    sp = rep["memo"]["spawned"]
    assert sp["pid"] == 9001 and ts.T <= sp["t"] < ts.T + 5                            # 띄운 시각은 스폰 직후(점검 시작 이후)
    w.alive[9001] = sp["t"] + 70                                                     # OS 가 잰 생성 시각이 70초 늦다
    rep, _ = tick(cfg, tmp_path, rep, w.deps(), ts.T + 100)
    assert w.acts() == ["plug_on", "engine:2"] and "시작하는 중" in rep["why"]        # 겹쳐 띄우지 않았다


# ---------- F7 ----------

def test_f7_charge_dwell_follows_charge_timeout_h():
    assert HEALTH["dwell_expect_h"]["CHARGE"] is None
    cfg = Config(charge_timeout_h=2.0)
    assert th.L(th.run(now=th.now_(phase="CHARGE", since=(3.0 - S) * 3600), cfg=cfg), "program") == "green"
    assert th.L(th.run(now=th.now_(phase="CHARGE", since=(3.0 + S) * 3600), cfg=cfg), "program") == "yellow"
    assert th.L(th.run(now=th.now_(phase="CHARGE", since=(6.0 + S) * 3600), cfg=cfg), "program") == "red"
    assert th.L(th.run(now=th.now_(phase="CHARGE", since=(6.0 + S) * 3600)), "program") == "yellow"   # 기본 4h → 빨강은 12h


# ---------- F8 ----------

@pytest.mark.parametrize("phase,want,not_want", [
    ("RECOVER", "셀 전원 · Wi-Fi 확인", "플러그"),
    ("PRECHARGE", "플러그 끄기", "플러그 켜기"),
    ("CHARGE", "플러그 끄기", "플러그 켜기"),
    ("DISCHARGE", "플러그 켜기", "플러그 끄기"),
    ("EXTRACT", "run.log", "플러그"),
])
def test_f8_dwell_human_action_is_phase_specific(phase, want, not_want):
    human = th.lane(th.run(now=th.now_(phase=phase, since=30 * 3600)), "program")["issues"][0]["human"]
    assert want in human and not_want not in human


# ---------- F9 ----------

def test_f9_floor_flat_needs_at_least_half_the_expected_samples():
    t0 = 1000.0; now = t0 + 12 * 60
    full = ni._flat(t0, 12, 27.7)                                                     # 20초 간격 — 10분 창에 31개 (기대 30)
    assert floor_flat(full, now, 10, 35.0, 20.0) is True
    assert floor_flat(full[::2], now, 10, 35.0, 20.0) is True                        # 16개 ≥ 15 (절반)
    assert floor_flat(full[::4], now, 10, 35.0, 20.0) is False                       # 8개 — 플러그 읽기가 대부분 빠졌다
    nan = [(t, float("nan")) for t, _ in full[:-4]] + full[-4:]                      # 마지막 넷만 읽혔다 — 예전엔 3개 이상이라 True
    assert floor_flat(nan, now, 10, 35.0, 20.0) is False
    assert floor_flat(full, now, 10, 35.0, 0.0) is True                              # poll_s 를 모르면 옛 규칙(3개)


# ---------- F10 ----------

def test_f10_last_progress_reads_the_largest_progress_row(tmp_path):
    assert last_progress(tmp_path / "none.csv") == 0
    r, _ = ni._loop_runner(tmp_path)
    assert r.rec.last_progress() == 0
    r.rec.event(100, "PLUG_OFF", "progress", "-", "목표 500 중 100사이클 완료 · 누적 방전 평균 3.0h")
    r.rec.event(100, "PLUG_OFF", "crash", "-", "중 999사이클 완료 가 아닌 다른 글")
    r.rec.event(200, "PLUG_OFF", "progress", "-", "목표 500 중 200사이클 완료 · 누적 방전 평균 3.1h")
    assert r.rec.last_progress() == 200 and last_progress(r.rec.dir / "events.csv") == 200


def test_f10_progress_already_sent_is_not_repeated_on_restart(tmp_path):
    r, _ = ni._loop_runner(tmp_path, target_cycles=3, progress_notify_every=2)
    for i in (1, 2):
        r.rec.cycle({"cycle": i, "discharge_h": "3.0"})
    r.rec.event(2, "PLUG_OFF", "progress", "-", "목표 3 중 2사이클 완료 · 누적 방전 평균 3.0h")   # 되살리기 전 엔진이 이미 알렸다
    r.run(1)
    assert (tmp_path / "events.csv").read_text(encoding="utf-8-sig").count(",progress,") == 1


def test_f10_progress_missed_by_a_crash_is_sent_once_on_restart(tmp_path):
    """사이클 2 를 기록한 직후 알리기 전에 엔진이 죽었다 — 되살아난 엔진이 그 알림을 한 번 보낸다(예전엔 누적으로 어림해 영영 빠졌다)."""
    r, _ = ni._loop_runner(tmp_path, target_cycles=3, progress_notify_every=2)
    for i in (1, 2):
        r.rec.cycle({"cycle": i, "discharge_h": "3.0"})
    got = []
    r.rec.alert = got.append
    r.run(1)
    ev = (tmp_path / "events.csv").read_text(encoding="utf-8-sig")
    assert ev.count(",progress,") == 1 and "목표 3 중 2사이클 완료" in ev and sum(1 for a in got if "progress" in a) == 1
