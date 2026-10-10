"""안전망 판정 (FMEA P4) — 엔진이 스스로 잡을 수 있는 고장을 가려내는 순수 함수와 작은 상태 기계.

장비·시계에 닿지 않는다(시각은 인자로 받는다). cycle.py 의 CycleRunner 가 표본마다 이것을 부르고, 판정에 따라
플러그·셀을 움직이고 이상을 남긴다. 그래서 장비 없이 검사한다(tests/test_guards.py).
파일을 읽는 것은 엔진 시작 때 한 번 부르는 storage_from_cells_csv 하나뿐이다.

무엇을 자동으로 하나 — 원칙은 '모를 때는 충전 쪽'이다.
  - 플러그를 켜는 쪽(Dock 저전력 복구)은 자동이다. 셀이 다 꺼지면 사람이 Dock 버튼을 눌러야 해서 그동안 시험이 멈추기 때문이다.
  - 전원을 끊는 쪽은 manual_plug_action 의 "revert" 하나만 자동이다 — 방전 중에 누가 켠 플러그를 원래대로 끄는 것뿐이고,
    그것도 최저 배터리가 기준선보다 충분히(기본 10%p) 높을 때만이다. 방전 단계의 기대 상태가 OFF 이고(켜진 채 두면 그 사이클의
    작동시간 기록이 무의미해진다), 그 여유면 다음 표본(20초) 전에 셀이 기준선 아래로 내려갈 수 없다.
"""
from __future__ import annotations

import csv
import math
import re
import statistics
import time
from pathlib import Path

MB = 1048576

# 단계마다 플러그가 어때야 하나. True = 켜져 있어야(충전 쪽) · False = 꺼져 있어야(방전).
# 없는 단계(THRESHOLD · PLUG_OFF)는 바꾸는 순간이라 보지 않는다.
PLUG_EXPECT = {"DISCHARGE": False, "PRECHARGE": True, "PLUG_ON": True, "EXTRACT": True, "CHARGE": True,
               "FULL": True, "RECOVER": True, "DONE": True, "STOPPED": True}


# ---------- Dock 저전력 (5.2 · 5.3) ----------

def power_stuck(on: bool | None, w: float, stuck_w: float) -> bool | None:
    """플러그 읽기 하나로 'Dock 이 전력을 끌어 쓰지 않는다'를 판정한다.

    꺼져 있거나, 켜져 있는데 stuck_w 아래면 True. 읽지 못했으면(on 이 None) None — 모르는 것은 판정을 바꾸지 않는다.
    전력을 못 읽은 켜짐(W 가 NaN)은 False 로 본다(꺼짐은 확실하지 않으므로).
    """
    if on is None:
        return None
    if on is False:
        return True
    return not math.isnan(w) and w < stuck_w


class PowerWatch:
    """플러그가 켜져 있어야 하는 단계 하나를 지켜본다. 단계가 바뀌면 새로 만든다(시도 횟수는 단계 한 번당).

    feed() 가 돌려주는 것: "recharge"(끊었다 켜라) · "give_up"(한도를 다 썼는데도 안 된다 — 한 번만) · None.
    """

    def __init__(self, stuck_w: float, stuck_s: float, gap_s: float, max_tries: int):
        self.stuck_w, self.stuck_s, self.gap_s, self.max_tries = stuck_w, stuck_s, gap_s, max_tries
        self.since: float | None = None      # 막힌 것이 처음 보인 시각
        self.tries = 0
        self.gave_up = False

    def feed(self, now: float, on: bool | None, w: float) -> str | None:
        stuck = power_stuck(on, w, self.stuck_w)
        if stuck is None:
            return None
        if not stuck:
            self.since = None
            return None
        self.since = now if self.since is None else self.since
        if now - self.since < self.stuck_s:
            return None
        self.since = None
        if self.tries < self.max_tries:
            self.tries += 1
            return "recharge"
        if not self.gave_up:
            self.gave_up = True
            return "give_up"
        return None


# ---------- 방전 중 켜진 플러그 (4.4 · 8.1) ----------

