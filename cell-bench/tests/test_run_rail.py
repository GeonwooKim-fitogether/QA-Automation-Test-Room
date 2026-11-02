"""레일 반복 실행기(tools/run_rail.py) — 모터 · LabSat 없이 순서와 멈춤 조건만 본다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import run_rail as R  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    pre = tmp_path / "presets"; pre.mkdir()
    (pre / "long-C.json").write_text(json.dumps({"a_from_left_mm": 150, "b_from_left_mm": 750}), encoding="utf-8")
    monkeypatch.setattr(R, "PRESETS", pre)
    monkeypatch.setattr(R, "APPROVAL", tmp_path / "long-approval.json")
    monkeypatch.setattr(R, "MARKER", tmp_path / "motion_attended")
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    return tmp_path


def test_install_preset_copies_only_approved_file(env):
    a = R.install_preset("C")
    assert a["b_from_left_mm"] == 750 and json.loads((env / "long-approval.json").read_text(encoding="utf-8")) == a
    with pytest.raises(SystemExit):
        R.install_preset("B")                       # 프리셋이 없으면 값을 지어내지 않고 멈춘다


def test_preflight_needs_marker_time_and_battery(env, monkeypatch):
    monkeypatch.setattr(R.charge, "rail_low", lambda: (55, 23, None))
    with pytest.raises(SystemExit, match="입회 표지"):
        R.preflight("C", 10, 95)
    (env / "motion_attended").write_text(json.dumps({"until": R.time.time() + 3600}), encoding="utf-8")
    R.preflight("C", 10, 95)
    monkeypatch.setattr(R.charge, "rail_low", lambda: (25, 23, None))
    with pytest.raises(SystemExit, match="30%"):
        R.preflight("C", 10, 95)


def test_run_c_records_each_run_and_stops_on_motor_error(env, monkeypatch):
    monkeypatch.setattr(R.charge, "rail_low", lambda: (55, 23, None))
    codes = iter([0, 0, 3])
    monkeypatch.setattr(R, "motor_once", lambda log: (next(codes), "오래 왕복 다 됨", f"m{log.stem}.csv", 1.0))
    runs = []
    assert R.run_c(5, env, runs) == 1
    assert [r["rc"] for r in runs] == [0, 0, 3]
    assert json.loads((env / "runs.json").read_text(encoding="utf-8"))[-1]["i"] == 3


def test_run_c_stops_when_battery_low(env, monkeypatch):
    monkeypatch.setattr(R.charge, "rail_low", lambda: (15, 23, None))
    monkeypatch.setattr(R, "motor_once", lambda log: pytest.fail("배터리가 낮은데 모터를 띄웠다"))
    assert R.run_c(3, env, []) == 2
