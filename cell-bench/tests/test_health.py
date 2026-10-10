"""신호등 — 9개 차선의 노랑·빨강 문턱을 경계값 양쪽에서 본다 (cellbench/health.py · cellbench/osinfo.py).

판정은 순수 함수라 장비·운영체제·네트워크에 닿지 않는다. 입력은 엔진(now.json) · 감시자(supervisor.json) · 운영체제 정보(osinfo.json) ·
cycles.csv 를 흉내 낸 dict 이다. 문턱 숫자는 config.HEALTH 와 이미 있던 Config 값(심박 · 셀 저장량 · 디스크 · 라이브 끊김)이다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench import health as H
from cellbench import osinfo
from cellbench.config import HEALTH, Config

T = 1_800_000_000.0          # 2027-01-15 08:00:00 UTC = 17:00 KST
CFG = Config()


def cells(n=24, battery=80, age=0.5, waiting=False):
    return [{"serial": 11733 + i, "ip": f"192.168.1.{20 + i}", "battery": battery, "hr": 0, "rssi": -50, "state": 1,
             "age": age, "waiting": waiting} for i in range(n)]


def metrics(**kw):
    m = {"plug": {"retries_1h": 0, "last_call_s": 1.0, "toggles_total": 100}, "recover_cycle": 0, "dock_power_fail": 0,
         "manual_plug_1h": 0, "manual_plug_last": None, "ip_changes_cycle": 0, "reconnects_24h": 0, "live_gaps_1h": 0,
         "events_1h": 0, "disk_free_gb": 300.0, "cell_storage": {"max_pct": 10.0, "max_serial": 11740, "est_full_at": T + 86400},
         "not_charging": [], "waiting": [],
         "cloud": {"enabled": True, "ok_t": T - 30, "fail_t": None, "fail_1h": 0, "consecutive_auth_fail": 0, "queue": 0, "dropped": 0}}
    m.update(kw)
    return m


def now_(age=10.0, phase="DISCHARGE", since=600.0, **kw):
    n = {"t": T - age, "beat": T - age, "phase": phase, "phase_since": T - since, "cycle": 3, "expected": 24,
         "cells": cells(), "plug": {"on": False, "w": 0.0}, "metrics": metrics()}
    n.update(kw)
    return n


def sup_(age=10.0, **kw):
    s = {"t": T - age, "checks": {"wifi": "ok", "board": "ok", "engine": "ok", "heartbeat": "ok", "old_watchdog": []},
         "signals": {"engine": ["green", "엔진 정상"], "plug_on": ["green", "이상 없음"], "board": ["green", "이상 없음"]},
         "slack": True, "disk_free_gb": 500.0, "paused": None, "engine": {"exit": None}}
    s.update(kw)
    return s


def os_(age=60.0, **kw):
    o = {"t": T - age, "power": {"ac": True, "battery_pct": 100, "has_battery": True},
         "update": {"reboot_pending": False, "pause": {"state": "ok", "text": "2026-11-14 까지 (34일 남음)", "days_left": 34.0}},
         "wifi": {"connected": True, "ssid": "FTG-3D93-5G", "signal_pct": 99, "rssi_dbm": None}}
    o.update(kw)
    return o


_DEFAULT = object()


def run(now=_DEFAULT, sup=_DEFAULT, osi=_DEFAULT, cycles=None, cfg=CFG, t=T):
    return H.compute(now_() if now is _DEFAULT else now, sup_() if sup is _DEFAULT else sup,
                     os_() if osi is _DEFAULT else osi, cycles, cfg, t)


def lane(h, key):
    return {l["key"]: l for l in h["lanes"]}[key]


def L(h, key):
    return lane(h, key)["light"]


def reasons(h, key):
    return [i["reason"] for i in lane(h, key)["issues"]]


# ---------- 전체 ----------

def test_all_green_when_healthy():
    h = run()
    assert [l["key"] for l in h["lanes"]] == list(H.LANE_KEYS)
    assert h["light"] == "green" and h["word"] == "정상" and h["reason"] == "모든 차선 정상"
    assert h["counts"] == {"yellow": 0, "red": 0, "unknown": 0} and h["supervisor"] == "감시자 정상"
    for l in h["lanes"]:
        assert l["light"] == "green" and l["word"] == "정상" and l["value"] and l["name"] and l["fmea"], l["key"]
    assert h["sets"][0] == {"id": 1, "label": "CLBY4B", "running": True, "light": "green", "word": "정상", "reason": "이상 없음"}
    assert h["beat_age"] == 10.0 and h["supervisor_age"] == 10.0 and h["stale_after_s"] == HEALTH["cloud_board_stale_s"]


def test_worst_light_unknown_weighs_like_red():
    assert H.worst(["green", "yellow"]) == "yellow"
    assert H.worst(["yellow", "unknown"]) == "unknown"
    assert H.worst(["unknown", "red", "yellow"]) == "red"
    assert H.worst([]) == "green"


def test_band_reason_counts_and_worst_first():
    h = run(sup=sup_(disk_free_gb=18.2), now=now_(metrics=metrics(dock_power_fail=1)))
    assert h["light"] == "red" and h["counts"] == {"yellow": 1, "red": 1, "unknown": 0}
    assert h["reason"].startswith("준비 1건 · 조치 1건 — Dock 이 전력을 끌어 쓰지 않음")
    assert "디스크 여유 18.2 GB" in h["reason"]


def test_kst_is_korean_time_regardless_of_pc_timezone():
    assert H.kst(T) == "17:00 KST" and H.kst(T, day=True) == "01-15 17:00 KST" and H.kst(None) == "-"


def test_thresholds_partial_override_keeps_defaults():
    cfg = Config(health={"wifi_warn_dbm": -45})
    th = H.thresholds(cfg)
    assert th["wifi_warn_dbm"] == -45 and th["relay_life"] == HEALTH["relay_life"]
    assert L(run(cfg=cfg), "wireless") == "yellow"               # 99 % ≈ −50.5 dBm 가 −45 아래


# ---------- 1 전원 · OS ----------

def test_power_unknown_without_or_with_stale_osinfo():
    assert L(run(osi=None), "power") == "unknown"
    assert L(run(osi=os_(age=1800)), "power") == "green"
    assert L(run(osi=os_(age=1801)), "power") == "unknown"


def test_power_reboot_pending_and_pause():
    assert L(run(osi=os_(update={"reboot_pending": True, "pause": {"state": "ok"}})), "power") == "yellow"
    h = run(osi=os_(update={"pause": {"state": "warn", "text": "곧 만료", "days_left": 3.0}}))
    assert L(h, "power") == "yellow" and "7일 안에 만료" in lane(h, "power")["reason"]
    assert L(run(osi=os_(update={"pause": {"state": "bad", "text": "만료됨", "days_left": -1}})), "power") == "red"


@pytest.mark.parametrize("pct,want", [(100, "yellow"), (20, "yellow"), (19, "red"), (None, "yellow")])
def test_power_battery(pct, want):
    assert L(run(osi=os_(power={"ac": False, "battery_pct": pct, "has_battery": True})), "power") == want


# ---------- 2 프로그램 ----------

@pytest.mark.parametrize("phase,age,want", [
    ("DISCHARGE", 60, None), ("DISCHARGE", 61, "yellow"), ("DISCHARGE", 300, "yellow"), ("DISCHARGE", 301, "red"),
    ("EXTRACT", 300, None), ("EXTRACT", 301, "yellow"), ("EXTRACT", 1200, "yellow"), ("EXTRACT", 1201, "red"),
])
def test_program_heartbeat_without_supervisor_uses_same_limits(phase, age, want):
    """감시자 판정이 없으면 같은 문턱(supervisor.warn_limit · stale_limit)으로 심박을 직접 본다."""
    h = run(now=now_(age=age, phase=phase), sup=None)
    beat = [i["light"] for i in lane(h, "program")["issues"] if "심박" in i["reason"]]
    assert (beat[0] if beat else None) == want
    assert "감시자 없음" in reasons(h, "program")


def test_program_follows_supervisor_signal():
    h = run(sup=sup_(signals={"engine": ["yellow", "엔진을 되살림 (1시간에 1번째)"]}))
    assert L(h, "program") == "yellow" and lane(h, "program")["reason"] == "엔진을 되살림 (1시간에 1번째)"
    h = run(sup=sup_(signals={"engine": ["red", "1시간에 3번 되살렸는데 또 멈췄다"]}))
    assert L(h, "program") == "red" and "사람" in lane(h, "program")["ai"]


def test_program_hold_is_yellow_on_screen_but_not_given_to_alerter():
    h = run(now=now_(age=400, phase="CHARGE"), sup=sup_(signals={"engine": ["hold", "엔진 심박이 멈춤 — 지켜보는 중"]}))
    p = lane(h, "program")
    assert p["light"] == "yellow" and p["hold"] is True
    lights = H.alert_lights(h)
    assert "program" not in lights and not any(k in lights for k in H.ENGINE_ONLY)    # 엔진만 아는 차선도 hold
    assert lights["power"] == ("green", "이상 없음")


@pytest.mark.parametrize("age,want", [(180, False), (181, True)])
def test_program_supervisor_missing_after_three_minutes(age, want):
    h = run(sup=sup_(age=age))
    assert ("감시자 없음" in reasons(h, "program")) is want
    assert h["supervisor"] == ("감시자 정상" if not want else "감시자 소식 3분 전")


def test_program_supervisor_paused_and_config_error():
    h = run(sup=sup_(paused="코드 교체", signals={}))
    assert L(h, "program") == "yellow" and h["supervisor"] == "감시자 일시 중지"
    assert any("일시 중지" in r for r in reasons(h, "program"))
    h = run(sup=sup_(checks={"config": "error"}, why="설정을 읽지 못해 아무것도 하지 않는다: KeyError"))
    assert L(h, "program") == "unknown"


@pytest.mark.parametrize("phase,hours,want", [
    ("DISCHARGE", 7.5, "green"), ("DISCHARGE", 7.5 + 1 / 3600, "yellow"),      # 기대 5시간 × 1.5
    ("CHARGE", 6.0, "green"), ("CHARGE", 6.0 + 1 / 3600, "yellow"),           # 기대 charge_timeout_h(4) × 1.5
    ("EXTRACT", 9.0, "green"),                                                 # 기대 시간이 없는 단계는 보지 않는다
])
def test_program_phase_dwell(phase, hours, want):
    assert L(run(now=now_(phase=phase, since=hours * 3600)), "program") == want


@pytest.mark.parametrize("n,want", [(2, "green"), (3, "yellow")])
def test_program_events_per_hour(n, want):
    assert L(run(now=now_(metrics=metrics(events_1h=n))), "program") == want


def test_metrics_error_is_one_yellow_and_other_lanes_survive():
    h = run(now=now_(metrics={"error": "ZeroDivisionError: x"}))
    assert L(h, "program") == "yellow" and "지표를 모으지 못함" in lane(h, "program")["reason"]
    assert L(h, "disk") == "green"                                         # 감시자가 잰 디스크는 그대로


# ---------- 3 무선 · LiveHub ----------

def test_wifi_dbm_from_percent_or_rssi():
    assert H.wifi_dbm({"signal_pct": 60}) == -70 and H.wifi_dbm({"signal_pct": 100}) == -50
    assert H.wifi_dbm({"signal_pct": 60, "rssi_dbm": -80}) == -80 and H.wifi_dbm(None) is None


@pytest.mark.parametrize("wifi,want", [({"signal_pct": 60}, "green"), ({"signal_pct": 59}, "yellow"),
                                       ({"signal_pct": 99, "rssi_dbm": -70}, "green"), ({"signal_pct": 99, "rssi_dbm": -71}, "yellow")])
def test_wireless_signal_threshold(wifi, want):
    assert L(run(osi=os_(wifi={"connected": True, "ssid": "FTG", **wifi})), "wireless") == want


@pytest.mark.parametrize("hub,want", [("ok", "green"), ("reconnected", "yellow"), ("down", "red"), ("reconnect_failed", "red")])
def test_wireless_livehub(hub, want):
    s = sup_()
    s["checks"] = {**s["checks"], "wifi": hub}
    assert L(run(sup=s), "wireless") == want


@pytest.mark.parametrize("kw,want", [({"reconnects_24h": 1}, "green"), ({"reconnects_24h": 2}, "yellow"),
                                     ({"ip_changes_cycle": 2}, "green"), ({"ip_changes_cycle": 3}, "yellow")])
def test_wireless_reconnects_and_ip_changes(kw, want):
    assert L(run(now=now_(metrics=metrics(**kw))), "wireless") == want


# ---------- 4 플러그 ----------

@pytest.mark.parametrize("plug,want", [({"retries_1h": 0}, "green"), ({"retries_1h": 1}, "yellow"),
                                       ({"last_call_s": 3.0}, "green"), ({"last_call_s": 3.1}, "yellow"),
                                       ({"toggles_total": 20999}, "green"), ({"toggles_total": 21000}, "yellow"),
                                       ({"toggles_total": 26999}, "yellow"), ({"toggles_total": 27000}, "red")])
def test_plug_metrics(plug, want):
    p = {"retries_1h": 0, "last_call_s": 1.0, "toggles_total": 100, **plug}
    assert L(run(now=now_(metrics=metrics(plug=p))), "plug") == want


def test_plug_recovery_and_dock_power_and_supervisor_signal():
    assert L(run(now=now_(metrics=metrics(recover_cycle=1))), "plug") == "yellow"
    assert L(run(now=now_(metrics=metrics(dock_power_fail=1))), "plug") == "red"
    assert L(run(sup=sup_(signals={"plug_on": ["red", "플러그를 3번 연속 켜지 못함"]})), "plug") == "red"
    assert L(run(sup=sup_(signals={"plug_on": ["yellow", "엔진이 플러그를 켜지 못하고 끝나 감시자가 켰다"]})), "plug") == "yellow"
    assert "가안" in lane(run(), "plug")["value"]                      # 릴레이 수명은 가안이라고 보인다


# ---------- 5 Dock · 셀 ----------

@pytest.mark.parametrize("pct,want", [(79.9, "green"), (80.0, "yellow"), (94.9, "yellow"), (95.0, "red")])
def test_dock_cell_storage(pct, want):
    cs = {"max_pct": pct, "max_serial": 11740, "est_full_at": T + 3600}
    assert L(run(now=now_(metrics=metrics(cell_storage=cs))), "dock") == want


def test_dock_not_charging_and_empty_cell():
    assert L(run(now=now_(metrics=metrics(not_charging=[11740]))), "dock") == "yellow"
    c = cells(); c[3]["battery"] = 1
    assert L(run(now=now_(cells=c)), "dock") == "green"
    c[3]["battery"] = 0
    h = run(now=now_(cells=c))
    assert L(h, "dock") == "red" and "세트 1 셀 11736 잔량 0 %" in h["reason"]


@pytest.mark.parametrize("last,want", [(47.9, "green"), (48.0, "yellow")])           # 직전 평균 40분 × 1.2 = 48
def test_dock_charge_time_trend(last, want):
    rows = [{"charge_min": "40"}, {"charge_min": "40"}, {"charge_min": "40"}, {"charge_min": str(last)}]
    assert L(run(cycles=rows), "dock") == want


def test_charge_trend_needs_enough_rows_and_skips_manual():
    assert H.charge_trend([{"charge_min": "40"}] * 3, 3) is None
    rows = [{"charge_min": "40"}, {"charge_min": "5", "note": "manual_stop_charge"}, {"charge_min": "40"},
            {"charge_min": "40"}, {"charge_min": "44"}]
    assert H.charge_trend(rows, 3) == (44.0, 40.0)


# ---------- 6 셀 수신 ----------

def test_live_gap_boundary_and_all_lost():
    c = cells(); c[0]["age"] = 15.0
    assert L(run(now=now_(cells=c)), "live") == "green"
    c[0]["age"] = 15.1
    h = run(now=now_(cells=c))
    assert L(h, "live") == "yellow" and "세트 1 끊김 1대" in h["reason"]
    assert L(run(now=now_(cells=cells(age=16))), "live") == "red"
    assert L(run(now=now_(cells=[])), "live") == "red"


@pytest.mark.parametrize("kw,want", [({"live_gaps_1h": 2}, "green"), ({"live_gaps_1h": 3}, "yellow"), ({"waiting": [11740]}, "yellow")])
def test_live_gaps_and_waiting(kw, want):
    c = cells()
    if kw.get("waiting"):
        c[7]["waiting"] = True
    assert L(run(now=now_(cells=c, metrics=metrics(**kw))), "live") == want


def test_engine_stale_makes_engine_only_lanes_unknown_and_held():
    """감시자가 되살리는 중(노랑)이면 그 사건은 노랑 1건이 설계다 — 프로그램 차선은 노랑, 엔진 차선만 미확인(hold)."""
    h = run(now=now_(age=4000), sup=sup_(signals={"engine": ["yellow", "엔진을 되살림 (1시간에 1번째)"],
                                                  "plug_on": ["green", "이상 없음"], "board": ["green", "이상 없음"]}))
    for k in H.ENGINE_ONLY:
        assert L(h, k) == "unknown" and lane(h, k)["hold"] is True
    assert L(h, "program") == "yellow" and L(h, "plug") == "green" and L(h, "disk") == "green"
    assert h["light"] == "unknown" and h["counts"]["unknown"] == 3
    assert h["reason"].count("엔진 기록이 멈춰 셀 쪽 차선 미확인") == 1          # 띠에는 한 줄로 모인다
    assert H.alert_lights(h)["program"] == ("yellow", "엔진을 되살림 (1시간에 1번째)")
    assert h["todo"]["key"] == "program"                                          # hold 차선이 아니라 원인 차선의 할 일


def test_engine_silent_while_supervisor_is_not_handling_raises_program_to_unknown():
    """QA 5 — 엔진 차선이 미확인인데 감시자가 그 일을 맡고 있지 않으면(판정이 '정상'이거나 없음) 누를 곳이 없었다.
    원인 차선 '프로그램'이 빨강과 같은 무게(미확인)로 올라가 '확인'을 받는다."""
    h = run(now=now_(age=4000))                                                    # 감시자 판정은 '엔진 정상'(1분 전 것)
    p = lane(h, "program")
    assert p["light"] == "unknown" and p["hold"] is False and "엔진 정상" in p["ai"]
    assert h["counts"]["unknown"] == 4 and h["reason"].count("엔진 기록이 멈춰 셀 쪽 차선 미확인") == 1
    assert H.alert_lights(h)["program"] == ("unknown", "엔진 기록이 멈춰 셀 쪽 차선을 볼 수 없음")
    assert not any(k in H.alert_lights(h) for k in H.ENGINE_ONLY)
    assert h["todo"]["key"] == "program" and "제어 PC" in h["todo"]["human"]
    h = run(now=now_(age=4000), sup=None)                                         # 감시자가 없으면 그대로 빨강
    assert L(h, "program") == "red" and "되살릴 감시자 판정이 없다" in lane(h, "program")["reason"]
    h = run(now=now_(age=400, phase="CHARGE"), sup=sup_(signals={"engine": ["hold", "엔진 심박이 멈춤 — 지켜보는 중"]}))
    assert L(h, "program") == "yellow" and lane(h, "program")["hold"] is True      # 감시자가 한 번 더 보는 첫 점검은 그대로


def test_engine_ended_is_quiet():
    h = run(now=now_(age=4000, phase="DONE"))
    assert all(l["light"] == "green" for l in h["lanes"]) and "엔진 끝남(완료)" in lane(h, "live")["value"]


def test_interrupted_engine_with_plug_off_is_yellow_not_quiet():
    """F14 — 사람이 Ctrl+C 로 멈춘 엔진은 감시자가 되살리지 않는다. 플러그가 꺼진 채면 셀이 방전 중 — '사람 조작' 차선이 준비.
    세트 차선이라 세트 1 의 불도 준비가 된다(시험실 카드가 '정상'으로 남지 않게)."""
    off = now_(age=4000, phase="DISCHARGE", plug={"on": False, "w": 0.3})
    h = run(now=off, sup=sup_(engine={"exit": "interrupted"}))
    hu = lane(h, "human")
    assert hu["light"] == "yellow" and hu["reason"] == H.INTERRUPTED and "Tapo" in hu["human"]
    assert "Ctrl+C" in lane(h, "program")["value"] and L(h, "program") == "green" and L(h, "live") == "green"
    assert "세트 1 엔진을 사람이 멈춤(Ctrl+C) · 플러그 꺼짐" in h["reason"] and h["sets"][0]["light"] == "yellow"
    on = now_(age=4000, phase="CHARGE", plug={"on": True, "w": 60.0})
    assert L(run(now=on, sup=sup_(engine={"exit": "interrupted"})), "human") == "green"
    unknown_plug = now_(age=4000, phase="DISCHARGE", plug={})                    # 플러그 상태를 모르면(옛 기록) 값 줄에만
    h = run(now=unknown_plug, sup=sup_(engine={"exit": "interrupted"}))
    assert L(h, "human") == "green" and "Ctrl+C" in lane(h, "human")["value"]
    fixed = sup_(engine={"exit": "interrupted"}, signals={"engine": ["green", "사람이 멈춤"], "board": ["green", "이상 없음"],
                                                          "plug_on": ["yellow", "엔진이 플러그를 켜지 못하고 끝나 감시자가 켰다"]})
    h = run(now=off, sup=fixed)
    assert L(h, "human") == "green" and L(h, "plug") == "yellow"              # 감시자가 켰으면 방전 중이 아니다


def test_interrupted_uses_supervisor_plug_guard_reading_first():
    """감시자의 플러그 지키기가 실제로 읽은 값이 엔진의 마지막 기록보다 먼저다. 꺼짐을 읽고 켰으면(action=plug_on) 방전 중이 아니다."""
    off = now_(age=4000, phase="DISCHARGE", plug={"on": False, "w": 0.3})
    on_now = now_(age=4000, phase="DISCHARGE", plug={"on": True, "w": 40.0})
    guard = {"active": True, "checked_t": T - 60, "on": False, "action": "plug_on_failed", "fail": 1}
    assert L(run(now=on_now, sup=sup_(engine={"exit": "interrupted"}, plug_guard=guard)), "human") == "yellow"
    guard = {**guard, "action": "plug_on", "fail": 0}
    assert L(run(now=off, sup=sup_(engine={"exit": "interrupted"}, plug_guard=guard)), "human") == "green"
    guard = {"active": True, "checked_t": T - 60, "on": True, "action": "ok"}
    assert L(run(now=off, sup=sup_(engine={"exit": "interrupted"}, plug_guard=guard)), "human") == "green"
    unread = {"active": True, "checked_t": None, "on": None, "action": None}                     # 아직 읽기 전 — 엔진의 기록으로
    assert L(run(now=off, sup=sup_(engine={"exit": "interrupted"}, plug_guard=unread)), "human") == "yellow"


def test_paused_supervisor_with_stopped_engine_says_so():
    """일시 중지 중 엔진이 멈추면 '되살릴 감시자 판정이 없다'가 아니라 사실대로 — 일시 중지 중이라 되살리지 않음."""
    h = run(now=now_(age=4000), sup=sup_(paused="코드 교체", signals={}))
    p = lane(h, "program")
    assert p["light"] == "red" and "감시자 일시 중지 중이라 되살리지 않음" in p["reason"] and "최대 2시간" in p["reason"]
    assert "data/supervisor_pause" in p["human"] and "되살릴 감시자 판정이 없다" not in p["reason"]


def test_config_warning_is_one_yellow_on_program():
    w = "감시자와 엔진의 설정이 다름: 플러그 MAC — 감시자 A · 엔진 B (엔진 것을 쓴다)"
    h = run(sup=sup_(config_warning=w))
    assert L(h, "program") == "yellow" and lane(h, "program")["reason"] == "감시자와 엔진의 설정이 다름" and w in h["reason"]


def test_unknown_cell_storage_is_not_red():
    """엔진은 아는 셀만 요약한다 — 모르면 null. 저장량은 '모름'이고 불을 올리지 않는다."""
    h = run(now=now_(metrics=metrics(cell_storage=None)))
    assert L(h, "dock") == "green" and "저장량 모름" in lane(h, "dock")["value"]
    h = run(now=now_(metrics=metrics(cell_storage={"max_pct": None, "max_serial": None, "est_full_at": None})))
    assert L(h, "dock") == "green"


def test_interrupted_seen_from_engine_json_without_supervisor_even_with_fresh_beat():
    """감시자가 없어도 결과판 서버가 engine.json 을 넘기면 안다. 끝난 시각이 마지막 심박 뒤면 심박이 신선해도 끝난 것이다."""
    n = now_(age=20, phase="DISCHARGE", plug={"on": False, "w": 0.3})
    h = H.compute(n, None, os_(), None, CFG, T, None, {"exit": "interrupted", "ended": T - 10, "plug_on": None})
    assert h["engine"] == "ended" and lane(h, "human")["reason"] == H.INTERRUPTED
    h = H.compute(n, None, os_(), None, CFG, T, None, {"exit": "interrupted", "ended": T - 3600})   # 옛 기록 — 그 뒤 새 심박
    assert h["engine"] == "live"


def test_never_ran_lanes_have_no_light_and_do_not_move_the_bench():
    """QA 2 — now.json 이 없으면 엔진에서만 재는 차선은 '정상'이 아니라 회색 '기록 없음'. 전체 불·알림에 들어가지 않는다."""
    h = run(now=None)
    for k in H.SET_LANES:
        assert L(h, k) == "none" and lane(h, k)["word"] == "기록 없음", k
    assert "아직 돈 적 없음" in lane(h, "program")["value"] and L(h, "program") == "green"
    assert h["light"] == "green" and h["counts"] == {"yellow": 0, "red": 0, "unknown": 0}
    assert h["reason"] == "정상 · 기록 없음 4개 차선 — 엔진이 아직 돈 적 없음"
    assert h["sets"][0]["light"] == "none" and h["sets"][0]["word"] == "기록 없음"
    assert not any(k in H.alert_lights(h) for k in H.SET_LANES)
    h = run(now=None, sup=sup_(disk_free_gb=3.0))
    assert h["light"] == "red" and h["reason"].startswith("준비 0건 · 조치 1건 · 기록 없음 4 — 디스크 여유")
    h = run(now=None, sup=sup_(signals={"plug_on": ["yellow", "엔진이 플러그를 켜지 못하고 끝나 감시자가 켰다"]}))
    assert L(h, "plug") == "yellow"                                             # 감시자가 본 것은 기록이 없어도 보인다


# ---------- 7 기록 · 디스크 ----------

@pytest.mark.parametrize("gb,want", [(20.0, "green"), (19.9, "yellow"), (5.0, "yellow"), (4.9, "red")])
def test_disk(gb, want):
    assert L(run(sup=sup_(disk_free_gb=gb)), "disk") == want


def test_disk_falls_back_to_engine_then_unknown():
    assert lane(run(sup=None), "disk")["value"] == "여유 300.0 GB"            # 감시자가 없으면 엔진이 잰 값
    assert L(run(now=now_(metrics=metrics(disk_free_gb=None)), sup=None), "disk") == "unknown"


# ---------- 8 클라우드 · 알림 ----------

@pytest.mark.parametrize("cloud,want", [({"fail_1h": 2}, "green"), ({"fail_1h": 3}, "yellow"),
                                        ({"consecutive_auth_fail": 2}, "green"), ({"consecutive_auth_fail": 3}, "red"),
                                        ({"enabled": False}, "yellow")])
def test_cloud(cloud, want):
    c = {**metrics()["cloud"], **cloud}
    assert L(run(now=now_(metrics=metrics(cloud=c))), "cloud") == want


def test_slack_not_connected_is_yellow():
    h = run(sup=sup_(slack=False))
    assert L(h, "cloud") == "yellow" and "remote_setup" in lane(h, "cloud")["human"]


# ---------- 9 사람 조작 ----------

@pytest.mark.parametrize("n,want", [(0, "green"), (1, "yellow")])
def test_human_manual_plug(n, want):
    h = run(now=now_(metrics=metrics(manual_plug_1h=n, manual_plug_last=T - 600 if n else None)))
    assert L(h, "human") == want
    if n:
        assert "16:50 KST" in h["reason"] and "누가·왜" in lane(h, "human")["human"]


# ---------- 세트 · 확인 · 알림 키 ----------

def test_set_light_is_worst_of_set_lanes_and_unrun_sets_have_none():
    cfg = Config(sets=[{"id": 1, "plug_mac": "20:E1:5D:E6:9C:77", "serials": "11733-11756"},
                       {"id": 2, "plug_mac": "AA:BB:CC:DD:EE:FF", "serials": "11594-11617"}])
    h = run(cfg=cfg, sup=sup_(disk_free_gb=10), now=now_(metrics=metrics(not_charging=[11740])))
    s1, s2 = h["sets"]
    assert s1["light"] == "yellow" and "Dock·셀" in s1["reason"] and "디스크" not in s1["reason"]   # 디스크는 시험대 전체 차선
    assert s2 == {"id": 2, "label": "", "running": False, "light": None, "word": "대기",
                  "reason": "등록됨 · 운전 대기 (다중 세트 기능 적용 뒤 운전)"}       # 불 없음 = 화면의 회색 (세트 등록 단계)


def test_mark_acks_only_after_the_light_started():
    h = run(sup=sup_(disk_free_gb=1.0))
    H.mark_acks(h, {"disk": T + 5, "power": T + 5}, {"disk": {"since": T}})
    assert lane(h, "disk")["acked_at"] == T + 5 and lane(h, "power")["acked_at"] is None      # 초록 차선에는 붙지 않는다
    H.mark_acks(h, {"disk": T - 5}, {"disk": {"since": T}})
    assert lane(h, "disk")["acked_at"] is None                                                # 이 빨강이 시작되기 전의 확인


def test_ack_is_bound_to_the_cause_even_without_supervisor():
    """QA 3 — 확인은 그 원인(이유·불)에 묶인다. 감시자가 없어 since 를 몰라도 새 원인이면 다시 '확인'."""
    h = run(sup=sup_(disk_free_gb=1.0))
    reason = lane(h, "disk")["reason"]
    acks = H.ack_record({}, "disk", reason, "red", T + 5)
    assert acks["disk"] == T + 5 and acks[H.ACK_REASONS]["disk"]["reason"] == reason      # 알림기가 읽는 옛 꼴(키: 시각)은 그대로
    H.mark_acks(h, acks, None)
    assert lane(h, "disk")["acked_at"] == T + 5 and lane(h, "disk")["ack_old"] is False
    h2 = run(sup=sup_(disk_free_gb=1.0), now=now_(metrics=metrics(dock_power_fail=1)))
    h2["lanes"] = [dict(l, reason="다른 원인") if l["key"] == "disk" else l for l in h2["lanes"]]
    H.mark_acks(h2, acks, None)
    assert lane(h2, "disk")["acked_at"] is None and lane(h2, "disk")["ack_old"] is True     # 새 원인 — 다시 확인
    H.mark_acks(h, {"disk": T + 5}, None)
    assert lane(h, "disk")["acked_at"] is None                                               # 원인 없이 남은 옛 확인은 치지 않는다


def test_ack_with_supervisor_since_and_cause_both_hold():
    h = run(sup=sup_(disk_free_gb=1.0))
    acks = H.ack_record({}, "disk", lane(h, "disk")["reason"], "red", T + 5)
    H.mark_acks(h, acks, {"disk": {"since": T + 10}})                                        # 같은 이유로 다시 켜진 새 빨강
    assert lane(h, "disk")["acked_at"] is None and lane(h, "disk")["ack_old"] is True


def test_todo_is_the_human_line_of_the_worst_lane():
    h = run(sup=sup_(disk_free_gb=18.2), now=now_(metrics=metrics(dock_power_fail=1)))
    assert h["todo"] == {"key": "plug", "name": "플러그", "light": "red", "human": "Dock 전원 어댑터·케이블 확인"}
    assert run()["todo"] is None


def test_band_items_are_clipped_and_ordered_worst_first():
    long = "결과판 서버가 응답하지 않음 — " + "다시 띄우기를 시도했지만 같은 오류가 반복되었다 " * 6
    h = run(sup=sup_(signals={"engine": ["green", "엔진 정상"], "board": ["red", long]}, disk_free_gb=18.0))
    assert h["reason_head"] == "준비 1건 · 조치 1건"
    first = h["reason_items"][0]
    assert len(first) == H.SHORT_MAX and first.endswith("…") and h["reason_items"][1] == "디스크 여유 18.0 GB"
    assert lane(h, "program")["reason"] == long                                               # 전문은 차선 카드에 그대로


def test_lane_cells_name_the_serials():
    """QA 10 — 빨강·노랑 차선 카드가 어느 셀인지 보이게 시리얼을 싣는다."""
    c = cells(); c[7]["battery"] = 0
    assert lane(run(now=now_(cells=c)), "dock")["cells"] == [11740]
    c = cells(); c[3]["age"] = 40.0; c[4]["age"] = 40.0
    h = run(now=now_(cells=c[:-1]))                                                           # 11756 은 한 번도 안 들림
    lv = lane(h, "live")
    assert lv["cells"] == [11736, 11737, 11756] and "세트 1 끊김 3대 (11736, 11737, 11756)" in h["reason"]


def test_cell_storage_full_cell_with_lost_live_says_the_engine_cannot_delete():
    """F15 — 지우기 전 신원 확인에 라이브가 필요하다. 라이브가 끊긴 가득 찬 셀은 엔진이 지우지 않는다."""
    cs = {"max_pct": 96.0, "max_serial": 11740, "est_full_at": T + 3600}
    h = run(now=now_(metrics=metrics(cell_storage=cs)))
    assert "다음 추출에서" in lane(h, "dock")["ai"]
    c = cells(); c[7]["age"] = 60.0
    d = lane(run(now=now_(cells=c, metrics=metrics(cell_storage=cs))), "dock")
    assert d["light"] == "red" and d["ai"] == "라이브가 끊겨 엔진이 자동으로 지울 수 없음 — 현장에서 추출·삭제"
    assert "현장에서" in d["human"] and d["reason"] == lane(h, "dock")["reason"]              # 이유(알림 키)는 같은 원인이라 그대로


def test_cloud_auth_fail_reason_and_band_use_the_same_words():
    """QA 15 — 띠 '키 거부 4회' 와 차선 '3회 연속' 이 어긋나 보이던 것: 같은 말, 이유는 문턱 · 띠·값은 지금 횟수."""
    c = {**metrics()["cloud"], "consecutive_auth_fail": 4}
    h = run(now=now_(metrics=metrics(cloud=c)))
    cl = lane(h, "cloud")
    assert cl["reason"] == "클라우드 키 연속 거부 3회 이상" and "키 연속 거부 4회" in cl["value"]
    assert "클라우드 키 연속 거부 4회" in h["reason"]


def test_alert_reasons_are_stable_while_numbers_change():
    """같은 원인이면 이유 글이 같아야 알림기가 다시 보내지 않는다 — 숫자는 값·짧은 글에만."""
    a = run(sup=sup_(disk_free_gb=18.0))
    b = run(sup=sup_(disk_free_gb=12.5))
    assert H.alert_lights(a)["disk"] == H.alert_lights(b)["disk"] == ("yellow", "디스크 여유 20 GB 미만")
    assert lane(a, "disk")["value"] != lane(b, "disk")["value"]


# ---------- 운영체제 정보 (osinfo) ----------

NETSH_KO = """
시스템에 1 인터페이스가 있습니다.

    이름                   : Wi-Fi
    설명                   : Intel(R) Wireless-AC 9560
    상태                   : 연결됨
    SSID                   : FTG-3D93-5G
    BSSID                  : 78:22:88:a5:3d:96
    신호                   : 99%
    프로필                : FTG-3D93-5G
