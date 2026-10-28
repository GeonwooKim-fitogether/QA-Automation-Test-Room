"""셀 vs 모터 — 위치 · 시계 · 패킷 손실 (motionbench/cellstats.py). 순수 함수, 장비 없이 돈다. 기획 dynamic-bench/design/long-run.md 2-4.

위치 판정의 뜻: 셀 위치에서 왕복 한 번보다 느린 떠돎을 빼고(고역 통과), 2분 덩어리마다 '모터 1 mm 에 셀이 몇 mm 움직였나'(기울기 벡터)를
구한다. 덩어리끼리 같은 방향 · 같은 크기로 모이면(평균이 표준오차의 2배를 넘으면) '보인다'. 느린 떠돎만 있으면 덩어리마다 제각각이라 안 보인다
— 2026-10-28 15:19 처럼 한 방향 이동과 떠돎이 우연히 닮아 상관 0.97 이 나오는 것을 막는다.
"""
import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motionbench import cellstats as S  # noqa: E402


def tri(t, amp, period):
    """0 ~ amp 를 오가는 삼각파 (mm)."""
    x = (t % period) / period
    return amp * (2 * x if x < 0.5 else 2 - 2 * x)


def motor(minutes=30, amp=1500.0, period=31.0):
    t = [i * 0.01 for i in range(int(minutes * 6000))]
    return t, [tri(x, amp, period) for x in t]


def cells(mt, mp, see: bool, seed=1, angle_deg=30.0, lag=0.4, noise=300.0, walk=40.0):
    """2 Hz 셀 위치 (x 동쪽 · y 북쪽 mm) — see 면 모터 위치가 angle 방향으로 실리고, 아니면 떠돎과 잡음뿐."""
    rnd = random.Random(seed)
    ct, cx, cy, wx, wy = [], [], [], 0.0, 0.0
    c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    t = mt[0] + 1.0
    while t < mt[-1] - 1.0:
        wx += rnd.gauss(0, walk); wy += rnd.gauss(0, walk)
        m = S.interp(mt, mp, t - lag) if see else 0.0
        ct.append(t); cx.append(wx + m * c + rnd.gauss(0, noise)); cy.append(wy + m * s + rnd.gauss(0, noise))
        t += 0.5
    return ct, cx, cy


def test_degrees_to_mm_near_seoul():
    x, y = S.to_mm(37.5 + 1e-5, 127.0 + 1e-5, 37.5, 127.0)
    assert y == pytest.approx(1113.2, abs=1) and x == pytest.approx(1113.2 * math.cos(math.radians(37.5)), abs=1)


def test_highpass_keeps_shuttle_and_drops_drift():
    t = [i * 0.5 for i in range(2000)]
    v = [tri(x, 1000, 30) + 5 * x for x in t]                    # 삼각파 + 느린 떠돎(5 mm/s)
    ht, hv = S.highpass(t, v, 30)
    assert ht[0] >= t[0] + 15 and ht[-1] <= t[-1] - 15          # 창이 다 차지 않는 양 끝은 버린다
    want = [tri(x, 1000, 30) - 500 for x in ht]
    assert max(abs(a - b) for a, b in zip(hv, want)) < 20


def test_motor_period_from_reversals():
    mt, mp = motor(minutes=3)
    assert S.motor_period(mt, mp) == pytest.approx(31.0, abs=0.5)
    assert S.motor_period(mt[:100], mp[:100]) is None            # 왕복이 없으면 None


def test_position_seen_when_cell_carries_motor():
    mt, mp = motor()
    ct, cx, cy = cells(mt, mp, see=True)
    r = S.position_fit(ct, cx, cy, mt, mp, 31.0)
    assert r["seen"] is True, r
    assert r["k"] == pytest.approx(1.0, abs=0.15) and r["dir_deg"] == pytest.approx(30, abs=8) and r["lag_s"] == pytest.approx(0.4, abs=0.3)
    assert r["blocks"] == 15


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_position_not_seen_from_drift_and_noise_alone(seed):
    mt, mp = motor()
    ct, cx, cy = cells(mt, mp, see=False, seed=seed)
    r = S.position_fit(ct, cx, cy, mt, mp, 31.0)
    assert r["seen"] is False, r


def test_position_needs_three_blocks():
    mt, mp = motor(minutes=5)
    ct, cx, cy = cells(mt, mp, see=True)
    r = S.position_fit(ct, cx, cy, mt, mp, 31.0)
    assert r["blocks"] == 2 and r["seen"] is None              # 판정하지 않는다 — 덩어리 2개로는 흩어짐을 잴 수 없다


def test_clock_offset_and_drift():
    t = [1000.0 + i * 0.5 for i in range(3600)]
    utc = [x - 0.3 - 10e-6 * (x - 1000.0) for x in t]            # 셀 시계가 PC 보다 0.3초 늦고 10 ppm 씩 더 늦어진다
    r = S.clock(t, utc)
    assert r["offset_ms"] == pytest.approx(300 + 10e-6 * 900 * 1000, abs=1) and r["drift_ppm"] == pytest.approx(10, abs=0.1)
    assert S.clock(t[:1], utc[:1])["drift_ppm"] is None


def test_packet_loss_from_sequence():
    t = [i * 0.5 for i in range(20)]
    seq = [s for s in range(100, 125) if s not in (103, 104, 110, 111, 112)]   # 25 개 중 5 개 빠짐
    r = S.loss(t, seq)
    assert (r["expected"], r["got"], r["lost"]) == (25, 20, 5) and r["lost_pct"] == pytest.approx(20.0)


def test_segments_split_by_time():
    t = [i * 10.0 for i in range(100)]                           # 0 ~ 990 초
    assert [len(x) for x in S.segments(t, 300)] == [30, 30, 30, 10]
