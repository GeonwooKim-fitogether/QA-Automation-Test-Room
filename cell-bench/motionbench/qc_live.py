"""레일 실시간 화면용 qc_now.json — 정지 상태 최소판 (2026-10-30, 결과판 세션 요청 · 사용자 "진짜 셀 값으로 바로 시험").

live_tap 이 받는 셀 라이브(0x09)만으로 만든다. 모터는 돌지 않는다 — motor 칸은 '멈춤'으로 고정하고 판정은 하지 않는다.
칸의 정본은 dynamic-bench/design/qc-protocol-v4.md 7-5 의 3 · 7-6. 이 최소판이 채우는 것과 비우는 것:
  채움: age_s · packets_10s · fix · cn0 · sats · sog_raw(speed_history 끝 값, mm/s) · sog_err(정지라 정답 0 → sog_raw 와 같다) ·
        battery · fix_age_s · dock{col,row} · grid · step(A0 정지, scene_step "still") · conditions(protocol v4, part A)
  null: pos_err_m(기준 위치 없음) · moving(셀이 보내지 않는다) · clock_dev_ms(시각 맞춤 방식 미정) · motor 의 위치 · 추종 오차 · 주기
speed_history 의 순서(끝이 최신)는 protocol.parse_gps 의 기본 가정이고 [확인 필요]다.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path

from cellbench.protocol import parse_gps

LAYOUT = Path(__file__).resolve().parent / "dock_layout.json"
QC_FILE = Path(__file__).resolve().parents[1] / "data" / "qc_now.json"
SOURCE = "live-still"          # 맨 위 칸 source — 데모(demo)와 구분


def layout(set_name: str = "Y", path: Path = LAYOUT) -> list[list[int]]:
    return [[int(s) for s in col] for col in json.loads(Path(path).read_text(encoding="utf-8"))[set_name]["columns"]]


def idle(now: float) -> dict:
    """시험이 끝났다 — 결과판 · 클라우드가 '시험 없음'으로 바꾼다 (7-5 의 3)."""
    return {"source": SOURCE, "t": round(now, 3), "step": {"key": "idle", "index": None}}


class QcLive:
    """feed(데이터그램, 받은 시각) 로 셀별 최신 값을 모으고, frame(지금) 이 qc_now 한 장이다. 장비 · 파일에 닿지 않는다."""

    def __init__(self, columns: list[list[int]], started: float, site: str = "lab", repeater: str = "on", set_name: str = "Y"):
        self.columns, self.started = columns, started
        self.serials = [s for col in columns for s in col]
        self.dock = {s: {"col": c + 1, "row": r + 1} for c, col in enumerate(columns) for r, s in enumerate(col)}
        self.grid = [[col[r] for col in columns] for r in range(len(columns[0]))]
        self.last: dict[int, dict] = {}
        self.times: dict[int, deque] = {s: deque(maxlen=64) for s in self.serials}
        self.fix_t: dict[int, float] = {}
        self._lock = threading.Lock()              # feed 는 수신 스레드, frame 은 쓰기 스레드
        self.cond = {"protocol": "v4", "part": "A", "site": site, "sky": "미확인", "repeater": repeater, "corrections": "미확인",
                     "set": set_name, "cell_type": set_name, "fw": "미확인", "dock": f"DKP{set_name}-1C", "scene": {"name": "없음 — 정지"},
                     "speeds_mm_s": [], "accels_mm_s2": [], "still_s": None, "dwell_s": None}

    def feed(self, d: bytes, now: float) -> None:
        g = parse_gps(d)
        if g is None or g.serial not in self.dock:
            return
        with self._lock:
            self._feed(g, d[52], now)

    def _feed(self, g, battery: int, now: float) -> None:
        self.times[g.serial].append(now)
        if g.fix not in ("N",) and not g.fix.startswith("0x"):
            self.fix_t.setdefault(g.serial, now)
        else:
            self.fix_t.pop(g.serial, None)
        self.last[g.serial] = {"t": now, "fix": g.fix, "cn0": g.cn0, "sats": g.sats, "battery": battery,
                               "sog": g.speed_history[-1] if g.speed_history else None}

    def _cell(self, s: int, now: float) -> dict:
        c = self.last.get(s)
        out = {"age_s": None, "packets_10s": sum(1 for t in self.times[s] if now - t <= 10), "fix": None, "cn0": None, "sats": None,
               "sog_raw": None, "sog_err": None, "pos_err_m": None, "moving": None, "clock_dev_ms": None, "battery": None,
               "fix_age_s": None, "dock": self.dock[s]}
        if c:
            fixed = s in self.fix_t
            out.update({"age_s": round(now - c["t"], 1), "fix": c["fix"], "cn0": c["cn0"], "sats": c["sats"],
                        "battery": c["battery"] if 0 <= c["battery"] <= 100 else None,
                        "sog_raw": c["sog"] if fixed else None, "sog_err": c["sog"] if fixed else None,
                        "fix_age_s": round(now - self.fix_t[s], 1) if fixed else None})
        return out

    def frame(self, now: float) -> dict:
        step = {"key": "A0", "index": 0, "started": round(self.started, 3), "ends": None, "v_mm_s": 0.0, "a_mm_s2": 0.0,
                "scene_now": None, "band_kmh": None, "scene_step": "still"}
        motor = {"pos_mm": None, "v_mm_s": 0.0, "ferr_max_um": None, "ferr_limit_um": None, "alarms": [], "cycle_ms_max": None,
                 "state": "stopped"}
        with self._lock:
            cells = {str(s): self._cell(s, now) for s in self.serials}
        return {"source": SOURCE, "t": round(now, 3), "stamp": time.strftime("%Y%m%d-%H%M%S", time.localtime(self.started)),
                "conditions": self.cond, "part": "A", "step": step, "motor": motor,
                "cells": cells, "grid": self.grid}
