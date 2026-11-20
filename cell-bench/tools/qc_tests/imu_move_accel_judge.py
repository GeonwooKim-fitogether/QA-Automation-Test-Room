"""IMU-MOVE · 운전-가속 기록 판정 — 가속도 단계마다(0.5 · 1 · 2 · 4 · 8 m/s²) 출발 구간에서 IMU 가 잰 속도 변화 ÷ 모터 속도 변화.
2026-11-20 사용자 합의 판정 항목(A안 — dynamic-bench/design/qc-tests.md 3절 IMU-이동): 셀마다 단계당 출발들의 중앙값, 감속 구간 제외.

운전-가속은 등속 구간 없이 가속 직후 감속하는 왕복이라(삼각형 속도) 옛 판정(imu_move_judge.py — 출발 0.5초 앞 ~ 가속 끝 0.8초 뒤,
영점은 출발 앞 멈춤)을 그대로 쓰면 감속이 섞인다. 그래서 이렇게 잰다.
  · 출발: 운전마다 오래 멈춘 뒤 출발 1번('멈춤 출발') + 끝에서 방향을 바꾸며 출발 9번('방향 바꿈 출발' — 끝 멈춤 0초여도 모터는 끝에서 약 0.15초
    멈췄다가 반대로 출발한다, 2026-11-20 실측). 다리(한 방향 이동)마다 가속 구간 속도를 직선으로 맞춰 0 으로 거슬러 올라간 때를 출발,
    감속 구간을 같은 식으로 맞춘 때를 멈춤으로 잡고, 출발 구간 = 출발 ~ 다리 가운데(최고 속도). 모터 속도 변화 = 2 × 그 구간 이동 거리 ÷ 걸린 시간
    (일정 가속 · 출발 속도 0).
  · IMU 속도 변화: 0x0F(0.1초 평균) · 0x0E(0.01초 값)를 합쳐 표본마다 제 간격을 곱한 누적 적분을, 구간 끝에서 선형 보간으로 읽는다
    (8 m/s² 출발 구간은 0.24초뿐이라 표본 하나(0.01초)를 빼먹으면 4 % 가 틀린다).
  · 영점: 운전 하나(멈춤에서 출발해 멈춤으로 끝남)의 실제 가속도 평균은 0 이므로, 운전 전체 IMU 평균 벡터(중력 + 영점)를 뺀다.
  · 레일 방향: 출발마다 낸 세 축 속도 변화 벡터들의 주된 방향(특잇값 분해). 시각 보정: 라이브로 잰 셀 시계 차에 −0.6 ~ +0.6초(0.005초 간격)를
    더해 가며 출발 속도 변화의 합이 가장 커지는 값(멈춤 출발이 정확한 시각에서만 최대가 된다).
판정: 합격 기준은 아직 합의 전이라 단계별 거르기 파일(qc_gate)에는 적지 않는다. 옛 기준 0.85 ~ 1.15 밖이면 '살펴봄'으로만 표시한다.
사용: python -X utf8 imu_move_accel_judge.py <기록 폴더> [<기록 폴더> ...] [--ftg <셀 기록 폴더>] [--min-runs 10]
      (같은 묶음이 끊겨 여러 폴더에 나뉘었으면 함께 준다 — 단계 폴더 이름의 가속도로 모은다. 정상 운전이 --min-runs 보다 적은 단계는 판정에 쓰지 않는다)
결과: 첫 기록 폴더의 imu_accel_items.json"""
from __future__ import annotations

import json
import re
import statistics as st
import sys
from pathlib import Path

import numpy as np

V_MIN = 20.0          # 움직임으로 보는 모터 속도(mm/s)
FIT_LO, FIT_HI = 0.2, 0.8   # 멈춤 출발 · 마지막 멈춤 시각을 맞출 때 쓰는 속도 범위(그 구간 최고 속도에 대한 비)
XLAGS = np.arange(-0.2, 0.2001, 0.005)   # 시각 맞추기 범위(초) — 라이브로 잰 셀 시계 차가 이미 0.02초 안으로 맞다(2026-11-20 실측)
REF_LO, REF_HI = 0.85, 1.15   # 옛 운전 기준 0.1 — 운전-가속에는 아직 합의 전, 살펴봄 표시에만


