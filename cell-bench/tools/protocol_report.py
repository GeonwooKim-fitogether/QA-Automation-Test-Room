"""셀 시험 프로토콜 v1 결과 — 셀 라이브 기록 + 모터 기록 → 셀별 값 · 판정, 속도별 GPS 비, X 세트 비교. 읽기만 하고 결과만 쓴다.

사용 (작업 트리 루트에서):
  .venv/Scripts/python.exe cell-bench/tools/protocol_report.py --live data/live_raw/<시각>.bin --motion data/motion_long_<시각>.json \
      --set Y [--x data/motion_long_A.json:data/live_raw/A.bin ...]
결과: data/protocol_<모터 기록 시각>.json · .csv (셀마다 한 줄). 판정 규칙은 motionbench/qc_protocol.py.
정지 구간 = 셀 기록 시작 60초 뒤 ~ 첫 움직임 2초 전(1단계), 마지막 움직임 2초 뒤부터 180초(3단계). 시각은 PC 수신 시각 − 0.3초(셀 시각 차).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from motionbench import align as A, qc_protocol as Q  # noqa: E402
from compare_motion_cell import read_live  # noqa: E402

CLK = 0.3            # 셀 시각이 PC 보다 약 0.3초 빠르다(README 7절 5번) — 수신 시각에서 빼서 모터 시각에 맞춘다
LAYOUT = ROOT / "motionbench" / "dock_layout.json"


def _ts(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()


def _corr(x, y) -> float:
    mx, my = st.fmean(x), st.fmean(y)
    sx, sy = sum((u - mx) ** 2 for u in x) ** .5, sum((v - my) ** 2 for v in y) ** .5
    return sum((u - mx) * (v - my) for u, v in zip(x, y)) / (sx * sy) if sx and sy else 0.0


def speed_rows(pk: list, t, p, steps: list[tuple[float, float, int]]) -> dict:
    """한 셀 — 지연(0~3초)을 찾아 맞춘 뒤 속도마다 움직일 때 GPS 원래 속도 중앙값과 그 비(GPS ÷ 모터)."""
    a0, b0 = steps[0][0], steps[-1][1]
    fx = [(x - CLK, g.hspeed_raw) for x, g in pk if a0 - 5 <= x - CLK <= b0 + 5]
    if len(fx) < 50:
        return {}
    best = None
    for k in range(31):
        lag = k * 0.1
        mv = [abs(m) for m in A.motor_speed(t, p, [x - lag for x, _ in fx])]
        c = _corr(mv, [g for _, g in fx])
        if best is None or c > best[0]:
            best = (c, lag, mv)
    r, lag, mv = best
    out = {"lag_s": lag, "r": round(r, 2), "slope": round(sum(m * g for m, (_, g) in zip(mv, fx)) / (sum(m * m for m in mv) or 1), 3)}
    for a, b, v in steps:
        mov = [g for (x, g), m in zip(fx, mv) if a <= x - lag <= b and m >= 0.9 * v]
        out[str(v)] = {"moving": st.median(mov) if mov else None, "ratio": round(st.median(mov) / v, 3) if mov else None, "n": len(mov)}
    return out


def analyse(live: str, motion_path: str) -> dict:
    """기록 한 쌍 → {rig, cells: {셀: 값}, windows}."""
    motion = json.loads(Path(motion_path).read_text(encoding="utf-8"))
    t, p = A.load_motion(motion["csv"])
    spans = A.motion_spans(t, p)
    cells = read_live(live)
    t_live0 = min(pk[0][0] for pk in cells.values())
    s1 = (t_live0 + 60, spans[0][0] - 2) if spans else None
    s2 = (spans[-1][1] + 2, spans[-1][1] + 182) if spans else None
    lg = motion.get("long") or {}
    steps = [(_ts(s["from"]), _ts(s["to"]), s["v_mm_s"]) for s in lg.get("steps") or []]
    if not steps and lg.get("from") and lg.get("to"):     # 속도 하나로 돈 오래 왕복(10-28 1차 · 2차 · 150) — 승인 vmax 한 단계로 본다
        steps = [(_ts(lg["from"]), _ts(lg["to"]), motion["approval"]["vmax_mm_s"])]
    allpos = [(g.lat_deg, g.lon_deg) for pk in cells.values() for _, g in pk if g.lat_deg and g.lon_deg]
    la0, lo0 = st.median(a for a, _ in allpos), st.median(b for _, b in allpos)
    kx, ky = 111_320_000 * math.cos(math.radians(la0)), 110_574_000
    out = {}
    for s, pk in cells.items():
        def win(w):
            if not w or w[1] <= w[0]:
                return []
            return [(g.fix, g.cn0, g.sats, g.hspeed_raw,
                     (g.lon_deg - lo0) * kx if g.lon_deg else None, (g.lat_deg - la0) * ky if g.lat_deg else None)
                    for x, g in pk if w[0] <= x - CLK <= w[1]]
        clk = [(g.utc - x) * 1000 for x, g in pk if g.utc]
        out[s] = {"packets": len(pk), "loss_pct": Q.loss_pct([g.seq for _, g in pk if g.seq is not None]),
                  "clock_ms": st.median(clk) if clk else None,
                  "static": Q.static_metrics(win(s1)), "static2": Q.static_metrics(win(s2)),
                  "speeds": speed_rows(pk, t, p, steps) if steps else {}}
    fmt = lambda w: None if not w else [datetime.fromtimestamp(x).strftime("%H:%M:%S") for x in w]
    return {"rig": Q.rig_verdict(motion), "cells": out, "steps": [v for _, _, v in steps],
            "windows": {"static1": fmt(s1), "static2": fmt(s2), "motion": fmt((spans[0][0], spans[-1][1])) if spans else None},
            "live": str(live), "motion": str(motion_path), "stamp": motion.get("stamp")}


def group(cells: dict, steps) -> dict:
    """무리 요약 — 셀 사이 중앙값(최소~최대)."""
    def agg(vals):
        v = [x for x in vals if x is not None]
        return None if not v else {"med": round(st.median(v), 3), "min": round(min(v), 3), "max": round(max(v), 3), "n": len(v)}
    clk = agg([c["clock_ms"] for c in cells.values()])
    dev = [c["clock_ms"] - clk["med"] for c in cells.values() if c["clock_ms"] is not None] if clk else []
    return {"loss_pct": agg([c["loss_pct"] for c in cells.values()]),
            "clock_dev_ms": agg(dev),
            "cn0": agg([c["static"].get("cn0") for c in cells.values()]),
            "sats": agg([c["static"].get("sats") for c in cells.values()]),
            "r_share": agg([c["static"].get("r_share") for c in cells.values()]),
            "still_speed": agg([c["static"].get("still_speed") for c in cells.values()]),
            "ratio": {str(v): agg([(c["speeds"].get(str(v)) or {}).get("ratio") for c in cells.values()]) for v in steps}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--live", required=True); ap.add_argument("--motion", required=True)
    ap.add_argument("--set", default=None, help="독 칸 배치 이름 (motionbench/dock_layout.json)")
    ap.add_argument("--x", nargs="*", default=[], help="비교할 X 세트 기록 '모터.json:라이브.bin' 여러 개")
    ap.add_argument("--out", default=str(ROOT / "data"))
    a = ap.parse_args(argv)
    res = analyse(a.live, a.motion)
    verdict = Q.judge(res["cells"])
    for s, v in verdict.items():
        res["cells"][s]["verdict"] = v
    res["group"] = group(res["cells"], res["steps"])
    if a.set:
        cols = json.loads(LAYOUT.read_text(encoding="utf-8"))[a.set]["columns"]
        res["grid"] = Q.layout_grid(cols, list(res["cells"]))
    xs = []
    for pair in a.x:
        m, l = pair.split(":", 1) if pair.count(":") == 1 else pair.rsplit(":", 1)
        xr = analyse(l, m)
        xs.append({"motion": m, "steps": xr["steps"], "group": group(xr["cells"], xr["steps"]), "windows": xr["windows"]})
    res["x"] = xs
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    stem = f"protocol_{res['stamp']}"
    (out / f"{stem}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    with open(out / f"{stem}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["cell", "verdict", "rx", "clock", "imu", "gps", "loss_pct", "clock_dev_ms", "cn0", "sats", "r_share", "still_speed",
                    *[f"ratio_{v}" for v in res["steps"]]])
        for s, c in res["cells"].items():
            v, sm = c["verdict"], c["static"]
            w.writerow([s, v["판정"], v["수신"], v["시각"], v["IMU"], v["GPS"], c["loss_pct"], v["clock_dev_ms"], sm.get("cn0"), sm.get("sats"),
                        sm.get("r_share"), sm.get("still_speed"), *[(c["speeds"].get(str(x)) or {}).get("ratio") for x in res["steps"]]])
    print(f"장비 {'유효' if res['rig']['유효'] else '무효 — ' + ' · '.join(res['rig']['이유'])} · 셀 {len(res['cells'])}대 · "
          f"합격 {sum(c['verdict']['판정'] == '합격' for c in res['cells'].values())} · "
          f"살펴볼 셀 {sum(c['verdict']['GPS'] == '살펴볼 셀' for c in res['cells'].values())} → {out / stem}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
