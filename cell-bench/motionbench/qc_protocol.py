"""셀 시험 프로토콜 v1 — 셀별 값 계산과 판정 (순수 함수).

프로토콜(2026-10-29 김건우 승인): 0 준비(1분) → 1 정지(5분) → 2 속도 계단 50~500 mm/s · 속도마다 왕복 3번 → 3 정지(3분) → 4 저장 기록 내려받기.
판정은 장비(모터가 정답 노릇을 했나) · 셀 수신 · 셀 시각은 합격/불합격, GPS 는 '살펴볼 셀'만 고른다 — 시험실 GPS 중계기 때문에
셀은 옥상 안테나 위치를 재므로 GPS 위치 · 속도를 정답과 비교해 판정하지 않는다(README 7절 7번). 기준 숫자는 지금까지 실측에서 나온 제안값.
"""
from __future__ import annotations

import statistics as st
from collections import Counter

CRITERIA = {"loss_pct_max": 1.0, "clock_dev_ms_max": 20.0, "imu_ratio_tol": 0.10, "gps_z": 3.0,
            "reach_um_max": 10, "cycle_ms_max": 50.0}
# 무리 점수(robust z)의 분모가 0 이 되지 않게 지표마다 둔 가장 작은 흩어짐 — 모두 같은 값일 때 1 단위 차이로 '벗어남'이 되지 않게
Z_FLOOR = {"cn0": 1.0, "sats": 1.0, "r_share": 5.0, "still_speed": 5.0}
GPS_Z_METRICS = ("cn0", "sats", "r_share", "still_speed")


def robust_z(vals: dict, floor: float = 0.0) -> dict:
    """{셀: 값} → {셀: 무리 점수}. 중앙값과 MAD × 1.4826(정규분포의 표준편차에 맞춘 값)으로 잰다. 값이 None 인 셀은 None."""
    xs = [v for v in vals.values() if v is not None]
    if not xs:
        return {k: None for k in vals}
    m = st.median(xs)
    s = max(st.median(abs(v - m) for v in xs) * 1.4826, floor) or 1.0
    return {k: None if v is None else (v - m) / s for k, v in vals.items()}


def loss_pct(seqs: list[int]) -> float | None:
    """패킷 번호(byte 7–10, 패킷마다 1씩 는다)로 빠진 비율 %. 번호가 되돌아가면(셀 재부팅) 구간을 나눠 더한다."""
    if not seqs:
        return None
    got = exp = 0
    run = [seqs[0]]
    for s in seqs[1:]:
        if s < run[-1]:
            got, exp = got + len(set(run)), exp + run[-1] - run[0] + 1
            run = [s]
        else:
            run.append(s)
    got, exp = got + len(set(run)), exp + run[-1] - run[0] + 1
    return 100.0 * (exp - got) / exp


def static_metrics(rows: list[tuple]) -> dict:
    """정지 구간의 패킷 [(fix, cn0, sats, hspeed, e_mm, n_mm)] → 고정 비율 · C/N0 · 위성 수 · 정지 속도 잡음 · 고정 위치 흔들림 95 %."""
    if not rows:
        return {}
    fx = Counter(r[0] for r in rows)
    r = [(e, n) for f, _, _, _, e, n in rows if f == "R" and e is not None]
    p95 = None
    if len(r) >= 20:
        e0, n0 = st.median(e for e, _ in r), st.median(n for _, n in r)
        d = sorted(((e - e0) ** 2 + (n - n0) ** 2) ** 0.5 for e, n in r)
        p95 = d[min(len(d) - 1, int(0.95 * len(d)))]
    return {"n": len(rows), "r_share": 100.0 * fx.get("R", 0) / len(rows),
            "fix": {k: round(100.0 * v / len(rows), 1) for k, v in fx.most_common()},
            "cn0": st.median(x[1] for x in rows), "sats": st.median(x[2] for x in rows),
            "still_speed": st.median(x[3] for x in rows), "r_p95_mm": p95}


