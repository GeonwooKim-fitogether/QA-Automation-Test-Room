"""보관된 FTG 에서 사이클마다 셀별 방전 용량(Q70 · FullCap · 평균 방전 전류)을 뽑아 세트별 fg_cycles.csv 로 쓴다 — 읽기 전용 도구.

  python tools/fg_cycles.py                 # 세트 1 · data/ftg/NNNN 전부 → data/fg_cycles.csv (이미 있는 사이클은 건너뜀)
  python tools/fg_cycles.py --set 2         # 세트 2 · data/set2/ftg → data/set2/fg_cycles.csv
  python tools/fg_cycles.py --from 1 --to 8 --out C:/tmp/fg.csv   # 사이클 범위 · 다른 곳에 쓰기
  python tools/fg_cycles.py --force         # 이미 있는 사이클도 다시 센다
  python tools/fg_cycles.py --summary       # 세는 일 없이 세트별 기준 · 최근 값만 보인다

한 행 = 셀 하나 · 사이클 하나(FTG 파일 하나). 열과 계산 정의는 cellbench/ftglog.py 의 FG_COLUMNS · discharge().
장비 · 엔진에 닿지 않는다. 결과판 서버(serve_board.py)가 10분마다 같은 계산을 증분으로 돌려(FgFollow) 새 사이클을 이어 붙이고 클라우드에 올린다.
끝난 사이클(cycles.csv 에 줄이 있는 것)만 센다 — 추출 중인 폴더를 반쪽으로 굳히지 않게. 셀이 추출 수보다 모자라면 굳히지 않고 다음 번에 다시 센다.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from cellbench import ftglog  # noqa: E402
from gauge_from_ftg import (FTG_NAME, _other_form, _read_ftg, _span, cycle_folders, expected_cells,  # noqa: E402
                            finished_cycles, read_rows)


def scan_cycle(cycle: int, folder: Path, failed: list | None = None) -> list[dict]:
    files: dict[int, Path] = {}
    for f in sorted(folder.glob("ftg_*.bin*")):
        m = FTG_NAME.search(f.name)
        if m and (int(m.group(1)) not in files or not m.group(2)):     # .bin 과 .bin.gz 가 함께 있으면(압축 중) 원본 .bin
            files[int(m.group(1))] = f
    rows = []
    for serial, f in sorted(files.items()):
        data = None
        for g in (f, _other_form(f)):
            try:
                data = _read_ftg(g)
                break
            except (OSError, EOFError, gzip.BadGzipFile):
                continue
        if data is None:
            if failed is not None:
                failed.append(serial)
            continue
        status, _ = ftglog.parse(data)
        rows.append({"cycle": cycle, "serial": serial, **ftglog.discharge(status)})
    return rows


def write_rows(path: Path, rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: (int(r["cycle"]), int(r["serial"])))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ftglog.FG_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def noted_cycles(sd: Path) -> set[int]:
    """cycles.csv 의 비고(note)가 있는 사이클 — 추세에서 뺀다(예: hold = 방전 중 PC 재시작, pair_mismatch)."""
    p = sd / "cycles.csv"
    if not p.exists():
        return set()
    with open(p, encoding="utf-8-sig", newline="") as f:
        return {int(r["cycle"]) for r in csv.DictReader(f) if str(r.get("cycle", "")).isdigit() and (r.get("note") or "").strip()}


def run(data: Path, set_id: int, lo: int | None = None, hi: int | None = None, out: Path | None = None, force: bool = False,
        log=print) -> tuple[Path, list[int]]:
    """→ (쓴 파일, 이번에 센 사이클들). 결과판 서버의 FgFollow 와 검사가 부른다."""
    sd = ftglog.set_dir(data, set_id)
    out = out or sd / "fg_cycles.csv"
    have = read_rows(out)
    done = {int(r["cycle"]) for r in have}
    fin = finished_cycles(sd)
    folders = cycle_folders(sd, lo, hi)
    todo = [(c, p) for c, p in folders if (force or c not in done) and (fin is None or c in fin)]
    if not todo:
        return out, []
    new, seen = [], []
    for c, p in todo:
        t0 = time.time()
        failed: list[int] = []
        rows = scan_cycle(c, p, failed)
        for s in failed:
            log(f"경고: 사이클 {c} 셀 {s} 읽기 실패 (.bin · .bin.gz 둘 다 — 압축 중일 수 있다)")
        want = expected_cells(sd, c)
        if want is not None and len(rows) < want:
            log(f"경고: 사이클 {c} 셀 {len(rows)}대만 읽혔다 (추출 {want}대) — 굳히지 않는다. 다음 실행에서 다시 센다")
            continue
        new += rows
        seen.append(c)
        q = [float(r["q70_mah"]) for r in rows if ftglog.fg_valid(r) and int(r["serial"]) not in ftglog.FG_EXCLUDE.get(set_id, {})]
        log(f"사이클 {c}: 셀 {len(rows)}대 · 유효 {len(q)}대" + (f" · Q70 중앙값 {statistics.median(q):.0f} mAh" if q else "")
            + f" · {time.time() - t0:.1f}초")
    if not seen:
        return out, []
    allrows = [r for r in have if int(r["cycle"]) not in set(seen)] + new
    write_rows(out, allrows)
    log(f"→ {out} ({len(allrows)}행 · 사이클 {_span(seen)} 추가)")
    return out, seen


def summary(data: Path, set_id: int, log=print) -> dict | None:
    """세트의 기준(사이클 6~20 중앙값)과 최근 10회 평균을 보인다 — 검산용."""
    sd = ftglog.set_dir(data, set_id)
    rows = read_rows(sd / "fg_cycles.csv")
    if not rows:
        log(f"세트 {set_id}: fg_cycles.csv 가 없다")
        return None
    ser = ftglog.fg_set_series(rows, set_id, noted_cycles(sd))
    base = {k: ftglog.fg_baseline(ser, k) for k in ("q70", "fullcap", "ma", "dis_h")}
    last = list(ser)[-10:]
    rec = {k: statistics.mean(ser[c][k] for c in last if ser[c][k] is not None) for k in base}
    pct = lambda k: (rec[k] / base[k] - 1) * 100 if base[k] else float("nan")
    log(f"세트 {set_id}: 사이클 {len(ser)}개 · 기준(사이클 {ftglog.FG_BASE[0]}~{ftglog.FG_BASE[1]} 중앙값) "
        f"Q70 {base['q70']:.1f} mAh · FullCap {base['fullcap']:.1f} mAh · 전류 {base['ma']:.1f} mA · 사용시간 {base['dis_h']:.3f} h")
    log(f"  최근 10회(사이클 {_span(last)}) Q70 {rec['q70']:.1f} ({pct('q70'):+.1f} %) · FullCap {rec['fullcap']:.1f} ({pct('fullcap'):+.1f} %) · "
        f"전류 {rec['ma']:.1f} ({pct('ma'):+.1f} %) · 사용시간 {rec['dis_h']:.3f} ({pct('dis_h'):+.1f} %)")
    return {"base": base, "recent": rec, "cycles": list(ser)}


def main(argv=None) -> int:
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--set", type=int, default=1, dest="set_id")
    ap.add_argument("--from", type=int, default=None, dest="lo")
    ap.add_argument("--to", type=int, default=None, dest="hi")
    ap.add_argument("--out", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--summary", action="store_true", help="세지 않고 기준 · 최근 값만 보인다")
    a = ap.parse_args(argv)
    data = Path(a.data)
    if not a.summary:
        if not (ftglog.set_dir(data, a.set_id) / "ftg").is_dir():
            print(f"FTG 폴더가 없다: {ftglog.set_dir(data, a.set_id) / 'ftg'}", file=sys.stderr)
            return 2
        run(data, a.set_id, a.lo, a.hi, Path(a.out) if a.out else None, a.force)
    summary(data, a.set_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