def velocity(t, p, dt: float = 0.002, w: float = 0.02):
    """모터 위치(초, mm) → 고른 간격 (시각, 위치, 속도 mm/s). 속도는 w 초 이동 평균."""
    t, p = np.asarray(t, float), np.asarray(p, float)
    tu = np.arange(t[0], t[-1], dt)
    pu = np.interp(tu, t, p)
    v = np.gradient(pu, tu)
    k = max(1, int(round(w / dt))) | 1                # 홀수 — 짝수 창은 반 칸(1 ms) 밀린다
    return tu, pu, np.convolve(v, np.ones(k) / k, mode="same")


def _zero_by_fit(tu, v, i0: int, i1: int, rising: bool) -> float | None:
    """i0 ~ i1 구간에서 속도가 직선이라 보고 속도 0 이 되는 시각. rising 이면 가속(멈춤 출발), 아니면 감속(마지막 멈춤)."""
    seg = slice(i0, i1 + 1)
    vv, tt = np.abs(v[seg]), tu[seg]
    peak = vv.max() if len(vv) else 0
    if peak < V_MIN:
        return None
    k = (vv >= FIT_LO * peak) & (vv <= FIT_HI * peak)
    if rising:                                   # 최고 속도 앞쪽만
        k &= np.arange(len(vv)) <= int(np.argmax(vv))
    else:
        k &= np.arange(len(vv)) >= int(np.argmax(vv))
    if k.sum() < 3:
        return None
    a, b = np.polyfit(tt[k], vv[k], 1)
    return float(-b / a) if a != 0 else None


def starts(t, p) -> dict | None:
    """운전 하나의 모터 기록 → {'events': [(출발 시각, 최고 속도 시각, 모터 속도 변화 mm/s (부호), '멈춤'|'방향')], 'span': (처음 출발, 마지막 멈춤),
    'g' · 'am': 시각 맞추기용 모터 가속도(0.01초 간격)}.
    다리(한 방향 이동)마다 따로 출발 · 멈춤 시각을 맞춘다 — 끝 멈춤 0초여도 모터는 끝에서 약 0.15초 멈췄다가 반대로 출발한다(2026-11-20 실측).
    방향을 바꾸는 순간을 쉼 없이 이어진 것으로 보면 출발 시각이 그 멈춤 가운데로 잡혀 모터 속도 변화가 작게 계산되고(8 m/s² 에서
    1,870 → 721 mm/s), 멈춤이 다리에서 차지하는 몫이 큰 높은 가속도일수록 비가 커졌다(1.03 → 2.40). 출발 구간 = 다리 출발 ~ 다리 가운데."""
    tu, pu, v = velocity(t, p)
    mov = (np.abs(v) > V_MIN).astype(int)
    edge = np.flatnonzero(np.diff(np.concatenate([[0], mov, [0]])))
    legs = [(int(i0), int(i1) - 1) for i0, i1 in zip(edge[::2], edge[1::2]) if tu[min(int(i1) - 1, len(tu) - 1)] - tu[int(i0)] >= 0.05]
    ev, span = [], []
    for k, (i0, i1) in enumerate(legs):
        t_s = _zero_by_fit(tu, v, max(0, i0 - 50), i1, rising=True)
        t_e = _zero_by_fit(tu, v, i0, min(len(v) - 1, i1 + 50), rising=False)
        if t_s is None or t_e is None or t_e <= t_s:
            continue
        mid = (t_s + t_e) / 2
        d = float(np.interp(mid, tu, pu) - np.interp(t_s, tu, pu))
        ev.append((t_s, mid, 2 * d / (mid - t_s), "멈춤" if not ev else "방향"))
        span.append((t_s, t_e))
    if not ev:
        return None
    t_first, t_last = span[0][0], span[-1][1]
    g = np.arange(t_first - 0.5, t_last + 0.5, 0.01)
    am = np.convolve(np.gradient(v, tu), np.ones(10) / 10, mode="same")
    return {"events": ev, "span": (t_first, t_last), "g": g, "am": np.interp(g, tu, am)}


