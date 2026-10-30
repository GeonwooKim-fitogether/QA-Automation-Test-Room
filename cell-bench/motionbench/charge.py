"""레일 독 충전 기준 — 셀이 완전히 방전되는 것만 막는다 (2026-10-30 김건우 요청, 결과판 세션 경유).

기준
  1. 시작 전: 레일 독 셀 중 가장 낮은 셀(평균이 아니다)이 START_MIN_PCT 미만이면 시작하지 않는다 — 충전부터.
  2. 시험 중: 가장 낮은 셀이 STOP_PCT 이하가 되면 지금 속도 구간만 마치고 멈춘다.
  3. 쉬는 동안: 플러그를 켜 둔다. 독은 완충되면 충전을 멈추고 켜진 채로는 다시 시작하지 않으므로(셀 시험대 2026-10-07 실측),
     전력이 바닥이면 끊었다 켠다 — cellbench.guards.PowerWatch 를 그대로 쓴다 (python cell-bench/motionbench/charge.py keep).

잔량은 live_tap 이 BATT_FILE 에 2초마다 남긴다(같은 UDP 포트를 두 프로세스가 열 수 없어서 파일로 넘긴다).
레일 실행기(run_motion.py long)는 시작 전과 속도 구간 사이에만 이 파일을 읽는다.
플러그 호출은 모터 어댑터를 열기 전과 닫은 뒤에만 한다 — 모터가 도는 동안에는 부르지 않는다.
시험 중 플러그는 끈다: 독에 전원이 들어온 채 셀이 기록을 이어 가는지 확인한 적이 없어서다(확인되면 바꾼다).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

if __name__ == "__main__":   # 직접 실행할 때 cell-bench 를 경로에 — python cell-bench/motionbench/charge.py keep
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cellbench.guards import batt_ok

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BATT_FILE = DATA / "rail_batt.json"           # live_tap 이 쓰고 run_motion 이 읽는다
LAYOUT = Path(__file__).resolve().parent / "dock_layout.json"
LOCK = DATA / "motion.lock"                   # run_motion 의 잠금 — 쥘 수 있으면 모터가 돌지 않는다

START_MIN_PCT = 30       # 이 미만이면 시작하지 않는다
STOP_PCT = 20            # 이 이하면 지금 구간을 마치고 멈춘다
CELL_FRESH_S = 10.0      # 셀 값이 이보다 오래됐으면 버린다 (셀은 초당 2번 보낸다)
FILE_FRESH_S = 15.0      # 파일 자체가 이보다 오래됐으면 live_tap 이 멈춘 것으로 본다

RAIL_PLUG_MAC = "EC:B9:31:A0:66:CB"     # Tapo P110M "Dynamic Bench" — 2026-10-30 연결 · 끄기/켜기 시험 끝남
RAIL_PLUG_IP = "192.168.1.115"          # DHCP 라 바뀔 수 있다 — 먼저 시도하고 안 되면 MAC 으로 찾는다


def rail_serials(path: Path = LAYOUT) -> set[int]:
    """레일 독의 셀 번호 — dock_layout.json 의 모든 세트 칸. 같은 망의 셀 시험대 셀은 빼야 해서 목록으로 거른다."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return {int(s) for k, v in doc.items() if not k.startswith("_") for col in v["columns"] for s in col}


def snapshot(cells: dict, now: float) -> dict:
    """LiveListener.snapshot() 의 {셀: CellLive} → 파일에 쓸 모양."""
    return {"t": now, "cells": {str(s): {"battery": c.battery, "t": c.t} for s, c in cells.items()}}


def lowest(doc: dict | None, serials: set[int], now: float) -> tuple[int | None, int, str | None]:
    """잔량 파일 → (가장 낮은 셀 %, 읽힌 셀 수, 못 읽은 이유). 못 읽으면 % 는 None."""
    if not doc or not isinstance(doc.get("cells"), dict):
        return None, 0, f"잔량 파일이 없다 ({BATT_FILE.name}) — live_tap 을 먼저 켠다"
    if now - float(doc.get("t") or 0) > FILE_FRESH_S:
        return None, 0, f"잔량 파일이 {now - float(doc.get('t') or 0):.0f}초 묵었다 — live_tap 이 멈췄는지 본다"
    vals = [c.get("battery") for s, c in doc["cells"].items()
            if s.isdigit() and int(s) in serials and now - float(c.get("t") or 0) <= CELL_FRESH_S and batt_ok(c.get("battery"))]
    if not vals:
        return None, 0, "레일 독 셀이 하나도 들리지 않는다"
    return int(min(vals)), len(vals), None


def start_problem(low: int | None, why: str | None) -> str | None:
    """기준 1. 시작해도 되면 None."""
    if low is None:
        return f"셀 잔량을 읽지 못해 시작하지 않는다 — {why}"
    if low < START_MIN_PCT:
        return f"가장 낮은 셀 {low}% — {START_MIN_PCT}% 미만이라 시작하지 않는다. 충전부터 한다 (charge.py keep)"
    return None


