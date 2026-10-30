"""셀 vs 모터 — 시간 정렬과 비교. 순수 함수, 표준 라이브러리만.

시계: 모터 기록의 t_wall(motion_record.py)과 셀 라이브 수신 시각(cells.py:107 → live_tap.py)은 둘 다 이 PC 의 time.time() 이라
처음부터 같은 축이다. 추출(FTG) IMU 의 시각은 셀 시계(UTC)라 다르다. 그래서 둘 다 '동기 이동'(뚜렷한 짧은 왕복)을 상호상관해 맞춘다.
lag 의 뜻: 셀 시각 = 모터 시각 + lag. 동기 창이 둘 이상이면 드리프트 d(초/초)까지 — lag(t) = lag_A + d·(t − t_A).
모터 속도는 0x606C 가 PDO 에 없어(cia402.TX_MAP) 위치 차분이다. 위치 1 pulse ≈ 1 µm (dynamic-bench/README.md 스케일).
"""
from __future__ import annotations

import bisect
import csv
import itertools
import math
import operator
import statistics
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HIST_DT = 0.1                    # speed_history 간격 — 10 Hz (펌웨어)
NO_VALUE = 0xFFFD                # 이 값 이상은 펌웨어 SPEED_NAN_1~3(값 없음) — cell-y4 master 0d4099f5 include/constants.h


def kst(t: float) -> str:
    """PC 벽시계(epoch 초) → ISO 시각 KST."""
    return datetime.fromtimestamp(t, KST).isoformat(timespec="milliseconds")


def load_motion(path) -> tuple[list[float], list[float]]:
    """모터 2 ms 기록(motion_record.Log 의 CSV) → (t_wall, 위치 mm). WKC 오류 줄(위치 빈칸)은 건너뛴다."""
    t, p = [], []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("pos"):
                t.append(float(row["t_wall"]))
                p.append(int(row["pos"]) / 1000)
    return t, p


def interp(xs, ys, x: float) -> float:
    """선형 보간 · 끝 밖은 끝값(numpy.interp 처럼). xs 오름차순 — 같은 시각이 겹쳐도 된다."""
    i = bisect.bisect_right(xs, x)
    if i == 0:
        return ys[0]
    if i == len(xs):
        return ys[-1]
    x0, x1 = xs[i - 1], xs[i]
    return ys[i - 1] + (ys[i] - ys[i - 1]) * ((x - x0) / (x1 - x0) if x1 > x0 else 0.0)


def grid(t0: float, dt: float, n: int) -> list[float]:
    return [t0 + i * dt for i in range(n)]


def motor_speed(t, p, ts, dt: float = HIST_DT) -> list[float]:
    """시각 ts 마다 모터 속도 mm/s — 앞뒤 dt/2 의 위치 차분(= 그 dt 동안의 평균 속도, 10 Hz 이력과 같은 폭)."""
    return [(interp(t, p, x + dt / 2) - interp(t, p, x - dt / 2)) / dt for x in ts]


def motor_accel(t, p, ts, h: float = 0.02) -> list[float]:
    """시각 ts 마다 모터 가속도 mm/s² — 간격 h 의 2차 차분. 1 µm 양자화 잡음 ≈ 0.001/h² (h 20 ms 에서 2.5 mm/s²)."""
    return [(interp(t, p, x + h) - 2 * interp(t, p, x) + interp(t, p, x - h)) / h ** 2 for x in ts]


def motion_spans(t, p, v_min: float = 0.5, gap: float = 5.0, dt: float = HIST_DT) -> list[tuple[float, float]]:
    """모터가 움직인 구간 [(시작, 끝)] — |속도| > v_min mm/s, gap 초보다 가까운 것은 한 무리로. 첫 무리 · 마지막 무리 = 동기 창."""
    spans: list[list[float]] = []
    g = grid(t[0], dt, int((t[-1] - t[0]) / dt) + 1)
    for x, v in zip(g, motor_speed(t, p, g, dt)):
        if abs(v) > v_min:
            if spans and x - spans[-1][1] <= gap:
                spans[-1][1] = x
            else:
                spans.append([x, x])
    return [(a, b) for a, b in spans]


