"""compare_motion_cell — 오래 왕복 기록에 위치 · 속도(움직일 때 vs 멈췄을 때) · 시계 · 패킷 손실 절을 낸다. 가짜 기록으로 끝에서 끝까지.

가짜 셀 3대: 11001 · 11002 는 위치에 모터 왕복이 북쪽으로 실리고(+ 떠돎 · 잡음), 11003 은 떠돎 · 잡음뿐.
11002 는 순번 10개가 빠진다. 셀 시계는 PC 보다 0.3초 늦다. 모터가 출발한 직후 3개는 위치를 못 잡은 패킷(fix N · 위도 · 경도 0 · 1980년)이다.
원래 속도(41–42, mm/s)는 보이는 셀에 모터 |속도| + 잡음, 안 보이는 셀에 잡음만 싣는다.
"""
import argparse
from collections import deque
import math
import random
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "tools"))
import compare_motion_cell as C  # noqa: E402
from motionbench import cia402  # noqa: E402
from motionbench.live_tap import REC  # noqa: E402
from motionbench.motion_record import Log  # noqa: E402

T0, MIN, AMP, PER = 1_793_170_000.0, 14.0, 1500.0, 31.0   # 14분 → 2분 덩어리 7개
LAT0, LON0 = 37.5, 127.0


def tri(t):
    x = (t % PER) / PER
    return AMP * (2 * x if x < 0.5 else 2 - 2 * x)


def nmea(deg):
    a = abs(deg)
    return (-1 if deg < 0 else 1) * (int(a) * 10**10 + round((a - int(a)) * 60 * 1e8))


