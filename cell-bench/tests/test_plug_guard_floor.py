"""규칙 B — 엔진이 서 있는 동안 Dock 유지 (자동 복구 기획 5절 · 커밋 1). 장비 없이 돈다.

Dock 은 충전을 한 번 멈추면 켜진 채로는 다시 시작하지 않는다. 그 바닥 전력(10 ~ 35 W)은 저전력 복구(10 W 미만)에 걸리지 않아,
엔진이 failsafe 로 서 있던 11-14 · 11-17 에 아무도 끊었다 켜지 않았고 셀 24대가 꺼졌다(사고 2 · 4). 감시자의 플러그 지키기가
켜짐 · 바닥 전력이 30분 이어지면 30초 끊었다 켠다 — 판정은 레일 독 충전 유지와 같은 함수(guards.floor_recycle), 주기만 다르다.
"""
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench import guards
from cellbench import supervisor as sup
from cellbench.config import DEFAULT_SET
from motionbench import charge as C
from test_supervisor import T, World, cfg_for, eng, now_json, put

P2 = "20:E1:5D:E6:97:5B"


# ---------- 순수 함수 — 주기를 인자로 받는다 ----------

def test_floor_recycle_fires_after_period():
    act, since = guards.floor_recycle(None, True, 24.6, 0.0, None, 10.0, 35.0, 1800.0)
    assert (act, since) == (None, 0.0)
    act, since = guards.floor_recycle(None, True, 33.8, 1799.0, since, 10.0, 35.0, 1800.0)
    assert (act, since) == (None, 0.0)
    assert guards.floor_recycle(None, True, 33.8, 1800.0, since, 10.0, 35.0, 1800.0) == ("recharge", None)


def test_floor_recycle_resets_outside_band_and_keeps_other_action():
    """충전 중(35 W 초과) · 막힘(10 W 미만) · 꺼짐 · 못 읽음 · 전력 모름은 시간을 다시 센다. 다른 처리가 있으면 그것을 돌려준다."""
    for on, w in ((True, 55.0), (True, 9.4), (False, 0.0), (None, 24.0), (True, float("nan"))):
        assert guards.floor_recycle(None, on, w, 100.0, 0.0, 10.0, 35.0, 1800.0) == (None, None)
    assert guards.floor_recycle("plug_on", True, 24.0, 100.0, 0.0, 10.0, 35.0, 1800.0) == ("plug_on", None)
    assert guards.floor_recycle(None, True, 10.0, 0.0, None, 10.0, 35.0, 60.0) == (None, 0.0)      # 띠의 양 끝은 바닥이다
    assert guards.floor_recycle(None, True, 35.0, 60.0, 0.0, 10.0, 35.0, 60.0) == ("recharge", None)


def test_rail_keeps_two_hours_through_shared_function():
    """레일 독 충전 유지는 같은 함수를 2시간 주기로 쓴다."""
    assert C.FLOOR_RECYCLE_S == 7200.0
    assert C.floor_check(None, True, 24.0, C.FLOOR_RECYCLE_S - 1, 0.0, 10.0, 35.0) == (None, 0.0)
    assert C.floor_check(None, True, 24.0, C.FLOOR_RECYCLE_S, 0.0, 10.0, 35.0) == ("recharge", None)


# ---------- 감시자의 플러그 지키기 ----------

def reading_world(reads):
    """plug_read 가 reads 를 차례로 돌려준다(다 쓰면 마지막 값)."""
    w = World()
    q = list(reads)
    base = w.deps

    def deps():
        def read():
            w.calls.append("read")
            return q.pop(0) if len(q) > 1 else q[0]
        return replace(base(), plug_read=read)
    w.deps = deps
    return w


def run_ticks(tmp_path, w, minutes, rep=None, cfg=None, start=0, beat=False):
    """1분마다 점검 — 분 start 부터 start + minutes 까지. beat 면 엔진이 도는 것처럼 점검마다 now.json 의 심박을 새로 쓴다."""
    cfg = cfg or cfg_for(tmp_path)
    logs = []
    for i in range(start, start + minutes + 1):
        if beat:
            (tmp_path / "now.json").write_text(json.dumps({"t": T + 60 * i, "beat": T + 60 * i, "phase": "CHARGE"}), encoding="utf-8")
        rep, lines = sup.tick(cfg, tmp_path, rep, w.deps(), T + 60 * i)
        logs += lines
    return rep, logs


def test_failsafe_engine_floor_kicked_after_30_min_without_slack(tmp_path):
    """사고 4 의 꼴 — failsafe 로 선 엔진 · 플러그 켜짐 · 15.6 W. 처음 읽는 10분 뒤부터 30분 바닥이면 끊었다 켠다(Slack 없음)."""
    put(tmp_path, eng(exit="failsafe", plug_on=True), now_json(4000, "CHARGE"), cycles=2)
    w = reading_world([(True, 15.6)])
    rep, logs = run_ticks(tmp_path, w, 39)
    assert w.acts().count("plug_on") == 0                                   # 10분 · 20분 · 30분 읽기 — 아직 30분이 안 됐다
    rep, more = run_ticks(tmp_path, w, 0, rep=rep, start=40)
    assert w.acts().count("plug_on") == 1                                   # 첫 바닥 읽기(10분) + 30분 = 40분
    g = rep["plug_guard"]
    assert g["action"] == "floor" and g["recharges_1h"] == 0                # 저전력 한도(1시간 3번)에 세지 않는다
    assert any("바닥" in l and "끊었다 켰다" in l for l in more)            # supervisor.log 한 줄
    assert rep["signals"]["plug_on"] == ["green", "이상 없음"]               # 일상 동작 — 불을 바꾸지 않는다
    assert not any("바닥" in a for a in w.alerts)


