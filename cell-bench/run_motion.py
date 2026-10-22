"""리니어 모터 모션 엔진 — M1 읽기 전용 (dynamic-bench plan-v2 5.1). EtherCAT SAFE-OP 까지만 가고 모터를 움직이지 않는다.

사용 (작업 트리 루트에서):
  python cell-bench/run_motion.py scan                   # 드라이브를 찾아 기록과 대조 · SDO 읽기 · PDO 1초 → data/drives.json
  python cell-bench/run_motion.py monitor --minutes 10   # 위 + 2 ms 주기 PDO 읽기 → data/motion.json(1초 심박) · data/motion_<시각>.csv

절대 규칙(dynamic-bench/dev/m1-prompt.md): 서보 온 없음 · SAFE-OP 까지만 · 출력 Controlword 는 늘 0 · SDO 쓰기 없음 · 움직임 없음.
예상 밖 Statusword · 상태 전이 · 0x603F · 슬레이브 수 · PDO 매핑 · WKC 3주기 연속 불일치면 즉시 INIT 로 내리고 어댑터를 닫는다.
pysoem 과 Npcap 이 필요하다(cell-bench requirements.txt 에는 아직 없다).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from cellbench import proc
from cellbench.control import write_json_atomic
from motionbench import ecat
from motionbench import motion_record as rec

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"          # ponytail: cell-bench 의 data 폴더에 고정. 감시자가 읽게 될 때(M3) Config.data_dir 로 바꾼다


def cycle(m, s, pos: int, seconds: float, csv_path: Path | None = None, beat_path: Path | None = None):
    """주기 스레드를 돌리고, 이 (주) 스레드는 0.1초마다 행을 꺼내 CSV 에 쓰고 1초마다 심박을 쓴다 → (멈춘 이유, Log)."""
    cyc = ecat.Cycler(m, s, pos, seconds)
    log = rec.Log(csv_path, m.expected_wkc)
    started = say = beat = time.time()
    cyc.start()
    try:
        while cyc.is_alive():
            time.sleep(0.1)
            log.take(cyc.rows)
            now = time.time()
            if beat_path and now - beat >= 1.0:
                rec.write_beat(beat_path, started, log, None)
                beat = now
            if now - say >= 60:
                c, v = log.summary(500), log.last
                proc.console(f"{rec.ts()} 주기 {log.n} · WKC 오류 {log.wkc_err} · 최근 1초 p99 {c['cycle_ms'].get('p99')} ms"
                             f" · {c['now']['state'] if v else '-'} · pos {v.pos if v else '-'}")
                say = now
    except KeyboardInterrupt:
        cyc.stop = cyc.stop or "Ctrl+C"
    finally:
        cyc.stop = cyc.stop or "기록 쪽 예외"   # 이 스레드가 예외로 빠져나가도 주기 스레드는 세운다
        cyc.join(5)
        log.take(cyc.rows)
        log.close()
    if beat_path:
        rec.write_beat(beat_path, started, log, cyc.stop, final=True)
    return cyc.stop, log


def main() -> int:
    ap = argparse.ArgumentParser(description="리니어 모터 모션 엔진 — M1 읽기 전용")
    ap.add_argument("mode", choices=["scan", "monitor"])
    ap.add_argument("--minutes", type=float, default=10.0, help="monitor 시간 (기본 10분)")
    args = ap.parse_args()
    proc.safe_stdio()
    DATA.mkdir(exist_ok=True)
    lock = proc.acquire_lock(DATA / "motion.lock")     # 장비는 한 프로세스만 만진다 — 두 번째 엔진은 시작하지 않는다
    if lock is None:
        proc.console("다른 모션 엔진이 이미 돈다 — 시작하지 않는다")
        return 1
    sys.setswitchinterval(0.0005)    # 주기 스레드가 GIL 을 기다리는 시간을 0.5 ms 안으로 (기본 5 ms)

    info = {"t": time.time(), "at": rec.ts(), "mode": args.mode, "ifname": ecat.IFNAME}
    m = ecat.pysoem.Master()
    try:
        m.open(ecat.IFNAME)
    except Exception as e:           # Npcap 권한 등 — 우회(관리자 권한 상승)하지 않고 그대로 기록하고 끝낸다
        info["exit"] = f"어댑터를 열지 못함 — {type(e).__name__}: {e}"
        write_json_atomic(DATA / "drives.json", info)
        proc.console(info["exit"])
        return 1
    reason = None
    try:
        s, pos = ecat.bring_up(m, info)
        if args.mode == "monitor":
            stamp = time.strftime("%Y%m%d-%H%M%S")
            info["csv"] = str(DATA / f"motion_{stamp}.csv")
            proc.console(f"{rec.ts()} SAFE-OP · monitor {args.minutes}분 시작 — {info['csv']}")
            reason, log = cycle(m, s, pos, args.minutes * 60, Path(info["csv"]), DATA / "motion.json")
        else:
            reason, log = cycle(m, s, pos, 1.0)
        info["pdo"] = log.summary()
    except ecat.Stop as e:
        reason = str(e)
    except KeyboardInterrupt:
        reason = "Ctrl+C"
    except Exception as e:           # 모르는 예외 — 이유를 남기고 INIT 로 내린 뒤 그대로 올린다
        reason = f"예외 {type(e).__name__}: {e}"
        raise
    finally:
        info["closed"] = ecat.shut(m)
        info["exit"] = reason
        info["emergencies"] = ecat.emergencies
        info["ended_at"] = rec.ts()
        write_json_atomic(DATA / "drives.json", info)
    proc.console(json.dumps(info, ensure_ascii=False, indent=1))
    return 0 if reason == ecat.DONE else 1


if __name__ == "__main__":
    sys.exit(main())
