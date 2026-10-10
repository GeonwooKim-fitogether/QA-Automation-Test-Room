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

    feed() 가 돌려주는 것: "recharge"(끊었다 켜라) · "give_up"(한도를 다 썼는데도 안 된다 — 한 번만) ·
    "plug_on"(한도를 다 쓴 뒤에도 꺼짐이 stuck_s 넘게 보인다 — 한도와 무관하게 켜라) · None.

    한도(max_tries)는 'Dock 이 끌어 쓰지 않는' 막힘을 몇 번 풀어 볼지다. 꺼진 플러그를 켜는 것은 한도와 무관하다 —
    포기한 뒤 꺼짐을 그대로 두면 셀이 방전된다(검토 F4). 켜기에 실패한 시도는 refund() 로 한도에서 뺀다 — 플러그가 응답하지 않은
    것은 Dock 을 풀어 본 것이 아니므로.
    """

    def __init__(self, stuck_w: float, stuck_s: float, gap_s: float, max_tries: int):
        self.stuck_w, self.stuck_s, self.gap_s, self.max_tries = stuck_w, stuck_s, gap_s, max_tries
        self.since: float | None = None      # 막힌 것이 처음 보인 시각
        self.tries = 0                       # 한도에 세는 시도 (켜기에 성공한 끊었다 켜기)
        self.attempts = 0                    # 모든 시도 (실패 포함) — _settle_power 가 추출을 무한정 미루지 않게 이것으로 끊는다
        self.fails = 0                       # 켜기에 연달아 실패한 수 — 이상은 연달아 실패한 첫 번째만 남긴다(1분마다 쌓이지 않게)
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
            self.attempts += 1
            return "recharge"
        if not self.gave_up:
            self.gave_up = True
            return "give_up"
        if on is False:
            self.attempts += 1
            return "plug_on"
        return None

    def refund(self) -> None:
        """방금 시도가 플러그를 켜지 못했다 — 한도에 세지 않는다(다음 feed 에서 다시 한다)."""
        if self.tries > 0 and not self.gave_up:
            self.tries -= 1
        self.fails += 1

    def worked(self) -> None:
        """방금 시도가 플러그를 켰다 — 연달아 실패한 수를 되돌린다."""
        self.fails = 0


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


def storage_fresh(known: dict[int, tuple[float, float]], now: float, max_age_s: float) -> dict[int, tuple[float, float]]:
    """믿을 수 있는 것만 — 마지막으로 잰 지 max_age_s 안인 셀. 더 오래된 셀은 '모름'이다(엔진 밖에서 지웠을 수 있다, 검토 F7).
    모르는 셀로는 빨강·Slack 을 내지 않는다."""
    return {s: v for s, v in known.items() if now - v[1] <= max_age_s}


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


def storage_from_cells_csv(data_dir: str | Path, serials: list[int], now: float | None = None,
                           max_age_s: float | None = None, need_delete: bool = False) -> dict[int, tuple[float, float]]:
    """직전 사이클들의 cells_<사이클>.csv 에서 셀별 '마지막으로 아는 크기'를 이어받는다 (엔진 시작 직후용).

    셀에 따로 묻지 않는다 — 묻는 동안 측정이 20초 멈춘다. 최근 파일부터 보고, 파일 안에서는 뒤의 줄이 최신이다(같은 사이클을
    다시 돌리면 같은 파일에 덧붙는다). 크기를 모르는 줄(셀이 안 들려 못 받음)은 건너뛰고 앞 파일에서 찾는다.
    시각: 추출 파일 이름의 시각(ftg_<시리얼>_<YYYYmmdd_HHMMSS>.bin = 그 셀 추출 시작)을 쓰고, 지웠으면 거기에 걸린 초를 더한다.
    파일 이름이 없으면 csv 파일의 수정 시각(추출이 다 끝난 뒤 한꺼번에 쓰인다)을 쓴다.

    '모름'으로 빼는 것 (돌려주는 dict 에 넣지 않는다 — 검토 F7). 그 셀은 앞 파일에서도 찾지 않는다(앞 파일은 더 오래됐다):
      · 그 셀의 가장 최근 줄이 now 보다 max_age_s 넘게 오래됐다 — 그 사이 엔진 밖에서 지웠을 수 있다
      · need_delete(지금 삭제를 켜 둠)인데 그 줄이 '삭제를 켜기 전' 추출이다 — 같은 파일의 어느 줄도 지우지 않았으면 그때는 삭제가
        꺼져 있었던 것으로 본다. 삭제를 켜던 무렵 사람이 손으로 지운 셀들이 있어, 그 전 기록의 크기는 지금과 다를 수 있다
    now · max_age_s 를 주지 않으면 나이를 보지 않는다.
    """
    want = set(serials)
    out: dict[int, tuple[float, float]] = {}
    unknown: set[int] = set()
    for f in sorted(Path(data_dir).glob("cells_*.csv"), reverse=True):
        if want <= set(out) | unknown:
            break
        try:
            mtime = f.stat().st_mtime
            with open(f, encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
        except (OSError, ValueError):
            continue
        deleting = any((r.get("deleted") or "").strip() == "1" for r in rows)
        for row in reversed(rows):
            try:
                s = int(row.get("serial") or 0)
            except ValueError:
                continue
            if s not in want or s in out or s in unknown:
                continue
            t = _row_time(row, mtime)
            deleted = (row.get("deleted") or "").strip() == "1"
            if not deleted and not (row.get("size_mb") or "").strip():
                continue                                    # 크기를 모르는 줄 — 앞 파일에서 찾는다
            if (now is not None and max_age_s is not None and now - t > max_age_s) or (need_delete and not deleting):
                unknown.add(s)
                continue
            if deleted:
                out[s] = (0.0, t)
            else:
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


# ---------- 짝 이상 — 방향 대조 (다중 세트 명세 3-6, 모든 모드) ----------

def median_change(before: dict[int, int], after: dict[int, int], min_cells: int) -> float | None:
    """창 양 끝 모두에서 들린 셀의 '셀별 변화량'(after − before)의 중앙값. 그런 셀이 min_cells 보다 적으면 None (판정 보류)."""
    common = [s for s in before if s in after]
    if len(common) < min_cells:
        return None
    return float(statistics.median(after[s] - before[s] for s in common))


class DirectionWatch:
    """세트 하나의 방향 대조 (a) 충전 쪽 하락 · (b) 방전 중 상승 · (c) 방전 시작 대조. 짝 이상이면 feed() 가 이유 한 줄을 돌려준다.

    (a) 충전 쪽(start_charge 뒤 after_s 부터): 창 window_s 내내 자기 플러그가 켜짐 · 전력 ≥ stuck_w 인데 drop 넘게 하락
        — 전력을 40 W 로 두면 만충 근처 Dock(29~40 W)의 교차 연결을 놓치고, 빼면 Dock 1.5 W 멈춤을 오탐한다
    (b) 방전(start_discharge 뒤): 창 내내 자기 플러그가 꺼짐인데 drop 넘게 상승
    (c) 방전 시작 start_s 안에 start_drop 만큼 내려가지 않음 (그동안 플러그가 꺼짐으로 읽혔을 때만)
    플러그를 못 읽은 표본(on 이 None)이 창에 있으면 그 창은 판정하지 않는다. 시각은 인자로 받는다(순수).
    """

    def __init__(self, rule: dict, stuck_w: float):
        self.r, self.stuck_w = rule, stuck_w
        self.mode: str | None = None
        self.armed = 0.0
        self.hist: list[tuple[float, dict[int, int], bool | None, float]] = []
        self.start: tuple[float, dict[int, int]] | None = None
        self.start_done = True

    def start_charge(self, t: float) -> None:
        self.mode, self.armed, self.hist, self.start, self.start_done = "charge", t + self.r["charge_after_s"], [], None, True

    def start_discharge(self, t: float, batts: dict[int, int]) -> None:
        self.mode, self.armed, self.hist = "discharge", t, []
        self.start, self.start_done = (t, dict(batts)), False

    def stop(self) -> None:
        self.mode, self.hist, self.start_done = None, [], True

    def feed(self, t: float, batts: dict[int, int], on: bool | None, w: float) -> str | None:
        if self.mode is None:
            return None
        self.hist.append((t, dict(batts), on, w))
        keep = t - self.r["window_s"] - 120
        while len(self.hist) > 1 and self.hist[1][0] <= keep:
            self.hist.pop(0)
        why = self._start_check(t, batts) if self.mode == "discharge" else None
        return why or self._window_check(t, batts)

    def _ok(self, on, w) -> bool:
        if self.mode == "charge":
            return on is True and w == w and w >= self.stuck_w
        return on is False

    def _window_check(self, t: float, batts: dict[int, int]) -> str | None:
        old = [h for h in self.hist if h[0] <= t - self.r["window_s"] and h[0] >= self.armed]
        if not old:
            return None
        t0 = old[-1][0]
        span = [h for h in self.hist if h[0] >= t0]
        if not all(self._ok(on, w) for _, _, on, w in span):
            return None
        ch = median_change(old[-1][1], batts, self.r["min_cells"])
        if ch is None:
            return None
        mins = (t - t0) / 60
        if self.mode == "charge" and ch < -self.r["drop_pct"]:
            return (f"충전 쪽인데 {mins:.0f}분 동안 플러그 켜짐 · {min(h[3] for h in span):.0f} W 이상에서 셀 중앙값이 "
                    f"{ch:+.0f}%p — 이 플러그가 이 셀들의 Dock 을 먹이지 않는다")
        if self.mode == "discharge" and ch > self.r["drop_pct"]:
            return f"방전 중인데 {mins:.0f}분 동안 플러그 꺼짐에서 셀 중앙값이 {ch:+.0f}%p — 다른 플러그가 이 셀들의 Dock 을 먹인다"
        return None

    def _start_check(self, t: float, batts: dict[int, int]) -> str | None:
        if self.start_done or self.start is None:
            return None
        t0, b0 = self.start
        if not all(on is False for h_t, _, on, _ in self.hist if h_t >= t0):
            self.start_done = True               # 플러그가 꺼짐으로 읽히지 않았다 — 이 방전은 대조하지 않는다
            return None
        ch = median_change(b0, batts, self.r["min_cells"])
        if ch is not None and ch <= -self.r["start_drop_pct"]:
            self.start_done = True
            return None
        if t - t0 >= self.r["start_s"] and ch is not None:
            self.start_done = True
            return (f"방전 시작(플러그 꺼짐) {(t - t0) / 60:.0f}분에 셀 중앙값이 {ch:+.0f}%p — {self.r['start_drop_pct']:g}%p 도 "
                    f"안 내려갔다. 이 플러그가 이 셀들의 Dock 을 먹이지 않는다")
        return None


class CrossWatch:
    """(d) 남의 세트 교차 — 운전하지 않는 등록 세트의 셀이 '엔진이 끈 뒤'에만 내려가는가.

    groups = {세트 id: 시리얼 목록}. 끄기(plug_off) 뒤 cross_s 안에 어느 무리가 cross_drop 넘게 내려갔고, 끄기 전 cross_s 동안은
    cross_still 이하로만 움직였으면 짝 이상이다. '끄기 전' 조건이 없으면 사람이 일부러 꺼 둔 세트(늘 내려간다) 때문에
    엔진의 정상적인 끄기마다 오탐한다.
    """

    def __init__(self, rule: dict, groups: dict):
        self.r, self.groups = rule, {k: set(v) for k, v in groups.items() if v}
        self.hist: list[tuple[float, dict[int, int]]] = []
        self.offs: list[float] = []

    def plug_off(self, t: float) -> None:
        self.offs.append(t)

    def feed(self, t: float, batts: dict[int, int]) -> str | None:
        if not self.groups:
            return None
        self.hist.append((t, dict(batts)))
        span = self.r["cross_s"]
        while len(self.hist) > 1 and self.hist[1][0] <= t - 2 * span - 120:
            self.hist.pop(0)
        self.offs = [o for o in self.offs if t - o <= span]
        for t_off in self.offs:
            before = [h for h in self.hist if h[0] <= t_off - span]
            at = [h for h in self.hist if h[0] <= t_off]
            if not before or not at:
                continue
            b_pre, b_off = before[-1][1], at[-1][1]
            for gid, ser in sorted(self.groups.items(), key=lambda x: str(x[0])):
                pick = lambda d: {s: v for s, v in d.items() if s in ser}
                pre = median_change(pick(b_pre), pick(b_off), self.r["min_cells"])
                post = median_change(pick(b_off), pick(batts), self.r["min_cells"])
                if pre is None or post is None:
                    continue
                if abs(pre) <= self.r["cross_still_pct"] and post < -self.r["cross_drop_pct"]:
                    self.offs = []
                    return (f"엔진이 플러그를 끈 뒤 {(t - t_off) / 60:.0f}분 동안 운전하지 않는 세트 {gid} 의 셀 중앙값이 "
                            f"{post:+.0f}%p (끄기 전 30분 {pre:+.0f}%p) — 끈 플러그가 세트 {gid} 의 Dock 을 먹인다")
        return None
