"""모션 엔진의 기록 — motion.json(1초 심박) · motion_<시각>.csv(2 ms 주기마다 한 줄) · 주기 통계.

파일은 이 파일이 쓴다. 주기 스레드(ecat.Cycler)는 행을 deque 에 넣기만 하고 파일을 만지지 않는다 — 주기 스레드는 멈추지 않는다(plan-v2 2.4).
CSV 의 시각은 줄마다 둘이다. t_wall = time.time() (셀 기록과 같은 PC 벽시계), t_mono_ns = time.perf_counter_ns()
(벽시계가 튀어도 간격은 정확하다). 둘 다 응답 프레임을 받은 직후에 잰다. rtt_us 는 보내기 직전부터 그 순간까지다.
"""
from __future__ import annotations

import csv
import os
import time
from array import array
from collections import Counter, deque
from pathlib import Path

from cellbench.control import write_json_atomic

from .cia402 import state

CSV_HEAD = ["t_wall", "t_mono_ns", "rtt_us", "wkc", "statusword", "err_code", "mode", "pos", "ferr", "di", "tp_status", "tp_pos1"]


def ts(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))      # cellbench.record._ts 와 같은 서식


def cycle_stats(ms, late_ms: float = 4.0) -> dict:
    """시간(ms) 목록 → 요약. 백분위는 최근접 순위(p99 = 99 % 가 이 값 이하).
    late = late_ms 초과 개수 — 주기 간격에는 주기의 2배(4 ms, M1 목표선), 왕복 시간에는 SOEM 기본 대기 2 ms 를 쓴다."""
    s = sorted(ms)
    n = len(s)
    if not n:
        return {"n": 0}

    def q(permille: int) -> float:
        return round(s[max(0, (permille * n + 999) // 1000 - 1)], 3)

    return {"n": n, "min": round(s[0], 3), "mean": round(sum(s) / n, 3), "p50": q(500), "p99": q(990),
            "p999": q(999), "max": round(s[-1], 3), "late_ms": late_ms, "late": sum(x > late_ms for x in s),
            "over_20ms": sum(x > 20 for x in s)}


def _span(r: list | None, x: int) -> list:
    return [x, x] if r is None else [min(r[0], x), max(r[1], x)]


def _hex(c: Counter, width: int) -> dict:
    return {f"0x{k:0{width}X}": n for k, n in sorted(c.items())}


class Log:
    """주기 스레드의 행 (t_wall, t_mono_ns, rtt_us, wkc, Inputs 또는 None) 을 꺼내 CSV 에 적고 요약을 쌓는다."""

    def __init__(self, csv_path: Path | None, expected_wkc: int):
        self.exp = expected_wkc
        self.iv = array("d")                 # 주기 간격 ms — 연속한 두 수신 시각의 차
        self.rtt = array("d")                # 왕복 시간 ms
        self.prev = None
        self.n = self.wkc_err = 0
        self.wkc_ok = None
        self.sw, self.err, self.di, self.mode = Counter(), Counter(), Counter(), Counter()
        self.pos = self.ferr = None          # [최소, 최대]
        self.last = None                     # 마지막으로 읽은 Inputs
        self.f = open(csv_path, "w", newline="", encoding="utf-8") if csv_path else None
        self.w = csv.writer(self.f) if self.f else None
        if self.w:
            self.w.writerow(CSV_HEAD)

    def take(self, rows: deque) -> None:
        while rows:
            t_wall, t_mono, rtt_us, wkc, v = rows.popleft()
            self.n += 1
            if self.prev is not None:
                self.iv.append((t_mono - self.prev) / 1e6)
            self.prev = t_mono
            self.rtt.append(rtt_us / 1000)
            self.wkc_ok = v is not None
            if v is None:                    # WKC 불일치 — 입력이 갱신되지 않았으니 시각과 WKC 만 적는다
                self.wkc_err += 1
                if self.w:
                    self.w.writerow([f"{t_wall:.6f}", t_mono, rtt_us, wkc])
                continue
            self.last = v
            self.sw[v.sw] += 1
            self.err[v.err] += 1
            self.di[v.di] += 1
            self.mode[v.mode] += 1
            self.pos, self.ferr = _span(self.pos, v.pos), _span(self.ferr, v.ferr)
            if self.w:
                self.w.writerow([f"{t_wall:.6f}", t_mono, rtt_us, wkc, v.sw, v.err, v.mode, v.pos, v.ferr, v.di, v.tp_status, v.tp_pos1])

    def close(self) -> None:
        if self.f:
            self.f.close()

    def summary(self, last: int | None = None) -> dict:
        """지금까지의 요약. last 를 주면 주기 통계는 마지막 last 개만 — 심박마다 30만 개를 정렬하면 그동안 주기 스레드가 GIL 을 못 얻는다."""
        v = self.last
        return {
            "cycles": self.n, "wkc_err": self.wkc_err, "wkc_ok": self.wkc_ok,
            "cycle_ms": cycle_stats(self.iv[-last:] if last else self.iv),
            "rtt_ms": cycle_stats(self.rtt[-last:] if last else self.rtt, late_ms=2.0),
            "now": None if v is None else {
                "state": state(v.sw), "statusword": f"0x{v.sw:04X}", "err_code": f"0x{v.err:04X}", "mode": v.mode,
                "pos": v.pos, "pos_mm": round(v.pos / 1000, 3), "ferr": v.ferr, "di": f"0x{v.di:08X}"},
            "seen": {"statusword": _hex(self.sw, 4), "err_code": _hex(self.err, 4), "di": _hex(self.di, 8),
                     "mode": dict(sorted(self.mode.items()))},
            "pos_range": self.pos, "ferr_range": self.ferr,
        }


def write_beat(path: Path, started: float, log: Log, exit_reason: str | None, final: bool = False) -> bool:
    """motion.json — 1초 심박. 돌고 있으면 최근 1초(500 주기)의 통계, 끝나면 전체 통계와 멈춘 이유."""
    doc = {"beat": time.time(), "beat_at": ts(), "mode": "monitor", "pid": os.getpid(), "started": started,
           "running": not final, "exit": exit_reason, "slave": 1, **log.summary(None if final else 500)}
    return write_json_atomic(path, doc)
