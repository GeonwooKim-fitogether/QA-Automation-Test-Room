"""레일 수집 하루 요약을 만들어 GitHub 에 1주일에 한 번 올린다 — 고정 봇 브랜치 chore/rail-records 와 PR 하나.

왜 (2026-11-06): 판정 결과는 클라우드 결과판에, 원본은 구글 드라이브에 자동으로 가지만, '그날 무엇을 어떤 조건으로 돌렸고
  무엇이 걸렸나'는 사람이 수집 일지에 손으로 적을 때만 저장소에 남았다. 사용자가 이것도 주기적으로 자동으로 되기를 원했고,
  PR 이 올라갈 때마다 자동 검사 5개(각 1분 과금)가 돌아 매일 대신 1주일에 한 번으로 정했다(약 월 20분).

무엇을 하나
  - data/collect_<날짜>/ 마다 dynamic-bench/records/rail-days/<YYYY-MM-DD>.md 를 만든다.
    바퀴 · 시험별 회차 수, 장비 무효, 판정한 셀 수, 불합격 항목 묶음, 실행 기록(loop.log)의 사건 줄.
  - 별도 작업 트리(C:\\Dev\\QA-rail-records)에서 origin/main 위에 chore/rail-records 를 다시 만들고 요약을 모두 다시 쓴다.
    바뀐 것이 없으면 아무것도 하지 않는다. 바뀌었으면 커밋해 그 브랜치에 올리고(봇 브랜치라 덮어쓴다), 열린 PR 이 없으면 연다.
  - main 에 합치는 것은 사람이다.
사용: python -X utf8 tools/rail_records.py [--dry]   (--dry: 요약만 만들어 화면에 경로를 찍고 git 은 건드리지 않는다)
등록: tools/install_records_task.ps1 (매주 금요일 17시)
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # <작업 트리>/cell-bench
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))
from motionbench import qc_ids  # noqa: E402
TREE = Path(r"C:\Dev\QA-rail-records")                  # 봇 브랜치 전용 작업 트리 — 시험을 돌리는 작업 트리를 건드리지 않는다
BRANCH = "chore/rail-records"
REL = Path("dynamic-bench/records/rail-days")
LOG = DATA / "rail_records.log"
EVENT = re.compile(r"(바퀴 시작|장면|위치 잡은|못 잡은|재부팅|충전|멈춤|건너뜀|예외|Traceback|끝 · 코드 [^0]|판정 끝|수집 (시작|끝)|무효)")


def say(line: str) -> None:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")


def test_name(c: dict) -> str:
    """판정 파일 conditions → 표의 시험 칸. 2026-11-12 전 파일은 conditions.part 글자로 읽는다(qc_ids.from_record)."""
    test = qc_ids.from_record(c) or "?"
    sp = c.get("speeds_mm_s") or []
    return f"{test} {sp[0]} mm/s" if test == "GNSS-MOVE" and sp else test


def summarize(day: Path) -> str:
    date = re.sub(r"collect_(\d{4})(\d{2})(\d{2}).*", r"\1-\2-\3", day.name)
    rows, sets = [], set()
    for rd in sorted(p for p in day.glob("r[0-9]*") if p.is_dir()):
        per: dict[str, dict] = collections.OrderedDict()
        for f in sorted(rd.glob("judged/protocol_*.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            c = d.get("conditions", {})
            sets.add(c.get("set") or "?")
            t = per.setdefault(test_name(c), {"layout": c.get("dock_layout"), "runs": 0, "bad": 0, "cells": set(),
                                               "fail_runs": 0, "items": collections.Counter()})
            t["runs"] += 1
            if not (d.get("rig") or {}).get("유효", True):
                t["bad"] += 1
                continue
            failed = False
            for s, cell in (d.get("cells") or {}).items():
                t["cells"].add(s)
                for k, it in (cell.get("items") or {}).items():
                    if it.get("verdict") == "fail":
                        t["items"][k] += 1; failed = True
            t["fail_runs"] += failed
        for name, t in per.items():
            items = " · ".join(f"{k} {n}" for k, n in t["items"].most_common()) or "없음"
            rows.append(f"| {rd.name} | {t['layout']} | {name} | {t['runs']} | {t['bad']} | {len(t['cells'])} | {items} |")
    events = []
    lp = day / "loop.log"
    if lp.exists():
        for line in lp.read_text(encoding="utf-8", errors="replace").splitlines():
            if "protocol_" not in line and EVENT.search(line):
                events.append(f"- {line.strip()}")
    out = [f"# 레일 수집 {date} — 세트 {', '.join(sorted(sets)) or '?'}", "",
           f"> 자동 요약(`cell-bench/tools/rail_records.py`). 원본은 제어 PC `cell-bench/data/{day.name}/` 와 구글 드라이브 백업에 있다. "
           "판정은 기준 없이 낸 것이며, 불합격 항목 수는 '셀 × 회차' 단위다. 해석은 `../qc-collection-log.md` 에 사람이 적는다.", "",
           "| 바퀴 | 배치 | 시험 | 회차 | 장비 무효 | 판정한 셀 | 불합격 항목 (셀 × 회차) |", "|---|---|---|---|---|---|---|", *rows,
           "", "## 실행 기록에서 뽑은 사건", "", *(events[:200] or ["- (loop.log 없음)"])]
    if len(events) > 200:
        out.append(f"- … {len(events) - 200}줄 더 (loop.log)")
    return "\n".join(out) + "\n"


def git(*args, check=True) -> str:
    r = subprocess.run(["git", "-C", str(TREE), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    days = sorted(p for p in DATA.glob("collect_2*") if p.is_dir() and re.fullmatch(r"collect_\d{8}", p.name))
    texts = {re.sub(r"collect_(\d{4})(\d{2})(\d{2})", r"\1-\2-\3", d.name) + ".md": summarize(d) for d in days}
    if a.dry:
        tmp = DATA / "rail_records_dry"
        tmp.mkdir(exist_ok=True)
        for name, text in texts.items():
            (tmp / name).write_text(text, encoding="utf-8")
        print(f"요약 {len(texts)}개 → {tmp}")
        return 0
    try:
        if not (TREE / ".git").exists():
            subprocess.run(["git", "-C", str(ROOT.parent), "worktree", "add", "--detach", str(TREE), "origin/main"],
                           check=True, capture_output=True)
        git("fetch", "origin", "-q")
        git("checkout", "-q", "-B", BRANCH, "origin/main")
        dest = TREE / REL
        dest.mkdir(parents=True, exist_ok=True)
        for name, text in texts.items():
            (dest / name).write_text(text, encoding="utf-8")
        git("add", str(REL))
        if not git("status", "--porcelain", str(REL)):
            say(f"바뀐 것 없음 — 요약 {len(texts)}개가 main 과 같다")
            return 0
        git("commit", "-q", "-m", f"chore: 레일 수집 하루 요약을 갱신한다 ({time.strftime('%Y-%m-%d')} 자동)\n\n"
                                   "cell-bench/tools/rail_records.py 가 매주 만든다.")
        remote = git("ls-remote", "origin", BRANCH, check=False)
        if remote and git("rev-parse", "HEAD^{tree}") == git("rev-parse", f"{remote.split()[0]}^{{tree}}", check=False):
            say("올린 것과 같다 — 건너뜀")
            return 0
        git("push", "-q", "--force", "origin", f"HEAD:refs/heads/{BRANCH}")
        gh = ["gh", "pr", "list", "--head", BRANCH, "--state", "open", "--json", "number", "-q", ".[].number"]
        opened = subprocess.run(gh, cwd=TREE, capture_output=True, text=True).stdout.strip()
        if not opened:
            body = ("레일 수집 하루 요약(`dynamic-bench/records/rail-days/`)을 자동으로 갱신한다. "
                    "매주 금요일 `cell-bench/tools/rail_records.py` 가 다시 만들고, 합치기는 사람이 한다.\n\n"
                    "## 보는 법\n- 어디서: 저장소 `dynamic-bench/records/rail-days/`\n- 무엇을 하면: 날짜 파일을 연다\n"
                    "- 무엇이 보이나: 그날 바퀴 · 시험별 회차 수, 장비 무효, 불합격 항목, 실행 기록의 사건\n\n"
                    "🤖 Generated with [Claude Code](https://claude.com/claude-code)")
            subprocess.run(["gh", "pr", "create", "--base", "main", "--head", BRANCH,
                            "--title", "chore: 레일 수집 하루 요약을 갱신한다", "--body", body], cwd=TREE, check=True,
                           capture_output=True)
            opened = "새 PR"
        say(f"올림 — 요약 {len(texts)}개 · PR {opened}")
    except Exception as e:                       # noqa: BLE001 — 예약 작업이라 기록만 남기고 끝낸다
        say(f"실패 — {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
