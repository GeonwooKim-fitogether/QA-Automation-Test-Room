"""dynamic-bench 내일 슬라이스 — 오프라인 검사. 장비 · 셀 · 실제 포트(60222)에 붙지 않는다.

② 모터 2 ms 기록: M1 의 motion_record.Log 가 쓴 CSV 를 align.load_motion 이 그대로 읽는다 (t_wall = 셀 수신 시각과 같은 PC 시계).
③ 정렬 · 비교: 알려진 오프셋 · 드리프트 · 잡음을 넣은 가짜 모터/셀 기록에서 그 값을 되찾는다 — compare_motion_cell 을 끝에서 끝까지.
④ 셀 종류: 등록부 · 혼재 · 모름 · 모순.   + live_tap: 가짜 UDP 를 127.0.0.1 의 임의 포트로만 보낸다.
"""
import csv
import math
import random
import socket
import struct
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import compare_motion_cell as C  # noqa: E402
from cellbench import cells  # noqa: E402
from cellbench.config import Config  # noqa: E402
from motionbench import align as A  # noqa: E402
from motionbench import celltype, cia402  # noqa: E402
from motionbench.live_tap import REC, LiveTap, read_raw  # noqa: E402
from motionbench.motion_record import Log  # noqa: E402

V, AM = 10.0, 50.0             # 슬라이스의 느린 상한 mm/s · mm/s²
T0 = 1792700000.0              # 가짜 모터 기록의 시작 — 2026-10-23 05:13:20 KST


def _moves():
    """동기 A(10 mm 왕복 3회, 간격을 다르게) → 20 초 정지 → 30 mm 왕복 2회 → 8 초 정지 → 동기 B. [(시작 초, 거리, 방향)], 끝 초."""
    out, s = [], 2.0
    for dist, gaps, rest in ((10, (0.7, 1.3, 0), 20), (30, (0.5, 0), 8), (10, (0.7, 1.3, 0), 2)):
        assert dist >= V * V / AM, "사다리꼴만 그린다 — 거리가 짧으면 등속 구간이 음수가 된다"
        T = 2 * V / AM + (dist - V * V / AM) / V
        for g in gaps:
            out += [(s, dist, 1), (s + T + 0.3, dist, -1)]
            s += 2 * T + 0.3 + g
        s += rest
    return out, s


MOVES, DUR = _moves()


def traj(t: float):
    """T0 부터 t 초 → (위치 mm, 속도 mm/s, 가속도 mm/s²) — 사다리꼴 이동을 식으로 (검사 대상 코드를 쓰지 않는다)."""
    x = v = a = 0.0
    ta = V / AM
    for s, dist, sg in MOVES:
        T, u = 2 * ta + (dist - V * V / AM) / V, t - s
        if u <= 0:
            continue
        if u < ta:
            dx, dv, da = 0.5 * AM * u * u, AM * u, AM
        elif u < T - ta:
            dx, dv, da = 0.5 * AM * ta * ta + V * (u - ta), V, 0.0
        elif u < T:
            dx, dv, da = dist - 0.5 * AM * (T - u) ** 2, AM * (T - u), -AM
        else:
            dx, dv, da = dist, 0.0, 0.0
        x, v, a = x + sg * dx, v + sg * dv, a + sg * da
    return x, v, a


def _motor_csv(path, moving=True):
    """M1 의 Log 로 가짜 2 ms 주기 기록을 쓴다 — 주기 흔들림 ±0.3 ms, 마지막에 WKC 오류 줄 하나."""
    rnd, rows, t = random.Random(1), deque(), 0.0
    while t < (DUR if moving else 1.0):
        pos = round(traj(t)[0] * 1000) if moving else -3972
        rows.append((T0 + t, int(t * 1e9), 1150, 3, cia402.Inputs(0, 0x0250, 0, pos, 0, 0, 0, 0)))
        t += 0.002 + rnd.uniform(-0.0003, 0.0003)
    rows.append((T0 + t, int(t * 1e9), 20000, -1, None))
    log = Log(path, expected_wkc=3)
    log.take(rows)
    log.close()


def _gps_pkt(serial, hist, fix=b"R", hspeed=0):
    d = bytearray(1320); d[:4] = b"FITO"; d[4] = 0x09
    struct.pack_into("<H", d, 11, serial); d[41] = hspeed; d[47] = fix[0]; d[48] = 12 << 3; d[49] = 40; d[55] = 7
    struct.pack_into("<600H", d, 57, *hist)
    return bytes(d)


