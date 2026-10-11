"""사이클 상태기계 — 방전 → 기준선 → 플러그 ON → 추출 → 충전 → 만충 → 플러그 OFF → (반복)

판정 함수(is_full, integrate_wh, discharge_done)는 순수 함수라 장비 없이 단위 검사한다.
CycleRunner 는 그 판정에 따라 플러그와 셀을 움직이고 Recorder 에 남긴다.
안전망(FMEA P4 — Dock 저전력 · 방전 중 켜진 플러그 · 셀 저장량 · 접촉 불량 · 대기 모드 셀 · 디스크)의 판정은
guards.py 의 순수 함수이고, 여기에는 그것을 부르는 얇은 배선(_guard_* · _watch_*)만 있다.
"""
from __future__ import annotations

import math
import shutil
import threading
import time
import traceback
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import guards, net
from .cells import CellLink, LiveListener, extract_problem
from .cloud import SCHEMA_AWARE
from .control import COMMANDS, ControlInbox, StopRequested, command_expired
from .config import SAFETY, Config, expand_serials, other_plugs, registered_serials
from .plug import Plug, ensure_on
from .record import Recorder

class CycleAborted(Exception):
    """사이클을 더 진행할 수 없다 (셀이 오래 안 보임 등). 감독 루프가 받아 안전 상태로 둔다."""


class Reconfigure(Exception):
    """세트 구성이 바뀌어 엔진을 다시 띄워야 한다 — 만충 직후 경계(PLUG_OFF 전)에서 플러그를 켠 채 끝낸다(명세 2-8)."""


