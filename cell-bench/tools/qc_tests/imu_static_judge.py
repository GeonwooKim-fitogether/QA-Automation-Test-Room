"""IMU-STATIC · IMU 정지 시험 판정 — 기록 폴더 summary.json 의 부팅마다 5분 기록 창에서 IMU 0.1초 값(0x0F 평균 기록 + 0x0E 를 0.1초로 평균 낸 값)으로
셀마다 중력 크기(g) · 자이로스코프 영점 오차(°/s, 세 축 중앙값의 크기) · 정지 잡음(mg, 가속도 크기의 표준편차 — 살펴봄, 기준 없음)을 낸다.
같은 셀을 부팅 6번에 걸쳐 본다.
기록은 imu_static_run.py 가 쓴다(2026-11-12 전 기록 폴더 이름은 imuStill_<날짜>).
단계별 거르기: 실행기가 뺀 셀(앞 시험 불합격, summary.json 의 excluded)은 건너뛰고, 셀마다 합격 · 불합격을 data/qc_gate_<세트>.json 에 적는다
(기준 motionbench/criteria/qc_tests.json 의 IMU-STATIC 버전 0.2 — 자이로스코프 영점 오차가 한 부팅이라도 2 °/s 넘거나 중력 크기가 1 g 에서 50 mg 넘게 벗어나면 불합격).
사용: python -X utf8 imu_static_judge.py <기록 폴더 — 예 IMU-STATIC_20261112> [--extra <셀 기록 폴더>]"""
import sys, json
from pathlib import Path
from datetime import datetime
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")
import raw_layer as R
import gnss_static_judge as J
P = J.P
ARGS = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and sys.argv[i - 1] != "--extra"]
if not ARGS:
    raise SystemExit(__doc__.splitlines()[-1])
D = J.DATA / ARGS[0]
EXTRA = [Path(sys.argv[sys.argv.index("--extra") + 1])] if "--extra" in sys.argv else []
j = json.load(open(D / "summary.json", encoding="utf-8"))
EX = J.excluded(j)
slot = {s: (c + 1, r + 1) for c, col in enumerate(j["columns"]) for r, s in enumerate(col)}
wins = []
for b in j["boots"]:
    t0 = datetime.strptime(b["from"], "%Y-%m-%d %H:%M:%S").timestamp(); t1 = datetime.strptime(b["to"], "%Y-%m-%d %H:%M:%S").timestamp()
    off = P.clock_offset(P.live(t0, t1)); wins.append((b["k"], t0, t1, off))
    print(f"부팅 {b['k']}: {b['from'][11:]}~{b['to'][11:]} · 셀 시계 차 {off:.1f}초", flush=True)
def tenth_avg(o, e, i0, i1):
    """판정 구간(파일 차례 i0 ~ i1)의 IMU 를 0.1초 값으로 모은다 — 0.1초 평균 기록(0x0F)은 그대로, 0.01초 기록(0x0E)은 한 메시지(10개 = 0.1초)를
    평균 내어 같은 0.1초 값으로 더한다. 셀 펌웨어는 이웃 표본 차이가 문턱을 넘으면 정지 중에도 0x0E 로 쓴다(2026-11-19 독 1행 1 · 4 · 5칸 —
    10686 은 부팅 6번 중 5번이 거의 0x0E 라 0x0F 만 쓰면 판정이 1번뿐이었다). (가속도, 각속도, 0x0E 에서 온 값 수)를 돌려준다."""
    acc, gyr, n_fast = [], [], 0
    if o is not None:
        sel = (o[1] >= i0) & (o[1] <= i1)
        acc.append(o[2][sel].astype(np.float64)); gyr.append(o[3][sel].astype(np.float64))
    if e is not None:
        sel = (e[1] >= i0) & (e[1] <= i1)
        if sel.any():
            _, inv = np.unique(e[1][sel], return_inverse=True)
            cnt = np.bincount(inv).astype(np.float64)[:, None]
            ea = np.zeros((cnt.shape[0], 3)); eg = np.zeros((cnt.shape[0], 3))
            np.add.at(ea, inv, e[2][sel].astype(np.float64)); np.add.at(eg, inv, e[3][sel].astype(np.float64))
            acc.append(ea / cnt); gyr.append(eg / cnt); n_fast = int(cnt.shape[0])
    if not acc:
        return np.zeros((0, 3)), np.zeros((0, 3)), 0
    return np.concatenate(acc), np.concatenate(gyr), n_fast


