import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench.cycle import discharge_done, integrate_wh, is_full


def test_discharge_done_uses_minimum_cell():
    assert discharge_done([31, 45, 90], 30) is False
    assert discharge_done([30, 45, 90], 30) is True
    assert discharge_done([], 30) is False            # 셀이 안 들리면 판단 보류


def test_integrate_wh_trapezoid_and_nan():
    assert math.isclose(integrate_wh(60, 80, 3600), 70.0)
    assert integrate_wh(float("nan"), 80, 3600) == 0.0
    assert integrate_wh(60, 80, 0) == 0.0


def _flat(start, minutes, w=31.0, step=20):
    return [(start + i * step, w) for i in range(int(minutes * 60 / step) + 1)]


def test_is_full_requires_flat_window_and_all_100():
    t0 = 1000.0
    samples = _flat(t0, 12)
    now = t0 + 12 * 60
    assert is_full(samples, [100] * 24, now, 10, 1.0, True, 24) is True
    assert is_full(samples, [100] * 23 + [99], now, 10, 1.0, True, 24) is False     # 한 셀이 99%
    assert is_full(samples, [100] * 23, now, 10, 1.0, True, 24) is False            # 셀 하나가 안 들림
    assert is_full(samples, [100] * 23, now, 10, 1.0, False, 24) is True            # 100% 조건을 끄면 전력만 본다


def test_is_full_rejects_still_falling_power():
    t0 = 1000.0
    samples = [(t0 + i * 20, 40 - i * 0.05) for i in range(37)]                    # 12분 동안 1.8 W 하락
    assert is_full(samples, [100] * 24, t0 + 12 * 60, 10, 1.0, True, 24) is False


def test_is_full_needs_enough_elapsed_time():
    t0 = 1000.0
    samples = _flat(t0, 5)                                                          # 5분치만
    assert is_full(samples, [100] * 24, t0 + 5 * 60, 10, 1.0, True, 24) is False


def test_discharge_csv_estimates_runtime(tmp_path):
    from cellbench.record import Recorder
    rec = Recorder(tmp_path)
    rec.discharge(1, {11733: 100, 11734: 100}, {11733: 30, 11734: 44}, 4 * 3600, 30)
    rows = (tmp_path / "discharge_0001.csv").read_text(encoding="utf-8-sig").splitlines()
    assert rows[1].split(",")[-1] == "4.00"          # 70% 를 4시간에 → 4.00h
    assert rows[2].split(",")[-1] == "5.00"          # 56% 를 4시간에 = 14%/h → 70/14 = 5.00h


def test_now_json_written_atomically(tmp_path):
    import json
    from cellbench.record import Recorder
    rec = Recorder(tmp_path)
    rec.now({"phase": "DISCHARGE", "cells": []})
    assert json.loads((tmp_path / "now.json").read_text(encoding="utf-8"))["phase"] == "DISCHARGE"
    assert not (tmp_path / "now.json.tmp").exists()
