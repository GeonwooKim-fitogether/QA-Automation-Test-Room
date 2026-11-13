"""FIRMWARE-1 · IMU 펌웨어 시험 판정(첫 판, 살펴봄) — 가속도 단계마다 셀의 '움직임' 기록(0x0E, 0.01초 값)이 운전 시간의 몇 % 를 덮는가,
단계 사이 정지에서는 몇 % 인가, 운전 시작에서 첫 '움직임' 기록까지 몇 초인가.
셀은 IMU 움직임 판정이 '움직임' 이면 0x0E 를, '움직임 아님' 이면 0x0F(0.1초 평균)를 기록한다(2026-11-10 확인). 합격 기준은 아직 없다.

알려진 약점(고치는 것은 2026-11-13 정리 범위 밖 — dynamic-bench/design/qc-tests.md FIRMWARE-1 '남은 것'):
  1. 셀 시계 차(PC 시각 − 셀 시각)를 운전 전체에서 값 하나로만 맞춘다. 그래서 '켜짐 지연' 칸(초)은 믿을 수 없다 — 셀 시계가 운전 동안
     조금만 흘러도 지연이 그만큼 틀린다. 꺼짐 지연은 아직 내지 않는다.
  2. 셀 시계가 기록 도중 뛴 셀(2026-11-12 첫 운전의 10680 · 10694)은 기록이 운전 구간 밖으로 밀려 모든 단계가 0 % 로 나온다.
     0 % 셀은 불량이라 읽기 전에 그 셀의 시계를 먼저 본다.

입력: 기록 폴더(firmware1_step_run.py 의 data/FIRMWARE-1_<날짜>[꼬리표], 또는 첫 운전 data/imuStep_20261112-1744)의 summary.json —
results[*].motor_json(단계마다 모터 기록) · a_mm_s2, ftg(받은 셀 기록 폴더).
독 안 셀: summary.json 의 columns(실행기가 적는다)로 가른다. 없으면 --dock 으로 준다(첫 운전은 독 12대, 나머지는 독 밖에 둔 셀).
결과: 화면 표와 <기록 폴더>/firmware1_step_judge.json — 셀마다 on_pct(단계별 %) · delay_s(단계별 켜짐 지연, 위 약점 1) · gap_pct(단계 사이 정지 %).
FTG 를 읽으므로 pandas 와 ohcoach-cell-tools 의 FtgParser 가 있는 파이썬(제어 PC 의 Python 3.10)으로 돌린다.
사용: python -X utf8 firmware1_step_judge.py <기록 폴더 — 이름만 주면 data/ 아래> [--dock 10689,10690,...]"""
import argparse
import json
import statistics as st
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]          # <작업 트리>/cell-bench
for _p in (HERE, ROOT / "tools", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import raw_layer as R  # noqa: E402  (FtgParser 경로도 여기서 잡힌다)
import protocol_v4 as P  # noqa: E402
from motionbench import align as A  # noqa: E402

DATA = ROOT / "data"
MOVE_MM = 0.5          # 레일이 이만큼 움직이면 운전 중으로 본다
REC_S = 0.01           # 0x0E 한 표본의 시간
GAP_SKIP_S = (3, 1)    # 단계 사이 정지 — 앞 단계 끝 3초 뒤부터 다음 단계 시작 1초 앞까지만 본다


def step_spans(summ: dict) -> list[tuple[int, float, float]]:
    """단계마다 (가속도, 첫 움직임 PC 시각, 마지막 움직임 PC 시각) — 모터 기록에서."""
    out = []
    for r in summ.get("results") or []:
        if not r.get("motor_json"):
            continue
        t, p = A.load_motion(str(Path(r["motor_json"]).with_suffix(".csv")))
        t = np.asarray(t); p = np.asarray(p, float)
        mv0 = np.where(np.abs(p - p[0]) > MOVE_MM)[0]
        mv1 = np.where(np.abs(p - p[-1]) > MOVE_MM)[0]
        if len(mv0) and len(mv1):
            out.append((int(r["a_mm_s2"]), float(t[mv0[0]]), float(t[mv1[-1]])))
    return out


def dock_cells(summ: dict, arg: str | None) -> set[int] | None:
    if arg:
        return {int(x) for x in arg.split(",") if x.strip()}
    cols = summ.get("columns")
    return {int(s) for c in cols for s in c} if cols else None


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", help="기록 폴더 — 예 FIRMWARE-1_20261113 또는 imuStep_20261112-1744")
    ap.add_argument("--dock", help="독 안 셀 시리얼, 쉼표로 — summary.json 에 columns 가 없을 때")
    a = ap.parse_args(argv)
    d = Path(a.folder) if Path(a.folder).is_dir() else DATA / a.folder
    summ = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    steps = step_spans(summ)
    if not steps:
        raise SystemExit(f"{d.name} — 모터 기록이 있는 단계가 없다")
    if not summ.get("ftg"):
        raise SystemExit(f"{d.name} — 받은 셀 기록(summary.json 의 ftg)이 없다")
    off = P.clock_offset(P.live(steps[0][1] - 5, steps[-1][2] + 5))      # 값 하나 — 위 약점 1
    if off is None:
        raise SystemExit("셀 시계 차를 잴 수 없다 — 그 시간의 실시간 기록이 없다")
    print("셀 시계 차(PC − 셀):", round(off, 2), "초 — 운전 전체에 값 하나로 쓴다(켜짐 지연은 참고만)")
    dock = dock_cells(summ, a.dock)
    out = {}
    for f in sorted(Path(summ["ftg"]).glob("ftg_*.bin")):
        s = int(f.name.split("_")[1])
        _, e, _ = R.read_file(f)
        et = (np.asarray(e[0]) + off) if e is not None else np.array([])
        row = []
        for _, t0, t1 in steps:
            on = ((et >= t0) & (et <= t1)).sum() * REC_S / (t1 - t0) * 100
            first = et[et >= t0 - 1][:1]
            delay = float(first[0] - t0) if len(first) and first[0] < t1 else None
            row.append((round(on), None if delay is None else round(delay, 2)))
        gaps = []
        for (_, _, t1), (_, u0, _) in zip(steps, steps[1:]):
            g0, g1 = t1 + GAP_SKIP_S[0], u0 - GAP_SKIP_S[1]
            gaps.append(round(((et >= g0) & (et <= g1)).sum() * REC_S / max(g1 - g0, 0.1) * 100))
        out[s] = (row, gaps)
    hdr = "  ".join(f"{acc / 1000:g}" for acc, _, _ in steps)
    print("운전 시간:", [round(t1 - t0, 1) for _, t0, t1 in steps], "초")
    print(f"\n'움직임' 기록이 덮은 비율 %  (가속도 m/s²: {hdr})")
    for s in sorted(out):
        row, gaps = out[s]
        where = "" if dock is None else ("독 " if s in dock else "밖 ")
        print(f"{s} {where} " + " ".join(f"{x[0]:>4}" for x in row) + "   | 정지 구간 % " + " ".join(f"{g:>3}" for g in gaps)
              + "   | 켜짐 지연 초(참고) " + " ".join("  -  " if x[1] is None else f"{x[1]:>5}" for x in row)
              + ("   ← 모두 0 % — 셀 시계가 뛰었는지 먼저 본다" if all(x[0] == 0 for x in row) else ""))
    sel = [out[s][0] for s in out if dock is None or s in dock]
    if sel:
        print(f"\n{'독 안' if dock else '모든'} {len(sel)}대 중앙값 %:", [int(st.median(r[i][0] for r in sel)) for i in range(len(steps))])
        print(f"{'독 안' if dock else '모든'} {len(sel)}대 범위 %:", [(min(r[i][0] for r in sel), max(r[i][0] for r in sel)) for i in range(len(steps))])
    res = {"clock_offset_s": off, "steps_mm_s2": [acc for acc, _, _ in steps],
           "cells": {str(s): {"on_pct": [x[0] for x in v[0]], "delay_s": [x[1] for x in v[0]], "gap_pct": v[1],
                              **({} if dock is None else {"in_dock": s in dock})} for s, v in out.items()}}
    (d / "firmware1_step_judge.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
