"""레일 독 충전 기준 (motionbench/charge.py · run_motion.py long) — 장비 없이 돈다.

2026-10-30 김건우 요청: 완전 방전만 막는다.
  1. 시작 전 가장 낮은 셀이 30% 미만이면 시작하지 않고 충전부터.
  2. 시험 중 가장 낮은 셀이 20% 에 닿으면 지금 구간만 마치고 멈춘 뒤 충전.
  3. 쉬는 동안은 플러그를 켜 두고, 독이 끌어 쓰지 않으면 끊었다 켠다(PowerWatch).
플러그는 모터 어댑터를 열기 전(끄기)과 닫은 뒤(켜기)에만 부른다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from motionbench import charge  # noqa: E402
from test_motion_long import LONG, SWEEP, rl  # noqa: E402,F401  (rl 은 fixture)
from test_motion_m2_modes import rm, run_move, wire  # noqa: E402,F401
from test_motion_rail import _put, rr  # noqa: E402,F401

SER = {10677, 10678, 10679}
NOW = 1000.0


def _doc(vals: dict, t=NOW, cell_t=NOW):
    return {"t": t, "cells": {str(s): {"battery": b, "t": cell_t} for s, b in vals.items()}}


# ---------------------------------------------------------------- 순수 판정

def test_rail_serials_are_the_dock_layout_24():
    s = charge.rail_serials()
    assert len(s) == 24 and 10677 in s and 10700 in s and 11733 not in s     # 셀 시험대 세트(11733~)는 빠진다


def test_lowest_is_the_minimum_not_the_mean():
    assert charge.lowest(_doc({10677: 90, 10678: 25, 10679: 95}), SER, NOW) == (25, 3, None)


def test_lowest_ignores_other_cells_stale_cells_and_bad_values():
    doc = _doc({10677: 80, 11733: 5})                                      # 11733 은 레일 독 셀이 아니다
    doc["cells"]["10678"] = {"battery": 3, "t": NOW - 60}                  # 1분 묵은 값
    doc["cells"]["10679"] = {"battery": 255, "t": NOW}                     # '모름' 표시
    assert charge.lowest(doc, SER, NOW) == (80, 1, None)


def test_lowest_says_why_when_it_cannot_read():
    assert charge.lowest(None, SER, NOW)[0] is None
    assert "묵었다" in charge.lowest(_doc({10677: 80}, t=NOW - 60), SER, NOW)[2]
    assert "들리지 않는다" in charge.lowest(_doc({11733: 80}), SER, NOW)[2]


def test_start_rule_30_pct():
    assert charge.start_problem(30, None) is None
    assert "30% 미만" in charge.start_problem(29, None)
    assert "읽지 못해" in charge.start_problem(None, "x")


def test_stop_rule_20_pct_and_unknown_does_not_stop():
    assert charge.should_stop(20) and charge.should_stop(5)
    assert not charge.should_stop(21) and not charge.should_stop(None)


# ---------------------------------------------------------------- run_motion long 배선

def test_long_refuses_below_30_and_turns_plug_on_without_touching_the_drive(rl, monkeypatch):
    rm = rl
    _put(rm.APPROVAL_LONG, LONG)
    wire(rm, monkeypatch, speed=3000)
    monkeypatch.setattr(rm, "BATT_LOW", lambda: (25, 24, None))
    reason, info, m, _, _ = run_move(rm, "long")
    assert reason.startswith("거부") and "30% 미만" in reason
    assert rm.plug_calls == [True]                                          # 충전부터
    assert m.opens == []                                                    # 어댑터를 열지 않았다 = 모터에 닿지 않았다


def test_long_refuses_when_cells_cannot_be_read(rl, monkeypatch):
    rm = rl
    _put(rm.APPROVAL_LONG, LONG)
    wire(rm, monkeypatch, speed=3000)
    monkeypatch.setattr(rm, "BATT_LOW", lambda: (None, 0, "잔량 파일이 없다"))
    reason, *_ = run_move(rm, "long")
    assert "읽지 못해" in reason
    assert rm.plug_calls == []                                              # 모르면 플러그를 건드리지 않는다


def test_long_turns_plug_off_before_and_on_after(rl, monkeypatch):
    rm = rl
    _put(rm.APPROVAL_LONG, LONG)
    wire(rm, monkeypatch, speed=3000)
    reason, info, *_ = run_move(rm, "long")
    assert reason == rm.DONE_LONG, reason
    assert rm.plug_calls == [False, True]
    assert info["batt_start"]["low_pct"] == 80 and info["plug_end"]["want"] == "on"


def test_sweep_stops_after_the_step_where_lowest_hits_20(rl, monkeypatch):
    rm = rl
    _put(rm.APPROVAL_LONG, {**SWEEP, "speeds_mm_s": [100, 150, 300], "cycles_per_speed": 1})
    wire(rm, monkeypatch, speed=3000)
    seq = iter([(80, 24, None), (20, 24, None)])                            # 시작 80% → 첫 구간 뒤 20%
    monkeypatch.setattr(rm, "BATT_LOW", lambda: next(seq, (20, 24, None)))
    reason, info, *_ = run_move(rm, "long")
    assert reason == rm.DONE_LOW_BATT, reason
    steps = info["long"]["steps"]
    assert len(steps) == 1 and steps[0]["cycles"] == 1                      # 첫 구간은 끝까지, 다음 구간은 시작하지 않았다
    assert "20%" in info["long"]["stopped"]
    assert rm.plug_calls == [False, True]                                   # 끝나면 켠다 = 충전