def _live_raw(path, lags, no_fix=()):
    """live_tap 과 같은 꼴의 파일. 셀이 PC 시각 τ 에 보고하는 속도 = 모터의 (τ − lag) 때 |속도| + 잡음(σ 0.5 mm/s, 정수).
    10 Hz 바탕 표본을 0.5 초마다 600 칸 창으로 싣는다(끝이 최신) — 같은 표본은 패킷이 달라도 같은 값. hspeed = 2 × |속도| (단위를 모르는 척)."""
    rnd = random.Random(2)
    base = [T0 - 70 + j * 0.1 for j in range(int((DUR + 80) / 0.1))]
    with open(path, "wb") as f:
        for serial in [*lags, *no_fix]:
            lag = lags.get(serial)
            vals = [max(0, round(abs(traj(x - lag - T0)[1]) + rnd.gauss(0, 0.5))) if lag is not None else 0 for x in base]
            for j in range(600, len(base) + 1, 5):
                t = base[j - 1] + rnd.uniform(-0.005, 0.005)
                h = min(255, round(2 * abs(traj(t - lag - T0)[1]))) if lag is not None else 0
                d = _gps_pkt(serial, vals[j - 600:j], b"R" if lag is not None else b"N", h)
                f.write(REC.pack(round(t * 1e6), len(d)) + d)


def _imu_csv(path, L0, d, k=-0.4177, b=120.0):
    """FTG 를 CSV 로 내보낸 꼴(시각 = UTC ISO, 시간대 없음). 셀 시계 τ = 모터 + L0 + d·(모터 − T0).
    acc_y = k·가속도 + b + 잡음 8(≈ 19 mm/s²) — 축이 반대(k<0), ±8 g 16비트 배율. acc_x · acc_z 는 잡음뿐, gyro 는 고르지 않아야 한다."""
    rnd = random.Random(3)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["datetime", "acc_x", "acc_y", "acc_z", "gyro_x"])
        for j in range(int((DUR * (1 + d) + 2) / 0.01)):
            tau = T0 + L0 - 1 + j * 0.01
            a = traj((tau - T0 - L0) / (1 + d))[2]
            w.writerow([datetime.fromtimestamp(tau, timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="milliseconds"),
                        round(rnd.gauss(0, 8)), round(k * a + b + rnd.gauss(0, 8)), round(4096 + rnd.gauss(0, 8)), round(rnd.gauss(0, 50))])


# ② 모터 2 ms 기록 -------------------------------------------------------------------------------------------------

def test_motor_log_from_m1_writer_reads_back_with_derived_speed(tmp_path):
    p = tmp_path / "motion_x.csv"
    _motor_csv(p)
    t, x = A.load_motion(p)
    assert t[0] == T0 and len(t) == len(p.read_text(encoding="utf-8").splitlines()) - 2      # 머리줄과 WKC 오류 줄은 빠진다
    assert A.kst(T0) == "2026-10-23T05:13:20.000+09:00"
    v = A.motor_speed(t, x, [T0 + 1.0, T0 + 2.6, T0 + 4.1])                                   # 정지 · 나가는 등속 · 돌아오는 등속
    assert abs(v[0]) < 0.02 and abs(v[1] - V) < 0.05 and abs(v[2] + V) < 0.05
    assert abs(A.motor_accel(t, x, [T0 + 2.1])[0] - AM) < 5                                    # 가속 구간 +50 mm/s²


# ③ 정렬 · 비교 ----------------------------------------------------------------------------------------------------

def test_find_offset_recovers_lag_and_sign_and_flags_search_edge():
    t = [i * 0.01 for i in range(3000)]

    def pulse(x):                                  # 모양이 다른 펄스 둘 — 한 지연에서만 겹친다
        return math.exp(-((x - 10) / 0.3) ** 2) - math.exp(-((x - 13) / 0.2) ** 2)
    ref = lambda g: [pulse(x) for x in g]          # noqa: E731
    cell_t, cell_v = [x + 0.237 for x in t], [-2 * pulse(x) for x in t]       # 셀 = 모터 + 0.237 · 축 반대 · 배율 2
    r = A.find_offset(ref, cell_t, cell_v, 8, 15, 0.01, 1.0, signed=True)
    assert abs(r["lag"] - 0.237) < 0.003 and r["r"] < -0.99 and not r["edge"]
    assert A.find_offset(ref, cell_t, cell_v, 8, 15, 0.01, 0.1, signed=True)["edge"]         # 참값이 탐색 범위 밖


