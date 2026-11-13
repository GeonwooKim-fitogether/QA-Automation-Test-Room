"""보관된 FTG 에서 셀 배터리 IC 의 학습 용량(FullCap)을 뽑아 세트별 gauge.csv 로 쓴다 — 읽기 전용 도구. 장비 · 엔진에 닿지 않는다.

  python tools/gauge_from_ftg.py                      # 세트 1 · data/ftg/NNNN 전부 → data/gauge.csv (이미 있는 사이클은 건너뜀)
  python tools/gauge_from_ftg.py --set 2              # 세트 2 · data/set2/ftg → data/set2/gauge.csv
  python tools/gauge_from_ftg.py --from 1 --to 8 --out C:/tmp/gauge.csv   # 사이클 범위 · 다른 곳에 쓰기
  python tools/gauge_from_ftg.py --force              # 이미 있는 사이클도 다시 센다

한 행 = 셀 하나 · 사이클 하나(FTG 파일 하나). 열은 cellbench/ftglog.py 의 COLUMNS — 표본 수, FullCap 중앙값 · p10 · p90(mAh), 부팅 수(POWER_ON),
POR 수, 설계값(mAh)과 설계값 고정 여부, 펌웨어 판, 최저 전압(mV), 첫 · 마지막 잔량(%), 그리고 그 행을 처음 센 시각(counted_at).
counted_at 은 결과판이 '측정이 기준 확정 전에 있었나' 를 가르는 데 쓴다 — --force 로 다시 세어도 처음 센 시각을 지킨다(기준이 뒤집히지 않게).
증분: 출력 파일에 이미 있는 사이클은 다시 읽지 않는다. cycles.csv 가 있으면 거기 줄이 있는(끝난) 사이클만 센다 — 추출 중인 폴더를 반쪽으로 굳히지 않게.
결과판은 이 파일을 /api/gauge 로 읽어 추이 분석의 '안정화 끝(측정)' 과 'IC 재학습 의심' 에 쓴다.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cellbench import ftglog  # noqa: E402

FTG_NAME = re.compile(r"ftg_(\d+)_\d{8}_\d{6}\.bin(\.gz)?$")   # 보관하며 gzip 으로 줄인 것(.bin.gz)도 읽는다


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


def _read_ftg(f: Path) -> bytes:
    return gzip.decompress(f.read_bytes()) if f.suffix == ".gz" else f.read_bytes()


def _other_form(f: Path) -> Path:
    """ftg_….bin ↔ ftg_….bin.gz — 압축 작업이 .bin 을 막 지웠거나 .gz 를 아직 쓰는 중이면 다른 쪽이 읽힌다."""
    return f.with_name(f.name[:-3]) if f.suffix == ".gz" else f.with_name(f.name + ".gz")


def scan_cycle(cycle: int, folder: Path, design: int | None, failed: list | None = None) -> list[dict]:
    """한 사이클 폴더의 셀들을 센다. 읽기에 실패하면 다른 형식(.bin ↔ .bin.gz)으로 한 번 더 읽고, 그래도 실패한 셀 번호는 failed 에 담는다."""
    rows, files = [], {}
    for f in sorted(folder.glob("ftg_*.bin*")):
        m = FTG_NAME.search(f.name)
        if m and (int(m.group(1)) not in files or not m.group(2)):     # 같은 셀의 .bin 과 .bin.gz 가 함께 있으면(압축 중) 원본 .bin 을 쓴다
            files[int(m.group(1))] = f
    for serial, f in sorted(files.items()):
        data = None
        for g in (f, _other_form(f)):
            try:
                data = _read_ftg(g)
                break
            except (OSError, EOFError, gzip.BadGzipFile):              # 지워졌거나 덜 써진 파일 — 다른 형식으로 한 번 더
                continue
        if data is None:
            if failed is not None:
                failed.append(serial)
            continue
        status, events = ftglog.parse(data)
        rows.append({"cycle": cycle, "serial": serial, **ftglog.summarize(status, events, design)})
    d = design if design is not None else ftglog.cycle_design(rows)     # 세트의 설계값은 하나 — 사이클의 최빈값으로 맞춘다
    return [ftglog.apply_design(r, d) for r in rows]


def expected_cells(set_dir: Path, cycle: int) -> int | None:
    """그 사이클에 추출이 성공한 셀 수 — cells_NNNN.csv 에서 오류 없이 파일이 있는 줄. 그 파일이 없으면 직전 사이클의 것, 둘 다 없으면 None(검사 안 함)."""
    for c in (cycle, cycle - 1):
        p = set_dir / f"cells_{c:04d}.csv"
        if p.exists():
            with open(p, encoding="utf-8-sig", newline="") as f:
                return len({r["serial"] for r in csv.DictReader(f) if not (r.get("error") or "").strip() and (r.get("file") or "").strip()})
    return None


def write_rows(path: Path, rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: (int(r["cycle"]), int(r["serial"])))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ftglog.COLUMNS + ["counted_at"], extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def _span(cs: list[int]) -> str:
    return "" if not cs else str(cs[0]) if len(cs) == 1 else f"{cs[0]}~{cs[-1]}" if cs[-1] - cs[0] + 1 == len(cs) else ", ".join(map(str, cs))


def run(data: Path, set_id: int, lo: int | None, hi: int | None, out: Path | None, force: bool, design: int | None,
        log=print) -> tuple[Path, list[int]]:
    """→ (쓴 파일, 이번에 센 사이클들). 테스트가 부른다."""
    sd = ftglog.set_dir(data, set_id)
    out = out or sd / "gauge.csv"
    if lo is not None and hi is not None and lo > hi:
        log(f"범위가 뒤집혔다 (--from {lo} > --to {hi}) — 센 것 없음")
        return out, []
    have = read_rows(out)
    done = {int(r["cycle"]) for r in have}
    fin = finished_cycles(sd)
    folders = cycle_folders(sd, lo, hi)
    open_ = [c for c, _ in folders if fin is not None and c not in fin]          # 아직 끝나지 않은(cycles.csv 에 줄이 없는) 사이클
    todo = [(c, p) for c, p in folders if (force or c not in done) and (fin is None or c in fin)]
    wait = f"아직 끝나지 않은 사이클 {_span(open_)} (cycles.csv 에 줄이 없다) — 충전까지 끝난 뒤 다시 돌리면 된다" if open_ else ""
    if not todo:
        rng = f"사이클 {lo if lo is not None else '처음'}~{hi if hi is not None else '끝'}"
        if not folders:
            log(f"센 것 없음 — {sd / 'ftg'} 에 {rng} 의 FTG 폴더가 없다")
        elif not [c for c, _ in folders if c not in open_]:
            log(f"센 것 없음 — {wait}")
        else:
            log(f"센 것 없음 — {out} 에 {len(done)}개 사이클이 이미 있다 (다시 세려면 --force)" + (f" · {wait}" if wait else ""))
        return out, []
    if wait:
        log(wait + " · 그 사이클은 건너뛴다")
    first_at = {(int(r["cycle"]), int(r["serial"])): r.get("counted_at") or "" for r in have}   # --force 로 다시 세도 처음 센 시각을 지킨다
    new, seen = [], []
    for c, p in todo:
        t0 = time.time()
        now = time.strftime("%Y-%m-%d %H:%M:%S")                   # cycles.csv 의 시각과 같은 꼴(현지 시각) — 결과판이 글자로 비교한다
        failed: list[int] = []
        rows = [{**r, "counted_at": first_at.get((c, int(r["serial"]))) or now} for r in scan_cycle(c, p, design, failed)]
        for s in failed:
            log(f"경고: 사이클 {c} 셀 {s} 읽기 실패 (.bin · .bin.gz 둘 다 — 압축 중일 수 있다)")
        want = expected_cells(sd, c)
        if want is not None and len(rows) < want:                  # 셀이 모자라면 굳히지 않는다 — 파일에 안 쓰면 다음 실행이 다시 센다
            log(f"경고: 사이클 {c} 셀 {len(rows)}대만 읽혔다 (추출 {want}대) — 센 사이클로 굳히지 않는다. 다음 실행에서 다시 센다")
            continue
        new += rows
        seen.append(c)
        med = ftglog.set_medians(rows).get(c)                     # 결과판과 같은 진짜 중앙값(짝수면 가운데 두 값의 평균)
        kinds = list(ftglog.suspect_kinds(rows).values())
        other = [f"{n} {kinds.count(k)}대" for k, n in (("pre", "학습 전"), ("short", "기록 짧음")) if kinds.count(k)]
        log(f"사이클 {c}: 셀 {len(rows)}대 · 세트 중앙값 {f'{med:.0f} mAh' if med is not None else '표본 없음'} · 재학습 의심 {kinds.count('relearn')}대"
            + (f" (따로: {' · '.join(other)})" if other else "") + f" · {time.time() - t0:.1f}초")
    kept = [r for r in have if int(r["cycle"]) not in set(seen)]   # 다시 센 사이클만 바꾼다 — 굳히지 못한 사이클은 옛 행이 있으면 그대로
    allrows = kept + new
    write_rows(out, allrows)
    log(f"→ {out} ({len(allrows)}행 · 사이클 {len(seen)}개 추가)")
    meds = ftglog.set_medians(allrows)
    hole = None if ftglog.stable_end(meds) is not None else ftglog.record_hole(meds)   # 결과판과 같은 판정 — 앞에서 이미 잰 안정화 끝은 뒤의 빈틈과 무관하다
    if hole is not None:
        why = f"사이클 {min(meds)} 부터라" if hole == 1 else f"사이클 {hole} 의 기록이 비어 있어"
        log(f"경고: {out} 의 IC 기록이 {why} 결과판이 안정화 끝을 잴 수 없다 — 옛 규칙(사이클 1~3 제외)을 쓴다. "
            f"--from 1 로 다시 돌리면 빈 사이클이 채워진다(이미 센 사이클은 건너뛴다)")
    return out, seen


def main(argv=None) -> int:
    for st in (sys.stdout, sys.stderr):                            # 출력을 파일 · 파이프로 받으면 Windows 기본(cp949)이 '—' 를 못 써 죽는다
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
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
