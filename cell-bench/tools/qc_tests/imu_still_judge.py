"""IMU-1 정지 시험 판정 — imuStill_<날짜>/summary.json 의 부팅마다 5분 기록 창에서 IMU 0.1초 평균(0x0F)으로
셀마다 중력 크기(g) · 자이로스코프 영점 오차(°/s, 세 축 중앙값의 크기)를 낸다. 같은 셀을 부팅 6번에 걸쳐 본다.
사용: python -X utf8 imu_still_judge.py [imuStill_20261110]"""
import sys, json
from pathlib import Path
from datetime import datetime
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")
import raw_layer as R
import a_judge as J
P = J.P
ARGS = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and sys.argv[i - 1] != "--extra"]
D = J.DATA / (ARGS[0] if ARGS else "imuStill_20261110")
EXTRA = [Path(sys.argv[sys.argv.index("--extra") + 1])] if "--extra" in sys.argv else []
j = json.load(open(D / "summary.json", encoding="utf-8"))
slot = {s: (c + 1, r + 1) for c, col in enumerate(j["columns"]) for r, s in enumerate(col)}
wins = []
for b in j["boots"]:
    t0 = datetime.strptime(b["from"], "%Y-%m-%d %H:%M:%S").timestamp(); t1 = datetime.strptime(b["to"], "%Y-%m-%d %H:%M:%S").timestamp()
    off = P.clock_offset(P.live(t0, t1)); wins.append((b["k"], t0, t1, off))
    print(f"부팅 {b['k']}: {b['from'][11:]}~{b['to'][11:]} · 셀 시계 차 {off:.1f}초", flush=True)
rows = []
for f in sorted(Path(j["ftg"]).glob("ftg_*.bin")) + [g for x in EXTRA for g in sorted(x.glob("ftg_*.bin"))]:
    s = int(f.name.split("_")[1])
    gps, e, o = R.read_file(f)
    if gps is None or o is None:
        print(f"{s}: GPS 또는 IMU 없음"); continue
    t, gi = gps[0], gps[8]
    it, iidx, acc, gyr = o
    out = []
    for k, t0, t1, off in wins:
        m = (t >= t0 - off) & (t <= t1 - off); ii = gi[m]
        if len(ii) < 100:
            out.append(f"부팅{k} 기록 없음"); continue
        gaps = np.where(np.diff(ii) > 50)[0]
        if len(gaps): ii = ii[gaps[-1] + 1:]
        sel = (iidx >= ii[0]) & (iidx <= ii[-1])
        if sel.sum() < 1000:
            out.append(f"부팅{k} IMU {int(sel.sum())}개"); continue
        a = acc[sel].astype(np.float64); g = gyr[sel].astype(np.float64)
        mag = np.sqrt((a ** 2).sum(1)); gb = np.median(g, 0)
        row = {"boot": k, "cell": s, "slot": slot.get(s), "n": int(sel.sum()), "g": float(np.median(mag)) / R.G_LSB,
               "acc_sd_mg": float(mag.std()) / R.G_LSB * 1000, "gb": float(np.sqrt((gb ** 2).sum())) / R.DPS_LSB,
               "gb_xyz": [round(float(v) / R.DPS_LSB, 2) for v in gb], "gyro_sd": float(g.std(0).mean()) / R.DPS_LSB}
        rows.append(row); out.append(f"{row['g']:.4f}g/{row['gb']:.2f}")
    print(f"{s} 칸 {slot.get(s)}: " + "  ".join(out), flush=True)
json.dump(rows, open(D / "imu_items.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n셀별 요약 (부팅 6번) — 중력 g 최소~최대 · 자이로 영점 °/s 최소~최대 · 2 °/s 넘은 부팅 수")
by = {}
for r in rows: by.setdefault(r["cell"], []).append(r)
for s, lst in sorted(by.items()):
    g = [r["g"] for r in lst]; b = [r["gb"] for r in lst]; bad = sum(1 for v in b if v > 2)
    print(f"  {s}: n={len(lst)} 중력 {min(g):.4f}~{max(g):.4f} · 영점 {min(b):.2f}~{max(b):.2f} · 2 넘음 {bad}" + ("  ← 불합격" if bad else ""))
