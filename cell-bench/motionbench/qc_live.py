"""레일 실시간 화면용 qc_now.json — 정지 상태 최소판 (2026-10-30, 결과판 세션 요청 · 사용자 "진짜 셀 값으로 바로 시험").

live_tap 이 받는 셀 라이브(0x09)만으로 만든다. 모터는 돌지 않는다 — motor 칸은 '멈춤'으로 고정하고 판정은 하지 않는다.
칸의 정본은 dynamic-bench/design/qc-protocol-v4.md 7-5 의 3 · 7-6. 이 최소판이 채우는 것과 비우는 것:
  채움: age_s · packets_10s · fix · cn0 · sats · sog_raw(speed_history 끝 값, mm/s) · sog_err(정지라 정답 0 → sog_raw 와 같다) ·
        battery · fix_age_s · dock{col,row} · grid · step(A0 정지, scene_step "still") · conditions(protocol v4, part A)
  null: pos_err_m(기준 위치 없음) · moving(셀이 보내지 않는다) · clock_dev_ms(시각 맞춤 방식 미정) · motor 의 추종 오차 · 주기
모터 칸(2026-10-30 더함): run_motion.py long 이 127.0.0.1:60333 으로 50 Hz 로 흘리는 레일 위치 탭(motion_record.PosTap)을 MotorTap 이
받아 pos_mm(왼쪽 끝에서 잰 mm, 레일 파일 · 축 정의로 바꿈) · v_mm_s · state(moving · holding · stopped)를 채운다. 탭이 1초 넘게
조용하면 모터 프로그램이 돌지 않는 것으로 보고 stopped. 파트 C(레일만 움직이고 하늘은 정지)는 part="C" 로 띄운다.
같은 포트를 sim_bridge.py 도 듣는다 — 둘을 함께 띄우면 늦게 띄운 쪽이 포트를 못 연다(LabSat 3 는 실시간 입력이 없어 지금은 sim_bridge 를 쓰지 않는다).
speed_history 의 순서(끝이 최신)는 protocol.parse_gps 의 기본 가정이고 [확인 필요]다.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
from collections import deque
from pathlib import Path

from cellbench.protocol import parse_gps

LAYOUT = Path(__file__).resolve().parent / "dock_layout.json"
QC_FILE = Path(__file__).resolve().parents[1] / "data" / "qc_now.json"
RAIL = Path(__file__).resolve().parents[1] / "data" / "rail-motor1.json"
AXIS = Path(__file__).resolve().parents[2] / "dynamic-bench" / "config" / "axis-motor1.json"
SOURCE = "live-still"          # 맨 위 칸 source — 데모(demo)와 구분
TAP_PORT = 60333               # motion_record.POS_TAP_PORT 와 같다 (그 모듈을 가져오지 않으려고 숫자로 둔다)
TAP = struct.Struct("<dii")    # [t_wall f64][pos i32 µm][v i32 µm/s] — motion_record.PosTap.FMT
TAP_STALE_S = 1.0              # 이보다 오래 탭이 조용하면 모터 프로그램이 돌지 않는 것
MOVING_MM_S = 1.0              # 이보다 빠르면 moving, 아니면 holding(서보 온 · 멈춤)


def rail_ref(rail_path: Path = RAIL, axis_path: Path = AXIS) -> tuple[dict | None, bool | None]:
    """(레일 파일, + 방향이 왼쪽인가). 못 읽으면 (None, None) — 그때 pos_mm 는 null."""
    try:
        doc = json.loads(Path(rail_path).read_text(encoding="utf-8"))
        ax = json.loads(Path(axis_path).read_text(encoding="utf-8"))
        return doc, ax.get("plus_direction") == "왼쪽"
    except (OSError, ValueError, AttributeError):
        return None, None


def to_left_mm(pos_um: int, doc: dict | None, plus_is_left: bool | None) -> float | None:
    """드라이브 위치(µm = pulse) → 왼쪽 끝에서 잰 mm. rail.from_left 의 거꾸로."""
    if not doc or plus_is_left is None or "plus_end" not in doc or "minus_end" not in doc:
        return None
    return round(((doc["plus_end"] - pos_um) if plus_is_left else (pos_um - doc["minus_end"])) / 1000, 1)


def motor_frame(last: tuple[float, int, int] | None, now: float, doc: dict | None = None, plus_is_left: bool | None = None) -> dict:
    """탭의 마지막 표본 (받은 시각, pos µm, v µm/s) → qc_now 의 motor 칸. 순수 함수."""
    out = {"pos_mm": None, "v_mm_s": 0.0, "ferr_max_um": None, "ferr_limit_um": None, "alarms": [], "cycle_ms_max": None,
           "state": "stopped"}
    if last is None or now - last[0] > TAP_STALE_S:
        return out
    v = last[2] / 1000
    if plus_is_left:              # 탭의 속도는 드라이브 좌표(+ 방향) — pos_mm 처럼 '왼쪽 끝에서 멀어지면 +' 로 맞춘다 (2026-10-30 시연에서 화살표가 거꾸로 그려짐)
        v = -v
    out.update({"pos_mm": to_left_mm(last[1], doc, plus_is_left), "v_mm_s": round(v, 1),
                "state": "moving" if abs(v) > MOVING_MM_S else "holding"})
    return out


class MotorTap(threading.Thread):
    """레일 위치 탭 듣기 — last = (받은 시각, pos µm, v µm/s). 포트를 못 열면 error 에 이유를 남기고 끝난다(셀 기록은 계속)."""

    def __init__(self, port: int = TAP_PORT):
        super().__init__(daemon=True)
        self.port, self.last, self.error, self.n = port, None, None, 0
        self._halt = threading.Event()

    def run(self) -> None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("127.0.0.1", self.port))
        except OSError as e:
            self.error = f"모터 탭 포트 {self.port} 를 열지 못함 — {e}"
            return
        s.settimeout(0.5)
        with s:
            while not self._halt.is_set():
                try:
                    d = s.recv(64)
                except socket.timeout:
                    continue
                if len(d) == TAP.size:
                    _, pos, v = TAP.unpack(d)
                    self.last, self.n = (time.time(), pos, v), self.n + 1

    def halt(self) -> None:
        self._halt.set()


def layout(set_name: str = "Y", path: Path = LAYOUT) -> list[list[int]]:
    return [[int(s) for s in col] for col in json.loads(Path(path).read_text(encoding="utf-8"))[set_name]["columns"]]


def idle(now: float) -> dict:
    """시험이 끝났다 — 결과판 · 클라우드가 '시험 없음'으로 바꾼다 (7-5 의 3)."""
    return {"source": SOURCE, "t": round(now, 3), "step": {"key": "idle", "index": None}}


class QcLive:
    """feed(데이터그램, 받은 시각) 로 셀별 최신 값을 모으고, frame(지금) 이 qc_now 한 장이다. 장비 · 파일에 닿지 않는다."""

    def __init__(self, columns: list[list[int]], started: float, site: str = "lab", repeater: str = "on", set_name: str = "Y",
                 part: str = "A"):
        if part not in ("A", "C"):
            raise ValueError(f"part {part!r} — 이 최소판은 A(셀 정지) · C(레일만 움직임, 하늘 정지)만 쓴다")
        self.columns, self.started, self.part = columns, started, part
        self.serials = [s for col in columns for s in col]
        self.dock = {s: {"col": c + 1, "row": r + 1} for c, col in enumerate(columns) for r, s in enumerate(col)}
        self.grid = [[col[r] for col in columns] for r in range(len(columns[0]))]
        self.last: dict[int, dict] = {}
        self.times: dict[int, deque] = {s: deque(maxlen=64) for s in self.serials}
        self.fix_t: dict[int, float] = {}
        self._lock = threading.Lock()              # feed 는 수신 스레드, frame 은 쓰기 스레드
        self.cond = {"protocol": "v4", "part": part, "site": site, "sky": "미확인", "repeater": repeater, "corrections": "미확인",
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

    def frame(self, now: float, motor: dict | None = None) -> dict:
        """motor 는 motor_frame() 의 결과. 없으면 '멈춤'."""
        motor = motor or motor_frame(None, now)
        step = {"key": f"{self.part}0", "index": 0, "started": round(self.started, 3), "ends": None,
                "v_mm_s": motor["v_mm_s"], "a_mm_s2": None if self.part == "C" else 0.0,
                "scene_now": None, "band_kmh": None, "scene_step": "still"}
        with self._lock:
            cells = {str(s): self._cell(s, now) for s in self.serials}
        return {"source": SOURCE, "t": round(now, 3), "stamp": time.strftime("%Y%m%d-%H%M%S", time.localtime(self.started)),
                "conditions": self.cond, "part": self.part, "step": step, "motor": motor,
                "cells": cells, "grid": self.grid}
