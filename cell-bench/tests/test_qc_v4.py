"""v4 판정 칸(motionbench/qc_v4.py)과 판정 도구의 작은 부분(tools/protocol_v4.py) — 장비 · 파일 없이 돈다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from motionbench import qc_v4 as V  # noqa: E402
import protocol_v4 as P  # noqa: E402


def test_review_flags_only_the_outlier_and_holds_missing():
    vals = {s: 48.0 for s in range(1, 11)} | {11: 40.0, 12: None}
    it = V.review(vals, "cn0_sim", "라이브")
    assert it[11]["verdict"] == "flag" and abs(it[11]["z"]) >= V.Z_THR
    assert all(it[s]["verdict"] == "pass" for s in range(1, 11))
    assert it[12]["verdict"] == "hold" and it[12]["basis"] == "재료 없음"


def test_review_floor_keeps_tiny_spread_from_flagging():
    # 모두 0.99 근처 — 바닥(0.01)이 없으면 0.005 차이도 벗어남이 된다
    vals = {1: 0.985, 2: 0.986, 3: 0.987, 4: 0.986, 5: 0.990}
    assert all(x["verdict"] == "pass" for x in V.review(vals, "gps_move").values())


def test_review_key_reads_numbers_out_of_a_bundle():
    vals = {s: {"v_ratio": {"300": 0.99}} for s in range(8)} | {8: {"v_ratio": {"300": 0.5}}}
    it = V.review(vals, "gps_move", key=lambda v: v["v_ratio"]["300"])
    assert it[8]["verdict"] == "flag" and it[8]["value"] == {"v_ratio": {"300": 0.5}}


def test_pass_fail_and_overall():
    it = V.pass_fail({1: 0.0, 2: 2.5, 3: None}, "rx")
    assert [it[s]["verdict"] for s in (1, 2, 3)] == ["pass", "fail", "hold"]
    flag = V.item(40, "dBHz", "flag", -4.0)
    assert V.overall({"rx": it[1], "cn0_sim": flag}) == {"overall": "pass", "flags": ["cn0_sim"], "reasons": []}
    v = V.overall({"rx": it[2]})
    assert v["overall"] == "fail" and "rx" in v["reasons"][0]
    assert V.overall({"rx": it[3]})["overall"] == "hold"


def test_build_and_group():
    items = {10677: {"rx": V.item(0.0, "%", "pass"), "lock": V.item(12.0, "s", "pass")},
             10679: {"rx": V.item(0.5, "%", "pass"), "lock": V.item(30.0, "s", "flag", 5.0)}}
    cm = V.build(items, {10677: {"col": 1, "row": 1}})
    assert set(cm) == {"10677", "10679"} and cm["10677"]["dock"] == {"col": 1, "row": 1} and cm["10679"]["dock"] is None
    assert cm["10679"]["verdict"]["flags"] == ["lock"]
    assert V.group(cm, "lock") == {"med": 21.0, "n": 2}


def test_best_lag_finds_the_shift():
    ref = lambda x: 300.0 if 10 <= x <= 20 else 0.0
    xs = [i / 10 for i in range(0, 300)]
    cs = [ref(x - 0.4) for x in xs]                  # 셀이 0.4초 늦게 따라온다
    lag, err = P.best_lag(xs, cs, ref)
    assert lag == 0.4 and err == 0


def test_clock_offset_uses_the_shortest_delays():
    class G:
        def __init__(self, utc):
            self.utc, self.fix, self.lat_deg, self.lon_deg = utc, "A", 37.5, 127.0
    # 수신 시각 − GPS 시각 = 100 + 지연(0.05 ~ 0.5) — 가장 짧은 쪽(약 100.05)을 고른다
    pk = [(1000.0 + i + 100 + (0.05 if i % 3 else 0.5), G(1000.0 + i)) for i in range(60)]
    off = P.clock_offset({1: pk, 2: pk})
    assert abs(off - 100.05) < 1e-6
