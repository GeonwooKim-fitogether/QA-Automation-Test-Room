"""모터 M1(읽기 전용) 순수 함수 — 장비 없이 돈다.

출력 PDO 의 Controlword 가 늘 0 인지, 예상 밖 Statusword 면 멈추는지, 주기 통계와 기록이 맞는지만 본다.
pysoem 은 가져오지 않는다(ecat.py 를 부르지 않는다) — pysoem 이 없는 PC 에서도 cell-bench 시험이 돈다.
"""
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motionbench import cia402
from motionbench.motion_record import Log, cycle_stats


def _v(sw, err=0):
    return cia402.Inputs(err, sw, 0, 0, 0, 0, 0, 0)


def test_output_is_always_controlword_zero():
    for pos in (0, 1, -1_080_400, 687_339, 2**31 - 1, -2**31):
        raw = cia402.m1_output(pos)
        assert (len(raw), *cia402.OUT.unpack(raw)) == (9, 0, 0, pos, 0)     # Controlword · Mode · Target · 0x60B8


def test_layout_matches_record():
    assert cia402.OUT.size == sum(b for *_, b in cia402.RX_MAP) // 8 == 9   # records §7
    assert cia402.IN.size == sum(b for *_, b in cia402.TX_MAP) // 8 == 23


def test_state_names():
    cases = {0x0000: "NOT_READY_TO_SWITCH_ON", 0x0250: "SWITCH_ON_DISABLED", 0x0231: "READY_TO_SWITCH_ON",
             0x0233: "SWITCHED_ON", 0x0237: "OPERATION_ENABLED", 0x0217: "QUICK_STOP_ACTIVE",
             0x021F: "FAULT_REACTION_ACTIVE", 0x0218: "FAULT", 0x0001: "UNKNOWN"}
    assert {sw: cia402.state(sw) for sw in cases} == cases


def test_m1_stops_on_anything_but_idle():
    assert cia402.m1_problem(None, _v(0x0250)) is None
    assert cia402.m1_problem("NOT_READY_TO_SWITCH_ON", _v(0x0250)) is None          # 켜지는 중 → 정상
    for sw in (0x0237, 0x0233, 0x0231, 0x0217, 0x021F, 0x0218, 0x0001):
        assert cia402.m1_problem("SWITCH_ON_DISABLED", _v(sw)), hex(sw)
    assert cia402.m1_problem("SWITCH_ON_DISABLED", _v(0x0200))                      # 거꾸로 가는 전이
    assert cia402.m1_problem("SWITCH_ON_DISABLED", _v(0x0250, err=0xFF00))          # 0x603F ≠ 0


def test_cycle_stats_nearest_rank():
    s = cycle_stats([2.0] * 98 + [5.0, 30.0])
    assert (s["n"], s["p50"], s["p99"], s["max"], s["late"], s["over_20ms"]) == (100, 2.0, 5.0, 30.0, 2, 1)
    assert cycle_stats([1.0, 2.5, 3.0], late_ms=2.0)["late"] == 2
    assert cycle_stats([]) == {"n": 0}


def test_log_counts_wkc_errors_and_writes_every_cycle(tmp_path):
    rows = deque([(1.0, 0, 1150, 3, _v(0x0250)), (1.002, 2_000_000, 1150, 3, _v(0x0250)),
                  (1.006, 6_000_000, 20000, -1, None)])
    log = Log(tmp_path / "m.csv", expected_wkc=3)
    log.take(rows)
    log.close()
    s = log.summary()
    assert (s["cycles"], s["wkc_err"], s["wkc_ok"], s["cycle_ms"]["max"], s["rtt_ms"]["max"]) == (3, 1, False, 4.0, 20.0)
    assert s["seen"]["statusword"] == {"0x0250": 2}
    assert len((tmp_path / "m.csv").read_text(encoding="utf-8").splitlines()) == 4  # 머리 + 3줄
