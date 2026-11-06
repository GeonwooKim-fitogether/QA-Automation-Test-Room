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

from cellbench.control import live_dir
from cellbench.guards import batt_ok

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PLUG_MANUAL = DATA / "plug_manual"         # 이 파일이 있으면 plug_set 은 플러그를 부르지 않는다 — 2026-11-05 Tapo 펌웨어가 TPAP 로 바뀌어 PC 가 못 다룰 때
LIVE = live_dir(DATA)                         # 2초마다 다시 쓰는 실시간 파일 자리 — C:\Dev 밖(cellbench.control.live_dir)
BATT_FILE = LIVE / "rail_batt.json"           # live_tap 이 쓰고 run_motion 이 읽는다
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
    if PLUG_MANUAL.exists():                     # 사람이 플러그를 손으로 다루는 날 — 부르지 않는다(못 찾는 플러그를 3번씩 기다리면 모터 출발이 늦는다)
        say(f"{time.strftime('%H:%M:%S')} [PLUG] 건너뜀 — {PLUG_MANUAL.name} 이 있다(사람이 앱으로 켜고 끈다). 원래 {'켜기' if on else '끄기'}")
        return {"want": "on" if on else "off", "skipped": True}
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


STATUS_FILE = DATA / "rail_charge.json"        # keep 이 틱마다 쓴다 — 결과판이 '충전 제어 끊김'을 띄운다
LOST_AFTER = 3                                  # 플러그 호출이 이만큼 잇달아 실패하면 끊김으로 본다


def safe_recharge(plug, gap_s: float, say=print, tries: int = 12, wait_s: float = 10.0):
    """끊었다 켜기 — 켜기가 실패하면 그 자리에서 tries 번(약 2분) 다시 켠다. 끝내 못 켜면 RuntimeError.
    2026-11-02: 끄기는 되고 켜기가 플러그를 못 찾아 실패해, 독이 꺼진 채 남은 일이 있었다(사용자가 손으로 켰다)."""
    from cellbench.plug import ensure_on
    plug._call("off")
    time.sleep(gap_s)
    try:
        return ensure_on(plug, tries=tries, wait_s=wait_s)
    except Exception as e:
        say(f"{time.strftime('%H:%M:%S')} [KEEP] 끊었다 켜기 뒤 다시 켜지 못했다 — 독이 꺼져 있을 수 있다. 플러그를 손으로 켠다 ({e})")
        raise


