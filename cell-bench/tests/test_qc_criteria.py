"""1차 기준 규칙(motionbench/qc_criteria.py)과 범위 합불(qc_v4.pass_range) — 장비 · 파일 없이 돈다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from motionbench import qc_criteria as Q, qc_v4 as V  # noqa: E402
import protocol_v4 as P  # noqa: E402


def doc(vals: dict, ok=True, test="GNSS-STATIC"):
    return {"conditions": {"test": test}, "rig": {"유효": ok},
            "cells": {str(s): {"items": {"cn0_sim": {"value": v}}} for s, v in vals.items()}}


def test_per_cell_skips_invalid_runs_and_missing():
    d1, d2 = doc({1: 40, 2: None}), doc({1: 99, 2: 41}, ok=False)
    assert Q.per_cell([d1, d2], "cn0_sim") == {"1": [40.0]}


def test_stable_cells_with_one_weak_become_pass_fail_and_catch_it():
    # 셀 10대: 9대는 43 근처, 1대는 34 — 같은 셀 흔들림(±0.5)이 셀 사이 차이보다 훨씬 작다
    runs = [doc({s: (34 if s == 9 else 41 + (s % 5)) + (0.5 if r % 2 else -0.5) for s in range(10)}) for r in range(4)]
    c = Q.derive(Q.per_cell(runs, "cn0_sim"), "cn0_sim")
    assert c["kind"] == "합불" and c["status"] == "제안값" and c["hi"] is None
    assert set(c["fails"]) == {"9"} and c["fails"]["9"]["fail_runs"] == 4


def test_noisy_item_stays_review():
    # 같은 셀이 실행마다 크게 흔들리면 셀을 가르지 못한다 → 살펴봄
    runs = [doc({s: 40 + (6 if (s + r) % 2 else -6) for s in range(10)}) for r in range(4)]
    c = Q.derive(Q.per_cell(runs, "cn0_sim"), "cn0_sim")
    assert c["kind"] == "살펴봄" and "가르지 못한다" in c["why"]


def test_too_few_runs_stays_review():
    c = Q.derive(Q.per_cell([doc({s: 40 + s for s in range(10)})] * 2, "cn0_sim"), "cn0_sim")
    assert c["kind"] == "살펴봄" and "재료 부족" in c["why"]


def test_pass_range_both_sides_and_key():
    it = V.pass_range({1: {"v_ratio": {"300": 0.99}}, 2: {"v_ratio": {"300": 0.80}}, 3: None}, "gps_move", 0.95, 1.05,
                      key=lambda v: v["v_ratio"]["300"])
    assert [it[s]["verdict"] for s in (1, 2, 3)] == ["pass", "fail", "hold"]
    assert it[1]["value"] == {"v_ratio": {"300": 0.99}}


def test_assemble_uses_criteria_file_kind():
    crit = {"version": "1차 시험", "tests": {"GNSS-STATIC": {"items": {"cn0_sim": {"kind": "합불", "status": "제안값", "lo": 38.0, "hi": None,
                                                                   "rule": "38 dBHz 이상"}}}}}
    per = {10677: {"rx": 0.0, "gps_rx": 100.0, "cn0_sim": 43.0}, 10687: {"rx": 0.0, "gps_rx": 100.0, "cn0_sim": 34.0}}
    d = P.assemble(per, {}, [[10677, 10687]], {"test": "GNSS-STATIC"}, {"유효": True}, "x", {}, {}, crit)
    assert d["cells"]["10687"]["verdict"]["overall"] == "fail" and d["cells"]["10677"]["verdict"]["overall"] == "pass"
    assert d["criteria"]["cn0_sim"]["kind"] == "합불" and d["criteria_version"] == "1차 시험"
    d0 = P.assemble(per, {}, [[10677, 10687]], {"test": "GNSS-STATIC"}, {"유효": True}, "x", {}, {}, None)
    assert d0["cells"]["10687"]["verdict"]["overall"] == "pass" and d0["criteria_version"] is None


def test_criteria_key_must_match_the_run():
    crit = {"tests": {"IMU-FW": {"items": {"gate_on": {"kind": "합불", "lo": None, "hi": 1.2, "key": {"accel": "50"}}}}}}
    assert P._crit_items(crit, "IMU-FW", {"gate_on": {"accel": "500"}}) == {}
    assert "gate_on" in P._crit_items(crit, "IMU-FW", {"gate_on": {"accel": "50"}})
    assert P._crit_items(crit, "IMU-MOVE", {"gate_on": {"accel": "50"}}) == {}       # 다른 시험의 칸은 쓰지 않는다


def test_two_sided_criteria_value_is_a_pair():
    crit = {"version": "v", "tests": {"GNSS-MOVE": {"items": {"gps_move": {"kind": "합불", "lo": 0.95, "hi": 1.02, "key": {"v_ratio": "300"}}}}}}
    per = {1: {"rx": 0.0, "gps_rx": 90.0, "cn0_sim": 48.0}}
    d = P.assemble(per, {"gps_move": ({1: {"v_ratio": {"300": 0.99}}}, lambda v: v["v_ratio"]["300"])}, [[1]],
                   {"test": "GNSS-MOVE"}, {"유효": True}, "x", {}, {}, crit, {"gps_move": {"v_ratio": "300"}})
    c = d["criteria"]["gps_move"]
    assert c["value"] == [0.95, 1.02] and c["lo"] == 0.95 and c["hi"] == 1.02
    assert d["cells"]["1"]["verdict"]["overall"] == "pass"


def test_retest_marks_conditions(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "RETEST", {"retest_of": "20261103-101500", "retest_cells": [10687]})
    doc = {"stamp": "s1", "conditions": {"test": "GNSS-STATIC"}, "rig": {"유효": True, "이유": []}, "cells": {}}
    P.write(doc, tmp_path)
    import json as _j
    c = _j.loads((tmp_path / "protocol_s1.json").read_text(encoding="utf-8"))["conditions"]
    assert c["retest_of"] == "20261103-101500" and c["retest_cells"] == [10687]


# ---------------------------------------------------------------- 시험 ID (2026-11-12 옛 이름 청산)

def test_criteria_file_is_keyed_by_test_id():
    import json as _j
    doc = _j.loads((Path(__file__).resolve().parents[1] / "motionbench" / "criteria" / "rail_v1.json").read_text(encoding="utf-8"))
    assert "parts" not in doc and list(doc["tests"]) == ["GNSS-STATIC", "IMU-FW", "GNSS-MOVE"]
    assert doc["tests"]["IMU-FW"]["items"]["gate_on"]["key"] == {"accel": "50"}


def test_judged_conditions_carry_test_id_and_board_part():
    assert P.conditions("GNSS-STATIC", "S01-Static-GLE-8h", "Y")["test"] == "GNSS-STATIC"
    assert [P.conditions(t, "s", "Y")["part"] for t in ("GNSS-STATIC", "IMU-FW", "GNSS-MOVE")] == ["A", "C", "B"]   # 결과판 호환 칸


def test_tool_reads_old_and_new_judged_files(tmp_path):
    import json as _j
    import qc_criteria as T
    from motionbench import qc_ids
    assert qc_ids.from_record({"part": "A"}) == "GNSS-STATIC" and qc_ids.from_record({"part": "B"}) == "GNSS-MOVE"
    assert qc_ids.from_record({"test": "FIRMWARE-2", "part": "B"}) == "GNSS-MOVE"      # 여섯 시험으로 나누기 전(2026-11-12) 이름
    assert qc_ids.from_record({"test": "IMU-FW", "part": "C"}) == "IMU-FW" and qc_ids.from_record({}) is None
    for i in range(4):     # 옛 파일(part 만) 둘, 새 파일(test) 둘, 다른 시험 하나
        (tmp_path / f"protocol_{i}.json").write_text(_j.dumps(doc({1: 40 + i % 2, 2: 41}, test="GNSS-STATIC") if i % 2 else
                                                              {**doc({1: 40, 2: 41}), "conditions": {"part": "A", "set": "Y"}}), encoding="utf-8")
    (tmp_path / "protocol_9.json").write_text(_j.dumps(doc({1: 10, 2: 10}, test="IMU-FW")), encoding="utf-8")
    out = tmp_path / "crit.json"
    T.main(["GNSS-STATIC", str(tmp_path / "protocol_*.json"), "--out", str(out)])
    got = _j.loads(out.read_text(encoding="utf-8"))
    assert list(got["tests"]) == ["GNSS-STATIC"] and got["tests"]["GNSS-STATIC"]["files"] == 4


def test_test_ids_parse_and_join():
    from motionbench import qc_ids
    import pytest
    assert qc_ids.parse("IMU-MOVE,IMU-FW") == qc_ids.parse("IMU-MOVE · IMU-FW") == ["IMU-MOVE", "IMU-FW"]
    assert qc_ids.join(["IMU-MOVE", "IMU-FW"]) == "IMU-MOVE · IMU-FW"
    for bad in ("A", "B", "C", "", "IMU-3"):
        with pytest.raises(ValueError):
            qc_ids.parse(bad)


def test_day_record_names_tests_by_id():
    import rail_records as RR
    assert RR.test_name({"test": "GNSS-MOVE", "speeds_mm_s": [100]}) == "GNSS-MOVE 100 mm/s"
    assert RR.test_name({"part": "B", "speeds_mm_s": [300]}) == "GNSS-MOVE 300 mm/s"     # 2026-11-12 전 파일
    assert RR.test_name({"part": "A"}) == "GNSS-STATIC" and RR.test_name({}) == "?"


def test_test_names_modules_and_combos():
    """2026-11-13 사용자 결정 — 시험 이름(GNSS · IMU × 정지 · 이동 · 펌웨어)과 조건 모듈 · 조합."""
    import pytest
    from motionbench import qc_ids
    assert qc_ids.TESTS == ("GNSS-STATIC", "GNSS-MOVE", "GNSS-FW", "IMU-STATIC", "IMU-MOVE", "IMU-FW")
    assert [qc_ids.KO[t] for t in qc_ids.TESTS] == ["GNSS-정지", "GNSS-이동", "GNSS-펌웨어", "IMU-정지", "IMU-이동", "IMU-펌웨어"]
    old = {"GNSS-1": "GNSS-STATIC", "GNSS-2": "GNSS-MOVE", "IMU-1": "IMU-STATIC", "IMU-2": "IMU-MOVE", "FIRMWARE-1": "IMU-FW"}
    assert all(qc_ids.from_record({"test": o}) == n for o, n in old.items())
    for row in qc_ids.COMBOS:                                       # 모든 조합 칸이 모듈 값이다
        assert all(v in qc_ids.MODULES[k] for k, v in zip(qc_ids.KEYS, row[2:]))
    assert len(qc_ids.COMBOS) == 9 and {t for t, place, *_ in qc_ids.COMBOS if place == "야외"} == {"GNSS-STATIC", "GNSS-MOVE", "GNSS-FW"}
    assert qc_ids.combo("GNSS-STATIC") == {"run": "RUN-STILL", "src": "SRC-LABSAT", "sat": "SAT-12", "gps": "GPS-STILL", "att": "ATT-18", "dock": "DOCK-12"}
    with pytest.raises(ValueError):
        qc_ids.conditions(run="RUN-STILL")
