"""기록 — 사이클 한 줄, 표본 한 줄, 이상 한 줄. 전부 CSV 라 엑셀로 바로 열린다.

data/
  cycles.csv              사이클마다 1줄 (결과판 '추이' 장의 재료)
  samples_<사이클>.csv     20초마다 1줄: 단계·플러그·전력·누적Wh·배터리 (결과판 '운영' 장의 재료)
  events.csv              이상 이벤트
  cells_<사이클>.csv       셀별 추출 결과
  ftg/<사이클>/            추출한 셀 파일
"""
from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path


def _ts(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


class Recorder:
    CYCLE_COLS = ["cycle", "start", "discharge_start", "discharge_end", "discharge_h", "min_batt_at_stop",
                  "plug_on", "extract_start", "extract_end", "extract_s", "extract_ok", "extract_mb",
                  "full_at", "charge_min", "charge_wh", "floor_w", "plug_off", "events", "note"]
    SAMPLE_COLS = ["time", "phase", "plug", "watts", "wh", "cells", "batt_min", "batt_avg", "batt_max", "hr_cells"]
    EVENT_COLS = ["time", "cycle", "phase", "kind", "serial", "detail"]
    CELL_COLS = ["cycle", "serial", "ip", "battery", "size_mb", "got_mb", "bad_blocks", "ended", "deleted",
                 "seconds", "resume_s", "error", "file"]

    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._cycles = self.dir / "cycles.csv"
        self._events = self.dir / "events.csv"
        self._ensure(self._cycles, self.CYCLE_COLS)
        self._ensure(self._events, self.EVENT_COLS)
        self._sample_path: Path | None = None
        self.log_path = self.dir / "run.log"

    @staticmethod
    def _ensure(path: Path, cols: list[str]) -> None:
        if not path.exists():
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(cols)

    @staticmethod
    def _append(path: Path, row: list) -> None:
        with open(path, "a", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow(row)

    def log(self, msg: str) -> None:
        line = f"{_ts()} {msg}"
        print(line, flush=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def next_cycle_no(self) -> int:
        with open(self._cycles, encoding="utf-8-sig") as f:
            return sum(1 for _ in f)          # 머리글 1줄 + 기록 n줄 → 다음 번호 = n+1

    def begin_cycle(self, cycle: int) -> None:
        self._sample_path = self.dir / f"samples_{cycle:04d}.csv"
        self._ensure(self._sample_path, self.SAMPLE_COLS)

    def sample(self, phase: str, plug_on: bool | None, watts: float, wh: float,
               batts: list[int], hr_cells: int) -> None:
        if self._sample_path is None:
            return
        row = [_ts(), phase, "" if plug_on is None else ("on" if plug_on else "off"),
               f"{watts:.2f}" if watts == watts else "", f"{wh:.3f}", len(batts),
               min(batts) if batts else "", f"{sum(batts)/len(batts):.1f}" if batts else "",
               max(batts) if batts else "", hr_cells]
        self._append(self._sample_path, row)

    def event(self, cycle: int, phase: str, kind: str, serial: int | str, detail: str) -> None:
        self._append(self._events, [_ts(), cycle, phase, kind, serial, detail])
        self.log(f"이상[{kind}] 셀 {serial}: {detail}")

    def cells(self, cycle: int, results: dict) -> None:
        path = self.dir / f"cells_{cycle:04d}.csv"
        self._ensure(path, self.CELL_COLS)
        for s, r in sorted(results.items()):
            self._append(path, [cycle, s, r.ip, r.battery if r.battery is not None else "",
                                f"{r.size/1048576:.2f}" if r.size else "", f"{r.got/1048576:.2f}",
                                r.bad_blocks, int(r.ended), int(r.deleted),
                                f"{r.t_end - r.t_start:.0f}" if r.t_end else "",
                                f"{r.resume_s:.0f}" if r.resume_s is not None else "",
                                r.error or "", r.file or ""])

    def cycle(self, row: dict) -> None:
        self._append(self._cycles, [row.get(c, "") for c in self.CYCLE_COLS])

    def now(self, payload: dict) -> bool:
        """결과판용 '지금 상태'. 반쯤 쓴 파일을 읽지 않도록 임시 파일에 쓰고 바꿔치기한다.

        Windows 에서는 결과판 서버가 now.json 을 여는 그 순간 바꿔치기가 '액세스 거부'로 실패한다
        (2026-10-07 18:19, 이것으로 시험 프로그램 전체가 멈췄다). 그래서 몇 번 다시 해 보고,
        끝내 안 되면 이번 갱신만 건너뛴다. 화면용 파일 하나 때문에 시험이 멈춰서는 안 된다.
        """
        tmp = self.dir / "now.json.tmp"
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            return False
        for _ in range(10):
            try:
                os.replace(tmp, self.dir / "now.json")
                return True
            except PermissionError:
                time.sleep(0.05)
        return False

    DISCHARGE_COLS = ["cycle", "serial", "start_pct", "end_pct", "hours", "pct_per_h", "est_runtime_h"]

    def discharge(self, cycle: int, start: dict[int, int], end: dict[int, int], seconds: float, stop_pct: int) -> None:
        """셀별 방전 속도. 방전은 가장 빠른 셀이 기준선에 닿으면 끝나므로, 셀마다 '작동시간'은
        직접 잴 수 없다. 대신 같은 시간 동안 몇 % 내려갔는지로 100%→기준선 작동시간을 환산한다."""
        path = self.dir / f"discharge_{cycle:04d}.csv"
        self._ensure(path, self.DISCHARGE_COLS)
        hours = seconds / 3600
        for s in sorted(set(start) & set(end)):
            drop = start[s] - end[s]
            rate = drop / hours if hours > 0 else 0
            est = (100 - stop_pct) / rate if rate > 0 else ""
            self._append(path, [cycle, s, start[s], end[s], f"{hours:.3f}", f"{rate:.2f}",
                                f"{est:.2f}" if est != "" else ""])
