"""레일 시험 실시간 화면용 데모 생성기 — qc_now.json 을 1초마다 덮어쓴다(프로토콜 v4 모양, 맨 위 칸 source: "demo").

  python board/demo_qc_now.py --out <폴더>                 # 파트 A → C → B 를 짧게(각 1~2분) 되풀이 (기본 --part cycle)
  python board/demo_qc_now.py --out <폴더> --part A        # 파트 하나를 프로토콜 시간대로 되풀이
  python board/demo_qc_now.py --out <폴더> --speed 10      # 시험 시간을 10배로 빨리 감는다(쓰기는 늘 1초마다)
  python serve_board.py --data <폴더>                      # 다른 창 — GET /api/qc_now 가 이 파일을 넘긴다

칸의 정본은 dynamic-bench/design/qc-protocol-v4.md 7-5 의 3 (qc_now.json)과 7-6 (칸 이름 · 단위). 실제로 쓰는 프로그램(모터
세션의 실행 프로그램)이 생기기 전까지 화면을 만들고 확인하는 데만 쓴다. 장비 · 네트워크에 닿지 않는다.

7-6 에 맞춘 것: step.key 는 파트 + 순번("A2" · "C3" · "B1"), step.index 는 그 파트 안 순번(0 부터, 시험 없음이면 null),
파트 B · C 의 started · ends 는 그 속도 · 가속도 단계의 시작 · 끝이다. 파트 A 는 step.band_kmh(등속 구간이면 그 km/h, 정지 ·
가감속 · 회전은 null)와 step.scene_step(장면 파일의 step 글자)을 싣는다 — scene_step 글자 가운데 7-6 이 예로 든 것은
"band_11" · "dwell_11" 뿐이고, 나머지("lock" · "still" · "accel_11" · "decel_11" · "accel" · "turn" · "still2")는 장면 파일
형식이 정해지기 전까지 데모가 임시로 붙인 이름이다. motor.alarms 는 [["YYYY-MM-DD HH:MM:SS", 이유], ...] — 그 파트 실행 안에서
50 ms 를 넘은 주기 지연이 쌓인다(모터 기록 JSON 의 alarms 와 같은 모양). 단위: sog_raw · sog_err · v_mm_s 는 mm/s,
pos_err_m 은 m, clock_dev_ms 는 ms, cn0 는 dBHz.

실제 시험 파일과 섞이지 않게 두 가지를 막는다 — --out 이 cell-bench/data(또는 그 아래)면 거부하고, 그 폴더에 이미 있는
qc_now.json 이 데모가 쓴 것(source: "demo")이 아니면 덮어쓰지 않는다.

흉내 내는 것: 셀 10696 은 50초마다 15초 수신이 끊기고(age_s 가 자라고 packets_10s 가 준다) 낮은 가속도(≤ 50 mm/s²)에서
움직임 판단이 켜지지 않는다, 11620 은 고정이 늦고(A0 를 넘겨 A1 에서 fix), 10681 은 무리에서 벗어난다(C/N0 가 낮고
sog_err · pos_err_m 이 크다). 모터는 70초마다 한 번 주기 지연이 30 ms 를 넘고(cycle_ms_max, 최근 10초 최대), 그 세 번에
한 번은 50 ms 를 넘어 motor.alarms 에 남고 10초 동안 state 가 alarm 이 된다. 값의 크기는 그럴듯하게만 맞췄고 실측이 아니다.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cellbench.control import read_json, write_json_atomic  # noqa: E402

QC_FILE = "qc_now.json"
# 독 배치 사본 — cell-bench/motionbench/dock_layout.json 의 Y 세트(2026-10-29). 열마다 위에서부터 6행
DOCK_Y = [[10677, 11619, 10679, 10680, 10681, 10682],
          [10683, 10684, 11620, 10686, 10687, 10688],
          [10689, 10690, 10691, 10692, 10693, 10694],
          [10695, 10696, 10697, 10698, 10699, 10700]]
DROP, LATE, ODD = 10696, 11620, 10681          # 수신 끊김(+ 낮은 가속도에서 움직임 판단 안 켜짐) · 고정 늦음 · 무리에서 벗어남
ORIGIN = (37.5, 127.0, 50.0)                   # 데모 장면 원점(위도, 경도, 높이 m) — 실제 장소가 아니다
RAIL_LEFT, FERR_LIMIT_UM, IDLE_S = 150.0, 2000.0, 5.0
ALARM_MS = 50.0                                # 주기 지연이 이것을 넘으면 motor.alarms 에 남긴다(7-6 의 3 예: "… 60.9 ms > 50 ms")
BANDS_KMH = (5, 11, 18, 23, 28)                # 파트 A2 — FIFA 속도 구간마다 한 값 (v4 3절)
ACCELS = (20, 50, 100, 200, 500)               # 파트 C 가속도 계단 mm/s², 속도 100 mm/s
SPEEDS = (100, 300, 500)                       # 파트 B 속도 계단 mm/s, 가속도 500 mm/s²


# ---- 모터: 사다리꼴 이동 ----------------------------------------------------------------------------------------------
def trap(dist: float, v: float, a: float) -> tuple[float, float, float]:
    """거리 dist(mm)를 최고 속도 v · 가속도 a 로 가는 사다리꼴 — (가속 시간, 최고 속도, 전체 시간). 짧으면 삼각형."""
    ta = v / a
    if a * ta * ta > dist:
        v = math.sqrt(a * dist)
        ta = v / a
    return ta, v, 2 * ta + (dist - v * ta) / v


def trap_at(t: float, dist: float, v: float, a: float) -> tuple[float, float]:
    """출발 뒤 t 초의 (간 거리, 속도)."""
    ta, vp, total = trap(dist, v, a)
    t = min(max(t, 0.0), total)
    if t < ta:
        return 0.5 * a * t * t, a * t
    if t > total - ta:
        r = total - t
        return dist - 0.5 * a * r * r, a * r
    return 0.5 * a * ta * ta + vp * (t - ta), vp


def motor_plan(stages, x0: float, x1: float, legs: int, pause: float, dwell: float):
    """단계마다 x0 ↔ x1 왕복 — ([(시작, 끝, 단계 번호, 움직임)], 전체 시간). 움직임은 ('move', 출발, 도착, v, a) · ('hold', 자리).
    한 번 가기 사이에 pause 초 서고(움직임 판단이 꺼지는 것이 보이게), 단계 끝에 dwell 초 선다."""
    plan, t = [], 0.0
    for i, (v, a) in enumerate(stages):
        for leg in range(legs):
            s, e = (x0, x1) if leg % 2 == 0 else (x1, x0)
            dur = trap(abs(e - s), v, a)[2]
            plan.append((t, t + dur, i, ("move", s, e, v, a)))
            t += dur
            gap = dwell if leg == legs - 1 else pause
            plan.append((t, t + gap, i, ("hold", e)))
            t += gap
    return plan, t


def motor_at(plan, t: float) -> tuple[float, float, int]:
    """단계 안 t 초의 (위치 mm, 속도 mm/s 부호 있음, 단계 번호)."""
    seg = plan[-1]
    for seg in plan:
        if t < seg[1]:
            break
    t0, _, i, mv = seg
    if mv[0] == "hold":
        return mv[1], 0.0, i
    _, s, e, v, a = mv
    d, sp = trap_at(t - t0, abs(e - s), v, a)
    sign = 1.0 if e > s else -1.0
    return s + sign * d, sign * sp, i


# ---- 장면(파트 A): 속도 · 방향이 선형으로 바뀌는 조각들 -----------------------------------------------------------------
def _advance(x: float, dur: float, v0, v1, h0, h1, k: int = 20) -> tuple[float, float]:
    """조각 시작부터 x 초 동안 간 거리(동, 북 m) — k 조각 중점 적분."""
    de = dn = 0.0
    for j in range(k):
        f = (j + 0.5) / k * x / dur
        v = (v0 + (v1 - v0) * f) / 3.6
        h = math.radians(h0 + (h1 - h0) * f)
        de += v * math.sin(h) * x / k
        dn += v * math.cos(h) * x / k
    return de, dn


def scene_plan(segs):
    """[(초, 시작 km/h, 끝 km/h, 시작 방향°, 끝 방향°, scene_step 글자)] → ([(시작, 길이, …, 시작 동, 시작 북, 글자)], 전체 시간)."""
    plan, t, e, n = [], 0.0, 0.0, 0.0
    for dur, v0, v1, h0, h1, label in segs:
        plan.append((t, dur, v0, v1, h0, h1, e, n, label))
        de, dn = _advance(dur, dur, v0, v1, h0, h1)
        e, n, t = e + de, n + dn, t + dur
    return plan, t


def scene_seg(plan, t: float):
    """단계 안 t 초가 든 장면 조각."""
    seg = plan[-1]
    for seg in plan:
        if t < seg[0] + seg[1]:
            break
    return seg


def scene_label(plan, t: float) -> tuple[float | None, str]:
    """(step.band_kmh, step.scene_step) — 7-6 의 2. band_kmh 는 A2 의 등속 구간(글자 band_<km/h>)에서만 숫자다."""
    seg = scene_seg(plan, t)
    return (float(seg[2]) if seg[8].startswith("band_") else None), seg[8]


def scene_at(plan, t: float) -> tuple[dict, bool]:
    """단계 안 t 초의 장면 정답(scene_now)과, 등속 · 정지 조각인가(pos_err_m 을 내는 구간)."""
    t0, dur, v0, v1, h0, h1, e, n, _ = scene_seg(plan, t)
    x = min(max(t - t0, 0.0), dur)
    de, dn = _advance(x, dur, v0, v1, h0, h1)
    f = x / dur
    lat = ORIGIN[0] + (n + dn) / 111320.0
    lon = ORIGIN[1] + (e + de) / (111320.0 * math.cos(math.radians(ORIGIN[0])))
    return ({"v_kmh": round(v0 + (v1 - v0) * f, 2), "heading_deg": round((h0 + (h1 - h0) * f) % 360, 1),
             "lat": round(lat, 8), "lon": round(lon, 8)}, v0 == v1 and h0 == h1)


# ---- 파트마다 단계 ------------------------------------------------------------------------------------------------------
def part_steps(part: str, short: bool) -> list[dict]:
    """파트 하나의 단계들 — [{key, index, dur, scene | motor+stages}]. short 면 파트 하나가 1~2분이 되게 줄인다."""
    if part == "A":
        legs, const, stop, reps = (2, 2.0, 1.0, 1) if short else (6, 20.0, 5.0, 5)
        a2 = []
        for v in BANDS_KMH:                    # 가속 2초 + 등속 + 감속 2초, 왕복, 끝 멈춤
            for leg in range(legs):
                h = 90 if leg % 2 == 0 else 270
                a2 += [(2.0, 0, v, h, h, f"accel_{v}"), (const, v, v, h, h, f"band_{v}"), (2.0, v, 0, h, h, f"decel_{v}")]
            a2.append((stop, 0, 0, 90, 90, f"dwell_{v}"))
        a3 = [(2.0, 0, 25, 90, 90, "accel"), (5.0, 25, 25, 90, 90, "accel"), (2.0, 25, 0, 90, 90, "accel"),
              (5.0, 0, 0, 90, 90, "accel")] * reps
        a4 = [(4.0, 15, 15, 90, 90, "turn"), (1.5, 15, 15, 90, 270, "turn"), (4.0, 15, 15, 270, 270, "turn"),
              (1.5, 15, 15, 270, 450, "turn")] * (1 if short else 10)

        def still(d, label):
            return [(d, 0, 0, 90, 90, label)]
        segs = [("A0", still(12.0 if short else 60.0, "lock")), ("A1", still(15.0 if short else 300.0, "still")),
                ("A2", a2), ("A3", a3), ("A4", a4), ("A5", still(8.0 if short else 60.0, "still2"))]
        out = []
        for i, (key, sg) in enumerate(segs):
            plan, dur = scene_plan(sg)
            out.append({"key": key, "index": i, "dur": dur, "scene": plan})
        return out
    if part == "C":
        stages, x1 = [(100.0, float(a)) for a in ACCELS], 750.0
    else:
        stages, x1 = [(float(v), 500.0) for v in SPEEDS], 1650.0
    plan, dur = motor_plan(stages, RAIL_LEFT, x1, legs=2 if short else 6, pause=1.0, dwell=2.0 if short else 5.0)
    spans = [(min(p[0] for p in plan if p[2] == i), max(p[1] for p in plan if p[2] == i)) for i in range(len(stages))]
    # 모터 프로그램 한 번이 단계(속도 · 가속도) 여럿을 돈다 — step.key 는 frame 이 지금 단계 번호로 "C3" · "B1" 꼴로 만든다
    return [{"key": part, "index": 0, "dur": dur, "motor": plan, "stages": stages, "spans": spans}]


def conditions(part: str, still_s: float, dwell_s: float) -> dict:
    """시험 조건 — v4 6-3 · 7-5 의 2 고정 이름. 모르는 값은 '미확인'."""
    scene = {"A": ("demo-5band", "file"), "B": ("demo-rail-follow", "realtime"), "C": ("demo-static", "file")}[part]
    return {"protocol": "v4", "sky": "simulator", "part": part, "site": "factory", "repeater": "off", "corrections": "off",
            "set": "Y", "cell_type": "Y", "fw": "0.99.71", "dock": "DKPY-1C",
            "scene": {"name": scene[0], "mode": scene[1], "file": "미확인", "t0_gps": "미확인",
                      "origin": {"lat": ORIGIN[0], "lon": ORIGIN[1], "h": ORIGIN[2]}},
            "simulator": {"model": "미확인", "serial": "미확인", "firmware": "미확인", "constellations": "미확인"},
            "rf": {"method": "radiated", "level_dbm": "미확인", "antenna": "미확인", "shield": "미확인"},
            "speeds_mm_s": list(SPEEDS) if part == "B" else [], "accels_mm_s2": list(ACCELS) if part == "C" else [],
            "still_s": still_s, "dwell_s": dwell_s, "judge_commit": "demo"}


def spike(k: int) -> tuple[float, float]:
    """k 번째 70초 창의 주기 지연 — (시험 시각, ms). 세 번에 한 번은 ALARM_MS 를 넘는다."""
    at = k * 70 + 20 + (k * 37) % 30
    return at, (ALARM_MS + 1.4 + (k * 5) % 9 if k % 3 == 2 else 30.0 + (k * 7) % 13)


def cycle_spike(ts: float) -> float:
    """주기 지연 흉내 — 70초마다 한 번 끼고, cycle_ms_max 는 최근 10초 최대라 10초 동안 보인다."""
    for k in (int(ts // 70), int(ts // 70) - 1):
        at, ms = spike(k)
        if 0 <= ts - at < 10:
            return ms
    return 0.0


def alarm_log(t_from: float, t_to: float, wall) -> list[list[str]]:
    """t_from ~ t_to(시험 시각) 사이에 ALARM_MS 를 넘은 주기 지연 — [["YYYY-MM-DD HH:MM:SS", 이유], ...] (7-6 의 3).
    wall 은 시험 시각을 벽시계 epoch 초로 바꾼다."""
    out = []
    for k in range(int(t_from // 70) - 1, int(t_to // 70) + 1):
        at, ms = spike(k)
        if ms > ALARM_MS and t_from <= at <= t_to:
            out.append([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(wall(at))), f"주기 지연 {ms:.1f} ms > {ALARM_MS:.0f} ms"])
    return out


class Demo:
    """part: A · B · C · cycle. w0 = 시작 벽시계(epoch 초), speed = 시험 시간 배속. frame(now) 가 그 순간의 qc_now 한 장이다
    (단계 · 모터 · 장면은 시각의 순수 함수, 잡음은 (셀, 초)로 씨앗을 정해 같은 초면 같은 값)."""

    def __init__(self, part: str, columns=DOCK_Y, w0: float | None = None, speed: float = 1.0):
        self.w0, self.speed = (time.time() if w0 is None else w0), speed
        self.serials = [s for col in columns for s in col]
        self.grid = [[col[r] for col in columns] for r in range(len(columns[0]))]
        short = part == "cycle"
        self.blocks, t = [], 0.0
        lock_t0, a0 = -600.0, 60.0                         # 파트 A 가 없으면 셀은 10분 전부터 고정해 있다
        for p in (("A", "C", "B") if short else (part,)):
            steps = part_steps(p, short)
            cond = conditions(p, 15.0 if short else 300.0, 2.0 if short else 5.0)
            if p == "A":
                lock_t0, a0 = t, steps[0]["dur"]           # 장면 재생이 A0 에서 시작한다 — 셀은 여기서부터 고정을 잡는다
            run_t0 = t
            for s in steps:
                self.blocks.append({**s, "part": p, "t0": t, "t1": t + s["dur"], "run_t0": run_t0, "cond": cond})
                t += s["dur"]
        self.blocks.append({"key": "idle", "t0": t, "t1": t + IDLE_S})   # 회차 사이 '시험 없음'
        self.period, self.lock_t0 = t + IDLE_S, lock_t0
        self.cell = {}
        for s in self.serials:
            r = random.Random(f"cell:{s}")
            self.cell[s] = {"fix": (1.6 if s == LATE else r.uniform(0.1, 0.6)) * a0, "cn0": 32.0 if s == ODD else r.uniform(38, 42),
                            "sats": r.randint(9, 11), "clk": 21.0 if s == LATE else r.gauss(0, 3), "bat": r.uniform(80, 100),
                            "ratio": 0.9 if s == ODD else r.uniform(0.98, 1.02), "pos": 3.5 if s == ODD else r.uniform(0.6, 1.5),
                            "lag": r.uniform(0.1, 0.25), "on": r.uniform(0.1, 0.3), "off": r.uniform(0.3, 0.6)}

    def frame(self, now: float) -> dict:
        ts = (now - self.w0) * self.speed                  # 시작 뒤 시험 시간(초)
        k, tl = divmod(ts, self.period)
        b = next((b for b in self.blocks if tl < b["t1"]), self.blocks[-1])
        if b["key"] == "idle":
            return {"source": "demo", "t": round(now, 3), "step": {"key": "idle", "index": None}}
        it0 = k * self.period                              # 이 회차가 시작한 시험 시간

        def wall(x):
            return round(self.w0 + (it0 + x) / self.speed, 3)
        x = tl - b["t0"]
        step = {"key": b["key"], "index": b["index"], "started": wall(b["t0"]), "ends": wall(b["t1"]),
                "v_mm_s": None, "a_mm_s2": None}
        if b.get("motor"):                                 # 지금 속도 · 가속도 단계 — key 는 파트 + 순번, 시작 · 끝은 그 단계의 것
            i = motor_at(b["motor"], x)[2]
            s0, s1 = b["spans"][i]
            step.update({"key": f"{b['part']}{i}", "index": i, "started": wall(b["t0"] + s0), "ends": wall(b["t0"] + s1)})
            step["v_mm_s"], step["a_mm_s2"] = b["stages"][i]
        if b.get("scene"):
            step["scene_now"] = scene_at(b["scene"], x)[0]
            step["band_kmh"], step["scene_step"] = scene_label(b["scene"], x)
        alarms = (alarm_log(it0 + b["run_t0"], ts, lambda a: self.w0 + a / self.speed)   # 이 파트 실행 안에서 쌓인 경고
                  if b.get("motor") else [])
        return {"source": "demo", "t": round(now, 3), "stamp": time.strftime("%Y%m%d-%H%M%S", time.localtime(wall(b["run_t0"]))),
                "conditions": b["cond"], "part": b["part"], "step": step, "motor": self._motor(b, x, ts, alarms),
                "cells": {str(s): self._cell(s, b, x, ts, it0 + self.lock_t0) for s in self.serials}, "grid": self.grid}

    def _motor(self, b, x: float, ts: float, alarms: list) -> dict:
        if not b.get("motor"):                             # 파트 A — 모터 멈춤, 모터 프로그램이 돌지 않는다
            return {"pos_mm": RAIL_LEFT, "v_mm_s": 0.0, "ferr_max_um": 0.0, "ferr_limit_um": FERR_LIMIT_UM, "alarms": [],
                    "cycle_ms_max": None, "state": "stopped"}
        pos, v, i = motor_at(b["motor"], x)
        sv, sa = b["stages"][i]
        r = random.Random(f"motor:{int(ts)}")
        cyc = cycle_spike(ts)
        return {"pos_mm": round(pos, 1), "v_mm_s": round(v, 1), "ferr_max_um": round(15 + 0.35 * sa + 0.08 * sv + r.uniform(0, 10), 1),
                "ferr_limit_um": FERR_LIMIT_UM, "alarms": alarms, "cycle_ms_max": round(max(2.0 + r.uniform(0, 1.5), cyc), 1),
                "state": "alarm" if cyc > ALARM_MS else "ok"}

    def _cell(self, s: int, b, x: float, ts: float, lock_ref: float) -> dict:
        c, r = self.cell[s], random.Random(f"{s}:{int(ts)}")

        def rx(t):                                         # 10696 은 50초마다 15초 끊긴다
            return not (s == DROP and 30 <= t % 50 < 45)
        last = ts - r.uniform(0.05, 0.5) if rx(ts) else ts - (ts % 50 - 30)
        packets = max(0, 2 * sum(rx(ts - i) for i in range(10)) - (1 if r.random() < 0.1 else 0))
        xe = max(0.0, x - (ts - last))                     # 마지막 패킷 때의 단계 안 시각 — 끊기면 값이 그때에 멈춘다
        fix_t = lock_ref + c["fix"]
        fixed = last >= fix_t
        sog = err = pos = None
        moving = False
        if fixed and b.get("scene"):                       # 파트 A — 셀 정지, 원래 속도만 장면을 따른다
            seen, _ = scene_at(b["scene"], max(0.0, xe - c["lag"]))
            truth, steady = scene_at(b["scene"], xe)
            sog = max(0.0, seen["v_kmh"] / 3.6 * 1000 * c["ratio"] + r.gauss(0, 25))
            err = sog - truth["v_kmh"] / 3.6 * 1000
            pos = abs(r.gauss(c["pos"], 0.3)) if steady else None
        if b.get("motor"):
            a = b["stages"][motor_at(b["motor"], xe)[2]][1]
            if fixed:                                      # 파트 C 는 정지 장면 — 잡음만, 파트 B 는 레일과 같은 속도
                sog = abs(r.gauss(0, 12)) if b["part"] == "C" else max(
                    0.0, abs(motor_at(b["motor"], max(0.0, xe - c["lag"]))[1]) * c["ratio"] + r.gauss(0, 15))
            on = c["on"] + (1.0 if a <= 20 else 0.0)       # 낮은 가속도는 늦게 켜진다
            off = max(c["off"], on + 0.2)
            moving = not (s == DROP and a <= 50) and any(
                xe - d >= 0 and abs(motor_at(b["motor"], xe - d)[1]) > 0.5 for d in (on, (on + off) / 2, off))
        return {"age_s": round(ts - last, 1), "packets_10s": packets, "fix": "A" if fixed else "N",
                "cn0": int(round(c["cn0"] + r.gauss(0, 0.7))) if fixed else int(round(29 + r.gauss(0, 1.5))),
                "sats": c["sats"] + r.randint(-1, 1) if fixed else r.randint(0, 3),
                "sog_raw": None if sog is None else round(sog, 1), "sog_err": None if err is None else round(err, 1),
                "pos_err_m": None if pos is None else round(pos, 2), "moving": moving,
                "clock_dev_ms": round(c["clk"] + r.gauss(0, 1.0), 1), "battery": max(5, int(c["bat"] - ts / 120)),
                "fix_age_s": round(last - fix_t, 1) if fixed else None}


# ---- 쓰기 ---------------------------------------------------------------------------------------------------------------
def refuse(out: Path) -> str | None:
    """쓰면 안 되는 폴더면 그 이유. cell-bench/data(또는 그 아래 set2 등)와, 데모가 쓰지 않은 qc_now.json 이 있는 폴더."""
    p = Path(out).resolve()
    for q in (p, *p.parents):
        if q.name.lower() == "data" and q.parent.name.lower() == "cell-bench":
            return f"{out} 은 실제 데이터 폴더(cell-bench/data)다 — 데모는 다른 폴더에 쓴다"
    f = p / QC_FILE
    if f.exists():
        old = read_json(f)
        if not isinstance(old, dict) or old.get("source") != "demo":
            return f"{f} 가 이미 있고 데모가 쓴 것이 아니다 — 실제 시험 파일일 수 있어 덮어쓰지 않는다"
    return None


def load_layout(path: str | None) -> list[list[int]]:
    """독 배치 — 파일을 주면 그 파일의 Y 세트 columns, 아니면 이 파일 안의 사본."""
    if not path:
        return DOCK_Y
    return json.loads(Path(path).read_text(encoding="utf-8"))["Y"]["columns"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="레일 시험 실시간 화면용 데모 qc_now.json 생성기 — 장비 · 네트워크에 닿지 않는다")
    ap.add_argument("--out", required=True, help="qc_now.json 을 쓸 폴더. 실제 데이터 폴더(cell-bench/data)는 거부한다")
    ap.add_argument("--part", choices=("A", "B", "C", "cycle"), default="cycle",
                    help="A · B · C 는 그 파트를 프로토콜 시간대로, cycle(기본)은 A → C → B 를 짧게 되풀이")
    ap.add_argument("--speed", type=float, default=1.0, help="시험 시간 배속(기본 1 = 실제 시간). 쓰기는 늘 1초마다")
    ap.add_argument("--layout", help="독 배치 파일(dock_layout.json 모양). 없으면 이 파일 안의 Y 세트 사본")
    ap.add_argument("--once", action="store_true", help="한 장만 쓰고 끝낸다")
    ap.add_argument("--seconds", type=float, help="이만큼(실제 초) 쓴 뒤 끝낸다")
    a = ap.parse_args(argv)
    if a.speed <= 0:
        ap.error("--speed 는 0 보다 커야 한다")
    why = refuse(Path(a.out))
    if why:
        print(f"거부 — {why}", file=sys.stderr)
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    w0 = time.time()
    demo = Demo(a.part, load_layout(a.layout), w0, a.speed)
    n = 0
    try:
        while True:
            now = time.time()
            if not write_json_atomic(out / QC_FILE, demo.frame(now)):
                print("qc_now.json 을 쓰지 못함 — 다음 초에 다시", file=sys.stderr)
            n += 1
            if a.once or (a.seconds is not None and now - w0 >= a.seconds):
                return 0
            time.sleep(max(0.0, w0 + n - time.time()))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
