"""단계별 거르기(motionbench/qc_gate.py) — 장비 · 셀 기록 없이 돈다. 2026-11-12 사용자 결정:
운영 순서 GNSS-STATIC → GNSS-MOVE → IMU-STATIC → IMU-MOVE → IMU-FW → GNSS-FW, 센서 시험에서 떨어진 셀은 펌웨어 시험에 넣지 않는다.
GNSS-STATIC 회차마다: 위치를 못 잡았거나 (세기 − 회차 효과 − 칸 기준값)이 −3 dB 보다 낮으면 재측정(다른 칸에서 한 번 더), 두 번 걸리면 불합격.
칸 기준값이 없는 조건에서는 같은 행 다른 셀 중앙값을 임시 기준으로 쓴다.
2026-11-25: 상태 넷(판정 불가 더함) · 뒤 시험에는 합격 셀만 · 위성 수는 살펴봄 · 항목별 걸림 · 두 기록 대조.
셀 번호 1 은 '셀 번호를 안 쓴 셀'(cell_models.NO_SERIAL)이라 보통 셀로 쓰지 않는다."""
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
    return G.gnss_static_round(doc, cells, cond, source, crit)


def test_threshold_and_slot_baseline_come_from_the_criteria_file():
    crit = G.criteria("GNSS-STATIC")
    assert crit["cn0_drop_db"] == 3.0 and crit["hits_to_fail"] == 2 and crit["version"] == "0.3"
    assert "sats_min_pct" not in crit and crit["sats_basis"]["withdrawn"].startswith("2026-11-25")    # 위성 수 비율은 살펴봄(기준 0.3)
    assert (crit["read_check"]["sats_max_diff"], crit["read_check"]["cn0_max_diff_db"], crit["read_check"]["fix_max_diff_pct"]) == (1.0, 2.0, 20.0)
    assert G.criteria("IMU-STATIC")["gyro_bias_max_dps"] == 2.0 and G.criteria("IMU-STATIC")["gravity_dev_max_mg"] == 50.0 and (G.criteria("IMU-MOVE")["speed_ratio_lo"], G.criteria("IMU-MOVE")["speed_ratio_hi"]) == (0.85, 1.15)
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
    r = _round(doc, {11: 40.0, 12: 41.0, 13: 39.0, 14: 36.0, 15: 37.4, 16: None}, "GNSS-STATIC_20261112/r01")
    assert all(x["base_kind"] == "임시 기준" for x in r.values())
    assert r[14]["hit"] and r[14]["status"] == G.RETEST and "낮음" in r[14]["why"]   # 같은 행 다른 셀 중앙값 39.5 − 3 = 36.5 보다 낮다
    assert not r[15]["hit"] and r[15]["status"] == G.PASS
    assert r[16]["hit"] and "위치" in r[16]["why"] and r[16]["status"] == G.RETEST     # 위치 못 잡음은 판정 불가가 아니라 재측정
    assert doc["cells"]["14"]["GNSS-STATIC"]["status"] == G.RETEST and doc["cells"]["14"]["GNSS-STATIC"]["rounds"][0]["cond"] == TMP


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
    cell_eff = {11: 1.0, 12: -1.0, 13: 0.5, 14: -0.5}
    order = sorted(slot_eff)
    doc = {}
    for k, level in enumerate((39.0, 39.5, 38.8, 39.2)):                       # 4회차 — 셀이 한 칸씩 돈다
        slots = {c: order[(i + k) % 4] for i, c in enumerate(cell_eff)}
        _round(doc, {c: level + slot_eff[slots[c]] + cell_eff[c] for c in cell_eff}, f"r{k + 1:02d}", slots, COND)
    obs = G.gnss_static_obs(doc, COND, 50.0)
    assert len(obs) == 16 and G.gnss_static_obs(doc, TMP, 50.0) == []
    rnd, slot, cell = G.fit_effects(obs)
    assert all(abs(slot[k] - v) < 0.05 for k, v in slot_eff.items())
    assert all(abs(cell[k] - v) < 0.05 for k, v in cell_eff.items())
    assert abs(rnd["r02"] - 39.5) < 0.05


