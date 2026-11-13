"""LabSat SD 카드에 장면 폴더를 넣는다 — PC 에 꽂힌 이동식 드라이브를 찾아 <이름>/<이름>_0000.LS3 를 복사한다.

  python cell-bench/tools/sd_scenes.py S04_RailB100 S05_RailB500
카드의 장면 구조는 2026-10-30 S03_RailB120 과 같다(폴더 이름 = 장면 이름, 안에 <이름>_0000.LS3). 복사 뒤 크기를 대조한다.
"""
from __future__ import annotations

import shutil
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENES = ROOT / "data" / "scenes"


def removable() -> list[Path]:
    import ctypes
    out = []
    for d in string.ascii_uppercase[3:]:
        p = Path(f"{d}:\\")
        if p.exists() and ctypes.windll.kernel32.GetDriveTypeW(f"{d}:\\") == 2:     # DRIVE_REMOVABLE
            out.append(p)
    return out


def main(argv=None) -> int:
    names = (argv or sys.argv[1:])
    if not names:
        print(__doc__); return 2
    drives = removable()
    if len(drives) != 1:
        print(f"이동식 드라이브가 하나가 아니다: {drives} — SD 카드를 PC 에 꽂았는지 본다"); return 2
    sd = drives[0]
    print(f"SD 카드 {sd} · 지금 장면 폴더: {sorted(p.name for p in sd.iterdir() if p.is_dir())}")
    for n in names:
        srcs = list((SCENES / n / "ls3").rglob("*.LS3")) + list((SCENES / n / "ls3").rglob("*.ls3"))
        if not srcs:
            print(f"{n}: 장면 파일(.LS3)이 없다"); return 1
        src = srcs[0]
        dst = sd / n / f"{n}_0000.LS3"
        dst.parent.mkdir(exist_ok=True)
        shutil.copyfile(src, dst)
        ok = dst.stat().st_size == src.stat().st_size
        print(f"{n}: {src.stat().st_size / 1048576:.0f} MB → {dst} · {'같음' if ok else '크기 다름!'}")
        if not ok:
            return 1
    print("끝 — SD 카드를 안전하게 제거한 뒤 LabSat 에 꽂고 정지 장면을 다시 튼다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