"""
NETSH_EN = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    State                  : connected
    SSID                   : FTG-3D93-5G
    BSSID                  : 78:22:88:a5:3d:96
    Signal                 : 58%
    Rssi                   : -71
"""


def test_parse_netsh_korean_and_english():
    assert osinfo.parse_netsh(NETSH_KO) == {"connected": True, "state": "연결됨", "ssid": "FTG-3D93-5G",
                                            "signal_pct": 99, "rssi_dbm": None}
    en = osinfo.parse_netsh(NETSH_EN)
    assert en["connected"] is True and en["signal_pct"] == 58 and en["rssi_dbm"] == -71
    assert osinfo.parse_netsh("Wireless AutoConfig Service (wlansvc) is not running.")["connected"] is None


def test_parse_power():
    assert osinfo.parse_power(1, 1, 100) == {"ac": True, "battery_pct": 100, "has_battery": True}
    assert osinfo.parse_power(0, 2, 15) == {"ac": False, "battery_pct": 15, "has_battery": True}
    assert osinfo.parse_power(1, 128, 255) == {"ac": True, "battery_pct": None, "has_battery": False}
    assert osinfo.parse_power(255, 255, 255) == {"ac": None, "battery_pct": None, "has_battery": None}


def test_update_uses_check_env_verdicts():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    vals = {"NoAutoRebootWithLoggedOnUsers": 1, "AUOptions": 4, "PauseUpdatesExpiryTime": "2026-11-13T15:00:00Z",
            "PausedQualityStatus": 1}
    u = osinfo.update(now, reg=lambda path, name: vals.get(name), reg_has=lambda path: True)
    assert u["reboot_pending"] is True and u["pause"]["state"] == "warn" and u["pause"]["days_left"] == 3.62
    vals["PauseUpdatesExpiryTime"] = "2026-11-01T00:00:00Z"
    u = osinfo.update(now, reg=lambda path, name: vals.get(name), reg_has=lambda path: False)
    assert u["pause"]["state"] == "bad" and u["reboot_pending"] is False
    vals["PauseUpdatesExpiryTime"] = "2026-12-31T00:00:00Z"
    assert osinfo.update(now, reg=lambda path, name: vals.get(name), reg_has=lambda path: False)["pause"]["state"] == "ok"


def test_collect_never_raises(monkeypatch):
    monkeypatch.setattr(osinfo, "power", lambda: 1 / 0)
    monkeypatch.setattr(osinfo, "wifi", lambda: {"connected": True})
    monkeypatch.setattr(osinfo, "update", lambda warn_days=7: {"reboot_pending": False})
    d = osinfo.collect(now_t=T)
    assert d["t"] == T and "ZeroDivisionError" in d["power"]["error"] and d["wifi"] == {"connected": True}
    h = run(osi=d)
    assert L(h, "power") == "green" and "전원 모름" in lane(h, "power")["value"]     # 못 읽은 칸은 판정하지 않고 '모름'
    d["update"] = {"error": "OSError: x"}
    assert L(run(osi=d), "power") == "unknown"                                         # 둘 다 못 읽으면 판정할 수 없다