def test_only_passed_cells_go_on_to_later_tests():
    """2026-11-25: 앞 시험 기록이 '합격'이 아닌 셀(불합격 · 재측정 · 판정 불가)은 뒤 시험에서 뺀다. 앞 시험 기록이 없는 셀은 오른다
    (그 시험을 아직 안 했거나 거르기 파일에 상태를 적지 않는 시험 — GNSS-MOVE)."""
    doc = {}
    G.record(doc, 11, "GNSS-STATIC", G.FAIL, "GNSS-STATIC_x/r02", "두 번")
    G.record(doc, 12, "IMU-STATIC", G.FAIL, "IMU-STATIC_x", "영점 9 °/s")
    G.record(doc, 13, "IMU-MOVE", G.PASS, "IMU-MOVE_x", "비 1.0")
    G.record(doc, 14, "GNSS-STATIC", G.RETEST, "GNSS-STATIC_x/r01", "한 번")
    G.record(doc, 15, "IMU-FW", G.FAIL, "x", "펌웨어")
    G.record(doc, 16, "GNSS-MOVE", G.FAIL, "GNSS-MOVE_x", "Raw data 속도 비 0.80")
    G.record(doc, 17, "GNSS-STATIC", G.UNJUDGED, "GNSS-STATIC_x/r01", "읽기 불일치")
    G.record(doc, 18, "GNSS-STATIC", G.PASS, "GNSS-STATIC_x/r01", "통과")
    assert G.GO_ON == (G.PASS,)
    assert G.excluded(doc, "GNSS-STATIC") == {}
    assert set(G.excluded(doc, "GNSS-MOVE")) == {11, 14, 17}                        # 합격(18)만 오른다 — 재측정 · 판정 불가도 뺀다
    assert set(G.excluded(doc, "IMU-STATIC")) == {11, 14, 16, 17}                   # GNSS-MOVE 는 IMU-STATIC 앞 센서 시험
    assert set(G.excluded(doc, "IMU-FW")) == {11, 12, 14, 16, 17}                   # 13 은 IMU-MOVE 합격 · GNSS-STATIC 기록 없음 → 오른다
    assert set(G.excluded(doc, "GNSS-FW")) == {11, 12, 14, 15, 16, 17}
    assert "재측정" in G.excluded(doc, "GNSS-MOVE")[14] and "판정 불가" in G.excluded(doc, "GNSS-MOVE")[17]
    # 기종 확인이 안 된 셀은 앞 시험이 없어도(GNSS-STATIC) 뺀다
    ex = G.excluded(doc, "GNSS-STATIC", {19: "기종 확인 필요 — 등록부에 없는 셀"})
    assert ex == {19: "판정 불가(기종 확인 필요 — 등록부에 없는 셀)"}


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


def test_sats_ratio_is_only_observed():
    """기준 0.3(2026-11-25): 위성 수 비율은 살펴봄 — 위성 2개(정상 중앙의 9 %)여도 걸리지 않고 값만 남는다.
    0.2 의 '50 % 아래면 걸림'은 시료 CLBY-2A 의 위성 수를 Y 형식으로 잘못 읽은 값이 유일한 근거였다.
    다른 종류(X)의 셀은 Y 중앙값과 비교하지 않는다."""
    doc = {}
    cells = {s: {"slot": f"1-{i + 1}", "cn0": 45.0, "gps_rx": 100.0, "sats": v, "kind": "Y"}
             for i, (s, v) in enumerate({101: 19, 102: 22, 103: 24, 104: 21, 105: 2}.items())}
    cells[106] = {"slot": "3-1", "cn0": 45.0, "gps_rx": 100.0, "sats": 9, "kind": "X"}
    cells[107] = {"slot": "3-2", "cn0": 45.0, "gps_rx": 100.0, "sats": 10, "kind": "X"}
    res = G.gnss_static_round(doc, cells, TMP, "t/r01")
    assert not any(res[s]["hit"] for s in (101, 102, 103, 104, 105, 106, 107))
    assert res[105]["status"] == G.PASS
    assert res[105]["checks"]["위성 수"] == {"hit": False, "observe": True, "why": "2개 = 같은 회차 같은 종류 다른 셀 중앙 21.5개의 9 %"}
    assert "위성 수 살펴봄: 2개" in res[105]["why"]
    assert doc["cells"]["105"]["GNSS-STATIC"]["rounds"][0]["sats_pct"] < 50
    assert res[106]["checks"]["위성 수"]["why"] == "9개 = 같은 회차 같은 종류 다른 셀 중앙 10개의 90 %"