def manual_plug_action(min_batt: int | None, stop_pct: int, margin_pct: int) -> str:
    """방전 중 원격 명령 없이 켜진 플러그를 어떻게 할까 — "revert"(다시 끈다) 또는 "charge"(그대로 충전 단계로 넘긴다).

    최저 배터리가 기준선 + margin 이하이면 끄지 않는다 — 끄면 곧 기준선이고, 켜진 김에 충전으로 가는 것이 안전하다.
    배터리를 모르면(셀이 안 들림) 충전 쪽이다.
    """
    if min_batt is None or min_batt <= stop_pct + margin_pct:
        return "charge"
    return "revert"


# ---------- 접촉 불량 셀 (5.5) ----------

def not_charging(start: dict[int, int], now: dict[int, int], median_rise: int, min_rise: int, full_pct: int) -> list[int]:
    """충전 중인데 혼자 오르지 않는 셀. start = 충전 시작 때 셀별 배터리, now = 지금.

    다른 셀들이 충분히 올랐을 때(상승 중앙값 ≥ median_rise)만 판정한다 — 그 전에는 '아직 덜 충전된 것'과 구분되지 않는다.
    그때 min_rise 도 못 올랐고 아직 full_pct 아래인 셀이 Dock 자리 접촉 불량 후보다. 두 시각 모두 들린 셀만 본다.
    """
    common = [s for s in start if s in now]
    if not common:
        return []
    rise = {s: now[s] - start[s] for s in common}
    if statistics.median(rise.values()) < median_rise:
        return []
    return sorted(s for s in common if rise[s] < min_rise and now[s] < full_pct)


# ---------- 셀 저장량 (6.5) ----------

def storage_now(known: dict[int, tuple[float, float]], now: float, rate_mb_h: float) -> dict[int, float]:
    """셀별 지금 저장량 추정(MB). known = {시리얼: (마지막으로 안 크기 MB, 그 시각)}. 그 뒤로 rate 만큼 늘었다고 본다."""
    return {s: mb + max(0.0, now - t) / 3600 * rate_mb_h for s, (mb, t) in known.items()}


def level(value: float, warn: float, alarm: float) -> int:
    """0 = 정상 · 1 = 노랑(warn 이상) · 2 = 빨강(alarm 이상)."""
    return 2 if value >= alarm else 1 if value >= warn else 0


def storage_crossings(pct: dict[int, float], told: dict[int, int], warn: float, alarm: float) -> tuple[dict[int, int], dict[int, int]]:
    """새로 넘어선 단계만 고른다 → (새로 알릴 {시리얼: 단계}, 고친 told).

    told = 셀별 이미 알린 단계. 같은 단계는 다시 알리지 않고(셀당 한 번), 단계가 내려가면(추출 뒤 지웠다) 낮춰서
    다음에 다시 넘을 때 또 알린다. 0 에서 2 로 한 번에 넘으면 빨강만 알린다.
    """
    new: dict[int, int] = {}
    out: dict[int, int] = {}
    for s, p in pct.items():
        lv = level(p, warn, alarm)
        if lv > told.get(s, 0):
            new[s] = lv
        out[s] = lv
    return new, out


def storage_full_cells(pct: dict[int, float], gaps: dict[int, float], alarm: float, gap_s: float) -> list[int]:
    """'가득 차서 측정이 멈춘' 셀 — 라이브가 gap_s 넘게 끊겼고 추정 저장량이 alarm% 이상.

    2026-10-10 실측: 가득 찬 셀은 LiveHub 연결·명령 응답은 되지만 측정·라이브가 멈추고, 0x26 복귀로도 돌아오지 않는다.
    """
    return sorted(s for s, g in gaps.items() if g > gap_s and pct.get(s, 0.0) >= alarm)


def storage_summary(known: dict[int, tuple[float, float]], now: float, cap_mb: float, rate_mb_h: float) -> dict:
    """결과판·신호등용 요약 — {max_pct, max_serial, est_full_at(epoch 초)}. 아는 셀이 없으면 셋 다 None."""
    est = storage_now(known, now, rate_mb_h)
    if not est or cap_mb <= 0:
        return {"max_pct": None, "max_serial": None, "est_full_at": None}
    s = max(est, key=est.get)
    full_at = None
    if rate_mb_h > 0:
        full_at = round(min(now + max(0.0, cap_mb - mb) / rate_mb_h * 3600 for mb in est.values()))
    return {"max_pct": round(est[s] / cap_mb * 100, 1), "max_serial": s, "est_full_at": full_at}


