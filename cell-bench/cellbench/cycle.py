"""사이클 상태기계 — 방전 → 기준선 → 플러그 ON → 추출 → 충전 → 만충 → 플러그 OFF → (반복)

판정 함수(is_full, integrate_wh, discharge_done)는 순수 함수라 장비 없이 단위 검사한다.
CycleRunner 는 그 판정에 따라 플러그와 셀을 움직이고 Recorder 에 남긴다.
안전망(FMEA P4 — Dock 저전력 · 방전 중 켜진 플러그 · 셀 저장량 · 접촉 불량 · 대기 모드 셀 · 디스크)의 판정은
guards.py 의 순수 함수이고, 여기에는 그것을 부르는 얇은 배선(_guard_* · _watch_*)만 있다.
"""
from __future__ import annotations

import math
import shutil
import statistics
import threading
import time
import traceback
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import guards, hr, net
from .cells import CellLink, LiveListener, extract_problem
from .cloud import SCHEMA_AWARE
from .control import COMMANDS, ControlInbox, StopRequested, command_expired
from .config import SAFETY, Config, dock_problems, dock_settings, expand_serials, other_plugs, registered_serials, set_entry
from .dock import Dock, WakeResult
from .hr import STOP_TEXT
from .plug import Plug, ensure_on
from .record import Recorder, recent_cell_events

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


class DockStallFail(PairMismatch):
    """Dock 충전 멈춤(자동 복구 규칙 A)을 1시간 한도까지 끊었다 켰는데도 셀이 사건 시작보다 내려가 있다. 짝 이상과 같은 안전 동작 길 —
    등록된 모든 플러그를 켜고 모든 운전 세트를 세우고 엔진을 failsafe 로 끝낸다. 이름과 문구만 다르고, engine.json 의 why 가 dock_stall 이다."""


class PartialOff(Exception):
    """세트 일부 꺼짐 (자동 복구 규칙 D-2) — 일부 셀이 15분 넘게 0x09 · 0x16 모두 안 들리고 나머지는 들린다. 표본 고리에서 올라와
    감독 루프(run)가 받아 리셋하거나(스위치 켜짐) 그 세트만 세트 정지 + 사람 호출로 둔다(_partial). 다른 세트는 계속 돈다."""

    def __init__(self, st: "CycleState", silent: list[int], alive: list[int]):
        super().__init__(f"셀 {len(silent)}대가 안 들린다: {silent[:8]}")
        self.st, self.silent, self.alive = st, silent, alive


RESET_OFF_TRIES = 3     # 리셋에서 플러그가 꺼짐으로 읽힐 때까지 다시 끄는 횟수 — 넘으면 세트 정지 + 사람 호출
STALL_PHASES = ("PRECHARGE", "EXTRACT_WAIT", "CHARGE", "RECOVER", "HOLD")   # 규칙 A 를 보는 충전 쪽 단계 — 방전 · 추출에는 배선하지 않는다(I1)

PHASES = ["PRECHARGE", "DISCHARGE", "THRESHOLD", "PLUG_ON", "EXTRACT", "CHARGE", "FULL", "PLUG_OFF"]
PHASE_KO = {"PRECHARGE": "예비 충전", "PLUG_ON": "플러그 켜기", "CHARGE": "충전", "RECOVER": "복구 대기", "RESET": "리셋"}


# ---------- 순수 판정 ----------

def discharge_done(batteries: list[int], stop_pct: int) -> bool:
    """24셀 중 하나라도 기준선 이하면 방전 끝. 셀이 하나도 안 들리면 False (판단 보류)."""
    return bool(batteries) and min(batteries) <= stop_pct


def integrate_wh(prev_w: float, cur_w: float, dt_s: float) -> float:
    """사다리꼴 적분. 어느 한쪽이 NaN 이면 0 (표본 하나 놓친 것으로 본다)."""
    if math.isnan(prev_w) or math.isnan(cur_w) or dt_s <= 0:
        return 0.0
    return (prev_w + cur_w) / 2 * dt_s / 3600


FULL_HOLD_PCT = 99      # 이번 충전에서 모든 셀이 한 번 100% 를 찍은 뒤에는 이 % 까지 만충으로 받아 준다


def is_full(samples: list[tuple[float, float]], batteries: list[int], now: float,
            flat_min: float, tol_w: float, require_all_100: bool, expected_cells: int,
            seen_all_100: bool = False) -> bool:
    """만충 = 최근 flat_min 분 동안 전력 변동이 tol_w 안 + (옵션) 모든 셀 100%.

    samples: (시각, W) 목록. 평평함을 보려면 그 구간 안에 표본이 3개 이상 있어야 한다.
    seen_all_100: 이번 충전에서 모든 셀이 이미 한 번 100% 를 보고했나. 그랬으면 셀 기준을 FULL_HOLD_PCT 로 낮춘다.
    Y Dock 은 만충 뒤 전력이 수십 분에 걸쳐 천천히 내려가는데, 그 사이 셀 하나가 99% 로 떨어지면 '모두 100% + 평탄'이
    끝내 겹치지 않아 충전 한도로 끝났다(2026-11-18 · 19 세트 1 140 · 141회, 셀은 2시간 만에 모두 100%).
    지난 기록에 다시 돌려 보면 정상 사이클(세트 1 · 2)의 만충 시각은 그대로이고 140 · 141회는 193 · 208분에 잡힌다.
    """
    if require_all_100:
        need = FULL_HOLD_PCT if seen_all_100 else 100
        if len(batteries) < expected_cells or any(b < need for b in batteries):
            return False
    window = [w for t, w in samples if now - t <= flat_min * 60 and not math.isnan(w)]
    if len(window) < 3:
        return False
    if samples and now - samples[0][0] < flat_min * 60:
        return False                       # 아직 flat_min 분이 지나지 않았다
    return (max(window) - min(window)) <= tol_w


def floor_flat(samples: list[tuple[float, float]], now: float, flat_min: float, floor_w_max: float, poll_s: float) -> bool:
    """최근 flat_min 분 동안 전력이 줄곧 바닥(≤ floor_w_max)이었나 — Dock 이 충전하지 않는 상태(만충 뒤 셀 작동분만 흐른다).

    is_full 과 같은 창 규칙(표본 3개 이상 · flat_min 분이 지났어야 한다)이고, 변동폭 대신 '전부 바닥 이하'를 본다.
    창 안의 유효 표본이 기대치(flat_min × 60 / poll_s)의 절반에 못 미치면 False — 플러그 읽기가 거의 다 실패한(NaN) 창에서
    남은 두세 표본만으로 '평탄'을 선언하지 않게(검토 F9).
    """
    window = [w for t, w in samples if now - t <= flat_min * 60 and not math.isnan(w)]
    need = max(3, (flat_min * 60 / poll_s) / 2 if poll_s and poll_s > 0 else 3)
    if len(window) < need:
        return False
    if samples and now - samples[0][0] < flat_min * 60:
        return False
    return max(window) <= floor_w_max


def cells_at_least(batteries: list[int], pct: int, expected_cells: int) -> bool | None:
    """모든 셀이 들리고(expected_cells 대) 전부 pct 이상인가.

    들린 셀이 기대 수보다 모자라면(한 대라도 안 들리거나 하나도 안 들림) None — 판단 보류. 모자란 셀이 몇 % 인지 모르므로 '충분히 찼다'도
    '덜 찼다'도 말할 수 없다(검토 F1 — 예전에는 False 라서 셀이 안 들리는 동안 dock_no_recharge 로 흘러 플러그를 끊었다 켰다).
    """
    if len(batteries) < expected_cells:
        return None
    return all(b >= pct for b in batteries)