def test_floor_kick_repeats_every_30_min_while_dock_stays_floor(tmp_path):
    """다 찬 셀의 바닥이어도 30분마다 한 번 — 하루 약 72번이 릴레이 예산(기획 규칙 B 표)."""
    put(tmp_path, eng(exit="done", plug_on=True), now_json(4000, "DONE"), cycles=4)
    w = reading_world([(True, 29.0)])
    rep, _ = run_ticks(tmp_path, w, 160)
    kicks = w.acts().count("plug_on")
    assert 4 <= kicks <= 5                                                  # 첫 읽기 10분 뒤부터 약 31분 간격
    assert rep["signals"]["plug_on"][0] == "green"


def test_charging_or_low_power_does_not_floor_kick(tmp_path):
    """충전 중(61 W)은 손대지 않는다. 10 W 미만은 지금 규칙(1분 뒤 끊었다 켜기 · 노랑)이 맡는다."""
    put(tmp_path, eng(exit="failsafe", plug_on=True), now_json(4000, "CHARGE"), cycles=2)
    w = reading_world([(True, 61.0)])
    run_ticks(tmp_path, w, 90)
    assert "plug_on" not in w.acts()
    t2 = tmp_path / "b"
    put(t2, eng(exit="failsafe", plug_on=True), now_json(4000, "CHARGE"), cycles=2)
    w2 = reading_world([(True, 1.4), (True, 1.4), (True, 61.0)])
    rep, logs = run_ticks(t2, w2, 15)
    assert w2.acts().count("plug_on") == 1 and rep["plug_guard"]["recharges_1h"] == 1
    assert rep["signals"]["plug_on"] == ["yellow", sup.GUARD_KICK]


def test_floor_kick_respects_running_engine_and_pause(tmp_path):
    """I1 · I2 — 엔진이 돌면 물러난다(그 엔진이 방전 중일 수 있다). 일시 중지 표지가 있으면 손대지 않는다."""
    put(tmp_path, eng(exit=None), now_json(5, "CHARGE"), cycles=2)
    w = reading_world([(True, 24.0)])
    w.alive[4242] = T - 3600
    rep, _ = run_ticks(tmp_path, w, 60, beat=True)
    assert rep["checks"]["engine"] == "ok"
    assert "read" not in w.acts() and "plug_on" not in w.acts()
    t2 = tmp_path / "b"
    put(t2, eng(exit="failsafe", plug_on=True), now_json(4000, "CHARGE"), cycles=2,
        pause=json.dumps({"reason": "수동 작업", "until": T + 7200}))
    w2 = reading_world([(True, 24.0)])
    run_ticks(t2, w2, 60)
    assert "read" not in w2.acts() and "plug_on" not in w2.acts()


# ---------- 세트 k (다중 모드) — 같은 규칙, 그리고 장부 한 겹 ----------

def multi_setup(tmp_path, reads):
    put(tmp_path, eng(exit="failsafe", plug_on=True, mode="multi",
                      sets=[{"id": 1, "mac": DEFAULT_SET["plug_mac"], "ip": ""}, {"id": 2, "mac": P2, "ip": ""}]),
        now_json(4000, "HOLD"), cycles=2)
    cfg = cfg_for(tmp_path, sets=[dict(DEFAULT_SET), {"id": 2, "plug_mac": P2, "serials": "11594-11617"}])
    w = reading_world([(True, 61.0)])
    acts = []
    base = w.deps

    def deps():
        d = base()
        d.plug_read_mac = lambda mac, ip: (acts.append(("read", mac)), reads[mac])[1]
        d.plug_on_mac = lambda mac, ip: (acts.append(("on", mac)), "켜짐")[1]
        d.plug_recharge_mac = lambda mac, ip: (acts.append(("recharge", mac)), "켜짐 · 84.0 W")[1]
        return d
    w.deps = deps
    return cfg, w, acts


def test_set_k_floor_kicked_like_set_1(tmp_path):
    cfg, w, acts = multi_setup(tmp_path, {P2: (True, 32.4)})
    rep, logs = run_ticks(tmp_path, w, 41, cfg=cfg)
    assert acts.count(("recharge", P2)) == 1
    assert any(l.startswith("세트 2 플러그 지키기:") and "바닥" in l for l in logs)
    assert rep["memo"]["guards"]["2"]["guard"]["action"] in ("floor", "ok")


def test_set_k_capped_turns_plug_lane_red(tmp_path):
    """함께 발견한 것(기획 부록) — 세트 k 의 장부는 memo.guards[k]["guard"] 로 한 겹 싸여 있는데, 불 판정이 바깥 겹을 봐서
    세트 2 의 빨강(1시간 한도까지 끊었다 켰는데도 안 됨)이 올라오지 않았다. 운영 supervisor.json 의 memo.guards["2"] 도 같은 모양이다."""
    cfg, w, acts = multi_setup(tmp_path, {P2: (True, 1.4)})
    rep, _ = run_ticks(tmp_path, w, 41, cfg=cfg)
    assert acts.count(("recharge", P2)) == 3
    assert rep["memo"]["guards"]["2"]["guard"]["capped"] is True
    assert rep["signals"]["plug_on"][0] == "red" and rep["signals"]["plug_on"][1].startswith("세트 2 — ")
