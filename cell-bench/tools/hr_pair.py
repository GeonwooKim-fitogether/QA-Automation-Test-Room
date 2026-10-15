"""심박 짝 표 — 보기 · bench.json 에 넣기 · 보드 읽기 (cellbench/hr.py).

  python -X utf8 tools/hr_pair.py                         # 짝 표 · 지금 셀 심박 · 보드 상태를 보여 준다 (아무것도 보내지 않음)
  python -X utf8 tools/hr_pair.py --boards                # 짝 표의 보드를 지금 USB 로 한 번 읽어 보여 준다 (읽기만)
  python -X utf8 tools/hr_pair.py --import-csv 짝.csv     # 짝 CSV(no,hub,port,name[,serial]) → 세트 1 의 hr_pairs 로 바꿔 보여 준다
  python -X utf8 tools/hr_pair.py --import-csv 짝.csv --write   # 위 내용을 bench.json 에 넣는다 (검사 → 임시 파일 → 바꿔치기)

짝 CSV 에 serial 칸이 없으면 보드 번호 N ↔ 세트의 첫 셀 + N − 1 (세트 1: 보드 1 ↔ 셀 11733). --set 으로 세트를 고른다.
bench.json 은 운전 중에 등록 화면이 고치는 파일이라 손으로 고치지 말고 --write 로 넣는다(config.set_hr_pairs — 다른 키는 그대로 둔다).
엔진 · 감시자 · 결과판은 bench.json 을 다시 읽어야 짝 표를 안다 — 감시자와 결과판은 다음 점검·요청에서, 엔진은 다음에 시작할 때.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cellbench import hr  # noqa: E402
from cellbench.config import BENCH_FILE, Config, expand_serials, set_entry, set_hr_pairs  # noqa: E402
from cellbench.control import read_json  # noqa: E402


def say(*a) -> None:
    print(*a, flush=True)


def pairs_from_csv(path: Path, first_serial: int) -> dict:
    """짝 CSV → hr_pairs. 칸: no · hub · port · name (· serial — 없으면 first_serial + no − 1)."""
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            no = int(r["no"])
            serial = int(r["serial"]) if (r.get("serial") or "").strip() else first_serial + no - 1
            port = r.get("port", "").strip()
            out[str(serial)] = {"board": r["name"].strip().upper(), "no": no, "hub": r.get("hub", "").strip(),
                                "port": int(port) if port.isdigit() else port}
    return out


def show(cfg: Config, data: Path, sid: int) -> None:
    pairs = hr.pairs_of(set_entry(cfg, sid))
    if not pairs:
        say(f"세트 {sid} 에는 심박 짝 표(hr_pairs)가 없다 — --import-csv 로 넣는다")
        return
    now = read_json(data / ("now.json" if sid == 1 else f"set{sid}/now.json")) or {}
    cells = {c.get("serial"): c for c in now.get("cells") or [] if isinstance(c, dict)}
    boards = read_json(data / hr.BOARDS_FILE)
    th = hr.thresholds(cfg)
    say(f"\n세트 {sid} · 단계 {now.get('phase', '-')} · 보드 기록 {time.strftime('%H:%M:%S', time.localtime(boards['t'])) if boards else '없음'}\n")
    say(" 번호  허브   보드          셀      심박  보드 상태")
    for s, p in sorted(pairs.items(), key=lambda x: (x[1].get("no") or 0, x[0])):
        st, b = hr.board_state(p["board"], boards, time.time(), th["hr_board_stale_s"])
        c = cells.get(s, {})
        conns = f" · 연결 {b.get('conns')}" if b and st in ("free", "joined") else ""
        say(f" {str(p.get('no') or '-'):>3}  {p['hub'] or '-'}-{str(p.get('port') or '-'):<3} {p['board']:<12}  {s}  "
            f"{str(c.get('hr', '-')):>4}  {st}{conns}")


def main() -> int:
    ap = argparse.ArgumentParser(description="심박 짝 표 — 보기 · bench.json 에 넣기 · 보드 읽기")
    ap.add_argument("--set", type=int, default=1)
    ap.add_argument("--bench", default=str(BENCH_FILE))
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--import-csv", default=None, help="짝 CSV (no,hub,port,name[,serial])")
    ap.add_argument("--first-serial", type=int, default=None, help="serial 칸이 없을 때 보드 1 의 셀 (기본: 세트의 첫 셀)")
    ap.add_argument("--write", action="store_true", help="--import-csv 의 결과를 bench.json 에 넣는다")
    ap.add_argument("--boards", action="store_true", help="짝 표의 보드를 지금 USB 로 읽는다 (읽기만)")
    a = ap.parse_args()
    cfg = Config.load(None, bench_path=a.bench)
    if a.import_csv:
        entry = set_entry(cfg, a.set)
        if entry is None:
            sys.exit(f"bench.json 에 세트 {a.set} 가 없다")
        first = a.first_serial or min(expand_serials(entry.get("serials", [])))
        pairs = pairs_from_csv(Path(a.import_csv), first)
        say(json.dumps({"hr_pairs": pairs}, ensure_ascii=False, indent=1))
        if not a.write:
            say(f"\n(보기만 함 — 세트 {a.set} 에 넣으려면 --write)")
            return 0
        set_hr_pairs(a.set, pairs, a.bench)
        say(f"\n{a.bench} 세트 {a.set} 에 짝 {len(pairs)}개를 넣었다")
        return 0
    if a.boards:
        names = hr.watched_names(cfg.sets)
        if not names:
            sys.exit("짝 표가 없다 — 읽을 보드가 없다")
        doc = hr.scan(names, None, time.time(), hr.thresholds(cfg)["hr_board_read_s"])
        say(json.dumps(doc, ensure_ascii=False, indent=1))
        return 0
    show(cfg, Path(a.data), a.set)
    return 0


if __name__ == "__main__":
    sys.exit(main())