def xcorr(ref, sig) -> list[float]:
    """ref(n개)를 sig(n+K−1개) 위로 한 칸씩 밀며 피어슨 상관 K개. 어느 한쪽이 한결같으면 0.
    ponytail: 지연마다 O(n) 곱합 — 창 수 초 · 지연 수천 개면 1초 안. 기록이 길어져 느리면 numpy FFT 로."""
    n, m = len(ref), statistics.fmean(ref)
    r = [x - m for x in ref]
    rr = math.sqrt(sum(x * x for x in r))
    ms = statistics.fmean(sig)
    s = [x - ms for x in sig]                       # 먼저 평균을 빼 두면 누적합의 자리수 손실이 없다
    c1 = list(itertools.accumulate(s, initial=0.0))
    c2 = list(itertools.accumulate((x * x for x in s), initial=0.0))
    out = []
    for j in range(len(s) - n + 1):
        s1 = c1[j + n] - c1[j]
        var = c2[j + n] - c2[j] - s1 * s1 / n
        out.append(sum(map(operator.mul, r, s[j:j + n])) / (rr * math.sqrt(var)) if rr > 0 and var > 1e-12 else 0.0)
    return out


def find_offset(ref, sig_t, sig_v, t0: float, t1: float, dt: float, max_lag: float, signed: bool = False) -> dict:
    """모터 창 [t0, t1] 의 기준 신호와 셀 신호를 ±max_lag 안에서 상호상관한다.
    ref: 모터 시각 목록 → 값 목록. (sig_t, sig_v): 셀 표본(시각 오름차순).
    → lag(초, 셀 = 모터 + lag) · r(피크 상관, 부호 포함) · width(피크 반높이 폭 초) · edge(피크가 탐색 끝 — 범위를 넓혀 다시) · n(창 표본 수).
    signed: |r| 이 가장 큰 곳 — 축 방향을 모르는 가속도. lag 는 포물선 보간으로 dt 보다 잘게."""
    n, k = int(round((t1 - t0) / dt)) + 1, int(round(max_lag / dt))
    c = xcorr(ref(grid(t0, dt, n)), [interp(sig_t, sig_v, x) for x in grid(t0 - k * dt, dt, n + 2 * k)])
    s = [abs(x) for x in c] if signed else c
    j = max(range(len(s)), key=s.__getitem__)
    frac = 0.0
    if 0 < j < len(s) - 1 and (den := s[j - 1] - 2 * s[j] + s[j + 1]) < 0:
        frac = 0.5 * (s[j - 1] - s[j + 1]) / den
    lo = hi = j
    while lo > 0 and s[lo - 1] >= s[j] / 2:
        lo -= 1
    while hi < len(s) - 1 and s[hi + 1] >= s[j] / 2:
        hi += 1
    return {"lag": (j - k + frac) * dt, "r": c[j], "width": (hi - lo + 1) * dt, "edge": j in (0, len(s) - 1), "n": n}


def offsets(ref, sig_t, sig_v, wins, dt: float, max_lag: float, signed: bool = False) -> list[dict]:
    """동기 창마다 find_offset — 결과마다 창 가운데 모터 시각 t 를 더한다."""
    return [dict(find_offset(ref, sig_t, sig_v, a, b, dt, max_lag, signed), t=(a + b) / 2) for a, b in wins]


def drift(res: list[dict]) -> float | None:
    """첫 창과 마지막 창의 lag 차 / 시각 차 (초/초, ×1e6 = ppm). 창이 하나면 None."""
    if len(res) < 2 or res[-1]["t"] <= res[0]["t"]:
        return None
    return (res[-1]["lag"] - res[0]["lag"]) / (res[-1]["t"] - res[0]["t"])


def to_motor(tc: float, res: list[dict]) -> float:
    """셀 시각 → 모터 시각. 셀 = 모터 + lag_A + d·(모터 − t_A) 를 모터에 대해 푼 것."""
    a, d = res[0], drift(res) or 0.0
    return (tc - a["lag"] + d * a["t"]) / (1 + d)


def quality(res: list[dict], r_min: float = 0.5, agree_s: float = 0.05) -> str:
    """정렬을 믿어도 되나 [제안값] → "정렬됨" · "약함" · "정렬 불가". 어느 창이든 피크가 탐색 끝이면 정렬 불가.
    정렬됨: 모든 창의 피크 |r| ≥ r_min.
    약함: r 은 낮지만 서로 떨어진 첫 창과 마지막 창이 같은 lag(agree_s 안)를 냈다 — 잡음이 큰 것일 수 있다. 합성 시험에서 IMU 잡음
          5 mg · 가속 50 mm/s² 일 때 r 은 0.43 이었지만 lag 는 1 ms 안으로 맞았다. 두 창이 우연히 같은 칸에 떨어질 확률은 1 % 아래다.
          다만 같은 모양의 동기 이동이 두 창 모두 옆 봉우리에 걸리면 한 주기만큼 틀린 채 일치할 수 있다 — 동기 왕복 사이 간격을 서로 다르게 두는 이유."""
    if not res or any(x["edge"] for x in res):
        return "정렬 불가"
    if all(abs(x["r"]) >= r_min for x in res):
        return "정렬됨"
    if len(res) > 1 and abs(res[-1]["lag"] - res[0]["lag"]) <= agree_s:
        return "약함"
    return "정렬 불가"