class HoldRequested(StopRequested):
    """조정자가 이 세트를 HOLD 로 보낸다(다른 세트의 짝 이상 · 세트 정지). 안전 정지와 같은 길로 끝나고 이유가 붙는다."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


class PairMismatch(Exception):
    """플러그와 셀의 짝이 틀렸다는 증거(방향 대조 — 다중 세트 명세 3-6). 실제 짝을 모르므로 등록된 모든 플러그를 켜고
    엔진을 failsafe 로 끝낸다(감시자가 되살리지 않고 사람을 부른다). 다른 사이클 오류처럼 복구 대기로 넘기지 않는다."""


PHASES = ["PRECHARGE", "DISCHARGE", "THRESHOLD", "PLUG_ON", "EXTRACT", "CHARGE", "FULL", "PLUG_OFF"]
PHASE_KO = {"PRECHARGE": "예비 충전", "PLUG_ON": "플러그 켜기", "CHARGE": "충전", "RECOVER": "복구 대기"}


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


def floor_flat(samples: list[tuple[float, float]], now: float, flat_min: float, floor_w_max: float) -> bool:
    """최근 flat_min 분 동안 전력이 줄곧 바닥(≤ floor_w_max)이었나 — Dock 이 충전하지 않는 상태(만충 뒤 셀 작동분만 흐른다).

    is_full 과 같은 창 규칙(표본 3개 이상 · flat_min 분이 지났어야 한다)이고, 변동폭 대신 '전부 바닥 이하'를 본다.
    """
    window = [w for t, w in samples if now - t <= flat_min * 60 and not math.isnan(w)]
    if len(window) < 3:
        return False
    if samples and now - samples[0][0] < flat_min * 60:
        return False
    return max(window) <= floor_w_max


def cells_at_least(batteries: list[int], pct: int, expected_cells: int) -> bool:
    """모든 셀이 들리고(expected_cells 대) 전부 pct 이상인가. 셀이 하나라도 안 들리면 False (판단 보류)."""
    return len(batteries) >= expected_cells and all(b >= pct for b in batteries)


def precharge_verdict(samples: list[tuple[float, float]], batteries: list[int], now: float, cfg: Config) -> str | None:
    """예비 충전을 끝낼 수 있나 (2026-10-11 사고 — 셀 96~97% 에서 Dock 이 다시 충전하지 않아 '모든 셀 100%' 가 영영 오지 않았다).

      "full"             사이클과 같은 만충 판정(is_full)
      "floor_full"       전력이 바닥에서 full_flat_min 분 평탄 + 모든 셀 ≥ precharge_skip_pct — 더 기다려도 소용없고 충분히 찼다
      "dock_no_recharge" 바닥 평탄인데 셀이 precharge_skip_pct 미만 — Dock 이 재충전하지 않는다(기존 저전력 복구는 10 W 아래만 본다)
      None               아직
    정규 충전 단계의 만충 판정은 이 함수를 쓰지 않는다 — 거기서는 Dock 이 충전 중이다.
    """
    if is_full(samples, batteries, now, cfg.full_flat_min, cfg.full_flat_tol_w, cfg.full_requires_all_100, len(cfg.serials)):
        return "full"
    if not floor_flat(samples, now, cfg.full_flat_min, cfg.floor_w_max):
        return None
    return "floor_full" if cells_at_least(batteries, cfg.precharge_skip_pct, len(cfg.serials)) else "dock_no_recharge"


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
    # 안전망 (FMEA P4)
    charge_start_batt: dict = field(default_factory=dict)   # 방전 끝(= 충전 시작) 때 셀별 배터리 — 접촉 불량 판정의 기준
    not_charging: set = field(default_factory=set)          # 이번 사이클에 알린 접촉 불량 셀
    power_recovers: int = 0                                  # 이번 사이클의 Dock 저전력 자동 복구(끊었다 켜기) 횟수
    dock_power_fail: int = 0                                 # 이번 사이클에 복구 한도를 다 쓰고도 안 된 횟수 (이상 dock_power)
    ip_base: int = 0                                         # 사이클 시작 때의 LiveListener.ip_changes


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
        self.started_at = time.time()                # 이보다 먼저 보낸 원격 명령은 엔진이 없던 동안 받은 것 (QA C-1 · N-1)
        self._drop_leftover_command()                # 엔진이 꺼져 있던 동안 남은 로컬 명령은 실행하지 않는다 (QA C-1)
        self._force: str | None = None               # 원격 명령: "charge" | "discharge"
        self.plug_on_at_exit: bool | None = None     # run() 이 끝내며 플러그를 켜 두었나 (engine.json 에 남는다)
        self.plug_known: bool | None = None          # 마지막으로 안 플러그 켜짐 여부 (읽기·명령의 응답) — Ctrl+C 로 끝날 때 쓴다(run_cycle.note_interrupt)
        # --- 안전망 (FMEA P4) ---
        self._last_read = None                       # 마지막 플러그 읽기 (실패면 None)
        self._cmd_failed: str | None = None          # 마지막 켜기·끄기 명령이 실패했으면 그 동작 ("on" | "off")
        self._power: guards.PowerWatch | None = None  # 지금 단계의 Dock 저전력 감시 (_phase 가 단계마다 새로 만든다)
        # 셀별 '마지막으로 아는 저장량' (MB, 시각). 시작 직후에는 셀에 묻지 않고 직전 cells_<사이클>.csv 에서 이어받는다.
        # cell_storage_known_max_h 보다 오래됐거나 삭제를 켜기 전 기록은 '모름'으로 빠진다 — 엔진 밖에서 지웠을 수 있다(검토 F7)
        self._storage_known = guards.storage_from_cells_csv(rec.dir, cfg.serials, now=time.time(),
                                                            max_age_s=cfg.cell_storage_known_max_h * 3600,
                                                            need_delete=cfg.delete_after_extract)
        self._storage_told: dict[int, int] = {}      # 셀별 이미 알린 저장량 단계
        self._storage_full_told: set[int] = set()    # 가득 차 멈춘 것으로 알린 셀 (라이브가 돌아오면 빠진다)
        self._last_resume: dict[int, float] = {}     # 대기 모드 셀을 마지막으로 깨운 시각
        self._disk_free = lambda: shutil.disk_usage(self.rec.dir).free   # 검사에서 바꾼다
        self._disk_at = 0.0; self._disk_lv = 0; self._disk_free_gb: float | None = None
        self._registered = set(registered_serials(cfg))   # 등록된 시리얼 (모든 세트) — now.json 의 heard 에서 뺀다
        # --- 짝 이상 (다중 세트 명세 3-6 · D2 · D3) — 모든 모드. 정상 운전에서는 아무 일도 하지 않는다 ---
        self._dir = guards.DirectionWatch(SAFETY, cfg.stuck_rule()[0])       # (a) 충전 쪽 하락 · (b) 방전 중 상승 · (c) 방전 시작
        self._cross = guards.CrossWatch(SAFETY, _other_groups(cfg))           # (d) 운전하지 않는 등록 세트 — 등록 세트가 둘 이상일 때만
        self.cross_skip = lambda: ()                 # (d) 에서 뺄 세트 — 지금 짝 검증 중인 세트 (run_cycle 이 검증 관리자에 잇는다)
        self._cross_read_at: dict = {}               # (d) 다른 세트 플러그를 마지막으로 읽은 시각
        self._cross_reading: dict = {}               # (d) 아직 끝나지 않은 읽기 스레드 (세트 id → Thread)
        self._cross_plug_obj: dict = {}              # (d) 세트 id → 플러그 객체 (읽기 전용으로만 쓴다)
        self._fault_threads: list[threading.Thread] = []
        # 다른 세트의 플러그 — 세트 1 의 plug_ip_hint 를 물려받지 않는다(ip 가 비면 MAC 방송 탐색, 명세 3-6 · D13)
        self._plug_factory = lambda mac, ip: Plug(replace(cfg, plug_mac=mac, plug_ip_hint=ip))
        # --- 다중 세트에서 조정자가 바꿔 끼우는 것 (명세 2-4 · 2-7). 기본값은 전부 '지금 동작'이다 ---
        self.name = "1"                              # 세트 id (추출 문 · 재연결 관문의 소유자 이름)
        self.gate = None                             # 추출 문 (shared.ExtractGate) — None 이면 지금처럼 혼자 연다
        self.link_guard = None                       # 재연결 관문 (shared.LinkGuard) — None 이면 지금처럼 이 실행기가 정한다
        self.commands = None                         # 명령 출처 — None 이면 지금처럼 control.json 과 클라우드 표를 직접 집는다.
                                                     # 다중 모드: () → [(명령 dict, 보낸 사람, ack 함수)] (조정자의 대기열)
        self._halt = None                            # threading.Event — 다중 모드에서 표본 사이 기다림을 끊는다(Ctrl+C 는 주 스레드에만 온다)
        self.verify_hook = None                      # 짝 검증 관리자(verifier.VerifyManager.tick) — 등록 세트가 둘 이상일 때만 run_cycle 이 끼운다
        self.boundary_hook = None                    # 만충 직후(PLUG_OFF 전) '다시 시작할까' — True 면 Reconfigure (명세 2-8)
        self.hold_request: str | None = None         # 조정자가 세운 HOLD 요청 — 다음 표본에서 HoldRequested
        self.on_pair_fault = None                    # 짝 이상을 조정자에게 알린다 — 모든 운전 세트 HOLD (명세 3-6)
        self.owns_bench = True                       # 시험대 지표(디스크 · 미등록 감지)를 재나 — 다중 모드에서 세트 1 실행기만
        self.fault_plugs: list[dict] | None = None   # 짝 이상 때 켤 '다른' 플러그 — 다중 모드는 조정자가 준다(세트 1 의 실효 MAC 포함).
                                                     # None 이면 지금처럼 세트 1 이 아닌 등록 세트 전부(other_plugs)
        self.external_fault: str | None = None       # 짝 검증기가 찾은 짝 이상 — 다음 표본에서 '모든 플러그 켬'(명세 3-6)

    # --- 공통 도우미 ---
    def _sleep(self, s: float) -> None:
        """표본 사이 기다림. 단일 모드는 지금처럼 time.sleep. 다중 모드는 끊을 수 있는 기다림 — 끊기면 KeyboardInterrupt 처럼 끝낸다."""
        if self._halt is None:
            time.sleep(s)
            return
        end = time.time() + s
        while True:                                  # 2초씩 — 멈춤 요청에 2초 안에 답한다(시계는 time 모듈을 따른다)
            if self._halt.is_set():
                raise KeyboardInterrupt("조정자가 멈춤을 요청")
            left = end - time.time()
            if left <= 0:
                return
            time.sleep(min(2.0, left))

    def _per_cell_batt(self) -> dict[int, int]:
        snap = self.live.snapshot(); now = time.time()
        return {s: c.battery for s, c in snap.items()
                if s in self.cfg.serials and now - c.t <= 5 and not c.waiting and guards.batt_ok(c.battery)}

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
            "metrics": self._metrics_or_error(st),
            "heard": self._heard(snap, now),
            # 이 엔진이 아는 클라우드 스키마 판 — 마이그레이션 적용 절차의 첫 줄이 클라우드에서 이것을 본다(명세 5-2 · 7-2 허용 차이)
            "cloud_schema": SCHEMA_AWARE,
        })

    def _heard(self, snap: dict, now: float) -> list[dict]:
        """어느 세트에도 속하지 않는데 heard_window_s(30초) 안에 들린 셀 — 결과판의 '미등록 감지'와 등록 화면의 재료.
        LiveListener 는 이 PC 로 오는 모든 셀의 라이브를 들고 있으므로(시리얼로 걸러 쓸 뿐) 따로 듣지 않는다.
        now.json 이 멈추면 감시자가 엔진을 '멈춤'으로 보므로, 이 화면용 목록 때문에 쓰기가 실패하지 않게 한다."""
        if not self.owns_bench:
            return []
        try:
            return [{"serial": s, "ip": c.ip, "battery": c.battery, "rssi": c.rssi, "age": round(now - c.t, 1)}
                    for s, c in sorted(snap.items()) if s not in self._registered and now - c.t <= self.cfg.heard_window_s]
        except Exception:
            return []

    def _metrics_or_error(self, st: CycleState) -> dict:
        """지표를 모으다 실패해도 now.json 은 써야 한다 — now.json 이 멈추면 감시자가 엔진을 '멈춤'으로 보고 끝낸다."""
        try:
            return self._metrics(st)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def _metrics(self, st: CycleState) -> dict:
        """신호등용 지표 (now.json 의 metrics). 키와 뜻은 다음 단계(신호등)와의 계약이다 — README '안전망' 절의 지표 표."""
        cfg, rec, now = self.cfg, self.rec, time.time()
        cloud: dict = {}
        if hasattr(rec.cloud, "stats"):                 # 클라우드 쪽(P3)이 만들면 쓴다
            try:
                cloud = rec.cloud.stats() or {}
            except Exception as e:
                cloud = {"error": f"{type(e).__name__}: {e}"}
        snap = self.live.snapshot()
        return {
            "plug": self.plug.metrics(now) if hasattr(self.plug, "metrics") else {},
            "recover_cycle": st.power_recovers,
            "dock_power_fail": st.dock_power_fail,
            "manual_plug_1h": rec.count({"manual_plug"}, 3600, now),
            "manual_plug_last": rec.last({"manual_plug"}),
            "ip_changes_cycle": self._ip_total() - st.ip_base,
            "reconnects_24h": rec.count({"wifi_reconnect"}, 86400, now),
            "live_gaps_1h": rec.count({"live_gap"}, 3600, now),
            "events_1h": rec.count(None, 3600, now),
            "disk_free_gb": self._disk_free_gb,
            # 모르는 셀(오래됐거나 이어받지 못한 셀)은 빼고 요약한다 — 낡은 추정으로 신호등이 빨강이 되지 않게(검토 F7)
            "cell_storage": guards.storage_summary({s: v for s, v in self._storage_fresh(now).items() if s in cfg.serials},
                                                   now, cfg.cell_storage_cap_mb, cfg.cell_storage_rate_mb_h),
            "not_charging": sorted(st.not_charging),
            "waiting": sorted(s for s, c in snap.items() if s in cfg.serials and _waiting(c, now)),
            "cloud": cloud,
            # 시험 진척 — 결과판 · '프로그램' 차선이 "누적 N / 목표 500" 을 보인다 (지금 사이클은 cycles.csv 에 아직 없으므로 누적에 들지 않는다)
            "cycles_done": rec.cycles_done(), "target_cycles": cfg.target_cycles,
        }

    def _ip_total(self) -> int:
        return int(getattr(self.live, "ip_changes", 0))

    def _phase(self, st: CycleState, name: str) -> None:
        st.phase = name; st.phase_since = time.time()
        # 플러그가 켜져 있어야 하는 단계면 Dock 저전력 감시를 새로 시작한다 (복구 시도 횟수는 단계 한 번당)
        self._power = guards.PowerWatch(*self.cfg.stuck_rule()) if guards.PLUG_EXPECT.get(name) else None
        self._publish(st)

    def _batts(self) -> list[int]:
        snap = self.live.snapshot()
        now = time.time()
        # 0 ~ 100 밖의 값은 기준선 · 만충 비교에서 버린다(코드 검토 8)
        return [c.battery for s, c in snap.items()
                if s in self.cfg.serials and now - c.t <= 5 and not c.waiting and guards.batt_ok(c.battery)]

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

    def _saw(self, st: CycleState | None, r) -> None:
        """플러그 응답 하나를 기억한다 — 결과판의 플러그 칸(st.last_on · last_w)과 마지막으로 안 켜짐 여부(plug_known)."""
        self.plug_known = r.on
        if st is not None:
            st.last_on, st.last_w = r.on, r.watts

    def _plug(self, action: str, st: CycleState):
        if self.dry:
            self.rec.log(f"(모의) 플러그 {action}")
            return None
        try:
            r = getattr(self.plug, action)()
        except RuntimeError as e:
            if not (self._blind_since and action == "read"):   # 셀도 안 보이면 같은 원인이라 쌓지 않는다
                self.rec.event(st.cycle, st.phase, "plug", "-", str(e)); st.events += 1
            r = None
        if r is not None:
            self.plug_known = r.on
        if action == "read":
            self._last_read = r                     # 안전망 판정(_guard_*)이 이 읽기를 쓴다
        else:
            if r is not None and r.on != (action == "on"):
                # 응답은 왔는데 상태가 명령과 반대다 — 실패로 본다(검토 F11). 끄기에 '켜짐'으로 답했는데 성공으로 치면,
                # 다음 표본의 방전 중 켜짐 감시가 이것을 사람의 조작(manual_plug)으로 잘못 적는다.
                self.rec.event(st.cycle, st.phase, "plug", "-",
                               f"플러그 {action} 명령에 {'켜짐' if r.on else '꺼짐'}({r.watts:.1f} W)으로 답함 — 실패로 본다")
                st.events += 1
                r = None
            self._cmd_failed = None if r else action
            if action == "off" and r is not None:
                self._cross.plug_off(time.time())        # (d) 는 엔진의 실제 끄기 뒤를 본다
        return r

    def _drop_leftover_command(self) -> None:
        """엔진 시작 때 남아 있는 로컬 명령(control.json)은 실행하지 않고 버린다 — 엔진이 꺼져 있던 동안 받은 것이라
        사람이 이미 '안 됐다'고 여겼을 수 있다. 클라우드 명령은 보낸 시각(requested_at)의 나이 규칙이 같은 일을 한다."""
        left = self.ctrl.take()
        if not left:
            return
        name = left.get("cmd")
        why = command_expired(left, self.cfg.command_max_age_s) or "엔진 시작 전에 남아 있던 명령"
        self.ctrl.ack(str(name), f"실행하지 않고 버림 — 엔진 시작 때 남아 있던 명령 ({why})")
        self.rec.log(f"엔진 시작: 남아 있던 원격 명령 {COMMANDS.get(name, name)} ({left.get('source', '?')}) 을 실행하지 않고 버림 — {why}")

    def _handle_control(self, st: CycleState) -> None:
        """원격 명령을 처리한다 (20초 표본마다 한 번). 통로는 둘 — 로컬 결과판의 파일, 클라우드 결과판의 표.
        엔진 시작 전에 보낸 명령과 command_max_age_s 보다 오래된 명령은 실행하지 않고 버린다(QA C-1). 시작 뒤에 보낸 명령은
        추출처럼 엔진이 몇 분 바빴어도 실행한다(QA N-1). 버린 사실은 응답과 이상 기록에 남는다."""
        now = time.time()
        if self.commands is not None:                 # 다중 모드 — 조정자가 나눠 준 이 세트의 명령
            for cmd, who, ack in self.commands():
                why = command_expired(cmd, self.cfg.command_max_age_s, now, since=self.started_at)
                if why:
                    self._drop_command(st, cmd.get("cmd"), who, why, ack)
                else:
                    self._apply_command(st, cmd.get("cmd"), who, ack, _target(cmd))
            return
        local = self.ctrl.take()
        if local:
            ack = lambda result, name=local.get("cmd"): self.ctrl.ack(str(name), result)
            why = command_expired(local, self.cfg.command_max_age_s, now, since=self.started_at)
            if why:
                self._drop_command(st, local.get("cmd"), local.get("source", "?"), why, ack)
            else:
                self._apply_command(st, local.get("cmd"), local.get("source", "?"), ack, _target(local))
        remote = self.rec.cloud.poll_command()
        if remote:
            ack = lambda result, cid=remote.get("id"): self.rec.cloud.ack(cid, result)
            who = remote.get("requested_by") or "cloud"
            why = command_expired(remote, self.cfg.command_max_age_s, now, since=self.started_at)
            if why:
                self._drop_command(st, remote.get("cmd"), who, why, ack)
            else:
                self._apply_command(st, remote.get("cmd"), who, ack, _target(remote))

    def _drop_command(self, st: CycleState, name, who: str, why: str, ack) -> None:
        self.rec.event(st.cycle, st.phase, "manual_expired", "-",
                       f"원격 명령 {COMMANDS.get(name, name)} ({who}) 실행하지 않고 버림 — {why}"); st.events += 1
        ack(f"실행하지 않고 버림 — 만료 ({why})")

    def _apply_command(self, st: CycleState, name, who: str, ack, target=None) -> None:
        # 세트가 지정됐는데 이 실행기의 세트가 아니면 실행하지 않는다(명세 2-7). 지정이 없는 명령(옛 결과판 · 옛 클라우드 행)은 지금과 같다.
        # 이것이 없으면 새 결과판이 세트 2 로 보낸 '플러그 끄기'를 이 실행기가 세트 1 에서 실행한다 — 다른 세트의 전원을 끊는 사고(FMEA 8.5)
        if target is not None and str(target) != str(self.name):
            self.rec.event(st.cycle, st.phase, "manual_rejected", "-",
                           f"원격 명령 {COMMANDS.get(name, name)} ({who}) 실행하지 않고 버림 — 세트 {target} 로 보낸 명령인데 "
                           f"이 엔진은 세트 {self.name} 만 운전한다"); st.events += 1
            ack(f"실행하지 않고 버림 — 세트 {target} 는 운전 중이 아님")
            return
        self.rec.event(st.cycle, st.phase, "manual", "-", f"원격 명령 {name} ({who})"); st.events += 1
        if name == "stop_safe":
            ack("받음 — 플러그 ON 으로 두고 멈춘다")
            raise StopRequested()
        if name == "plug_on":
            # 방전 중일 때만 '방전을 끝내고 충전으로'의 뜻이 있다. 다른 단계에서 표지를 세우면 남아 있다가
            # 다음 방전을 첫 표본에서 끝내 버린다(10-10 발견) — 그래서 그 단계에서만 세운다.
            r = self._plug("on", st); self._force = "charge" if st.phase == "DISCHARGE" else None
            ack(f"플러그 켜짐 · {r.watts:.1f} W" if r else "플러그 응답 없음 (재시도 중)")
        elif name == "plug_off":
            # 충전 쪽 단계에서만 '만충으로 치고 넘어간다'. 방전·복구 대기 중에 세우면 다음 충전을 첫 표본에서 끝낸다.
            # 복구 대기 중 끈 플러그는 표지가 없으므로 저전력 복구가 1분 뒤 도로 켠다 — 안전 쪽이다.
            r = self._plug("off", st)
            self._force = "discharge" if st.phase in ("PRECHARGE", "PLUG_ON", "EXTRACT", "CHARGE", "FULL") else None
            ack("플러그 꺼짐" if r else "플러그 응답 없음 (재시도 중)")
        else:
            ack("알 수 없는 명령")

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
            if self.link_guard is not None:
                self.link_guard.report(self.name, None)
            return
        if self._blind_since is None:
            self._blind_since = now
            self.rec.event(st.cycle, st.phase, "blind", "-", "셀이 하나도 안 들림"); st.events += 1
            return
        blind = now - self._blind_since
        if self.link_guard is not None:
            self.link_guard.report(self.name, self._blind_since)
            due = self.link_guard.may_reconnect(now, self.cfg.blind_reconnect_s)   # 운전 세트가 모두 안 들릴 때만 (명세 2-4)
        else:
            due = blind >= self.cfg.blind_reconnect_s and now - self._last_reconnect >= 120
        if self.cfg.wifi_reconnect and not self.dry and due:
            self._last_reconnect = now
            if self.link_guard is not None:
                self.link_guard.did_reconnect(now)
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
        if self.hold_request:
            why, self.hold_request = self.hold_request, None
            raise HoldRequested(why)
        self._safe(st, self._watch_storage, self._watch_disk, self._watch_direction)
        if self.verify_hook is not None:
            self._safe(st, lambda st_: self.verify_hook(self))
        self._publish(st)
        self._handle_control(st)
        self._watch_link(st)
        return wh, (now, w), w

    # --- 안전망 (FMEA P4) — 판정은 guards.py, 여기는 배선만 ---
    def _safe(self, st: CycleState, *checks) -> None:
        """안전망 판정을 차례로 돌린다. 안전망 자신의 오류로 사이클이 깨지면 안 되므로 예외는 로그만 남긴다
        (원격 정지·사이클 중단 신호는 그대로 올린다)."""
        for fn in checks:
            try:
                fn(st)
            except (StopRequested, CycleAborted, PairMismatch):
                raise
            except Exception as e:
                self.rec.log(f"안전망 {getattr(fn, '__name__', fn)} 건너뜀 — {type(e).__name__}: {e}")

    def _ev(self, st: CycleState, kind: str, serial, detail: str) -> None:
        self.rec.event(st.cycle, st.phase, kind, serial, detail); st.events += 1

    def _ev_cells(self, st: CycleState, kind: str, serials: list[int], detail: str) -> None:
        """셀 여럿의 같은 이상은 한 줄로 남긴다 — 셀 수만큼 알림이 쏟아지지 않게. 한 대면 시리얼 칸에, 여럿이면 '-' (목록은 detail 에)."""
        self._ev(st, kind, serials[0] if len(serials) == 1 else "-", detail)

    def _guard_power(self, st: CycleState) -> None:
        """플러그가 켜져 있어야 하는 단계에서 Dock 이 전력을 끌어 쓰지 않으면 끊었다 켠다 (FMEA 5.2 · 5.3, guards.PowerWatch).

        켜는 쪽(충전 쪽)이라 사람을 기다리지 않고 자동으로 한다 — 셀이 다 꺼지면 사람이 Dock 버튼을 눌러야 해서 그동안 시험이 멈춘다.
        원격 '플러그 끄기'로 끈 동안은 사람 뜻대로 두고 보지 않는다.
        """
        watch = self._power
        if watch is None or self.dry or self._force == "discharge":
            return
        r = self._last_read
        act = watch.feed(time.time(), r.on if r else None, r.watts if r else float("nan"))
        if act is None:
            return
        where = PHASE_KO.get(st.phase, st.phase)
        why = "꺼짐" if r.on is False else f"{r.watts:.1f} W"
        if act in ("recharge", "plug_on"):
            # recharge = Dock 저전력을 풀려고 끊었다 켠다(한도에 센다). plug_on = 한도를 다 쓴 뒤에도 꺼짐이 보여 그냥 켠다(한도와 무관 — 검토 F4).
            err = None
            try:
                r2 = self.plug.recharge(gap_s=watch.gap_s) if act == "recharge" else self.plug.on()
                self._saw(st, r2)
                self._last_read = r2
            except Exception as e:
                r2, err = None, e
            if r2 is not None and r2.on:
                watch.worked()
                if act == "recharge":
                    st.power_recovers += 1
                    self._ev(st, "plug", "-", f"{where} 중 플러그 {why} — {watch.gap_s:.0f}초 끊었다 켬 ({watch.tries}/{watch.max_tries})")
                else:
                    self._ev(st, "plug", "-", f"{where} 중 플러그 꺼짐 — 복구 한도를 다 쓴 뒤라도 꺼진 플러그는 켠다")
                return
            # 켜지 못했다 — 한도에 세지 않고 stuck_s 뒤 다시 한다. 이상은 연달아 실패한 첫 번째만(1분마다 Slack 이 쌓이지 않게)
            watch.refund()
            what = f"{where} 중 플러그 {why} — {'끊었다 켜기' if act == 'recharge' else '켜기'} 실패"
            more = f"{err}" if err is not None else "응답이 꺼짐"
            if watch.fails == 1:
                self._ev(st, "plug", "-", f"{what}: {more} — 켜지 못한 시도는 한도에 세지 않고 {watch.stuck_s:.0f}초 뒤 다시 한다")
            else:
                self.rec.log(f"{what} ({watch.fails}번째 연달아): {more}")
        else:
            st.dock_power_fail += 1
            self._ev(st, "dock_power", "-", f"{where} 중 플러그를 {watch.max_tries}번 끊었다 켰는데도 Dock 이 전력을 끌어 쓰지 않음"
                                            f"({why}) — Dock 전원·어댑터·케이블 확인")

    def _settle_power(self, st: CycleState) -> None:
        """플러그를 켠 직후 Dock 이 끌어 쓰지 않으면 추출 전에 푼다 (예전에는 이상만 남겼다).

        추출(묶음마다 몇 분)은 표본이 돌지 않아 그동안은 아무도 Dock 을 보지 않고, 셀은 기준선 아래로 계속 내려간다. 그래서 여기서
        막혔으면 표본을 돌리며 _guard_power 로 풀어 본다. 풀리거나, 한도를 다 쓰거나(dock_power), 읽지 못하면 추출로 넘어간다.
        건강하면(첫 읽기가 문턱 이상) 기다리지 않는다.
        켜기에 실패한 시도는 한도에 세지 않으므로(검토 F4) 시도 수(attempts, 실패 포함)로도 끊는다 — 플러그가 영영 응답하지 않아도
        추출과 충전 단계로 넘어가고, 충전 단계의 _guard_power 가 계속 켜 본다.
        """
        wh, prev = 0.0, None
        while (not self.dry and self._power is not None and not self._power.gave_up and self._force != "discharge"
               and self._power.attempts <= self._power.max_tries):
            r = self._last_read
            if r is None or guards.power_stuck(r.on, r.watts, self._power.stuck_w) is not True:
                return
            self._guard_power(st)
            self._sleep(min(self.cfg.poll_s, 10.0))
            wh, prev, _ = self._poll(st, wh, prev)

    def _watch_direction(self, st: CycleState) -> None:
        """방향 대조 (a) ~ (d) — 짝 이상이면 _pair_fault (다중 세트 명세 3-6). 판정은 guards.DirectionWatch · CrossWatch."""
        if self.external_fault:
            why, self.external_fault = self.external_fault, None
            self._pair_fault(st, why)
        if self.dry:
            return
        now = time.time()
        r = self._last_read
        why = self._dir.feed(now, self._per_cell_batt(), r.on if r else None, r.watts if r else math.nan)
        if not why and self._cross.groups:
            self._cross_sample(now)
            snap = self.live.snapshot()
            try:
                skip = set(self.cross_skip() or ())
            except Exception:
                skip = set(self._cross.groups)      # 검증 상태를 모르면 판정하지 않는다 (오탐 쪽을 피한다)
            why = self._cross.feed(now, {s: c.battery for s, c in snap.items()
                                         if now - c.t <= 5 and not c.waiting and guards.batt_ok(c.battery)}, skip=skip)
        if why:
            self._pair_fault(st, why)

    CROSS_READ_S = 5.0     # 다른 세트 플러그 한 번 읽기를 표본 고리가 기다리는 상한(실시간 초) — 넘으면 '모름'으로 남기고 간다

    def _cross_sample(self, now: float) -> None:
        """(d) 끄기 뒤에만, 무리마다 1분에 한 번 그 세트 플러그를 읽는다(켜기 · 끄기는 하지 않는다). 읽지 못하면 '모름' — 그 창은 판정하지 않는다."""
        plugs = {p["id"]: p for p in (self.fault_plugs if self.fault_plugs is not None else other_plugs(self.cfg))}
        for gid in self._cross.want_plugs(now):
            if now - self._cross_read_at.get(gid, -1e18) < self._cross.PLUG_EVERY_S:
                continue
            self._cross_read_at[gid] = now
            p = plugs.get(gid)
            old = self._cross_reading.get(gid)
            if p is None or (old is not None and old.is_alive()):
                self._cross.plug_seen(gid, now, None)
                continue
            box: dict = {}

            def read(p=p, gid=gid, box=box) -> None:
                try:
                    if gid not in self._cross_plug_obj:
                        self._cross_plug_obj[gid] = self._plug_factory(p["mac"], p["ip"])
                    box["on"] = bool(self._cross_plug_obj[gid].read().on)
                except Exception as e:
                    box["err"] = f"{type(e).__name__}: {e}"

            th = threading.Thread(target=read, daemon=True, name=f"cross-read-{gid}")
            self._cross_reading[gid] = th
            th.start(); th.join(self.CROSS_READ_S)
            self._cross.plug_seen(gid, now, box.get("on"))

    def _pair_fault(self, st: CycleState, why: str) -> None:
        """짝 이상 — 실제 짝을 모르므로 등록된 다른 세트의 플러그를 모두 켜기 시작하고(병렬) PairMismatch 를 올린다.
        다른 플러그는 끊지 않고 켜짐으로 읽힐 때까지 켠다(plug.ensure_on — 코드 검토 4: 끊었다 켜기는 켜기가 실패하면 끈 채 남긴다).
        끝내 켜지 못하면 MAC 과 함께 조치 알림(plug)을 낸다. 자기 플러그는 run() 이 _safe_end 로 켠다."""
        others = self.fault_plugs if self.fault_plugs is not None else other_plugs(self.cfg)
        self._ev(st, "pair_mismatch", "-", f"{why} — 등록된 모든 플러그({1 + len(others)}개)를 켜고 멈춘다. 케이블 · 플러그 별명을 "
                                           f"실물로 확인한 뒤 다시 시작")

        def turn_on(p: dict) -> None:
            try:
                rr = ensure_on(self._plug_factory(p["mac"], p["ip"]), sleep=time.sleep)
                self.rec.log(f"짝 이상: 세트 {p['id']} 플러그 {p['mac']} 켬 · {rr.watts:.1f} W")
            except Exception as e:
                msg = f"짝 이상: 세트 {p['id']} 플러그 {p['mac']} 를 켜지 못함 — {type(e).__name__}: {e}. 그 세트 셀이 방전 중일 수 있다"
                self.rec.log(msg)
                self.rec.alert(f"[셀 시험대] plug{self.rec.tag} · {msg}")

        if not self.dry:
            for p in others:
                th = threading.Thread(target=turn_on, args=(p,), daemon=True, name=f"pair-fault-{p['mac']}")
                th.start(); self._fault_threads.append(th)
        if self.on_pair_fault is not None:
            try:
                self.on_pair_fault(why)
            except Exception as e:
                self.rec.log(f"짝 이상을 조정자에게 알리지 못함 — {e}")
        raise PairMismatch(why)

    def _guard_discharge_plug(self, st: CycleState) -> None:
        """방전 중인데 플러그가 켜져 있으면 되돌린다 (FMEA 4.4 · 8.1 — 10-08 19:06 세트 1 플러그가 실수로 켜진 사고).

        원격 '플러그 켜기'(_force == "charge")가 아닌데 켜져 있으면 사람의 실수 조작으로 보고 즉시 끈다. 이것이 이 엔진에서
        전원을 자동으로 끊는 유일한 경우다 — 방전 단계의 기대 상태가 꺼짐이고(켜진 채 두면 이번 사이클의 작동시간 기록이 무의미하다),
        최저 배터리가 기준선 + manual_plug_margin_pct 보다 높을 때만 끄므로 다음 표본 전에 셀이 기준선 아래로 갈 수 없다.
        그 이하이거나 배터리를 모르면 끄지 않고 그대로 충전 단계로 넘긴다(안전 쪽).
        엔진 자신의 끄기 명령이 실패해 켜진 채인 것이면 사람 탓이 아니므로 종류를 plug 로 남긴다.
        """
        r = self._last_read
        if self.dry or r is None or not r.on or self._force == "charge":
            return
        cfg = self.cfg
        b = self._batts(); low = min(b) if b else None
        ours = self._cmd_failed == "off"
        kind = "plug" if ours else "manual_plug"
        who = "엔진의 끄기 명령이 실패해 켜진 채다" if ours else "원격 명령 없이 켜졌다 — 누가 왜 켰는지 확인"
        low_s = f"{low}%" if low is not None else "모름"
        if guards.manual_plug_action(low, cfg.discharge_stop_pct, cfg.manual_plug_margin_pct) == "charge":
            self._force = "charge"                      # 다음 바퀴에서 방전을 끝내고 충전으로 — 원격 '플러그 켜기'와 같은 길
            st.note = (st.note + " manual_plug_charge").strip()
            self._ev(st, kind, "-", f"방전 중 플러그 켜짐({r.watts:.1f} W) · {who}. 최저 배터리 {low_s} 가 "
                                    f"기준선+{cfg.manual_plug_margin_pct}%p 이하라 끄지 않고 충전으로 넘긴다")
        else:
            self._plug("off", st)
            self._ev(st, kind, "-", f"방전 중 플러그 켜짐({r.watts:.1f} W) · {who}. 최저 배터리 {low_s} 라 다시 껐다")

    def _watch_waiting(self, st: CycleState) -> None:
        """방전 중 대기 모드(0x16 만 오고 0x09 가 끊김) 셀을 그 셀만 깨워 0x26 으로 측정에 돌려보낸다 (FMEA 6.3).

        추출 뒤 0x26 을 못 받았거나 셀이 스스로 TCP 대기로 들어가면 측정을 안 한 채 남는다. 상태·추출 없이 깨운다(CellLink.resume).
        저장량 추정으로는 막지 않는다(검토 F7) — 추정은 엔진 밖에서 지운 셀을 모르고, 잘못된 추정으로 막으면 그 셀은 사이클 내내 측정을
        안 한다. 정말 가득 찬 셀은 깨워도 돌아오지 않을 뿐 해는 없고(셀당 10분에 한 번), 남의 셀을 깨우는 것은 신원 확인이 막는다.
        깨우는 동안(최대 약 2분) 표본이 쉬므로 방전 판정이 그만큼 늦을 수 있다(20 %/h 면 1%p 미만).
        """
        if self.dry or self.link is None:
            return
        cfg = self.cfg; now = time.time()
        waiting = {s: c.t for s, c in self.live.snapshot().items() if s in cfg.serials and _waiting(c, now)}
        due = guards.waiting_due(waiting, self._last_resume, now, cfg.waiting_resume_s, cfg.waiting_resume_every_s)
        if not due:
            return
        if self.gate is not None and self.gate.holder is not None:
            return                                  # 다른 세트가 추출 중 — 다음 표본에 다시 (명세 2-6)
        for s in due:
            self._last_resume[s] = now
        self._ev_cells(st, "cell_waiting", due, f"방전 중 대기 모드(측정 멈춤)가 {cfg.waiting_resume_s:.0f}초 넘게 이어짐 {due} — "
                                                f"그 셀만 깨워 0x26 으로 복귀시킨다")
        token = None
        if self.gate is not None:
            token = self.gate.try_take(self.name, wait_turn=False)
            if token is None:                       # 다른 세트가 추출 중 — 기다리지 않고 다음 표본에 다시(깨운 시각을 적지 않는다)
                for s in due:
                    self._last_resume.pop(s, None)
                return
        try:
            res = self.link.resume(due)
        finally:
            if self.gate is not None:
                self.gate.release(token)
        # 그 주소의 셀을 이 시리얼이라고 믿을 수 없어 깨우지 않았다(또는 0x26 만 보내고 끊었다) — 한 번에 묶어 남긴다
        self._ev_identity(st, res, "대기 셀 복귀")
        back = sorted(s for s, x in res.items() if x.resume_s is not None)
        left = sorted(set(due) - set(back))
        self.rec.log(f"대기 셀 복귀 {len(back)}/{len(due)}대" + (f" · 안 돌아옴 {left} (10분 뒤 다시)" if left else ""))

    def _enter_gate(self, st: CycleState):
        """추출 문 — None 이면 지금처럼 곧바로(None 을 돌려준다). 있으면 줄을 서고, 차례가 올 때까지 EXTRACT_WAIT 에서 표본 ·
        저전력 감시 · 방향 대조 · 명령을 계속 돈다(명세 2-6). 한도(SAFETY extract_wait_s)를 넘으면 이번 추출을 건너뛴다(False)."""
        if self.gate is None or self.dry:
            return None
        self.gate.queue(self.name)
        token = self.gate.try_take(self.name)
        if token is not None:
            return token
        self._phase(st, "EXTRACT_WAIT")
        held = self.gate.holder
        self.rec.log(f"추출 대기 — 세트 {held.owner if held else '?'} 가 추출 중")
        t0 = time.time(); wh, prev = 0.0, None
        while True:
            self._sleep(self.cfg.poll_s)
            wh, prev, _ = self._poll(st, wh, prev)
            self._safe(st, self._guard_power)
            token = self.gate.try_take(self.name)
            if token is not None:
                return token
            if time.time() - t0 > SAFETY["extract_wait_s"]:
                self.gate.leave(self.name)
                self._ev(st, "extract_skipped", "-", f"추출 문을 {SAFETY['extract_wait_s'] / 60:.0f}분 넘게 기다림 — 이번 사이클 추출을 "
                                                     f"건너뛰고 충전으로 간다(다음 사이클에 받는다)")
                return False

    def _watch_charging(self, st: CycleState) -> None:
        """충전 중 혼자 오르지 않는 셀을 알린다 — Dock 자리 접촉 불량 (FMEA 5.5, guards.not_charging). 셀당 사이클에 한 번."""
        cfg = self.cfg
        cur = self._per_cell_batt()
        bad = [s for s in guards.not_charging(st.charge_start_batt, cur, cfg.not_charging_median_pct,
                                              cfg.not_charging_min_pct, cfg.not_charging_full_pct)
               if s not in st.not_charging]
        if not bad:
            return
        st.not_charging.update(bad)
        which = ", ".join(f"{s} {st.charge_start_batt[s]}→{cur[s]}%" for s in bad)
        self._ev_cells(st, "cell_not_charging", bad, f"충전 중 다른 셀들은 {cfg.not_charging_median_pct}%p 넘게 올랐는데 {which} — "
                                                      f"{cfg.not_charging_min_pct}%p 도 못 오름. 그 셀의 Dock 자리(접촉) 확인")

    def _storage_fresh(self, now: float) -> dict[int, tuple[float, float]]:
        """믿을 수 있는 저장량 기록만 — 마지막으로 잰 지 cell_storage_known_max_h 안인 셀. 나머지는 '모름'(검토 F7)."""
        return guards.storage_fresh(self._storage_known, now, self.cfg.cell_storage_known_max_h * 3600)

    def _storage_pct(self, now: float) -> dict[int, float]:
        """셀별 추정 저장량(%) — 모르는 셀은 넣지 않는다. 그래서 낡은 추정으로는 빨강·Slack 이 나지 않는다."""
        cfg = self.cfg
        est = guards.storage_now(self._storage_fresh(now), now, cfg.cell_storage_rate_mb_h)
        return {s: mb / cfg.cell_storage_cap_mb * 100 for s, mb in est.items() if s in cfg.serials}

    def _ev_identity(self, st: CycleState, results: dict, what: str) -> None:
        """신원 확인에 걸린 셀들을 한 번에 묶어 남긴다 — 셀마다 빨강 한 건씩이면 대기 셀 몇 대로 Slack 이 쏟아진다(검토 F15).

        ③ 라이브 신선도에만 걸린 셀(대기 모드·저장 가득 참으로 라이브가 끊긴 셀)은 주소 충돌의 증거가 아니라 identity_stale(노랑, 기록만),
        ①②④ 주소 충돌이면 identity(빨강 · Slack). 이유는 앞의 셋만 적는다.
        """
        bad = {s: r for s, r in results.items() if r.identity}
        stale = sorted(s for s, r in bad.items() if r.identity_stale)
        hard = sorted(s for s in bad if s not in stale)

        def reasons(cells: list[int]) -> str:
            shown = "; ".join(f"{s}: {bad[s].identity}" for s in cells[:3])
            return shown + (f" 외 {len(cells) - 3}대" if len(cells) > 3 else "")

        if hard:
            self._ev_cells(st, "identity", hard, f"{what} — 주소-시리얼 불일치(주소 충돌) {len(hard)}대 {hard} · 받기·지우기를 멈췄다 · "
                                                + reasons(hard))
        if stale:
            self._ev_cells(st, "identity_stale", stale, f"{what} — 라이브가 끊겨 신원을 확인할 수 없는 셀 {len(stale)}대 {stale} · "
                                                        f"받지·지우지 않았다(대기 모드·저장 가득 참 의심 — 주소 충돌의 증거는 아님) · "
                                                        + reasons(stale))

    def _watch_storage(self, st: CycleState) -> None:
        """셀 저장량 추정이 문턱을 넘으면 알린다 (FMEA 6.5 — 2026-10-10 셀 23대가 ≈160 MB 에서 측정을 멈춘 사고).

        셀에 크기를 묻지 않는다(묻는 동안 측정이 20초 멈춘다). 추출 때 받은 크기·삭제 여부에서 시간 × 증가율로 추정한다.
        라이브가 끊긴 셀이 95% 이상이면 '가득 차 멈춤'으로 따로 알린다 — 그 셀은 복귀(0x26)로 돌아오지 않고 지워야 돌아온다.
        """
        cfg = self.cfg; now = time.time()
        pct = self._storage_pct(now)
        new, self._storage_told = guards.storage_crossings(pct, self._storage_told, cfg.cell_storage_warn_pct,
                                                           cfg.cell_storage_alarm_pct)
        for lv, kind, what in ((1, "cell_storage", "추출 뒤 삭제가 되는지 확인"),
                               (2, "cell_storage_critical", "곧 저장이 가득 차 측정이 멈춘다 — 추출 뒤 삭제 필요")):
            cells = sorted(s for s, x in new.items() if x == lv)
            if cells:
                limit = cfg.cell_storage_warn_pct if lv == 1 else cfg.cell_storage_alarm_pct
                self._ev_cells(st, kind, cells, f"셀 저장량 추정 {limit:.0f}% 넘음 ({_pcts(pct, cells)} · 상한 "
                                                f"{cfg.cell_storage_cap_mb:.0f} MB) — {what}")
        snap = self.live.snapshot()
        gaps = {s: now - snap[s].t if s in snap else math.inf for s in cfg.serials}
        full = set(guards.storage_full_cells(pct, gaps, cfg.cell_storage_alarm_pct, cfg.live_gap_alarm_s))
        fresh = sorted(full - self._storage_full_told)
        self._storage_full_told = full               # 라이브가 돌아오거나 지워지면 빠진다 — 다시 멈추면 또 알린다
        if fresh:
            self._ev_cells(st, "cell_storage_full", fresh,
                           f"라이브가 끊겼고 추정 저장량이 {cfg.cell_storage_alarm_pct:.0f}% 이상 ({_pcts(pct, fresh)}) — 저장이 가득 차 "
                           f"측정이 멈춘 것으로 보인다. 추출 뒤 삭제 필요 — 복귀(0x26)만으로는 돌아오지 않는다")

    def _watch_disk(self, st: CycleState) -> None:
        """data 폴더 드라이브의 여유를 disk_check_s 마다 본다 (FMEA 1.5). 단계가 바뀔 때만 이상을 남긴다."""
        cfg = self.cfg; now = time.time()
        if not self.owns_bench or now - self._disk_at < cfg.disk_check_s:
            return
        self._disk_at = now
        free = self._disk_free() / 1024 ** 3
        self._disk_free_gb = round(free, 1)
        lv = guards.disk_level(free, cfg.disk_warn_gb, cfg.disk_alarm_gb)
        if lv == self._disk_lv:
            return
        self._disk_lv = lv
        if lv == 0:
            self.rec.log(f"디스크 여유 회복 · {free:.1f} GB")
            return
        limit = cfg.disk_alarm_gb if lv == 2 else cfg.disk_warn_gb
        self._ev(st, "disk_critical" if lv == 2 else "disk", "-",
                 f"data 폴더 드라이브 여유 {free:.1f} GB < {limit:.0f} GB — 추출 파일(data/ftg)을 옮기거나 지운다")

    # --- 단계 ---
    def run_cycle(self) -> CycleState:
        cfg = self.cfg
        st = CycleState(cycle=self.rec.next_cycle_no(), ip_base=self._ip_total())
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
        self._dir.start_discharge(st.discharge_start, st.discharge_start_batt)
        self.rec.log(f"방전 시작 · 배터리 최저 {min(self._batts() or [0])}%")
        wh, prev = 0.0, None
        while True:
            b = self._batts()
            if (not self.dry and self._force != "charge"
                    and time.time() - st.discharge_start > SAFETY["discharge_max_h"] * 3600):
                # 방전 한도 (D3) — 플러그가 꺼진 위험 상태에는 시간 울타리가 있어야 한다. 정상 충전 길로 넘긴다
                # (RECOVER 로 보내면 셀이 돌아오는 즉시 다시 방전해 같은 일을 되풀이한다 — 명세 3-6)
                st.note = (st.note + " discharge_timeout").strip()
                self._ev(st, "discharge_timeout", "-", f"방전이 {SAFETY['discharge_max_h']:g}시간 넘게 기준선에 닿지 않음 · 최저 "
                                                       f"{min(b) if b else '모름'}% — 충전으로 넘긴다. 짝 · 셀 확인")
                self._force = "charge"
            if discharge_done(b, cfg.discharge_stop_pct) or self._force == "charge":
                st.min_batt_at_stop = min(b) if b else None
                if self._force == "charge":
                    st.note = st.note or "manual_charge"     # 켜진 플러그를 그대로 둔 경우는 _guard_discharge_plug 가 먼저 적었다
                self._force = None; break
            wh, prev, _ = self._poll(st, wh, prev)
            self._safe(st, self._guard_discharge_plug, self._watch_waiting)
            self._sleep(cfg.poll_s)
        st.discharge_end = time.time()
        st.charge_start_batt = self._per_cell_batt()
        self.rec.discharge(st.cycle, st.discharge_start_batt, st.charge_start_batt,
                           st.discharge_end - st.discharge_start, cfg.discharge_stop_pct)
        self._phase(st, "THRESHOLD")
        self.rec.log(f"기준선 도달 · 최저 {st.min_batt_at_stop}% · 방전 {(st.discharge_end - st.discharge_start)/3600:.2f}h")

        # 2) 플러그 ON → 충전 시작 확인
        self._phase(st, "PLUG_ON")
        r = self._plug("on", st); st.plug_on_at = time.time()
        self._dir.start_charge(st.plug_on_at)
        time.sleep(5)
        r = self._plug("read", st)
        if r and r.watts < cfg.plug_on_min_w:
            self.rec.event(st.cycle, st.phase, "plug", "-", f"켠 뒤 전력 {r.watts:.1f} W < {cfg.plug_on_min_w} W — 충전이 시작되지 않음"); st.events += 1
        self.rec.log(f"플러그 ON · {r.watts:.1f} W" if r else "플러그 ON (전력 미확인)")
        self._settle_power(st)

        # 3) 추출 (충전 중) — 추출 문이 있으면(다중 모드 · 짝 검증 중) 차례를 기다린다. 없으면 지금처럼 곧바로
        token = self._enter_gate(st)
        self._phase(st, "EXTRACT"); st.extract_start = time.time()
        wh, prev = 0.0, None
        if self.dry:
            self.rec.log("(모의) 추출 생략"); results = {}
        elif token is False:
            self.rec.log("추출 건너뜀 — 추출 문을 기다리다 한도를 넘었다"); results = {}
        else:
            try:
                results = self.link.extract(cfg.serials, Path(cfg.data_dir) / "ftg" / f"{st.cycle:04d}")
            finally:
                if self.gate is not None:
                    self.gate.release(token)
            self.rec.cells(st.cycle, results)
            self._storage_known = guards.storage_after_extract(self._storage_known, results, time.time())
            # 주소-시리얼 불일치 — 받기·지우기를 멈췄다(FMEA 6.8). 셀마다가 아니라 이 추출에서 한 번에 묶어 남긴다(검토 F15)
            self._ev_identity(st, results, "추출")
            for s, res in results.items():
                if res.identity:
                    continue          # 같은 일을 extract · no_resume 으로 또 남기지 않는다
                problem = extract_problem(res)
                if problem:
                    keep = " — 지우지 않음" if cfg.delete_after_extract and res.size else ""
                    self.rec.event(st.cycle, st.phase, "extract", s, problem + keep); st.events += 1
                elif cfg.delete_after_extract and res.size and not res.deleted:
                    self.rec.event(st.cycle, st.phase, "extract", s, "삭제(0x13) 응답 없음 — 셀 저장량이 줄지 않았을 수 있다"); st.events += 1
                if res.resume_s is None and res.error != "라이브 신호 없음":
                    self.rec.event(st.cycle, st.phase, "no_resume", s, f"{cfg.resume_timeout_s:.0f}초 안에 측정 미복귀"); st.events += 1
            st.extract_ok = sum(1 for r_ in results.values() if r_.ended and not r_.bad_blocks and not r_.identity)
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
            # 추출(EXTRACT) 중에는 표본이 돌지 않아 Dock 을 보지 못한다 — 추출이 끝난 뒤 여기서 이어 잡는다
            self._safe(st, self._guard_power, self._watch_charging)
            self._sleep(cfg.poll_s)
        self._phase(st, "FULL")
        recent = [w for t, w in samples if st.full_at - t <= cfg.full_flat_min * 60 and not math.isnan(w)]
        st.floor_w = sum(recent) / len(recent) if recent else None
        # 충전에 들어간 Wh = 전체 적분 − 바닥 전력 × 충전 시간 (셀 작동분 제외)
        charge_h = (st.full_at - st.plug_on_at) / 3600
        st.charge_wh = wh - (st.floor_w or 0) * charge_h
        self.rec.log(f"만충 · 충전 {charge_h*60:.0f}분 · 적분 {wh:.1f} Wh · 바닥 {st.floor_w or float('nan'):.1f} W → 충전분 {st.charge_wh:.1f} Wh")

        # 세트 구성이 바뀌었으면 여기서 — 만충 직후라 잃는 것이 없고 플러그는 켜진 채다(명세 2-8, PLUG_OFF 전에 판정)
        if self.boundary_hook is not None and self.boundary_hook(self):
            st.note = (st.note + " reconfigure").strip()
            self.rec.cycle(self._row(st))
            raise Reconfigure()

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
        """복구 대기 — 들어감 · 나옴을 holds.csv 에 남긴다(다중 세트 명세 D15, 고장일 때만 생기는 파일).
        이유는 부르는 쪽이 self._recover_why 에 둔다 — 인자를 늘리지 않는 것은 기존 검사가 이 메서드를 바꿔 끼우기 때문이다."""
        why, self._recover_why = getattr(self, "_recover_why", ""), ""
        self.rec.hold(st.cycle, "enter", "RECOVER", why)
        t0 = time.time()
        try:
            self._recover_loop(st)
        finally:
            self.rec.hold(st.cycle, "leave", "RECOVER", f"{time.time() - t0:.0f}초")

    def _recover_loop(self, st: CycleState) -> None:
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
                    self._saw(st, r)
                    if plug_on:
                        self._dir.start_charge(time.time())     # (a) 충전 쪽 하락 — 셀이 들리는 동안의 RECOVER 도 본다
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
                spaced = (self.link_guard.spaced(now) if self.link_guard is not None
                          else now - self._last_reconnect >= 120)
                if self.cfg.wifi_reconnect and not self.dry and spaced and not net.hub_reachable(self.cfg.hub_ip):
                    self._last_reconnect = now
                    if self.link_guard is not None:
                        self.link_guard.did_reconnect(now)
                    ok = net.reconnect(self.cfg.wifi_profile, self.cfg.hub_ip)
                    self.rec.event(st.cycle, st.phase, "wifi_reconnect", "-", f"재연결 {'성공' if ok else '실패'}")
                if plug_on and not asked_human and now - t0 > 30 * 60 and net.hub_reachable(self.cfg.hub_ip):
                    asked_human = True
                    self.rec.event(st.cycle, st.phase, "need_human", "-",
                                   "시험망·플러그는 정상인데 셀이 30분째 안 들림 — 셀이 꺼졌을 수 있다. Dock 버튼을 2초 이상 눌러 켠다")
            # 안전 상태(플러그 ON)인데 Dock 이 끌어 쓰지 않으면 끊었다 켠다. 시험망이 닿을 때만 읽는다 — 끊긴 채로 읽으면
            # 플러그 재시도(최대 1분 남짓)가 이 대기 루프를 붙잡는다
            if plug_on and not self.dry and (self._batts() or net.hub_reachable(self.cfg.hub_ip)):
                try:
                    r = self.plug.read(); self._last_read = r
                    self._saw(st, r)
                except Exception:
                    self._last_read = None
                self._safe(st, self._guard_power, self._watch_direction)
            self._publish(st)
            self._handle_control(st)
            self._sleep(self.cfg.poll_s)

    def precharge(self) -> None:
        """시작 전에 만충까지 충전한다 — 첫 사이클의 방전이 100% 에서 시작하도록.

        플러그를 껐다 켜서(recharge) Dock 이 충전을 확실히 시작하게 하고, 사이클과 같은 만충 판정을 쓴다.
        사이클 기록(cycles.csv)에는 넣지 않고 표본(samples)과 로그에만 'PRECHARGE' 로 남긴다.

        2026-10-11 사고 뒤 더한 두 조건 (판정은 precharge_verdict):
          (a) 시작 때 모든 셀이 precharge_skip_pct(95) 이상이면 건너뛴다 — 충전할 것이 없는데 Dock 이 재충전하지 않아 기다리기만 한다.
          (b) 전력이 바닥(≤ floor_w_max)에서 full_flat_min 분 평탄하면: 모든 셀 ≥ 95% 면 만충으로 치고, 아니면 dock_no_recharge(노랑)를
              남기고 30초 끊었다 켠다(1회). 그 뒤에도 바닥 평탄이면 사이클로 넘어간다 — 기준선 30% 방전은 95% 미만에서 시작해도 뜻이 있다.
        """
        cfg = self.cfg
        st = CycleState(cycle=self.rec.next_cycle_no(), ip_base=self._ip_total())
        self.current = st
        self.rec.begin_cycle(st.cycle)
        self._phase(st, "PRECHARGE")
        if self.dry:
            self.rec.log("(모의) 예비 충전 생략"); return
        b0 = self._batts()
        if cells_at_least(b0, cfg.precharge_skip_pct, len(cfg.serials)):
            self.rec.log(f"예비 충전 건너뜀 — 모든 셀 {min(b0)}% 이상(precharge_skip_pct {cfg.precharge_skip_pct}%) · 곧바로 사이클로")
            self.current = None
            return
        try:
            r = self.plug.recharge(); st.plug_on_at = time.time()
            self._saw(st, r)
            self.rec.log(f"예비 충전 시작 · 플러그 껐다 켬 · {r.watts:.1f} W")
        except Exception as e:
            self.rec.event(st.cycle, st.phase, "plug", "-", f"예비 충전 플러그 실패: {e}"); st.plug_on_at = time.time()
        self._dir.start_charge(st.plug_on_at)
        wh, prev = 0.0, None; samples: list[tuple[float, float]] = []
        kicked = False                               # dock_no_recharge 로 끊었다 켠 적이 있나 (1회뿐)
        while True:
            wh, prev, w = self._poll(st, wh, prev)
            if prev:
                samples.append(prev)
            now = time.time()
            b = self._batts()
            verdict = precharge_verdict(samples, b, now, cfg)
            if verdict in ("full", "floor_full"):
                if verdict == "floor_full":
                    self.rec.log(f"예비 충전 끝 — 전력이 바닥({max(w_ for _, w_ in samples[-3:]):.1f} W ≤ {cfg.floor_w_max:g} W)에서 "
                                 f"{cfg.full_flat_min:g}분 평탄하고 모든 셀 {min(b)}% ≥ {cfg.precharge_skip_pct}% — 만충으로 친다")
                break
            if verdict == "dock_no_recharge":
                low = min(b) if b else None
                if not kicked:
                    kicked = True
                    self._ev(st, "dock_no_recharge", "-",
                             f"예비 충전 중 전력이 바닥({w:.1f} W ≤ {cfg.floor_w_max:g} W)에서 {cfg.full_flat_min:g}분 평탄한데 최저 셀 "
                             f"{low if low is not None else '모름'}% < {cfg.precharge_skip_pct}% — Dock 이 재충전하지 않는다. "
                             f"{cfg.stuck_rule()[2]:.0f}초 끊었다 켠다(1회). 그래도 같으면 사이클로 넘어간다")
                    try:
                        r = self.plug.recharge(gap_s=cfg.stuck_rule()[2]); self._saw(st, r); self._last_read = r
                    except Exception as e:
                        self.rec.log(f"예비 충전 — 끊었다 켜기 실패: {e}")
                    samples = []                     # 평탄 창을 새로 센다 (아래 표본 간격 기다림은 그대로 거친다)
                else:
                    self.rec.log(f"예비 충전 끝 — 끊었다 켠 뒤에도 전력이 바닥({w:.1f} W)에서 {cfg.full_flat_min:g}분 평탄 · 최저 셀 "
                                 f"{low if low is not None else '모름'}% — Dock 이 재충전하지 않아 사이클로 넘어간다")
                    break
            if self._force == "discharge":
                # 원격 '플러그 끄기' — 충전 중이면 만충으로 치고 넘어간다(control.py). 예전에는 예비 충전이 이 명령을 보지 않아
                # 자동 복구가 1분 뒤 도로 켰다. 이제 복구는 원격으로 끈 동안 손대지 않으므로, 여기서 예비 충전을 끝내야
                # 플러그가 꺼진 채 만충 한도(charge_timeout_h)까지 기다리지 않는다.
                self._force = None
                self.rec.log("예비 충전 중 원격 플러그 끄기 — 예비 충전을 끝내고 사이클로 넘어간다")
                break
            # 자동 복구: 밖에서 꺼졌거나, 켜져 있는데 Dock 이 끌어 쓰지 않으면(≈1.5 W) 끊었다 켠다 (2026-10-08 실측 · guards.PowerWatch)
            self._safe(st, self._guard_power)
            if now - st.plug_on_at > cfg.charge_timeout_h * 3600:
                self.rec.event(st.cycle, st.phase, "charge_timeout", "-", f"예비 충전 {cfg.charge_timeout_h}h 안에 만충 안 됨")
                break
            self._sleep(cfg.poll_s)
        self.rec.log(f"예비 충전 끝 · {(time.time() - st.plug_on_at)/60:.0f}분 · {wh:.1f} Wh")
        self.current = None

    def run(self, cycles: int, precharge: bool = False) -> str:
        """감독 루프: 사이클 하나가 깨져도 기록하고 안전 상태로 둔 뒤 다음 사이클로 간다.

        끝난 이유를 돌려준다 — "done"(다 돎) · "stopped"(원격 안전 정지) · "failsafe"(연속 실패로 멈춤).
        셋 모두 플러그를 켜 두고(충전 쪽이 안전) 끝내며, 실제로 켰는지는 self.plug_on_at_exit 에 남는다
        (run_cycle.py 가 data/engine.json 에 옮겨 적고, 못 켰으면 감시자가 대신 켠다).
        원격 정지는 예비 충전·복구 대기 중에 와도 같은 길로 끝낸다.

        cfg.auto_continue(기본 True)면 cycles 는 '이번 실행에서 최소 이만큼'이고, 그 뒤에도 cycles.csv 의 누적이 시험 목표
        cfg.target_cycles(500)에 닿을 때까지 이어 간다(사이클 번호는 cycles.csv 에 이어서). --cycles 를 다 돈 순간은 target_reached 로
        기록만 하고, 누적이 progress_notify_every(100)의 배수에 닿으면 progress(노랑 · Slack 1건)를 남긴다. "done" 은 누적이 목표에 닿았을 때만.
        2026-10-11 사이클 4 가 끝난 뒤 사람이 새 시험을 띄울 때까지 4시간 21분 공회전했다 — 감시자는 다 돈 엔진을 되살리지 않는 것이 설계라서다.
        """
        done = fails = 0
        told = False                                 # target_reached 를 남겼나 (한 번만)
        every = max(0, int(self.cfg.progress_notify_every))
        milestone = (self.rec.cycles_done() // every) * every if every else 0    # 시작 때 이미 지난 진척 단계는 다시 알리지 않는다
        try:
            if precharge:
                try:
                    self.precharge()
                except (StopRequested, PairMismatch):
                    raise
                except Exception as e:
                    st = self.current or CycleState(cycle=self.rec.next_cycle_no())
                    self.rec.event(st.cycle, "PRECHARGE", "aborted", "-", f"예비 충전 중단: {e}")
                    self._recover_why = f"예비 충전 중단: {e}"
                    self._recover(st)
            while True:
                total = self.rec.cycles_done()
                if every and total // every * every > milestone:
                    milestone = total // every * every
                    self._progress(milestone)
                if self.cfg.auto_continue and total >= self.cfg.target_cycles:
                    self.rec.log(f"시험 목표 {self.cfg.target_cycles}사이클에 닿았다(누적 {total}) — 플러그 ON 으로 끝낸다")
                    break
                if done >= cycles:
                    if not self.cfg.auto_continue:
                        break
                    if not told:
                        told = True
                        self._target_reached(cycles, total)
                try:
                    self.run_cycle()
                    done += 1; fails = 0
                except (KeyboardInterrupt, StopRequested, PairMismatch, Reconfigure):
                    raise
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
                        self.plug_on_at_exit = self._safe_end(st)
                        return "failsafe"
                    self._recover_why = detail
                    self._recover(st)
        except Reconfigure:
            self.rec.log("세트 구성이 바뀌어 경계에서 끝낸다 — 플러그는 켜진 채(만충 직후)")
            self.plug_on_at_exit = self.plug_known
            return "reconfigure"
        except PairMismatch as e:
            # 짝 이상 (명세 3-6) — 다른 세트 플러그는 _pair_fault 가 켜는 중이다. 자기 플러그를 켜고 failsafe 로 끝낸다
            st = self.current or CycleState(cycle=self.rec.next_cycle_no())
            self.rec.log(f"짝 이상 — 등록된 모든 플러그를 켜고 멈춘다 (failsafe): {e}")
            st.note = (st.note + " pair_mismatch").strip()
            self.rec.cycle(self._row(st))
            self.plug_on_at_exit = self._safe_end(st)
            for th in self._fault_threads:
                th.join(timeout=180)
            return "failsafe"
        except StopRequested as e:
            st = self.current or CycleState(cycle=self.rec.next_cycle_no())
            hold = getattr(e, "why", None)
            self.rec.log(f"HOLD 요청 — {hold} · 플러그 ON 으로 두고 멈춘다" if hold else "원격 정지 요청 — 플러그 ON 으로 두고 멈춘다")
            self.plug_on_at_exit = self._safe_end(st)
            st.note = (st.note + (" hold" if hold else " stopped_by_user")).strip()
            if hold:
                self.rec.event(st.cycle, st.phase, "set_hold", "-", f"{hold} · 플러그 ON")
            else:
                self.rec.event(st.cycle, st.phase, "stopped", "-", "원격 안전 정지 · 플러그 ON")
            self.rec.cycle(self._row(st)); self._phase(st, "STOPPED")
            return "stopped"
        # 다 돈 뒤에도 안전 상태 = 플러그 ON. 마지막 사이클이 PLUG_OFF 로 끝나 그대로 두면 셀이 끝없이 방전되고,
        # 다 꺼지면 사람이 Dock 버튼을 눌러야 다시 켜진다.
        st = self.current or CycleState(cycle=max(1, self.rec.next_cycle_no() - 1))
        self.plug_on_at_exit = self._safe_end(st)
        self._phase(st, "DONE")
        return "done"

    def _target_reached(self, cycles: int, total: int) -> None:
        """이번 실행의 --cycles 를 다 돌았는데 시험 목표(target_cycles)까지 계속 돈다 — run.log 한 줄 + 이상 target_reached(노랑, 기록만). 실행에 한 번."""
        st = self.current or CycleState(cycle=max(1, self.rec.next_cycle_no() - 1))
        self.rec.log(f"이번 실행 {cycles}사이클 완료 · 누적 {total}/{self.cfg.target_cycles} — 멈추지 않고 사이클 {self.rec.next_cycle_no()} 을 이어 간다")
        self._ev(st, "target_reached", "-", f"이번 실행 {cycles}사이클 완료 · 누적 {total}/{self.cfg.target_cycles} — 목표까지 계속 돈다 "
                                             f"(멈추려면 결과판 '안전 정지' · 플러그 ON 으로 멈춘다)")

    def _progress(self, total: int) -> None:
        """누적 사이클이 progress_notify_every 의 배수에 닿았다 — 이상 progress(노랑 · Slack 1건) "목표 500 중 100사이클 완료 · 누적 방전 평균 Nh"."""
        from .record import avg_discharge_h
        st = self.current or CycleState(cycle=max(1, self.rec.next_cycle_no() - 1))
        avg = avg_discharge_h(self.rec.cycle_rows())
        self._ev(st, "progress", "-", f"목표 {self.cfg.target_cycles} 중 {total}사이클 완료 · 누적 방전 평균 "
                                      f"{f'{avg:.1f}h' if avg is not None else '-'}")

    def hold(self, why: str, phase: str = "HOLD") -> None:
        """HOLD — 플러그를 켜 두고 지킨다(다중 모드, 명세 2-3 · D10). 조정자가 멈춤(_halt)을 세울 때까지 돈다.

        지금의 복구 대기에서 '셀이 돌아오면 끝냄'을 뺀 고리다. 저전력 복구 한도는 시간당 다시 채우고(하나의 긴 단계라 단계당 3번이면
        3번 뒤 켜짐 · 1.5 W 로 방치된다 — A-H1), 셀 중앙값이 90 % 아래면 한 시간에 한 번 끊었다 켠다(Dock 은 만충 뒤 켜진 채로는
        충전을 다시 시작하지 않는다). 명령(플러그 켜기 · 끄기)은 받는다 — 세트 정지는 이미 HOLD 라 아무 일도 하지 않는다.
        """
        st = self.current or CycleState(cycle=max(1, self.rec.next_cycle_no() - 1))
        self.current = st
        self._phase(st, phase)
        self.rec.hold(st.cycle, "enter", phase, why)
        t0 = refill = kick = time.time()
        try:
            while True:
                now = time.time()
                if now - refill >= 3600:
                    self._power = guards.PowerWatch(*self.cfg.stuck_rule()); refill = now
                r = self._plug("read", st)
                if r is not None:
                    st.last_on, st.last_w = r.on, r.watts
                self._safe(st, self._guard_power, self._watch_disk)
                b = list(self._per_cell_batt().values())
                if (not self.dry and b and r is not None and r.on and now - kick >= 3600
                        and sorted(b)[len(b) // 2] < 90 and self._force != "discharge"):
                    kick = now
                    try:
                        rr = self.plug.recharge(gap_s=self.cfg.stuck_rule()[2]); self._saw(st, rr)
                        self.rec.log(f"HOLD — 셀 중앙값 {sorted(b)[len(b) // 2]}% 라 끊었다 켬 · {rr.watts:.1f} W")
                    except Exception as e:
                        self.rec.log(f"HOLD — 끊었다 켜기 실패: {e}")
                self._publish(st)
                try:
                    self._handle_control(st)
                except StopRequested:
                    pass                                   # 이미 HOLD 다
                self._sleep(self.cfg.poll_s)
        except KeyboardInterrupt:
            pass
        finally:
            self.rec.hold(st.cycle, "leave", phase, f"{time.time() - t0:.0f}초")

    def _safe_end(self, st: CycleState | None = None) -> bool | None:
        """끝낼 때 플러그를 켜 둔다 — stuck_gap_s(30초) 끊었다 켜서 Dock 이 충전을 확실히 다시 시작하게(10초로는 안 됐다, 2026-10-08).
        켰으면 True, 못 켰으면(또는 끝내 꺼짐으로 읽히면) False, 모의면 None.

        켠 것으로 끝내지 않고 확인한다(검토 F3): stuck_s(60초) 뒤 전력을 다시 읽어, 꺼짐이거나 stuck_w(10 W) 미만이면 다시 끊었다 켠다
        (stuck_max 번까지). 다 써도 안 되면 dock_power(빨강)를 남긴다. 기다리는 동안 심박을 찍어 감시자가 '멈춤'으로 끝내지 않게 한다.
        끝난 뒤의 플러그는 감시자가 이어서 지킨다(supervisor 의 plug_guard).
        """
        if self.dry:
            self.rec.log("(모의) 끝 — 플러그 ON 생략")
            return None
        stuck_w, stuck_s, gap_s, tries = self.cfg.stuck_rule()
        try:
            r = self.plug.recharge(gap_s=gap_s)
            self._saw(st, r)                               # 결과판의 플러그 칸이 끝난 뒤 상태를 보이게
            self.rec.log(f"안전 상태로 끝냄: 플러그 ON · {r.watts:.1f} W ({gap_s:.0f}초 끊었다 켬)")
        except Exception as e:
            self.rec.log(f"플러그 ON 실패: {e}")
            return False
        cycle = st.cycle if st is not None else max(1, self.rec.next_cycle_no() - 1)
        phase = st.phase if st is not None else "-"
        on = r.on
        for n in range(tries + 1):
            self.rec.beat()
            time.sleep(stuck_s)
            self.rec.beat()
            try:
                r = self.plug.read()
                self._saw(st, r)
            except Exception as e:
                self.rec.log(f"끝낸 뒤 플러그 확인을 못 함 ({type(e).__name__}: {e}) — 감시자가 이어서 지킨다")
                return on
            on = r.on
            if guards.power_stuck(r.on, r.watts, stuck_w) is not True:
                self.rec.log(f"끝낸 뒤 {stuck_s:.0f}초 확인: 플러그 켜짐 · {r.watts:.1f} W — Dock 이 충전 중")
                return True
            why = "꺼짐" if r.on is False else f"{r.watts:.1f} W"
            if n == tries:
                self.rec.event(cycle, phase, "dock_power", "-",
                               f"끝낸 뒤 플러그를 {tries}번 더 끊었다 켰는데도 Dock 이 전력을 끌어 쓰지 않음({why}) — Dock 전원·어댑터·케이블 확인")
                return on
            self.rec.event(cycle, phase, "plug", "-", f"끝낸 뒤 {stuck_s:.0f}초 확인: 플러그 {why} — {gap_s:.0f}초 끊었다 켬 ({n + 1}/{tries})")
            try:
                r = self.plug.recharge(gap_s=gap_s)
                self._saw(st, r)
                on = r.on
            except Exception as e:
                self.rec.log(f"끝낸 뒤 다시 켜기 실패: {e}")
        return on


def _target(cmd: dict) -> object:
    """명령이 가리키는 세트 — 로컬 control.json 은 "set", 클라우드 bench_command 는 "set_id". 없으면 None(지정 없음)."""
    v = cmd.get("set") if "set" in cmd else cmd.get("set_id")
    return None if v is None or v == "" else v


def _other_groups(cfg: Config) -> dict:
    """(d) 교차 감시의 무리 — 세트 1 이 아닌 등록 세트마다 시리얼(세트 1 의 실효 시리얼은 뺀다). 읽을 수 없는 줄은 건너뛴다."""
    mine = set(cfg.serials)
    out: dict = {}
    for s in cfg.sets if isinstance(cfg.sets, list) else []:
        if not isinstance(s, dict) or s.get("id") == 1:
            continue
        try:
            ser = [x for x in expand_serials(s.get("serials", [])) if x not in mine]
        except ValueError:
            continue
        if ser:
            out[s.get("id")] = ser
    return out


def _ts(t: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""


def _waiting(c, now: float) -> bool:
    """지금 대기 모드인가 — 0x16 은 오는데(5초 안) 0x09 가 끊겼다. 0x16 마저 끊긴 셀(꺼짐)은 대기가 아니다."""
    return c.waiting and now - c.t_wait <= 5.0


def _pcts(pct: dict[int, float], cells: list[int], n: int = 8) -> str:
    s = ", ".join(f"{x} {pct[x]:.0f}%" for x in cells[:n])
    return s + (f" 외 {len(cells) - n}대" if len(cells) > n else "")
