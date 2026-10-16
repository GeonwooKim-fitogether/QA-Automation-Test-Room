"""심박 허브를 세트 단위로 · '전원만' 보드(허브 P…) · 제어 PC 의 USB 장치 수 (cellbench/hr.py · health.hr_summary). 장비 없이 돈다.

배경(2026-10-16): 결과판 '시험실 구성'을 세트마다 한 줄로 다시 그리면서 (1) 허브를 세트별로 묶고(세트 1 의 "A" 와 세트 2 의 "A" 는 다른 허브),
(2) 보드를 USB 충전기에만 꽂은 세트를 '전원만'으로 적을 수 있게 하고(짝 표 hub 가 P 로 시작), (3) 감시자가 USB 장치 수를 센다.
지금 TestPC 의 짝 표(hub "A" · "B")는 고치지 않고 세트 1 의 허브로 읽혀야 한다.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench import health as H
from cellbench import hr
from cellbench.config import HR, Config, validate

T = 1_800_000_000.0


def pairs(first, n, hub, board0, per_hub=None):
    out = {}
    for i in range(n):
        h = hub if per_hub is None else hub[i // per_hub]
        out[str(first + i)] = {"board": f"HRSIM-{board0 + i:04X}", "no": i + 1, "hub": h, "port": (i % (per_hub or n)) + 1}
    return out


def two_sets(p2):
    s1 = {"id": 1, "plug_mac": "20:E1:5D:E6:9C:77", "serials": "11733-11756", "hr_pairs": pairs(11733, 24, "AB", 1, 16)}
    s2 = {"id": 2, "plug_mac": "20:E1:5D:E6:97:5B", "serials": "11594-11617", "hr_pairs": p2}
    return Config(sets=[s1, s2])


def test_same_hub_name_in_two_sets_is_two_hubs_and_validates():
    cfg = two_sets(pairs(11594, 24, "AB", 0x100, 16))                  # 세트 2 도 허브 A · B 에 같은 포트 번호
    assert validate(cfg) == []                                         # 세트가 다르면 같은 포트 번호가 겹치지 않는다
    hubs = hr.hubs(cfg.sets, None, T, 180)
    assert list(hubs) == ["1A", "1B", "2A", "2B"]
    assert hubs["2A"]["set"] == 2 and hubs["2A"]["hub"] == "A" and hubs["2A"]["total"] == 16 and hubs["2B"]["total"] == 8
    assert {b["set"] for b in hubs["1A"]["boards"]} == {1}


def test_power_hub_is_accepted_not_read_and_grouped_per_set():
    cfg = two_sets(pairs(11594, 24, "P2", 0x100))
    assert validate(cfg) == []
    assert all(not n.startswith("HRSIM-01") for n in hr.watched_names(cfg.sets))   # 전원만 보드는 감시자가 열지 않는다
    assert len(hr.watched_names(cfg.sets)) == 24
    hubs = hr.hubs(cfg.sets, None, T, 180)
    assert list(hubs) == ["1A", "1B"]                                  # 전원만은 감시 허브가 아니다
    pw = hr.power(cfg.sets)
    assert list(pw) == ["2"] and pw["2"]["set"] == 2 and pw["2"]["hubs"] == ["P2"] and pw["2"]["total"] == 24
    assert pw["2"]["boards"][0] == {"no": 1, "port": 1, "board": "HRSIM-0100", "serial": 11594, "hub": "P2"}
    assert hr.is_power("P3") and hr.is_power(" p4") and not hr.is_power("A") and not hr.is_power("") and not hr.is_power(None)


def test_mixed_set_draws_both_watched_and_power():
    p = pairs(11594, 16, "A", 0x100)
    p.update(pairs(11610, 8, "P2", 0x200))
    cfg = two_sets(p)
    assert validate(cfg) == []
    assert "2A" in hr.hubs(cfg.sets, None, T, 180) and hr.power(cfg.sets)["2"]["total"] == 8


def test_judge_power_board_cause_is_not_read():
    pp = hr.pairs_of({"hr_pairs": pairs(11594, 2, "P2", 0x100)})
    cells = [{"serial": s, "hr": 0, "age": 0.3, "waiting": False, "up_s": 9000, "hr0_s": 400} for s in (11594, 11595)]
    j = hr.judge(pp, {"phase": "DISCHARGE", "cells": cells}, {"t": T, "boards": {}}, T, dict(HR), 15.0)
    assert [x["cause"] for x in j["lost"]] == ["power", "power"]
    assert j["lost"][0]["why"].startswith("보드 상태 안 읽음") and j["lost"][0]["conns"] is None


def test_power_lane_short_text_and_no_key_error():
    cfg = Config(sets=[{"id": 1, "plug_mac": "20:E1:5D:E6:9C:77", "serials": "11733-11756", "hr_pairs": pairs(11733, 24, "P1", 1)}])
    cells = [{"serial": 11733 + i, "hr": 0 if i < 3 else 70, "age": 0.3, "waiting": False, "up_s": 9000, "hr0_s": 400 if i < 3 else 0}
             for i in range(24)]
    now = {"t": T, "beat": T, "phase": "DISCHARGE", "phase_since": T - 600, "cells": cells, "expected": 24, "metrics": {}}
    h = H.compute(now, {"t": T, "slack": True}, None, None, cfg, T,
                  sets_data={"running": [1], "nows": {}, "cycles": {}, "info": {}, "hr_boards": None})
    l = next(x for x in h["lanes"] if x["key"] == "hr")
    assert l["light"] == "red" and "보드 상태 안 읽음 3" in l["issues"][0]["short"] and "전원만 보드" in l["issues"][0]["human"]
    assert h["hr"]["hubs"] == {} and h["hr"]["power"]["1"]["total"] == 24


def test_board_watch_writes_usb_count_and_skips_ports_for_power_only(tmp_path):
    calls = []

    def scanner(names, prev, now, t):
        calls.append(sorted(names))
        return {"t": now, "boards": {}}

    only_p = lambda: Config(sets=[{"id": 1, "serials": "11733-11734", "hr_pairs": pairs(11733, 2, "P1", 1)}])
    w = hr.BoardWatch(tmp_path, only_p, scanner=scanner, clock=lambda: T, usb=lambda: 48)
    w.once()
    doc = json.loads((tmp_path / hr.BOARDS_FILE).read_text(encoding="utf-8"))
    assert calls == [] and doc == {"t": T, "boards": {}, "usb_devices": 48}       # 포트는 하나도 열지 않는다

    def boom():
        raise OSError("x")
    with_a = lambda: Config(sets=[{"id": 1, "serials": "11733-11734", "hr_pairs": pairs(11733, 2, "A", 1)}])
    hr.BoardWatch(tmp_path, with_a, scanner=scanner, clock=lambda: T, usb=boom).once()
    assert calls == [["HRSIM-0001", "HRSIM-0002"]]
    assert json.loads((tmp_path / hr.BOARDS_FILE).read_text(encoding="utf-8"))["usb_devices"] is None   # 못 세면 모름


def test_summary_carries_usb_count_and_limit_setting():
    cfg = Config(sets=[{"id": 1, "serials": "11733-11734", "hr_pairs": pairs(11733, 2, "A", 1)}], health={"usb_limit": 64})
    s = H.hr_summary({}, {}, cfg, 1, [1], {"t": T, "boards": {}, "usb_devices": 48}, T)
    assert s["usb_devices"] == 48 and s["usb_limit"] == 64 and list(s["hubs"]) == ["1A"]
    s = H.hr_summary({}, {}, Config(sets=[]), 1, [1], {"t": T, "boards": {}, "usb_devices": "x"}, T)
    assert s["usb_devices"] is None and s["usb_limit"] is None


def test_usb_count_on_this_pc_is_a_number_or_none():
    n = hr.usb_devices()
    assert n is None or (isinstance(n, int) and n >= 0)