def test_without_sats_the_round_is_judged_as_before():
    """위성 수가 없는 옛 기록(실시간 패킷에 sats 를 적기 전)은 위성 수 항목 없이 0.1 규칙대로 판정한다."""
    res = _round({}, {11: 45.0, 12: 45.5, 13: 44.5}, "t/r02")
    assert not any(r["hit"] for r in res.values())


# ---------------------------------------------------------------- 2026-11-25 판정 불가 · 항목별 걸림 · 두 기록 대조

def _cells(vals: dict, sats: dict | None = None, slots: dict | None = None) -> dict:
    slots = slots or {s: f"1-{i + 1}" for i, s in enumerate(vals)}
    return {s: {"slot": slots[s], "cn0": v, "gps_rx": 100.0, "sats": (sats or {}).get(s, 13), "kind": "Y"} for s, v in vals.items()}


def test_unknown_model_cell_is_unjudged_and_does_not_go_on():
    """기종 확인이 안 된 셀은 값이 이상해도 걸림이 아니라 판정 불가 — 기록은 남고, 뒤 시험(GNSS-MOVE)에 오르지 않는다."""
    doc = {}
    res = G.gnss_static_round(doc, _cells({101: 40.0, 102: 40.0, 103: 40.0, 104: 30.0}, {104: 1}), TMP, "t/r01",
                              unjudged={104: "기종 확인 필요 — 등록부에 없는 셀"})
    assert res[104]["status"] == G.UNJUDGED and res[104]["hit"] is None and res[104]["why"].startswith("판정 불가(기종 확인 필요")
    rnd = doc["cells"]["104"]["GNSS-STATIC"]["rounds"][0]
    assert rnd["unjudged"] and rnd["cn0"] == 30.0 and rnd["sats"] == 1                   # 값은 남는다
    assert set(G.excluded(doc, "GNSS-MOVE")) == {104}
    assert all(res[s]["status"] == G.PASS for s in (101, 102, 103))


