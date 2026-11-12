"""IMU-1 항목을 GNSS-1 기록에서 읽는 판정 — GNSS-1 회차(r<회차>/summary.json)의 판정 구간에서 IMU 원래 값(0.1초 평균, 0x0F)을 읽어
셀마다 중력 크기(g) · 자이로스코프 영점 오차(°/s, 세 축 중앙값의 크기)를 내고, 셀 · 슬롯 · 회차 효과로 분해한다.
IMU-1 시험 자체(부팅 6번)의 판정은 imu1_judge.py 다.
사용: python -X utf8 imu1_gnss1_judge.py <GNSS-1 기록 폴더 — 예 GNSS-1_20261112>"""
import sys, json
from pathlib import Path
from datetime import datetime
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")
import raw_layer as R
import gnss1_judge as J
P = J.P
if len(sys.argv) < 2:
    raise SystemExit(__doc__.splitlines()[-1])
COLL = J.DATA / sys.argv[1]

rows = []
for sm in sorted(COLL.glob("r*/summary.json")):
    j = json.load(open(sm, encoding="utf-8"))
    w = J.window(j)
    if not w or not j.get("ftg"):
        continue
    t0 = datetime.strptime(w["from"], "%Y-%m-%d %H:%M:%S").timestamp(); t1 = datetime.strptime(w["to"], "%Y-%m-%d %H:%M:%S").timestamp()
    t1 = min(t1, J.scene_end(sm.parent / "round.log") or t1)
    off = P.clock_offset(P.live(t0, t1))
    slot = {s: (c + 1, r + 1) for c, col in enumerate(j["columns"]) for r, s in enumerate(col)}
    for f in sorted(Path(j["ftg"]).glob("ftg_*.bin")):
        s = int(f.name.split("_")[1])
        gps, e, o = R.read_file(f)
        if gps is None or o is None:
            print(f"r{j['round']:02d} {s}: GPS 또는 IMU 없음"); continue
        t, gi = gps[0], gps[8]
        m = (t >= t0 - off) & (t <= t1 - off); ii = gi[m]
        if len(ii) < 100:
            print(f"r{j['round']:02d} {s}: 구간 기록 없음"); continue
        gaps = np.where(np.diff(ii) > 50)[0]
        if len(gaps): ii = ii[gaps[-1] + 1:]
        it, iidx, acc, gyr = o
        k = (iidx >= ii[0]) & (iidx <= ii[-1])
        if k.sum() < 1000:
            print(f"r{j['round']:02d} {s}: IMU {k.sum()}개뿐"); continue
        a = acc[k].astype(np.float64); g = gyr[k].astype(np.float64)
        mag = np.sqrt((a ** 2).sum(1)); gb = np.median(g, 0)
        row = {"round": j["round"], "cell": s, "slot": slot.get(s), "n": int(k.sum()),
               "g": float(np.median(mag)) / R.G_LSB, "acc_sd_mg": float(mag.std()) / R.G_LSB * 1000,
               "gb": float(np.sqrt((gb ** 2).sum())) / R.DPS_LSB, "gb_xyz": [round(float(v) / R.DPS_LSB, 2) for v in gb],
               "gyro_sd": float(g.std(0).mean()) / R.DPS_LSB}
        rows.append(row)
        print(f"r{j['round']:02d} {s} 슬롯 {row['slot']}: 중력 {row['g']:.4f} g · 가속 흔들림 {row['acc_sd_mg']:.1f} mg · 자이로 영점 {row['gb']:.2f} °/s {row['gb_xyz']} · 자이로 흔들림 {row['gyro_sd']:.3f}", flush=True)
json.dump(rows, open(COLL / "imu_items.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

def decompose(key, label, unit, scale=1.0):
    obs = [(r["cell"], tuple(r["slot"]), r["round"], r[key] * scale) for r in rows if r["slot"]]
    cells = sorted({o[0] for o in obs}); slots = sorted({o[1] for o in obs}); rnds = sorted({o[2] for o in obs})
    nc, ns, nr = len(cells), len(slots), len(rnds)
    X = np.zeros((len(obs) + 2, 1 + nc + ns + nr)); y = np.zeros(len(obs) + 2)
    for i, (c, s, r, v) in enumerate(obs):
        X[i, 0] = 1; X[i, 1 + cells.index(c)] = 1; X[i, 1 + nc + slots.index(s)] = 1; X[i, 1 + nc + ns + rnds.index(r)] = 1; y[i] = v
    X[-2, 1:1 + nc] = 100; X[-1, 1 + nc:1 + nc + ns] = 100          # 셀 효과 합 0 · 슬롯 효과 합 0
    X = np.vstack([X, np.r_[0, np.zeros(nc + ns), 100 * np.ones(nr)]]); y = np.r_[y, 0]   # 회차 효과 합 0
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    res = y[:len(obs)] - X[:len(obs)] @ b
    dof = max(1, len(obs) - (1 + (nc - 1) + (ns - 1) + (nr - 1)))
    sd = float(np.sqrt((res ** 2).sum() / dof))
    ce = dict(zip(cells, b[1:1 + nc])); se = dict(zip(slots, b[1 + nc:1 + nc + ns])); re_ = dict(zip(rnds, b[1 + nc + ns:]))
    print(f"\n[{label}] 전체 평균 {b[0]:.4f} {unit} · 남은 흔들림(같은 셀 반복) {sd:.4f} {unit} · 관측 {len(obs)}")
    print("  셀 효과:", "  ".join(f"{c} {v:+.4f}" for c, v in sorted(ce.items(), key=lambda x: x[1])))
    print("  슬롯 효과 범위:", f"{min(se.values()):+.4f} ~ {max(se.values()):+.4f}", "· 회차 효과:", {k: round(v, 4) for k, v in re_.items()})
    return ce, se, sd

decompose("g", "중력 크기", "g")
decompose("gb", "자이로 영점 오차", "°/s")
