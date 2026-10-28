"""셀별 비교 (motionbench/percell.py) — 셀 하나가 모터의 거리 · 속도 · 가속도 · 움직임을 어떻게 읽었나. 순수 함수, 장비 없이 돈다.

정답은 모터다(M3 · 오래 왕복 1차로 검증). 앱 방식은 결과판 세션의 Live 앱 대조표(1.14.0)를 따른다:
거리 = 속도 이력 × 0.1초의 합(무효 표시는 0), 가속도 = 속도 이력에 11칸 3차 Savitzky–Golay 1차 미분.
"""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motionbench import percell as P  # noqa: E402

V, D, DW = 100.0, 1500.0, 5.0             # mm/s · mm · 끝 멈춤 초
LEG = D / V


def motor(n_legs=6, dt=0.01):
    """0 에서 멈춤 DW → D 로 등속(가속 무시) → 멈춤 DW → 0 … (위치 mm)."""
    t, p, x, pos, sign = [], [], 0.0, 0.0, 1
    for _ in range(n_legs):
        for _ in range(int(DW / dt)):
            t.append(x); p.append(pos); x += dt
        for _ in range(int(LEG / dt)):
            pos += sign * V * dt
            t.append(x); p.append(pos); x += dt
        sign = -sign
    for _ in range(int(DW / dt)):
        t.append(x); p.append(pos); x += dt
    return t, p


def test_dwells_and_legs_from_motor():
    t, p = motor()
    dw = P.dwells(t, p)
    assert len(dw) == 7 and all(abs((b - a) - DW) < 0.3 for a, b, _ in dw)
    assert [round(x) for *_, x in dw] == [0, 1500, 0, 1500, 0, 1500, 0]
    assert len(P.leg_list(dw)) == 6


def test_app_distance_sums_speed_times_tenth_second_and_zeroes_no_value():
    ts = [i * 0.1 for i in range(150)]
    assert P.app_distance(ts, [100] * 150, 0, 14.95) == pytest.approx(1500)
    assert P.app_distance(ts, [100] * 149 + [0xFFFF], 0, 14.95) == pytest.approx(1490)   # 앱처럼 무효 표시는 0


def test_sg_accel_is_exact_on_a_ramp():
    vs = [50 * i * 0.1 for i in range(30)]                    # 50 mm/s² 로 늘어나는 속도
    a = P.sg_accel(vs, 0.1)
    assert a[:5] == [None] * 5 and a[-5:] == [None] * 5
    assert all(x == pytest.approx(50) for x in a[5:-5])


def test_flag_stats_on_off_and_lags():
    t, p = motor()
    dw = P.dwells(t, p)
    ts = [i * 0.1 for i in range(int(t[-1] / 0.1))]
    moving = lambda x: any(b + 0.4 <= x < c + 0.4 for (_, b, _), (c, _, _) in zip(dw, dw[1:]))   # 0.4초 늦게 켜지고 꺼진다
    vs = [80 if moving(x) else 0 for x in ts]
    r = P.flag_stats(ts, vs, dw)
    assert r["on_moving_pct"] == pytest.approx(100, abs=1) and r["on_dwell_pct"] == pytest.approx(0, abs=1)
    assert r["lag_on_s"] == pytest.approx(0.4, abs=0.11) and r["lag_off_s"] == pytest.approx(0.4, abs=0.11)


def test_flag_stats_has_no_lags_when_dwell_is_too_short():
    t = [i * 0.01 for i in range(2000)]
    p = [P_ % 1000 for P_ in range(2000)]                    # 쉬지 않고 움직인다
    r = P.flag_stats([i * 0.1 for i in range(200)], [5] * 200, P.dwells(t, p))
    assert r["lag_off_s"] is None and r["on_dwell_pct"] is None


def test_position_legs_projects_on_rail_bearing():
    t, p = motor()
    dw = P.dwells(t, p)
    bearing = math.radians(125)
    ct = [i * 0.5 for i in range(int(t[-1] / 0.5))]
    m = [P.interp(t, p, x) for x in ct]
    cx = [v * math.cos(bearing) for v in m]
    cy = [v * math.sin(bearing) for v in m]
    legs = P.position_legs(ct, cx, cy, P.leg_list(dw), bearing)
    assert len(legs) == 6 and all(c == pytest.approx(mo, abs=1) and abs(mo) == pytest.approx(1500, abs=1) for c, mo in legs)
    assert P.pooled_bearing([P.leg_vectors(ct, cx, cy, P.leg_list(dw))]) == pytest.approx(bearing)   # 오가는 두 방향을 모터 부호로 맞춰 하나로


def test_pooled_bearing_is_not_cancelled_by_a_cell_that_sees_nothing():
    seen = [(0.0, 1500.0, 1500.0), (0.0, -1500.0, -1500.0)]              # 북쪽으로 오간다
    blind = [(0.0, -20.0, 1500.0), (0.0, 25.0, -1500.0)]                  # 잡음 — 평균 벡터가 짧고 남쪽
    assert math.degrees(P.pooled_bearing([seen, blind])) == pytest.approx(90, abs=2)
