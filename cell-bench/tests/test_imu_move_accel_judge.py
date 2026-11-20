"""IMU-MOVE 운전-가속 판정(tools/qc_tests/imu_move_accel_judge.py) — 장비 없이 만든 삼각형 속도 운전과 가짜 IMU 로 계산만 본다.
numpy 가 있는 파이썬에서만 돈다(판정 도구가 numpy 를 쓴다 — 제어 PC 의 판정용 3.10). 없으면 건너뛴다."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "qc_tests"))
import imu_move_accel_judge as M  # noqa: E402

A_MM_S2, D_MM, LEGS = 2000.0, 1200.0, 10


def triangle_run(t_start=100.0, a=A_MM_S2, d=D_MM, legs=LEGS, still=2.0, dt=0.002, jitter=0.0003, seed=1, pause=0.15):
    """멈춤 still 초 → 왕복(다리 legs 개, 다리마다 가속 직후 감속, 끝에서 pause 초 멈춤 — 실제 모터처럼) → 멈춤 still 초.
    (시각, 위치 mm, 참 가속도 함수, 다리 시간)."""
    T = 2 * np.sqrt(d / a)                       # 다리 하나 시간
    TP = T + pause
    rng = np.random.default_rng(seed)
    t = np.arange(t_start - still, t_start + legs * TP + still, dt) + rng.normal(0, jitter, 1)[0]

    def state(x):
        x = np.asarray(x, float)
        k = np.clip(np.floor((x - t_start) / TP), 0, legs - 1)
        tau = np.clip(x - t_start - k * TP, 0, T)
        sgn = np.where(k % 2 == 0, 1.0, -1.0)
        half = T / 2
        pos_in = np.where(tau <= half, 0.5 * a * tau ** 2, d - 0.5 * a * (T - tau) ** 2)
        base = np.where(k % 2 == 0, 0.0, d)
        p = base + sgn * pos_in
        p = np.where(x < t_start, 0.0, np.where(x > t_start + legs * TP, d if legs % 2 else 0.0, p))
        in_leg = (x - t_start - k * TP) <= T
        acc = np.where(tau <= half, a, -a) * sgn * in_leg
        acc = np.where((x < t_start) | (x > t_start + legs * TP), 0.0, acc)
        return p, acc

    p, _ = state(t)
    return t, p, (lambda x: state(x)[1]), T


def test_starts_finds_still_and_turn_starts_with_motor_speed_change():
    t, p, _, T = triangle_run()
    s = M.starts(t, p)
    ev = s["events"]
    assert len(ev) == LEGS and ev[0][3] == "멈춤" and all(k == "방향" for *_, k in ev[1:])
    vpk = np.sqrt(A_MM_S2 * D_MM)                # 삼각형 최고 속도 = √(a·d)
    assert all(abs(abs(dv) - vpk) / vpk < 0.01 for _, _, dv, _ in ev)
    assert [np.sign(dv) for *_, dv, _ in [(e[0], e[1], e[2], e[3]) for e in ev]][:2] == [1.0, -1.0]
    assert abs(s["span"][0] - 100.0) < 0.005 and abs((s["span"][1] - s["span"][0]) - (LEGS * T + (LEGS - 1) * 0.15)) < 0.01
    assert all(abs((t1 - t0) - T / 2) < 0.005 for t0, t1, _, _ in ev)              # 끝 멈춤(0.15초)은 출발 구간에 들지 않는다


def test_starts_without_pause_at_the_ends():
    t, p, _, T = triangle_run(pause=0.0)
    ev = M.starts(t, p)["events"]
    vpk = np.sqrt(A_MM_S2 * D_MM)
    assert len(ev) == LEGS and all(abs(abs(dv) - vpk) / vpk < 0.02 for _, _, dv, _ in ev)


def fake_imu(acc_fn, t0, t1, gain=1.04, clock_ahead=0.12, seed=2):
    """레일 가속도를 센서 좌표로 돌려 gain 배로 재고, 중력 · 영점 · 잡음을 더한 0.01초 표본. IMU 시각은 clock_ahead 초 앞선다."""
    rng = np.random.default_rng(seed)
    ts = np.arange(t0, t1, 0.01)
    rail = np.array([0.6, 0.8, 0.0])                         # 레일 방향(센서 좌표)
    g = np.array([0.0, 0.0, 9806.65]) + np.array([30.0, -20.0, 15.0])
    a = np.outer(gain * acc_fn(ts + 0.005), rail) + g + rng.normal(0, 20.0, (len(ts), 3))
    return M.Imu(ts + 0.005 + clock_ahead, a, np.full(len(ts), 0.01))


def test_judge_stage_recovers_gain_and_clock_shift():
    runs, imus = [], []
    for k in range(3):
        t, p, acc, T = triangle_run(t_start=100.0 + 60 * k, seed=10 + k)
        runs.append(M.starts(t, p))
    t_all = np.arange(95.0, 100.0 + 60 * 3 + 30, 0.01)
    _, _, acc0, _ = triangle_run(t_start=100.0)
    acc_fn = lambda x: sum(triangle_run(t_start=100.0 + 60 * k)[2](x) for k in range(3))
    imu = fake_imu(acc_fn, t_all[0], t_all[-1])
    r = M.judge_stage(imu, runs)
    assert r["n"] == 3 * LEGS and r["n_still"] == 3 and r["n_turn"] == 27
    # IMU 를 0.01초 간격으로 재므로 출발 · 꼭짓점에서 반 칸씩 어긋나 약 0.5 ~ 1 % 작게 나온다(실제 셀도 같다) — 그만큼 허용
    assert abs(r["ratio"] - 1.04) < 0.015 and abs(r["ratio_still"] - 1.04) < 0.02 and abs(r["ratio_turn"] - 1.04) < 0.015
    assert abs(r["lag"] + 0.12) <= 0.01                     # IMU 시각이 0.12초 앞서면 창을 0.12초 뒤로
    assert r["side"] < 0.03 and abs(abs(r["u"][0]) - 0.6) < 0.02


def test_imu_integral_reads_inside_a_sample():
    imu = M.Imu(np.array([0.005, 0.015, 0.025]), np.array([[100.0, 0, 0], [200.0, 0, 0], [300.0, 0, 0]]), np.full(3, 0.01))
    assert abs(imu.integral(0.0, 0.015)[0] - (100 * 0.01 + 200 * 0.005)) < 1e-9     # 표본 가운데에서 끊어도 그만큼만


def test_stage_key():
    assert M.stage_key("03_a4000") == 4000 and M.stage_key("long") is None
