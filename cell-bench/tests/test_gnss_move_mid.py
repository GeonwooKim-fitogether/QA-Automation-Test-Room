"""GNSS-이동 속도 판정(2026-11-17) — 등속 구간 가운데 표본만 쓰고, 셀 기록 속도 칸의 단위를 mm/s 로 바꾼다. 장비 없이 돈다."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tools"):
    sys.path.insert(0, str(p))
import protocol_v4 as P  # noqa: E402
from motionbench import align as A  # noqa: E402


def scene(v=1000.0, a=8000.0, cruise=1.0, legs=6, still=2.0, dt=0.01):
    """정지 → (가속 · 등속 · 감속 · 멈춤 1초) × legs 의 장면 정답 (tt, tv)."""
    ta = v / a
    leg = 2 * ta + cruise
    tt, tv = [], []
    n = int((still + legs * (leg + 1.0)) / dt)
    for i in range(n):
        t = i * dt
        u = t - still
        s = 0.0
        if u >= 0:
            r = u % (leg + 1.0)
            s = a * r if r < ta else v if r < ta + cruise else max(0.0, a * (leg - r)) if r < leg else 0.0
        tt.append(round(t, 4)); tv.append(s)
    return tt, tv


def test_plateaus_are_the_fast_stretches():
    tt, tv = scene()
    pl = P.plateaus(tt, tv, 900.0)
    assert len(pl) == 6
    assert all(0.99 < b - a < 1.05 for a, b in pl)          # 등속 1초 + 90 % 넘는 가감속 끝 조금


def test_mid_speed_ignores_low_edges():
    """구간 앞뒤(가감속에 걸친 표본)가 낮아도 가운데 표본의 중앙값만 낸다 — 11-17 2.0 m/s 가 0.986 으로 낮게 나온 원인."""
    tt, tv = scene()
    t0 = 100.0
    sky = lambda x: A.interp(tt, tv, x - t0)
    pl = P.plateaus(tt, tv, 900.0)
    xs = [t0 + i * 0.1 for i in range(int(tt[-1] / 0.1))]
    cs = []
    for x in xs:
        u = x - t0
        k = next((j for j, (a, b) in enumerate(pl) if a <= u < b), None)
        if k is None:
            cs.append(sky(x)); continue
        frac = (u - pl[k][0]) / (pl[k][1] - pl[k][0])
        cs.append(1000.0 if 1 / 3 <= frac < 2 / 3 else 950.0)   # 가장자리는 5 % 낮다
    med, n = P.mid_speed(xs, cs, sky, t0, pl, 900.0)
    assert med == 1000.0 and n >= 12
    assert P.mid_speed(xs[:20], cs[:20], sky, t0, pl, 900.0) is None      # 표본 30개 미만


def test_ftg_sog_scale_is_km_per_h():
    """셀 기록 sog_scaled 는 km/h × 1000 — 1,000 mm/s = 3.6 km/h = 3,600."""
    assert 3600 / P.SOG_PER_MM_S == 1000.0