def test_unjudged_cell_value_stays_out_of_the_round_level_and_row_median():
    """판정 불가 셀의 세기는 회차 수준(칸 기준) · 같은 행 중앙값(임시 기준) · 위성 수 비교 대상 · 칸 기준 다시 맞추기에 섞이지 않는다."""
    t = G.slot_table(G.criteria("GNSS-STATIC"), COND)
    slots = {101: "1-1", 102: "1-2", 103: "1-3", 104: "1-4"}
    vals = {101: 39.0 + t["1-1"], 102: 40.0 + t["1-2"], 103: 41.0 + t["1-3"], 104: 10.0 + t["1-4"]}   # 104 는 해독이 틀린 셀처럼 아주 낮다
    mixed = G.gnss_static_round({}, _cells(vals, {104: 1}, slots), COND, "a/r01")
    doc = {}
    clean = G.gnss_static_round(doc, _cells(vals, {104: 1}, slots), COND, "a/r01", unjudged={104: "기종 확인 필요"})
    assert clean[101]["base"] == round(40.0 + t["1-1"], 2)                         # 회차 수준 40.0 = 판정 대상 셋의 중앙값
    assert mixed[101]["base"] == round(39.5 + t["1-1"], 2)                         # 섞였으면 39.5 로 끌린다
    assert clean[104]["status"] == G.UNJUDGED
    obs = G.gnss_static_obs(doc, COND, 50.0)
    assert len(obs) == 3 and 104 not in {o["cell"] for o in obs}                     # 칸 기준 다시 맞추기에도 넣지 않는다
    row = G.gnss_static_round({}, _cells({201: 40.0, 202: 41.0, 203: 37.4, 204: 10.0}, {204: 1}), TMP, "b/r01", unjudged={204: "기종 확인 필요"})
    assert row[203]["base"] == 40.5 and row[203]["hit"]                             # 같은 행 중앙값도 판정 대상끼리
    row_mixed = G.gnss_static_round({}, _cells({201: 40.0, 202: 41.0, 203: 37.4, 204: 10.0}, {204: 1}), TMP, "b/r01")
    assert row_mixed[203]["base"] == 40.0 and not row_mixed[203]["hit"]             # 섞였으면 걸려야 할 셀이 빠져나갔다
    peers = G.gnss_static_round({}, _cells({301: 40.0, 302: 40.0, 303: 40.0}, {301: 13, 302: 13, 303: 1}), TMP, "c/r01", unjudged={303: "x"})
    assert peers[301]["checks"]["위성 수"]["why"] == "13개 = 같은 회차 같은 종류 다른 셀 중앙 13개의 100 %"   # 위성 1개 셀은 비교 대상에서 빠진다


def test_low_signal_is_caught_by_signal_regardless_of_satellites():
    """항목은 따로 본다 — 위성 수가 적은 셀이라도 신호 세기가 낮으면 '신호 세기'로 걸린다(0.2 에서는 위성 수 걸림이 앞에 있어 가려졌다)."""
    res = G.gnss_static_round({}, _cells({101: 40.0, 102: 40.0, 103: 40.0, 104: 33.0}, {104: 1}), TMP, "t/r01")
    c = res[104]["checks"]
    assert res[104]["hit"] and res[104]["status"] == G.RETEST
    assert c["신호 세기"]["hit"] and not c["위치"]["hit"] and not c["위성 수"]["hit"] and c["위성 수"]["observe"]
    assert res[104]["why"].startswith("위치 통과: ") and "신호 세기 걸림: 33.0 dB-Hz" in res[104]["why"] and "위성 수 살펴봄: 1개" in res[104]["why"]
    weak = {**_cells({101: 40.0, 102: 40.0, 103: 40.0}), 104: {"slot": "1-4", "cn0": 33.0, "gps_rx": 20.0, "sats": 13, "kind": "Y"}}
    both = G.gnss_static_round({}, weak, TMP, "t/r01")
    assert both[104]["checks"]["위치"]["hit"] and both[104]["checks"]["신호 세기"]["hit"]       # 둘 다 걸리면 둘 다 적힌다


def test_read_mismatch_marks_the_cell_unjudged():
    """같은 셀의 실시간 값과 셀 기록 값이 어긋나면 판정 불가(읽기 불일치). 같으면 그대로, 셀 기록이 없으면 대조 못 함(판정은 그대로)."""
    live = {101: {"sats": 13, "cn0": 40, "gps_rx": 100.0}, 102: {"sats": 1, "cn0": 33, "gps_rx": 100.0},
            103: {"sats": 13, "cn0": 40, "gps_rx": 100.0}, 104: {"sats": 12, "cn0": 39, "gps_rx": 95.0}}
    ftg = {101: {"sats": 13.0, "cn0": 40.0, "fix_pct": 100.0}, 102: {"sats": 13.0, "cn0": 33.0, "fix_pct": 100.0},
           104: {"sats": 13.0, "cn0": 40.0, "fix_pct": 100.0}}
    bad, rec = G.read_mismatch(live, ftg)
    assert set(bad) == {102} and bad[102].startswith("읽기 불일치 — 위성 수 실시간 1 · 셀 기록 13")
    assert rec[101]["ok"] is True and rec[104]["ok"] is True                         # 1개 · 1 dB · 5 %p 차는 허용 차 안
    assert rec[103] == {"ok": None, "why": "셀 기록 없음 — 대조 못 함"}
    doc = {}
    res = G.gnss_static_round(doc, _cells({s: v["cn0"] for s, v in live.items()}, {s: v["sats"] for s, v in live.items()}), TMP, "t/r01",
                              unjudged=bad, read=rec)
    assert res[102]["status"] == G.UNJUDGED and "읽기 불일치" in res[102]["why"]
    assert doc["cells"]["101"]["GNSS-STATIC"]["rounds"][0]["read"]["ok"] is True
    assert set(G.excluded(doc, "GNSS-MOVE")) == {102}


