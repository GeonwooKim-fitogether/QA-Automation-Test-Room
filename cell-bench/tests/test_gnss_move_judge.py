"""GNSS-이동 셀 최종 판정(2026-11-25 사용자 결정 A, 숫자는 임시) — 장비 없이 돈다.
속도마다 걸린 운전 0 → 통과 · 1 → 재측정 · 2 이상 → 불합격, 재측정 회차는 1번이라도 걸리면 불합격. rx 는 합불이 아니다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tools", ROOT / "tools" / "qc_tests"):
    sys.path.insert(0, str(p))
import gnss_move_judge as J  # noqa: E402
import protocol_v4 as P  # noqa: E402

R = {"lim": 50.0, "min_runs": 5, "fail_runs": 2, "retest_runs": 10}


def test_rule_comes_from_the_criteria_file():
    r = J.rule()
    assert r["lim"] == 50.0 and (r["min_runs"], r["fail_runs"], r["retest_runs"]) == (5, 2, 10)


def test_speed_verdicts():
    ok = [1.0] * 10
    assert J.speed_verdict(ok, R)[0] == J.PASS
    assert J.speed_verdict([7700.8] + ok[:9], R)[0] == J.RETEST          # 11-17 11619 처럼 한 번 튐
    assert J.speed_verdict([60.0, -70.0] + ok[:8], R)[0] == J.FAIL
    assert J.speed_verdict(ok[:4], R) == (J.RETEST, "셀 수 운전 4번 < 5번(운전 부족)")
    assert J.speed_verdict([55.0] + ok[:9], R, retest=True)[0] == J.FAIL   # 재측정 회차는 1번이라도 걸리면 불합격
    assert J.speed_verdict(ok, R, retest=True)[0] == J.PASS
    assert J.speed_verdict([50.0] + ok[:9], R)[0] == J.PASS               # 경계 ±50 은 안


def test_cell_verdict():
    assert J.cell_verdict({"300": J.PASS, "1000": J.PASS}) == J.CELL_PASS
    assert J.cell_verdict({"300": J.PASS, "1000": J.RETEST}) == J.RETEST
    assert J.cell_verdict({"300": J.RETEST, "1000": J.FAIL}) == J.FAIL


def _record(tmp: Path, runs: list[tuple[str, bool, dict[str, float]]], excluded=None) -> tuple[Path, Path]:
    """가짜 기록 폴더 + 판정 산출 — runs = [(stamp, 장비 유효, {셀: gps_err})]."""
    rec, judged = tmp / "GNSS-MOVE_x", tmp / "judged"
    (rec / "v1000").mkdir(parents=True); judged.mkdir()
    (rec / "summary.json").write_text(json.dumps({"excluded": excluded or {}}), encoding="utf-8")
    rows = []
    for i, (stamp, valid, errs) in enumerate(runs, 1):
        rows.append({"i": i, "rc": 0, "motor_csv": f"C:/x/motion_long_{stamp}.csv"})
        cells = {s: {"items": {"gps_err": {"value": e}, "rx": {"value": 3.0, "verdict": "fail"}}} for s, e in errs.items()}
        (judged / f"protocol_{stamp}.json").write_text(json.dumps({"rig": {"유효": valid, "이유": [] if valid else ["출발 차"]}, "cells": cells}),
                                                       encoding="utf-8")
    (rec / "v1000" / "runs.json").write_text(json.dumps(rows), encoding="utf-8")
    return rec, judged


def test_judge_counts_only_valid_runs_and_skips_sample_and_excluded(tmp_path):
    runs = [(f"20261125-13{i:02d}00", i != 2, {"10679": 1.0, "10680": 80.0 if i in (3, 5) else 2.0, "10681": 1.0, "1": 1.0})
            for i in range(1, 9)]
    runs[1] = (runs[1][0], False, {"10679": 999.0, "10680": 999.0, "10681": 999.0, "1": 1.0})      # 무효 운전의 값은 세지 않는다
    rec, judged = _record(tmp_path, runs, excluded={"10681": "앞 시험 불합격"})
    out = J.judge(rec, judged, R)
    assert out["cells"]["10679"]["verdict"] == J.CELL_PASS and out["cells"]["10679"]["speeds"]["1000"]["n"] == 7
    assert out["cells"]["10680"]["verdict"] == J.FAIL                    # 유효 운전에서 두 번 걸림
    assert "1" not in out["cells"] and "10681" not in out["cells"] and out["skipped"] == ["1", "10681"]


def test_retest_round_uses_only_new_runs(tmp_path):
    first = J.judge(*_record(tmp_path / "a", [(f"20261125-13{i:02d}00", True, {"10679": 80.0 if i == 1 else 1.0}) for i in range(1, 11)]), R)
    assert first["cells"]["10679"]["verdict"] == J.RETEST
    again = J.judge(*_record(tmp_path / "b", [(f"20261125-14{i:02d}00", True, {"10679": 1.0}) for i in range(1, 11)]), R, prev=first)
    assert again["cells"]["10679"]["verdict"] == J.CELL_PASS and again["cells"]["10679"]["speeds"]["1000"]["retest"] is True


def test_rx_is_not_a_pass_fail_item_for_gnss_move():
    crit = P.gnss_move_crit({"version": "1차", "tests": {"GNSS-MOVE": {"items": {}}}})
    assert crit["tests"]["GNSS-MOVE"]["items"]["rx"]["kind"] == "살펴봄"