def test_quality_needs_clear_peaks_or_two_windows_that_agree():
    def w(lag, r, edge=False):
        return {"lag": lag, "r": r, "edge": edge, "t": 0.0}
    assert A.quality([w(0.30, 0.9), w(0.31, -0.8)]) == "정렬됨"
    assert A.quality([w(12.30, 0.4), w(12.32, 0.35)]) == "약함"          # 잡음이 커도 두 창이 같은 lag
    assert A.quality([w(12.30, 0.4), w(9.10, 0.35)]) == "정렬 불가"
    assert A.quality([w(0.30, 0.4)]) == "정렬 불가"                       # 창 하나로는 '약함' 을 말할 수 없다
    assert A.quality([w(0.30, 0.9), w(5.0, 0.9, edge=True)]) == "정렬 불가"
    assert A.quality([]) == "정렬 불가"


def test_err_stats_and_speed_series_and_history_order():
    s = A.err_stats([3.0, -4.0] + [0.0] * 18)
    assert (s["n"], round(s["mean"], 3), round(s["rms"], 3), s["p95"], s["max"]) == (20, -0.05, 1.118, 3.0, 4.0)
    under = list(range(200))                       # 바탕 표본 j 의 시각 = j × 0.1
    pkts = [(j * 0.1, under[j - 19:j + 1]) for j in range(19, 200, 5)]      # 20 칸 창, 0.5 초마다
    ts, vs = A.speed_series(pkts)
    assert vs == under and all(abs(x - j * 0.1) < 1e-6 for j, x in enumerate(ts))   # 겹침도 빠짐도 없이 이어진다
    rnd = random.Random(4)
    stream = [rnd.randrange(0, 50) for _ in range(700)]
    a, b = stream[0:600], stream[5:605]
    assert A.history_order(a, b) == "as_is" and A.history_order(a[::-1], b[::-1]) == "reversed"
    assert A.history_order([0] * 600, [0] * 600) is None                    # 실내 fix 없음 — 판정 불가


def test_compare_recovers_known_offsets_drift_and_scales(tmp_path):
    mot, raw, imu = tmp_path / "motion_x.csv", tmp_path / "live.bin", tmp_path / "imu.csv"
    lags = {11740: 0.35, 11741: 0.40, 11742: 0.30}
    _motor_csv(mot)
    _live_raw(raw, lags, no_fix=(11743,))
    _imu_csv(imu, L0=12.3, d=1e-3)                 # 셀 시계 12.3 초 앞 · 1000 ppm 빠름 (수학 검사라 과장했다)
    assert C.main(["--motion", str(mot), "--live", str(raw), "--imu", str(imu), "--imu-max-lag", "15", "--out", str(tmp_path / "r")]) == 0
    with open(tmp_path / "r.csv", encoding="utf-8-sig", newline="") as f:
        rows = {r["cell"] or "imu": r for r in csv.DictReader(f)}
    for s, lag in lags.items():
        r = rows[str(s)]
        assert r["status"] == "정렬됨", r
        assert abs(float(r["lag_A_s"]) - lag) < 0.03 and abs(float(r["lag_B_s"]) - lag) < 0.03
        assert abs(float(r["hist_k"]) - 1) < 0.05 and float(r["hist_rms"]) < 1.0       # speed_history 는 mm/s 그대로
        assert abs(float(r["hspeed_k"]) - 2) < 0.2                                        # 단위를 모르는 hspeed 는 배율로
    assert rows["11743"]["status"] == "fix 없음" and rows["11743"]["lag_A_s"] == ""            # 비교하지 않았다
    m = rows["imu"]
    assert m["axis"] == "acc_y" and m["status"] == "정렬됨" and float(m["r_A"]) < -0.5
    assert abs(float(m["lag_A_s"]) - 12.3) < 0.02 and abs(float(m["drift_ppm"]) - 1000) < 200
    assert abs(float(m["acc_k"]) + 0.4177) < 0.05
    md = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "**Y**" in md and "정렬된 셀 3/4대" in md and "반대가 맞음 0 " in md
    # 순서 가정을 거꾸로 주면 점검 줄이 그것을 잡는다
    assert C.main(["--motion", str(mot), "--live", str(raw), "--newest-first", "--out", str(tmp_path / "r2")]) == 0
    assert "지금 해석과 맞음 0 " in (tmp_path / "r2.md").read_text(encoding="utf-8")


