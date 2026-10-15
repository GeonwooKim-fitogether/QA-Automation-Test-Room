"""기록 — 사이클 한 줄, 표본 한 줄, 이상 한 줄. 전부 CSV 라 엑셀로 바로 열린다.

data/
  cycles.csv              사이클마다 1줄 (결과판 '추이' 장의 재료)
  now.json                지금 상태 (결과판 '운영' 장 · 감시자의 심박 beat · 신호등의 metrics)
  plug_stats.json         플러그 릴레이가 실제로 움직인 누적 횟수 (릴레이 수명 — 엔진의 Plug 가 쓴다)
  engine.json             이 엔진이 누구고 어디까지 돌 것이며 어떻게 끝났나 (감시자가 읽는다)
  samples_<사이클>.csv     20초마다 1줄: 단계·플러그·전력·누적Wh·배터리 (결과판 '운영' 장의 재료)
  events.csv              이상 이벤트
  cells_<사이클>.csv       셀별 추출 결과
  ftg/<사이클>/            추출한 셀 파일
"""
from __future__ import annotations

import csv
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable

from .control import write_json_atomic
from .proc import console

# 엔진과 감시자(cellbench/supervisor.py)가 함께 읽고 쓰는 파일 이름 — 한 곳에만 둔다
CYCLES_FILE = "cycles.csv"
NOW_FILE = "now.json"
ENGINE_FILE = "engine.json"
BEAT_MIN_GAP_S = 5.0        # 추출 중 심박은 이보다 자주 쓰지 않는다 (셀 24대 로그마다 5 KB 파일을 다시 쓰지 않게)
CLOUD_BEAT_S = 60.0         # 추출 중에는 심박 때 클라우드 '지금 상태'도 이만큼마다 올린다 — 클라우드 심박 감시(5분)의 거짓 경보를 막는다

# 이 종류의 이상은 휴대폰 알림으로도 보낸다 (cycle 끝 요약은 cycle() 에서 따로).
# 안전망(FMEA P4)이 더한 것: 사람이 봐야 하는 것만 — 저절로 처리되는 cell_waiting · 셀별 extract · identity_stale 은 기록만 한다.
ALERT_KINDS = {"blind", "aborted", "crash", "need_human", "plug", "charge_timeout", "missing_cells",
               "manual", "wifi_reconnect", "stopped",
               "dock_power", "manual_plug", "cell_not_charging", "cell_storage", "cell_storage_critical",
               "cell_storage_full", "disk", "disk_critical", "identity", "interrupted_plug_off",
               "pair_mismatch", "discharge_timeout",     # 다중 세트 명세 3-6 — 고장일 때만 난다
               "manual_rejected",                        # 명세 2-7 — 세트 지정이 맞지 않아 버린 명령 (manual 과 같게 알린다)
               "set_hold", "fenced", "reconfigure",       # 다중 세트 — 세트 HOLD · 울타리 재시작 · 세트 구성 변경 재시작
               "progress", "dock_no_recharge",            # 놀지 않기(10-11) — 누적 100사이클마다 진척 · 예비 충전 중 Dock 재충전 안 함
                                                          # (target_reached 는 기록만 — 진척 알림이 Slack 몫이다)
               "onboard_start", "onboard_calib_done", "onboard_verified", "onboard_run_on", "onboard_joined", "onboard_failed",
                                                          # 자동 합류 — 시작 · 보정 끝 · 검증 통과 · 운전 켬 · 합류 완료 · 실패(onboard.py)
               "charge_timeout_streak"}                   # 충전 한도 초과가 charge_timeout_max 사이클 연속 — 엔진이 failsafe 로 멈춤(검토 F4)

