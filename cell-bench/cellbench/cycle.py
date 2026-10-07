"""사이클 상태기계 — 방전 → 기준선 → 플러그 ON → 추출 → 충전 → 만충 → 플러그 OFF → (반복)

판정 함수(is_full, integrate_wh, discharge_done)는 순수 함수라 장비 없이 단위 검사한다.
CycleRunner 는 그 판정에 따라 플러그와 셀을 움직이고 Recorder 에 남긴다.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from .cells import CellLink, LiveListener
from .config import Config
from .plug import Plug
from .record import Recorder

PHASES = ["DISCHARGE", "THRESHOLD", "PLUG_ON", "EXTRACT", "CHARGE", "FULL", "PLUG_OFF"]


# ---------- 순수 판정 ----------

def discharge_done(batteries: list[int], stop_pct: int) -> bool:
    """24셀 중 하나라도 기준선 이하면 방전 끝. 셀이 하나도 안 들리면 False (판단 보류)."""
    return bool(batteries) and min(batteries) <= stop_pct


def integrate_wh(prev_w: float, cur_w: float, dt_s: float) -> float:
    """사다리꼴 적분. 어느 한쪽이 NaN 이면 0 (표본 하나 놓친 것으로 본다)."""
    if math.isnan(prev_w) or math.isnan(cur_w) or dt_s <= 0:
        return 0.0
    return (prev_w + cur_w) / 2 * dt_s / 3600


def is_full(samples: list[tuple[float, float]], batteries: list[int], now: float,
            flat_min: float, tol_w: float, require_all_100: bool, expected_cells: int) -> bool:
    """만충 = 최근 flat_min 분 동안 전력 변동이 tol_w 안 + (옵션) 모든 셀 100%.

    samples: (시각, W) 목록. 평평함을 보려면 그 구간 안에 표본이 3개 이상 있어야 한다.
    """
    if require_all_100:
        if len(batteries) < expected_cells or any(b < 100 for b in batteries):
            return False
    window = [w for t, w in samples if now - t <= flat_min * 60 and not math.isnan(w)]
    if len(window) < 3:
        return False
    if samples and now - samples[0][0] < flat_min * 60:
        return False                       # 아직 flat_min 분이 지나지 않았다
    return (max(window) - min(window)) <= tol_w


# ---------- 실행기 ----------

@dataclass
class CycleState:
    cycle: int
    phase: str = "DISCHARGE"
    t_start: float = field(default_factory=time.time)
    discharge_start: float | None = None
    discharge_end: float | None = None
    min_batt_at_stop: int | None = None
    plug_on_at: float | None = None
    extract_start: float | None = None
    extract_end: float | None = None
    extract_ok: int = 0
    extract_mb: float = 0.0
    full_at: float | None = None
    charge_wh: float = 0.0
    floor_w: float | None = None
    plug_off_at: float | None = None
    events: int = 0
    note: str = ""


class CycleRunner:
    def __init__(self, cfg: Config, live: LiveListener, plug: Plug, link: CellLink, rec: Recorder,
                 dry_run: bool = False):
        self.cfg, self.live, self.plug, self.link, self.rec = cfg, live, plug, link, rec
        self.dry = dry_run
        self._last_seen: dict[int, float] = {}

    # --- 공통 도우미 ---
    def _batts(self) -> list[int]:
        snap = self.live.snapshot()
        now = time.time()
        return [c.battery for s, c in snap.items() if s in self.cfg.serials and now - c.t <= 5 and not c.waiting]

    def _hr_cells(self) -> int:
        return sum(1 for s, c in self.live.snapshot().items() if s in self.cfg.serials and c.hr > 0)

    def _check_gaps(self, st: CycleState) -> None:
        """라이브 신호가 끊긴 셀을 이상으로 남긴다 (끊김당 1회)."""
        snap = self.live.snapshot(); now = time.time()
        for s in self.cfg.serials:
            c = snap.get(s)
            gap = now - c.t if c else float("inf")
            was = self._last_seen.get(s, 0)
            if gap > self.cfg.live_gap_alarm_s and was != -1:
                self.rec.event(st.cycle, st.phase, "live_gap", s, f"{gap:.0f}초 이상 신호 없음" if c else "한 번도 안 들림")
                st.events += 1
                self._last_seen[s] = -1          # 복구될 때까지 다시 알리지 않는다
            elif gap <= self.cfg.live_gap_alarm_s:
                self._last_seen[s] = now

    def _plug(self, action: str, st: CycleState):
        if self.dry:
            self.rec.log(f"(모의) 플러그 {action}")
            return None
        try:
            return getattr(self.plug, action)()
        except RuntimeError as e:
            self.rec.event(st.cycle, st.phase, "plug", "-", str(e)); st.events += 1
            return None

    def _poll(self, st: CycleState, wh: float, prev: tuple[float, float] | None) -> tuple[float, tuple[float, float] | None, float]:
        """표본 1회: 플러그 읽기 → Wh 누적 → 기록. (wh, (t, W), W) 를 돌려준다."""
        r = self._plug("read", st)
        w = r.watts if r else float("nan"); on = r.on if r else None; now = time.time()
        if prev is not None:
            wh += integrate_wh(prev[1], w, now - prev[0])
        self.rec.sample(st.phase, on, w, wh, self._batts(), self._hr_cells())
        self._check_gaps(st)
        return wh, (now, w), w

    # --- 단계 ---
    def run_cycle(self) -> CycleState:
        cfg = self.cfg
        st = CycleState(cycle=self.rec.next_cycle_no())
        self.rec.begin_cycle(st.cycle)
        self.rec.log(f"=== 사이클 {st.cycle} 시작 · 기준선 {cfg.discharge_stop_pct}% · 삭제 {'켬' if cfg.delete_after_extract else '끔'}")
        missing = self.live.wait_for(cfg.serials, 10)
        if missing:
            self.rec.event(st.cycle, st.phase, "missing_cells", "-", f"{len(missing)}대 신호 없음: {missing}"); st.events += 1

        # 1) 방전: 플러그 OFF, 기준선까지 대기
        st.phase = "DISCHARGE"
        self._plug("off", st); st.discharge_start = time.time()
        self.rec.log(f"방전 시작 · 배터리 최저 {min(self._batts() or [0])}%")
        wh, prev = 0.0, None
        while True:
            b = self._batts()
            if discharge_done(b, cfg.discharge_stop_pct):
                st.min_batt_at_stop = min(b); break
            wh, prev, _ = self._poll(st, wh, prev)
            time.sleep(cfg.poll_s)
        st.discharge_end = time.time(); st.phase = "THRESHOLD"
        self.rec.log(f"기준선 도달 · 최저 {st.min_batt_at_stop}% · 방전 {(st.discharge_end - st.discharge_start)/3600:.2f}h")

        # 2) 플러그 ON → 충전 시작 확인
        st.phase = "PLUG_ON"
        r = self._plug("on", st); st.plug_on_at = time.time()
        time.sleep(5)
        r = self._plug("read", st)
        if r and r.watts < cfg.plug_on_min_w:
            self.rec.event(st.cycle, st.phase, "plug", "-", f"켠 뒤 전력 {r.watts:.1f} W < {cfg.plug_on_min_w} W — 충전이 시작되지 않음"); st.events += 1
        self.rec.log(f"플러그 ON · {r.watts:.1f} W" if r else "플러그 ON (전력 미확인)")

        # 3) 추출 (충전 중)
        st.phase = "EXTRACT"; st.extract_start = time.time()
        wh, prev = 0.0, None
        if self.dry:
            self.rec.log("(모의) 추출 생략"); results = {}
        else:
            results = self.link.extract(cfg.serials, Path(cfg.data_dir) / "ftg" / f"{st.cycle:04d}")
            self.rec.cells(st.cycle, results)
            for s, res in results.items():
                if res.error or (res.size and not res.ended) or res.bad_blocks:
                    self.rec.event(st.cycle, st.phase, "extract", s, res.error or f"미완료/오류블록 {res.bad_blocks}"); st.events += 1
                if res.resume_s is None and res.error != "라이브 신호 없음":
                    self.rec.event(st.cycle, st.phase, "no_resume", s, f"{cfg.resume_timeout_s:.0f}초 안에 측정 미복귀"); st.events += 1
            st.extract_ok = sum(1 for r_ in results.values() if r_.ended and not r_.bad_blocks)
            st.extract_mb = sum(r_.got for r_ in results.values()) / 1048576
        st.extract_end = time.time()
        self.rec.log(f"추출 끝 · {st.extract_ok}/{len(cfg.serials)}대 · {st.extract_mb:.0f} MB · {st.extract_end - st.extract_start:.0f}초")

        # 4) 충전 → 만충
        st.phase = "CHARGE"; samples: list[tuple[float, float]] = []
        while True:
            wh, prev, w = self._poll(st, wh, prev)
            if prev: samples.append(prev)
            now = time.time()
            if is_full(samples, self._batts(), now, cfg.full_flat_min, cfg.full_flat_tol_w,
                       cfg.full_requires_all_100, len(cfg.serials)):
                st.full_at = now; break
            if now - st.plug_on_at > cfg.charge_timeout_h * 3600:
                self.rec.event(st.cycle, st.phase, "charge_timeout", "-", f"{cfg.charge_timeout_h}h 안에 만충 판정 안 됨"); st.events += 1
                st.full_at = now; st.note = "charge_timeout"; break
            time.sleep(cfg.poll_s)
        st.phase = "FULL"
        recent = [w for t, w in samples if st.full_at - t <= cfg.full_flat_min * 60 and not math.isnan(w)]
        st.floor_w = sum(recent) / len(recent) if recent else None
        # 충전에 들어간 Wh = 전체 적분 − 바닥 전력 × 충전 시간 (셀 작동분 제외)
        charge_h = (st.full_at - st.plug_on_at) / 3600
        st.charge_wh = wh - (st.floor_w or 0) * charge_h
        self.rec.log(f"만충 · 충전 {charge_h*60:.0f}분 · 적분 {wh:.1f} Wh · 바닥 {st.floor_w or float('nan'):.1f} W → 충전분 {st.charge_wh:.1f} Wh")

        # 5) 플러그 OFF
        st.phase = "PLUG_OFF"
        self._plug("off", st); st.plug_off_at = time.time()
        self.rec.cycle({
            "cycle": st.cycle, "start": _ts(st.t_start),
            "discharge_start": _ts(st.discharge_start), "discharge_end": _ts(st.discharge_end),
            "discharge_h": f"{(st.discharge_end - st.discharge_start)/3600:.3f}", "min_batt_at_stop": st.min_batt_at_stop,
            "plug_on": _ts(st.plug_on_at), "extract_start": _ts(st.extract_start), "extract_end": _ts(st.extract_end),
            "extract_s": f"{st.extract_end - st.extract_start:.0f}", "extract_ok": st.extract_ok, "extract_mb": f"{st.extract_mb:.1f}",
            "full_at": _ts(st.full_at), "charge_min": f"{charge_h*60:.0f}", "charge_wh": f"{st.charge_wh:.1f}",
            "floor_w": f"{st.floor_w:.1f}" if st.floor_w is not None else "", "plug_off": _ts(st.plug_off_at),
            "events": st.events, "note": st.note,
        })
        self.rec.log(f"=== 사이클 {st.cycle} 끝 · 이상 {st.events}건")
        return st

    def run(self, cycles: int) -> None:
        for _ in range(cycles):
            self.run_cycle()


def _ts(t: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""
