"""규칙 C — failsafe 뒤 자동 재개 (자동 복구 기획 5절 · 커밋 3). 장비 · 프로세스 없이 돈다.

엔진이 Dock 충전 멈춤(dock_stall)으로 failsafe 에 들면 — 끊었다 켜기를 1시간 3번 다 쓰고도 셀이 내려갔다 — 감시자가 10분 · 1시간 · 3시간 뒤
플러그를 켜고 엔진을 예비 충전부터 다시 띄운다(24시간 3번). Dock 은 나중에 돌아온다(사고 3 끝맺음 84.1 W). 돌아오지 않았으면 규칙 A 가
약 13분 만에 다시 멈춘다. 다른 이유의 failsafe(짝 이상 · 연속 실패 · 충전 한도 · 이유 없음)는 지금처럼 되살리지 않는다.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench import supervisor as sup
from cellbench.config import Config
from test_supervisor import T, World, cfg_for, eng, now_json, put

ENDED = T - 30                      # 엔진이 failsafe 로 끝난 시각


def stalled(**kw):
    e = eng(exit="failsafe", why="dock_stall", plug_on=True, ended=ENDED, target_last_cycle=10)
    e.update(kw)
    return e


def cycles_csv(data: Path, done: int, total: int | None = None):
    """완료 사이클 done 줄(방전 시작 · 만충이 있는 줄) + 중단 줄(total − done)."""
    rows = ["cycle,start,discharge_start,full_at,note"]
    rows += [f"{i},x,2026-11-16 10:00:00,2026-11-16 14:00:00," for i in range(1, done + 1)]
    rows += [f"{i},x,2026-11-16 20:00:00,,dock_stall" for i in range(done + 1, (total or done) + 1)]
    (data / "cycles.csv").write_text("\n".join(rows) + "\n", encoding="utf-8-sig")


def ticks(tmp_path, w, minutes, rep=None, start=0, cfg=None):
    cfg = cfg or cfg_for(tmp_path)
    logs = []
    for i in range(start, start + minutes + 1):
        rep, lines = sup.tick(cfg, tmp_path, rep, w.deps(), T + 60 * i)
        logs += lines
    return rep, logs


# ---------- 순수 판정 ----------

def test_plan_waits_then_resumes_with_precharge():
    cfg = Config(auto_continue=False)
    p = sup.restart_plan("paused", stalled(), None, 3, [], T, cfg)
    assert p.restart == 0 and not p.call_human and "자동 재개 대기" in p.why and "(1/3)" in p.why
    assert time.strftime("%H:%M", time.localtime(ENDED + 600)) in p.why
    p = sup.restart_plan("paused", stalled(), None, 3, [], ENDED + 600, cfg)
    assert p.restart == 7 and p.plug_on and p.precharge and p.resume and "자동 재개 1/3" in p.why


def test_plan_gaps_grow_and_stop_after_three_in_24h():
    cfg = Config(auto_continue=False)
    one = [ENDED - 3000]
    assert sup.restart_plan("paused", stalled(), None, 3, [], ENDED + 3599, cfg, resumes=one).restart == 0
    p = sup.restart_plan("paused", stalled(), None, 3, [], ENDED + 3600, cfg, resumes=one)
    assert p.restart == 7 and "2/3" in p.why
    two = [ENDED - 9000, ENDED - 3000]
    assert sup.restart_plan("paused", stalled(), None, 3, [], ENDED + 10799, cfg, resumes=two).restart == 0
    assert sup.restart_plan("paused", stalled(), None, 3, [], ENDED + 10800, cfg, resumes=two).restart == 7
    three = [ENDED - 20000, ENDED - 9000, ENDED - 3000]
    p = sup.restart_plan("paused", stalled(), None, 3, [], ENDED + 20000, cfg, resumes=three)
    assert p.restart == 0 and p.call_human and "3번" in p.why                 # 소진 — 빨강
    assert sup.restart_plan("paused", stalled(), None, 3, [], ENDED + 99999, cfg, resumes=three).restart == 7   # 셋 다 24시간을 넘었다
    old = [ENDED - 90000, ENDED - 9000, ENDED - 3000]                          # 하나는 24시간 넘었다
    assert sup.restart_plan("paused", stalled(), None, 3, [], ENDED + 10800, cfg, resumes=old).restart == 7


def test_plan_other_failsafe_reasons_are_not_revived():
    cfg = Config(auto_continue=False)
    for why in (None, "pair_mismatch", "charge_timeout_streak", "failures"):
        e = stalled(why=why) if why else {k: v for k, v in stalled().items() if k != "why"}
        p = sup.restart_plan("paused", e, None, 3, [], ENDED + 99999, cfg)
        assert p.restart == 0 and p.call_human and "되살리지 않는다" in p.why


def test_plan_respects_hourly_limit_config_problems_and_target():
    cfg = Config(auto_continue=False)
    p = sup.restart_plan("paused", stalled(), None, 3, [T - 100, T - 200, T - 300], ENDED + 600, cfg)
    assert p.restart == 0 and p.call_human                                     # 기존 '1시간 3번' 한도도 함께
    p = sup.restart_plan("paused", stalled(), None, 3, [], ENDED + 600, cfg, problems=["시리얼이 겹친다"])
    assert p.restart == 0 and p.call_human and p.plug_on
    p = sup.restart_plan("paused", stalled(target_last_cycle=3), None, 3, [], ENDED + 600, cfg)
    assert p.restart == 0 and not p.call_human                                 # 목표를 이미 다 돌았다


def test_plan_waits_if_someone_else_writes_now_json():
    cfg = Config(auto_continue=False)
    p = sup.restart_plan("paused", stalled(), {"t": ENDED + 590, "beat": ENDED + 590, "phase": "CHARGE"}, 3, [], ENDED + 600, cfg)
    assert p.restart == 0 and "다른 엔진" in p.why


# ---------- 한 번의 점검 (가짜 바깥 세상) ----------

def test_tick_resumes_after_10_min_plug_first_and_keeps_ledger(tmp_path):
    put(tmp_path, stalled(), now_json(40, "CHARGE"), cycles=0)
    cycles_csv(tmp_path, 3, total=4)
    w = World()
    rep, logs = ticks(tmp_path, w, 9)
    assert "engine:" not in " ".join(w.acts())
    assert rep["signals"]["engine"][0] == "yellow" and "자동 재개 대기" in rep["signals"]["engine"][1]
    rep, logs = ticks(tmp_path, w, 0, rep=rep, start=10)
    assert w.acts()[-2:] == ["plug_on", "engine:6"]                         # 플러그 ON 이 먼저, 남은 10 − 4 = 6사이클
    assert w.engine_args[-1] == stalled()["args"]
    assert any("자동 재개 1/3" in l for l in logs) and any("엔진 다시 시작" in l and "--precharge" in l for l in logs)
    led = rep["memo"]["resume"]
    assert led["times"] == [T + 600] and led["cycles"] == 3
    assert rep["signals"]["engine"][0] == "yellow"


def test_tick_does_not_guard_plug_while_resumed_engine_starts(tmp_path):
    """띄운 엔진이 engine.json 을 쓰기 전 — 같은 failsafe 기록이 보여도 기다린다(플러그 지키기 · 두 번째 재개 없음)."""
    put(tmp_path, stalled(ended=T - 600), now_json(4000, "CHARGE"), cycles=0)
    cycles_csv(tmp_path, 3, total=4)
    w = World()
    rep, _ = ticks(tmp_path, w, 0)
    assert w.acts() == ["plug_on", "engine:6"]
    rep, logs = ticks(tmp_path, w, 3, rep=rep, start=1)
    assert w.acts() == ["plug_on", "engine:6"]
    assert rep["plug_guard"]["active"] is False


def test_ledger_resets_after_a_completed_cycle(tmp_path):
    put(tmp_path, stalled(ended=T - 600), now_json(4000, "CHARGE"), cycles=0)
    cycles_csv(tmp_path, 3, total=4)
    w = World()
    rep, _ = ticks(tmp_path, w, 0)
    assert rep["memo"]["resume"]["times"] == [T]
    put(tmp_path, eng(exit=None, started=T + 120, pid=9001), None)
    cycles_csv(tmp_path, 4, total=5)                                         # 다시 띄운 엔진이 사이클 하나를 마쳤다
    w.alive[9001] = T + 120
    (tmp_path / "now.json").write_text(json.dumps({"t": T + 7200, "beat": T + 7200, "phase": "DISCHARGE"}), encoding="utf-8")
    rep, _ = sup.tick(cfg_for(tmp_path), tmp_path, rep, w.deps(), T + 7200)
    assert rep["memo"]["resume"]["times"] == []


def test_tick_red_after_three_resumes(tmp_path):
    put(tmp_path, stalled(), now_json(4000, "CHARGE"), cycles=0)
    cycles_csv(tmp_path, 3, total=4)
    w = World()
    prev = {"memo": {"resume": {"times": [T - 20000, T - 9000, T - 3000], "cycles": 3}}}
    rep, _ = sup.tick(cfg_for(tmp_path), tmp_path, prev, w.deps(), T + 999)
    assert rep["signals"]["engine"][0] == "red" and "engine:" not in " ".join(w.acts())


def test_tick_pause_marker_and_stray_engine_block_resume(tmp_path):
    put(tmp_path, stalled(ended=T - 600), now_json(4000, "CHARGE"), cycles=0,
        pause=json.dumps({"reason": "수동 작업", "until": T + 7200}))
    w = World()
    ticks(tmp_path, w, 3)
    assert w.acts() == []
    t2 = tmp_path / "b"
    put(t2, stalled(ended=T - 600), now_json(4000, "CHARGE"), cycles=0)
    w2 = World()
    w2.scan_result = {"watchdog": [], "engine": [777]}
    rep, _ = ticks(t2, w2, 0)
    assert "engine:" not in " ".join(w2.acts()) and "자동 재개" in rep["why"]


def test_failsafe_without_why_still_calls_human(tmp_path):
    """지금 동작 그대로 — why 가 없으면(옛 엔진 · 다른 이유) 되살리지 않고 빨강."""
    e = {k: v for k, v in stalled(ended=T - 99999).items() if k != "why"}
    put(tmp_path, e, now_json(4000, "CHARGE"), cycles=2)
    w = World()
    rep, _ = ticks(tmp_path, w, 2)
    assert "engine:" not in " ".join(w.acts()) and rep["signals"]["engine"][0] == "red"
