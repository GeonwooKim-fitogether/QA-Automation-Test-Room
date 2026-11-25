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


# ── 셀 기록 표본을 셀 GPS 시각으로 고른다(2026-11-25 QA 동의 · 조건 다섯) ─────────────────────────────────
UTC0 = 1793376000.0          # 2026-10-30 16:00:00 UTC — S12 장면 정의(GPS_Start_Time_in_sec 57600)


def receiver(tt, tv, lag=0.0, late_excess=0.0, dt=0.1):
    """가짜 셀 기록(0.1초마다) — (셀 GPS 시각, 속도 mm/s). lag 만큼 늦게 따라오고, 등속 구간의 뒤 1/3 에서 late_excess 만큼 높게 낸다
    (11-25 1.0 m/s 에서 창을 늦게 놓을수록 오차가 커진 모양을 흉내 낸다)."""
    plats = P.plateaus_closed(tt, tv, 900.0)
    ws, cs = [], []
    t = 0.0
    while t <= tt[-1]:
        v = A.interp(tt, tv, t - lag)
        if any(a0 + 2 * (b0 - a0) / 3 <= t < b0 for a0, b0 in plats):
            v += late_excess
        ws.append(UTC0 + t); cs.append(v)
        t = round(t + dt, 4)
    return ws, cs


def test_synthetic_run_with_no_lag_gives_zero_error():
    """알려진 장면 · 늦음 0 의 가짜 셀 기록 → 가운데 표본 중앙값이 장면 속도와 같다(오차 0)."""
    tt, tv = scene()
    ws, cs = receiver(tt, tv)
    mid = P.mid_speed_gps(ws, cs, UTC0, P.plateaus_closed(tt, tv, 900.0))
    assert mid is not None and abs(mid[0] - 1000.0) < 1e-6 and mid[1] >= 6 * 3


def test_no_lag_correction_so_a_late_receiver_shows_its_lag_at_the_edges_only():
    """늦음 보정을 넣지 않는다 — 같은 시각의 속도를 견준다. 가운데 1/3 은 늦음 0.2초에도 등속 안이라 오차 0 그대로."""
    tt, tv = scene()
    ws, cs = receiver(tt, tv, lag=0.2)
    mid = P.mid_speed_gps(ws, cs, UTC0, P.plateaus_closed(tt, tv, 900.0))
    assert abs(mid[0] - 1000.0) < 1e-6


def test_window_is_not_moved_by_a_lag_estimate():
    """등속 구간 뒤 1/3 에서 높게 내는 수신기 — 셀 GPS 시각 그대로 고르면 가운데 중앙값은 1000 그대로다.
    창을 0.25초 늦게 놓으면(예전 방식이 실시간 패킷 늦음 · 장면 시작 추정으로 한 일) 높은 표본이 섞여 오차가 생긴다."""
    tt, tv = scene(cruise=1.0)
    ws, cs = receiver(tt, tv, late_excess=20.0)
    plats = P.plateaus_closed(tt, tv, 900.0)
    assert abs(P.mid_speed_gps(ws, cs, UTC0, plats)[0] - 1000.0) < 1e-6
    late = P.mid_speed_gps([w - 0.25 for w in ws], cs, UTC0, plats)      # 표본을 0.25초 이르게 놓음 = 창을 0.25초 늦게 놓음
    assert late[0] > 1000.0


def test_scene_start_comes_from_the_scene_definition(tmp_path):
    (tmp_path / "satgen_profile.txt").write_text("[Receiver Dynamics]\nGPS_Start_Time_in_sec=57600\nSimulation_Day=30\n"
                                                 "Simulation_Mon=10\nSimulation_Year=2026\n", encoding="utf-8")
    (tmp_path / "S_truth.csv").write_text("t_s,pos_mm,v_mm_s\n", encoding="utf-8")
    assert P.scene_utc0_profile(tmp_path / "S_truth.csv") == UTC0
    assert P.scene_utc0_profile(tmp_path / "nowhere" / "x.csv") is None


def test_closed_plateau_ends_at_the_last_fast_sample():
    """셀 기록 경로의 등속 구간 끝은 90 % 이상인 마지막 표본 — plateaus() 보다 정답 한 칸(0.01초) 짧다."""
    tt, tv = scene()
    old, new = P.plateaus(tt, tv, 900.0), P.plateaus_closed(tt, tv, 900.0)
    assert len(old) == len(new) == 6
    assert all(abs((ob - nb) - 0.01) < 1e-6 and oa == na for (oa, ob), (na, nb) in zip(old, new))