class Imu:
    """셀 IMU 기록(0x0F · 0x0E 를 합친 표본) — PC 시각으로 옮긴 표본 시각 t, 가속도 a(mm/s², 세 축), 표본 간격 dt.
    표본 i 가 [t_i − dt_i/2, t_i + dt_i/2] 를 덮는다고 보고, 표본 끝마다 누적 적분을 두어 아무 시각에서나 선형 보간으로 읽는다."""

    def __init__(self, t, a, dt):
        k = np.argsort(t, kind="stable")
        self.t, self.a, self.dt = np.asarray(t, float)[k], np.asarray(a, float)[k], np.asarray(dt, float)[k]
        self.edge = np.concatenate([[self.t[0] - self.dt[0] / 2], self.t + self.dt / 2])
        self.cum = np.vstack([np.zeros((1, 3)), np.cumsum(self.a * self.dt[:, None], 0)])

    def integral(self, x, y) -> np.ndarray:
        """x ~ y 초 사이 가속도 적분(mm/s, 세 축). x · y 가 배열이면 (구간 수, 3)."""
        x, y = np.asarray(x, float), np.asarray(y, float)
        return np.stack([np.interp(y, self.edge, self.cum[:, j]) - np.interp(x, self.edge, self.cum[:, j]) for j in range(3)], -1)

    def covers(self, x, y):
        """구간마다 표본이 있나(0.01초 기록이면 넉넉히 — 0.02초에 하나 이상, 최소 3개). x · y 가 배열이면 배열."""
        x, y = np.asarray(x, float), np.asarray(y, float)
        return np.searchsorted(self.t, y) - np.searchsorted(self.t, x) >= np.maximum(3, ((y - x) / 0.02).astype(int))


def flatten(runs: list[dict]) -> dict:
    """운전 목록 → 출발마다 한 줄인 배열들(출발 · 끝 시각, 모터 속도 변화, 종류, 그 운전의 처음 출발 · 마지막 멈춤)."""
    ev = [(*e, *r["span"]) for r in runs for e in r["events"]]
    return {"t0": np.array([e[0] for e in ev]), "t1": np.array([e[1] for e in ev]), "dv": np.array([e[2] for e in ev]),
            "kind": [e[3] for e in ev], "a0": np.array([e[4] for e in ev]), "a1": np.array([e[5] for e in ev])}


def dv_vectors(imu: Imu, runs: list[dict] | dict, lag: float):
    """lag 초 보정에서 출발마다 (영점 뺀 세 축 속도 변화 · 모터 방향 부호로 맞춤, 모터 속도 변화 크기, 종류). 쓸 수 없는 출발은 None.
    영점은 그 운전 전체(처음 출발 ~ 마지막 멈춤)의 IMU 평균 — 실제 가속도 평균은 0 이므로 남는 것이 중력 + 영점이다."""
    f = runs if isinstance(runs, dict) else flatten(runs)
    ok = imu.covers(f["a0"] - lag, f["a1"] - lag) & imu.covers(f["t0"] - lag, f["t1"] - lag) & (np.abs(f["dv"]) >= 10)
    bias = imu.integral(f["a0"] - lag, f["a1"] - lag) / (f["a1"] - f["a0"])[:, None]
    vec = (imu.integral(f["t0"] - lag, f["t1"] - lag) - bias * (f["t1"] - f["t0"])[:, None]) * np.sign(f["dv"])[:, None]
    return [(vec[i], abs(float(f["dv"][i])), f["kind"][i]) if ok[i] else None for i in range(len(ok))]


def direction(vs) -> np.ndarray | None:
    M = np.array([v[0] for v in vs if v is not None])
    if len(M) < 4:
        return None
    u = np.linalg.svd(M, full_matrices=False)[2][0]
    return u if (M @ u).sum() > 0 else -u