def precharge_verdict(samples: list[tuple[float, float]], batteries: list[int], now: float, cfg: Config) -> str | None:
    """예비 충전을 끝낼 수 있나 (2026-10-11 사고 — 셀 96~97% 에서 Dock 이 다시 충전하지 않아 '모든 셀 100%' 가 영영 오지 않았다).

      "full"             사이클과 같은 만충 판정(is_full)
      "floor_full"       전력이 바닥에서 full_flat_min 분 평탄 + 모든 셀 ≥ precharge_skip_pct — 더 기다려도 소용없고 충분히 찼다
      "dock_no_recharge" 바닥 평탄인데 들린 셀(모든 셀) 중 precharge_skip_pct 미만이 있다 — Dock 이 재충전하지 않는다(기존 저전력 복구는 10 W 아래만 본다)
      None               아직 — 바닥 평탄이 아니거나, 셀이 다 들리지 않아 판단을 보류한다(다음 표본에 다시 본다. 오래 안 들리면 blind 경로가 잡는다)
    정규 충전 단계의 만충 판정은 이 함수를 쓰지 않는다 — 거기서는 Dock 이 충전 중이다.
    """
    if is_full(samples, batteries, now, cfg.full_flat_min, cfg.full_flat_tol_w, cfg.full_requires_all_100, len(cfg.serials)):
        return "full"
    if not floor_flat(samples, now, cfg.full_flat_min, cfg.floor_w_max, cfg.poll_s):
        return None
    enough = cells_at_least(batteries, cfg.precharge_skip_pct, len(cfg.serials))
    if enough is None:
        return None
    return "floor_full" if enough else "dock_no_recharge"


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
    hr_tally: dict = field(default_factory=dict)            # 방전 구간 심박 표본 — 셀 → [표본, 라이브 들림, 심박 > 0] (짝 있는 셀만, hr_####.csv)


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
        self._charge_timeouts = 0                    # 정규 충전(CHARGE)이 charge_timeout_h 안에 만충되지 못한 연속 사이클 수 — 만충되면 0 (검토 F4)
        self._precharge_start_min: int | None = None # 예비 충전을 시작할 때의 최저 셀 % — 신호등이 체류 기대를 잔량에 비례시킨다(검토 F2). 예비 충전 밖에서는 None
        self.plug_on_at_exit: bool | None = None     # run() 이 끝내며 플러그를 켜 두었나 (engine.json 에 남는다)
        self.end_why: str | None = None              # failsafe 로 끝난 이유 — dock_stall · pair_mismatch · charge_timeout_streak · failures (engine.json why)
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
        # Dock 충전 멈춤 (자동 복구 규칙 A) — 한도(1시간 stuck_max 번)는 실행기에 하나라 단계가 바뀌어도 이어 센다
        self._stall = guards.StallWatch(SAFETY["stall_window_s"], SAFETY["stall_drop_pct"], cfg.stuck_rule()[0], SAFETY["stall_w_max"],
                                        cfg.precharge_skip_pct, SAFETY["min_cells"], cfg.stuck_rule()[3])
        self._reg_groups = _other_groups(cfg)         # 등록된 다른 세트의 시리얼 — now.json metrics 의 registered_heard 재료
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
        # --- 심박 (bench.json 세트 줄의 hr_pairs 가 있는 세트만 — cellbench/hr.py) ---
        self._hr_last_try: dict[int, float] = {}     # 셀별 마지막 자동 재연결(0x20) 시각
        self._hr_tries: dict[int, int] = {}          # 셀별 심박이 돌아오지 않은 채 이어진 재연결 횟수 (심박이 오면 0)
        self._hr_fail_told: set[int] = set()         # hr_repair_fail 을 이미 남긴 셀
        # --- 세트 전체 꺼짐 (자동 복구 규칙 D-1) — 셀별 마지막으로 들은 시각(0x09 · 0x16)과 사라지기 전 마지막 잔량. 기록은 남기지 않는다 ---
        self._seen: dict[int, float] = {s: self.started_at for s in cfg.serials}   # 한 번도 안 들린 셀은 엔진 시작부터 센다
        self._last_batt: dict[int, int] = {}
        self._dock_factory = lambda: Dock(self.cfg, int(self.name))   # 이 세트의 Dock — 검사가 가짜로 바꾼다. 스위치가 꺼져 있으면 부르지 않는다(I10)
        self._pre_after_recover = False              # 복구 대기에서 나온 뒤 예비 충전부터 할까 (G7 — _recover_back 이 정한다)
        # 자동 복구가 진행 중이면 {what: 무엇을 기다리나, since} — now.json metrics.auto_recovery. 사람을 불렀거나 끝나면 None(칸이 사라진다).
        # 신호등은 이것이 있는 동안 '셀 수신 전부 끊김' · '셀 잔량 0 %' 를 빨강 대신 노랑으로 낸다(자동 복구 규칙 E — 사람 호출은 자동 수단 뒤)
        self._auto: dict | None = None
        self._off_known: str | None = None           # 다음 복구 대기에 넘기는 '모두 꺼짐 확인'의 근거 — 리셋이 확인했다(망 증거 대신)
        # --- 세트 일부 꺼짐 (자동 복구 규칙 D-2) ---
        self._partial_due: PartialOff | None = None  # 아직 다루지 못한 일부 꺼짐 — 감독 루프의 다음 바퀴가 다룬다(예외 처리 안에서 다시 올리지 않게)
        # 셀별 마지막 '충전 안 오름'(cell_not_charging) 시각 — 접촉 고장 셀은 리셋하지 않는다. 엔진이 자주 다시 떠도(자동 재개 · 조용한 재시도)
        # 잊지 않게 시작 때 이 세트의 events.csv 지난 24시간에서 이어받는다
        self._not_charging_t: dict[int, float] = recent_cell_events(Path(rec.dir) / "events.csv", "cell_not_charging",
                                                                    time.time() - 86400)
        self._audible_since: float | None = None     # 이 세트 셀이 한 대라도 들리기 시작한 시각(모두 안 들리면 None) — 망이 오래 끊겼다
                                                     # 돌아올 때 늦게 붙는 셀을 일부 꺼짐으로 읽지 않게, 들린 지 partial_off_s 가 지나야 판정한다

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
                       "age": round(now - c.t, 1), "waiting": c.waiting, **self._hr_times(s, c, now)}
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
        out = {
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
            # 예비 충전 시작 때의 최저 셀 % (예비 충전 중에만 값, 밖에서는 null) — '프로그램' 차선이 PRECHARGE 체류 기대를 이것에 비례시킨다(검토 F2)
            "precharge_start_min_pct": self._precharge_start_min,
        }
        out.update(self._registered_heard(snap, now))
        if self._auto:                # 자동 복구 중일 때만 — 정상 운전의 now.json 은 지금과 같다(I7)
            out["auto_recovery"] = dict(self._auto)
        if self._hr_pairs():          # 짝 표가 있는 세트만 — 없는 세트의 now.json 은 지금과 같다
            out["hr_repair_fail"] = sorted(self._hr_fail_told)
        return out

    # --- 심박 (cellbench/hr.py) ---
    def _hr_pairs(self) -> dict[int, dict]:
        """이 실행기 세트의 짝 표 {셀: {board, no, hub, port}} — 없으면 {}. 이름(세트 id)은 다중 모드에서 생성 뒤에 바뀌므로 그때그때 찾는다."""
        try:
            return hr.pairs_of(set_entry(self.cfg, int(self.name)))
        except (TypeError, ValueError):
            return {}

    def _hr_times(self, serial: int, c, now: float) -> dict:
        """now.json 셀 칸에 붙이는 심박 시간 — 라이브가 이어진 초(up_s) · 심박 연속 0 초(hr0_s). 짝 있는 셀만(신호등 '심박' 차선의 재료)."""
        if serial not in self._hr_pairs():
            return {}
        up, zero = hr.times(c, now)
        return {"up_s": None if up is None else round(up), "hr0_s": round(zero)}

    def _hr_tally(self, st: CycleState, now: float) -> None:
        """방전 표본 하나 — 짝 있는 셀마다 표본 수 · 라이브가 들린 표본 · 심박 > 0 인 표본을 센다 (사이클 끝의 hr_####.csv 재료)."""
        pairs = self._hr_pairs()
        if not pairs:
            return
        snap = self.live.snapshot()
        for s in pairs:
            c = snap.get(s)
            live = c is not None and now - c.t <= 5 and not c.waiting
            t = st.hr_tally.setdefault(s, [0, 0, 0])
            t[0] += 1; t[1] += live; t[2] += bool(live and c.hr > 0)

    def _write_hr_ratio(self, st: CycleState) -> None:
        pairs = self._hr_pairs()
        rows = [{"serial": s, "board": pairs.get(s, {}).get("board", ""), "samples": t[0], "live": t[1], "hr_ok": t[2]}
                for s, t in sorted(st.hr_tally.items())]
        self.rec.hr_ratio(st.cycle, rows)

    def _hr_dir(self) -> Path:
        """감시자가 hr_boards.json 을 쓰는 data 폴더 — 세트 k 의 실행기는 data/set{k} 를 쓰므로 그 위다(config.set_dir)."""
        d = Path(self.cfg.data_dir)
        return d if str(self.name) == "1" else d.parent

    def _watch_hr(self, st: CycleState) -> None:
        """심박 자동 재연결 — 짝 보드는 켜져 있고 연결 0 인데 셀 심박이 hr_repair_after_s(10분) 넘게 0 이면 그 셀에 짝 보드 이름(0x20)을
        다시 보내고 0x26 으로 측정에 돌려보낸다 (CellLink.set_ble_id). 조건은 hr.repair_due — 추출 중 · 다른 세트가 추출 문을 잡은 동안은
        하지 않고, 같은 셀은 hr_repair_every_s(30분)에 한 번. 시도마다 이상 hr_repair. hr_repair_fail_max(3)번 보내도 심박이 돌아오지 않아
        다음 시도 차례가 오면 그 셀에는 더 보내지 않고 멈춘다 — hr_repair_fail 한 번 + 신호등 '심박' 노랑(사람 확인). 심박이 돌아오면 풀린다. 셀이 재부팅하므로 시도마다 live_gap 이 1건 남는 것이 정상이다(2026-10-15 실측 16~25초).
        대기 셀 복귀(_watch_waiting)를 본보기로 했다 — 보내는 동안(최대 약 2분) 표본이 쉰다."""
        pairs = self._hr_pairs()
        if not pairs or self.dry or self.link is None or not hasattr(self.link, "set_ble_id") or st.phase == "RESET":
            return                                  # 리셋 방전에서는 심박 재연결을 하지 않는다 — 셀이 곧 꺼진다
        cfg, now = self.cfg, time.time()
        th = hr.thresholds(cfg)
        snap = self.live.snapshot()
        cells = {}
        for s in pairs:
            c = snap.get(s)
            if c is None:
                continue
            if c.hr > 0:                             # 심박이 돌아왔다 — 실패 횟수를 지운다
                self._hr_tries.pop(s, None); self._hr_fail_told.discard(s)
            zero = hr.times(c, now)[1]
            cells[s] = {"hr": c.hr, "age": now - c.t, "waiting": _waiting(c, now), "hr0_s": zero}
        if not th.get("hr_repair"):
            return
        from .control import read_json
        boards = read_json(self._hr_dir() / hr.BOARDS_FILE)
        busy = self.gate is not None and self.gate.holder is not None
        cand = hr.repair_due(pairs, cells, boards, now, self._hr_last_try, th, st.phase, busy, cfg.live_gap_alarm_s)
        due = hr.repair_due(pairs, cells, boards, now, self._hr_last_try, th, st.phase, busy, cfg.live_gap_alarm_s, self._hr_tries)
        for s in sorted(set(cand) - set(due) - self._hr_fail_told):
            # 앞의 시도들이 심박을 되돌리지 못했다(심박이 돌아오면 횟수가 0 이 된다) — 이 셀은 멈추고 한 번만 알린다
            self._hr_fail_told.add(s)
            p = pairs[s]
            self._ev(st, "hr_repair_fail", s, f"심박 자동 재연결 {self._hr_tries.get(s, 0)}번 연속 — 심박이 돌아오지 않음. "
                                              f"{STOP_TEXT}. 짝 보드 {p['board']}(허브 {p.get('hub') or '-'}-{p.get('port') or '-'})와 "
                                              f"셀을 현장에서 확인 (보드 뽑았다 꽂기 · 셀 재부팅)")
        if not due:
            return
        due = due[:cfg.extract_batch]
        token = None
        if self.gate is not None:
            token = self.gate.try_take(self.name, wait_turn=False)
            if token is None:                       # 그사이 다른 세트가 추출 문을 잡았다 — 다음 표본에 다시
                return
        for s in due:
            self._hr_last_try[s] = now
            self._hr_tries[s] = self._hr_tries.get(s, 0) + 1
        try:
            res = self.link.set_ble_id({s: pairs[s]["board"] for s in due})
        finally:
            if self.gate is not None:
                self.gate.release(token)
        self._ev_identity(st, res, "심박 재연결")
        for s in due:
            r = res.get(s)
            p = pairs[s]
            ok = bool(r and r.ble_ok)
            where = f"허브 {p['hub']}-{p['port']}" if p.get("hub") else "허브 -"
            self._ev(st, "hr_repair", s, f"심박 {cells[s]['hr0_s'] / 60:.0f}분째 0 · 짝 보드 {p['board']}({where}) 켜짐 · 연결 0 — "
                                         f"이름을 다시 보냄 ({self._hr_tries[s]}회째) · "
                                         + ("셀 응답 받음" if ok else f"셀 응답 없음 ({(r.error if r else None) or '이유 모름'})")
                                         + (f" · 측정 복귀 {r.resume_s:.0f}초" if r and r.resume_s is not None else ""))

    def _registered_heard(self, snap: dict, now: float) -> dict:
        """세트 1 이 아닌 등록 세트별 들린 셀 수(registered_heard)와 잔량 중앙값(registered_batt_med) — 감시자의 세트 지키기가 읽는다.
        등록된 세트의 셀은 now.json 의 heard(미등록 감지)에서 빠지므로 운전하지 않는 세트가 조용히 꺼져도 어디에도 안 보였다(10-13 사고).
        세트 1 만 등록이면 칸을 더하지 않는다 — 단일 세트 now.json 은 지금과 같다."""
        if not self.owns_bench or not self._reg_groups:
            return {}
        heard, med = {}, {}
        for gid, ser in self._reg_groups.items():
            cells = [snap[s] for s in ser if s in snap and now - snap[s].t <= self.cfg.heard_window_s]
            heard[str(gid)] = len(cells)
            bs = [c.battery for c in cells if guards.batt_ok(c.battery)]
            med[str(gid)] = float(statistics.median(bs)) if bs else None
        return {"registered_heard": heard, "registered_batt_med": med}

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

    def _note_cells(self, now: float) -> None:
        """셀별 마지막으로 들은 시각(0x09 · 0x16 — guards.heard_t)과 마지막 잔량을 기억한다. 세트 전체 꺼짐(D-1)의 재료이고 기록은 남기지 않는다.
        시각이 거꾸로 가지 않게 큰 값만 받는다."""
        snap = self.live.snapshot()
        audible = False
        for s in self.cfg.serials:
            c = snap.get(s)
            if c is None:
                continue
            t = guards.heard_t(c)
            if t > self._seen.get(s, 0.0):
                self._seen[s] = t
            audible = audible or now - t <= 5
            if now - getattr(c, "t", 0.0) <= 5 and not getattr(c, "waiting", False) and guards.batt_ok(c.battery):
                self._last_batt[s] = c.battery
        self._audible_since = (self._audible_since or now) if audible else None

    def _watch_partial(self, st: CycleState) -> None:
        """세트 일부 꺼짐 (자동 복구 규칙 D-2, guards.partial_off) — 일부 셀이 0x09 · 0x16 모두 15분(SAFETY partial_off_s) 넘게 안 들리고
        나머지는 들리면 PartialOff 를 올린다. 저장이 가득 차 신호만 멈춘 셀(추정 ≥ cell_storage_alarm_pct)은 꺼진 셀로 치지 않는다(가7).
        리셋 · 세트 정지 · 복구 대기에서는 보지 않는다(그 고리들이 따로 본다). 한 번도 안 들린 셀은 엔진 시작부터 센다."""
        if self.dry or st.phase in ("RESET", "HOLD", "RECOVER"):
            return
        now = time.time()
        self._note_cells(now)
        if self._audible_since is None or now - self._audible_since < SAFETY["partial_off_s"]:
            return                                  # 세트가 들리기 시작한 지 15분이 안 됐다 — 망이 끊겼다 돌아오는 중일 수 있다
        pct = self._storage_pct(now)
        silent, alive = guards.partial_off(self._seen, now, SAFETY["partial_off_s"], SAFETY["wake_silent_s"],
                                           skip={s for s, p in pct.items() if p >= self.cfg.cell_storage_alarm_pct})
        if silent:
            raise PartialOff(st, silent, alive)

    def _silent_s(self, now: float) -> float:
        """이 세트 셀이 하나도 안 들린 지 몇 초인가(0x09 · 0x16 모두). 라이브를 지금 다시 보고 잰다 — 보내기 직전의 확인(불변식 I8)도 이것이다."""
        self._note_cells(now)
        return now - max(self._seen.values(), default=self.started_at)

    def _heard_now(self, now: float) -> set[int]:
        """지금(5초 안) 들리는 이 세트 셀 — 0x09 · 0x16 어느 쪽이든."""
        snap = self.live.snapshot()
        return {s for s in self.cfg.serials if s in snap and now - guards.heard_t(snap[s]) <= 5}

    def _others_heard(self, now: float) -> dict:
        """다른 등록 세트별 지금 들리는 셀 수 — '망 정상'의 증거(guards.net_ok). 세트 1 도 빠뜨리지 않는다(G13 — _other_groups 는
        세트 k 의 실행기에서도 세트 1 을 건너뛴다). 운전하지 않는 등록 세트도 센다."""
        mine = set(self.cfg.serials)
        snap = self.live.snapshot()
        out: dict = {}
        for s, sid in registered_serials(self.cfg).items():
            if s in mine or str(sid) == str(self.name):
                continue
            c = snap.get(s)
            out[sid] = out.get(sid, 0) + (1 if c is not None and now - guards.heard_t(c) <= 5 else 0)
        return out

    def _dock_switch(self) -> tuple[bool, str]:
        """이 세트의 Dock 켜기 명령을 쓸 수 있나 — (쓸 수 있음, 못 쓰는 까닭). 설정만 보고 포트는 열지 않는다(I10).
        스위치(bench.json 세트 줄의 dock.power_on)는 실물 확인 전까지 꺼짐이 기본이다 — 지금 운영에서 실제로 도는 길은 '꺼짐'이다."""
        try:
            sid = int(self.name)
        except (TypeError, ValueError):
            return False, "세트 번호를 모른다"
        d = dock_settings(set_entry(self.cfg, sid))
        if d is None:
            return False, "이 세트에 Dock 명령 설정(bench.json 세트 줄의 dock)이 없다"
        probs = dock_problems(self.cfg.sets)
        if probs:
            return False, "Dock 명령 설정 문제 — " + probs[0]
        if not d["power_on"]:
            return False, "Dock 명령 스위치(dock.power_on)가 꺼져 있다 — 실물 확인 전 기본값"
        return True, ""

    def _check_gaps(self, st: CycleState) -> None:
        """라이브 신호가 끊긴 셀을 이상으로 남긴다 (끊김당 1회). 리셋 방전에서는 셀이 꺼지는 것이 목적이라 남기지 않는다."""
        if st.phase == "RESET":
            return
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
            # 리셋 방전(RESET)도 같다 — 사람이 켜면 리셋을 취소하고 충전으로 간다(사람 뜻을 따른다, 불변식 I2)
            r = self._plug("on", st); self._force = "charge" if st.phase in ("DISCHARGE", "RESET") else None
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
        if st.phase == "RESET":                     # 리셋 방전 — 셀이 모두 꺼지는 것이 목적이다. 사이클을 접지 않고 재연결도 하지 않는다
            return                                  # (다중 모드면 리셋하는 동안 재연결 관문의 '운전 중'에서 빠져 있다 — _reset)
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
        self._note_cells(now)
        self.rec.sample(st.phase, on, w, wh, self._batts(), self._hr_cells())
        if st.phase == "DISCHARGE":
            self._safe(st, lambda st_: self._hr_tally(st_, now))
        self._check_gaps(st)
        if self.hold_request:
            why, self.hold_request = self.hold_request, None
            raise HoldRequested(why)
        self._safe(st, self._watch_storage, self._watch_disk, self._watch_direction, self._watch_hr, self._watch_partial)
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
            except (StopRequested, CycleAborted, PairMismatch, PartialOff):
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

    def _guard_stall(self, st: CycleState) -> None:
        """Dock 충전 멈춤 (자동 복구 규칙 A, guards.StallWatch) — 충전 쪽 단계에서 켜짐 · 바닥 전력(10 ~ 35 W)인데 셀이 3분 동안 오르지
        않으면 30초 끊었다 켠다. 1시간 stuck_max(3)번. 끊었다 켠 뒤에는 방향 대조 (a) 의 창도 새로 연다 — 끊기 전의 하락을 짝 틀림으로 읽지 않게.
        한도를 다 쓰고도 셀이 사건 시작보다 내려가 있으면 짝 이상과 같은 안전 동작(DockStallFail)으로 멈춘다. 세트 정지(HOLD)는 이미
        플러그를 켜 둔 채 선 상태라 남기기만 한다. 방전 · 추출에는 배선하지 않고(I1), 원격 '플러그 끄기'로 끈 동안은 물러난다(I2)."""
        if self.dry or self._force == "discharge" or st.phase not in STALL_PHASES:
            return
        r = self._last_read
        watch = self._stall
        act = watch.feed(time.time(), st.phase, self._per_cell_batt(), r.on if r else None, r.watts if r else math.nan)
        if act is None:
            return
        x, where, gap = watch.last, PHASE_KO.get(st.phase, st.phase), self.cfg.stuck_rule()[2]
        seen = (f"{where} 중 플러그 켜짐 · {x.get('w', math.nan):.1f} W 에서 {x.get('mins', 0):.0f}분 동안 셀 중앙값 "
                f"{x.get('med0', math.nan):.0f} % → {x.get('med', math.nan):.0f} %({x.get('ch', 0):+.1f}%p)")
        if act == "kick":
            err = None
            try:
                r2 = self.plug.recharge(gap_s=gap)
                self._saw(st, r2)
                self._last_read = r2
            except Exception as e:
                r2, err = None, e
            if r2 is None or not r2.on:
                watch.refund()                          # 켜기 실패는 한도에 세지 않는다 — 꺼짐이면 저전력 복구가 1분 뒤 켠다
                self.rec.log(f"Dock 충전 멈춤 — {gap:.0f}초 끊었다 켜기 실패: {err if err is not None else '응답이 꺼짐'} — 한도에 세지 않는다")
                return
            if self._dir.mode == "charge":
                self._dir.start_charge(time.time())
            st.power_recovers += 1
            self._ev(st, "dock_stall", "-", f"Dock 충전 멈춤 — {seen}. 자동으로 한 일: {gap:.0f}초 끊었다 켬 "
                                            f"(지난 1시간 {x.get('n')}/{watch.max_kicks}) · {r2.watts:.1f} W. 사람이 할 일: 없음")
        elif act == "ok":
            self._ev(st, "dock_stall_ok", "-", f"Dock 회복 — {where} 중 {x['w']:.1f} W, 셀 중앙값이 {x['mins']:.0f}분에 "
                                               f"{x['ch']:+.1f}%p 다시 오른다. 시험 계속")
        elif act == "capped":
            self.rec.log(f"Dock 충전 멈춤 — 1시간 한도 {watch.max_kicks}번을 다 썼고 셀이 사건 시작보다 내려가지 않았다"
                         f"({x.get('drop') if x.get('drop') is not None else '?'}%p) — 이 단계의 기존 한도에 맡긴다")
        else:                                           # fail
            st.dock_power_fail += 1
            why = (f"Dock 충전 멈춤 — {seen}. 자동으로 한 일: 1시간에 {watch.max_kicks}번 끊었다 켬, 셀은 사건 시작보다 "
                   f"{x['drop']:+.1f}%p")
            if st.phase == "HOLD":
                self._ev(st, "dock_stall_fail", "-", f"{why} — 세트 정지 중이라 플러그를 켠 채 둔다. 사람이 할 일: Dock 어댑터 · "
                                                     f"충전기 포트 · 케이블 확인")
                return
            self._pair_fault(st, why, kind="dock_stall_fail", exc=DockStallFail,
                             tail="감시자가 10분 · 1시간 · 3시간 뒤 다시 띄운다(하루 3번). 사람이 할 일: 없음 — 그래도 되풀이되면 감시자가 부른다")

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

    def _pair_fault(self, st: CycleState, why: str, kind: str = "pair_mismatch", exc: type = PairMismatch,
                    tail: str = "사람이 할 일: 케이블 · 플러그 별명을 실물로 확인한 뒤 엔진을 다시 시작한다(감시자는 되살리지 않는다)") -> None:
        """짝 이상 — 실제 짝을 모르므로 등록된 다른 세트의 플러그를 모두 켜기 시작하고(병렬) PairMismatch 를 올린다.
        다른 플러그는 끊지 않고 켜짐으로 읽힐 때까지 켠다(plug.ensure_on — 코드 검토 4: 끊었다 켜기는 켜기가 실패하면 끈 채 남긴다).
        끝내 켜지 못하면 MAC 과 함께 조치 알림(plug)을 낸다. 자기 플러그는 run() 이 _safe_end 로 켠다.
        Dock 충전 멈춤의 한도 소진(규칙 A)도 같은 길이다 — kind · exc · tail 만 바꿔 부른다(dock_stall_fail · DockStallFail)."""
        others = self.fault_plugs if self.fault_plugs is not None else other_plugs(self.cfg)
        label = "짝 이상" if kind == "pair_mismatch" else "Dock 충전 멈춤"
        did = "" if "자동으로 한 일" in why else "자동으로 한 일: "      # 규칙 E — '자동으로 한 일'을 먼저, '사람이 할 일'을 뒤에
        self._ev(st, kind, "-", f"{why} — {did}등록된 모든 플러그({1 + len(others)}개)를 켜고 멈춘다. {tail}")

        def turn_on(p: dict) -> None:
            try:
                rr = ensure_on(self._plug_factory(p["mac"], p["ip"]), sleep=time.sleep)
                self.rec.log(f"{label}: 세트 {p['id']} 플러그 {p['mac']} 켬 · {rr.watts:.1f} W")
            except Exception as e:
                msg = f"{label}: 세트 {p['id']} 플러그 {p['mac']} 를 켜지 못함 — {type(e).__name__}: {e}. 그 세트 셀이 방전 중일 수 있다"
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
                self.rec.log(f"{label}을 조정자에게 알리지 못함 — {e}")
        raise exc(why)

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
            self._safe(st, self._guard_power, self._guard_stall)
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
        for s in bad:
            self._not_charging_t[s] = time.time()   # 일부 꺼짐에서 이 셀은 리셋하지 않는다(접촉 고장 — 리셋해도 충전되지 않는다)
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
        self._safe(st, self._write_hr_ratio)
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
        self._phase(st, "CHARGE"); samples: list[tuple[float, float]] = []; seen_100 = False
        while True:
            wh, prev, w = self._poll(st, wh, prev)
            if prev: samples.append(prev)
            now = time.time()
            b = self._batts()
            seen_100 = seen_100 or cells_at_least(b, 100, len(cfg.serials)) is True
            if is_full(samples, b, now, cfg.full_flat_min, cfg.full_flat_tol_w,
                       cfg.full_requires_all_100, len(cfg.serials), seen_all_100=seen_100) or self._force == "discharge":
                if self._force == "discharge":
                    st.note = (st.note + " manual_stop_charge").strip()
                elif b and min(b) < 100:
                    self.rec.log(f"만충 — 모든 셀이 100% 를 찍은 뒤 일부가 {min(b)}% 로 내려간 채 전력이 평탄")
                self._force = None; st.full_at = now; self._charge_timeouts = 0; break
            if now - st.plug_on_at > cfg.charge_timeout_h * 3600:
                self._charge_timeouts += 1
                self.rec.event(st.cycle, st.phase, "charge_timeout", "-",
                               f"{cfg.charge_timeout_h}h 안에 만충 판정 안 됨 (연속 {self._charge_timeouts}/{cfg.charge_timeout_max})"); st.events += 1
                st.full_at = now; st.note = "charge_timeout"; break
            # 추출(EXTRACT) 중에는 표본이 돌지 않아 Dock 을 보지 못한다 — 추출이 끝난 뒤 여기서 이어 잡는다
            self._safe(st, self._guard_power, self._guard_stall, self._watch_charging)
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
        self._pre_after_recover = False
        try:
            self._recover_loop(st)
        finally:
            self._auto = None
            self.rec.hold(st.cycle, "leave", "RECOVER", f"{time.time() - t0:.0f}초")
        if self._pre_after_recover:
            self._pre_after_recover = False
            self._precharge_after_recover()

    def _precharge_after_recover(self) -> None:
        """복구 대기에서 나온 뒤 예비 충전부터 (G7) — 방금 켠 셀(또는 기준선 근처 셀)로 곧바로 방전하면 0초짜리 방전이 '완료 사이클'로 세어진다.
        예비 충전은 모든 셀이 precharge_skip_pct 이상이면 스스로 건너뛴다. 예비 충전이 깨지면 이상(aborted)만 남기고 사이클로 넘어간다 —
        감독 루프가 다시 잡는다. 원격 정지 · 짝 이상은 그대로 올린다."""
        try:
            self.precharge()
        except (StopRequested, PairMismatch):
            raise
        except PartialOff as e:                     # 일부만 돌아왔다 — 감독 루프의 다음 바퀴가 다룬다(리셋 · 세트 정지)
            self._partial_due = e
            self.current = None
        except Exception as e:
            st = self.current or CycleState(cycle=self.rec.next_cycle_no())
            self.rec.event(st.cycle, "PRECHARGE", "aborted", "-", f"복구 대기 뒤 예비 충전 중단: {e}")
            self.current = None
        finally:
            self._precharge_start_min = None

    def _recover_loop(self, st: CycleState) -> None:
        """안전 상태로 두고 셀이 다시 보일 때까지 기다린다.

        안전 상태 = 플러그 ON. 셀이 꺼지면 사람이 Dock 버튼을 눌러야 하고 그동안 시험이 멈추므로,
        모를 때는 충전 쪽으로 둔다. Wi-Fi 가 끊겨 플러그에도 닿지 않으면 재연결을 계속 시도한다.

        세트 전체 꺼짐 (자동 복구 규칙 D-1 — _wake_tick):
          · 충전 유지 (G6) — 저전력 복구 한도를 1시간마다 되채우고, 셀이 안 들리는 동안 바닥 전력이 30분 이어지면 끊었다 켠다(_recover_charge)
          · 셀이 0x09 · 0x16 모두 5분 안 들리고 망이 정상이면(guards.net_ok) '모두 꺼짐'(cells_off)으로 본다
          · Dock 명령 스위치가 켜져 있으면 충전 20분 뒤 Dock 켜기 명령 → 3분 안에 들리는 셀을 센다. 0대면 30분 더 충전한 뒤 한 번 더,
            그래도 0대면 사람을 부른다. 일부만 켜지면 다시 누르지 않는다(켜진 셀이 꺼진다)
          · 스위치가 꺼져 있으면(지금 운영 기본값) 30분 뒤 사람 호출 한 번 — 'Dock 버튼 한 번(모두 꺼져 있어 안전하다)'. 사람이 켜면 저절로 이어진다
          · 모두 꺼졌던 세트가 돌아오면 cells_woke 를 남기고 예비 충전부터 이어 간다 (G7 — _recover_back)
        """
        self._phase(st, "RECOVER")
        plug_on = False; asked_human = False; t0 = time.time(); seen_since = None
        wk = {"t0": t0, "off": None, "tries": 0, "sent": None, "last_try": None, "why": "", "charge_s": 0.0, "read_t": None,
              "floor_since": None, "floor_t": None,     # D-1 사건 장부 — 이 복구 대기 하나 동안만
              "known": self._off_known}                 # 리셋(D-2)이 모두 꺼짐을 확인하고 넘어왔으면 그 근거 — 망 증거 대신 쓴다
        self._off_known = None
        refill = t0
        while True:
            now = time.time()
            self._note_cells(now)
            if now - refill >= 3600:                    # 저전력 복구 한도를 1시간마다 되채운다 — 긴 복구 대기에서 3번 뒤 방치되지 않게(G6)
                self._power = guards.PowerWatch(*self.cfg.stuck_rule()); refill = now
            if not plug_on and not self.dry:
                try:
                    r = self.plug.recharge(); plug_on = r.on
                    self._saw(st, r)
                    if plug_on:
                        self._dir.start_charge(time.time())     # (a) 충전 쪽 하락 — 셀이 들리는 동안의 RECOVER 도 본다
                    self.rec.log(f"안전 상태: 플러그 ON · {r.watts:.1f} W")
                except Exception as e:
                    self.rec.log(f"플러그 ON 실패 ({e}) — 재연결 뒤 다시 시도")
            heard = bool(self._batts())
            if heard:
                seen_since = seen_since or now
                if now - seen_since >= 30 and not self._wake_confirming(wk, now):
                    self._recover_back(st, wk, now)
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
                text = self._wake_tick(st, wk, now) if plug_on and not self.dry else None
                if text and not asked_human:
                    asked_human = True
                    self.rec.event(st.cycle, st.phase, "need_human", "-", text)
            # 안전 상태(플러그 ON)인데 Dock 이 끌어 쓰지 않으면 끊었다 켠다. 시험망이 닿을 때만 읽는다 — 끊긴 채로 읽으면
            # 플러그 재시도(최대 1분 남짓)가 이 대기 루프를 붙잡는다
            if plug_on and not self.dry and (self._batts() or net.hub_reachable(self.cfg.hub_ip)):
                try:
                    r = self.plug.read(); self._last_read = r
                    self._saw(st, r)
                except Exception:
                    self._last_read = None
                self._safe(st, self._guard_power, self._guard_stall, self._watch_direction)
                self._recover_charge(st, wk, heard)
            self._auto = None if asked_human or wk["off"] is None else {"what": self._auto_what(wk), "since": wk["off"]}
            self._publish(st)
            self._handle_control(st)
            self._sleep(self.cfg.poll_s)

    # --- 세트 전체 꺼짐 → Dock 켜기 (자동 복구 규칙 D-1) — 판정은 guards.net_ok · wake_verdict, 여기는 배선만 ---
    def _recover_charge(self, st: CycleState, wk: dict, heard: bool) -> None:
        """복구 대기의 충전 유지 (G6). 켜기 전 충전(플러그 켜짐 · floor_w_max 초과)을 누적하고 바닥이 이어진 시각을 기억한다.
        셀이 안 들리는 동안 켜짐 · 바닥 전력이 30분 이어지면 끊었다 켠다 — 규칙 B 와 같은 함수(guards.floor_recycle). 셀이 들리면 규칙 A 가 본다.
        일상 동작이라 이상을 남기지 않고 run.log 에만 쓴다."""
        r, now, cfg = self._last_read, time.time(), self.cfg
        if r is None:
            wk["read_t"] = None
            return
        w = r.watts
        if wk["read_t"] is not None and r.on and w == w and w > cfg.floor_w_max:
            wk["charge_s"] += min(now - wk["read_t"], 3 * cfg.poll_s + 60)     # 읽지 못한 긴 틈은 충전으로 치지 않는다
        wk["read_t"] = now
        stuck_w = cfg.stuck_rule()[0]
        floor = r.on is True and w == w and stuck_w <= w <= cfg.floor_w_max
        wk["floor_since"] = (wk["floor_since"] or now) if floor else None
        if heard:
            wk["floor_t"] = None
            return
        act, wk["floor_t"] = guards.floor_recycle(None, r.on, w, now, wk["floor_t"], stuck_w, cfg.floor_w_max, SAFETY["recover_floor_s"])
        if act != "recharge":
            return
        gap = cfg.stuck_rule()[2]
        try:
            r2 = self.plug.recharge(gap_s=gap); self._saw(st, r2); self._last_read = r2
            self.rec.log(f"복구 대기 — 켜짐 · {w:.1f} W 바닥이 {SAFETY['recover_floor_s'] / 60:.0f}분 이어져 {gap:.0f}초 끊었다 켬 · "
                         f"{r2.watts:.1f} W (Dock 은 충전을 멈추면 켜진 채로는 다시 시작하지 않는다)")
        except Exception as e:
            self.rec.log(f"복구 대기 — 바닥 전력이라 끊었다 켜려다 실패: {e}")
        wk["floor_since"] = None                        # 끊었다 켠 뒤의 바닥을 새로 센다 ('켠 뒤 10분 바닥 평탄 = 이미 찼다')

    def _auto_what(self, wk: dict) -> str:
        """복구 대기에서 무엇을 기다리나 — 신호등 글(상태가 바뀔 때만 글이 바뀌게 숫자를 넣지 않는다)."""
        if not self._dock_switch()[0]:
            return "세트 셀이 모두 꺼짐 — 충전 유지 중 · 30분 안에 돌아오지 않으면 사람을 부른다"
        if wk["sent"] is not None:
            return "세트 셀이 모두 꺼짐 — Dock 켜기 명령 뒤 셀이 켜지기를 기다린다"
        if wk["tries"]:
            return "세트 셀이 모두 꺼짐 — 더 충전한 뒤 Dock 켜기 명령을 한 번 더 보낸다"
        return "세트 셀이 모두 꺼짐 — 충전한 뒤 Dock 켜기 명령을 보낸다"

    def _wake_confirming(self, wk: dict, now: float) -> bool:
        """켜기 명령 뒤 확인 창(wake_confirm_s) 안이고 아직 모든 셀이 들리지 않았나 — 그동안은 '한 대 들림'으로 복구 대기를 끝내지 않는다."""
        if wk["sent"] is None or now - wk["sent"] >= SAFETY["wake_confirm_s"]:
            return False
        return len(self._heard_now(now)) < len(self.cfg.serials)

    def _wake_tick(self, st: CycleState, wk: dict, now: float) -> str | None:
        """복구 대기의 표본마다(셀이 하나도 안 들리고 플러그가 켜져 있을 때 — 불변식 I13). 모두 꺼졌다고 볼 수 있으면 cells_off 를 사건에
        한 번 남기고, 보낼 때가 되면 Dock 켜기 명령을 보낸다. 사람을 불러야 하면 그 글을 돌려준다(부르는 쪽이 복구 대기마다 한 번 남긴다)."""
        cfg = self.cfg
        if wk["sent"] is not None:                      # 켜기 명령 뒤 확인 중 — 한 대도 안 들린 채 창이 끝나면 실패로 센다
            if now - wk["sent"] < SAFETY["wake_confirm_s"]:
                return None
            wk["sent"] = None
            self.rec.log(f"Dock 켜기 명령 {wk['tries']}번째 뒤 {SAFETY['wake_confirm_s']:.0f}초 — 셀이 하나도 켜지지 않았다")
        late = now - wk["t0"] > 30 * 60
        silent = self._silent_s(now)
        hub = net.hub_reachable(cfg.hub_ip) if (silent >= SAFETY["wake_silent_s"] or late) else True
        ok, basis = guards.net_ok(hub, self._others_heard(now), self._last_batt, cfg.serials, SAFETY["min_cells"],
                                  SAFETY["off_low_pct"])
        if not ok and hub and wk.get("known"):
            ok, basis = True, wk["known"]
        sw, sw_why = self._dock_switch()
        if silent >= SAFETY["wake_silent_s"] and ok and wk["off"] is None:
            wk["off"] = now
            self._cells_off(st, silent, basis, sw, sw_why)
        pct = self._storage_pct(now)
        full = sorted(s for s in cfg.serials if pct.get(s, 0.0) >= cfg.cell_storage_alarm_pct)
        if wk["tries"] == 0:
            charged = (wk["charge_s"] >= SAFETY["wake_charge_s"]
                       or (wk["floor_since"] is not None and now - wk["floor_since"] >= cfg.full_flat_min * 60))
        else:
            charged = now - wk["last_try"] >= SAFETY["wake_retry_s"]
        v = guards.wake_verdict(silent, ok, sw, full, wk["tries"], self.rec.count({"cells_off"}, 86400, now), charged, SAFETY)
        if v == "send":
            self._wake_send(st, wk)
            return None
        if v == "charging":
            return None
        if v in ("silent", "net"):
            return self._human_text("net", wk, now, basis) if late and hub else None
        if v == "switch":
            return self._human_text("switch", wk, now, sw_why) if late else None
        if v == "tries":
            return self._human_text("tries", wk, now, wk["why"])
        return self._human_text(v, wk, now, ", ".join(map(str, full)) if v == "storage" else "") if late else None

    def _cells_off(self, st: CycleState, silent: float, basis: str, sw: bool, sw_why: str) -> None:
        """이상 cells_off (노랑 · 사건에 한 번) — 세트 셀이 모두 꺼졌다. 무엇을 자동으로 할지 함께 적는다."""
        last = [self._last_batt[s] for s in self.cfg.serials if s in self._last_batt]
        lb = f" · 마지막 잔량 {_span(last)}" if last else ""
        then = (f"충전 {SAFETY['wake_charge_s'] / 60:.0f}분 뒤 Dock 켜기 명령을 보낸다. 사람이 할 일: 없음" if sw else
                f"Dock 켜기 명령은 쓰지 않는다({sw_why}). 사람이 할 일: 30분 안에 돌아오지 않으면 알린다")
        self._ev(st, "cells_off", "-", f"세트 {self.name} 셀 {len(self.cfg.serials)}대가 모두 꺼졌다 — {silent / 60:.0f}분째 0대 "
                                       f"(0x09 · 0x16 모두) · {basis}{lb}. 자동으로 한 일: 플러그를 켜 두고 충전 유지 · {then}")

    def _wake_send(self, st: CycleState, wk: dict) -> None:
        """Dock 켜기 명령 한 번 (dock.Dock.wake). 보내기 직전에 그 세트의 라이브를 다시 보고, 한 대라도 들리면 보내지 않는다(불변식 I8).
        이번 사건의 시도로 센다(I11). Dock 에 한 바이트라도 갔으면 확인 창(wake_confirm_s)을 연다."""
        if self._silent_s(time.time()) < SAFETY["wake_silent_s"]:
            self.rec.log("Dock 켜기 명령 — 보내기 직전에 이 세트 셀이 들려 보내지 않는다(불변식 I8)")
            return
        wk["tries"] += 1
        try:
            res = self._dock_factory().wake(all_off_confirmed=True)
        except Exception as e:                          # Dock 모듈은 예외 대신 결과로 답하지만, 여기서 시험이 멈추면 안 된다
            res = WakeResult(False, f"{type(e).__name__}: {e}")
        wrote = sum(s.wrote for s in res.steps)
        wk["last_try"] = time.time()
        wk["sent"] = wk["last_try"] if wrote else None
        wk["why"] = "" if res.ok else res.reason
        steps = " → ".join(f"{s.cmd}{'' if s.ok else ' 실패'}" for s in res.steps) or "보내지 못함"
        confirm = f"{SAFETY['wake_confirm_s']:.0f}초 동안 들리는 셀을 센다"
        what = (f"응답 OK · {confirm}" if res.ok else
                f"실패 — {res.reason} · Dock 에 {wrote}바이트가 갔다 · {confirm}" if wrote else f"보내지 못함 — {res.reason}")
        self._ev(st, "dock_wake", "-", f"세트 {self.name} Dock 켜기 명령 {wk['tries']}/{SAFETY['wake_max_tries']} ({steps}) · "
                                       f"충전 {wk['charge_s'] / 60:.0f}분 뒤 — {what}")

    def _recover_back(self, st: CycleState, wk: dict, now: float) -> None:
        """복구 대기에서 나간다 — 셀이 다시 들린다. 모두 꺼졌던 세트(cells_off)였으면 cells_woke 를 남기고, 그때나 가장 낮은 셀이
        기준선 근처(기준선 + manual_plug_margin_pct 이하)면 예비 충전부터 이어 간다(G7). 일부만 돌아왔으면 다시 누르지 않는다."""
        cfg = self.cfg
        b = self._batts()
        low = min(b) if b else None
        self._pre_after_recover = wk["off"] is not None or (low is not None and low <= cfg.discharge_stop_pct + cfg.manual_plug_margin_pct)
        if wk["off"] is None:
            return
        heard = sorted(self._heard_now(now))
        miss = sorted(set(cfg.serials) - set(heard))
        how = (f"충전 {wk['charge_s'] / 60:.0f}분 뒤 Dock 켜기 명령 {wk['tries']}번" if wk["tries"] else "사람이 켰거나 저절로 돌아왔다")
        tail = (f" · 켜지지 않은 셀 {len(miss)}대 {miss[:8]}{' …' if len(miss) > 8 else ''} — 다시 누르지 않는다(켜진 셀이 꺼진다). "
                f"일부 꺼짐 처리(리셋 또는 세트 정지)로 넘긴다 — 사람이 할 일이 생기면 따로 알린다" if miss else ". 사람이 할 일: 없음")
        self._ev(st, "cells_woke", "-", f"세트 {self.name} 셀 {len(heard)}/{len(cfg.serials)}대가 다시 들린다 — 자동으로 한 일: {how} · "
                                        f"예비 충전부터 이어 간다{tail}")

    def _human_text(self, kind: str, wk: dict, now: float, why: str = "") -> str:
        """복구 대기의 사람 호출 글 (need_human). '자동으로 한 일'을 먼저, '사람이 할 일'을 뒤에. Dock 버튼은 세트가 모두 꺼졌을 때만
        안전하다 — 켜진 셀은 3초 누름에 꺼진다(recovery-plan 4-3). 그래서 모두 꺼졌다는 것을 모를 때는 누르라고 하지 않는다."""
        k, n = self.name, len(self.cfg.serials)
        mins, chg = (now - wk["t0"]) / 60, wk["charge_s"] / 60
        safe = "Dock 버튼을 한 번(3초) 눌러 전체를 켠다 — 셀이 모두 꺼져 있어 안전하다. 켜면 저절로 이어진다(예비 충전부터)"
        if kind == "net":
            return (f"세트 {k} 셀이 {mins:.0f}분째 하나도 안 들린다 — 셀이 꺼졌는지 망 문제인지 가릴 수 없다({why}). "
                    f"자동으로 한 일: 플러그를 켜 두고 충전 유지 · 망 문제일 수 있어 Dock 명령은 보내지 않았다. "
                    f"사람이 할 일: LiveHub 전원과 연결을 먼저 본다. 셀이 모두 꺼져 있으면 그다음 Dock 버튼을 한 번 눌러 켠다"
                    f"(켜진 셀이 하나라도 있으면 Dock 버튼을 누르지 않는다 — 켜진 셀이 꺼진다)")
        if kind == "switch":
            return (f"세트 {k} 셀 {n}대가 모두 꺼졌다. 자동으로 한 일: 플러그를 켜 두고 {mins:.0f}분째 충전 유지(충전 {chg:.0f}분) · "
                    f"Dock 켜기 명령은 쓰지 않았다({why}). 사람이 할 일: {safe}")
        if kind == "tries":
            return (f"세트 {k} 셀 {n}대가 모두 꺼졌다. 자동으로 한 일: 충전 {chg:.0f}분 · Dock 켜기 명령 {wk['tries']}번 — "
                    f"셀이 하나도 켜지지 않았다{f'({why})' if why else ''}. 사람이 할 일: {safe}")
        what = (f"저장이 가득 차 신호만 멈춘 셀이 섞여 있을 수 있어({why}) — 누르면 그 셀이 꺼진다" if kind == "storage" else
                f"지난 24시간 이 세트가 {SAFETY['wake_max_per_day']}번 넘게 모두 꺼졌다")
        return (f"세트 {k} 셀 {n}대가 모두 안 들린다. 자동으로 한 일: 플러그를 켜 두고 충전 유지 · {what}. Dock 켜기 명령은 보내지 않았다. "
                f"사람이 할 일: 셀을 직접 본다 — 모두 꺼져 있으면 Dock 버튼을 한 번 눌러 켠다. 켜진 셀이 있으면 Dock 버튼은 누르지 않는다")

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
        if cells_at_least(b0, cfg.precharge_skip_pct, len(cfg.serials)) is True:     # None(셀이 다 안 들림)이면 건너뛰지 않고 충전한다
            self.rec.log(f"예비 충전 건너뜀 — 모든 셀 {min(b0)}% 이상(precharge_skip_pct {cfg.precharge_skip_pct}%) · 곧바로 사이클로")
            self.current = None
            return
        self._precharge_start_min = min(b0) if b0 else None
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
            if verdict == "dock_no_recharge":        # 모든 셀이 들렸고(precharge_verdict 가 보류하지 않았다) 그중 95% 미만이 있다
                low = min(b)
                if not kicked:
                    kicked = True
                    self._ev(st, "dock_no_recharge", "-",
                             f"예비 충전 중 전력이 바닥({w:.1f} W ≤ {cfg.floor_w_max:g} W)에서 {cfg.full_flat_min:g}분 평탄한데 최저 셀 "
                             f"{low}% < {cfg.precharge_skip_pct}% — Dock 이 재충전하지 않는다. "
                             f"{cfg.stuck_rule()[2]:.0f}초 끊었다 켠다(1회). 그래도 같으면 사이클로 넘어간다")
                    try:
                        r = self.plug.recharge(gap_s=cfg.stuck_rule()[2]); self._saw(st, r); self._last_read = r
                    except Exception as e:
                        self.rec.log(f"예비 충전 — 끊었다 켜기 실패: {e}")
                    samples = []                     # 평탄 창을 새로 센다 (아래 표본 간격 기다림은 그대로 거친다)
                else:
                    self.rec.log(f"예비 충전 끝 — 끊었다 켠 뒤에도 전력이 바닥({w:.1f} W)에서 {cfg.full_flat_min:g}분 평탄 · 최저 셀 "
                                 f"{low}% < {cfg.precharge_skip_pct}% — Dock 이 재충전하지 않아 사이클로 넘어간다")
                    break
            if self._force == "discharge":
                # 원격 '플러그 끄기' — 충전 중이면 만충으로 치고 넘어간다(control.py). 예전에는 예비 충전이 이 명령을 보지 않아
                # 자동 복구가 1분 뒤 도로 켰다. 이제 복구는 원격으로 끈 동안 손대지 않으므로, 여기서 예비 충전을 끝내야
                # 플러그가 꺼진 채 만충 한도(charge_timeout_h)까지 기다리지 않는다.
                self._force = None
                self.rec.log("예비 충전 중 원격 플러그 끄기 — 예비 충전을 끝내고 사이클로 넘어간다")
                break
            # 자동 복구: 밖에서 꺼졌거나, 켜져 있는데 Dock 이 끌어 쓰지 않으면(≈1.5 W) 끊었다 켠다 (2026-10-08 실측 · guards.PowerWatch)
            self._safe(st, self._guard_power, self._guard_stall)
            if now - st.plug_on_at > cfg.charge_timeout_h * 3600:
                self.rec.event(st.cycle, st.phase, "charge_timeout", "-", f"예비 충전 {cfg.charge_timeout_h}h 안에 만충 안 됨")
                break
            self._sleep(cfg.poll_s)
        self.rec.log(f"예비 충전 끝 · {(time.time() - st.plug_on_at)/60:.0f}분 · {wh:.1f} Wh")
        self._precharge_start_min = None
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
        try:
            every = max(0, int(self.cfg.progress_notify_every))
        except (TypeError, ValueError):              # 설정 검사(config.validate)가 먼저 막지만, 여기서도 예외로 시험을 멈추지 않는다(검토 F3)
            self.rec.log(f"progress_notify_every 가 정수가 아니다({self.cfg.progress_notify_every!r}) — 진척 알림을 끈다")
            every = 0
        # 마지막으로 알린 진척 단계는 events.csv 의 progress 행에서 복원한다(검토 F10) — 누적 사이클 수로 어림하면, 사이클 100 을 기록한 직후
        # 알리기 전에 엔진이 죽었을 때 그 알림이 영영 빠진다. 행이 없으면 0 (= 지난 단계가 있으면 지금 한 번 알린다)
        milestone = self.rec.last_progress() if every else 0
        try:
            if self.rec.recover_cut_charge() is not None:   # 충전 중 끊긴 사이클 — 복구 행을 쓰고 다음 번호로, 충전은 예비 충전이 이어 간다
                precharge = True                            # (덜 찬 셀로 방전하면 그 사이클 작동시간이 틀어진다 — 셀 ≥95% 면 건너뛴다)
            if precharge:
                try:
                    self.precharge()
                except (StopRequested, PairMismatch):
                    raise
                except PartialOff as e:                  # 일부 꺼짐 — 아래 고리의 첫 바퀴가 다룬다(리셋 · 세트 정지)
                    self._partial_due = e
                    self.current = None
                except Exception as e:
                    st = self.current or CycleState(cycle=self.rec.next_cycle_no())
                    self.rec.event(st.cycle, "PRECHARGE", "aborted", "-", f"예비 충전 중단: {e}")
                    self._recover_why = f"예비 충전 중단: {e}"
                    self._recover(st)
                finally:
                    self._precharge_start_min = None
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
                    if self._partial_due is not None:
                        # 일부 꺼짐 (규칙 D-2) — 리셋 또는 세트 정지. 리셋 사이클은 이번 실행의 --cycles 에도 목표에도 세지 않는다
                        e, self._partial_due = self._partial_due, None
                        self._partial(e)
                        continue
                    self.run_cycle()
                    done += 1; fails = 0
                    if self._charge_timeouts >= max(1, int(self.cfg.charge_timeout_max)):
                        # Dock 이 사이클마다 만충에 실패한다 — 같은 결함을 되풀이하지 않고 failsafe 로 끝낸다(감시자는 되살리지 않고 사람을 부른다, 검토 F4)
                        st = self.current or CycleState(cycle=max(1, self.rec.next_cycle_no() - 1), phase="CHARGE")
                        self._ev(st, "charge_timeout_streak", "-",
                                 f"Dock 이 {self._charge_timeouts}사이클 연속 만충 실패({self.cfg.charge_timeout_h:g}h 한도) — 사람 확인 · 플러그 ON 으로 멈춘다")
                        self.rec.log(f"충전 한도 초과 {self._charge_timeouts}사이클 연속 — 플러그 ON 으로 두고 멈춘다 (failsafe)")
                        self.plug_on_at_exit = self._safe_end(st)
                        self.end_why = "charge_timeout_streak"
                        return "failsafe"
                except (KeyboardInterrupt, StopRequested, PairMismatch, Reconfigure):
                    raise
                except PartialOff as e:
                    self._partial_due = e                   # 다음 바퀴에서 — 처리 중 오류가 나도 아래의 실패 길이 받게 try 안에서 다룬다
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
                        self.end_why = "failures"
                        return "failsafe"
                    self._recover_why = detail
                    self._recover(st)
        except Reconfigure:
            self.rec.log("세트 구성이 바뀌어 경계에서 끝낸다 — 플러그는 켜진 채(만충 직후)")
            self.plug_on_at_exit = self.plug_known
            return "reconfigure"
        except PairMismatch as e:
            # 짝 이상 (명세 3-6) — 다른 세트 플러그는 _pair_fault 가 켜는 중이다. 자기 플러그를 켜고 failsafe 로 끝낸다.
            # Dock 충전 멈춤의 한도 소진(DockStallFail, 규칙 A)도 같은 길이다 — 이름 · 사이클 표지 · 끝난 이유(why)만 다르다
            st = self.current or CycleState(cycle=self.rec.next_cycle_no())
            self.end_why = "dock_stall" if isinstance(e, DockStallFail) else "pair_mismatch"
            label = "Dock 충전 멈춤" if self.end_why == "dock_stall" else "짝 이상"
            self.rec.log(f"{label} — 등록된 모든 플러그를 켜고 멈춘다 (failsafe): {e}")
            st.note = (st.note + f" {self.end_why}").strip()
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

    def hold(self, why: str, phase: str = "HOLD", until=None) -> None:
        """HOLD — 플러그를 켜 두고 지킨다(다중 모드, 명세 2-3 · D10). 조정자가 멈춤(_halt)을 세울 때까지 돈다.

        지금의 복구 대기에서 '셀이 돌아오면 끝냄'을 뺀 고리다. 저전력 복구 한도는 시간당 다시 채우고(하나의 긴 단계라 단계당 3번이면
        3번 뒤 켜짐 · 1.5 W 로 방치된다 — A-H1), 셀 중앙값이 90 % 아래면 한 시간에 한 번 끊었다 켠다(Dock 은 만충 뒤 켜진 채로는
        충전을 다시 시작하지 않는다). 명령(플러그 켜기 · 끄기)은 받는다 — 세트 정지는 이미 HOLD 라 아무 일도 하지 않는다.

        until(now) 를 주면 그것이 참이 될 때 끝낸다 — 세트 일부 꺼짐의 세트 정지(규칙 D-2): 사람이 꺼진 셀을 켜면 저절로 이어지게.
        그때는 Ctrl+C · 원격 정지 · 다른 세트의 세트 정지 요청(hold_request)을 삼키지 않고 올린다 — 감독 루프가 받는다.
        """
        st = self.current or CycleState(cycle=max(1, self.rec.next_cycle_no() - 1))
        self.current = st
        self._phase(st, phase)
        self.rec.hold(st.cycle, "enter", phase, why)
        t0 = refill = kick = time.time()
        try:
            while True:
                now = time.time()
                if until is not None:
                    if self.hold_request:
                        hr_why, self.hold_request = self.hold_request, None
                        raise HoldRequested(hr_why)
                    if until(now):
                        return
                if now - refill >= 3600:
                    self._power = guards.PowerWatch(*self.cfg.stuck_rule()); refill = now
                r = self._plug("read", st)
                if r is not None:
                    st.last_on, st.last_w = r.on, r.watts
                self._safe(st, self._guard_power, self._guard_stall, self._watch_disk)
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
                    if until is not None:
                        raise
                    pass                                   # 이미 HOLD 다
                self._sleep(self.cfg.poll_s)
        except KeyboardInterrupt:
            if until is not None:
                raise
        finally:
            self.rec.hold(st.cycle, "leave", phase, f"{time.time() - t0:.0f}초")

    # --- 세트 일부 꺼짐 → 전체 방전 리셋 (자동 복구 규칙 D-2) — 판정은 guards.partial_off · reset_verdict · reset_all_off ---
    def _partial(self, e: PartialOff) -> None:
        """일부 꺼짐을 다룬다(감독 루프의 한 바퀴). 섞인 세트에 Dock 명령을 보내지 않는다(I8) — 스위치가 켜져 있으면 그 세트만 모두
        꺼지게 한 뒤(리셋) 전체를 켜고, 아니면 그 세트만 세트 정지(충전 유지) + 사람 호출로 둔다. 다른 세트는 계속 돈다(실행기 스레드가 따로다)."""
        st, now = e.st, time.time()
        if self.gate is not None:
            self.gate.leave(self.name)                  # 추출 대기 중이었으면 줄에서 빠진다 — 다른 세트가 그 차례를 기다리지 않게
        sw, sw_why = self._dock_switch()
        contact = sorted(s for s in e.silent if now - self._not_charging_t.get(s, -math.inf) <= 86400)
        v = guards.reset_verdict(sw, contact, self.rec.count({"set_reset"}, 86400, now), SAFETY)
        if v == "reset":
            self._reset(st, e.silent, e.alive)
            return
        k, cells = self.name, _cells(e.silent)
        others = ", 다른 세트는 계속 돈다" if self.link_guard is not None else ""
        alive = f"나머지 {len(e.alive)}대는 켜져 있다"
        if v == "contact":
            text = (f"세트 {k} 셀 {cells} 가 {SAFETY['partial_off_s'] / 60:.0f}분 넘게 안 들린다 — {alive}. 이 셀은 지난 24시간 안에 충전이 "
                    f"안 올랐다(Dock 자리 접촉 의심). 자동으로 한 일: 리셋해도 이 셀은 충전되지 않아 리셋하지 않고, 이 세트를 세트 정지"
                    f"(플러그 켜짐 · 충전 유지)로 두었다{others}. 사람이 할 일: 셀 {cells} 의 Dock 자리(접촉)와 셀을 보고 전원 버튼으로 켠다"
                    f"(Dock 버튼은 누르지 않는다 — 켜진 셀이 꺼진다). 다 켜지면 저절로 이어진다(예비 충전부터)")
        elif v == "switch":
            text = (f"세트 {k} 셀 {cells} 가 {SAFETY['partial_off_s'] / 60:.0f}분 넘게 안 들린다 — {alive}. 자동으로 한 일: 이 세트를 "
                    f"세트 정지(플러그 켜짐 · 충전 유지)로 두었다{others} · Dock 명령은 쓰지 않는다({sw_why}). 사람이 할 일: 셀 {cells} 의 "
                    f"전원 버튼을 하나씩 눌러 켠다(Dock 버튼은 누르지 않는다 — 켜진 셀이 꺼진다). 다 켜지면 저절로 이어진다(예비 충전부터)")
        else:                                           # day — 오늘 이미 리셋했다(취소 포함). 같은 셀이 또 꺼졌으면 그 자리의 고장이다
            text = (f"세트 {k} 셀 {cells} 가 {SAFETY['partial_off_s'] / 60:.0f}분 넘게 안 들린다 — {alive}. 자동으로 한 일: 지난 24시간 "
                    f"안에 이 세트를 이미 리셋했다(하루 {SAFETY['reset_max_per_day']}번 — 리셋 뒤에도 같은 셀이면 그 자리의 고장이다) — "
                    f"리셋하지 않고 세트 정지(플러그 켜짐 · 충전 유지){others}. 사람이 할 일: 셀 {cells} 의 "
                    f"Dock 자리와 셀을 보고 전원 버튼으로 켠다. Dock 버튼은 누르지 않는다(켜진 {len(e.alive)}대가 꺼진다). 다 켜지면 저절로 이어진다")
        self._fold(st, "partial_off")
        self._hold_for_cells(st, text, f"일부 꺼짐 {len(e.silent)}대 — 사람이 켜기를 기다린다")

    def _fold(self, st: CycleState, note: str) -> None:
        """일부 꺼짐으로 지금 사이클을 접는다 — 방전을 시작한 사이클만 줄을 쓴다(예비 충전은 사이클이 아니다). 만충 칸은 비운다."""
        if st.discharge_start is None or st.full_at is not None:
            return
        st.note = (st.note + f" {note}").strip()
        self.rec.cycle(self._row(st))
        self.current = None

    def _hold_for_cells(self, st: CycleState, text: str, why: str) -> None:
        """사람 호출(need_human) 한 번 뒤 세트 정지 — 그 세트의 셀이 모두(저장 가득 참 셀은 빼고) 30초 넘게 들리면 저절로 풀고
        cells_woke 를 남긴 뒤 예비 충전부터 이어 간다."""
        self.rec.event(st.cycle, st.phase, "need_human", "-", text)
        if not self.dry and self.plug_known is not True:
            self._plug("on", st)                        # 방전 중이었으면 꺼져 있다 — 세트 정지는 충전 쪽이다
        back: dict = {"t": None}

        def until(now: float) -> bool:
            pct = self._storage_pct(now)
            need = {s for s in self.cfg.serials if pct.get(s, 0.0) < self.cfg.cell_storage_alarm_pct}
            if need <= self._heard_now(now):
                back["t"] = back["t"] or now
                return now - back["t"] >= 30
            back["t"] = None
            return False
        self.hold(why, until=until)
        cur = self.current or st
        self._ev(cur, "cells_woke", "-", f"세트 {self.name} 셀 {len(self._heard_now(time.time()))}/{len(self.cfg.serials)}대가 다시 들린다 — "
                                         f"자동으로 한 일: 세트 정지(충전 유지)를 풀고 예비 충전부터 이어 간다. 사람이 할 일: 없음")
        self.current = None
        self._precharge_after_recover()

    def _reset(self, st: CycleState, silent: list[int], alive: list[int]) -> None:
        """전체 방전 리셋 (규칙 D-2). 그 세트를 모두 꺼지게 한 뒤 Dock 으로 전체를 켠다.

          · 충전 쪽이었으면 지금 사이클을 접고(note reset · 만충 칸 비움) 새 번호의 리셋 사이클로, 방전 중이었으면 그 사이클을 이어 간다
          · 플러그를 끈 채 기준선(30 %)에 닿아도 멈추지 않는다. 추출 · 대기 셀 깨우기 · 심박 재연결은 하지 않는다. 셀이 다 꺼져도 사이클을
            접지 않고 재연결도 하지 않는다(다중 모드면 리셋 동안 재연결 관문의 '운전 중'에서 빠진다)
          · 모두 꺼짐 = 모든 셀이 5분 넘게 안 들리고, 리셋을 시작할 때 들리던 셀은 사라지기 직전 잔량이 5 % 이하였다 — 그러면 복구 대기로 가서
            D-1 의 켜기(충전 20분 · Dock 명령 · 3분 확인)를 한다
          · 사람이 플러그를 켜면(원격 '플러그 켜기' 포함) 리셋을 취소하고 충전으로 간다 — 24시간 리셋하지 않는다(하루 1번 셈에 든다)
          · 방전 한도(SAFETY discharge_max_h, 7시간)가 지나면 플러그를 켜고 세트 정지 + 사람 호출
        리셋 사이클의 줄은 note 'reset deep_discharge' 이고 만충 칸이 비어 목표 사이클에 세지 않는다(record.cycles_counted)."""
        cfg, k = self.cfg, self.name
        others = " 다른 세트는 계속 돈다." if self.link_guard is not None else ""
        self._ev(st, "set_reset", "-", f"세트 {k} 셀 {_cells(silent)} 가 {SAFETY['partial_off_s'] / 60:.0f}분 넘게 안 들린다 — 나머지 "
                                       f"{len(alive)}대는 들린다. 자동으로 한 일: 이 세트를 모두 꺼지게 하는 리셋을 시작한다 — 플러그를 끈 채 "
                                       f"남은 셀이 다 꺼질 때까지 방전하고(약 4~8시간), 모두 꺼지면 Dock 으로 전체를 켠 뒤 예비 충전부터 이어 간다. "
                                       f"이 사이클은 목표 사이클에 세지 않는다.{others} 사람이 할 일: 없음")
        if st.phase == "DISCHARGE" and st.full_at is None:
            rst = st                                    # 방전 중이었다 — 그 사이클을 리셋 사이클로 이어 간다
        else:
            self._fold(st, "reset")
            rst = CycleState(cycle=self.rec.next_cycle_no(), ip_base=self._ip_total())
            self.rec.begin_cycle(rst.cycle)
        self.current = rst
        rst.note = (rst.note + " reset deep_discharge").strip()
        self.rec.hold(rst.cycle, "enter", "RESET", f"일부 꺼짐 {len(silent)}대 — 모두 꺼지게 한 뒤 Dock 으로 켠다")
        t0 = time.time()
        if self.link_guard is not None:
            self.link_guard.remove(self.name)           # 꺼지는 셀 때문에 다른 세트의 재연결을 막거나 부르지 않게 — 다음 _watch_link 가 다시 넣는다
        self._auto = {"what": "일부 꺼짐 리셋 — 셀이 모두 꺼질 때까지 방전한 뒤 Dock 으로 켠다", "since": t0}
        self._phase(rst, "RESET")
        if rst.discharge_start is None:
            self._plug("off", rst); rst.discharge_start = time.time()
            rst.discharge_start_batt = self._per_cell_batt()
            self._dir.start_discharge(rst.discharge_start, rst.discharge_start_batt)   # (b) · (c) 는 리셋 방전도 본다 — 짝이 틀렸으면 잡는다
        how, why, told = "", "", ""
        seen_off, offs = False, 0                     # 리셋을 시작한 뒤 플러그가 꺼짐으로 읽힌 적이 있나 · 다시 끈 횟수
        wh, prev = 0.0, None
        try:
            while True:
                wh, prev, _ = self._poll(rst, wh, prev)
                now = time.time()
                r = self._last_read
                seen_off = seen_off or (r is not None and r.on is False)
                # 사람이 켰다고 보는 것은 꺼짐을 한 번이라도 본 뒤의 켜짐뿐이다 — 그 전의 켜짐은 끄기가 안 먹었거나(릴레이가 안 바뀌어도
                # 끄기 응답은 '꺼짐'일 수 있다) 다른 세트의 짝 이상이 모든 플러그를 켠 것이라, 취소하면 하루 1번 리셋을 헛되이 쓴다
                if self._force == "charge" or (r is not None and r.on and seen_off):
                    how = "cancel"
                    break
                if r is not None and r.on:              # 아직 꺼짐을 못 봤다 — 다시 끈다. RESET_OFF_TRIES 번 넘게 안 꺼지면 세트 정지 + 사람 호출
                    offs += 1
                    if offs > RESET_OFF_TRIES:
                        how = "stuck"
                        break
                    self._plug("off", rst)
                ok, why = guards.reset_all_off(self._seen, self._last_batt, silent, now, SAFETY["wake_silent_s"], SAFETY["off_low_pct"])
                if ok:
                    how = "off"
                    break
                if "사라졌다" in why and told != why:
                    told = why
                    self.rec.log(f"리셋 — 모두 안 들리지만 {why}. 기다린다(방전 한도 {SAFETY['discharge_max_h']:g}시간)")
                if now - rst.discharge_start > SAFETY["discharge_max_h"] * 3600:
                    how = "timeout"
                    break
                self._sleep(cfg.poll_s)
        finally:
            self._auto = None
            self.rec.hold(rst.cycle, "leave", "RESET", f"{time.time() - t0:.0f}초 · {how or '중단'}")
        rst.discharge_end = time.time()
        hours = (rst.discharge_end - rst.discharge_start) / 3600
        if how == "cancel":
            rst.note += " reset_cancelled"
            self._force = None
        elif how == "timeout":
            rst.note += " reset_timeout"
        elif how == "stuck":
            rst.note += " reset_plug_stuck"
        self.rec.cycle(self._row(rst))
        self.current = None
        if how == "off":
            last = [self._last_batt[s] for s in cfg.serials if s in self._last_batt and s not in silent]
            lb = f" · 사라지기 직전 잔량 {_span(last)}" if last else ""
            sw, sw_why = self._dock_switch()
            nxt = "충전 20분 뒤 Dock 켜기 명령" if sw else f"Dock 켜기 명령은 쓰지 않는다({sw_why}) — 30분 뒤 사람을 부른다"
            self._ev(rst, "set_reset_done", "-", f"세트 {k} 셀 {len(cfg.serials)}대가 모두 꺼졌다 — 리셋 방전 {hours:.1f}시간{lb}. "
                                                 f"자동으로 한 일: 플러그를 켜고 복구 대기에서 {nxt}. 사람이 할 일: 없음")
            self._off_known = "리셋으로 모두 꺼짐을 확인했다"
            self._recover_why = "리셋 — 모두 꺼짐 확인"
            self._recover(rst)
            return
        if how == "cancel":
            self._ev(rst, "set_reset_done", "-", f"세트 {k} 리셋 취소 — 리셋 방전 {hours:.1f}시간에 플러그가 켜졌다(사람 · 원격 명령). "
                                                 f"자동으로 한 일: 사람 뜻대로 충전으로 간다(예비 충전부터) · 24시간 리셋하지 않는다. 사람이 할 일: 없음")
            self._precharge_after_recover()
            return
        if how == "stuck":                              # 플러그가 꺼지지 않는다 — 리셋을 멈추고 켜진 채 세트 정지(충전 쪽이 안전)
            self._ev(rst, "set_reset_done", "-", f"세트 {k} 리셋 멈춤 — 플러그를 {RESET_OFF_TRIES}번 더 껐는데도 켜짐으로 읽힌다. "
                                                 f"자동으로 한 일: 리셋을 멈추고 플러그를 켠 채 세트 정지")
            self._hold_for_cells(rst, f"세트 {k} 리셋을 시작했지만 플러그가 꺼지지 않는다(끄기 {RESET_OFF_TRIES + 1}번 — 계속 켜짐으로 읽힌다). "
                                      f"자동으로 한 일: 리셋을 멈추고 세트 정지(플러그 켜짐 · 충전 유지).{others} 사람이 할 일: 플러그(릴레이)와 "
                                      f"Tapo 앱의 예약 · 자동화를 본다. 꺼진 셀 {_cells(silent)} 는 전원 버튼으로 켠다(Dock 버튼은 누르지 않는다 — "
                                      f"켜진 셀이 꺼진다). 다 켜지면 저절로 이어진다",
                                 "리셋 멈춤(플러그가 꺼지지 않음) — 사람을 기다린다")
            return
        try:                                            # 7시간 — 모두 꺼졌다고 확인하지 못했다. 충전 쪽으로 두고 사람을 부른다
            r = self.plug.recharge(gap_s=cfg.stuck_rule()[2]); self._saw(rst, r)
        except Exception as e:
            self.rec.log(f"리셋 — 7시간 뒤 플러그 켜기 실패: {e} (세트 정지의 저전력 복구가 다시 켠다)")
        self._ev(rst, "set_reset_done", "-", f"세트 {k} 리셋 방전 {hours:.1f}시간 — 모두 꺼졌다고 확인하지 못했다({why}). "
                                             f"자동으로 한 일: 리셋을 멈추고 플러그를 켜 충전 유지 · 세트 정지")
        self._hold_for_cells(rst, f"세트 {k} 리셋 방전이 {SAFETY['discharge_max_h']:g}시간을 넘었다 — {why}. 자동으로 한 일: 플러그를 켜 두고 "
                                  f"세트 정지(충전 유지).{others} 사람이 할 일: LiveHub 연결을 먼저 본다. 셀이 모두 꺼져 있으면 Dock 버튼을 한 번 "
                                  f"눌러 켠다. 켜진 셀이 있으면 Dock 버튼은 누르지 않고 꺼진 셀만 전원 버튼으로 켠다. 다 켜지면 저절로 이어진다",
                             "리셋 7시간 — 사람을 기다린다")

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


def _span(vals) -> str:
    """잔량 범위 — '1~3 %', 같으면 '1 %'."""
    lo, hi = min(vals), max(vals)
    return f"{lo} %" if lo == hi else f"{lo}~{hi} %"


def _cells(xs: list[int], n: int = 8) -> str:
    """셀 목록을 사람이 읽는 글로 — '11740 · 11751', 많으면 '… 외 N대'."""
    xs = sorted(xs)
    return " · ".join(map(str, xs[:n])) + (f" 외 {len(xs) - n}대" if len(xs) > n else "")


def _pcts(pct: dict[int, float], cells: list[int], n: int = 8) -> str:
    s = ", ".join(f"{x} {pct[x]:.0f}%" for x in cells[:n])
    return s + (f" 외 {len(cells) - n}대" if len(cells) > n else "")