def test_running(path: Path = LIVE / "qc_now.json", fresh_s: float = 15.0) -> bool:
    """시험(live_tap --qc-now)이 지금 도는가 — 파트 A 는 모터 잠금이 없어서 이것으로 본다. 도는 동안에는 플러그를 끊지 않는다."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return (d.get("step") or {}).get("key") not in (None, "idle") and time.time() - float(d.get("t", 0)) < fresh_s
    except (OSError, ValueError):
        return False


def write_status(**kw) -> None:
    from cellbench.control import write_json_atomic
    try:
        write_json_atomic(STATUS_FILE, {"t": round(time.time(), 1)} | kw)
    except OSError:
        pass


def keep(hours: float, tick_s: float = 60.0, say=print) -> int:
    """기준 3 — 쉬는 동안 완충 유지. 플러그를 켜 두고, 전력이 바닥에 붙으면 끊었다 켠다.
    모터 잠금(motion.lock)을 쥘 수 없으면 run_motion 이 도는 중이라 그 틱은 플러그에 손대지 않는다."""
    from cellbench import proc
    from cellbench.config import Config
    from cellbench.guards import PowerWatch
    cfg = Config()
    watch = PowerWatch(cfg.stuck_w, cfg.stuck_s, cfg.stuck_gap_s, cfg.stuck_max)
    plug, end, fails = None, time.time() + hours * 3600, 0
    say(f"{time.strftime('%H:%M:%S')} [KEEP] 레일 독 완충 유지 {hours:g}시간 — Ctrl+C 로 끝낸다")
    try:
        manual = False
        while time.time() < end:
            if PLUG_MANUAL.exists():                 # 사람이 앱으로 다루는 날 — 못 찾는 플러그를 1분마다 찾지 않는다(11-06 새벽 271번 실패)
                if not manual:
                    say(f"{time.strftime('%H:%M:%S')} [KEEP] 쉼 — {PLUG_MANUAL.name} 이 있다(사람이 앱으로 켜고 끈다). 지우면 다시 본다")
                manual = True
                time.sleep(tick_s)
                continue
            manual = False
            lock = proc.acquire_lock(LOCK)
            if lock is None:
                say(f"{time.strftime('%H:%M:%S')} [KEEP] 모터 실행 중 — 플러그에 손대지 않는다")
            else:
                lock.close()                   # 확인만 — 플러그 호출(길면 20초) 동안 잠금을 쥐면 모터 시작이 거절된다(2026-11-02 20:29)
                try:
                    plug = plug or rail_plug()
                    r = plug.read()
                    act = watch.feed(time.time(), r.on, r.watts)
                    low, n, _ = rail_low()
                    say(f"{time.strftime('%H:%M:%S')} [KEEP] {'켜짐' if r.on else '꺼짐'} · {r.watts:.1f} W · "
                        f"가장 낮은 셀 {low if low is not None else '?'}% ({n}대)" + (f" → {act}" if act else ""))
                    if act in ("recharge", "plug_on") and test_running():
                        say(f"{time.strftime('%H:%M:%S')} [KEEP] 시험 중이라 플러그를 건드리지 않는다 — 시험이 끝나면 한다")
                        act = "wait_test"
                    if act == "recharge":
                        r = safe_recharge(plug, cfg.stuck_gap_s, say)
                    elif act == "plug_on" or (r.on is False and act is None and not test_running()):
                        from cellbench.plug import ensure_on
                        r = ensure_on(plug, tries=12, wait_s=10.0)
                    elif act == "give_up":
                        say(f"{time.strftime('%H:%M:%S')} [KEEP] 끊었다 켜기를 {cfg.stuck_max}번 해도 독이 전력을 끌어 쓰지 않는다 — 독을 직접 본다")
                    fails = 0
                    write_status(state="give_up" if act == "give_up" else "ok", on=r.on, watts=round(r.watts, 1),
                                 action=act, low_pct=low, cells=n, fails=0, error=None)
                except Exception as e:
                    fails += 1
                    plug = None                    # 다음 틱에 다시 찾는다(주소가 바뀌었을 수 있다)
                    say(f"{time.strftime('%H:%M:%S')} [KEEP] 플러그 호출 실패 {fails}번째 — {type(e).__name__}: {e}")
                    write_status(state="lost" if fails >= LOST_AFTER else "retry", on=None, watts=None, action=None,
                                 low_pct=rail_low()[0], cells=None, fails=fails, error=f"{type(e).__name__}: {e}"[:200])
            time.sleep(tick_s)
    except KeyboardInterrupt:
        pass
    say(f"{time.strftime('%H:%M:%S')} [KEEP] 끝 — 플러그는 켜 둔 채로 둔다")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="레일 독 충전 — keep: 쉬는 동안 완충 유지 · status: 지금 가장 낮은 셀")
    ap.add_argument("what", choices=["keep", "status"])
    ap.add_argument("--hours", type=float, default=12.0, help="keep 시간 (기본 12시간)")
    ap.add_argument("--log", help="keep 줄을 이 파일에 덧붙인다(창 없이 상시 돌 때 — tools/install_charge_keep.ps1)")
    a = ap.parse_args()
    if a.what == "status":
        low, n, why = rail_low()
        print(f"가장 낮은 셀 {low}% ({n}대)" if low is not None else f"못 읽음 — {why}")
        print(start_problem(low, why) or f"시작해도 된다 ({START_MIN_PCT}% 이상)")
        return 0
    from cellbench import proc
    one = proc.acquire_lock(DATA / "rail_charge_keep.lock")      # 둘이 동시에 플러그를 만지지 않게 — 이미 돌고 있으면 조용히 끝낸다
    if one is None:
        print("충전 유지가 이미 돌고 있다 — 끝낸다")
        return 0
    say = print
    if a.log:
        def say(m, _p=Path(a.log)):
            with open(_p, "a", encoding="utf-8") as f:
                f.write(m + "\n")
    try:
        return keep(a.hours, say=say)
    finally:
        one.close()


if __name__ == "__main__":
    sys.exit(main())