def xcorr_lag(imu: Imu, runs: list[dict]) -> tuple[float, float] | None:
    """IMU 레일 방향 가속도와 모터 가속도의 상관이 가장 큰 시각 보정(초)과 그 상관. 레일 방향은 운전 동안 IMU 가속도의 주된 방향.
    출발 속도 변화의 합을 키우는 방식은 방향 바꿈 출발에서 기준점이 없어(가속이 끊이지 않는다) ±0.6초 끝으로 갔다(2026-11-20) —
    상관은 운전 전체 모양으로 맞추므로 하나로 정해진다. 왕복 주기만큼 어긋난 곳도 상관이 커서 범위를 ±0.2초로 둔다."""
    xs = [(r["g"], r["am"], (imu.t >= r["g"][0]) & (imu.t <= r["g"][-1])) for r in runs]
    xs = [x for x in xs if x[2].sum() >= 50]
    if not xs:
        return None
    S = np.vstack([imu.a[k] - imu.a[k].mean(0) for _, _, k in xs])
    u = np.linalg.svd(S, full_matrices=False)[2][0]
    proj = imu.a @ u
    am = np.concatenate([x[1] for x in xs])
    best = None
    for L in XLAGS:
        ia = np.concatenate([np.interp(g + L, imu.t, proj) for g, _, _ in xs])
        c = abs(np.corrcoef(ia, am)[0, 1])
        if best is None or c > best[1]:
            best = (float(L), float(c))
    return best


def judge_stage(imu: Imu, runs: list[dict]) -> dict | None:
    """한 단계(운전 여러 번)의 출발들 → 셀 값: 속도 변화 비 중앙값 · 사분위 · 멈춤 출발 / 방향 바꿈 출발 따로 · 옆 방향 비 · 시각 보정 · 레일 방향."""
    f = flatten(runs)
    x = xcorr_lag(imu, runs)
    if x is None:
        return None
    best, corr = x
    vs = dv_vectors(imu, f, -best)                                     # IMU 시각 = PC 시각 + best → 창을 best 만큼 뒤로
    u = direction(vs)
    if u is None:
        return None
    rat = [(float(v[0] @ u) / v[1], v[2]) for v in vs if v is not None]
    side = [float(np.linalg.norm(v[0] - (v[0] @ u) * u)) / v[1] for v in vs if v is not None]
    if not rat:
        return None
    r_all = [x for x, _ in rat]
    q1, q3 = np.percentile(r_all, [25, 75])
    med = lambda xs: round(st.median(xs), 3) if xs else None
    return {"n": len(r_all), "ratio": med(r_all), "q1": round(float(q1), 3), "q3": round(float(q3), 3),
            "ratio_still": med([x for x, k in rat if k == "멈춤"]), "n_still": sum(1 for _, k in rat if k == "멈춤"),
            "ratio_turn": med([x for x, k in rat if k == "방향"]), "n_turn": sum(1 for _, k in rat if k == "방향"),
            "side": med(side), "lag": round(float(-best), 3), "corr": round(corr, 3), "u": [round(float(x), 3) for x in u]}


