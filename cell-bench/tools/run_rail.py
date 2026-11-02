"""레일 시험 반복 실행기 — 파트 C(레일 가속도 + 정지 장면) · 파트 B(레일 왕복 + 같은 경로 장면)를 n 번 돈다.

  python cell-bench/tools/run_rail.py C --n 10 --out cell-bench/data/collect_20261102/C1
  python cell-bench/tools/run_rail.py B --n 10 --out cell-bench/data/collect_20261102/B1

하기 전에(담당자 절차서 dynamic-bench/design/qc-operator-procedure.md):
  · live_tap 이 돌고 있어야 한다(셀 배터리 · 위치를 이 파일로 읽는다): python cell-bench/motionbench/live_tap.py --minutes 60 --qc-now --part C
  · 입회 표지(data/motion_attended)가 남은 시간 안이어야 한다 — 운전자가 현장에서 확인한 뒤에만 만든다.
  · 모터 값은 사용자가 승인한 프리셋(dynamic-bench/dev/presets/long-<파트>.json)만 쓴다. 실행기는 그 파일을
    dev/long-approval.json 으로 옮겨 놓을 뿐 값을 만들지 않는다.
파트 B 는 LabSat 에 장면 S03_RailB120 을 틀고, 재생 명령 뒤 2.75초(2026-10-30 실측) + 정지 20초에 레일이 출발하도록 모터를 띄운다.
끝나면 out/runs.json 에 실행마다 모터 기록 경로와 시각을 남긴다. 판정은 tools/protocol_v4.py 가 한다.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motionbench import charge  # noqa: E402

REPO = ROOT.parent
DATA = ROOT / "data"
DEV = REPO / "dynamic-bench" / "dev"
PRESETS = DEV / "presets"
APPROVAL = DEV / "long-approval.json"
MARKER = DATA / "motion_attended"
PY = sys.executable
SCENE_B, SKY_DELAY_S, STILL_S = "S03_RailB120", 2.75, 20.0


def say(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def marker_until() -> float:
    try:
        return float(json.loads(MARKER.read_text(encoding="utf-8"))["until"])
    except (OSError, ValueError, KeyError):
        return 0.0


def install_preset(part: str) -> dict:
    """사용자가 승인한 프리셋을 승인 파일 자리에 둔다. 프리셋이 없으면 멈춘다 — 값을 지어내지 않는다."""
    src = PRESETS / f"long-{part}.json"
    if not src.exists():
        raise SystemExit(f"승인된 프리셋 {src} 이 없다 — 사용자 승인 뒤에 만든다")
    shutil.copyfile(src, APPROVAL)
    return json.loads(src.read_text(encoding="utf-8"))


def preflight(part: str, n: int, per_run_s: float) -> None:
    left = marker_until() - time.time()
    need = n * per_run_s + 120
    if left < need:
        raise SystemExit(f"입회 표지 남은 시간 {max(left, 0) / 60:.0f}분 < 필요 {need / 60:.0f}분 — 현장 확인을 다시 받는다")
    low, nc, why = charge.rail_low()
    prob = charge.start_problem(low, why)
    if prob:
        raise SystemExit(prob)
    say(f"파트 {part} {n}번 시작 — 가장 낮은 셀 {low}% ({nc}대) · 입회 표지 {left / 60:.0f}분 남음")


def motor_once(log: Path) -> tuple[int, str, str | None, float]:
    t_cmd = time.time()
    with open(log, "w", encoding="utf-8") as lf:
        rc = subprocess.call([PY, "-X", "utf8", str(ROOT / "run_motion.py"), "long"], cwd=REPO, stdout=lf, stderr=subprocess.STDOUT)
    txt = log.read_text(encoding="utf-8", errors="replace")
    why = next((l.split("끝난 이유: ")[-1] for l in txt.splitlines() if "끝난 이유" in l), "?")
    csvs = [c for c in glob.glob(str(DATA / "motion_long_*.csv")) if not c.endswith("_trace.csv") and os.path.getmtime(c) >= t_cmd - 1]
    return rc, why, max(csvs, key=os.path.getmtime) if csvs else None, t_cmd


def first_move(csv_path: str | None, thr_um: int = 500) -> float | None:
    if not csv_path:
        return None
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    p0 = int(rows[0]["pos"])
    return next((float(r["t_wall"]) for r in rows if abs(int(r["pos"]) - p0) > thr_um), None)


def run_c(n: int, out: Path, runs: list) -> int:
    for k in range(1, n + 1):
        i = len(runs) + 1
        if charge.should_stop(charge.rail_low()[0]):
            say("가장 낮은 셀이 멈춤 기준 이하 — 충전부터"); return 2
        rc, why, csvp, t_cmd = motor_once(out / f"run{i}_motor.log")
        runs.append({"i": i, "t_motor_cmd": t_cmd, "rc": rc, "why": why, "motor_csv": csvp})
        (out / "runs.json").write_text(json.dumps(runs, ensure_ascii=False, indent=1), encoding="utf-8")
        say(f"C {i}번째 끝 · 코드 {rc} · {why}")
        if rc != 0:
            say("정상으로 끝나지 않아 멈춘다"); return 1
        time.sleep(5)
    return 0


def run_b(n: int, out: Path, runs: list, lead: float) -> int:
    from motionbench.labsat import LabSat
    ls = LabSat()
    for k in range(1, n + 1):
        i = len(runs) + 1
        if charge.should_stop(charge.rail_low()[0]):
            say("가장 낮은 셀이 멈춤 기준 이하 — 충전부터"); return 2
        ls.stop(); time.sleep(2)
        t_play = ls.play(SCENE_B)
        t_go = t_play + SKY_DELAY_S + STILL_S - lead
        say(f"B {i}번째 — 재생 시작 · 모터는 {t_go - time.time():.1f}초 뒤 (lead {lead:.2f})")
        while time.time() < t_go:
            time.sleep(0.005)
        rc, why, csvp, t_cmd = motor_once(out / f"run{i}_motor.log")
        try:
            ls.stop()
        except Exception as e:  # 재생 멈춤이 실패해도 다음 재생 명령이 처음부터 다시 튼다
            say(f"PLAY:STOP 실패 {e}")
        fm = first_move(csvp)
        off = None if fm is None else round(fm - (t_play + SKY_DELAY_S + STILL_S), 3)
        runs.append({"i": i, "t_play": t_play, "t_motor_cmd": t_cmd, "t_first_move": fm, "rail_minus_sky_s": off,
                     "lead_used": lead, "sky_delay_used": SKY_DELAY_S, "rc": rc, "why": why, "motor_csv": csvp})
        (out / "runs.json").write_text(json.dumps(runs, ensure_ascii=False, indent=1), encoding="utf-8")
        say(f"B {i}번째 끝 · 코드 {rc} · {why} · 레일 출발 − 하늘 출발 {off}초")
        if rc != 0:
            say("정상으로 끝나지 않아 멈춘다"); return 1
        if fm:
            lead = fm - t_cmd
        time.sleep(5)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("part", choices=["C", "B"])
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lead", type=float, default=5.3, help="파트 B — 모터 명령에서 첫 움직임까지 첫 추정(초). 둘째 번부터는 앞 번 실측")
    a = ap.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    preset = install_preset(a.part)
    per = 110.0 if a.part == "B" else 95.0
    preflight(a.part, a.n, per)
    (out / "preset.json").write_text(json.dumps(preset, ensure_ascii=False, indent=1), encoding="utf-8")
    rp = out / "runs.json"
    runs = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else []
    rc = run_c(a.n, out, runs) if a.part == "C" else run_b(a.n, out, runs, a.lead)
    say(f"끝 → {rp}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
