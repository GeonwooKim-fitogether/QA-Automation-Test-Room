"""레일 독 셀 — 배터리 · 저장 크기 보기와 '받으면서 비우기'.

  python cell-bench/tools/rail_cells.py status            # 셀마다 배터리 % · 저장 MB (지우지 않는다, 셀당 약 20초 측정이 멈춘다)
  python cell-bench/tools/rail_cells.py extract           # 받으면서 비우기 → cell-bench/data/ftg_y_<시각>/ (받은 파일이 온전할 때만 지운다)

라이브 기록(live_tap)이 같은 UDP 포트를 쥐고 있으면 둘 다 시작하지 않는다 — 시험이 끝난 뒤에 쓴다.
셀 저장은 약 160 MB 에서 가득 차 측정이 멈춘다(2026-10-10 실측). 120 MB 를 넘은 셀은 시험 전에 비운다.
10677 은 펌웨어 0.99.68 이라 지우기에 응답하지 않는다(2026-10-30) — 받기만 되고 지워지지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cellbench.cells import CellLink, LiveListener, PortBusy  # noqa: E402
from cellbench.config import Config  # noqa: E402

DATA = ROOT / "data"
LAYOUT = ROOT / "motionbench" / "dock_layout.json"
FULL_MB, WARN_MB = 160.0, 120.0


def serials(set_name: str) -> list[int]:
    return sorted(s for col in json.loads(LAYOUT.read_text(encoding="utf-8"))[set_name]["columns"] for s in col)


def extract_all(set_name: str, say=print) -> Path:
    """받으면서 비우기 — 받은 폴더를 돌려준다. 라이브 포트가 막혀 있으면 PortBusy 를 올린다(시험이 끝난 뒤에 부른다)."""
    want = serials(set_name)
    cfg = replace(Config(), serials=want)
    lv = LiveListener(cfg); lv.start(); time.sleep(6)
    alive = [s for s in want if s in lv.alive()]
    say(f"받기 시작 — 셀 {len(alive)}/{len(want)}대 보임")
    out = DATA / f"ftg_{set_name.lower()}_{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        res = CellLink(cfg, lv, log=say).extract(alive, out)
    finally:
        lv.stop()
    (out / "extract_result.json").write_text(json.dumps(
        [{"serial": s, "battery": r.battery, "size": r.size, "got": r.got, "deleted": getattr(r, "deleted", None), "error": r.error}
         for s, r in sorted(res.items())], ensure_ascii=False, indent=1), encoding="utf-8")
    bad = [s for s, r in res.items() if r.error or not getattr(r, "deleted", False)]
    say(f"받기 끝 → {out} · 지우지 못한 셀 {sorted(bad)}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", choices=["status", "extract"])
    ap.add_argument("--set", default="Y")
    a = ap.parse_args(argv)
    want = serials(a.set)
    cfg = replace(Config(), serials=want)
    try:
        lv = LiveListener(cfg)
    except PortBusy as e:
        print(f"라이브 포트를 다른 프로그램이 쓰고 있다 — 시험(live_tap)이 끝난 뒤에 다시 한다 ({e})")
        return 2
    lv.start(); time.sleep(6)
    alive = [s for s in want if s in lv.alive()]
    gone = sorted(set(want) - set(alive))
    print(f"{time.strftime('%H:%M:%S')} 셀 {len(alive)}/{len(want)}대 보임" + (f" · 안 보임 {gone} — 꺼졌거나 망에서 빠졌다" if gone else ""))
    link = CellLink(cfg, lv, log=lambda m: None)
    try:
        if a.what == "status":
            res = link.status(alive)
        else:
            out = DATA / f"ftg_{a.set.lower()}_{time.strftime('%Y%m%d-%H%M%S')}"
            res = link.extract(alive, out)
            (out / "extract_result.json").write_text(json.dumps(
                [{"serial": s, "battery": r.battery, "size": r.size, "got": r.got, "deleted": getattr(r, "deleted", None),
                  "error": r.error} for s, r in sorted(res.items())], ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"받은 곳 → {out}")
    finally:
        lv.stop()
    bad = 0
    for s, r in sorted(res.items()):
        mb = None if r.size is None else r.size / 1048576
        note = "" if mb is None else " · 가득 참(측정 멈춤)" if mb >= FULL_MB * 0.95 else " · 비워야 함" if mb >= WARN_MB else ""
        extra = "" if a.what == "status" else f" · 받음 {r.got / 1048576:.1f} MB · {'지움' if getattr(r, 'deleted', False) else '안 지움'}"
        print(f"  {s} 배터리 {r.battery if r.battery is not None else '?'}% · 저장 {'?' if mb is None else f'{mb:.1f}'} MB{note}{extra}"
              + (f" · 오류 {r.error}" if r.error else ""))
        bad += bool(r.error or note)
    return 1 if bad or gone else 0


if __name__ == "__main__":
    raise SystemExit(main())