def stage_key(name: str) -> int | None:
    m = re.search(r"_a(\d+)$", name)
    return int(m.group(1)) if m else None


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    sys.stdout.reconfigure(encoding="utf-8")
    import raw_layer as R
    import gnss_static_judge as J
    from motionbench import align as A
    P = J.P
    opt = lambda k, d=None: argv[argv.index(k) + 1] if k in argv else d
    folders = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] not in ("--ftg", "--min-runs"))]
    if not folders:
        raise SystemExit(__doc__.splitlines()[-3])
    dirs = [J.DATA / f for f in folders]
    sums = [json.load(open(d / "summary.json", encoding="utf-8")) for d in dirs]
    ex = {}
    for s in sums:
        ex |= J.excluded(s)
    ftg = Path(opt("--ftg") or next((s["ftg"] for s in sums if s.get("ftg")), "") or "")
    if not ftg.is_dir():
        raise SystemExit("셀 기록 폴더를 모른다 — --ftg 로 준다")
    min_runs = int(opt("--min-runs", 10))
    stages, used, t_lo, t_hi = {}, [], 1e20, 0.0
    for d in dirs:
        for sub in sorted(x for x in d.iterdir() if x.is_dir() and (x / "runs.json").exists()):
            acc = stage_key(sub.name)
            runs = [r for r in json.load(open(sub / "runs.json", encoding="utf-8")) if r.get("rc") == 0 and r.get("motor_csv")]
            if acc is None or len(runs) < min_runs:
                print(f"{d.name}/{sub.name}: 정상 운전 {len(runs)}번 < {min_runs} — 판정에 쓰지 않는다")
                continue
            for r in runs:
                t, p = A.load_motion(r["motor_csv"])
                s = starts(t, p)
                if s is None:
                    print(f"{sub.name} {r['i']}번: 출발을 찾지 못함"); continue
                stages.setdefault(acc, []).append(s)
                t_lo, t_hi = min(t_lo, s["span"][0]), max(t_hi, s["span"][1])
            used.append(f"{d.name}/{sub.name}")
    print("쓴 단계 폴더:", " · ".join(used))
    print("단계별 운전 · 출발 수:", {a: (len(rs), sum(len(r["events"]) for r in rs)) for a, rs in sorted(stages.items())})
    # 셀 시계 차는 단계마다 그 단계 시간대의 라이브로 잰다 — 15시 · 16시 기록을 묶어 한 번에 재면 −0.40초, 단계 하나로 재면 −0.05초였다
    # (2026-11-20, 셀 시계가 시간대에 따라 다르다). 잘못 잰 시계 차는 시각 보정 범위(±0.2초) 밖이라 판정이 엉뚱해진다
    offs = {acc: P.clock_offset(P.live(rs[0]["span"][0] - 5, rs[-1]["span"][1] + 5)) for acc, rs in stages.items()}
    print("단계별 셀 시계 차(초):", {a: round(v, 3) for a, v in sorted(offs.items())})
    rows = []
    for f in sorted(ftg.glob("ftg_*.bin")):
        cell = int(f.name.split("_")[1])
        if cell in ex:
            continue
        _, e, o = R.read_file(f)
        parts = [(x[0], x[2].astype(np.float64) * R.MMS2, np.full(len(x[0]), d)) for x, d in ((o, 0.1), (e, 0.01)) if x is not None]
        if not parts:
            print(cell, "IMU 없음"); continue
        t_raw, a_raw, dt_raw = (np.concatenate([q[i] for q in parts]) for i in range(3))
        row = {"cell": cell}
        for acc, runs in sorted(stages.items()):
            imu = Imu(t_raw + offs[acc], a_raw, dt_raw)
            if not imu.covers(runs[0]["span"][0] - 1, runs[-1]["span"][1] + 1):
                continue                                            # 이 셀은 이 단계 때 독에 없었다(다른 묶음)
            row[f"a{acc}"] = judge_stage(imu, runs)
        if len(row) > 1:
            rows.append(row)
            print(f"{cell}: " + " | ".join(f"{k} 비 {v['ratio']} [{v['q1']}~{v['q3']}] 멈춤 {v['ratio_still']} 방향 {v['ratio_turn']} n{v['n']} 보정 {v['lag']:+.3f}s"
                                         for k, v in row.items() if isinstance(v, dict)), flush=True)
    out = dirs[0] / "imu_accel_items.json"
    json.dump({"folders": folders, "ftg": str(ftg), "clock_offset_s": {f"a{a}": v for a, v in offs.items()}, "stages_used": used, "rows": rows},
              open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\n요약 — 단계별 셀 중앙값의 분포 (합격 기준 합의 전 · 옛 기준 {REF_LO:g} ~ {REF_HI:g} 밖은 살펴봄)")
    for acc in sorted(stages):
        v = sorted((r[f"a{acc}"]["ratio"], r["cell"]) for r in rows if r.get(f"a{acc}"))
        if v:
            print(f"  {acc / 1000:g} m/s²: {len(v)}대 · 중앙 {st.median(x for x, _ in v):.3f} · 범위 {v[0][0]:.3f}({v[0][1]}) ~ {v[-1][0]:.3f}({v[-1][1]})"
                  f" · 살펴봄 {[c for x, c in v if not REF_LO <= x <= REF_HI]}")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
