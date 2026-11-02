"""1차 기준 규칙(motionbench/qc_criteria.py)과 범위 합불(qc_v4.pass_range) — 장비 · 파일 없이 돈다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from motionbench import qc_criteria as Q, qc_v4 as V  # noqa: E402
import protocol_v4 as P  # noqa: E402


def doc(vals: dict, ok=True, part="A"):
    return {"conditions": {"part": part}, "rig": {"유효": ok},
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
    crit = {"version": "1차 시험", "parts": {"A": {"items": {"cn0_sim": {"kind": "합불", "status": "제안값", "lo": 38.0, "hi": None,
                                                                   "rule": "38 dBHz 이상"}}}}}
    per = {10677: {"rx": 0.0, "gps_rx": 100.0, "cn0_sim": 43.0}, 10687: {"rx": 0.0, "gps_rx": 100.0, "cn0_sim": 34.0}}
    d = P.assemble(per, {}, [[10677, 10687]], {"part": "A"}, {"유효": True}, "x", {}, {}, crit)
    assert d["cells"]["10687"]["verdict"]["overall"] == "fail" and d["cells"]["10677"]["verdict"]["overall"] == "pass"
    assert d["criteria"]["cn0_sim"]["kind"] == "합불" and d["criteria_version"] == "1차 시험"
    d0 = P.assemble(per, {}, [[10677, 10687]], {"part": "A"}, {"유효": True}, "x", {}, {}, None)
    assert d0["cells"]["10687"]["verdict"]["overall"] == "pass" and d0["criteria_version"] is None


def test_criteria_key_must_match_the_run():
    crit = {"parts": {"C": {"items": {"gate_on": {"kind": "합불", "lo": None, "hi": 1.2, "key": {"accel": "50"}}}}}}
    assert P._crit_items(crit, "C", {"gate_on": {"accel": "500"}}) == {}
    assert "gate_on" in P._crit_items(crit, "C", {"gate_on": {"accel": "50"}})