def test_unjudged_round_does_not_count_as_a_hit_and_later_rounds_decide():
    doc = {}
    G.gnss_static_round(doc, _cells({101: 30.0, 102: 40.0, 103: 40.0}), TMP, "t/r01")                 # 걸림 → 재측정
    assert doc["cells"]["101"]["GNSS-STATIC"]["status"] == G.RETEST
    G.gnss_static_round(doc, _cells({101: 30.0, 102: 40.0, 103: 40.0}), TMP, "t/r02", unjudged={101: "읽기 불일치"})
    assert doc["cells"]["101"]["GNSS-STATIC"]["status"] == G.UNJUDGED                                 # 걸림 1번 + 판정 불가 — 불합격 아님
    G.gnss_static_round(doc, _cells({101: 40.0, 102: 40.0, 103: 40.0}), TMP, "t/r03")
    assert doc["cells"]["101"]["GNSS-STATIC"]["status"] == G.PASS


def test_cell_without_a_serial_is_not_written_to_the_gate_file():
    """셀 번호를 안 쓴 셀(번호 1)은 셀끼리 구별할 수 없어 거르기 파일에 적지 않는다 — 결과는 돌려주되 판정 불가."""
    doc = {}
    res = G.gnss_static_round(doc, _cells({1: 33.0, 101: 40.0, 102: 40.0, 103: 40.0}, {1: 1}), TMP, "t/r01")
    assert "1" not in doc["cells"] and res[1]["status"] == G.UNJUDGED and "셀 번호를 안 쓴 셀" in res[1]["why"]
    G.record(doc, 1, "IMU-STATIC", G.PASS, "x", "y")
    assert "1" not in doc["cells"]


def test_recalc_drops_old_satellite_hits_and_the_unnumbered_cell():
    """쌓인 거르기 다시 내기(tools/qc_tests/gnss_static_regate.py --recalc) — 0.2 의 위성 수 걸림이 빠지고 셀 번호 1 은 파일에서 빠진다.
    다른 시험 칸은 그대로, 원본은 고치지 않는다."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "qc_tests"))
    import gnss_static_regate as R
    old = {"cells": {}}
    for s, slot, sats in ((1, "1-1", 1), (10679, "1-2", 13), (10680, "1-3", 13), (10681, "1-4", 6)):
        hit = sats < 7
        r = {"source": "S/r03", "date": "2026-11-24", "cond": TMP, "slot": slot, "cn0": 40.0, "gps_rx": 100.0, "sats": sats, "hit": hit, "why": "위성"}
        old["cells"][str(s)] = {"GNSS-STATIC": {"status": G.RETEST if hit else G.PASS, "rounds": [r]}}
    old["cells"]["10679"]["IMU-STATIC"] = {"status": G.PASS}
    new = R.recalc(old, "Y")
    assert "1" not in new["cells"] and new["cells"]["10679"]["IMU-STATIC"] == {"status": G.PASS}
    assert new["cells"]["10681"]["GNSS-STATIC"]["status"] == G.PASS                  # 위성 6개(46 %)는 이제 살펴봄
    assert old["cells"]["10681"]["GNSS-STATIC"]["status"] == G.RETEST                # 원본은 그대로
