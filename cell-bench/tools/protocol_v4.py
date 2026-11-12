"""셀 레일 시험 프로토콜 v4 판정 파일 — 시험 한 번마다 data/protocol_<시각>.json 하나. 결과판 레일 목록에 한 줄로 쌓인다.

칸 이름은 dynamic-bench/design/qc-protocol-v4.md 6-3 · 7-6(파일 약속 — 시험 이름의 정본은 qc-tests.md), 판정 갈래는 motionbench/qc_v4.py.
읽기만 하고 결과만 쓴다. 기준이 아직 '미정'인 항목은 살펴봄(무리에서 |z| ≥ 3)으로 돈다 — 셀 종합은 합불 항목으로만 낸다.
판정 파일의 conditions.test 에 시험 ID 를 적는다. 결과판이 아직 읽는 옛 열쇠 conditions.part 도 함께 적는다(motionbench/qc_ids.py 머리말).

FIRMWARE-2 (레일 왕복 + 같은 속도로 움직이는 LabSat 장면) — 실시간 기록만으로 된다. --ftg 로 셀 기록 폴더를 주면 거른 속도 항목
(ftg_move · ftg_on · ftg_dist)도 낸다 — 그때는 아래 둘처럼 pandas 가 있는 파이썬으로 돌린다. 작업 트리 루트에서:
  .venv/Scripts/python.exe cell-bench/tools/protocol_v4.py FIRMWARE-2 --runs cell-bench/data/FIRMWARE-2_<날짜>/v300/runs.json \
      --truth cell-bench/data/partB_20261030/rail_B_120s_truth.csv --scene S03_RailB120 --scene-utc0 2026-10-30T00:00:00
FIRMWARE-1 (레일 왕복 + 정지 장면, 움직임 판단) · GNSS-1 (셀 정지 + 정지 장면) — 셀 FTG 를 읽으므로 pandas 와 ohcoach-cell-tools 의
FtgParser 가 있는 파이썬으로 돌린다(제어 PC2 는 Python 3.10):
  python cell-bench/tools/protocol_v4.py FIRMWARE-1 --motion cell-bench/data/motion_long_<시각>.json ... --ftg cell-bench/data/ftg_y_<시각> ... --accel 50
  python cell-bench/tools/protocol_v4.py GNSS-1 --from "2026-10-30 16:48:30" --to "2026-10-30 16:53:30" --ftg cell-bench/data/ftg_y_<시각> --scene S01-Static-GLE-8h
정답 파일 partB_20261030/rail_B_120s_truth.csv 는 2026-10-30 에 만든 300 mm/s 장면의 정답이다 — 지난 기록 폴더 이름은 바꾸지 않는다.

셀 시계: 시뮬레이터 아래 셀의 GPS 시각은 장면 시각이다. 라이브 패킷의 GPS 시각(byte 13–20)과 PC 수신 시각의 차로 실행마다 잰다 —
손으로 넣는 오프셋은 없다. LabSat 은 재생 명령 뒤 약 2.7초 지나 장면을 시작하므로(2026-10-30 실측), FIRMWARE-2 의 '하늘 출발'도 이 값으로 잰다.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import statistics as st
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cellbench.protocol import parse_gps  # noqa: E402
from motionbench import align as A, percell, qc_ids, qc_protocol as Q, qc_v4 as V  # noqa: E402

DATA = ROOT / "data"
REC = struct.Struct("<QH")     # live_raw 레코드 머리 — motionbench/live_tap.py 와 같다(그 모듈은 수신 장치까지 끌어와 3.10 에서 안 뜰 수 있어 따로 둔다)


def read_raw(path) -> list[tuple[float, bytes]]:
    """live_raw 파일 → [(수신 시각, 데이터그램)] — live_tap.read_raw 와 같은 일. 쓰다 끊긴 마지막 레코드는 버린다."""
    b, out, i = Path(path).read_bytes(), [], 0
    while i + REC.size <= len(b):
        t, n = REC.unpack_from(b, i)
        i += REC.size
        if i + n > len(b):
            break
        out.append((t / 1e6, b[i:i + n]))
        i += n
    return out
LAYOUT = ROOT / "motionbench" / "dock_layout.json"
FIXED = ("A", "D", "F", "R")
SIM = {"model": "Racelogic LabSat 3 RLL03-1", "serial": "00071272", "firmware": "01.09 build 1099"}
SCENE_POS = {"S01-Static-GLE-8h": (37 + 33.05351 / 60, 126 + 58.36159 / 60, 101.0)}   # LabSat MON:LOC 2026-10-30
DOCK_NAME = "Y"      # main 이 --dock 으로 바꾼다 — conditions.dock_layout 에 남는다
SYNC_MAX_S = 1.0     # FIRMWARE-2 — 레일 출발과 하늘(장면) 출발의 차가 이보다 크면 장비 무효 [제안값 · 한 번 가기 5.6초의 약 1/5]


def _ts(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()


def _stamp(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y%m%d-%H%M%S")


def commit() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def live(t0: float, t1: float, data: Path = DATA) -> dict[int, list]:
    """t0~t1 의 셀 라이브 패킷 {시리얼: [(수신 시각, Gps)]} — 여러 live_raw 파일에 걸쳐도 된다(실행 사이에 기록을 다시 띄운 날)."""
    out: dict[int, list] = {}
    for f in sorted((data / "live_raw").glob("*.bin")):
        try:
            start = datetime.strptime(f.stem, "%Y%m%d-%H%M%S").timestamp()
        except ValueError:
            continue
        if start > t1 or f.stat().st_mtime < t0:
            continue
        for x, d in read_raw(f):
            if t0 <= x <= t1:
                g = parse_gps(d)
                if g:
                    out.setdefault(g.serial, []).append((x, g))
    return {s: sorted(v, key=lambda p: p[0]) for s, v in sorted(out.items())}


def fixed(pk: list) -> list:
    return [(x, g) for x, g in pk if g.fix in FIXED and (g.lat_deg, g.lon_deg) != (0.0, 0.0)]


def clock_offset(cells: dict) -> float | None:
    """PC 수신 시각 − 셀 GPS 시각(초). 셀마다 가장 짧은 지연(5 번째 백분위)을 잡고 그 중앙값 — 잘못된 시각이 섞인 패킷에 끌리지 않게."""
    per = []
    for pk in cells.values():
        d = sorted(x - g.utc for x, g in fixed(pk) if g.utc)
        if len(d) >= 10:
            per.append(d[len(d) // 20])
    return st.median(per) if per else None


def layout(set_name: str) -> list[list[int]]:
    return json.loads(LAYOUT.read_text(encoding="utf-8"))[set_name]["columns"]


def dock_of(columns) -> dict:
    return {s: {"col": c + 1, "row": r + 1} for c, col in enumerate(columns) for r, s in enumerate(col)}


def base_items(pk: list, a: float, b: float, rx_from: float | None = None) -> dict:
    """세 시험(GNSS-1 · FIRMWARE-1 · FIRMWARE-2) 공통 — 수신(rx, 합불) · 위치 잡음 비율(gps_rx) · C/N0(cn0_sim)의 값. a~b 는 PC 시각.
    수신은 시험 전체(rx_from~b)로 잰다 — 판정 구간이 100초 남짓이면 패킷 1개만 빠져도 1 % 를 넘는다(기준 1 % 는 10분 시험에서 정한 제안값)."""
    w = [(x, g) for x, g in pk if a <= x <= b]
    fx = fixed(w)
    wr = [g.seq for x, g in pk if (a if rx_from is None else rx_from) <= x <= b and g.seq is not None]
    return {"rx": Q.loss_pct(wr) if wr else None,
            "gps_rx": 100.0 * len(fx) / len(w) if w else None,
            "cn0_sim": st.median(g.cn0 for _, g in fx) if fx else None}


CRIT_FILE = ROOT / "motionbench" / "criteria" / "rail_v1.json"


def load_criteria(path: str | Path | None = CRIT_FILE) -> dict | None:
    """1차 기준 파일(tools/qc_criteria.py 가 쓴다). 없거나 'none' 이면 None — 그때는 지금처럼 살펴봄으로만 돈다."""
    if not path or str(path).lower() == "none" or not Path(path).exists():
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _crit_items(crit: dict | None, test: str, keys: dict) -> dict:
    """이 실행에 쓸 기준 칸 {id: 칸}. 기준 파일의 tests.<시험 ID>.items 를 읽는다.
    묶음 값의 열쇠(가속도 등)가 기준을 만든 조건과 다르면 그 항목은 쓰지 않는다."""
    items = (((crit or {}).get("tests") or {}).get(test) or {}).get("items") or {}
    return {i: c for i, c in items.items() if not c.get("key") or c["key"] == keys.get(i, c["key"])}


def assemble(per_cell: dict, extra: dict, columns, cond: dict, rig: dict, stamp: str, src: dict, basis: dict,
             crit: dict | None = None, keys: dict | None = None) -> dict:
    """셀별 값 → 판정 파일 한 장. per_cell = {셀: {rx, gps_rx, cn0_sim}}, extra = {id: ({셀: 값}, key 함수 또는 None)}.
    crit 는 1차 기준 파일 — 그 시험에서 kind '합불' 인 항목은 기준 범위로 합불을 내고, 나머지는 살펴봄으로 돈다."""
    cells = sorted(per_cell)
    ci = _crit_items(crit, cond["test"], keys or {})
    kinds = {i: c["kind"] for i, c in ci.items()}

    def judge(iid, vals, key=None):
        c = ci.get(iid)
        if c and c.get("kind") == "합불":
            return V.pass_range(vals, iid, c.get("lo"), c.get("hi"), basis.get(iid), key)
        return V.review(vals, iid, basis.get(iid), key)

    vals = {i: {s: per_cell[s][i] for s in cells} for i in ("rx", "gps_rx", "cn0_sim")}
    items = {s: {} for s in cells}
    for s, it in V.pass_fail(vals["rx"], "rx", basis.get("rx")).items():
        items[s]["rx"] = it
    for iid in ("gps_rx", "cn0_sim"):
        for s, it in judge(iid, {k: None if v is None else round(v, 2) for k, v in vals[iid].items()}).items():
            items[s][iid] = it
    for iid, (vs, key) in extra.items():
        for s, it in judge(iid, {s: vs.get(s) for s in cells}, key).items():
            items[s][iid] = it
    cm = V.build(items, dock_of(columns), kinds)
    grp = {i: V.group(cm, i) for i in ("rx", "gps_rx", "cn0_sim")}
    for iid, (vs, key) in extra.items():
        grp[iid] = V.group(cm, iid, key)
    if "gps_move" in extra:
        steps = {k for v in extra["gps_move"][0].values() if v for k in v["v_ratio"]}
        grp["gps_move"] = {"v_ratio": {k: V.group(cm, "gps_move", lambda v, k=k: (v or {}).get("v_ratio", {}).get(k)) for k in sorted(steps)}}
    if "gate_on" in extra:
        steps = {k for v in extra["gate_on"][0].values() if v for k in v}
        grp["gate_on"] = {k: V.group(cm, "gate_on", lambda v, k=k: (v or {}).get(k)) for k in sorted(steps)}
    out_crit = {}
    for i in ["rx", "gps_rx", "cn0_sim", *extra]:
        c = ci.get(i)
        if c and c.get("kind") == "합불":
            lo, hi = c.get("lo"), c.get("hi")
            out_crit[i] = {"value": [lo, hi] if lo is not None and hi is not None else (lo if lo is not None else hi), "lo": lo, "hi": hi,
                           "unit": V.CRITERIA[i]["unit"], "status": c.get("status", "제안값"), "kind": "합불", "rule": c.get("rule")}
        else:
            out_crit[i] = dict(V.CRITERIA[i])
    used = any(c.get("kind") == "합불" for c in ci.values())
    return {"stamp": stamp, "conditions": cond | {"judge_commit": commit()}, "criteria": out_crit,
            "criteria_version": (crit or {}).get("version") if used else None, "cells": cm,
            "group": grp, "grid": Q.layout_grid(columns, [int(s) for s in cm]), "rig": rig, "source": src}


def conditions(test: str, scene: str, set_name: str, **kw) -> dict:
    """판정 파일의 conditions. part 는 결과판 호환 칸이다 — 결과판이 test 를 읽게 되면 지운다(motionbench/qc_ids.py 머리말)."""
    return {"protocol": "v4", "test": test, "part": qc_ids.board_judged_part(test), "dock_layout": DOCK_NAME, "site": "lab", "sky": "simulator", "repeater": "off", "corrections": "없음 (LabSat 단일 하늘)",
            "set": set_name, "cell_type": set_name, "fw": "미확인", "dock": f"DKP{set_name[:1]}-1C", "scene": {"name": scene},
            "simulator": SIM} | kw


def motion_json(csv_path: str) -> dict:
    return json.loads(Path(csv_path).with_suffix(".json").read_text(encoding="utf-8"))


# ── FIRMWARE-2 ─────────────────────────────────────────────────────────────────────────────
def truth(path) -> tuple[list[float], list[float]]:
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    return [float(r["t_s"]) for r in rows], [abs(float(r["v_mm_s"])) for r in rows]


def best_lag(xs, cs, ref, lo=-0.5, hi=1.5, step=0.05, vmove=270.0) -> tuple[float, float] | None:
    """셀 속도 cs(시각 xs)가 기준 ref(x) 를 몇 초 늦게 따라오나 → (늦음, 그때 움직일 때 |셀 − 기준| 중앙값).
    늦음은 전 구간 |셀 − 기준| 평균이 가장 작은 값으로 고른다 — 중앙값으로 고르면 가감속 몇 표본만 어긋나는 여러 늦음이 모두 0 이 되어
    첫 후보가 뽑힌다(2026-10-30 검사에서 확인)."""
    best = None
    for k in range(round(lo / step), round(hi / step) + 1):
        lag = k * step
        rv = [ref(x - lag) for x in xs]
        if sum(r >= vmove for r in rv) < 10:
            continue
        e = st.fmean(abs(c - r) for c, r in zip(cs, rv))
        if best is None or e < best[1]:
            best = (round(lag, 2), e, rv)
    if best is None:
        return None
    lag, _, rv = best
    return lag, st.median(abs(c - r) for c, r in zip(cs, rv) if r >= vmove)


def scene_utc0_auto(cells: dict, tp: float, sky_delay: float, still: float) -> float | None:
    """장면 0초의 GPS 시각 — 재생 뒤 정지 구간(sky_delay+3 ~ sky_delay+still−2초)의 셀 패킷에서
    GPS 시각 − (수신 시각 − 장면 시작 PC 시각) 의 셀별 5번째 백분위(전송 지연이 가장 짧은 쪽) 중앙값. SatGen 의 시각 칸을 믿지 않는다."""
    t_sky = tp + sky_delay
    per = []
    for pk in cells.values():
        v = sorted(g.utc - (x - t_sky) for x, g in fixed(pk) if g.utc and t_sky + 3 <= x <= t_sky + still - 2)
        if len(v) >= 5:
            per.append(v[len(v) // 20])
    return st.median(per) if per else None


FTG_GAP_S = 0.5        # 셀 기록 표본 사이가 이보다 벌어지면 그 사이는 적분하지 않는다(10 Hz 기록)
FTG_COVER_MIN = 0.8    # 판정 구간의 이만큼은 셀 기록이 있어야 거리 비율을 낸다
FTG_MAX_MM_S = 12000   # 이보다 빠른 거른 속도는 값 없음 — 사람 질주(약 10 m/s)보다 빠르다. X5E 기록에 25 m/s 넘는 값이 한 회차 내내 나온 셀이 있었다(11-06)


def ftg_speed(raw: float) -> float | None:
    """FTG filtered_sog_scaled(mm/s, 해석기는 ÷1000 해 m/s) → 속도. −1 · −2 · −3 은 필터가 움직임을 끈 상태 코드라 0,
    그보다 작은 음수와 FTG_MAX_MM_S 보다 큰 값은 값 없음(X5E 기록에 −27649 · 25,000 같은 값이 섞여 나온다 — 11-06 확인)."""
    if raw > FTG_MAX_MM_S or raw < -3:
        return None
    return 0.0 if raw < 0 else float(raw)


def truth_dist(tt, tv, t_from: float) -> float:
    """장면 정답 속도(mm/s)를 장면 t_from 초부터 끝까지 적분한 거리(mm)."""
    return sum((tv[i] + tv[i + 1]) / 2 * (tt[i + 1] - tt[i]) for i in range(len(tt) - 1) if tt[i] >= t_from)


def ftg_fw2_items(w: list[float], fs: list[float], sky, a: float, b: float, vmax: float, vmove: float, dist_ref: float) -> dict:
    """FIRMWARE-2 의 셀 기록(FTG) 항목 셋 — 레일 정답 대비 거른 속도.
    w 는 PC 시각(셀 GPS 시각 + 시계 차 — 시뮬레이터 아래에서는 장면 시각과 같은 축), fs 는 filtered_sog_scaled, sky(x) 는 그 시각의 장면 속도.
      ftg_move: 장면 속도가 vmove 이상인 표본의 거른 속도 중앙값 ÷ vmax
      ftg_on:   그 표본 중 거른 속도 > 0 인 비율(%) — 필터가 움직임을 끄고 있으면 낮다
      ftg_dist: a~b 의 거른 속도 적분 ÷ 장면 거리 dist_ref"""
    pts = [(x, ftg_speed(v)) for x, v in zip(w, fs) if a <= x <= b]
    pts = [(x, v) for x, v in pts if v is not None]
    out = {"ftg_move": None, "ftg_on": None, "ftg_dist": None}
    mv = [v for x, v in pts if sky(x) >= vmove]
    if len(mv) >= 10:
        out["ftg_move"] = round(st.median(mv) / vmax, 4)
        out["ftg_on"] = round(100.0 * sum(v > 0 for v in mv) / len(mv), 1)
    pairs = [(p0, p1) for p0, p1 in zip(pts, pts[1:]) if p1[0] - p0[0] <= FTG_GAP_S]
    if dist_ref > 0 and b > a and sum(x1 - x0 for (x0, _), (x1, _) in pairs) >= FTG_COVER_MIN * (b - a):
        out["ftg_dist"] = round(sum((v0 + v1) / 2 * (x1 - x0) for (x0, v0), (x1, v1) in pairs) / dist_ref, 4)
    return out


def judge_firmware2(run: dict, tt, tv, scene: str, utc0: float | None, still: float, set_name: str, data: Path = DATA, crit: dict | None = None,
           folders: list[str] | None = None) -> dict:
    tp, fm = run["t_play"], run["t_first_move"]
    m = motion_json(run["motor_csv"])
    t, p = A.load_motion(run["motor_csv"])
    cells = live(tp, t[-1] + 10, data)
    if utc0 is None:                                            # --scene-utc0 auto
        utc0 = scene_utc0_auto(cells, tp, float(run.get("sky_delay_used") or 2.75), still)
    off = clock_offset(cells)                                   # PC − 셀 GPS 시각
    t0 = None if off is None or utc0 is None else utc0 + off    # 장면 0초의 PC 시각
    sky_start = None if t0 is None else t0 + still
    sync = None if sky_start is None or fm is None else fm - sky_start
    rig = Q.rig_verdict(m)
    rig["sync_lag_ms"] = None if sync is None else round(sync * 1000)
    rig["labsat_start_s"] = None if t0 is None else round(t0 - tp, 2)
    if sync is None:
        rig["유효"], rig["이유"] = False, rig["이유"] + ["하늘 출발 시각을 잴 수 없음(셀 GPS 시각 없음)"]
    elif abs(sync) > SYNC_MAX_S:
        rig["유효"], rig["이유"] = False, rig["이유"] + [f"레일과 하늘 출발 차 {sync:+.2f}초 > ±{SYNC_MAX_S}초"]
    a, b = (sky_start or tp + still), (t0 or tp) + tt[-1]
    sky = lambda x: A.interp(tt, tv, x - t0) if t0 is not None else 0.0
    vmax = max(tv); vkey = str(int(round(vmax))); vmove = 0.9 * vmax     # 장면 속도(300 · 100 · 500) — '움직일 때' = 장면 속도의 90 % 이상
    per, gm, lag, lock = {}, {}, {}, {}
    fitems = {k: {} for k in ("ftg_move", "ftg_on", "ftg_dist")} if folders else {}
    segs = ftg_segments(folders) if folders and off is not None else {}
    wide, picks = wide_live(a, b, data), {}
    dref = truth_dist(tt, tv, still)
    for s, pk in cells.items():
        per[s] = base_items(pk, a, max(b, t[-1]), rx_from=tp)
        f0 = next((x for x, _ in fixed(pk) if t0 is not None and x >= t0), None)
        lock[s] = None if f0 is None else round(f0 - t0, 1)
        if fitems:
            seg = ftg_pick(segs.get(s) or [], s, pk, a, b, wide, picks)
            w, fs = ([x + off for x in seg[0]], seg[1]) if seg else ([], [])
            fv = ftg_fw2_items(w, fs, sky, a, b, vmax, vmove, dref) if w and t0 is not None else dict.fromkeys(fitems)
            for k in fitems:
                fitems[k][s] = fv[k]
        fx = [(x, g.hspeed_raw) for x, g in fixed(pk) if a <= x <= b and g.hspeed_raw < 32767]
        bl = best_lag([x for x, _ in fx], [c for _, c in fx], sky, vmove=vmove) if len(fx) >= 30 and t0 is not None else None
        if bl is None:
            gm[s] = lag[s] = None
            continue
        mv = [c for x, c in fx if sky(x - bl[0]) >= vmove]
        gm[s] = {"v_ratio": {vkey: round(st.median(mv) / vmax, 4)}, "err_mm_s": round(bl[1], 1)} if mv else None
        lag[s] = bl[0]
    cond = conditions("FIRMWARE-2", scene, set_name, speeds_mm_s=[int(round(vmax))], accels_mm_s2=[500], dwell_s=3, still_s=still,
                      repeat={"run": run["i"], "t_play": round(tp, 3), "sky_delay_used_s": run.get("sky_delay_used", 0.0)})
    basis = {"rx": "라이브 패킷 번호(재생 시작 → 모터 끝)", "gps_rx": "라이브 fix 칸", "cn0_sim": "라이브 평균 C/N0",
             "gps_move": f"라이브 원래 속도(byte 41–42) 중앙값 ÷ 장면 속도 {vkey} mm/s — 거른 속도(FTG) 아님",
             "sog_lag": "라이브 원래 속도 대 장면 속도, 셀 GPS 시각 기준 늦음",
             "lock": "장면 0초(셀 GPS 시각으로 잰 PC 시각) → 첫 고정 패킷"}
    if fitems:
        basis |= {"ftg_move": f"FTG 거른 속도(filtered_sog) 중앙값 ÷ 장면 속도 {vkey} mm/s — 장면 속도 90 % 이상인 때. 이 회차의 기록 구간은 라이브 위치가 같은 구간으로 골랐다",
                  "ftg_on": "장면 속도 90 % 이상인 때 FTG 거른 속도 > 0 인 표본 비율",
                  "ftg_dist": f"FTG 거른 속도 적분 ÷ 장면 거리 {dref / 1000:.2f} m(정지 {still:g}초 뒤 ~ 장면 끝)"}
    stamp = m.get("stamp") or _stamp(fm or tp)
    extra = {"lock": (lock, None), "gps_move": (gm, lambda v, k=vkey: v["v_ratio"][k]), "sog_lag": (lag, None)} | {k: (v, None) for k, v in fitems.items()}
    src = {"motion": run["motor_csv"], "runs": "FIRMWARE-2", "clock_offset_s": off, "scene_utc0": utc0} | ({"ftg": folders, "ftg_pick": picks} if folders else {})
    return assemble(per, extra, layout(set_name), cond, rig, stamp, src, basis, crit, {"gps_move": {"v_ratio": vkey}})


# ── 셀 FTG 읽기 (pandas 필요) ──────────────────────────────────────────────────────────────
def _ftg_files(folders: list[str]):
    """FTG 폴더들의 셀 기록 파일마다 (시리얼, (셀 GPS 시각, filtered_sog, sog, 위도, 경도)) — 파일에 적힌 차례 그대로(numpy 배열)."""
    sys.path.insert(0, r"C:\Dev\ohcoach-cell-tools\cores\fitogether_data_parser")
    import numpy as np, pandas as pd  # noqa: E401
    from fitogether_data_parser.ftg.parser import FtgParser
    from fitogether_data_parser.ftg.messages.gps_message import GpsMessage, GPS_MESSAGE_DATA_TYPE_V2
    from fitogether_data_parser.ftg.utils.ftg_message_utils import decode_datetime_vectorized

    def deg(v):
        v = v.astype(np.float64); s = np.where(v > 0, 1.0, -1.0); a = np.abs(v)
        return s * (a // 1e10 + (a % 1e10) / 1e8 / 60)
    for fo in folders:
        for f in sorted(Path(fo).glob("ftg_*.bin")):
            s = int(f.name.split("ftg_")[-1][:5])
            data = f.read_bytes()
            p = FtgParser("CLBY-4B-00000_0.99_0_1761724328_0.ftg", filecontents=data)
            g = [bytes(pl) for ty, cd, pl, crc in p.raw_parser(memoryview(data)) if ty == GpsMessage.MSG_TYPE and FtgParser._crc_check(cd, crc)]
            if not g:
                continue
            r = pd.DataFrame(np.frombuffer(b"".join(g), dtype=GPS_MESSAGE_DATA_TYPE_V2))
            dt = pd.to_datetime(pd.Series(decode_datetime_vectorized(r["date"].values, r["time_utc"].values)), errors="coerce", utc=True)
            ok = dt.notna().to_numpy()
            w = dt[ok].astype("int64").to_numpy() / 1e9
            yield s, (w, r["filtered_sog_scaled"].to_numpy()[ok], r["sog_scaled"].to_numpy()[ok],
                      deg(r["gps_nmea_latitude"].to_numpy()[ok]), deg(r["gps_nmea_longitude"].to_numpy()[ok]))


# ── 장면을 다시 틀면 셀 GPS 시각이 되풀이된다 — 판정 창마다 맞는 기록 구간을 골라 쓴다 ──────────────
# 시뮬레이터 장면은 틀 때마다 같은 GPS 시각에서 시작한다. 그래서 FIRMWARE-2 의 회차 10번, 바퀴마다 다시 트는 정지 장면(GNSS-1 · FIRMWARE-1),
# 밤새 돈 GNSS-1 이 셀 기록 안에서 같은 GPS 시각을 갖고, 시각으로 정렬해 합치면 회차 · 바퀴가 뒤섞인다(2026-11-06 FIRMWARE-2 에서, 11-07 GNSS-1 · FIRMWARE-1 에서 확인 —
# 셀이 기록을 지우지 않은 X · X5E 는 GNSS-1 · FIRMWARE-1 창마다 같은 시각의 구간이 4개, Y 도 밤 GNSS-1 이 든 폴더는 5~8개였다).
# 셀 기록을 적힌 차례대로 두고 GPS 시각이 뒤로 뛰는 곳에서 자른 뒤, 그 창의 라이브 고정 패킷과 위치가 같은 구간을 고른다.
# 라이브와 셀 기록은 같은 수신기 값이라 맞는 구간은 위치가 그대로 같다(차 0).
# 다만 LabSat 은 같은 장면을 같은 신호로 다시 틀어서, 정지 장면에서는 다른 재생의 위치까지 그대로 같은 때가 많다(FIRMWARE-1 의 80초 창에서
# 셀의 절반 가까이가 동점이었다). 그때는 판정 창 앞뒤로 넓힌 같은 재생의 라이브로 다시 가리고, 그래도 같으면 고르지 않는다.
_FTGSEG: dict = {}
SEG_BACK_S = 1.0       # GPS 시각이 이만큼 뒤로 뛰면 장면을 다시 튼 것으로 보고 자른다
SEG_TOL_DEG = 5e-7     # 라이브 위치(분 단위 소수 4자리 ≈ 1.7e-6 도)와 같다고 볼 차
SEG_MATCH_MIN = 0.5    # 겹친 라이브 표본 중 이만큼은 위치가 같아야 그 구간으로 인정
SEG_WIDE_S = 1800.0    # 동점이면 판정 창 앞뒤를 이만큼 넓힌 라이브로 다시 가린다
SEG_SAME_PLAY_S = 2.0  # 같은 재생이면 (PC 수신 시각 − 셀 GPS 시각)이 이 안에서 같다 — 다시 틀면 수 분 이상 달라진다


def ftg_segments(folders: list[str]) -> dict[int, list[tuple]]:
    """셀별 기록 구간 목록 [(셀 GPS 시각, filtered_sog, sog, 위도, 경도) 리스트 묶음]. 여러 폴더에 같은 구간이 겹치면 하나만 둔다.
    같다고 보는 것은 내용(시각 · 위치)이 같을 때뿐이다 — 같은 장면은 회차마다 시작 · 끝 시각과 길이까지 같게 나오는 일이 흔해서,
    그것만으로 겹침을 가리면 다른 회차를 지운다(2026-11-06 확인 — X 11572 r01 B300 10회의 기록이 이렇게 빠졌다)."""
    key = tuple(folders)
    if key not in _FTGSEG:
        import numpy as np
        out: dict[int, dict] = {}
        for s, cols in _ftg_files(folders):
            cut = np.where(np.diff(cols[0]) < -SEG_BACK_S)[0] + 1
            for idx in np.split(np.arange(len(cols[0])), cut):
                if len(idx) < 10:
                    continue
                same = (cols[0][idx].tobytes(), cols[3][idx].tobytes(), cols[4][idx].tobytes())
                out.setdefault(s, {}).setdefault(hash(same), tuple(c[idx].tolist() for c in cols))
        _FTGSEG[key] = {s: list(v.values()) for s, v in out.items()}
    return _FTGSEG[key]


def ftg_rank(segs: list[tuple], live_fx: list[tuple[float, float, float]], tol: float = SEG_TOL_DEG) -> list[tuple[int, tuple]]:
    """기록 구간마다 라이브 고정 패킷 [(셀 GPS 시각, 위도, 경도)] 과 위치가 같은 표본 수 — 겹친 표본의 SEG_MATCH_MIN 이상이
    같은 구간만, 많은 차례로(같은 수면 기록에 적힌 차례)."""
    out = []
    for seg in segs:
        w, la, lo = seg[0], seg[3], seg[4]
        if not live_fx or w[-1] < live_fx[0][0] or w[0] > live_fx[-1][0]:
            continue
        idx = {round(x, 1): i for i, x in enumerate(w)}
        hit = [(a, b, idx[k]) for u, a, b in live_fx if (k := round(u, 1)) in idx]
        same = sum(1 for a, b, i in hit if abs(la[i] - a) <= tol and abs(lo[i] - b) <= tol)
        if hit and same >= SEG_MATCH_MIN * len(hit):
            out.append((same, seg))
    return sorted(out, key=lambda r: -r[0])


def ftg_match(segs: list[tuple], live_fx: list[tuple[float, float, float]], wide=None, same=None,
              tol: float = SEG_TOL_DEG) -> tuple[tuple | None, str]:
    """라이브와 위치가 가장 많이 같은 기록 구간과 고른 방법 — 'one'(하나뿐) · 'same'(동점이지만 판정 창 안의 기록이 모두 같음) ·
    'wide'(동점을 넓힌 라이브로 가림) · 'tie'(가리지 못해 고르지 않음) · 'none'(맞는 구간 없음).
    same 은 동점 구간들의 판정 창 안 기록이 모두 같은지 보는 함수 — 셀이 기록을 지우지 않으면 같은 재생이 여러 폴더에 길이만 달리
    들어 있다(X5E A 창의 동점은 모두 이것이었다, 2026-11-07). wide 는 같은 재생의 더 긴 라이브 [(셀 GPS 시각, 위도, 경도)] 를 주는 함수."""
    r = ftg_rank(segs, live_fx, tol)
    if not r:
        return None, "none"
    if len(r) == 1 or r[1][0] < r[0][0]:
        return r[0][1], "one"
    tied = [seg for n, seg in r if n == r[0][0]]
    if same is not None and same(tied):
        return tied[0], "same"
    if wide is None:
        return None, "tie"
    r = ftg_rank(tied, wide(), tol)
    if not r or (len(r) > 1 and r[1][0] == r[0][0]):
        return None, "tie"
    return r[0][1], "wide"


def wide_live(a: float, b: float, data: Path = DATA):
    """판정 창 a~b 앞뒤를 SEG_WIDE_S 만큼 넓힌 라이브 {시리얼: 패킷} — 동점이 처음 나올 때 한 번만 읽는다."""
    box: list = []
    return lambda: box[0] if box else (box.append(live(a - SEG_WIDE_S, b + SEG_WIDE_S, data)) or box[0])


def ftg_pick(segs: list[tuple], s: int, pk: list, a: float, b: float, wide, picks: dict) -> tuple | None:
    """셀 s 의 판정 창 a~b(PC 시각)에 맞는 기록 구간. 동점이면 넓힌 라이브 중 같은 재생 것(PC − 셀 GPS 시각이 창 안의 중앙값에서
    SEG_SAME_PLAY_S 안)으로 다시 가린다. picks 에 고른 방법을 센다(판정 파일 source.ftg_pick)."""
    fx = [(x, g.utc, g.lat_deg, g.lon_deg) for x, g in fixed(pk) if g.utc and a <= x <= b]
    o = sorted(x - u for x, u, _, _ in fx)[len(fx) // 2] if fx else 0.0     # 이 창의 PC − 셀 GPS 시각

    def same_play():
        return [(g.utc, g.lat_deg, g.lon_deg) for x, g in fixed(wide().get(s) or []) if g.utc and abs(x - g.utc - o) <= SEG_SAME_PLAY_S]

    def same_body(tied):
        body = [{round(w, 1): tuple(sg[c][i] for c in range(1, 5)) for i, w in enumerate(sg[0]) if a - o <= w <= b - o} for sg in tied]
        return bool(body[0]) and all(x == body[0] for x in body[1:])
    seg, how = ftg_match(segs, [f[1:] for f in fx], same_play, same_body)
    picks[how] = picks.get(how, 0) + 1
    return seg


def judge_firmware1(motion_csv: str, folders: list[str], accel: str, set_name: str, data: Path = DATA, crit: dict | None = None) -> dict:
    m = motion_json(motion_csv)
    t, p = A.load_motion(motion_csv)
    t2, p2 = t[::10], p[::10]
    cells = live(t[0] - 5, t[-1] + 5, data)
    off = clock_offset(cells)
    rig = Q.rig_verdict(m)
    if off is None:
        rig["유효"], rig["이유"] = False, rig["이유"] + ["셀 시계 차를 잴 수 없음(라이브 GPS 시각 없음)"]
    segs = ftg_segments(folders) if off is not None else {}
    wide, picks = wide_live(t[0], t[-1], data), {}
    dw = percell.dwells(t2, p2)
    per, gate = {}, {}
    for s, pk in cells.items():
        per[s] = base_items(pk, t[0], t[-1])
        seg = ftg_pick(segs.get(s) or [], s, pk, t[0], t[-1], wide, picks) if segs else None
        w, fs = ([x + off for x in seg[0]], seg[1]) if seg else ([], [])
        if not w:
            gate[s] = None
            continue
        lags = []
        for (_, b, _) in dw[:-1]:                              # 멈춤이 끝나는 때 = 출발
            i = bisect.bisect_left(w, b)
            j = next((k for k in range(i, len(w)) if w[k] > b + percell.LAG_MAX_S or 0 < fs[k] < 32767), None)
            if j is not None and w[j] <= b + percell.LAG_MAX_S and 0 < fs[j] < 32767:
                lags.append(w[j] - b)
        gate[s] = {accel: round(st.median(lags), 3)} if lags else None
    long = m.get("long") or {}
    cond = conditions("FIRMWARE-1", "S01-Static-GLE-8h", set_name, speeds_mm_s=[s.get("v_mm_s") for s in long.get("steps") or []],
                      accels_mm_s2=[float(accel)], dwell_s=long.get("dwell_s"))
    basis = {"rx": "라이브 패킷 번호", "gps_rx": "라이브 fix 칸", "cn0_sim": "라이브 평균 C/N0",
             "gate_on": f"FTG 거른 속도 > 0 이 켜지기까지(레일 출발마다, 중앙값) · 셀 시계 차 {off:+.2f}초로 맞춤 · "
                        "기록 구간은 라이브 위치가 같은 구간으로 골랐다" if off is not None else "셀 시계 차 없음"}
    return assemble(per, {"gate_on": (gate, lambda v: v.get(accel))}, layout(set_name), cond, rig, m.get("stamp") or _stamp(t[0]),
                    {"motion": motion_csv, "ftg": folders, "ftg_pick": picks, "clock_offset_s": off}, basis, crit, {"gate_on": {"accel": accel}})


def judge_gnss1(t0: float, t1: float, folders: list[str], scene: str, set_name: str, data: Path = DATA, crit: dict | None = None) -> dict:
    import math
    cells = live(t0, t1, data)
    off = clock_offset(cells)
    rig = {"유효": off is not None, "이유": [] if off is not None else ["셀 시계 차를 잴 수 없음"], "도달_최대_um": None}
    segs = ftg_segments(folders) if off is not None else {}
    wide, picks = wide_live(t0, t1, data), {}
    lat0, lon0, _ = SCENE_POS[scene]
    k = 111320.0 * math.cos(math.radians(lat0))
    per, pos = {}, {}
    for s, pk in cells.items():
        per[s] = base_items(pk, t0, t1)
        seg = ftg_pick(segs.get(s) or [], s, pk, t0, t1, wide, picks) if segs else None
        w, la, lo = ([x + off for x in seg[0]], seg[3], seg[4]) if seg else ([], [], [])
        d = [math.hypot((y - lat0) * 110574.0, (x - lon0) * k) for tw, y, x in zip(w, la, lo) if t0 <= tw <= t1 and (y, x) != (0.0, 0.0)]
        pos[s] = round(st.median(d), 3) if len(d) >= 50 else None
    cond = conditions("GNSS-1", scene, set_name, still_s=round(t1 - t0))
    basis = {"rx": "라이브 패킷 번호", "gps_rx": "라이브 fix 칸", "cn0_sim": "라이브 평균 C/N0",
             "pos_h": "FTG 위치 − 장면 좌표(LabSat MON:LOC) 수평 거리 중앙값 · 기록 구간은 라이브 위치가 같은 구간으로 골랐다"}
    return assemble(per, {"pos_h": (pos, None)}, layout(set_name), cond, rig, _stamp(t0),
                    {"ftg": folders, "ftg_pick": picks, "clock_offset_s": off}, basis, crit)


RETEST: dict = {}     # main 이 --retest-of · --retest-cells 로 채운다 — write 가 conditions 에 붙인다


def write(doc: dict, out: Path) -> Path:
    if RETEST:
        doc["conditions"] |= RETEST
    path = out / f"protocol_{doc['stamp']}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
    n = {k: sum(c["verdict"]["overall"] == k for c in doc["cells"].values()) for k in ("pass", "fail", "hold")}
    fl = sorted({f for c in doc["cells"].values() for f in c["verdict"]["flags"]})
    print(f"{path.name} · {doc['conditions']['test']} · 장비 {'유효' if doc['rig']['유효'] else '무효 — ' + ' · '.join(doc['rig']['이유'])} · "
          f"셀 {len(doc['cells'])} · 합격 {n['pass']} 불합격 {n['fail']} 보류 {n['hold']} · 벗어난 항목 {fl or '없음'}", flush=True)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="test", required=True)
    b = sub.add_parser("FIRMWARE-2", help="속도 · 거리 시험"); b.add_argument("--runs", required=True); b.add_argument("--truth", required=True)
    b.add_argument("--scene", required=True); b.add_argument("--scene-utc0", default="auto", help="장면 0초의 UTC(예 2026-10-30T00:00:00) 또는 auto — 셀 GPS 시각으로 잰다")
    b.add_argument("--still", type=float, default=20.0)
    b.add_argument("--ftg", nargs="+", help="셀 기록 폴더 — 주면 거른 속도 항목(ftg_move · ftg_on · ftg_dist)도 낸다(pandas 가 있는 파이썬)")
    c = sub.add_parser("FIRMWARE-1", help="움직임 판단 시험"); c.add_argument("--motion", nargs="+"); c.add_argument("--runs", help="tools/run_rail.py 가 쓴 runs.json — 정상으로 끝난 실행의 모터 기록을 모두 판정")
    c.add_argument("--ftg", nargs="+", required=True)
    c.add_argument("--accel", required=True)
    a_ = sub.add_parser("GNSS-1", help="GNSS 수신 시험"); a_.add_argument("--from", dest="t0", required=True); a_.add_argument("--to", dest="t1", required=True)
    a_.add_argument("--ftg", nargs="+", required=True); a_.add_argument("--scene", required=True)
    a_.add_argument("--window", type=float, help="이 초마다 나눠 여러 장을 쓴다(기준용 데이터 모으기). 없으면 --from~--to 한 장")
    for s in (b, c, a_):
        s.add_argument("--set", default="Y"); s.add_argument("--out", default=str(DATA))
        s.add_argument("--dock", help="dock_layout.json 의 배치 이름(기본 --set) — 자리 바꾼 회차는 Y-swap")
        s.add_argument("--retest-of", help="재시험이면 원래 판정 파일의 stamp(예 20261103-101500) — conditions.retest_of 로 남는다")
        s.add_argument("--retest-cells", help="재시험 대상 시리얼 쉼표 목록(예 10687,10679) — conditions.retest_cells")
        s.add_argument("--criteria", default=str(CRIT_FILE), help="1차 기준 파일 — none 이면 살펴봄으로만 판정(기준 만들 데이터를 모을 때)")
    a = ap.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    crit = load_criteria(a.criteria)
    global layout, DOCK_NAME
    DOCK_NAME = a.dock or a.set
    _layout0 = layout
    layout = lambda set_name, _k=(a.dock or None): _layout0(_k or set_name)   # 배치만 바꾸고 conditions.set 은 그대로 둔다
    if a.retest_of:
        RETEST.update(retest_of=a.retest_of, retest_cells=[int(x) for x in (a.retest_cells or "").split(",") if x.strip()])
    if a.test == "FIRMWARE-2":
        tt, tv = truth(a.truth)
        utc0 = None if a.scene_utc0 == "auto" else datetime.fromisoformat(a.scene_utc0).replace(tzinfo=timezone.utc).timestamp()
        for run in json.loads(Path(a.runs).read_text(encoding="utf-8")):
            if run.get("rc") == 0 and run.get("motor_csv"):
                write(judge_firmware2(run, tt, tv, a.scene, utc0, a.still, a.set, crit=crit, folders=a.ftg), out)
    elif a.test == "FIRMWARE-1":
        motions = list(a.motion or [])
        if a.runs:
            motions += [r["motor_csv"] for r in json.loads(Path(a.runs).read_text(encoding="utf-8")) if r.get("rc") == 0 and r.get("motor_csv")]
        if not motions:
            raise SystemExit("판정할 모터 기록이 없다 — --motion 또는 --runs")
        for mc in motions:
            write(judge_firmware1(str(Path(mc).with_suffix(".csv")), a.ftg, a.accel, a.set, crit=crit), out)
    else:
        t0, t1 = _ts(a.t0), _ts(a.t1)
        step = a.window or (t1 - t0)
        while t0 + step <= t1 + 1e-6:
            write(judge_gnss1(t0, t0 + step, a.ftg, a.scene, a.set, crit=crit), out)
            t0 += step
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
