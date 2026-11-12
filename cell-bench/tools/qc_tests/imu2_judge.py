"""IMU-2 · IMU 레일 시험 판정 2판 — 셀마다 IMU 속도 변화 ÷ 모터 속도 변화(출발 구간), 감속 구간은 참고로.
기록 폴더 안의 운전 폴더(runs.json 이 있는 하위 폴더)를 모두 읽는다 — imu2_run.py 는 long/ 하나다.
1판(firmware1_judge.py 의 IMU 비율, 쓰지 않음)과 다른 점:
  · 축을 하나 고르지 않는다. 출발마다 세 축의 속도 변화 벡터를 내고, 그 벡터들이 가리키는 방향(특잇값 분해의 첫 방향)을 레일 방향으로 쓴다.
  · 시각을 상호상관으로 맞추지 않는다. 라이브로 잰 셀 시계 차에 작은 보정(−1 ~ +1초, 0.05초 간격)을 더해 가며 출발 속도 변화가 가장 커지는 값을 고른다.
  · 두 IMU 기록(멈춤 0.1초 평균 0x0F, 움직임 0.01초 값 0x0E)을 합치고, 표본마다 제 간격을 곱해 누적합으로 적분한다.
  · 영점은 출발 앞 멈춤(0x0F)의 평균 벡터.
단계별 거르기: 실행기가 뺀 셀(앞 시험 불합격, summary.json 의 excluded)은 건너뛰고, 긴 거리 운전(long/)이 있으면 셀마다 합격 · 불합격을
data/qc_gate_<세트>.json 에 적는다(기준 motionbench/criteria/qc_tests.json 의 IMU-2 — 속도 변화 비 0.85 ~ 1.15).
사용: python -X utf8 imu2_judge.py <기록 폴더 — 예 IMU-2_20261112> [--lag -0.3]"""
import sys, json, statistics as st
from pathlib import Path
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")
import raw_layer as R
import gnss1_judge as J
from motionbench import align as A, percell as PC
P = J.P
if len(sys.argv) < 2 or sys.argv[1].startswith("--"):
    raise SystemExit(__doc__.splitlines()[-1])
D = J.DATA / sys.argv[1]
PRE, TAIL = 0.5, 0.8          # 출발 앞 여유, 가속 끝 뒤 여유(초)
BIAS_GAP = 0.5                # 출발 이만큼 앞까지를 영점 구간으로
FIX_LAG = float(sys.argv[sys.argv.index("--lag") + 1]) if "--lag" in sys.argv else None   # 주면 모든 셀에 같은 시각 보정(초)
OUT_NAME = f"imu_rail_items{'' if FIX_LAG is None else '_lag' + str(FIX_LAG)}.json"
summ = json.load(open(D / "summary.json", encoding="utf-8"))
EX = J.excluded(summ)
layouts = json.load(open(J.ROOT / "motionbench" / "dock_layout.json", encoding="utf-8")) | json.load(open(Path(__file__).parent / "dock_layout_a.json", encoding="utf-8"))
slot = {s: (c + 1, r + 1) for c, col in enumerate(layouts[summ["layout"]]["columns"]) for r, s in enumerate(col)}

events = {}      # 프리셋 → [(출발 시각, 앞 멈춤 시작, 가속 시간, 방향 부호, 모터 속도 변화, 감속 시작, 멈춤 시작)]
t_lo, t_hi = 1e20, 0
for sub in sorted(x for x in D.iterdir() if x.is_dir() and (x / "runs.json").exists()):
    pre = json.load(open(sub / "preset.json", encoding="utf-8"))
    t_acc = pre["vmax_mm_s"] / pre["amax_mm_s2"]
    for r in json.load(open(sub / "runs.json", encoding="utf-8")):
        if r.get("rc") != 0 or not r.get("motor_csv"):
            continue
        t, p = A.load_motion(r["motor_csv"]); t_lo, t_hi = min(t_lo, t[0]), max(t_hi, t[-1])
        for a, b in PC.leg_list(PC.dwells(t, p)):
            sgn = 1.0 if b[2] > a[2] else -1.0
            s0, e0 = a[1], b[0]
            mv = A.motor_speed(t, p, [s0 + t_acc + 0.5])[0] - A.motor_speed(t, p, [s0 - 0.2])[0]
            events.setdefault(sub.name, []).append((s0, a[0], t_acc, sgn, mv, e0 - t_acc, e0))
print("출발 수", {k: len(v) for k, v in events.items()})
off = P.clock_offset(P.live(t_lo - 5, t_hi + 5)); print(f"셀 시계 차 {off:.2f}초")


def merged(f):
    gps, e, o = R.read_file(f)
    parts = [(x[0], x[2].astype(np.float64) * R.MMS2, np.full(len(x[0]), d)) for x, d in ((o, 0.1), (e, 0.01)) if x is not None]
    if not parts:
        return None
    t = np.concatenate([q[0] for q in parts]) + off; a = np.concatenate([q[1] for q in parts]); dt = np.concatenate([q[2] for q in parts])
    is_f = np.concatenate([np.full(len(q[0]), q[2][0] == 0.1) for q in parts])
    k = np.argsort(t, kind="stable"); t, a, dt, is_f = t[k], a[k], dt[k], is_f[k]
    cs = np.vstack([np.zeros((1, 3)), np.cumsum(a * dt[:, None], 0)]); cd = np.concatenate([[0], np.cumsum(dt)])
    return t, a, is_f, cs, cd


