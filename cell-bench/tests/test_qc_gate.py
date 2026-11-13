"""단계별 거르기(motionbench/qc_gate.py) — 장비 · 셀 기록 없이 돈다. 2026-11-12 사용자 결정:
운영 순서 GNSS-STATIC → GNSS-MOVE → IMU-STATIC → IMU-MOVE → IMU-FW → GNSS-FW, 센서 시험에서 떨어진 셀은 펌웨어 시험에 넣지 않는다.
GNSS-STATIC 회차마다: 위치를 못 잡았거나 (세기 − 회차 효과 − 칸 기준값)이 −3 dB 보다 낮으면 재측정(다른 칸에서 한 번 더), 두 번 걸리면 불합격.
칸 기준값이 없는 조건에서는 같은 행 다른 셀 중앙값을 임시 기준으로 쓴다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from motionbench import qc_gate as G  # noqa: E402

COND = "감쇠18_행1,3"           # 칸 기준값 표가 있는 조건(1 · 3행 네 회차 실측)
TMP = "감쇠18_행2,4"            # 표가 없는 조건 — 임시 기준(같은 행 다른 셀 중앙값)


def _round(doc, vals: dict, source: str, slots: dict | None = None, cond: str = TMP, crit: dict | None = None):
    """vals = {셀: 신호 세기 또는 None(위치 못 잡음)}. 칸은 기본으로 1행 r 칸(셀 순서대로)."""
    slots = slots or {s: f"1-{i + 1}" for i, s in enumerate(vals)}
    cells = {s: {"slot": slots[s], "cn0": v, "gps_rx": 100.0 if v is not None else 10.0} for s, v in vals.items()}
    return G.gnss1_round(doc, cells, cond, source, crit)


def test_threshold_and_slot_baseline_come_from_the_criteria_file():
    crit = G.criteria("GNSS-STATIC")
    assert crit["cn0_drop_db"] == 3.0 and crit["hits_to_fail"] == 2 and crit["version"] == "0.1"
    assert G.criteria("IMU-STATIC")["gyro_bias_max_dps"] == 2.0 and (G.criteria("IMU-MOVE")["speed_ratio_lo"], G.criteria("IMU-MOVE")["speed_ratio_hi"]) == (0.85, 1.15)
    t = G.slot_table(crit, COND)
    assert len(t) == 12 and (t["1-3"], t["1-5"], t["3-1"], t["3-3"]) == (-4.6, 5.4, 4.4, -2.1)
    assert G.slot_table(crit, TMP) == {}


def test_slot_baseline_removes_the_slot_effect():
    """1 · 3행 12칸에 '회차 39.4 + 칸 기준값' 세기. 약한 칸(1행 3칸 −4.6)의 정상 셀은 안 걸리고, 보통 칸에서 3.5 dB 낮은 셀은 걸린다."""
    t = G.slot_table(G.criteria("GNSS-STATIC"), COND)
    slots = {i: k for i, k in enumerate(sorted(t), start=1)}
    vals = {i: 39.4 + t[k] for i, k in slots.items()}
    weak = next(i for i, k in slots.items() if k == "1-3")
    low = next(i for i, k in slots.items() if k == "3-4")
    vals[weak] -= 1.0
    vals[low] -= 3.5
    r = _round({}, vals, "r01", slots, COND)
    assert all(x["base_kind"] == "칸 기준" for x in r.values())
    assert not r[weak]["hit"] and r[weak]["status"] == G.PASS                   # 행 중앙값 기준이었으면 약 6.5 dB 낮아 걸렸다
    assert r[low]["hit"] and r[low]["status"] == G.RETEST and "회차 39.4 -0.7(칸 3-4)" in r[low]["why"]
    tmp = _round({}, vals, "r01", slots, TMP)                                   # 같은 값을 임시 기준으로 재면 약한 칸 셀이 걸린다
    assert tmp[weak]["hit"] and tmp[weak]["base_kind"] == "임시 기준"


def test_slot_missing_from_the_table_falls_back_to_temporary():
    crit = G.criteria("GNSS-STATIC") | {"slot_baseline": {"c": {"values": {"1-1": 0.0, "1-2": 0.0, "1-3": 0.0}}}}
    r = _round({}, {1: 40.0, 2: 40.0, 3: 40.0, 4: 40.0}, "r01", None, "c", crit)
    assert [r[i]["base_kind"] for i in (1, 2, 3, 4)] == ["칸 기준", "칸 기준", "칸 기준", "임시 기준"]


def test_temporary_row_median_without_a_table_and_marks_retest():
    doc = {}
    r = _round(doc, {1: 40.0, 2: 41.0, 3: 39.0, 4: 36.0, 5: 37.4, 6: None}, "GNSS-STATIC_20261112/r01")
    assert all(x["base_kind"] == "임시 기준" for x in r.values())
    assert r[4]["hit"] and r[4]["status"] == G.RETEST and "낮음" in r[4]["why"]   # 같은 행 다른 셀 중앙값 39.5 − 3 = 36.5 보다 낮다
    assert not r[5]["hit"] and r[5]["status"] == G.PASS
    assert r[6]["hit"] and "위치" in r[6]["why"]
    assert doc["cells"]["4"]["GNSS-STATIC"]["status"] == G.RETEST and doc["cells"]["4"]["GNSS-STATIC"]["rounds"][0]["cond"] == TMP


def test_exactly_3_db_lower_is_not_a_hit():
    r = _round({}, {1: 40.0, 2: 40.0, 3: 37.0}, "r01")
    assert not r[3]["hit"]                                                       # '3 dB 넘게' 낮아야 걸린다


def test_second_hit_in_another_slot_fails():
    doc = {}
    _round(doc, {9: 30.0, 7: 40.0, 8: 40.0}, "r01", {9: "1-1", 7: "1-2", 8: "1-3"})
    assert doc["cells"]["9"]["GNSS-STATIC"]["status"] == G.RETEST
    _round(doc, {9: 30.0, 1: 41.0, 2: 41.0}, "r02", {9: "1-2", 1: "1-1", 2: "1-3"})   # 다른 칸에서 다시 걸림
    assert doc["cells"]["9"]["GNSS-STATIC"]["status"] == G.FAIL
    _round(doc, {9: 41.0, 1: 41.0, 2: 41.0}, "r03", {9: "1-3", 1: "1-1", 2: "1-2"})   # 두 번 걸리면 뒤에 붙어도 불합격 그대로
    assert doc["cells"]["9"]["GNSS-STATIC"]["status"] == G.FAIL


def test_retest_that_passes_in_another_slot_becomes_pass():
    doc = {}
    _round(doc, {1: 40.0, 2: 40.0, 3: 30.0}, "r01")
    assert doc["cells"]["3"]["GNSS-STATIC"]["status"] == G.RETEST
    _round(doc, {3: 40.0, 1: 40.0, 2: 40.0}, "r02", {3: "1-1", 1: "1-2", 2: "1-3"})
    assert doc["cells"]["3"]["GNSS-STATIC"]["status"] == G.PASS


def test_same_slot_retest_is_flagged():
    doc = {}
    _round(doc, {1: 40.0, 2: 40.0, 3: 30.0}, "r01")
    r = _round(doc, {3: 30.0, 1: 40.0, 2: 40.0}, "r02", {3: "1-3", 1: "1-1", 2: "1-2"})
    assert r[3]["status"] == G.FAIL and "같은 칸" in r[3]["why"]


def test_rerunning_the_same_round_does_not_double_count():
    doc = {}
    for _ in range(3):
        _round(doc, {1: 40.0, 2: 40.0, 3: 30.0}, "r01")
    assert doc["cells"]["3"]["GNSS-STATIC"]["status"] == G.RETEST and len(doc["cells"]["3"]["GNSS-STATIC"]["rounds"]) == 1


def test_fit_recovers_slot_effects_from_accumulated_rounds():
    """같은 셀이 회차마다 다른 칸에 앉으면, 쌓인 회차 기록에서 칸 효과와 셀 효과를 가를 수 있다(칸 기준값 다시 맞추기)."""
    slot_eff = {"1-1": 3.0, "1-2": -3.0, "1-3": 1.0, "1-4": -1.0}
    cell_eff = {1: 1.0, 2: -1.0, 3: 0.5, 4: -0.5}
    order = sorted(slot_eff)
    doc = {}
    for k, level in enumerate((39.0, 39.5, 38.8, 39.2)):                       # 4회차 — 셀이 한 칸씩 돈다
        slots = {c: order[(i + k) % 4] for i, c in enumerate(cell_eff)}
        _round(doc, {c: level + slot_eff[slots[c]] + cell_eff[c] for c in cell_eff}, f"r{k + 1:02d}", slots, COND)
    obs = G.gnss1_obs(doc, COND, 50.0)
    assert len(obs) == 16 and G.gnss1_obs(doc, TMP, 50.0) == []
    rnd, slot, cell = G.fit_effects(obs)
    assert all(abs(slot[k] - v) < 0.05 for k, v in slot_eff.items())
    assert all(abs(cell[k] - v) < 0.05 for k, v in cell_eff.items())
    assert abs(rnd["r02"] - 39.5) < 0.05


def test_excluded_only_looks_at_earlier_tests():
    doc = {}
    G.record(doc, 1, "GNSS-STATIC", G.FAIL, "GNSS-STATIC_x/r02", "두 번")
    G.record(doc, 2, "IMU-STATIC", G.FAIL, "IMU-STATIC_x", "영점 9 °/s")
    G.record(doc, 3, "IMU-MOVE", G.PASS, "IMU-MOVE_x", "비 1.0")
    G.record(doc, 4, "GNSS-STATIC", G.RETEST, "GNSS-STATIC_x/r01", "한 번")
    G.record(doc, 5, "IMU-FW", G.FAIL, "x", "펌웨어")
    G.record(doc, 6, "GNSS-MOVE", G.FAIL, "GNSS-MOVE_x", "원값 속도 비 0.80")
    assert G.excluded(doc, "GNSS-STATIC") == {}
    assert set(G.excluded(doc, "GNSS-MOVE")) == {1}
    assert set(G.excluded(doc, "IMU-STATIC")) == {1, 6}                              # GNSS-MOVE 는 IMU-STATIC 앞 센서 시험
    assert set(G.excluded(doc, "IMU-FW")) == {1, 2, 6}                       # 재측정 · 합격은 빼지 않는다
    assert set(G.excluded(doc, "GNSS-FW")) == {1, 2, 5, 6}


def test_record_replaces_same_source_and_file_round_trips(tmp_path):
    doc = G.load(tmp_path / "없음.json")
    G.record(doc, 7, "IMU-STATIC", G.FAIL, "IMU-STATIC_20261112", "a")
    G.record(doc, 7, "IMU-STATIC", G.PASS, "IMU-STATIC_20261112", "b")                    # 판정을 다시 돌림
    assert doc["cells"]["7"]["IMU-STATIC"]["status"] == G.PASS and len(doc["cells"]["7"]["IMU-STATIC"]["history"]) == 1
    p = G.path("Y", tmp_path)
    G.save(p, doc)
    assert p.name == "qc_gate_Y.json" and G.load(p)["cells"]["7"]["IMU-STATIC"]["why"] == "b"


def test_judge_reads_excluded_cells_next_to_runs(tmp_path):
    import protocol_v4 as P
    rec = tmp_path / "IMU-FW_20261112"; (rec / "a50").mkdir(parents=True)
    (rec / "a50" / "runs.json").write_text("[]", encoding="utf-8")
    assert P.run_excluded(rec / "a50" / "runs.json") == {}
    (rec / "summary.json").write_text(json.dumps({"excluded": {"10683": "GNSS-STATIC 불합격"}}, ensure_ascii=False), encoding="utf-8")
    assert P.run_excluded(rec / "a50" / "runs.json") == {10683: "GNSS-STATIC 불합격"}


def test_old_test_ids_in_gate_file_are_read_as_current_ids(tmp_path):
    """2026-11-13 이름 바꾸기 전 거르기 파일(GNSS-1 · IMU-2 열쇠)을 지금 ID 로 읽고, 거르기도 그대로 된다."""
    p = tmp_path / "qc_gate_Y.json"
    p.write_text(json.dumps({"cells": {"10683": {"GNSS-1": {"status": G.FAIL, "date": "d", "source": "GNSS-1_20261112/r02"}},
                                       "10690": {"IMU-2": {"status": G.FAIL, "date": "d", "source": "IMU-2_20261112"}}}},
                            ensure_ascii=False), encoding="utf-8")
    doc = G.load(p)
    assert list(doc["cells"]["10683"]) == ["GNSS-STATIC"] and list(doc["cells"]["10690"]) == ["IMU-MOVE"]
    assert set(G.excluded(doc, "IMU-FW")) == {10683, 10690} and set(G.excluded(doc, "IMU-STATIC")) == {10683}