def judge(cells: dict, crit: dict = CRITERIA) -> dict:
    """셀별 값 {셀: {loss_pct, clock_ms, imu_ratio?, static: {...}}} → {셀: {수신, 시각, IMU, GPS, 이유}}.
    시각은 무리 중앙값에서의 차로 본다(PC 와 셀의 고정된 차이 약 0.3초는 모든 셀에 같아서)."""
    clk = [c.get("clock_ms") for c in cells.values() if c.get("clock_ms") is not None]
    clk0 = st.median(clk) if clk else None
    z = {m: robust_z({s: (c.get("static") or {}).get(m) for s, c in cells.items()}, Z_FLOOR[m]) for m in GPS_Z_METRICS}
    out = {}
    for s, c in cells.items():
        v = {}
        lp = c.get("loss_pct")
        v["수신"] = None if lp is None else lp <= crit["loss_pct_max"]
        ck = c.get("clock_ms")
        v["clock_dev_ms"] = None if ck is None or clk0 is None else ck - clk0
        v["시각"] = None if v["clock_dev_ms"] is None else abs(v["clock_dev_ms"]) <= crit["clock_dev_ms_max"]
        ir = c.get("imu_ratio")
        v["IMU"] = None if ir is None else abs(ir - 1) <= crit["imu_ratio_tol"]
        out_z = {m: z[m][s] for m in GPS_Z_METRICS if z[m][s] is not None and abs(z[m][s]) >= crit["gps_z"]}
        v["GPS_z"] = {m: round(x, 1) for m, x in out_z.items()}
        v["GPS"] = "살펴볼 셀" if out_z else "정상"
        v["판정"] = "불합격" if any(v[k] is False for k in ("수신", "시각", "IMU")) else (
            "보류" if any(v[k] is None for k in ("수신", "시각")) else "합격")
        out[s] = v
    return out


def rig_verdict(motion: dict, crit: dict = CRITERIA) -> dict:
    """모터 기록 JSON(run_motion long) → 장비 판정. 끝까지 돌았나 · 알람 · 도달 오차 · 속도별 추종 오차 · 주기 지연."""
    why = []
    if motion.get("exit") != "오래 왕복 다 됨":
        why.append(f"끝까지 돌지 못함: {motion.get('exit')}")
    al = motion.get("alarms") or []
    if al:
        why.append(f"알람 {len(al)}번")
    legs = motion.get("legs") or []
    reach = [abs(g["err_um"]) for g in legs if isinstance(g, dict) and g.get("err_um") is not None]
    if reach and max(reach) > crit["reach_um_max"]:
        why.append(f"도달 오차 최대 {max(reach)} µm > {crit['reach_um_max']}")
    for s in (motion.get("long") or {}).get("steps") or []:
        if s.get("ferr_max") is not None and s.get("ferr_limit_um") and abs(s["ferr_max"]) > s["ferr_limit_um"]:
            why.append(f"{s['v_mm_s']} mm/s 추종 오차 {s['ferr_max']} > {s['ferr_limit_um']} µm")
    cyc = ((motion.get("pdo") or {}).get("cycle_ms") or {}).get("max")
    if cyc is not None and cyc >= crit["cycle_ms_max"]:
        why.append(f"주기 지연 최대 {cyc} ms")
    return {"유효": not why, "이유": why, "도달_최대_um": max(reach) if reach else None}


def layout_grid(columns: list[list[int]], serials) -> list[list[int | None]]:
    """독 칸 배치(열마다 위에서부터) → 6행 × 4열 표 [행][열]. 배치에 없는 셀은 빈 칸에 번호 순으로 채운다."""
    rows, cols = max((len(c) for c in columns), default=6), max(len(columns), 4)
    grid = [[None] * cols for _ in range(rows)]
    placed = set()
    for j, col in enumerate(columns):
        for i, s in enumerate(col):
            grid[i][j] = s
            placed.add(s)
    rest = iter(sorted(set(serials) - placed))
    for j in range(cols):
        for i in range(rows):
            if grid[i][j] is None:
                grid[i][j] = next(rest, None)
    return grid