def err_stats(e) -> dict:
    """오차 → 평균(치우침) · RMS · |오차| p95(최근접 순위) · |오차| 최대."""
    if not e:
        return {"n": 0}
    a = sorted(map(abs, e))
    return {"n": len(e), "mean": statistics.fmean(e), "rms": math.sqrt(statistics.fmean(x * x for x in e)),
            "p95": a[math.ceil(0.95 * len(a)) - 1], "max": a[-1]}


def compare(cell_t, cell_v, motor, res: list[dict], span: tuple[float, float], fit: str = "raw") -> dict:
    """정렬 뒤 셀 값 vs 모터 값(motor: 모터 시각 목록 → 값 목록), 모터 시각이 span 안인 표본만.
    k · b: 셀 ≈ k·모터 + b (fit="affine" 일 때만 b, 아니면 원점을 지난다) · r: 상관.
    오차(err_stats): fit="raw" 면 셀 − 모터 그대로(단위가 같을 때), 아니면 (셀 − b)/k − 모터 — 단위를 모르는 값을 모터 단위로 맞춘 것."""
    tm = [to_motor(x, res) for x in cell_t]
    keep = [i for i, x in enumerate(tm) if span[0] <= x <= span[1]]
    if len(keep) < 3:
        return {"n": 0}
    c, m = [cell_v[i] for i in keep], motor([tm[i] for i in keep])
    try:
        k, b = statistics.linear_regression(m, c, proportional=fit != "affine")
        r = statistics.correlation(m, c)
    except statistics.StatisticsError:            # 모터나 셀 값이 한결같다 — 배율도 상관도 없다
        k = b = r = None
    if fit == "raw":
        e = [x - y for x, y in zip(c, m)]
    else:
        e = [(x - b) / k - y for x, y in zip(c, m)] if k else []
    return {"k": k, "b": b, "r": r, **err_stats(e)}


def speed_series(pkts, dt: float = HIST_DT) -> tuple[list[float], list[float]]:
    """[(수신 시각, speed_history 오래된 → 최신)] (시각 순) → 겹치는 60초 창을 이은 10 Hz 시계열 (ts, vs).
    표본 i 의 시각 = 수신 시각 − (마지막 − i)·dt — 최신 표본을 수신 순간에 둔다. 남는 일정한 지연은 정렬 lag 가 흡수한다.
    앞 패킷이 이미 덮은 시각(반 칸 여유)은 건너뛴다. 펌웨어의 '값 없음'(NO_VALUE 이상)은 속도로 쓰지 않고 뺀다. ponytail: 수신 흔들림이 반 칸(50 ms)을 넘으면 표본 하나가 겹치거나 빠진다 — RMS 에는 무해."""
    ts, vs, last = [], [], -math.inf
    for t, hist in pkts:
        n = len(hist)
        for i, v in enumerate(hist):
            x = t - (n - 1 - i) * dt
            if x > last + dt / 2 and v < NO_VALUE:
                ts.append(x)
                vs.append(v)
        last = max(last, t)
    return ts, vs


def history_order(a, b, max_shift: int = 10) -> str | None:
    """연속한 두 패킷(a 다음 b, 약 0.5초 = 5칸 뒤)의 speed_history 로 순서 가정을 점검한다 — 엔디안 · 부호와 무관하다(같은 표본은 같은 값).
    → "as_is"(지금 해석대로 끝이 최신) · "reversed"(앞이 최신) · None(판정 불가 — 값이 거의 한결같다. 실내 fix 없음이면 전부 0).
    끝이 최신이면 b[i] == a[i+s], 앞이 최신이면 b[i+s] == a[i] 인 밀림 s 가 있어야 한다."""
    def best(x, y):
        return max(sum(map(operator.eq, y, x[s:])) / (len(x) - s) for s in range(1, max_shift + 1))
    fwd, rev = best(a, b), best(b, a)
    if fwd >= 0.95 > rev:
        return "as_is"
    if rev >= 0.95 > fwd:
        return "reversed"
    return None
