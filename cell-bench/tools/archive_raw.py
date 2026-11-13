"""다 쓴 레일 원값을 날짜별 zip 으로 묶어 구글 드라이브로 옮기고 PC 에서 지운다.

  python cell-bench/tools/archive_raw.py                    # 마지막으로 쓴 지 7일 지난 것을 모두
  python cell-bench/tools/archive_raw.py --until-free 50    # 남은 공간이 50 GB 가 될 때까지 오래된 날짜부터
  python cell-bench/tools/archive_raw.py --restore 20261109 # 그 날짜 zip 을 data/ 에 다시 푼다(다시 분석할 때)
  --dry-run 은 무엇을 묶을지만 보여 준다.

왜: 2026-11-10 제어 PC2 의 C 드라이브가 8.5 GB 까지 찼다. 셀 기록(ftg_*) 은 zip 으로 41 %, 라이브 원값(live_raw/*.bin) 은
7 % 로 준다(무손실). 시험은 프로토콜이 서면 2주에 한 번 — 원값은 구글 드라이브에 두고 PC 에는 최근 것만 둔다.
- 대상: data/ftg_*/ 폴더와 data/live_raw/*.bin 중 마지막으로 쓴 지 keep_days 가 지난 것. 판정 결과(protocol_*.json ·
  collect_*/) 와 장면(scenes/) 은 그대로 둔다 — 결과판은 판정 결과만 읽는다.
- 묶음: 이름 속 날짜(YYYYMMDD) 하나에 rail_raw_<날짜>.zip 하나. 같은 이름이 이미 있으면 _2, _3 … 을 붙인다.
- 순서: data/archive/ 에 먼저 쓰고 → 다시 열어 파일마다 CRC · 크기 · 개수가 원본과 맞을 때만 원본을 지우고 →
  구글 드라이브가 이 PC 에 붙어 있으면 그리로 옮긴다. 안 붙어 있으면 data/archive/ 에 두었다가 다음 실행 때 옮긴다.
- zip 안 경로는 data/ 기준이라 --restore 로 풀면 원래 자리 그대로다."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOCAL = DATA / "archive"
# 구글 드라이브(데스크톱 앱)가 붙인 공유 드라이브 DOB_Hardware 안 폴더. 앱 언어에 따라 이름이 다르다.
# RAIL_ARCHIVE_DIR 환경 변수가 있으면 그것을 쓴다.
DRIVE_ROOTS = [Path(r"G:\공유 드라이브\DOB_Hardware"), Path(r"G:\Shared drives\DOB_Hardware")]
DRIVE_SUB = "레일 시험 원값"
KEEP_DAYS = 7
DAY = re.compile(r"(20\d{6})")
GB = 1024 ** 3


def drive_dir() -> Path | None:
    """구글 드라이브 보관 폴더 — 이 PC 에 드라이브가 안 붙어 있으면 None."""
    env = os.environ.get("RAIL_ARCHIVE_DIR")
    if env:
        p = Path(env)
        return p if p.parent.exists() else None
    for r in DRIVE_ROOTS:
        if r.exists():
            return r / DRIVE_SUB
    return None


def free_gb(path: Path = DATA) -> float:
    return shutil.disk_usage(path).free / GB


def _files(p: Path) -> list[Path]:
    return [p] if p.is_file() else sorted(f for f in p.rglob("*") if f.is_file())


def _last_write(p: Path) -> float:
    return max((f.stat().st_mtime for f in _files(p)), default=p.stat().st_mtime)


def candidates(data: Path = DATA, keep_days: float = KEEP_DAYS, now: float | None = None) -> dict[str, list[Path]]:
    """{날짜: [묶을 폴더 · 파일]} — 오래된 날짜부터. 마지막으로 쓴 지 keep_days 안 된 것은 뺀다(쓰는 중인 기록 보호)."""
    cut = (now or time.time()) - keep_days * 86400
    items = [p for p in data.glob("ftg_*") if p.is_dir()] + sorted((data / "live_raw").glob("*.bin"))
    out: dict[str, list[Path]] = {}
    for p in items:
        m = DAY.search(p.name)
        if m and _last_write(p) < cut:
            out.setdefault(m.group(1), []).append(p)
    return dict(sorted(out.items()))


def _free_name(day: str, *dirs: Path | None) -> str:
    k = 1
    while True:
        name = f"rail_raw_{day}.zip" if k == 1 else f"rail_raw_{day}_{k}.zip"
        if not any(d and (d / name).exists() for d in dirs):
            return name
        k += 1


def verify(zpath: Path, files: dict[str, Path]) -> str | None:
    """zip 이 원본과 같은가 — 다르면 이유, 같으면 None."""
    with zipfile.ZipFile(zpath) as z:
        bad = z.testzip()
        if bad:
            return f"CRC 안 맞음: {bad}"
        got = {i.filename: i.file_size for i in z.infolist()}
    want = {a: f.stat().st_size for a, f in files.items()}
    if got != want:
        return f"파일 목록 · 크기가 다름(zip {len(got)}개, 원본 {len(want)}개)"
    return None


def pack(day: str, paths: list[Path], data: Path = DATA, local: Path = LOCAL, say=print) -> Path | None:
    """한 날짜를 zip 으로 묶고 확인한 뒤 원본을 지운다. 실패하면 원본은 그대로 두고 None."""
    local.mkdir(parents=True, exist_ok=True)
    files = {f.relative_to(data).as_posix(): f for p in paths for f in _files(p)}
    raw = sum(f.stat().st_size for f in files.values())
    zpath = local / _free_name(day, local, drive_dir())
    part = zpath.with_suffix(".zip.part")
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for arc, f in files.items():
                z.write(f, arc)
        why = verify(part, files)
        if why:
            raise OSError(why)
        part.replace(zpath)
    except OSError as e:
        part.unlink(missing_ok=True)
        say(f"{day} 묶기 실패 — 원본은 그대로 둔다: {e}")
        return None
    for p in paths:
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    say(f"{day} 묶음 {len(files)}개 파일 {raw / GB:.2f} GB → {zpath.name} {zpath.stat().st_size / GB:.2f} GB, 원본 지움")
    return zpath


def ship(local: Path = LOCAL, say=print) -> int:
    """data/archive/ 의 zip 을 구글 드라이브로 옮긴다. 옮긴 개수(드라이브가 없으면 0)."""
    dest = drive_dir()
    if dest is None:
        if any(local.glob("*.zip")):
            say(f"구글 드라이브가 이 PC 에 안 붙어 있다 — zip 은 {local} 에 둔다. 드라이브가 붙으면 다음 실행 때 옮긴다")
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for z in sorted(local.glob("*.zip")):
        target = dest / z.name
        if target.exists() and target.stat().st_size != z.stat().st_size:
            target = dest / _free_name(z.stem.removeprefix("rail_raw_"), dest)
        shutil.copy2(z, target)
        if target.stat().st_size != z.stat().st_size:
            say(f"{z.name} 드라이브 복사 크기가 다르다 — PC 쪽 zip 을 남긴다")
            continue
        z.unlink()
        n += 1
    if n:
        say(f"zip {n}개를 구글 드라이브 {dest} 로 옮겼다")
    return n


def run(keep_days: float = KEEP_DAYS, until_free: float | None = None, dry: bool = False,
        data: Path = DATA, local: Path = LOCAL, say=print) -> int:
    """오래된 날짜부터 묶는다. until_free 가 있으면 남은 공간이 그만큼 되면 멈춘다. 묶은 날짜 수."""
    done = 0
    for day, paths in candidates(data, keep_days).items():
        if until_free is not None and free_gb(data) >= until_free:
            break
        if dry:
            raw = sum(f.stat().st_size for p in paths for f in _files(p))
            say(f"{day}: {len(paths)}개 {raw / GB:.2f} GB — " + ", ".join(p.name for p in paths))
            continue
        if pack(day, paths, data, local, say):
            done += 1
        ship(local, say)                     # 날짜마다 바로 옮겨 PC 에 zip 이 쌓이지 않게 한다
    if not dry:
        ship(local, say)
    return done


def ensure_space(min_gb: float = 20, target_gb: float = 50, say=print) -> bool:
    """수집 실행기가 시작 전에 부른다. 남은 공간이 target_gb 아래면 오래된 원값부터 옮기고, 그래도 min_gb 아래면 False."""
    ship(say=say)
    if free_gb() < target_gb:
        say(f"남은 공간 {free_gb():.1f} GB — {target_gb:.0f} GB 가 될 때까지 오래된 원값을 묶어 옮긴다")
        run(until_free=target_gb, say=say)
    left = free_gb()
    if left < min_gb:
        say(f"남은 공간 {left:.1f} GB — {min_gb:.0f} GB 아래라 수집을 시작하지 않는다. "
            f"7일 안 된 기록만 남았다. 다른 큰 파일을 치우거나 --keep-days 를 줄여 archive_raw.py 를 돌린다")
        return False
    return True


def restore(day: str, data: Path = DATA, local: Path = LOCAL, say=print) -> int:
    """그 날짜의 zip(PC · 드라이브)을 data/ 에 푼다. 이미 있는 파일은 덮어쓰지 않는다. 푼 zip 수."""
    zips = sorted(local.glob(f"rail_raw_{day}*.zip"))
    if (d := drive_dir()) and d.exists():
        zips += sorted(d.glob(f"rail_raw_{day}*.zip"))
    for z in zips:
        with zipfile.ZipFile(z) as f:
            names = [n for n in f.namelist() if not (data / n).exists()]
            f.extractall(data, names)
        say(f"{z.name} → data/ 에 {len(names)}개 풀었다")
    if not zips:
        say(f"{day} zip 이 없다 — {local} 와 구글 드라이브를 본다")
    return len(zips)


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="다 쓴 레일 원값을 zip 으로 묶어 구글 드라이브로 옮긴다")
    ap.add_argument("--keep-days", type=float, default=KEEP_DAYS, help="마지막으로 쓴 지 이 날수가 안 된 기록은 두기(기본 7)")
    ap.add_argument("--until-free", type=float, help="남은 공간이 이 GB 가 되면 멈추기")
    ap.add_argument("--restore", metavar="YYYYMMDD", help="그 날짜 zip 을 data/ 에 다시 풀기")
    ap.add_argument("--dry-run", action="store_true", help="무엇을 묶을지만 보기")
    a = ap.parse_args(argv)
    if a.restore:
        return 0 if restore(a.restore) else 1
    print(f"남은 공간 {free_gb():.1f} GB · 보관 {drive_dir() or LOCAL}")
    run(a.keep_days, a.until_free, a.dry_run)
    print(f"남은 공간 {free_gb():.1f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