# 이상 종류별 신호등 색 — 결과판·클라우드의 신호등이 events.csv 의 kind 로 색을 고른다.
# 한 종류는 한 색이다. 같은 현상이 두 단계면 종류를 나눴다(cell_storage / cell_storage_critical, disk / disk_critical).
# 지금 상태(저장량 %·디스크 여유)는 이 표가 아니라 now.json 의 metrics 로 본다 — 이 표는 '무슨 일이 있었나'의 색이다.
EVENT_LIGHT = {
    # 기존
    "live_gap": "yellow", "missing_cells": "yellow", "wifi_reconnect": "yellow", "plug": "yellow",
    "extract": "yellow", "no_resume": "yellow", "charge_timeout": "yellow",
    "manual": "yellow", "stopped": "yellow",            # 사람이 원격으로 개입했다 — 고장은 아니지만 알아 둘 일
    "manual_expired": "yellow",                         # 원격 명령이 너무 늦게 와(command_max_age_s) 실행하지 않고 버렸다 (QA C-1)
    "blind": "red", "aborted": "red", "crash": "red", "need_human": "red",
    # 안전망 (FMEA P4)
    "cell_storage": "yellow", "cell_storage_critical": "red", "cell_storage_full": "red",
    "dock_power": "red", "manual_plug": "yellow", "cell_not_charging": "yellow",
    "disk": "yellow", "disk_critical": "red", "cell_waiting": "yellow",
    "identity": "red",          # 주소-시리얼 불일치(주소 충돌 ①②④)로 받기·지우기를 멈췄다 — 다른 셀의 데이터를 지울 뻔했다는 뜻 (cells.identity_problem)
    "identity_stale": "yellow",  # 라이브가 끊겨(③ 신선도만) 신원을 확인할 수 없어 받지·지우지 않았다 — 대기·저장 가득 참 셀. 주소 충돌의 증거는 아니다
    "interrupted_plug_off": "yellow",   # 사람이 Ctrl+C 로 멈췄는데 플러그가 꺼져 있다 — 셀이 방전 중 (감시자가 일시 중지 표지가 없으면 켠다)
    # 방향 대조 · 방전 한도 (다중 세트 명세 3-6 · D2 · D3 — 모든 모드)
    "pair_mismatch": "red",       # 플러그와 셀의 짝이 틀렸다는 증거 — 등록된 모든 플러그를 켜고 엔진을 failsafe 로 끝냈다
    "discharge_timeout": "red",   # 방전이 7시간 넘게 기준선에 닿지 않아 충전으로 넘겼다
    "manual_rejected": "yellow",  # 세트 지정이 이 엔진의 운전 세트와 맞지 않아 실행하지 않고 버린 원격 명령 (명세 2-7)
    "extract_skipped": "yellow",  # 추출 문을 30분 넘게 기다려 이번 추출을 건너뛰었다 (명세 2-6)
    "set_hold": "red",            # 세트가 HOLD 로 갔다 — 다른 세트의 짝 이상 · 실행기 오류 (플러그 ON 으로 지킨다)
    "fenced": "red",              # 세트 실행기 심박이 멈춰 모든 플러그를 켜고 엔진을 다시 띄운다 (D9)
    "reconfigure": "yellow",      # 세트 구성이 바뀌어 경계에서 엔진을 다시 띄운다 (명세 2-8)
    "set_not_ready": "yellow",    # 세트 k 운전이 켜졌지만 관문(짝 · 보정 · 스키마)이 모자라 돌지 않는다 — 기록만
    "pair_check": "yellow",       # 짝 검증 시작 · 끝 — 기록만
    # 자동 합류 (PIN 한 번 · 보정 → 문턱 도출 → 짝 검증 → 운전 켜기 → 다음 경계 합류, onboard.py) — 진행은 노랑, 어느 단계든 실패하면 빨강
    "onboard_start": "yellow", "onboard_calib_done": "yellow", "onboard_verified": "yellow", "onboard_run_on": "yellow",
    "onboard_joined": "yellow", "onboard_failed": "red",
    "set_silent": "yellow",       # 엔진이 운전하지 않는 등록 세트의 셀이 15분 넘게 하나도 안 들린다 — 감시자의 세트 지키기가 신호등 '플러그' 차선으로 알린다
    # 시험대가 놀지 않게 (2026-10-11 사고)
    "target_reached": "yellow",   # 이번 실행의 --cycles 를 다 돌았지만 시험 목표(target_cycles)까지 멈추지 않고 계속 돈다 — 기록만
    "progress": "yellow",         # 누적 사이클이 progress_notify_every(100)의 배수에 닿았다 — "목표 500 중 100사이클 완료" (Slack 1건)
    "dock_no_recharge": "yellow", # 예비 충전 중 전력이 바닥에서 평탄한데 셀이 95% 미만 — Dock 이 재충전하지 않아 30초 끊었다 켰다(1회)
    # 심박 자동 재연결 (cycle._watch_hr — bench.json 세트 줄의 hr_pairs 가 있는 세트만)
    "hr_repair": "yellow",        # 짝 보드 켜짐 · 연결 0 인데 셀 심박이 10분 넘게 0 — 그 셀에 이름(0x20)을 다시 보냈다 (셀당 30분에 한 번)
    "hr_repair_fail": "yellow",   # 보내도 심박이 돌아오지 않은 채 3번 — 그 셀의 자동 재연결을 멈췄다(심박이 돌아오면 풀린다). 사람 확인 필요
    "charge_timeout_streak": "red",   # 충전 한도(charge_timeout_h) 초과가 charge_timeout_max(3)사이클 연속 — 플러그 ON · failsafe 로 멈춤, 사람 확인(검토 F4)
    # 클라우드만 남기는 것 (supabase/migrations/20261009235950_heartbeat_watch.sql — events.csv 에는 없다)
    "heartbeat_lost": "red",                            # 시험대 PC 소식이 5분 넘게 끊김
    "heartbeat_back": "yellow",                         # 다시 들어옴 — 고장은 아니지만 직전에 끊겼다는 기록
}
RECENT_KEEP_S = 86400.0     # metrics 의 시간 창(1시간·24시간)을 세려고 이만큼의 이상을 메모리에 들고 있는다
# 다중 세트에서 세트 1 실행기와 조정자가 같은 data/events.csv · run.log 에 쓴다 — 줄 하나씩 붙이는 쓰기를 잠금으로 묶는다(명세 2-4).
# 잎 잠금이다: 쥔 채 다른 잠금을 잡지 않는다
_FILE_LOCK = threading.Lock()