def pkt(serial, seq, t_utc, lat, lon, fix=b"A", hspeed=0):
    d = bytearray(1320); d[:4] = b"FITO"; d[4] = 0x09
    struct.pack_into("<I", d, 7, seq); struct.pack_into("<H", d, 11, serial)
    from datetime import datetime, timezone
    u = datetime.fromtimestamp(t_utc, timezone.utc)
    struct.pack_into("<HBBI", d, 13, u.year, u.month, u.day, u.hour * 10**6 + u.minute * 10**4 + u.second * 100 + u.microsecond // 10_000)
    struct.pack_into("<qq", d, 21, nmea(lat), nmea(lon))
    struct.pack_into("<H", d, 41, hspeed)
    d[47] = fix[0]; d[48] = 12 << 3; d[49] = 36; d[55] = 0x9F
    return bytes(d)


def files(tmp_path):
    motion, live = tmp_path / "motion_long_x.csv", tmp_path / "live.bin"
    log, rows, t = Log(motion, expected_wkc=3), deque(), 0.0
    while t < MIN * 60:
        rows.append((T0 + t, int(t * 1e9), 1150, 3, cia402.Inputs(0, 0x1637, 0, round(tri(t) * 1000), 0, 0, 0, 0)))
        t += 0.02
    log.take(rows)
    log.close()
    rnd = random.Random(7)
    with open(live, "wb") as f:
        for serial, see in ((11001, True), (11002, True), (11003, False)):
            walk, seq, t = 0.0, 500, -20.0                                       # 모터보다 20초 먼저 켠다 — 멈춘 구간
            while t < MIN * 60 + 20:
                walk += rnd.gauss(0, 30)
                north = walk + (tri(t - 0.5) if see and 0 <= t - 0.5 <= MIN * 60 else 0) + rnd.gauss(0, 250)
                lat = LAT0 + north / 1000 / 111_320.0
                seq += 1
                v = abs(tri(t + 0.25) - tri(t - 0.25)) / 0.5 if see and 0 <= t <= MIN * 60 else 0.0
                hs = max(0, round(v + rnd.gauss(0, 20)))
                if 0 <= t < 1.5:                                                    # 모터 기록 안의 위치를 못 잡은 3개
                    d = pkt(serial, seq, 315_532_800.0 + seq, 0.0, 0.0, b"N", hs)
                else:
                    d = pkt(serial, seq, T0 + t - 0.3, lat, LON0, b"A", hs)
                if not (serial == 11002 and 600 <= seq < 610):
                    f.write(REC.pack(round((T0 + t) * 1e6), 1320) + d)
                t += 0.5
    return motion, live


def args(motion, live):
    return argparse.Namespace(motion=str(motion), live=str(live), imu=[], out=None, max_lag=5.0, imu_max_lag=30.0,
                              speed_fmt="<H", newest_first=False)


def test_long_record_gets_position_clock_and_loss_sections(tmp_path):
    md, rows = C.report(args(*files(tmp_path)))
    assert "## 위치 (위도 · 경도)" in md and "왕복 주기 31.0 초" in md
    assert "보인다 2/3대" in md, md
    by = {r["cell"]: r for r in rows if r.get("kind") == "cell"}
    assert by[11001]["pos_seen"] is True and by[11003]["pos_seen"] is False
    assert abs(by[11001]["pos_k"] - 1) < 0.2 and abs(abs(by[11001]["pos_dir_deg"]) - 90) < 10   # 북쪽
    assert by[11002]["loss_lost"] == 10 and by[11001]["loss_lost"] == 0
    assert abs(by[11001]["clock_offset_ms"] - 300) < 5
    assert "## 시계 · 패킷 (5분마다)" in md
    inside = sum(1 for k in range(int((MIN * 60 + 40) / 0.5)) if 0 <= -20 + k * 0.5 <= MIN * 60)   # 모터 기록 안 · 위치 잡힌 것
    assert by[11001]["pos_n"] == inside - 3, (by[11001]["pos_n"], inside)
    assert abs(by[11001]["clock_drift_ppm"]) < 1                                  # 1980년 시각이 기울기를 망치지 않는다
    assert abs(by[11001]["hspeed_moving_med"] - 1500 / 15.5) < 8 and by[11001]["hspeed_still_med"] < 30
    assert by[11003]["hspeed_moving_med"] < 30


# ---------------------------------------------------------------- 셀별 절 — 끝 멈춤이 있는 왕복 (2차 모양)

DW2, V2, D2, N2 = 5.0, 100.0, 1500.0, 8


def motor2():
    """멈춤 5초 → 1.5 m 등속 100 mm/s → 멈춤 … 8번 (가속은 무시)."""
    t, p, x, pos, sign, dt = [], [], 0.0, 0.0, 1, 0.02
    for _ in range(N2):
        for _ in range(int(DW2 / dt)):
            t.append(x); p.append(pos); x += dt
        for _ in range(int(D2 / V2 / dt)):
            pos += sign * V2 * dt
            t.append(x); p.append(pos); x += dt
        sign = -sign
    for _ in range(int(DW2 / dt)):
        t.append(x); p.append(pos); x += dt
    return t, p


def files2(tmp_path):
    t, p = motor2()
    motion, live = tmp_path / "motion_long_y.csv", tmp_path / "live2.bin"
    log = Log(motion, expected_wkc=3)
    log.take(deque((T0 + a, int(a * 1e9), 1150, 3, cia402.Inputs(0, 0x1637, 0, round(b * 1000), 0, 0, 0, 0)) for a, b in zip(t, p)))
    log.close()
    from motionbench.align import interp
    rnd = random.Random(11)
    base = [-70 + j * 0.1 for j in range(int((t[-1] + 90) / 0.1))]     # 10 Hz 바탕 — 모터 앞 70초부터
    speed = lambda x: abs(interp(t, p, x + 0.05) - interp(t, p, x - 0.05)) / 0.1 if 0 <= x <= t[-1] else 0.0
    with open(live, "wb") as f:
        for serial, see in ((12001, True), (12002, False)):
            hist = [max(0, round(speed(x - 0.4) + rnd.gauss(0, 5))) if see and speed(x - 0.4) > 1 else 0 for x in base]   # 0.4초 늦게 켜지고 꺼짐
            seq = 0
            for j in range(600, len(base) + 1, 5):
                x = base[j - 1]
                north = interp(t, p, x - 0.3) + rnd.gauss(0, 30) if see and 0 <= x <= t[-1] else rnd.gauss(0, 30)
                d = bytearray(pkt(serial, seq := seq + 1, T0 + x - 0.3, LAT0 + north / 1000 / 111_320.0, LON0, b"A", round(speed(x))))
                struct.pack_into("<600H", d, 57, *hist[j - 600:j])
                f.write(REC.pack(round((T0 + x) * 1e6), 1320) + bytes(d))
    return motion, live


def test_per_cell_section_reads_distance_speed_accel_and_flag(tmp_path):
    md, rows = C.report(args(*files2(tmp_path)))
    assert "## 셀별 — 모터 정답과 나란히" in md, md
    by = {r["cell"]: r for r in rows if r.get("kind") == "cell"}
    a, b = by[12001], by[12002]
    assert a["pc_legs"] == N2 and abs(a["pc_pos_dist_med"] - D2) < 50 and abs(b["pc_pos_dist_med"]) < 300
    assert abs(a["pc_app_dist_med"] - D2) < 60 and b["pc_app_dist_med"] == 0
    assert a["pc_flag_moving_pct"] > 95 and a["pc_flag_dwell_pct"] < 5 and abs(a["pc_lag_on_s"] - 0.4) < 0.25 and abs(a["pc_lag_off_s"] - 0.4) < 0.25
    assert a["pc_app_acc_peak_med"] > 30 and abs(a["pc_app_acc_peak_med"] - a["pc_motor_acc_peak_med"]) < 0.3 * a["pc_motor_acc_peak_med"]
