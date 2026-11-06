"""보관된 FTG 에서 셀 배터리 IC 의 학습 용량(FullCap)을 뽑아 세트별 gauge.csv 로 쓴다 — 읽기 전용 도구. 장비 · 엔진에 닿지 않는다.

  python tools/gauge_from_ftg.py                      # 세트 1 · data/ftg/NNNN 전부 → data/gauge.csv (이미 있는 사이클은 건너뜀)
  python tools/gauge_from_ftg.py --set 2              # 세트 2 · data/set2/ftg → data/set2/gauge.csv
  python tools/gauge_from_ftg.py --from 1 --to 8 --out C:/tmp/gauge.csv   # 사이클 범위 · 다른 곳에 쓰기
  python tools/gauge_from_ftg.py --force              # 이미 있는 사이클도 다시 센다

한 행 = 셀 하나 · 사이클 하나(FTG 파일 하나). 열은 cellbench/ftglog.py 의 COLUMNS — 표본 수, FullCap 중앙값 · p10 · p90(mAh), 부팅 수(POWER_ON),
POR 수, 설계값(mAh)과 설계값 고정 여부, 펌웨어 판, 최저 전압(mV), 첫 · 마지막 잔량(%).
증분: 출력 파일에 이미 있는 사이클은 다시 읽지 않는다. cycles.csv 가 있으면 거기 줄이 있는(끝난) 사이클만 센다 — 추출 중인 폴더를 반쪽으로 굳히지 않게.
결과판은 이 파일을 /api/gauge 로 읽어 추이 분석의 '안정화 끝(측정)' 과 'IC 재학습 의심' 에 쓴다.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cellbench import ftglog  # noqa: E402

FTG_NAME = re.compile(r"ftg_(\d+)_\d{8}_\d{6}\.bin$")


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("cycle")]


def finished_cycles(set_dir: Path) -> set[int] | None:
    """cycles.csv 에 줄이 있는 사이클(끝난 사이클). 파일이 없으면 None(제한 없음)."""
    p = set_dir / "cycles.csv"
    if not p.exists():
        return None
    with open(p, encoding="utf-8-sig", newline="") as f:
        return {int(r["cycle"]) for r in csv.DictReader(f) if str(r.get("cycle", "")).isdigit()}


def cycle_folders(set_dir: Path, lo: int | None, hi: int | None) -> list[tuple[int, Path]]:
    out = []
    for p in sorted((set_dir / "ftg").glob("[0-9][0-9][0-9][0-9]")):
        if not p.is_dir():
            continue
        c = int(p.name)
        if (lo is None or c >= lo) and (hi is None or c <= hi):
            out.append((c, p))
    return out


def scan_cycle(cycle: int, folder: Path, design: int | None) -> list[dict]:
    rows = []
    for f in sorted(folder.glob("ftg_*.bin")):
        m = FTG_NAME.search(f.name)
        if not m:
            continue
        status, events = ftglog.parse(f.read_bytes())
        rows.append({"cycle": cycle, "serial": int(m.group(1)), **ftglog.summarize(status, events, design)})
    d = design if design is not None else ftglog.cycle_design(rows)     # 세트의 설계값은 하나 — 사이클의 최빈값으로 맞춘다
    return [ftglog.apply_design(r, d) for r in rows]


def write_rows(path: Path, rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: (int(r["cycle"]), int(r["serial"])))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ftglog.COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def run(data: Path, set_id: int, lo: int | None, hi: int | None, out: Path | None, force: bool, design: int | None,
        log=print) -> tuple[Path, list[int]]:
    """→ (쓴 파일, 이번에 센 사이클들). 테스트가 부른다."""
    sd = ftglog.set_dir(data, set_id)
    out = out or sd / "gauge.csv"
    have = read_rows(out)
    done = {int(r["cycle"]) for r in have}
    fin = finished_cycles(sd)
    todo = [(c, p) for c, p in cycle_folders(sd, lo, hi) if (force or c not in done) and (fin is None or c in fin)]
    if not todo:
        log(f"셀 것 없음 — {out} 에 {len(done)}개 사이클이 이미 있다 (다시 세려면 --force)")
        return out, []
    kept = [r for r in have if int(r["cycle"]) not in {c for c, _ in todo}]
    new, seen = [], []
    for c, p in todo:
        t0 = time.time()
        rows = scan_cycle(c, p, design)
        new += rows
        seen.append(c)
        meds = [float(r["fullcap_med"]) for r in rows if r["fullcap_med"] != ""]
        med = f"{sorted(meds)[len(meds) // 2]:.0f} mAh" if meds else "표본 없음"
        log(f"사이클 {c}: 셀 {len(rows)}대 · 세트 중앙값 {med} · 재학습 의심 {sum(ftglog.relearn_suspect(r) for r in rows)}대 · {time.time() - t0:.1f}초")
    write_rows(out, kept + new)
    log(f"→ {out} ({len(kept) + len(new)}행 · 사이클 {len(seen)}개 추가)")
    return out, seen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", default=str(ROOT / "data"), help="기록 폴더 (기본 cell-bench/data)")
    ap.add_argument("--set", type=int, default=1, dest="set_id", help="세트 번호 — 1 은 data/, 2~ 는 data/set{k}/")
    ap.add_argument("--from", type=int, default=None, dest="lo", help="첫 사이클")
    ap.add_argument("--to", type=int, default=None, dest="hi", help="마지막 사이클")
    ap.add_argument("--out", default=None, help="출력 CSV (기본 <세트 폴더>/gauge.csv)")
    ap.add_argument("--force", action="store_true", help="이미 있는 사이클도 다시 센다")
    ap.add_argument("--design", type=int, default=None, help="설계값 mAh 를 직접 준다 (기본은 부팅 직후 값에서 읽는다 — Y4B 750 · X6B 500)")
    a = ap.parse_args(argv)
    data = Path(a.data)
    if not (ftglog.set_dir(data, a.set_id) / "ftg").is_dir():
        print(f"FTG 폴더가 없다: {ftglog.set_dir(data, a.set_id) / 'ftg'}", file=sys.stderr)
        return 2
    run(data, a.set_id, a.lo, a.hi, Path(a.out) if a.out else None, a.force, a.design)
    return 0


if __name__ == "__main__":
    sys.exit(main())
