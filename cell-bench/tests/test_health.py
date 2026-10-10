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
    h = run(now=now_(age=4000), sup=sup_(signals={"engine": ["yellow", "엔진을 되살림 (1시간에 1번째)"],
                                                  "plug_on": ["green", "이상 없음"], "board": ["green", "이상 없음"]}))
    for k in H.ENGINE_ONLY:
        assert L(h, k) == "unknown" and lane(h, k)["hold"] is True
    assert L(h, "program") == "yellow" and L(h, "plug") == "green" and L(h, "disk") == "green"
    assert h["light"] == "unknown" and h["counts"]["unknown"] == 3
    assert h["reason"].count("엔진 기록이 멈춰 셀 쪽 차선 미확인") == 1          # 띠에는 한 줄로 모인다


def test_engine_ended_or_never_ran_is_quiet():
    h = run(now=now_(age=4000, phase="DONE"))
    assert all(l["light"] == "green" for l in h["lanes"]) and "엔진 끝남(완료)" in lane(h, "live")["value"]
    h = run(now=now_(age=4000, phase="CHARGE"), sup=sup_(engine={"exit": "interrupted"}))    # Ctrl+C — 사람이 멈췄다
    assert L(h, "live") == "green"
    h = run(now=None)
    assert all(l["light"] == "green" for l in h["lanes"]) and "아직 돈 적 없음" in lane(h, "program")["value"]


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
    assert s2 == {"id": 2, "label": "", "running": False, "light": None, "word": "운전 전", "reason": "등록됨 · 아직 운전하지 않음"}


def test_mark_acks_only_after_the_light_started():
    h = run(sup=sup_(disk_free_gb=1.0))
    H.mark_acks(h, {"disk": T + 5, "power": T + 5}, {"disk": {"since": T}})
    assert lane(h, "disk")["acked_at"] == T + 5 and lane(h, "power")["acked_at"] is None      # 초록 차선에는 붙지 않는다
    H.mark_acks(h, {"disk": T - 5}, {"disk": {"since": T}})
    assert lane(h, "disk")["acked_at"] is None                                                # 이 빨강이 시작되기 전의 확인


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