def should_stop(low: int | None) -> bool:
    """기준 2. 못 읽으면 멈추지 않는다 — 한순간 안 들린 것으로 시험을 끊지 않는다(판정은 셀 수신 항목이 따로 본다)."""
    return low is not None and low <= STOP_PCT


def read_batt(path: Path = BATT_FILE) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def rail_low(now: float | None = None) -> tuple[int | None, int, str | None]:
    """지금 레일 독 셀의 가장 낮은 % — 파일 하나 읽기라 구간 사이에 불러도 가볍다."""
    return lowest(read_batt(), rail_serials(), time.time() if now is None else now)


# ---------- 플러그 (모터가 돌지 않을 때만 부른다) ----------

def rail_plug():
    from cellbench.config import Config
    from cellbench.plug import Plug
    return Plug(replace(Config(), plug_mac=RAIL_PLUG_MAC, plug_ip_hint=RAIL_PLUG_IP))


def plug_set(on: bool, say=print) -> dict:
    """켜거나 끈다. 실패해도 예외를 올리지 않고 결과에 남긴다 — 플러그 때문에 모터의 안전한 끝맺음이 막히면 안 된다."""
    t0 = time.time()
    try:
        p = rail_plug()
        if on:
            from cellbench.plug import ensure_on
            r = ensure_on(p)
        else:
            r = p.off()
        out = {"want": "on" if on else "off", "on": r.on, "w": round(r.watts, 1), "s": round(time.time() - t0, 1)}
    except Exception as e:
        out = {"want": "on" if on else "off", "error": f"{type(e).__name__}: {e}", "s": round(time.time() - t0, 1)}
    say(f"{time.strftime('%H:%M:%S')} [PLUG] 레일 독 플러그 {'켜기' if on else '끄기'} — "
        + (f"실패: {out['error']}" if "error" in out else f"{'켜짐' if out['on'] else '꺼짐'} · {out['w']} W"))
    return out


def keep(hours: float, tick_s: float = 60.0, say=print) -> int:
    """기준 3 — 쉬는 동안 완충 유지. 플러그를 켜 두고, 전력이 바닥에 붙으면 끊었다 켠다.
    모터 잠금(motion.lock)을 쥘 수 없으면 run_motion 이 도는 중이라 그 틱은 플러그에 손대지 않는다."""
    from cellbench import proc
    from cellbench.config import Config
    from cellbench.guards import PowerWatch
    cfg = Config()
    watch = PowerWatch(cfg.stuck_w, cfg.stuck_s, cfg.stuck_gap_s, cfg.stuck_max)
    plug, end = None, time.time() + hours * 3600
    say(f"{time.strftime('%H:%M:%S')} [KEEP] 레일 독 완충 유지 {hours:g}시간 — Ctrl+C 로 끝낸다")
    try:
        while time.time() < end:
            lock = proc.acquire_lock(LOCK)
            if lock is None:
                say(f"{time.strftime('%H:%M:%S')} [KEEP] 모터 실행 중 — 플러그에 손대지 않는다")
            else:
                try:
                    plug = plug or rail_plug()
                    r = plug.read()
                    act = watch.feed(time.time(), r.on, r.watts)
                    low, n, _ = rail_low()
                    say(f"{time.strftime('%H:%M:%S')} [KEEP] {'켜짐' if r.on else '꺼짐'} · {r.watts:.1f} W · "
                        f"가장 낮은 셀 {low if low is not None else '?'}% ({n}대)" + (f" → {act}" if act else ""))
                    if act == "recharge":
                        plug.recharge(cfg.stuck_gap_s)
                    elif act == "plug_on" or (r.on is False and act is None):
                        plug.on()
                    elif act == "give_up":
                        say(f"{time.strftime('%H:%M:%S')} [KEEP] 끊었다 켜기를 {cfg.stuck_max}번 해도 독이 전력을 끌어 쓰지 않는다 — 독을 직접 본다")
                except Exception as e:
                    say(f"{time.strftime('%H:%M:%S')} [KEEP] 플러그 호출 실패 — {type(e).__name__}: {e}")
                finally:
                    lock.close()
            time.sleep(tick_s)
    except KeyboardInterrupt:
        pass
    say(f"{time.strftime('%H:%M:%S')} [KEEP] 끝 — 플러그는 켜 둔 채로 둔다")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="레일 독 충전 — keep: 쉬는 동안 완충 유지 · status: 지금 가장 낮은 셀")
    ap.add_argument("what", choices=["keep", "status"])
    ap.add_argument("--hours", type=float, default=12.0, help="keep 시간 (기본 12시간)")
    a = ap.parse_args()
    if a.what == "status":
        low, n, why = rail_low()
        print(f"가장 낮은 셀 {low}% ({n}대)" if low is not None else f"못 읽음 — {why}")
        print(start_problem(low, why) or f"시작해도 된다 ({START_MIN_PCT}% 이상)")
        return 0
    return keep(a.hours)


if __name__ == "__main__":
    sys.exit(main())
