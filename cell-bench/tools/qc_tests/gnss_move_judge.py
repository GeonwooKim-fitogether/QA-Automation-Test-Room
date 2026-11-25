"""GNSS-MOVE · GNSS 레일 시험 셀 최종 판정 — 운전마다의 판정 산출(protocol_v4.py 가 쓴 protocol_<시각>.json)을 셀 × 속도로 모은다.
규칙(2026-11-25 사용자 결정 A — 숫자는 임시, 기준 파일 motionbench/criteria/qc_tests.json 의 GNSS-MOVE.cell_rule):
  셀 수 운전 = 그 속도의 장비 유효 운전 중 그 셀의 속도 오차(gps_err)가 있는 운전. min_runs 보다 적으면 그 속도 '재측정(운전 부족)'.
  걸린 운전 = |gps_err| > err_center_max_mm_s.  속도마다 걸린 운전 0 → 통과 · 1 → 재측정(그 속도만 retest_runs 번 다시) · 2 이상 → 불합격.
  재측정 회차(--retest-of 로 앞 판정 파일을 주면): 재측정이던 속도는 새로 돈 운전만 세어 1번이라도 걸리면 불합격, 0번이면 통과.
  셀: 모든 속도 통과 → 합격 · 하나라도 불합격 → 불합격 · 그 밖 → 재측정. rx(실시간 패킷 빠짐) · 위치 비율 · 위치 잡기까지는 합불에 넣지 않는다.
단계별 거르기: 실행기가 뺀 셀(summary.json 의 excluded)과 셀 번호 1(시료 등 판정 불가)은 건너뛴다. 거르기 파일에는 아직 적지 않는다.
사용: python -X utf8 gnss_move_judge.py <기록 폴더 — 예 GNSS-MOVE_20261125_confirm> [--judged <판정 산출 폴더, 기본 data>] [--retest-of <앞 gnss_move_cells.json>]
결과: <기록 폴더>/gnss_move_cells.json"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from motionbench import cell_models  # noqa: E402

DATA = ROOT / "data"
CRIT_FILE = ROOT / "motionbench" / "criteria" / "qc_tests.json"
PASS, RETEST, FAIL = "통과", "재측정", "불합격"      # 속도 판정
CELL_PASS = "합격"                                      # 셀 판정(불합격 · 재측정은 같은 말)


def rule(path: Path = CRIT_FILE) -> dict:
    t = json.loads(Path(path).read_text(encoding="utf-8"))["tests"]["GNSS-MOVE"]
    return {"lim": float(t["err_center_max_mm_s"]), **t["cell_rule"]}


def speed_verdict(errs: list[float], r: dict, retest: bool = False) -> tuple[str, str]:
    """한 셀 · 한 속도의 운전마다 오차 [mm/s] → (판정, 이유). retest 면 재측정 회차(새 운전만, 1번이라도 걸리면 불합격)."""
    n, bad = len(errs), sum(1 for e in errs if abs(e) > r["lim"])
    if n < r["min_runs"]:
        return RETEST, f"셀 수 운전 {n}번 < {r['min_runs']}번(운전 부족)"
    if retest:
        return (FAIL, f"재측정에서 걸린 운전 {bad}/{n}") if bad else (PASS, f"재측정 걸린 운전 0/{n}")
    if bad >= r["fail_runs"]:
        return FAIL, f"걸린 운전 {bad}/{n} ≥ {r['fail_runs']}"
    if bad == 1:
        return RETEST, f"걸린 운전 1/{n} — 그 속도만 {r['retest_runs']}번 다시"
    return PASS, f"걸린 운전 0/{n}"


def cell_verdict(speeds: dict[str, str]) -> str:
    vs = list(speeds.values())
    return FAIL if FAIL in vs else CELL_PASS if vs and all(v == PASS for v in vs) else RETEST


def collect(folder: Path, judged: Path) -> tuple[dict[str, dict[str, list[float]]], dict]:
    """기록 폴더의 v<속도>/runs.json 마다 판정 산출을 찾아 {셀: {속도: [유효 운전의 gps_err]}} 와 운전 장부를 돌려준다."""
    per: dict[str, dict[str, list[float]]] = {}
    book = {}
    for rp in sorted(folder.glob("v*/runs.json")):
        v = rp.parent.name[1:]
        rows = []
        for run in json.loads(rp.read_text(encoding="utf-8")):
            if run.get("rc") != 0 or not run.get("motor_csv"):
                continue
            stamp = Path(run["motor_csv"]).stem.split("_")[-1]
            f = Path(judged) / f"protocol_{stamp}.json"
            if not f.exists():
                rows.append({"run": run.get("i"), "stamp": stamp, "used": False, "why": "판정 산출 없음"})
                continue
            d = json.loads(f.read_text(encoding="utf-8"))
            if not d["rig"].get("유효"):
                rows.append({"run": run.get("i"), "stamp": stamp, "used": False, "why": " · ".join(d["rig"].get("이유") or [])})
                continue
            rows.append({"run": run.get("i"), "stamp": stamp, "used": True})
            for s, c in d["cells"].items():
                e = (c["items"].get("gps_err") or {}).get("value")
                if e is not None:
                    per.setdefault(s, {}).setdefault(v, []).append(float(e))
        book[v] = rows
    return per, book


def judge(folder: Path, judged: Path, r: dict, prev: dict | None = None) -> dict:
    summ = json.loads((folder / "summary.json").read_text(encoding="utf-8")) if (folder / "summary.json").exists() else {}
    excluded = {str(k) for k in (summ.get("excluded") or {})}
    per, book = collect(folder, judged)
    cells = {}
    for s in sorted(per, key=int):
        if s in excluded or int(s) in cell_models.NO_SERIAL:
            continue
        before = ((prev or {}).get("cells") or {}).get(s, {}).get("speeds", {})
        sp = {}
        for v, errs in sorted(per[s].items(), key=lambda kv: int(kv[0])):
            retest = (before.get(v) or {}).get("verdict") == RETEST
            verdict, why = speed_verdict(errs, r, retest)
            sp[v] = {"verdict": verdict, "why": why, "n": len(errs), "bad": sum(1 for e in errs if abs(e) > r["lim"]),
                     "errs": [round(e, 1) for e in errs], "retest": retest}
        merged = {**{v: x for v, x in before.items()}, **sp}       # 재측정 회차는 다시 돈 속도만 바꾼다
        cells[s] = {"verdict": cell_verdict({v: x["verdict"] for v, x in merged.items()}), "speeds": merged}
    return {"rule": r, "folder": folder.name, "judged": str(judged), "runs": book, "cells": cells,
            "skipped": sorted(s for s in per if s in excluded or int(s) in cell_models.NO_SERIAL)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder")
    ap.add_argument("--judged", default=str(DATA))
    ap.add_argument("--retest-of")
    ap.add_argument("--data", default=str(DATA))
    a = ap.parse_args(argv)
    folder = Path(a.folder) if Path(a.folder).is_absolute() else Path(a.data) / a.folder
    prev = json.loads(Path(a.retest_of).read_text(encoding="utf-8")) if a.retest_of else None
    out = judge(folder, Path(a.judged), rule(), prev)
    (folder / "gnss_move_cells.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    r = out["rule"]
    print(f"GNSS-이동 셀 판정 · 기준 ±{r['lim']:g} mm/s · 걸린 운전 0 통과 · 1 재측정 · {r['fail_runs']} 이상 불합격 (임시)")
    for v, rows in out["runs"].items():
        print(f"  {v} mm/s 운전 {len(rows)}번 · 쓴 운전 {sum(1 for x in rows if x['used'])}번")
    for s, c in out["cells"].items():
        print(f"  {s}: {c['verdict']} — " + " · ".join(f"{v} {x['verdict']}({x['why']})" for v, x in c["speeds"].items()))
    if out["skipped"]:
        print(f"  건너뜀(뺀 셀 · 판정 불가): {out['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