def storage_after_extract(known: dict[int, tuple[float, float]], results: dict, now: float) -> dict[int, tuple[float, float]]:
    """추출 결과로 셀별 '마지막으로 아는 크기'를 고친다. 지웠으면 0, 상태 응답 크기를 받았으면 그 크기, 못 받았으면 그대로."""
    out = dict(known)
    for s, r in results.items():
        if r.deleted:
            out[s] = (0.0, r.t_end or now)
        elif r.size is not None:
            out[s] = (r.size / MB, r.t_start or now)
    return out


_FILE_TS = re.compile(r"_(\d{8}_\d{6})\.bin$")


def storage_from_cells_csv(data_dir: str | Path, serials: list[int]) -> dict[int, tuple[float, float]]:
    """직전 사이클들의 cells_<사이클>.csv 에서 셀별 '마지막으로 아는 크기'를 이어받는다 (엔진 시작 직후용).

    셀에 따로 묻지 않는다 — 묻는 동안 측정이 20초 멈춘다. 최근 파일부터 보고, 파일 안에서는 뒤의 줄이 최신이다(같은 사이클을
    다시 돌리면 같은 파일에 덧붙는다). 크기를 모르는 줄(셀이 안 들려 못 받음)은 건너뛰고 앞 파일에서 찾는다.
    시각: 추출 파일 이름의 시각(ftg_<시리얼>_<YYYYmmdd_HHMMSS>.bin = 그 셀 추출 시작)을 쓰고, 지웠으면 거기에 걸린 초를 더한다.
    파일 이름이 없으면 csv 파일의 수정 시각(추출이 다 끝난 뒤 한꺼번에 쓰인다)을 쓴다.
    """
    want = set(serials)
    out: dict[int, tuple[float, float]] = {}
    for f in sorted(Path(data_dir).glob("cells_*.csv"), reverse=True):
        if want <= set(out):
            break
        try:
            mtime = f.stat().st_mtime
            with open(f, encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
        except (OSError, ValueError):
            continue
        for row in reversed(rows):
            try:
                s = int(row.get("serial") or 0)
            except ValueError:
                continue
            if s not in want or s in out:
                continue
            t = _row_time(row, mtime)
            if (row.get("deleted") or "").strip() == "1":
                out[s] = (0.0, t)
            elif (row.get("size_mb") or "").strip():
                try:
                    out[s] = (float(row["size_mb"]), t)
                except ValueError:
                    continue
    return out


def _row_time(row: dict, fallback: float) -> float:
    m = _FILE_TS.search(row.get("file") or "")
    if not m:
        return fallback
    try:
        t = time.mktime(time.strptime(m.group(1), "%Y%m%d_%H%M%S"))
    except ValueError:
        return fallback
    if (row.get("deleted") or "").strip() == "1":
        try:
            t += float(row.get("seconds") or 0)
        except ValueError:
            pass
    return t


# ---------- 대기 모드 셀 (6.3) ----------

def waiting_due(waiting: dict[int, float], last_resume: dict[int, float], now: float, after_s: float, every_s: float) -> list[int]:
    """깨워 복귀시킬 셀. waiting = {시리얼: 마지막 라이브(0x09) 시각} — 지금 대기 모드인 셀만.

    라이브가 after_s 넘게 끊긴 채 대기 모드이고, 같은 셀을 every_s 안에 깨운 적이 없을 때.
    """
    return sorted(s for s, t in waiting.items()
                  if now - t >= after_s and now - last_resume.get(s, -math.inf) >= every_s)


# ---------- 디스크 (1.5) ----------

def disk_level(free_gb: float, warn_gb: float, alarm_gb: float) -> int:
    """0 = 정상 · 1 = 노랑(warn 아래) · 2 = 빨강(alarm 아래)."""
    return 2 if free_gb < alarm_gb else 1 if free_gb < warn_gb else 0
