"""무인 충방전 사이클 실행.

사용:
  python run_cycle.py                      # 1사이클
  python run_cycle.py --cycles 3
  python run_cycle.py --dry-run            # 플러그·셀을 건드리지 않고 흐름만 (라이브 수신은 함)
  python run_cycle.py --config my.json     # Config 필드를 JSON 으로 덮어씀

멈추려면 Ctrl+C. 플러그는 마지막 상태 그대로 남으니 멈춘 뒤 tools/plug_cli.py 로 확인한다.
"""
from __future__ import annotations

import argparse
import sys
import time

from cellbench.alert import make_notifier
from cellbench.cells import CellLink, LiveListener
from cellbench.config import Config
from cellbench.cycle import CycleRunner
from cellbench.plug import Plug
from cellbench.record import Recorder


def main() -> int:
    ap = argparse.ArgumentParser(description="셀 내구 시험대 무인 충방전")
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    rec = Recorder(cfg.data_dir, alert=make_notifier())
    rec.log("설정: " + cfg.dump().replace("\n", " "))

    live = LiveListener(cfg); live.start()
    time.sleep(3)
    heard = live.alive()
    rec.log(f"라이브 신호 {len(heard)}/{len(cfg.serials)}대")
    if not heard and not args.dry_run:
        rec.log("셀이 하나도 안 들린다 — Wi-Fi 가 LiveHub(FTG-3D93-5G)에 붙어 있는지, 셀이 켜져 있는지 확인"); return 2

    plug = None
    if not args.dry_run:
        plug = Plug(cfg)
        r = plug.read()
        rec.log(f"플러그 {plug.ip} · {'켜짐' if r.on else '꺼짐'} · {r.watts:.1f} W")
    link = CellLink(cfg, live, rec.log)
    runner = CycleRunner(cfg, live, plug, link, rec, dry_run=args.dry_run)
    try:
        runner.run(args.cycles)
    except KeyboardInterrupt:
        rec.log("사용자 중단 — 플러그 상태는 그대로다")
        return 130
    finally:
        live.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
