"""사이클 상태기계 — 방전 → 기준선 → 플러그 ON → 추출 → 충전 → 만충 → 플러그 OFF → (반복)

판정 함수(is_full, integrate_wh, discharge_done)는 순수 함수라 장비 없이 단위 검사한다.
CycleRunner 는 그 판정에 따라 플러그와 셀을 움직이고 Recorder 에 남긴다.
"""
from __future__ import annotations

import math
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import net
from .cells import CellLink, LiveListener
from .control import ControlInbox, StopRequested
from .config import Config
from .plug import Plug
from .record import Recorder

class CycleAborted(Exception):
    """사이클을 더 진행할 수 없다 (셀이 오래 안 보임 등). 감독 루프가 받아 안전 상태로 둔다."""


PHASES = ["PRECHARGE", "DISCHARGE", "THRESHOLD", "PLUG_ON", "EXTRACT", "CHARGE", "FULL", "PLUG_OFF"]


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
    phase_since: float = field(default_factory=time.time)
    last_on: bool | None = None
    last_w: float | None = None
    discharge_start_batt: dict = field(default_factory=dict)


class CycleRunner:
    def __init__(self, cfg: Config, live: LiveListener, plug: Plug, link: CellLink, rec: Recorder,
                 dry_run: bool = False):
        self.cfg, self.live, self.plug, self.link, self.rec = cfg, live, plug, link, rec
        self.dry = dry_run
        self._last_seen: dict[int, float] = {}
        self._blind_since: float | None = None      # 셀이 하나도 안 들리기 시작한 시각
        self._last_reconnect = 0.0
        self.current: CycleState | None = None
        self.ctrl = ControlInbox(cfg.data_dir)
        self._force: str | None = None               # 원격 명령: "charge" | "discharge"

    # --- 공통 도우미 ---
    def _per_cell_batt(self) -> dict[int, int]:
        snap = self.live.snapshot(); now = time.time()
        return {s: c.battery for s, c in snap.items() if s in self.cfg.serials and now - c.t <= 5 and not c.waiting}

    def _publish(self, st: CycleState) -> None:
        """결과판이 읽는 data/now.json 을 갱신한다. 실패해도 시험은 계속한다."""
        try:
            self._publish_inner(st)
        except Exception as e:                       # 화면용 파일 때문에 시험이 멈추면 안 된다
            self.rec.log(f"now.json 갱신 건너뜀: {e}")

    def _publish_inner(self, st: CycleState) -> None:
        snap = self.live.snapshot(); now = time.time()
        w = st.last_w
        self.rec.now({
            "t": now, "cycle": st.cycle, "phase": st.phase, "phase_since": st.phase_since,
            "cycle_start": st.t_start, "discharge_start": st.discharge_start, "plug_on_at": st.plug_on_at,
            "stop_pct": self.cfg.discharge_stop_pct, "expected": len(self.cfg.serials), "events": st.events,
            "full_flat_min": self.cfg.full_flat_min, "dry_run": self.dry,
            "plug": {"on": st.last_on, "w": None if w is None or w != w else round(w, 2)},
            "cells": [{"serial": s, "ip": c.ip, "battery": c.battery, "hr": c.hr, "rssi": c.rssi, "state": c.state,
                       "age": round(now - c.t, 1), "waiting": c.waiting}
                      for s, c in sorted(snap.items()) if s in self.cfg.serials],
            "discharge_start_batt": {str(k): v for k, v in st.discharge_start_batt.items()},
        })

    def _phase(self, st: CycleState, name: str) -> None:
        st.phase = name; st.phase_since = time.time()
        self._publish(st)

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
            if not (self._blind_since and action == "read"):   # 셀도 안 보이면 같은 원인이라 쌓지 않는다
                self.rec.event(st.cycle, st.phase, "plug", "-", str(e)); st.events += 1
            return None

    def _handle_control(self, st: CycleState) -> None:
        """결과판에서 들어온 원격 명령을 처리한다 (20초 표본마다 한 번 확인)."""
        cmd = self.ctrl.take()
        if not cmd:
            return
        name = cmd.get("cmd"); who = cmd.get("source", "?")
        self.rec.event(st.cycle, st.phase, "manual", "-", f"원격 명령 {name} ({who})"); st.events += 1
        if name == "stop_safe":
            self.ctrl.ack(name, "받음 — 플러그 ON 으로 두고 멈춘다")
            raise StopRequested()
        if name == "plug_on":
            r = self._plug("on", st); self._force = "charge"
            self.ctrl.ack(name, f"플러그 켜짐 · {r.watts:.1f} W" if r else "플러그 응답 없음 (재시도 중)")
        elif name == "plug_off":
            r = self._plug("off", st); self._force = "discharge"
            self.ctrl.ack(name, "플러그 꺼짐" if r else "플러그 응답 없음 (재시도 중)")
        else:
            self.ctrl.ack(str(name), "알 수 없는 명령")

    def _watch_link(self, st: CycleState) -> None:
        """셀이 하나도 안 들리면: 1분 뒤부터 2분마다 Wi-Fi 재연결, 5분 넘으면 사이클 중단.

        2026-10-07 22:43 처럼 PC Wi-Fi 가 끊기면 셀도 플러그도 안 보인다. 그때 방전 중이었다면
        플러그가 꺼진 채 '기준선 도달'을 영원히 기다리다 셀이 모두 꺼졌을 것이다.
        """
        now = time.time()
        if self._batts():
            if self._blind_since:
                self.rec.log(f"셀 신호 복구 · {now - self._blind_since:.0f}초 끊김")
            self._blind_since = None
            return
        if self._blind_since is None:
            self._blind_since = now
            self.rec.event(st.cycle, st.phase, "blind", "-", "셀이 하나도 안 들림"); st.events += 1
            return
        blind = now - self._blind_since
        if (self.cfg.wifi_reconnect and not self.dry and blind >= self.cfg.blind_reconnect_s
                and now - self._last_reconnect >= 120):
            self._last_reconnect = now
            ok = net.reconnect(self.cfg.wifi_profile, self.cfg.hub_ip)
            self.rec.event(st.cycle, st.phase, "wifi_reconnect", "-", f"{self.cfg.wifi_profile} 재연결 {'성공' if ok else '실패'}")
            st.events += 1
        if blind >= self.cfg.blind_failsafe_min * 60:
            raise CycleAborted(f"셀이 {blind/60:.0f}분 동안 하나도 안 들림")

    def _poll(self, st: CycleState, wh: float, prev: tuple[float, float] | None) -> tuple[float, tuple[float, float] | None, float]:
        """표본 1회: 플러그 읽기 → Wh 누적 → 기록. (wh, (t, W), W) 를 돌려준다."""
        r = self._plug("read", st)
        w = r.watts if r else float("nan"); on = r.on if r else None; now = time.time()
        if prev is not None:
            wh += integrate_wh(prev[1], w, now - prev[0])
        if r:
            st.last_on, st.last_w = on, w
        self.rec.sample(st.phase, on, w, wh, self._batts(), self._hr_cells())
        self._check_gaps(st)
        self._publish(st)
        self._handle_control(st)
        self._watch_link(st)
        return wh, (now, w), w

    # --- 단계 ---
    def run_cycle(self) -> CycleState:
        cfg = self.cfg
        st = CycleState(cycle=self.rec.next_cycle_no())
        self.current = st
        self.rec.begin_cycle(st.cycle)
        self.rec.log(f"=== 사이클 {st.cycle} 시작 · 기준선 {cfg.discharge_stop_pct}% · 삭제 {'켬' if cfg.delete_after_extract else '끔'}")
        missing = self.live.wait_for(cfg.serials, 10)
        if missing:
            self.rec.event(st.cycle, st.phase, "missing_cells", "-", f"{len(missing)}대 신호 없음: {missing}"); st.events += 1

        # 1) 방전: 플러그 OFF, 기준선까지 대기
        self._phase(st, "DISCHARGE")
        self._plug("off", st); st.discharge_start = time.time()
        st.discharge_start_batt = self._per_cell_batt()
        self.rec.log(f"방전 시작 · 배터리 최저 {min(self._batts() or [0])}%")
        wh, prev = 0.0, None
        while True:
            b = self._batts()
            if discharge_done(b, cfg.discharge_stop_pct) or self._force == "charge":
                st.min_batt_at_stop = min(b) if b else None
                if self._force == "charge":
                    st.note = "manual_charge"
                self._force = None; break
            wh, prev, _ = self._poll(st, wh, prev)
            time.sleep(cfg.poll_s)
        st.discharge_end = time.time()
        self.rec.discharge(st.cycle, st.discharge_start_batt, self._per_cell_batt(),
                           st.discharge_end - st.discharge_start, cfg.discharge_stop_pct)
        self._phase(st, "THRESHOLD")
        self.rec.log(f"기준선 도달 · 최저 {st.min_batt_at_stop}% · 방전 {(st.discharge_end - st.discharge_start)/3600:.2f}h")

        # 2) 플러그 ON → 충전 시작 확인
        self._phase(st, "PLUG_ON")
        r = self._plug("on", st); st.plug_on_at = time.time()
        time.sleep(5)
        r = self._plug("read", st)
        if r and r.watts < cfg.plug_on_min_w:
            self.rec.event(st.cycle, st.phase, "plug", "-", f"켠 뒤 전력 {r.watts:.1f} W < {cfg.plug_on_min_w} W — 충전이 시작되지 않음"); st.events += 1
        self.rec.log(f"플러그 ON · {r.watts:.1f} W" if r else "플러그 ON (전력 미확인)")

        # 3) 추출 (충전 중)
        self._phase(st, "EXTRACT"); st.extract_start = time.time()
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
        self._phase(st, "CHARGE"); samples: list[tuple[float, float]] = []
        while True:
            wh, prev, w = self._poll(st, wh, prev)
            if prev: samples.append(prev)
            now = time.time()
            if is_full(samples, self._batts(), now, cfg.full_flat_min, cfg.full_flat_tol_w,
                       cfg.full_requires_all_100, len(cfg.serials)) or self._force == "discharge":
                if self._force == "discharge":
                    st.note = (st.note + " manual_stop_charge").strip()
                self._force = None; st.full_at = now; break
            if now - st.plug_on_at > cfg.charge_timeout_h * 3600:
                self.rec.event(st.cycle, st.phase, "charge_timeout", "-", f"{cfg.charge_timeout_h}h 안에 만충 판정 안 됨"); st.events += 1
                st.full_at = now; st.note = "charge_timeout"; break
            time.sleep(cfg.poll_s)
        self._phase(st, "FULL")
        recent = [w for t, w in samples if st.full_at - t <= cfg.full_flat_min * 60 and not math.isnan(w)]
        st.floor_w = sum(recent) / len(recent) if recent else None
        # 충전에 들어간 Wh = 전체 적분 − 바닥 전력 × 충전 시간 (셀 작동분 제외)
        charge_h = (st.full_at - st.plug_on_at) / 3600
        st.charge_wh = wh - (st.floor_w or 0) * charge_h
        self.rec.log(f"만충 · 충전 {charge_h*60:.0f}분 · 적분 {wh:.1f} Wh · 바닥 {st.floor_w or float('nan'):.1f} W → 충전분 {st.charge_wh:.1f} Wh")

        # 5) 플러그 OFF
        self._phase(st, "PLUG_OFF")
        self._plug("off", st); st.plug_off_at = time.time()
        self.rec.cycle(self._row(st))
        self._publish(st)
        self.rec.log(f"=== 사이클 {st.cycle} 끝 · 이상 {st.events}건")
        return st

    def _row(self, st: CycleState) -> dict:
        def h(a, b): return f"{(b - a)/3600:.3f}" if a and b else ""
        return {
            "cycle": st.cycle, "start": _ts(st.t_start),
            "discharge_start": _ts(st.discharge_start), "discharge_end": _ts(st.discharge_end),
            "discharge_h": h(st.discharge_start, st.discharge_end), "min_batt_at_stop": st.min_batt_at_stop or "",
            "plug_on": _ts(st.plug_on_at), "extract_start": _ts(st.extract_start), "extract_end": _ts(st.extract_end),
            "extract_s": f"{st.extract_end - st.extract_start:.0f}" if st.extract_start and st.extract_end else "",
            "extract_ok": st.extract_ok, "extract_mb": f"{st.extract_mb:.1f}",
            "full_at": _ts(st.full_at),
            "charge_min": f"{(st.full_at - st.plug_on_at)/60:.0f}" if st.full_at and st.plug_on_at else "",
            "charge_wh": f"{st.charge_wh:.1f}" if st.full_at else "",
            "floor_w": f"{st.floor_w:.1f}" if st.floor_w is not None else "", "plug_off": _ts(st.plug_off_at),
            "events": st.events, "note": st.note,
        }

    def _recover(self, st: CycleState) -> None:
        """안전 상태로 두고 셀이 다시 보일 때까지 기다린다.

        안전 상태 = 플러그 ON. 셀이 꺼지면 사람이 Dock 버튼을 눌러야 하고 그동안 시험이 멈추므로,
        모를 때는 충전 쪽으로 둔다. Wi-Fi 가 끊겨 플러그에도 닿지 않으면 재연결을 계속 시도한다.
        """
        self._phase(st, "RECOVER")
        plug_on = False; asked_human = False; t0 = time.time(); seen_since = None
        while True:
            now = time.time()
            if not plug_on and not self.dry:
                try:
                    r = self.plug.recharge(); plug_on = r.on
                    st.last_on, st.last_w = r.on, r.watts
                    self.rec.log(f"안전 상태: 플러그 ON · {r.watts:.1f} W")
                except Exception as e:
                    self.rec.log(f"플러그 ON 실패 ({e}) — 재연결 뒤 다시 시도")
            if self._batts():
                seen_since = seen_since or now
                if now - seen_since >= 30:
                    self.rec.log("셀 신호 복구 — 다음 사이클을 시작한다")
                    self._blind_since = None
                    return
            else:
                seen_since = None
                if (self.cfg.wifi_reconnect and not self.dry and now - self._last_reconnect >= 120
                        and not net.hub_reachable(self.cfg.hub_ip)):
                    self._last_reconnect = now
                    ok = net.reconnect(self.cfg.wifi_profile, self.cfg.hub_ip)
                    self.rec.event(st.cycle, st.phase, "wifi_reconnect", "-", f"재연결 {'성공' if ok else '실패'}")
                if plug_on and not asked_human and now - t0 > 30 * 60 and net.hub_reachable(self.cfg.hub_ip):
                    asked_human = True
                    self.rec.event(st.cycle, st.phase, "need_human", "-",
                                   "시험망·플러그는 정상인데 셀이 30분째 안 들림 — 셀이 꺼졌을 수 있다. Dock 버튼을 2초 이상 눌러 켠다")
            self._publish(st)
            self._handle_control(st)
            time.sleep(self.cfg.poll_s)

    def precharge(self) -> None:
        """시작 전에 만충까지 충전한다 — 첫 사이클의 방전이 100% 에서 시작하도록.

        플러그를 껐다 켜서(recharge) Dock 이 충전을 확실히 시작하게 하고, 사이클과 같은 만충 판정을 쓴다.
        사이클 기록(cycles.csv)에는 넣지 않고 표본(samples)과 로그에만 'PRECHARGE' 로 남긴다.
        """
        cfg = self.cfg
        st = CycleState(cycle=self.rec.next_cycle_no())
        self.current = st
        self.rec.begin_cycle(st.cycle)
        self._phase(st, "PRECHARGE")
        if self.dry:
            self.rec.log("(모의) 예비 충전 생략"); return
        try:
            r = self.plug.recharge(); st.plug_on_at = time.time()
            st.last_on, st.last_w = r.on, r.watts
            self.rec.log(f"예비 충전 시작 · 플러그 껐다 켬 · {r.watts:.1f} W")
        except Exception as e:
            self.rec.event(st.cycle, st.phase, "plug", "-", f"예비 충전 플러그 실패: {e}"); st.plug_on_at = time.time()
        wh, prev = 0.0, None; samples: list[tuple[float, float]] = []
        while True:
            wh, prev, w = self._poll(st, wh, prev)
            if prev:
                samples.append(prev)
            now = time.time()
            if is_full(samples, self._batts(), now, cfg.full_flat_min, cfg.full_flat_tol_w,
                       cfg.full_requires_all_100, len(cfg.serials)):
                break
            if now - st.plug_on_at > cfg.charge_timeout_h * 3600:
                self.rec.event(st.cycle, st.phase, "charge_timeout", "-", f"예비 충전 {cfg.charge_timeout_h}h 안에 만충 안 됨")
                break
            time.sleep(cfg.poll_s)
        self.rec.log(f"예비 충전 끝 · {(time.time() - st.plug_on_at)/60:.0f}분 · {wh:.1f} Wh")
        self.current = None

    def run(self, cycles: int, precharge: bool = False) -> None:
        """감독 루프: 사이클 하나가 깨져도 기록하고 안전 상태로 둔 뒤 다음 사이클로 간다."""
        done = fails = 0
        if precharge:
            try:
                self.precharge()
            except StopRequested:
                raise
            except Exception as e:
                st = self.current or CycleState(cycle=self.rec.next_cycle_no())
                self.rec.event(st.cycle, "PRECHARGE", "aborted", "-", f"예비 충전 중단: {e}")
                self._recover(st)
        while done < cycles:
            try:
                self.run_cycle()
                done += 1; fails = 0
            except KeyboardInterrupt:
                raise
            except StopRequested:
                st = self.current or CycleState(cycle=self.rec.next_cycle_no())
                self.rec.log("원격 정지 요청 — 플러그 ON 으로 두고 멈춘다")
                try:
                    if not self.dry:
                        self.plug.recharge()
                except Exception as e:
                    self.rec.log(f"플러그 ON 실패: {e}")
                st.note = (st.note + " stopped_by_user").strip()
                self.rec.event(st.cycle, st.phase, "stopped", "-", "원격 안전 정지 · 플러그 ON")
                self.rec.cycle(self._row(st)); self._phase(st, "STOPPED")
                return
            except Exception as e:
                fails += 1
                st = self.current or CycleState(cycle=self.rec.next_cycle_no())
                kind = "aborted" if isinstance(e, CycleAborted) else "crash"
                detail = str(e) if isinstance(e, CycleAborted) else f"{type(e).__name__}: {e}"
                self.rec.log(f"사이클 {st.cycle} 중단 ({kind}) · {detail}")
                if kind == "crash":
                    self.rec.log(traceback.format_exc())
                self.rec.event(st.cycle, st.phase, kind, "-", detail); st.events += 1
                st.note = f"{kind}: {detail}"[:200]
                self.rec.cycle(self._row(st))
                self.current = None
                if fails >= self.cfg.max_consecutive_failures:
                    self.rec.log(f"연속 {fails}회 중단 — 플러그 ON 으로 두고 멈춘다")
                    try:
                        if not self.dry:
                            self.plug.recharge()
                    except Exception:
                        pass
                    return
                self._recover(st)


def _ts(t: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""