def _ts(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def _read_recent(path: Path, since: float) -> list[tuple[float, str]]:
    """events.csv 에서 since 이후의 (시각, 종류). 시각은 이 PC 의 현지 시각 문자열이다. 못 읽는 줄은 건너뛴다."""
    out: list[tuple[float, str]] = []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    t = time.mktime(time.strptime(row.get("time") or "", "%Y-%m-%d %H:%M:%S"))
                except (ValueError, OverflowError):
                    continue
                if t >= since:
                    out.append((t, row.get("kind") or ""))
    except OSError:
        pass
    return out


def cycles_done(path: str | Path) -> int:
    """cycles.csv 에 기록된 사이클 수 (머리글 제외). 파일이 없으면 0.

    중단(crash · aborted · stopped)된 사이클도 한 줄로 남으므로 '번호를 쓴 사이클 수'다.
    엔진의 다음 사이클 번호(Recorder.next_cycle_no)와 감시자의 남은 사이클 계산이 같은 셈을 쓴다.
    """
    try:
        with open(path, encoding="utf-8-sig") as f:
            return max(0, sum(1 for _ in f) - 1)
    except FileNotFoundError:
        return 0


_PROGRESS_RE = re.compile(r"중 (\d+)사이클 완료")


def last_progress(path: str | Path) -> int:
    """events.csv 의 progress 행 중 가장 큰 진척 단계(사이클 수). 없거나 못 읽으면 0.

    엔진이 다시 뜰 때 '어디까지 알렸나'를 이것으로 복원한다(검토 F10) — 누적 사이클 수로 어림하면 사이클 100 을 기록한 직후 알리기 전에
    죽은 엔진의 알림이 빠진다. 행의 detail 은 cycle._progress 가 쓰는 "목표 N 중 M사이클 완료 · …" 이고, 여기서 M 을 읽는다.
    """
    best = 0
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if (row.get("kind") or "") != "progress":
                    continue
                m = _PROGRESS_RE.search(row.get("detail") or "")
                if m:
                    best = max(best, int(m.group(1)))
    except OSError:
        pass
    return best


def avg_discharge_h(rows: list[dict]) -> float | None:
    """cycles.csv 줄들의 방전 시간(discharge_h) 평균 — 진척 알림("누적 방전 평균 Nh")의 재료. 빈칸(중단된 사이클)은 빼고, 하나도 없으면 None."""
    vals = []
    for r in rows or []:
        try:
            v = float(str(r.get("discharge_h") or "").strip())
        except ValueError:
            continue
        if v > 0:
            vals.append(v)
    return sum(vals) / len(vals) if vals else None


def read_rows(path: str | Path) -> list[dict]:
    """CSV 를 줄(dict) 목록으로 — 감시자의 신호등이 cycles.csv 의 만충 시간 추세를 본다. 없거나 못 읽으면 빈 목록."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []


class Recorder:
    CYCLE_COLS = ["cycle", "start", "discharge_start", "discharge_end", "discharge_h", "min_batt_at_stop",
                  "plug_on", "extract_start", "extract_end", "extract_s", "extract_ok", "extract_mb",
                  "full_at", "charge_min", "charge_wh", "floor_w", "plug_off", "events", "note"]
    SAMPLE_COLS = ["time", "phase", "plug", "watts", "wh", "cells", "batt_min", "batt_avg", "batt_max", "hr_cells"]
    EVENT_COLS = ["time", "cycle", "phase", "kind", "serial", "detail"]
    CELL_COLS = ["cycle", "serial", "ip", "battery", "size_mb", "got_mb", "bad_blocks", "ended", "deleted",
                 "seconds", "resume_s", "error", "file"]

    def __init__(self, data_dir: str | Path, alert: Callable[[str], None] | None = None, cloud=None, set_id=None):
        from .cloud import NoCloud
        self.set_id = set_id                      # 다중 세트 — id ≠ 1 세트만 알림 글에 '세트 k' 를 붙인다(B-H2). None · 1 은 지금 글 그대로
        self.alert = alert or (lambda text: None)
        self.cloud = cloud or NoCloud()
        self._cycle_no: int | None = None
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._cycles = self.dir / CYCLES_FILE
        self._events = self.dir / "events.csv"
        self._ensure(self._cycles, self.CYCLE_COLS)
        self._ensure(self._events, self.EVENT_COLS)
        self._sample_path: Path | None = None
        self.log_path = self.dir / "run.log"
        self._last_now: dict | None = None
        self._now_lock = threading.Lock()         # 추출 중에는 셀 묶음의 작업 스레드들이 심박을 찍는다
        self._engine: dict | None = None
        self._cloud_t = 0.0                       # 클라우드에 '지금 상태'를 마지막으로 올린 시각 (now · beat)
        # (시각, 종류) — 엔진이 다시 떠도 '지난 1시간·24시간' 횟수가 0 으로 돌아가지 않게 events.csv 에서 이어받는다
        self._recent: list[tuple[float, str]] = _read_recent(self._events, time.time() - RECENT_KEEP_S)

    @staticmethod
    def _ensure(path: Path, cols: list[str]) -> None:
        if not path.exists():
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(cols)

    @staticmethod
    def _append(path: Path, row: list) -> None:
        with _FILE_LOCK, open(path, "a", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow(row)

    @property
    def tag(self) -> str:
        """알림 글의 세트 칸 — id ≠ 1 세트만 ' · 세트 k'. 세트 1 의 글은 모드와 상관없이 지금 그대로다(명세 5-1)."""
        return "" if self.set_id in (None, 1) else f" · 세트 {self.set_id}"

    def log(self, msg: str) -> None:
        """run.log 에 한 줄 쓰고 화면에도 보인다. 파일을 먼저 쓰고, 화면 출력은 실패해도 예외를 내지 않는다(proc.console) —
        감시자가 창 없이 띄운 엔진은 '—' 한 글자를 화면에 못 써서 죽었고, 그 예외를 남기려던 로그도 같은 글자로 다시 죽었다(검토 F1)."""
        line = f"{_ts()} {msg}"
        with _FILE_LOCK, open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        console(line)

    def next_cycle_no(self) -> int:
        return cycles_done(self._cycles) + 1      # 기록 n줄 → 다음 번호 = n+1

    def recover_cut_charge(self) -> int | None:
        """충전 중에 끊긴 사이클을 cycles.csv 에 되살린다 (충전 중 엔진 교체 — restart_at_boundary.ps1 -InCharge · 비정상 종료 뒤 재시작).

        다음 번호 N(= 기록 줄 수 + 1)의 사이클이 '방전을 끝내고 추출까지 마친 뒤 충전 중에 끊겼다' — discharge_N.csv(방전 끝 기록)와
        cells_N.csv(추출 결과)가 있는데 cycles.csv 에 N 줄이 없다 — 면 그 사이클의 행을 쓴다. 방전 · 추출 칸은 채우고 충전 칸
        (full_at · charge_min · charge_wh · floor_w · plug_off)은 비우고 note=restart_in_charge. 줄이 생기므로 새 사이클 번호는 N+1 로 이어지고
        그 사이클의 samples · discharge · cells 파일은 그대로 남아 새 사이클과 섞이지 않는다 — 고치기 전에는 같은 번호 N 을 다시 써서
        discharge_N.csv 에 같은 셀 줄이 겹쳤다.
        방전 중(discharge_N.csv 가 아직 없다) 끊김은 방전이 무효이므로 기존대로 행 없이 같은 번호를 다시 쓴다. 추출 중(cells_N.csv 가 아직 없다)
        끊김도 기존 동작이다. 되살렸으면 그 번호, 아니면 None."""
        n = self.next_cycle_no()
        dis, cel = self.dir / f"discharge_{n:04d}.csv", self.dir / f"cells_{n:04d}.csv"
        if not dis.exists():
            return None
        d, c = read_rows(dis), (read_rows(cel) if cel.exists() else [])
        if not d:
            return None
        # 추출 파일이 없거나(추출을 건너뜀 · 추출 중 끊김) 쓰다 만 경우도 방전이 끝난 사이클이다 — 번호 N 을 다시 쓰면 discharge_N.csv 에 같은 셀 줄이 겹친다
        if not cel.exists():
            note = "restart_in_charge_no_cells"
        elif not c or any(r.get("ended") != "1" for r in c):
            note = "restart_in_charge_partial_cells"
        else:
            note = "restart_in_charge"
        samples = read_rows(self.dir / f"samples_{n:04d}.csv")
        dis_t = [r["time"] for r in samples if r.get("phase") == "DISCHARGE"]
        after = [r["time"] for r in samples if r.get("phase") in ("PLUG_ON", "EXTRACT", "CHARGE")]
        try:
            ends = [int(r["end_pct"]) for r in d if r.get("end_pct") not in (None, "")]
            mb = sum(float(r.get("got_mb") or 0) for r in c)
        except ValueError:
            ends, mb = [], 0.0
        row = {"cycle": n, "start": samples[0]["time"] if samples else "", "discharge_start": dis_t[0] if dis_t else "",
               "discharge_end": dis_t[-1] if dis_t else "", "discharge_h": d[0].get("hours", ""),
               "min_batt_at_stop": min(ends) if ends else "", "plug_on": after[0] if after else "",
               "extract_ok": sum(1 for r in c if r.get("ended") == "1" and r.get("bad_blocks") in ("0", "", None)),
               "extract_mb": f"{mb:.1f}", "events": 0, "note": note}
        self._append(self._cycles, [row.get(k, "") for k in self.CYCLE_COLS])
        self.cloud.cycle({k: row.get(k, "") for k in self.CYCLE_COLS})
        self.log(f"사이클 {n}: 방전은 끝났고 충전 중에 끊겼다 — cycles.csv 에 복구 행(note={note})을 썼다. 새 사이클은 {n + 1} 번")
        return n

    def cycles_done(self) -> int:
        """cycles.csv 에 번호를 쓴 사이클 수 — 시험 목표(target_cycles)와 비교하는 '누적'. 감시자의 cycles_done 과 같은 셈."""
        return cycles_done(self._cycles)

    def cycle_rows(self) -> list[dict]:
        return read_rows(self._cycles)

    def last_progress(self) -> int:
        """events.csv 에 남긴 마지막 진척 단계(사이클 수) — 없으면 0 (record.last_progress)."""
        return last_progress(self._events)

    def begin_cycle(self, cycle: int) -> None:
        self._cycle_no = cycle
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
        self.cloud.sample(time.time(), phase, self._cycle_no, plug_on, watts, wh, batts, len(batts))

    def event(self, cycle: int, phase: str, kind: str, serial: int | str, detail: str) -> None:
        self._append(self._events, [_ts(), cycle, phase, kind, serial, detail])
        self.log(f"이상[{kind}] 셀 {serial}: {detail}")
        self.cloud.event(time.time(), cycle, phase, kind, serial, detail)
        now = time.time()
        self._recent = [x for x in self._recent if now - x[0] <= RECENT_KEEP_S] + [(now, kind)]
        if kind in ALERT_KINDS:
            self.alert(f"[셀 시험대] {kind}{self.tag} · 사이클 {cycle} {phase} · {detail}")

    def count(self, kinds: set[str] | None, within_s: float, now: float | None = None) -> int:
        """지난 within_s 초 동안 남긴 이상의 수 (kinds 가 None 이면 모든 종류). 24시간까지만 센다."""
        now = time.time() if now is None else now
        return sum(1 for t, k in self._recent if now - t <= within_s and (kinds is None or k in kinds))

    def last(self, kinds: set[str]) -> float | None:
        """이 종류의 이상을 마지막으로 남긴 시각 (지난 24시간 안에 없으면 None)."""
        return max((t for t, k in self._recent if k in kinds), default=None)

    def cells(self, cycle: int, results: dict) -> None:
        path = self.dir / f"cells_{cycle:04d}.csv"
        self._ensure(path, self.CELL_COLS)
        for s, r in sorted(results.items()):
            self._append(path, [cycle, s, r.ip, r.battery if r.battery is not None else "",
                                # 크기 0 도 '0.00' 으로 남긴다 — 빈칸은 '상태를 못 받음'이라 저장량 추정이 둘을 가른다
                                f"{r.size/1048576:.2f}" if r.size is not None else "", f"{r.got/1048576:.2f}",
                                r.bad_blocks, int(r.ended), int(r.deleted),
                                f"{r.t_end - r.t_start:.0f}" if r.t_end else "",
                                f"{r.resume_s:.0f}" if r.resume_s is not None else "",
                                r.error or "", r.file or ""])

    def cycle(self, row: dict) -> None:
        self._append(self._cycles, [row.get(c, "") for c in self.CYCLE_COLS])
        self.cloud.cycle({c: row.get(c, "") for c in self.CYCLE_COLS})
        self.alert(f"[셀 시험대]{self.tag} 사이클 {row.get('cycle')} 끝 · 방전 {row.get('discharge_h') or '-'}h · 충전 {row.get('charge_min') or '-'}분 "
                   f"{row.get('charge_wh') or '-'}Wh · 추출 {row.get('extract_ok')}/24 · 이상 {row.get('events')}건"
                   + (f" · {row.get('note')}" if row.get("note") else ""))

    def now(self, payload: dict) -> bool:
        """결과판용 '지금 상태'. 반쯤 쓴 파일을 읽지 않도록 임시 파일에 쓰고 바꿔치기한다.

        Windows 에서는 결과판 서버가 now.json 을 여는 그 순간 바꿔치기가 '액세스 거부'로 실패한다
        (2026-10-07 18:19, 이것으로 시험 프로그램 전체가 멈췄다). 그래서 몇 번 다시 해 보고,
        끝내 안 되면 이번 갱신만 건너뛴다. 화면용 파일 하나 때문에 시험이 멈춰서는 안 된다.

        beat(마지막 진척 시각)를 함께 적는다 — 감시자가 이것이 멈춘 시간으로 엔진의 '멈춤(행)'을 판정한다.
        클라우드에는 받은 그대로 올린다(beat 는 이 PC 의 감시자용).
        """
        self.cloud.state(payload)
        with self._now_lock:
            self._cloud_t = time.time()
            self._last_now = {**payload, "beat": time.time()}
            return self._write_now(self._last_now)

    def beat(self) -> None:
        """진척 표시 — now.json 의 beat 만 새 시각으로 다시 쓰고, CLOUD_BEAT_S(60초)에 한 번은 클라우드 '지금 상태'도 올린다.

        추출 중에는 표본(_poll)이 돌지 않아 now.json 이 몇 분씩 멈추므로, 셀 하나하나의 추출 진척(CellLink 로그)마다
        이것을 불러 감시자가 추출을 '멈춤'으로 오판하지 않게 한다. 별도 스레드로 주기적으로 찍지 않는다 —
        그러면 흐름이 멈춰도 심박이 살아 있어 감시자가 '멈춤'을 잡지 못한다.
        클라우드의 심박 감시(bench_state.updated_at 이 5분 넘게 묵으면 heartbeat_lost)도 같은 이유로 추출 중에 거짓 경보를 냈다(검토 F6).
        그래서 클라우드에도 같은 내용(now.json 그대로, beat 포함)을 올리되, 전송 목록이 넘치지 않게 60초에 한 번만 올린다.
        이번 실행에서 now.json 을 한 번도 쓰지 않았으면(시작 직후) 아무것도 하지 않는다 — 이전 실행의 화면을 지우지 않게.
        """
        up = None
        with self._now_lock:
            if self._last_now is None or time.time() - self._last_now.get("beat", 0) < BEAT_MIN_GAP_S:
                return
            self._last_now = {**self._last_now, "beat": time.time()}
            self._write_now(self._last_now)
            if time.time() - self._cloud_t >= CLOUD_BEAT_S:
                self._cloud_t = time.time()
                up = self._last_now
        if up is not None:
            try:
                self.cloud.state(up)              # 전송은 클라우드의 작업 스레드가 한다 — 여기서는 목록에 넣기만
            except Exception:
                pass

    def _write_now(self, payload: dict) -> bool:
        tmp = self.dir / (NOW_FILE + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            return False
        for _ in range(10):
            try:
                os.replace(tmp, self.dir / NOW_FILE)
                return True
            except PermissionError:
                time.sleep(0.05)
        return False

    # --- 감시자가 읽는 엔진 상태 ---
    def engine_start(self, info: dict) -> None:
        """data/engine.json — 이 엔진은 누구고(pid · started), 어디까지 돌 것이며(target_last_cycle), 어떻게 끝났나(exit).

        exit 는 시작 때 null 이고, 끝날 때 done · stopped · failsafe · interrupted · no_cells 중 하나로 바뀐다
        (설정 오류는 run_cycle.py 가 Recorder 없이 config_error 로 남긴다). 예외로 죽거나 강제로 끝나면 null 로 남고,
        감시자는 그것을 '비정상 종료'로 보고 되살린다.
        """
        self._engine = {**info, "exit": None}
        write_json_atomic(self.dir / ENGINE_FILE, self._engine)

    def engine_update(self, **kw) -> None:
        """engine.json 에 칸을 더하거나(값) 지운다(None) — 짝 검증 중 verifying 처럼 운전 중에 바뀌는 것 (다중 세트 명세 5-1).
        검증기 스레드와 실행기가 함께 부르므로 잠금 안에서 쓴다."""
        with self._now_lock:
            if self._engine is None:
                return
            for k, v in kw.items():
                if v is None:
                    self._engine.pop(k, None)
                else:
                    self._engine[k] = v
            write_json_atomic(self.dir / ENGINE_FILE, self._engine)

    def engine_exit(self, kind: str, plug_on: bool | None = None) -> None:
        """끝난 이유를 남긴다. plug_on 은 끝내며 플러그를 켜 두었는가(못 켰으면 False — 감시자가 대신 켠다)."""
        if self._engine is None:
            return
        self._engine.update(exit=kind, ended=time.time(), plug_on=plug_on)
        write_json_atomic(self.dir / ENGINE_FILE, self._engine)

    @property
    def engine_exit_kind(self) -> str | None:
        return (self._engine or {}).get("exit")

    HOLD_COLS = ["time", "cycle", "event", "phase", "reason"]

    def hold(self, cycle: int, event: str, phase: str, reason: str) -> None:
        """안전 상태(RECOVER · HOLD) 체류 기록 — holds.csv 에 들어감(enter) · 나옴(leave) 한 줄씩 (다중 세트 명세 D15).
        cycles.csv 와 따로 둔다. 고장일 때만 생기는 파일이다(명세 7-2 허용 차이)."""
        path = self.dir / "holds.csv"
        self._ensure(path, self.HOLD_COLS)
        self._append(path, [_ts(), cycle, event, phase, reason])

    DISCHARGE_COLS = ["cycle", "serial", "start_pct", "end_pct", "hours", "pct_per_h", "est_runtime_h"]

    HR_COLS = ["cycle", "serial", "board", "samples", "live", "hr_ok", "hr_ratio"]

    def hr_ratio(self, cycle: int, rows: list[dict]) -> None:
        """셀별 심박 수신 비율 — hr_####.csv (심박 짝 표가 있는 세트만 · 사이클마다 방전이 끝날 때 한 번).
        samples = 방전 구간 표본 수, live = 그중 라이브가 들린 표본, hr_ok = 심박 > 0 인 표본, hr_ratio = hr_ok / samples (0~1).
        cycles.csv 에 열을 더하지 않은 까닭: cycles.csv 는 사이클 한 줄이라 셀 24대의 값이 들어갈 자리가 없고, 머리줄은 처음 만들 때만 쓰여
        이미 쌓인 파일(TestPC 20사이클)에 열을 더하면 옛 줄과 칸이 어긋난다. discharge_####.csv 와 같은 꼴의 사이클별 파일로 둔다."""
        if not rows:
            return
        path = self.dir / f"hr_{cycle:04d}.csv"
        self._ensure(path, self.HR_COLS)
        for r in rows:
            self._append(path, [cycle, r["serial"], r.get("board", ""), r["samples"], r["live"], r["hr_ok"],
                                f"{r['hr_ok'] / r['samples']:.3f}" if r["samples"] else ""])

    def discharge(self, cycle: int, start: dict[int, int], end: dict[int, int], seconds: float, stop_pct: int) -> None:
        """셀별 방전 속도. 방전은 가장 빠른 셀이 기준선에 닿으면 끝나므로, 셀마다 '작동시간'은
        직접 잴 수 없다. 대신 같은 시간 동안 몇 % 내려갔는지로 100%→기준선 작동시간을 환산한다."""
        path = self.dir / f"discharge_{cycle:04d}.csv"
        self._ensure(path, self.DISCHARGE_COLS)
        hours = seconds / 3600
        rows = []
        for s in sorted(set(start) & set(end)):
            drop = start[s] - end[s]
            rate = drop / hours if hours > 0 else 0
            est = (100 - stop_pct) / rate if rate > 0 else ""
            self._append(path, [cycle, s, start[s], end[s], f"{hours:.3f}", f"{rate:.2f}",
                                f"{est:.2f}" if est != "" else ""])
            rows.append({"cycle": cycle, "serial": s, "start_pct": start[s], "end_pct": end[s], "hours": round(hours, 3),
                         "pct_per_h": round(rate, 2), "est_runtime_h": round(est, 2) if est != "" else None})
        if rows:
            self.cloud.discharge(rows)
