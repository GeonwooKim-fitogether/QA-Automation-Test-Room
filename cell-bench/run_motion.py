"""리니어 모터 모션 엔진 — M1 읽기 전용 · M2 입회 왕복 (dynamic-bench plan-v2 5.1).

사용 (작업 트리 루트에서):
  python cell-bench/run_motion.py scan                   # 드라이브를 찾아 기록과 대조 · SDO 읽기 · PDO 1초 → data/drives.json
  python cell-bench/run_motion.py monitor --minutes 10   # 위 + 2 ms 주기 PDO 읽기 → data/motion.json(1초 심박) · data/motion_<시각>.csv
  python cell-bench/run_motion.py move                   # M2 — 승인 파일 + 유효 입회 표지가 있을 때만 PP 상대 왕복 → data/motion_m2.json

scan · monitor 절대 규칙(dynamic-bench/dev/m1-prompt.md): 서보 온 없음 · SAFE-OP 까지만 · 출력 Controlword 는 늘 0 · SDO 쓰기 없음.
예상 밖 Statusword · 상태 전이 · 0x603F · 슬레이브 수 · PDO 매핑 · WKC 3주기 연속 불일치면 즉시 INIT 로 내리고 어댑터를 닫는다.
move 의 규칙은 dynamic-bench/dev/m2-prompt.md — 수치는 승인 파일에서만, 이상이면 Quick stop, 끝은 늘 Quick stop → 0x06 → 0 → SAFE-OP → INIT.
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
from motionbench import ecat, m2, safety
from motionbench import motion_record as rec

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"          # ponytail: cell-bench 의 data 폴더에 고정. 감시자가 읽게 될 때(M3) Config.data_dir 로 바꾼다
APPROVAL = ROOT.parent / "dynamic-bench" / "dev" / "m2-approval.json"   # 읽기만 한다 — 다른 쪽이 사용자 승인으로 만든다
MARKER = DATA / "motion_attended"                                         # 읽기만 한다 — 다른 쪽이 사용자 승인으로 만든다
DONE_M2 = "왕복 다 됨"
GEAR_SDO = [(0x608F, 1), (0x608F, 2), (0x6091, 1), (0x6091, 2), (0x6092, 1), (0x6092, 2)]


def move(m, info: dict) -> str:
    """M2 — 입회 하 Profile Position 으로 p0 기준 상대 distance 왕복을 승인 파일의 cycles 회. 마지막 왕복의 가는 길
    중간에서 Quick stop 을 스스로 걸어 정지 거리를 재고, 다시 켜서 p0 로 돌아온다. → 끝난 이유 (정상이면 DONE_M2)."""
    a, why = safety.load_approval(APPROVAL)
    info["approval"] = a or why
    if why:
        return why
    mwhy, deadline = safety.check_marker(MARKER, time.time())
    info["marker_start"] = {"valid": not mwhy, "why": mwhy, "deadline": rec.ts(deadline) if deadline else None}
    proc.console(f"{rec.ts()} 입회 표지 검사 — " + (f"무효: {mwhy}" if mwhy else f"유효 (마감 {rec.ts(deadline)})"))
    if mwhy:
        return f"입회 표지 무효 — 서보 온 거부: {mwhy}"
    k = safety.PULSE_PER_MM
    D, W = round(a["distance_mm"] * k) * a["first_move_dir"], round(a["window_mm"] * k)
    V, A = round(a["vmax_mm_s"] * k), round(a["amax_mm_s2"] * k)
    s = ax = cyc = log = limit0 = None
    opened, reason = False, None
    try:
        m.open(ecat.IFNAME)          # Npcap 권한 등으로 실패하면 우회하지 않고 그대로 기록하고 끝낸다
        opened = True
        s, p0 = ecat.bring_up(m, info)                       # 슬레이브 1대 · 정체 · PDO 매핑 대조 · SAFE-OP (M1 그대로)
        sdo = {r["obj"]: r["value"] for r in info["sdo"]}
        gear = {f"0x{i:04X}:{j:02X}": ecat.sdo(s, i, j, "<I")[0] for i, j in GEAR_SDO}
        over, over_err = ecat.sdo(s, 0x3513, 0, "<h")        # Pr5.13 과속도 레벨 (PANATERM 객체 덤프: 3513h "Over-speed level setup")
        err, sw = ecat.sdo(s, 0x603F, 0, "<H")[0], ecat.sdo(s, 0x6041, 0, "<H")[0]
        a_qs = sdo["0x6085:00"]
        info["checks"] = {"p0": p0, "gear": gear, "pr5_13_mm_s": over, "pr5_13_error": over_err, "err": err,
                          "statusword": None if sw is None else f"0x{sw:04X}", "quick_stop_decel": a_qs,
                          "stop_distance_pulse": round(V * V / (2 * a_qs), 1) if a_qs else None,
                          "profile_before": {f"0x{i:04X}": ecat.sdo(s, i, 0, "<I")[0] for i in (0x6081, 0x6083, 0x6084)}}
        why = (safety.start_problem(info["slaves"], sdo["0x1018:04"], err or 0, sw or 0, sdo["0x605A:00"])
               or (err is None or sw is None) and "0x603F · 0x6041 을 읽지 못했다"
               or safety.units_problem(gear) or safety.speed_problem(a["vmax_mm_s"], over)
               or safety.target_problem(p0, p0 + D, W, V, a_qs) or safety.target_problem(p0, p0, W, V, a_qs))
        info["checks"]["result"] = why or "통과"
        if why:
            raise m2.Abort(f"움직이지 않음 — {why}")
        limit0 = (sdo["0x607D:01"], sdo["0x607D:02"])
        info["sdo_written"] = {"0x6081:00": ecat.sdo_put(s, 0x6081, 0, "<I", V), "0x6083:00": ecat.sdo_put(s, 0x6083, 0, "<I", A),
                               "0x6084:00": ecat.sdo_put(s, 0x6084, 0, "<I", A),
                               "0x607D:01": ecat.sdo_put(s, 0x607D, 1, "<i", p0 - W), "0x607D:02": ecat.sdo_put(s, 0x607D, 2, "<i", p0 + W)}
        stamp = time.strftime("%Y%m%d-%H%M%S")
        info["csv"] = str(DATA / f"motion_m2_{stamp}.csv")
        ax = m2.Axis(p0, W, deadline)
        cyc = ecat.Cycler(m, s, p0, 3600, ctl=ax)
        log = rec.Log(Path(info["csv"]), m.expected_wkc)
        ax.idle = lambda: log.take(cyc.rows)
        ax.dead = lambda: cyc.stop or (None if cyc.is_alive() else "주기 스레드가 끝났다")
        cyc.start()
        v = ax.pause(0.3)                                    # SAFE-OP 에서 입력 PDO 로 한 번 더
        why = safety.start_problem(1, sdo["0x1018:04"], v.err, v.sw, sdo["0x605A:00"]) if v else "입력 PDO 가 안 온다"
        if why:
            raise m2.Abort(f"움직이지 않음 — {why}")
        info["op"] = ecat.go(m, s, ecat.pysoem.OP_STATE)
        proc.console(f"{rec.ts()} OP 요청 — {info['op']}")
        if s.state != ecat.pysoem.OP_STATE:
            raise m2.Abort(f"OP 에 못 감 — {info['op']}")
        info["servo_on"] = m2.servo_on(ax, MARKER)
        proc.console(f"{rec.ts()} 서보 온 (Operation enabled) · p0 {p0} · 목표 {p0 + D} · 창 ±{W}")
        info["legs"] = []
        n = a["cycles"]
        for c in range(1, n + 1):
            for target, qs in ((p0 + D, abs(D) // 2 if c == n else None), (p0, None)):
                leg = {"cycle": c, **m2.move(ax, target, MARKER, V, A, a_qs, qs)}
                info["legs"].append(leg)
                proc.console(f"{rec.ts()} {json.dumps(leg, ensure_ascii=False)}")
                if qs is not None:
                    info["resume"] = m2.resume(ax, MARKER)     # Quick stop 시험 뒤 정상 복귀 — 창 안의 p0 로만
        reason = DONE_M2
    except (ecat.Stop, m2.Abort) as e:
        reason = str(e)
    except KeyboardInterrupt:
        reason = "Ctrl+C"
    except Exception as e:           # 모르는 예외 — 이유를 남기고 내린다
        reason = f"예외 {type(e).__name__}: {e}"
    finally:                         # 늘: Quick stop → Shutdown 0x06 → Controlword 0 → SAFE-OP → INIT → 닫기. 한 단계가 실패해도 다음으로
        if ax:
            _safely(info, "finish", lambda: m2.finish(ax))
            _safely(info, "safe_op", lambda: ecat.go(m, s, ecat.pysoem.SAFEOP_STATE))
            v = ax.last                                      # 서보가 꺼졌는지 — SAFE-OP 에서도 입력은 계속 온다
            info["final"] = None if v is None else {"state": rec.state(v.sw), "statusword": f"0x{v.sw:04X}", "err": f"0x{v.err:04X}", "pos": v.pos}
            cyc.stop = cyc.stop or "끝"
            cyc.join(5)
            _safely(info, "pdo", lambda: (log.take(cyc.rows), log.close(), log.summary())[-1])
            info["alarms"] = [(rec.ts(t), w) for t, w in ax.alarms]
            info["qs_latched"] = ax.qs
            _safely(info, "trace_csv", lambda: _trace(ax.trace, info["csv"].replace(".csv", "_trace.csv")))
        if limit0:                   # 창으로 좁힌 0x607D 를 처음 값으로 되돌린다 (RAM — 전원을 끄면 어차피 돌아간다)
            _safely(info, "sdo_restored", lambda: {"0x607D:01": ecat.sdo_put(s, 0x607D, 1, "<i", limit0[0]),
                                                   "0x607D:02": ecat.sdo_put(s, 0x607D, 2, "<i", limit0[1])})
        if opened:
            info["closed"] = ecat.shut(m)
    return reason


def _safely(info: dict, key: str, fn) -> None:
    try:
        info[key] = fn()
    except Exception as e:
        info[key] = f"실패 {type(e).__name__}: {e}"


def _trace(trace, path: str) -> str:
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_mono_ns", "cw_sent", "target_sent", "statusword", "err_code", "mode", "pos", "ferr"])
        w.writerows(trace)
    return path


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
    ap = argparse.ArgumentParser(description="리니어 모터 모션 엔진 — M1 읽기 전용 · M2 입회 왕복")
    ap.add_argument("mode", choices=["scan", "monitor", "move"])
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
    if args.mode == "move":
        info["exit"] = move(m, info)
        info["emergencies"] = ecat.emergencies
        info["ended_at"] = rec.ts()
        write_json_atomic(DATA / "motion_m2.json", info)
        proc.console(json.dumps(info, ensure_ascii=False, indent=1, default=str))
        return 0 if info["exit"] == DONE_M2 else 1
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