rows = []
for f in sorted(Path(j["ftg"]).glob("ftg_*.bin")) + [g for x in EXTRA for g in sorted(x.glob("ftg_*.bin"))]:
    s = int(f.name.split("_")[1])
    if s in EX:
        continue
    gps, e, o = R.read_file(f)
    if gps is None or (o is None and e is None):
        print(f"{s}: GPS 또는 IMU 없음"); continue
    t, gi = gps[0], gps[8]
    out = []
    for k, t0, t1, off in wins:
        m = (t >= t0 - off) & (t <= t1 - off); ii = gi[m]
        if len(ii) < 100:
            out.append(f"부팅{k} 기록 없음"); continue
        gaps = np.where(np.diff(ii) > 50)[0]
        if len(gaps): ii = ii[gaps[-1] + 1:]
        a, g, n_fast = tenth_avg(o, e, ii[0], ii[-1])
        if len(a) < 1000:
            out.append(f"부팅{k} IMU {len(a)}개"); continue
        mag = np.sqrt((a ** 2).sum(1)); gb = np.median(g, 0)
        row = {"boot": k, "cell": s, "slot": slot.get(s), "n": len(a), "n_fast": n_fast, "g": float(np.median(mag)) / R.G_LSB,
               "acc_sd_mg": float(mag.std()) / R.G_LSB * 1000, "gb": float(np.sqrt((gb ** 2).sum())) / R.DPS_LSB,
               "gb_xyz": [round(float(v) / R.DPS_LSB, 2) for v in gb], "gyro_sd": float(g.std(0).mean()) / R.DPS_LSB}
        rows.append(row); out.append(f"{row['g']:.4f}g/{row['gb']:.2f}/{row['acc_sd_mg']:.1f}mg")
    print(f"{s} 칸 {slot.get(s)}: " + "  ".join(out), flush=True)
json.dump(rows, open(D / "imu_items.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
from motionbench import qc_gate as G
CR = G.criteria("IMU-STATIC")
LIM = float(CR["gyro_bias_max_dps"])
GLIM = CR.get("gravity_dev_max_mg")       # 버전 0.2 — 중력 크기가 1 g 에서 벗어난 양(mg), 근거는 기준 파일 basis
print(f"\n셀별 요약 (부팅 6번) — 중력 g 최소~최대 · 자이로 영점 °/s 최소~최대 · 정지 잡음 mg 최소~최대(살펴봄) · {LIM:g} °/s 넘은 부팅 수")
by = {}
for r in rows: by.setdefault(r["cell"], []).append(r)
GP = G.path(j["layout"].split("-")[0], J.DATA); GATE = G.load(GP)
for s, lst in sorted(by.items()):
    g = [r["g"] for r in lst]; b = [r["gb"] for r in lst]; n = [r["acc_sd_mg"] for r in lst]; bad = sum(1 for v in b if v > LIM)
    gbad = sum(1 for v in g if GLIM is not None and abs(v - 1.0) * 1000 > float(GLIM))
    print(f"  {s}: n={len(lst)} 중력 {min(g):.4f}~{max(g):.4f} · 영점 {min(b):.2f}~{max(b):.2f} · 잡음 {min(n):.1f}~{max(n):.1f} mg · {LIM:g} 넘음 {bad} · 중력 ±{GLIM} mg 넘음 {gbad}" + ("  ← 불합격" if bad or gbad else ""))
    G.record(GATE, s, "IMU-STATIC", G.FAIL if bad or gbad else G.PASS, D.name,
             f"자이로스코프 영점 최대 {max(b):.2f} °/s — {LIM:g} °/s 넘은 부팅 {bad}/{len(lst)} · 중력 {min(g):.4f} ~ {max(g):.4f} g — ±{GLIM} mg 넘은 부팅 {gbad}/{len(lst)}")
G.save(GP, GATE)
ok = lambda r: r["gb"] <= LIM and (GLIM is None or abs(r["g"] - 1.0) * 1000 <= float(GLIM))
print(f"단계별 거르기 — IMU-STATIC 합격 {sum(1 for l in by.values() if all(ok(r) for r in l))} · 불합격 {sum(1 for l in by.values() if not all(ok(r) for r in l))} → {GP.name}")