def test_compare_reports_no_fix_and_no_motion_plainly(tmp_path):
    mot, raw = tmp_path / "motion_x.csv", tmp_path / "live.bin"
    _motor_csv(mot)
    _live_raw(raw, {}, no_fix=(11733, 11734))
    C.main(["--motion", str(mot), "--live", str(raw), "--out", str(tmp_path / "r")])
    assert "GPS fix 없음 — 2대 전부" in (tmp_path / "r.md").read_text(encoding="utf-8")
    still = tmp_path / "motion_still.csv"
    _motor_csv(still, moving=False)                # M1 처럼 서 있는 축
    C.main(["--motion", str(still), "--out", str(tmp_path / "s")])
    assert "모터가 움직이지 않았다" in (tmp_path / "s.md").read_text(encoding="utf-8")


# ④ 셀 종류 --------------------------------------------------------------------------------------------------------

def test_cell_types_file_matches_the_firmware_facts():
    tab = celltype.load()
    assert {m: (v["type"], v["fw_expected"]) for m, v in tab["models"].items()} == {
        "X5": ("X", None), "X6A": ("X", "v0.99.71"), "X6B": ("X", "v0.99.71"),
        "Y1": ("Y", None), "Y3B": ("Y", "v0.99.71"), "Y4A": ("Y", "v0.99.71"), "Y4B": ("Y", "v0.99.71")}
    assert {k: (v["dock"], v["payload_kg"]) for k, v in tab["types"].items()} == {"X": ("DKPX-1C", 5.0), "Y": ("DKPY-1C", 5.0)}


def test_set_judgement_blocks_mixed_unknown_and_contradiction():
    tab = celltype.load()
    ok = celltype.judge_set([(s, None) for s in range(11733, 11757)], tab)          # 세트 1 · 24대
    assert ok["ok"] and ok["kind"] == "Y" and ok["models"] == {"Y4B": 24}
    assert {k: ok["preset"][k] for k in ("dock", "payload_kg", "fw_expected", "parser")} == {
        "dock": "DKPY-1C", "payload_kg": 5.0, "fw_expected": "v0.99.71", "parser": "y4"}
    with_x = dict(tab, registry={**tab["registry"], "20001-20024": "X6A"})
    mixed = celltype.judge_set([(11733, None), (20001, None)], with_x)
    assert not mixed["ok"] and mixed["kind"] is None and "X 와 Y" in mixed["reason"]
    unknown = celltype.judge_set([(11733, None), (11594, None)], tab)              # README 의 세트 2 예시 — 등록부에 없다
    assert not unknown["ok"] and "11594(모름" in unknown["reason"]
    clash = celltype.judge_set([(11740, "CLBX6A")], tab)                          # 등록부 Y4B 인데 셀은 X6A 라고 한다
    assert not clash["ok"] and "모순" in clash["reason"]
    assert celltype.judge_set([(99999, "CLBY4B")], tab)["kind"] == "Y"            # 셀이 말해 주면 등록부 없이도
    assert celltype.judge_set([], tab)["reason"] == "셀 없음"
    x5 = celltype.judge_set([(20001, None)], dict(tab, registry={"20001": "X5"}))
    assert x5["ok"] and x5["kind"] == "X" and x5["preset"]["fw_expected"] is None  # X5 양산 판 미확정


# live_tap ---------------------------------------------------------------------------------------------------------

def test_live_tap_records_raw_datagrams_and_keeps_listener_behaviour(tmp_path, monkeypatch):
    def loopback(port):                            # 0.0.0.0:60222 대신 127.0.0.1 의 임의 포트 — 셀에도 실제 포트에도 붙지 않는다
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        return s
    monkeypatch.setattr(cells, "bind_live_udp", loopback)
    path = tmp_path / "raw.bin"
    tap = LiveTap(Config(), path)
    sent = [_gps_pkt(11746, [7] * 600), b"FITO" + bytes([0x16]) + bytes(4), _gps_pkt(11746, [8] * 600)]
    t0 = time.time()
    tap.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as c:
            for d in sent:
                c.sendto(d, tap._sock.getsockname())
            end = time.time() + 3
            while tap.n < len(sent) and time.time() < end:
                time.sleep(0.01)
    finally:
        tap.stop()
        assert tap.done.wait(2) and tap.out.closed  # 스레드가 끝나며 파일을 닫는다 (join 은 부모 쪽 사정으로 쓰지 않는다)
    got = read_raw(path)
    assert [d for _, d in got] == sent and all(t0 - 1 < t <= time.time() for t, _ in got)
    assert 11746 in tap.cells                      # 부모(LiveListener)의 셀 표는 그대로 갱신된다
    with open(path, "ab") as f:
        f.write(REC.pack(1, 1320) + bytes(10))     # 쓰다 끊긴 마지막 레코드
    assert len(read_raw(path)) == len(sent)