def window(m, x, y):
    t, _, _, cs, cd = m
    i, j = np.searchsorted(t, x), np.searchsorted(t, y)
    return cs[j] - cs[i], cd[j] - cd[i], j - i


def bias(m, x, y):
    t, a, is_f, _, _ = m
    k = (t >= x) & (t <= y)
    return a[k].mean(0) if k.sum() >= 3 else None


def dv_starts(m, evs, L):
    """보정 L 초에서 출발마다 세 축 속도 변화 벡터(영점 뺌). 쓸 수 없는 출발은 None."""
    out = []
    for s0, d0, t_acc, sgn, mv, _, _ in evs:
        b = bias(m, d0 + 0.3 - L, s0 - BIAS_GAP - L)
        if b is None:
            out.append(None); continue
        S, T, n = window(m, s0 - PRE - L, s0 + t_acc + TAIL - L)
        out.append(None if n < 5 else (S - b * T) * sgn)
    return out


def direction(vs):
    M = np.array([v for v in vs if v is not None])
    if len(M) < 4:
        return None
    u = np.linalg.svd(M, full_matrices=False)[2][0]
    return u if (M @ u).sum() > 0 else -u


rows = []
for f in sorted(Path(summ["ftg"]).glob("ftg_*.bin")):
    s = int(f.name.split("_")[1])
    if s in EX:
        continue
    m = merged(f)
    if m is None:
        print(s, "IMU 없음"); continue
    row = {"cell": s, "slot": slot.get(s)}
    for name, evs in events.items():
        u = direction(dv_starts(m, evs, 0.0))
        if u is None:
            row[name] = None; continue
        best = FIX_LAG if FIX_LAG is not None else max(np.arange(-1.0, 1.0001, 0.05), key=lambda L: sum(v @ u for v in dv_starts(m, evs, L) if v is not None))
        vs = dv_starts(m, evs, best); u = direction(vs)
        rat = [float(v @ u) / abs(e[4]) for v, e in zip(vs, evs) if v is not None and abs(e[4]) > 10]
        side = [float(np.linalg.norm(v - (v @ u) * u)) / abs(e[4]) for v, e in zip(vs, evs) if v is not None and abs(e[4]) > 10]
        q1, q3 = np.percentile(rat, [25, 75]) if rat else (None, None)
        row[name] = {"n": len(rat), "ratio": round(st.median(rat), 3) if rat else None, "q1": round(float(q1), 3) if rat else None, "q3": round(float(q3), 3) if rat else None,
                     "side": round(st.median(side), 3) if side else None, "lag": round(float(best), 2), "u": [round(float(x), 3) for x in u]}
    rows.append(row)
    print(f"{s} 칸 {slot.get(s)}: " + " | ".join(f"{n} 비 {v['ratio']} [{v['q1']}~{v['q3']}] n{v['n']} 옆 {v['side']} 보정 {v['lag']:+.2f}s 방향 {v['u']}" for n, v in row.items() if isinstance(v, dict)), flush=True)
json.dump(rows, open(D / OUT_NAME, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
from motionbench import qc_gate as G
CR = G.criteria("IMU-2"); LO, HI = float(CR["speed_ratio_lo"]), float(CR["speed_ratio_hi"])
print("\n요약 — 프리셋별 셀 중앙값의 분포")
for name in events:
    v = sorted((r[name]["ratio"], r["cell"]) for r in rows if r.get(name) and r[name]["ratio"] is not None)
    if v:
        print(f"  {name}: 중앙 {st.median(x for x, _ in v):.3f} · 범위 {v[0][0]:.3f}({v[0][1]}) ~ {v[-1][0]:.3f}({v[-1][1]}) · {LO:g} ~ {HI:g} 밖 {[c for x, c in v if not LO <= x <= HI]}")
if "long" in events:                       # 판정에 쓰는 운전은 긴 거리 하나(qc-tests.md IMU-2)
    GP = G.path(summ["layout"].split("-")[0], J.DATA); GATE = G.load(GP); n = {G.PASS: 0, G.FAIL: 0}
    for r in rows:
        x = (r.get("long") or {}).get("ratio")
        if x is None:
            continue
        st_ = G.PASS if LO <= x <= HI else G.FAIL; n[st_] += 1
        G.record(GATE, r["cell"], "IMU-2", st_, D.name, f"긴 거리 속도 변화 비 {x:.3f} (기준 {LO:g} ~ {HI:g})")
    G.save(GP, GATE)
    print(f"단계별 거르기 — IMU-2 합격 {n[G.PASS]} · 불합격 {n[G.FAIL]} → {GP.name}")
else:
    print("긴 거리 운전(long/)이 없어 단계별 거르기 파일에는 적지 않는다(2026-11-12 전 기록)")
