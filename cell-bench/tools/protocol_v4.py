"""셀 레일 시험 프로토콜 v4 판정 파일 — 시험 한 번마다 data/protocol_<시각>.json 하나. 결과판 레일 목록에 한 줄로 쌓인다.

칸 이름은 dynamic-bench/design/qc-protocol-v4.md 6-3 · 7-6, 판정 갈래는 motionbench/qc_v4.py. 읽기만 하고 결과만 쓴다.
기준이 아직 '미정'인 항목은 살펴봄(무리에서 |z| ≥ 3)으로 돈다 — 셀 종합은 합불 항목(지금은 수신 rx 하나)으로만 낸다.

파트 B (레일 왕복 + 같은 경로의 LabSat 장면) — 실시간 기록만으로 된다. 작업 트리 루트에서:
  .venv/Scripts/python.exe cell-bench/tools/protocol_v4.py b --runs cell-bench/data/partB_20261030/runs.json \
      --truth cell-bench/data/partB_20261030/rail_B_120s_truth.csv --scene S03_RailB120 --scene-utc0 2026-10-30T00:00:00
파트 C (레일 왕복 + 정지 장면, 움직임 판단) · 파트 A (셀 정지 + 정지 장면) — 셀 FTG 를 읽으므로 pandas 와 ohcoach-cell-tools 의
FtgParser 가 있는 파이썬으로 돌린다(제어 PC2 는 Python 3.10):
  python cell-bench/tools/protocol_v4.py c --motion cell-bench/data/motion_long_<시각>.json ... --ftg cell-bench/data/ftg_y_<시각> ... --accel 50
  python cell-bench/tools/protocol_v4.py a --from "2026-10-30 16:48:30" --to "2026-10-30 16:53:30" --ftg cell-bench/data/ftg_y_<시각> --scene S01-Static-GLE-8h

셀 시계: 시뮬레이터 아래 셀의 GPS 시각은 장면 시각이다. 라이브 패킷의 GPS 시각(byte 13–20)과 PC 수신 시각의 차로 실행마다 잰다 —
손으로 넣는 오프셋은 없다. LabSat 은 재생 명령 뒤 약 2.7초 지나 장면을 시작하므로(2026-10-30 실측), 파트 B 의 '하늘 출발'도 이 값으로 잰다.
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
from motionbench import align as A, percell, qc_protocol as Q, qc_v4 as V  # noqa: E402

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
SYNC_MAX_S = 1.0     # 파트 B — 레일 출발과 하늘(장면) 출발의 차가 이보다 크면 장비 무효 [제안값 · 한 번 가기 5.6초의 약 1/5]


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
    """세 파트 공통 — 수신(rx, 합불) · 위치 잡음 비율(gps_rx) · C/N0(cn0_sim)의 값. a~b 는 PC 시각.
    수신은 시험 전체(rx_from~b)로 잰다 — 판정 구간이 100초 남짓이면 패킷 1개만 빠져도 1 % 를 넘는다(기준 1 % 는 10분 시험에서 정한 제안값)."""
    w = [(x, g) for x, g in pk if a <= x <= b]
    fx = fixed(w)
    wr = [g.seq for x, g in pk if (a if rx_from is None else rx_from) <= x <= b and g.seq is not None]
    return {"rx": Q.loss_pct(wr) if wr else None,
            "gps_rx": 100.0 * len(fx) / len(w) if w else None,
            "cn0_sim": st.median(g.cn0 for _, g in fx) if fx else None}


def assemble(per_cell: dict, extra: dict, columns, cond: dict, rig: dict, stamp: str, src: dict, basis: dict) -> dict:
    """셀별 값 → 판정 파일 한 장. per_cell = {셀: {rx, gps_rx, cn0_sim}}, extra = {id: ({셀: 값}, key 함수 또는 None)}."""
    cells = sorted(per_cell)
    vals = {i: {s: per_cell[s][i] for s in cells} for i in ("rx", "gps_rx", "cn0_sim")}
    items = {s: {} for s in cells}
    for s, it in V.pass_fail(vals["rx"], "rx", basis.get("rx")).items():
        items[s]["rx"] = it
    for iid in ("gps_rx", "cn0_sim"):
        for s, it in V.review({k: None if v is None else round(v, 2) for k, v in vals[iid].items()}, iid, basis.get(iid)).items():
            items[s][iid] = it
    for iid, (vs, key) in extra.items():
        for s, it in V.review({s: vs.get(s) for s in cells}, iid, basis.get(iid), key).items():
            items[s][iid] = it
    cm = V.build(items, dock_of(columns))
    grp = {i: V.group(cm, i) for i in ("rx", "gps_rx", "cn0_sim")}
    for iid, (vs, key) in extra.items():
        grp[iid] = V.group(cm, iid, key)
    if "gps_move" in extra:
        steps = {k for v in extra["gps_move"][0].values() if v for k in v["v_ratio"]}
        grp["gps_move"] = {"v_ratio": {k: V.group(cm, "gps_move", lambda v, k=k: (v or {}).get("v_ratio", {}).get(k)) for k in sorted(steps)}}
    if "gate_on" in extra:
        steps = {k for v in extra["gate_on"][0].values() if v for k in v}
        grp["gate_on"] = {k: V.group(cm, "gate_on", lambda v, k=k: (v or {}).get(k)) for k in sorted(steps)}
    crit = {i: V.CRITERIA[i] for i in ["rx", "gps_rx", "cn0_sim", *extra]}
    return {"stamp": stamp, "conditions": cond | {"judge_commit": commit()}, "criteria": crit, "cells": cm,
            "group": grp, "grid": Q.layout_grid(columns, [int(s) for s in cm]), "rig": rig, "source": src}


def conditions(part: str, scene: str, set_name: str, **kw) -> dict:
    return {"protocol": "v4", "part": part, "site": "lab", "sky": "simulator", "repeater": "off", "corrections": "없음 (LabSat 단일 하늘)",
            "set": set_name, "cell_type": set_name, "fw": "미확인", "dock": f"DKP{set_name}-1C", "scene": {"name": scene},
            "simulator": SIM} | kw


def motion_json(csv_path: str) -> dict:
    return json.loads(Path(csv_path).with_suffix(".json").read_text(encoding="utf-8"))


# ── 파트 B ──────────────────────────────────────────────────────────────────────────────────
def truth(path) -> tuple[list[float], list[float]]:
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    return [float(r["t_s"]) for r in rows], [abs(float(r["v_mm_s"])) for r in rows]


def best_lag(xs, cs, ref, lo=-0.5, hi=1.5, step=0.05) -> tuple[float, float] | None:
    """셀 속도 cs(시각 xs)가 기준 ref(x) 를 몇 초 늦게 따라오나 → (늦음, 그때 움직일 때 |셀 − 기준| 중앙값).
    늦음은 전 구간 |셀 − 기준| 평균이 가장 작은 값으로 고른다 — 중앙값으로 고르면 가감속 몇 표본만 어긋나는 여러 늦음이 모두 0 이 되어
    첫 후보가 뽑힌다(2026-10-30 검사에서 확인)."""
    best = None
    for k in range(round(lo / step), round(hi / step) + 1):
        lag = k * step
        rv = [ref(x - lag) for x in xs]
        if sum(r >= 270 for r in rv) < 10:
            continue
        e = st.fmean(abs(c - r) for c, r in zip(cs, rv))
        if best is None or e < best[1]:
            best = (round(lag, 2), e, rv)
    if best is None:
        return None
    lag, _, rv = best
    return lag, st.median(abs(c - r) for c, r in zip(cs, rv) if r >= 270)


def part_b(run: dict, tt, tv, scene: str, utc0: float, still: float, set_name: str, data: Path = DATA) -> dict:
    tp, fm = run["t_play"], run["t_first_move"]
    m = motion_json(run["motor_csv"])
    t, p = A.load_motion(run["motor_csv"])
    cells = live(tp, t[-1] + 10, data)
    off = clock_offset(cells)                                   # PC − 셀 GPS 시각
    t0 = None if off is None else utc0 + off                    # 장면 0초의 PC 시각
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
    per, gm, lag, lock = {}, {}, {}, {}
    for s, pk in cells.items():
        per[s] = base_items(pk, a, max(b, t[-1]), rx_from=tp)
        f0 = next((x for x, _ in fixed(pk) if t0 is not None and x >= t0), None)
        lock[s] = None if f0 is None else round(f0 - t0, 1)
        fx = [(x, g.hspeed_raw) for x, g in fixed(pk) if a <= x <= b and g.hspeed_raw < 32767]
        bl = best_lag([x for x, _ in fx], [c for _, c in fx], sky) if len(fx) >= 30 and t0 is not None else None
        if bl is None:
            gm[s] = lag[s] = None
            continue
        mv = [c for x, c in fx if sky(x - bl[0]) >= 270]
        gm[s] = {"v_ratio": {"300": round(st.median(mv) / 300, 4)}, "err_mm_s": round(bl[1], 1)} if mv else None
        lag[s] = bl[0]
    cond = conditions("B", scene, set_name, speeds_mm_s=[300], accels_mm_s2=[500], dwell_s=3, still_s=still,
                      repeat={"run": run["i"], "t_play": round(tp, 3), "sky_delay_used_s": run.get("sky_delay_used", 0.0)})
    basis = {"rx": "라이브 패킷 번호(재생 시작 → 모터 끝)", "gps_rx": "라이브 fix 칸", "cn0_sim": "라이브 평균 C/N0",
             "gps_move": "라이브 원래 속도(byte 41–42) 중앙값 ÷ 장면 속도 300 mm/s — 거른 속도(FTG) 아님",
             "sog_lag": "라이브 원래 속도 대 장면 속도, 셀 GPS 시각 기준 늦음",
             "lock": "장면 0초(셀 GPS 시각으로 잰 PC 시각) → 첫 고정 패킷"}
    stamp = m.get("stamp") or _stamp(fm or tp)
    return assemble(per, {"lock": (lock, None), "gps_move": (gm, lambda v: v["v_ratio"]["300"]), "sog_lag": (lag, None)}, layout(set_name), cond, rig, stamp,
                    {"motion": run["motor_csv"], "runs": "partB", "clock_offset_s": off}, basis)


# ── 파트 C · A — 셀 FTG (pandas 필요) ────────────────────────────────────────────────────────
_FTG: dict = {}


def ftg_series(folders: list[str], offset: float) -> dict[int, tuple[list, list, list, list, list]]:
    """FTG 폴더들의 셀별 GPS 10 Hz (PC 시각 = 셀 GPS 시각 + offset, filtered_sog, sog, 위도, 경도). 두 폴더에 겹친 기록은 한 번만.
    같은 폴더 묶음은 한 번만 읽는다(파트 C 10번이 같은 FTG 를 쓴다)."""
    key = tuple(folders)
    if key not in _FTG:
        _FTG[key] = _ftg_read(folders)
    return {s: ([w + offset for w in c[0]], *c[1:]) for s, c in _FTG[key].items()}


def _ftg_read(folders: list[str]) -> dict[int, tuple[list, list, list, list, list]]:
    sys.path.insert(0, r"C:\Dev\ohcoach-cell-tools\cores\fitogether_data_parser")
    import numpy as np, pandas as pd  # noqa: E401
    from fitogether_data_parser.ftg.parser import FtgParser
    from fitogether_data_parser.ftg.messages.gps_message import GpsMessage, GPS_MESSAGE_DATA_TYPE_V2
    from fitogether_data_parser.ftg.utils.ftg_message_utils import decode_datetime_vectorized

    def deg(v):
        v = v.astype(np.float64); s = np.where(v > 0, 1.0, -1.0); a = np.abs(v)
        return s * (a // 1e10 + (a % 1e10) / 1e8 / 60)
    acc: dict[int, list] = {}
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
            acc.setdefault(s, []).append((w, r["filtered_sog_scaled"].to_numpy()[ok], r["sog_scaled"].to_numpy()[ok],
                                          deg(r["gps_nmea_latitude"].to_numpy()[ok]), deg(r["gps_nmea_longitude"].to_numpy()[ok])))
    out = {}
    for s, parts in acc.items():
        cols = [np.concatenate([x[i] for x in parts]) for i in range(5)]
        o = np.argsort(cols[0]); cols = [c[o] for c in cols]
        keep = np.concatenate([[True], np.diff(cols[0]) > 1e-3])
        out[s] = tuple(c[keep].tolist() for c in cols)
    return out


def part_c(motion_csv: str, folders: list[str], accel: str, set_name: str, data: Path = DATA) -> dict:
    m = motion_json(motion_csv)
    t, p = A.load_motion(motion_csv)
    t2, p2 = t[::10], p[::10]
    cells = live(t[0] - 5, t[-1] + 5, data)
    off = clock_offset(cells)
    rig = Q.rig_verdict(m)
    if off is None:
        rig["유효"], rig["이유"] = False, rig["이유"] + ["셀 시계 차를 잴 수 없음(라이브 GPS 시각 없음)"]
    ser = ftg_series(folders, off or 0.0)
    dw = percell.dwells(t2, p2)
    per, gate = {}, {}
    for s, pk in cells.items():
        per[s] = base_items(pk, t[0], t[-1])
        w, fs = (ser.get(s) or ([], []))[:2]
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
    cond = conditions("C", "S01-Static-GLE-8h", set_name, speeds_mm_s=[s.get("v_mm_s") for s in long.get("steps") or []],
                      accels_mm_s2=[float(accel)], dwell_s=long.get("dwell_s"))
    basis = {"rx": "라이브 패킷 번호", "gps_rx": "라이브 fix 칸", "cn0_sim": "라이브 평균 C/N0",
             "gate_on": f"FTG 거른 속도 > 0 이 켜지기까지(레일 출발마다, 중앙값) · 셀 시계 차 {off:+.2f}초로 맞춤" if off is not None else "셀 시계 차 없음"}
    return assemble(per, {"gate_on": (gate, lambda v: v.get(accel))}, layout(set_name), cond, rig, m.get("stamp") or _stamp(t[0]),
                    {"motion": motion_csv, "ftg": folders, "clock_offset_s": off}, basis)


def part_a(t0: float, t1: float, folders: list[str], scene: str, set_name: str, data: Path = DATA) -> dict:
    import math
    cells = live(t0, t1, data)
    off = clock_offset(cells)
    rig = {"유효": off is not None, "이유": [] if off is not None else ["셀 시계 차를 잴 수 없음"], "도달_최대_um": None}
    ser = ftg_series(folders, off or 0.0)
    lat0, lon0, _ = SCENE_POS[scene]
    k = 111320.0 * math.cos(math.radians(lat0))
    per, pos = {}, {}
    for s, pk in cells.items():
        per[s] = base_items(pk, t0, t1)
        w, _, _, la, lo = ser.get(s) or ([], [], [], [], [])
        d = [math.hypot((y - lat0) * 110574.0, (x - lon0) * k) for tw, y, x in zip(w, la, lo) if t0 <= tw <= t1 and (y, x) != (0.0, 0.0)]
        pos[s] = round(st.median(d), 3) if len(d) >= 50 else None
    cond = conditions("A", scene, set_name, still_s=round(t1 - t0))
    basis = {"rx": "라이브 패킷 번호", "gps_rx": "라이브 fix 칸", "cn0_sim": "라이브 평균 C/N0", "pos_h": "FTG 위치 − 장면 좌표(LabSat MON:LOC) 수평 거리 중앙값"}
    return assemble(per, {"pos_h": (pos, None)}, layout(set_name), cond, rig, _stamp(t0), {"ftg": folders, "clock_offset_s": off}, basis)


def write(doc: dict, out: Path) -> Path:
    path = out / f"protocol_{doc['stamp']}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
    n = {k: sum(c["verdict"]["overall"] == k for c in doc["cells"].values()) for k in ("pass", "fail", "hold")}
    fl = sorted({f for c in doc["cells"].values() for f in c["verdict"]["flags"]})
    print(f"{path.name} · 파트 {doc['conditions']['part']} · 장비 {'유효' if doc['rig']['유효'] else '무효 — ' + ' · '.join(doc['rig']['이유'])} · "
          f"셀 {len(doc['cells'])} · 합격 {n['pass']} 불합격 {n['fail']} 보류 {n['hold']} · 벗어난 항목 {fl or '없음'}", flush=True)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="part", required=True)
    b = sub.add_parser("b"); b.add_argument("--runs", required=True); b.add_argument("--truth", required=True)
    b.add_argument("--scene", required=True); b.add_argument("--scene-utc0", required=True, help="장면 0초의 UTC, 예 2026-10-30T00:00:00")
    b.add_argument("--still", type=float, default=20.0)
    c = sub.add_parser("c"); c.add_argument("--motion", nargs="+", required=True); c.add_argument("--ftg", nargs="+", required=True)
    c.add_argument("--accel", required=True)
    a_ = sub.add_parser("a"); a_.add_argument("--from", dest="t0", required=True); a_.add_argument("--to", dest="t1", required=True)
    a_.add_argument("--ftg", nargs="+", required=True); a_.add_argument("--scene", required=True)
    for s in (b, c, a_):
        s.add_argument("--set", default="Y"); s.add_argument("--out", default=str(DATA))
    a = ap.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if a.part == "b":
        tt, tv = truth(a.truth)
        utc0 = datetime.fromisoformat(a.scene_utc0).replace(tzinfo=timezone.utc).timestamp()
        for run in json.loads(Path(a.runs).read_text(encoding="utf-8")):
            if run.get("rc") == 0 and run.get("motor_csv"):
                write(part_b(run, tt, tv, a.scene, utc0, a.still, a.set), out)
    elif a.part == "c":
        for mc in a.motion:
            write(part_c(str(Path(mc).with_suffix(".csv")), a.ftg, a.accel, a.set), out)
    else:
        write(part_a(_ts(a.t0), _ts(a.t1), a.ftg, a.scene, a.set), out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
