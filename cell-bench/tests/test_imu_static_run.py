"""IMU-STATIC 실행기(tools/qc_tests/imu_static_run.py)의 셀 시각 조건 — 장비 없이 가짜로 본다(2026-11-25).
시험 문서 1-2절 '지킬 것': ① 기록 전에 독 셀이 모두 위치를 잡는다 ③ 시작할 때 셀마다 위성 수 · 위치 상태를 기록에 남긴다.
그리고 판정 불가 셀(셀 번호 1 — 시료 CLBY-2A)은 원격 재부팅 · 위치 기다림에서 뺀다(시험 문서 2-1절)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

QT = Path(__file__).resolve().parents[1] / "tools" / "qc_tests"
sys.path.insert(0, str(QT))
import imu_static_run as I  # noqa: E402
import gnss_static_run as G  # noqa: E402

Q = I.RC.Q
DOCK = [10689, 10690, 10691, 10692, 10693, 10694, 11619, 10679, 10680, 10681, 10682, 10700]
FIX = sorted(Q.FIX_MODES)[0]


class Rec:
    def __init__(self, out: Path):
        self.out, self.summary, self.saved = out, {}, 0

    def save(self):
        self.saved += 1


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """가짜 장비 — 시계는 sleep 만큼 흐른다. state['unfixed'] 셀은 위치를 못 잡고, 재부팅되면 잡는다(state['fix_on_reboot'])."""
    clock = [1_800_000_000.0]
    state = {"unfixed": set(), "fix_on_reboot": True, "reboots": [], "heard": DOCK + [1]}

    def sleep(s):
        clock[0] += s

    monkeypatch.setattr(I.time, "time", lambda: clock[0])
    monkeypatch.setattr(I.time, "sleep", sleep)          # I.time 과 G.time 은 같은 time 모듈이다

    def qc_now():
        cells = {}
        for x in state["heard"]:
            cells[str(x)] = {"age_s": 1.0, "battery": 90, "fix": None if x in state["unfixed"] else FIX,
                             "sats": 0 if x in state["unfixed"] else 13}
        return {"cells": cells}

    def reboot(serials, say):
        state["reboots"].append(list(serials))
        if state["fix_on_reboot"]:
            state["unfixed"] -= set(serials)
        return {}

    monkeypatch.setattr(Q, "qc_now", qc_now)
    monkeypatch.setattr(Q, "reboot", reboot)
    monkeypatch.setattr(Q, "start_tap", lambda *a, **k: "tap")
    monkeypatch.setattr(Q, "stop_tap", lambda tap: None)
    monkeypatch.setattr(Q, "layout_columns", lambda name: [DOCK[:6], [], DOCK[6:], []])
    monkeypatch.setattr(Q.charge, "plug_set", lambda on, say: {"on": on})
    a = SimpleNamespace(boots=2, settle_min=3.0, rec_min=5.0, min_batt=50.0, min_cells=12, layout="Y-12")
    return Rec(tmp_path), a, state, clock


def test_sample_is_not_rebooted_or_waited_for(rig):
    rec, a, state, _ = rig
    assert I.drive(rec, lambda m: None, a) == 0
    assert all(1 not in r for r in state["reboots"])                  # 시료(셀 번호 1)는 재부팅하지 않는다
    assert rec.summary["gps_wait_for"] == sorted(DOCK)                # 위치 기다림도 독의 Y 셀만
    assert "1" in rec.summary["model_check"]


def test_records_fix_and_sats_per_cell_at_each_boot_start(rig):
    rec, a, state, _ = rig
    I.drive(rec, lambda m: None, a)
    boots = rec.summary["boots"]
    assert [b["k"] for b in boots] == [1, 2]
    for b in boots:
        assert b["gps"]["10689"] == {"fix": FIX, "sats": 13, "age_s": 1.0}
        assert b["unlocked"] == []
    assert rec.summary["gps_start"] == boots[0]["gps"]               # 지킬 것 ③ — 시작할 때 값


def test_waits_for_unfixed_dock_cell_and_reboots_only_it(rig, monkeypatch):
    rec, a, state, _ = rig
    seen = []
    real = Q.reboot

    def reboot(serials, say):
        res = real(serials, say)
        if len(state["reboots"]) == 1:                               # 부팅 1 의 전체 재부팅 뒤 10694 만 위치를 못 잡는다
            state["unfixed"].add(10694)
        seen.append(list(serials))
        return res

    monkeypatch.setattr(Q, "reboot", reboot)
    I.drive(rec, lambda m: None, a)
    assert seen[1] == [10694]                                         # 90초 뒤 못 잡은 독 셀만 다시 재부팅
    b1 = rec.summary["boots"][0]
    assert b1["unlocked"] == [] and b1["lock_s"] >= G.FIRST_REBOOT_S
    assert b1["gps"]["10694"]["fix"] == FIX                          # 기록은 잡은 뒤에 시작한다


def test_sample_inside_layout_with_eleven_y_cells_runs_through(rig, monkeypatch):
    """QA 반려 시나리오(2026-11-25): 배치표 3-1 에 시료(셀 번호 1) + Y 11대, 기본 --min-cells 12, 모두 위치를 잡음 → 멈추지 않는다."""
    rec, a, state, _ = rig
    y11 = DOCK[:6] + DOCK[6:11]
    monkeypatch.setattr(Q, "layout_columns", lambda name: [DOCK[:6], [], [1] + DOCK[6:11], []])
    state["heard"] = y11 + [1]
    assert I.drive(rec, lambda m: None, a) == 0
    assert rec.summary["gps_need"] == 11 and rec.summary["gps_wait_for"] == sorted(y11)
    assert [b["k"] for b in rec.summary["boots"]] == [1, 2]
    assert all(1 not in r for r in state["reboots"])


def test_too_few_judgeable_dock_cells_says_so(rig, monkeypatch):
    """판정할 수 있는 독 셀이 모자라면 위치 잡기 전에 그 이유로 멈춘다(못 잡은 셀이 없는데 '신호원을 보라'고 하지 않는다)."""
    rec, a, state, _ = rig
    monkeypatch.setattr(Q, "layout_columns", lambda name: [DOCK[:6], [], [1] + DOCK[6:10], []])
    state["heard"] = DOCK[:10] + [1, 10700, 10681]     # 독 밖 Y 두 대도 실시간 데이터가 들어온다 — 셀 수 확인은 지나간다
    with pytest.raises(SystemExit, match="판정할 수 있는 독 셀이 10대뿐"):
        I.drive(rec, lambda m: None, a)
    assert state["reboots"] == []


def test_offdock_sample_does_not_lower_the_need(rig, monkeypatch):
    """QA 2번째 반려 시나리오: 배치표 밖 시료가 켜져 있고(실시간 데이터 들어옴) 독 Y 12대 중 1대가 끝내 못 잡으면 멈춘다(지킬 것 ①).
    배치표 밖 판정 불가 셀은 필요한 셀 수를 줄이지 않는다."""
    rec, a, state, _ = rig
    state["fix_on_reboot"] = False
    real = Q.reboot

    def reboot(serials, say):
        res = real(serials, say)
        state["unfixed"].add(10694)
        return res

    monkeypatch.setattr(Q, "reboot", reboot)
    with pytest.raises(SystemExit, match="위치를 잡은 독 셀이 11대"):
        I.drive(rec, lambda m: None, a)
    assert rec.summary["gps_need"] == 12


def test_stops_when_dock_cells_cannot_fix(rig):
    rec, a, state, _ = rig
    state["fix_on_reboot"] = False
    state["unfixed"] = set(DOCK)                                       # 신호원이 꺼진 날 — 아무도 위치를 못 잡는다
    with pytest.raises(SystemExit, match="위치를 잡은 독 셀이 0대"):
        I.drive(rec, lambda m: None, a)
    assert rec.summary["boots"][-1]["stopped"] is True
