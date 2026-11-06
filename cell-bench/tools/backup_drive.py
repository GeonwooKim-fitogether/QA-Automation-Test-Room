"""수집 원본을 구글 드라이브 폴더로 복사한다 — 더하기만 하고 지우지 않는다.

왜 (2026-11-06): 셀 기록(ftg_*) · 바퀴 폴더(collect_*) · 실행 기록은 git 에 넣지 않는 크기라(레일 X 세트만 약 5 GB)
  제어 PC 한 곳에만 있었다. 판정 파일은 결과판 서버가 클라우드에 올리지만, 다시 판정하려면 원본이 필요하다.
  이 도구를 작업 스케줄러가 1시간마다 부르고(install_backup_task.ps1), 구글 드라이브 데스크톱 앱이 그 폴더를 올린다.

무엇을 하나
  - <작업 트리>/cell-bench/data 전체를 <대상>/<PC 이름>/cell-bench-data 로 robocopy /E — 바뀐 파일만 복사하고 지우지 않는다.
  - 대상은 --dest 또는 data/backup_dest.txt 의 한 줄(드라이브 폴더). 둘 다 없으면 아무것도 하지 않는다.
  - 모터가 돌거나(motion.lock), 시험이 돌거나(qc_now.json), 레일 실행기(qc_loop · run_rail · run_motion)가 살아 있으면 건너뛴다.
    큰 복사는 모터 주기 지연 멈춤을 부른다(2026-10-29). 시험 사이 빈틈에 시작했다가 다음 모터와 겹치는 것도 막는다.
  - 결과 한 줄을 data/backup.log 에 남긴다.
사용: python -X utf8 tools/backup_drive.py [--dest "G:\\내 드라이브\\rail-backup"] [--dry]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data"
DEST_FILE = DATA / "backup_dest.txt"
LOG = DATA / "backup.log"
RUNNERS = ("qc_loop.py", "run_rail.py", "run_motion")
EXCLUDE_FILES = ("*.lock", "backup.log")
MIN_FREE_GB = 15


def log(line: str) -> None:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")


def runners_alive() -> list[str]:
    """레일 실행기 프로세스의 명령줄 — 못 알아내면 ['?'] (모르면 건너뛰는 쪽으로)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
          "ForEach-Object { $_.CommandLine }")
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, errors="replace", timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ["?"]
    return [c.strip() for c in r.stdout.splitlines() if any(k in c for k in RUNNERS)]


def busy() -> str | None:
    """복사하면 안 되는 이유, 괜찮으면 None."""
    from cellbench import proc
    from motionbench import charge
    lock = proc.acquire_lock(charge.LOCK)
    if lock is None:
        return "모터 실행 중(motion.lock)"
    lock.close()
    if charge.test_running():
        return "시험 실행 중(qc_now.json)"
    alive = runners_alive()
    if alive:
        return f"레일 실행기 살아 있음 ({len(alive)}개)"
    import shutil
    free_gb = shutil.disk_usage(DATA).free / 1e9
    if free_gb < MIN_FREE_GB:          # 드라이브 앱(스트리밍)은 올리기 전까지 복사본을 C: 캐시에 둔다 — 디스크가 차면 시험이 멈춘다
        return f"C: 남은 공간 {free_gb:.0f} GB < {MIN_FREE_GB} GB"
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", default="")
    ap.add_argument("--dry", action="store_true", help="복사하지 않고 무엇을 할지만 본다(robocopy /L)")
    a = ap.parse_args(argv)
    dest = a.dest or (DEST_FILE.read_text(encoding="utf-8").strip() if DEST_FILE.exists() else "")
    if not dest:
        log("건너뜀 — 대상 폴더가 없다 (backup_dest.txt 에 드라이브 폴더 한 줄)")
        return 0
    if not Path(dest).exists():
        log(f"건너뜀 — 대상 폴더가 없다: {dest} (구글 드라이브 앱이 켜져 있나)")
        return 0
    why = busy()
    if why:
        log(f"건너뜀 — {why}")
        return 0
    target = Path(dest) / os.environ.get("COMPUTERNAME", "pc") / "cell-bench-data"
    cmd = ["robocopy", str(DATA), str(target), "/E", "/R:1", "/W:5", "/NP", "/NFL", "/NDL", "/NJH",
           "/XF", *EXCLUDE_FILES] + (["/L"] if a.dry else [])
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="oem", errors="replace")   # robocopy 는 OEM 코드 페이지(한국어 cp949)로 쓴다
    summary = " | ".join(" ".join(l.split()) for l in r.stdout.splitlines() if l.strip().startswith(("Files", "Bytes", "파일", "바이트")))
    ok = r.returncode < 8                       # robocopy: 0~7 정상, 8 이상 실패
    log(f"{'모의 ' if a.dry else ''}{'복사' if ok else '실패'} — 코드 {r.returncode} · {time.time() - t0:.0f}초 · {target} · {summary}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
