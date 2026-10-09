"""감시자 — 순수 판정과, 가짜 바깥 세상으로 돌린 한 번의 점검(tick).

가장 중요한 약속: 엔진을 다시 띄우기 전에 플러그를 먼저 켠다. 사람이 일부러 멈춘 것은 되살리지 않는다.
같은 사건에서 플러그를 두 번 켜지 않는다(켤 때마다 10초 끊었다 켠다). 장비·실제 프로세스에는 닿지 않는다
(마지막 proc 검사만 이 PC 에서 무해한 python 자식 하나를 띄웠다 끝낸다).
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench import proc
from cellbench import supervisor as sup
from cellbench.config import Config
from cellbench.supervisor import (Deps, Plan, engine_argv, engine_verdict, heartbeat, pause_state, pid_matches,
                                  remaining_cycles, restart_plan, tick)

T = 1_800_000_000.0                       # 시험 기준 시각
CFG = Config()                            # 주의 60 · 멈춤 300 · 추출 1200 · 시간당 3번


def eng(**kw):
    e = {"bench_id": "hq-bench-1", "pid": 4242, "started": T - 3600, "exit": None,
         "args": {"cycles": 4, "precharge": True, "dry_run": False, "config": None}, "target_last_cycle": 4}
    e.update(kw)
    return e


def now_json(age, phase="DISCHARGE", key="beat"):
    return {"t": T - age, key: T - age, "phase": phase} if key == "beat" else {"t": T - age, "phase": phase}


# ---------- 엔진 판정 ----------

def test_verdict_absent_and_intended_exits():
    assert engine_verdict(None, None, False, T, CFG) == "absent"
    assert engine_verdict(eng(exit="done"), None, False, T, CFG) == "finished"
    assert engine_verdict(eng(exit="stopped"), None, False, T, CFG) == "stopped"
    assert engine_verdict(eng(exit="interrupted"), None, False, T, CFG) == "stopped"
    assert engine_verdict(eng(exit="failsafe"), None, False, T, CFG) == "paused"
    assert engine_verdict(eng(exit="config_error"), None, False, T, CFG) == "config_error"


def test_verdict_abnormal_exits_are_dead():
    assert engine_verdict(eng(), now_json(5), False, T, CFG) == "dead"          # 이유 없이 사라짐
    assert engine_verdict(eng(exit="no_cells"), None, False, T, CFG) == "dead"
    assert engine_verdict(eng(exit="뭔가"), None, True, T, CFG) == "dead"        # 모르는 이유도 비정상으로


@pytest.mark.parametrize("phase,age,want", [
    ("DISCHARGE", 30, "ok"), ("DISCHARGE", 299, "ok"), ("DISCHARGE", 301, "hung"),
    ("CHARGE", 600, "hung"), ("RECOVER", 200, "ok"),                               # 복구 중 플러그·Wi-Fi 재시도로 2~3분 비는 것은 정상
    ("EXTRACT", 600, "ok"), ("EXTRACT", 1199, "ok"), ("EXTRACT", 1201, "hung"),    # 추출 중에는 길게 본다
])
def test_verdict_heartbeat_threshold_by_phase(phase, age, want):
    assert engine_verdict(eng(), now_json(age, phase), True, T, CFG) == want


def test_heartbeat_levels_for_signal_light():
    assert heartbeat(eng(), now_json(30), T, CFG)[2] == "ok"
    assert heartbeat(eng(), now_json(61), T, CFG)[2] == "warn"              # 노란불 — 감시자는 기록만
    assert heartbeat(eng(), now_json(301), T, CFG)[2] == "stale"
    assert heartbeat(eng(), now_json(200, "EXTRACT"), T, CFG)[2] == "ok"    # 추출 중 셀 복귀 대기는 정상
    assert heartbeat(eng(), now_json(301, "EXTRACT"), T, CFG)[2] == "warn"


def test_old_engine_without_beat_is_not_hung_while_extracting():
    """감시자 이전 코드는 추출 내내 now.json 을 안 쓴다. 셀 파일이 커질수록 추출이 길어지므로 충전 한도(4h)까지 기다린다."""
    old = now_json(3000, "EXTRACT", key="t")
    assert sup.now_fresh(old, T, CFG) is True
    assert sup.now_fresh(now_json(3000, "EXTRACT"), T, CFG) is False      # 심박이 있는 새 엔진은 1200초
    assert sup.now_fresh(now_json(400, "CHARGE", key="t"), T, CFG) is False


def test_verdict_uses_start_time_when_now_json_is_from_previous_run():
    old = now_json(5000, "EXTRACT")                               # 이전 실행이 남긴 화면 (추출 중에 멈춘 것)
    assert engine_verdict(eng(started=T - 60), old, True, T, CFG) == "ok"       # 막 시작 — 아직 안 썼을 뿐
    assert engine_verdict(eng(started=T - 400), old, True, T, CFG) == "hung"    # 이전 단계(EXTRACT)를 믿지 않는다


def test_verdict_old_now_json_without_beat_uses_t():
    assert engine_verdict(eng(), now_json(30, key="t"), True, T, CFG) == "ok"


def test_pid_matches_guards_against_reuse_and_reboot():
    started = T - 100
    assert pid_matches(started - 3, started, boot_t=T - 9999) is True        # 엔진 자신
    assert pid_matches(None, started, boot_t=0) is False                      # 없음
    assert pid_matches(0.0, started, boot_t=0) is True                        # 있는데 권한 때문에 모름 → 살아 있다고 본다
    assert pid_matches(started + 60, started, boot_t=0) is False              # 나중에 태어난 다른 프로그램(pid 재사용)
    assert pid_matches(0.0, started, boot_t=started + 10) is False            # 지난 부팅 때 시작한 엔진은 살 수 없다


# ---------- 할 일 ----------

def test_remaining_cycles():
    assert remaining_cycles(eng(target_last_cycle=5), 3, 4) == 2
    assert remaining_cycles(eng(target_last_cycle=5), 5, 4) == 0
    assert remaining_cycles(None, 1, 4) == 3                             # 기록이 없으면 옛 감시자와 같은 규칙
    assert remaining_cycles(None, 9, 4) == 1                             # 최소 1


def test_plan_dead_restarts_remaining_after_plug_on():
    p = restart_plan("dead", eng(target_last_cycle=6), now_json(4000), 4, [], T, CFG)
    assert p.plug_on and p.restart == 2 and not p.kill and not p.call_human


def test_plan_no_restart_when_all_cycles_done():
    p = restart_plan("dead", eng(target_last_cycle=4), now_json(4000), 4, [], T, CFG)
    assert p.plug_on and p.restart == 0


def test_plan_hourly_cap():
    three = [T - 3000, T - 2000, T - 100]
    p = restart_plan("dead", eng(), now_json(4000), 1, three, T, CFG)
    assert p.restart == 0 and p.plug_on and p.call_human and "1시간에 3번" in p.why
    p = restart_plan("dead", eng(), now_json(4000), 1, [T - 3700] + three[1:], T, CFG)   # 한 번은 1시간 전
    assert p.restart == 3


def test_plan_hung_kills_first():
    p = restart_plan("hung", eng(), now_json(4000), 1, [], T, CFG)
    assert p.kill and p.plug_on and p.restart == 3


def test_plan_respects_intended_stops():
    for v, ex in [("finished", "done"), ("stopped", "stopped"), ("stopped", "interrupted")]:
        p = restart_plan(v, eng(exit=ex), None, 4, [], T, CFG)
        assert p == Plan(why=p.why), (v, ex)                    # 아무것도 하지 않는다
    assert restart_plan("config_error", eng(exit="config_error"), None, 0, [], T, CFG).call_human
    p = restart_plan("paused", eng(exit="failsafe", plug_on=True), None, 2, [], T, CFG)
    assert p.call_human and p.restart == 0 and not p.plug_on


def test_plan_turns_plug_on_when_engine_ended_without_it():
    assert restart_plan("finished", eng(exit="done", plug_on=False), None, 4, [], T, CFG).plug_on
    assert restart_plan("paused", eng(exit="failsafe", plug_on=False), None, 2, [], T, CFG).plug_on
    # Ctrl+C 는 플러그를 그대로 두는 것이 사람의 뜻이다
    assert not restart_plan("stopped", eng(exit="interrupted", plug_on=None), None, 2, [], T, CFG).plug_on


def test_plan_config_problems_block_restart():
    p = restart_plan("dead", eng(), now_json(4000), 1, [], T, CFG, problems=["세트 1 과 세트 2 의 시리얼이 겹친다"])
    assert p.plug_on and p.restart == 0 and p.call_human and "겹친다" in p.why


def test_plan_dry_run_engine_is_left_alone():
    assert restart_plan("dead", eng(args={"dry_run": True}), now_json(4000), 0, [], T, CFG) == \
        Plan(why=restart_plan("dead", eng(args={"dry_run": True}), now_json(4000), 0, [], T, CFG).why)


def test_plan_waits_when_someone_else_writes_now_json():
    p = restart_plan("dead", eng(), now_json(10), 1, [], T, CFG)
    assert not p.plug_on and p.restart == 0 and "다른 엔진" in p.why


def test_plan_absent():
    assert restart_plan("absent", None, None, 0, [], T, CFG) == Plan(why=restart_plan("absent", None, None, 0, [], T, CFG).why)
    assert not restart_plan("absent", None, now_json(10), 0, [], T, CFG).plug_on          # 옛 코드 엔진이 도는 중
    p = restart_plan("absent", None, now_json(4000), 1, [], T, CFG)                      # 기록 없는 옛 엔진이 사라졌다
    assert p.plug_on and p.restart == 3 and not p.call_human


def test_engine_argv_keeps_original_args():
    root = Path("C:/cb")
    head = [str(root / "run_cycle.py"), "--precharge", "--cycles"]
    assert engine_argv("py", root, 3, {"config": None})[1:] == head + ["3"]
    args = {"cycles": 4, "precharge": False, "dry_run": False, "config": "C:/cb/data/run_config.json", "charge_cap_h": 6}
    assert engine_argv("py", root, 2, args)[1:] == head + ["2", "--charge-cap-h", "6", "--config", "C:/cb/data/run_config.json"]


def test_engine_argv_without_record_uses_fallback_config():
    root = Path("C:/cb")
    assert engine_argv("py", root, 3, None, "C:/cb/data/run_config.json")[-2:] == ["--config", "C:/cb/data/run_config.json"]
    assert "--config" not in engine_argv("py", root, 3, None, None)
    assert "--config" not in engine_argv("py", root, 3, {}, "C:/cb/data/run_config.json")   # 기록이 있으면 기록을 따른다


# ---------- 일시 중지 표지 ----------

def test_pause_state():
    assert pause_state(None, T) == (False, "")
    assert pause_state(json.dumps({"reason": "교체", "until": T + 60}), T) == (True, "교체")
    paused, why = pause_state(json.dumps({"reason": "교체", "until": T - 1}), T)
    assert not paused and "만료" in why
    assert pause_state(json.dumps({"reason": "이관"}), T) == (True, "이관")            # 만료 없음 → 사람이 지울 때까지
    assert pause_state("", T)[0] is True                                               # 빈 파일도 일부러 둔 것
    assert pause_state("{깨짐", T)[0] is True
    assert pause_state(json.dumps({"until": "내일"}), T)[0] is True


# ---------- 한 번의 점검 (가짜 바깥 세상) ----------

class World:
    """가짜 바깥 세상 — 무엇이 어떤 순서로 불렸는지 calls 에 남긴다."""

    def __init__(self, hub=True, board=True, plug_ok=True, kill_ok=True, alive=None):
        self.hub, self.board, self.plug_ok, self.kill_ok = hub, board, plug_ok, kill_ok
        self.alive = dict(alive or {})          # pid → 태어난 시각
        self.calls: list[str] = []
        self.alerts: list[str] = []
        self.next_pid = 9000
        self.scan_result = None                 # {"watchdog": [...], "engine": [...]}
        self.engine_args = []

    def deps(self):
        def plug_on():
            self.calls.append("plug_on")
            if not self.plug_ok:
                raise RuntimeError("플러그 응답 없음")
            return "켜짐 · 60.0 W"

        def kill(pid):
            self.calls.append(f"kill:{pid}")
            if self.kill_ok:
                self.alive.pop(pid, None)
            return self.kill_ok

        def spawn(kind):
            def f(*a):
                self.next_pid += 1
                self.calls.append(f"{kind}:{a[0] if a else ''}")
                if kind == "engine":
                    self.engine_args.append(a[1])
                self.alive[self.next_pid] = T
                return self.next_pid
            return f

        return Deps(
            hub_reachable=lambda: self.calls.append("hub") or self.hub,
            reconnect=lambda: self.calls.append("reconnect") or True,
            board_ok=lambda: self.board,
            start_board=spawn("board"),
            plug_on=plug_on,
            proc_created=lambda pid: self.alive.get(pid),
            kill=kill,
            start_engine=spawn("engine"),
            alert=self.alerts.append,
            scan=lambda: self.scan_result,
            boot_t=T - 86400,
        )

    def acts(self):
        return [c for c in self.calls if c != "hub"]


def put(data: Path, engine=None, now=None, cycles=0, pause=None):
    data.mkdir(parents=True, exist_ok=True)
    if engine is not None:
        (data / "engine.json").write_text(json.dumps(engine), encoding="utf-8")
    if now is not None:
        (data / "now.json").write_text(json.dumps(now), encoding="utf-8")
    rows = ["cycle,start"] + [f"{i},x" for i in range(1, cycles + 1)]
    (data / "cycles.csv").write_text("\n".join(rows) + "\n", encoding="utf-8-sig")
    if pause is not None:
        (data / "supervisor_pause").write_text(pause, encoding="utf-8")


def cfg_for(tmp_path, **kw):
    return Config(data_dir=str(tmp_path), **kw)


def test_tick_dead_engine_plug_on_before_restart(tmp_path):
    put(tmp_path, eng(target_last_cycle=4), now_json(4000), cycles=1)
    w = World()
    rep, lines = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    assert w.acts() == ["plug_on", "engine:3"]                 # 플러그 ON 이 먼저, 남은 3사이클
    assert rep["checks"] == {"wifi": "ok", "board": "ok", "engine": "dead", "heartbeat": "-", "old_watchdog": []}
    assert rep["restarts_1h"] == 1 and rep["memo"]["spawned"]["pid"] == 9001
    assert rep["bench_id"] == "hq-bench-1" and rep["last_action"]["what"]
    assert len(w.alerts) == 1 and "플러그 ON" in w.alerts[0] and "다시 시작" in w.alerts[0]


def test_tick_first_tick_after_reboot(tmp_path):
    """재부팅 직후: engine.json 은 '돌고 있음'인데 그 엔진은 지난 부팅 때 것 → 죽은 것으로 보고 플러그 ON 부터."""
    put(tmp_path, eng(started=T - 7 * 86400), now_json(7 * 86400 - 100), cycles=2)
    w = World(alive={4242: T - 7 * 86400})                     # 같은 pid 를 다른 프로그램이 받았다 해도
    w_deps = w.deps()
    rep, _ = tick(cfg_for(tmp_path), tmp_path, None, w_deps, T)
    assert rep["checks"]["engine"] == "dead"
    assert w.acts()[0] == "plug_on" and w.acts()[1] == "engine:2"


def test_tick_hung_needs_two_ticks_then_kill_plug_restart(tmp_path):
    put(tmp_path, eng(), now_json(400, "CHARGE"), cycles=1)
    w = World(alive={4242: T - 3601})
    rep, _ = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    assert rep["checks"]["engine"] == "hung" and w.acts() == []          # 처음 본 '멈춤'은 기다린다
    rep2, _ = tick(cfg_for(tmp_path), tmp_path, rep, w.deps(), T + 60)
    assert w.acts() == ["kill:4242", "plug_on", "engine:3"]


def test_tick_hung_kill_failure_never_double_starts(tmp_path):
    put(tmp_path, eng(), now_json(400, "CHARGE"), cycles=1)
    w = World(alive={4242: T - 3601}, kill_ok=False)
    rep, _ = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    tick(cfg_for(tmp_path), tmp_path, rep, w.deps(), T + 60)
    assert "kill:4242" in w.acts() and not any(c.startswith("engine:") for c in w.acts())


def test_tick_plug_on_once_per_incident(tmp_path):
    """시간당 한도에 걸려 다시 띄우지 못하는 동안 매 점검마다 플러그를 껐다 켜면 안 된다."""
    put(tmp_path, eng(), now_json(4000), cycles=1)
    prev = {"memo": {"restart_times": [T - 3000, T - 2000, T - 1000]}}
    w = World()
    rep, _ = tick(cfg_for(tmp_path), tmp_path, prev, w.deps(), T)
    rep, _ = tick(cfg_for(tmp_path), tmp_path, rep, w.deps(), T + 60)
    rep, _ = tick(cfg_for(tmp_path), tmp_path, rep, w.deps(), T + 120)
    assert w.acts() == ["plug_on"]
    assert len(w.alerts) == 1 and "사람 확인" in w.alerts[0]


def test_tick_restart_waits_until_plug_on_succeeds(tmp_path):
    put(tmp_path, eng(), now_json(4000), cycles=1)
    w = World(plug_ok=False)
    rep, lines = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    assert w.acts() == ["plug_on"] and any("미룬다" in l for l in lines)
    w.plug_ok = True
    tick(cfg_for(tmp_path), tmp_path, rep, w.deps(), T + 60)
    assert w.acts() == ["plug_on", "plug_on", "engine:3"]


def test_tick_alerts_after_repeated_plug_failures(tmp_path):
    put(tmp_path, eng(), now_json(4000), cycles=1)
    w = World(plug_ok=False)
    rep = None
    for i in range(4):
        rep, _ = tick(cfg_for(tmp_path), tmp_path, rep, w.deps(), T + 60 * i)
    assert sum("연속 켜지 못함" in a for a in w.alerts) == 1


def test_tick_pause_marker_blocks_all_actions(tmp_path):
    put(tmp_path, eng(), now_json(4000), cycles=1, pause=json.dumps({"reason": "코드 교체", "until": T + 600}))
    w = World(board=False, hub=False)
    rep, lines = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    assert w.acts() == [] and lines == [] and w.alerts == []
    assert rep["paused"] == "코드 교체" and rep["checks"]["engine"] == "dead"


def test_tick_expired_pause_marker_is_ignored(tmp_path):
    put(tmp_path, eng(), now_json(4000), cycles=1, pause=json.dumps({"reason": "교체", "until": T - 1}))
    w = World()
    tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    assert w.acts() == ["plug_on", "engine:3"]


def test_tick_finished_and_stopped_do_nothing(tmp_path):
    for ex in ("done", "stopped", "interrupted"):
        put(tmp_path, eng(exit=ex, plug_on=True if ex != "interrupted" else None), now_json(4000), cycles=4)
        w = World()
        rep, _ = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
        assert w.acts() == [] and w.alerts == [], ex


def test_tick_reconnects_wifi_only_when_it_must_act(tmp_path):
    put(tmp_path, eng(), now_json(10), cycles=1)              # 엔진 정상 — 재연결은 엔진이 한다
    w = World(hub=False, alive={4242: T - 3601})
    rep, _ = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    assert w.acts() == [] and rep["checks"]["wifi"] == "down"
    put(tmp_path, eng(), now_json(4000), cycles=1)            # 엔진이 죽었고 시험망도 안 닿는다
    w = World(hub=False)
    rep, _ = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    assert w.acts() == ["reconnect", "plug_on", "engine:3"] and rep["checks"]["wifi"] == "reconnected"


def test_tick_starts_board_once(tmp_path):
    put(tmp_path, eng(exit="done", plug_on=True), now_json(4000), cycles=4)
    w = World(board=False)
    rep, _ = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    assert w.acts() == ["board:"] and rep["checks"]["board"] == "started"
    rep, _ = tick(cfg_for(tmp_path), tmp_path, rep, w.deps(), T + 60)     # 띄운 서버가 살아 있는데 아직 응답 없음
    assert w.acts() == ["board:"] and rep["checks"]["board"] == "down"
    assert len(w.alerts) == 1


def test_tick_config_problem_blocks_restart(tmp_path):
    put(tmp_path, eng(), now_json(4000), cycles=1)
    bad = cfg_for(tmp_path, sets=[{"id": 1, "plug_mac": "20:E1:5D:E6:9C:77", "serials": "11733-11756"},
                                  {"id": 2, "plug_mac": "AA:BB:CC:DD:EE:FF", "serials": "11750-11760"}])
    w = World()
    rep, _ = tick(bad, tmp_path, None, w.deps(), T)
    assert w.acts() == ["plug_on"] and rep["config_problems"]
    assert "겹친다" in w.alerts[0]


def test_tick_waits_for_just_spawned_engine(tmp_path):
    put(tmp_path, eng(), now_json(4000), cycles=1)
    w = World(alive={777: T - 30})
    prev = {"memo": {"spawned": {"pid": 777, "t": T - 30}}}
    rep, _ = tick(cfg_for(tmp_path), tmp_path, prev, w.deps(), T)
    assert w.acts() == [] and "시작하는 중" in rep["why"]


def test_tick_engine_args_carried_from_record(tmp_path):
    args = {"cycles": 4, "precharge": True, "dry_run": False, "config": "C:/cb/data/run_config.json"}
    put(tmp_path, eng(args=args), now_json(4000), cycles=1)
    w = World()
    tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    assert w.engine_args == [args]


def test_tick_stands_down_while_old_watchdog_lives(tmp_path):
    put(tmp_path, eng(), now_json(4000), cycles=1)
    w = World(board=False)
    w.scan_result = {"watchdog": [1596], "engine": []}
    rep, lines = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    assert w.acts() == [] and w.alerts == [] and lines == []
    assert rep["checks"]["old_watchdog"] == [1596] and "watchdog.ps1" in rep["why"] and "비정상 종료" in rep["why"]
    w.scan_result = {"watchdog": [], "engine": []}                  # 옛 감시자가 꺼지면 그때부터 조치한다
    tick(cfg_for(tmp_path), tmp_path, rep, w.deps(), T + 60)
    assert w.acts() == ["plug_on", "board:", "engine:3"]


def test_tick_restarts_engine_without_record_like_old_watchdog(tmp_path):
    """engine.json 없이 돌던 옛 엔진이 사라짐 → 플러그 ON 뒤 max(1, 4 − 기록 수) 사이클, 기록 대신 fallback 설정."""
    put(tmp_path, None, now_json(4000), cycles=1)
    w = World()
    rep, _ = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    assert w.acts() == ["plug_on", "engine:3"] and w.engine_args == [None]
    assert rep["checks"]["engine"] == "absent"


def test_tick_never_kills_engine_not_in_record(tmp_path):
    """기록에 없는 엔진 프로세스가 남았는데 이 data 폴더의 now.json 은 멈췄다 — 다른 data 폴더로 도는 건강한 엔진일 수 있다
    (2026-10-10 모의 점검에서 실제로 이 PC 의 진짜 엔진이 이렇게 잡혔다). 끝내지도 다시 띄우지도 않고, 두 번 확인한 뒤 플러그 ON + 사람 호출."""
    put(tmp_path, None, now_json(4000, "CHARGE", key="t"), cycles=2)
    w = World(alive={10612: T - 9000})
    w.scan_result = {"watchdog": [], "engine": [10612]}
    rep, _ = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    assert rep["checks"]["engine"] == "hung" and rep["strays"] == [10612] and w.acts() == []
    rep, _ = tick(cfg_for(tmp_path), tmp_path, rep, w.deps(), T + 60)
    assert w.acts() == ["plug_on"]
    assert len(w.alerts) == 1 and "끝내지 않는다" in w.alerts[0]
    tick(cfg_for(tmp_path), tmp_path, rep, w.deps(), T + 120)
    assert w.acts() == ["plug_on"] and len(w.alerts) == 1          # 같은 사건에서 되풀이하지 않는다


def test_tick_waits_for_young_engine_without_record(tmp_path):
    put(tmp_path, None, now_json(4000), cycles=1)
    w = World(alive={10612: T - 20})
    w.scan_result = {"watchdog": [], "engine": [10612]}
    rep, _ = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    assert w.acts() == [] and "막 뜬 엔진" in rep["why"]


def test_tick_reports_heartbeat_warning_without_acting(tmp_path):
    put(tmp_path, eng(), now_json(100), cycles=1)
    w = World(alive={4242: T - 3601})
    rep, _ = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    assert rep["checks"]["engine"] == "ok" and rep["checks"]["heartbeat"] == "warn" and w.acts() == []
    assert rep["engine"]["beat_age"] == 100.0


def test_tick_restart_count_survives_supervisor_restart(tmp_path):
    """감시자 자신이 다시 떠도(작업 스케줄러) supervisor.json 의 memo 로 시간당 한도를 이어 센다."""
    put(tmp_path, eng(), now_json(4000), cycles=1)
    w = World()
    rep, _ = tick(cfg_for(tmp_path), tmp_path, None, w.deps(), T)
    reloaded = json.loads(json.dumps(rep))
    assert reloaded["memo"]["restart_times"] == [T]


# ---------- run_once ----------

def test_run_once_writes_state_unless_dry(tmp_path):
    put(tmp_path, eng(exit="done", plug_on=True), now_json(4000), cycles=4)
    load = lambda path: Config(data_dir=str(tmp_path))
    logs = []
    w = World()
    sup.run_once(tmp_path, None, lambda c, d: w.deps(), True, lambda d, m: logs.append(m), now_t=T, load=load)
    assert not (tmp_path / sup.STATE_FILE).exists()                # 모의는 상태를 남기지 않는다
    sup.run_once(tmp_path, None, lambda c, d: w.deps(), False, lambda d, m: logs.append(m), now_t=T, load=load)
    st = json.loads((tmp_path / sup.STATE_FILE).read_text(encoding="utf-8"))
    assert st["checks"]["engine"] == "finished" and st["pid"] == os.getpid() and st["tick"] == 1
    assert any("점검 — 엔진 finished" in m for m in logs)


def test_run_once_config_error_does_nothing_and_alerts_once(tmp_path, monkeypatch):
    monkeypatch.setattr(sup, "data_path", lambda root, cfg: tmp_path)
    put(tmp_path, eng(), now_json(4000), cycles=1)

    def broken(path):
        raise KeyError("알 수 없는 설정: pol_s")

    w = World()
    for i in range(2):
        rep = sup.run_once(tmp_path, None, lambda c, d: w.deps(), False, lambda d, m: None, now_t=T + i, load=broken)
    assert w.acts() == [] and len(w.alerts) == 1 and "설정을 읽지 못해" in w.alerts[0]
    assert rep["checks"] == {"config": "error"}


# ---------- 실물 통로 (모의 모드) · 프로세스 도우미 ----------

def test_dry_run_deps_only_log(tmp_path):
    import supervise
    logs = []
    make = supervise.make_deps_factory(True, lambda d, m: logs.append(m), None)
    d = make(Config(data_dir=str(tmp_path)), tmp_path)
    assert d.plug_on() == "모의" and d.kill(1) is True and d.start_engine(2, {}) == 0 and d.start_board() == 0
    d.alert("시험")
    assert any("플러그 ON" in m for m in logs) and any("--cycles 2" in m for m in logs) and any("알림: 시험" in m for m in logs)


def test_board_probe_against_real_board_handler(tmp_path):
    """감시자의 결과판 확인이 실제 serve_board 처리기에 맞는지 — PIN 없이(자격 증명 관리자를 읽지 않고) 임의 포트로 띄운다."""
    import threading
    from http.server import ThreadingHTTPServer
    import serve_board
    import supervise
    from cellbench.control import PinGuard
    (tmp_path / "now.json").write_text(json.dumps({"phase": "CHARGE"}), encoding="utf-8")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), serve_board.make_handler(tmp_path, PinGuard(None)))
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
    try:
        assert supervise.board_ok(port) is True
    finally:
        srv.shutdown(); srv.server_close()
    assert supervise.board_ok(port) is False


def test_proc_created_and_boot_time():
    me = proc.created(os.getpid())
    assert me is not None and proc.boot_time() < me <= time.time()
    assert proc.created(0) is None
    assert proc.created(0x7FFFFFF0) is None                          # 없는 pid


def test_lock_allows_only_one(tmp_path):
    a = proc.acquire_lock(tmp_path / "supervisor.lock")
    assert a is not None
    assert proc.acquire_lock(tmp_path / "supervisor.lock") is None      # 두 번째 감시자는 조용히 끝난다
    a.close()                                                             # 첫 감시자가 죽으면(파일이 닫히면) 풀린다
    b = proc.acquire_lock(tmp_path / "supervisor.lock")
    assert b is not None
    b.close()


def test_spawn_and_kill_a_harmless_child(tmp_path):
    pid = proc.spawn([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, tmp_path / "err.txt")
    try:
        born = proc.created(pid)
        assert born is not None and pid_matches(born, time.time(), proc.boot_time())
    finally:
        assert proc.kill(pid) is True
    assert proc.created(pid) is None
