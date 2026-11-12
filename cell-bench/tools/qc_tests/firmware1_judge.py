"""FIRMWARE-1 · 움직임 판단 시험 판정 — 기록 폴더 안 운전 폴더(firmware1_run.py 는 a50/ · a500/)의 runs.json 모터 기록과 셀 기록(FTG)을 맞대,
셀마다 ① IMU 속도 변화 ÷ 모터 속도 변화를 출발 구간과 감속 구간 따로(비율 — 쓰지 않는다, IMU-2 는 imu2_judge.py)
② 움직임 판단이 켜지기까지(출발 뒤) · 꺼지기까지(감속 시작 뒤) 초. 결과는 기록 폴더의 judge_items.json(운전 폴더 이름별).
IMU 는 두 기록을 합친다 — 셀은 멈춰 있을 때 0.1초 평균(0x0F), 움직일 때 0.01초 값(0x0E)만 쓴다(11-10 확인: 두 기록 시간 합 = 전체 시간). 적분은 표본마다 제 간격(0.1 · 0.01초)을 곱한다. 레일 방향 축은 모터 가속도와 상관이 가장 큰 축. 셀 시계는 라이브로 잰 큰 차(off) + 모터 가속도 상호상관의 작은 차(lag).
영점: 출발 구간은 바로 앞 멈춤의 중앙값, 감속 구간은 바로 앞 등속 구간의 중앙값. 구간 끝은 멈춤 뒤 0.5초.
사용: python -X utf8 firmware1_judge.py <기록 폴더 — 예 FIRMWARE-1_20261112> [--extra <셀 기록 폴더>]"""
import sys, json, statistics as st
from pathlib import Path
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")
import raw_layer as R
import gnss1_judge as J
from motionbench import align as A, percell as PC
P = J.P
ARGS = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and sys.argv[i - 1] != "--extra"]
if not ARGS:
    raise SystemExit(__doc__.splitlines()[-1])
D = J.DATA / ARGS[0]
EXTRA = [Path(sys.argv[sys.argv.index("--extra") + 1])] if "--extra" in sys.argv else []
TAIL = 0.5            # 멈춤 뒤 이만큼까지 적분(출렁임 일부 포함)
PRE = 0.3             # 출발 · 감속 시작 전 여유
LAG_MAX = PC.LAG_MAX_S
summ = json.load(open(D / "summary.json", encoding="utf-8"))
layouts = json.load(open(J.ROOT / "motionbench" / "dock_layout.json", encoding="utf-8")) | json.load(open(Path(__file__).parent / "dock_layout_a.json", encoding="utf-8"))
slot = {s: (c + 1, r + 1) for c, col in enumerate(layouts[summ["layout"]]["columns"]) for r, s in enumerate(col)}

# 1. 모터 운전 목록 — (프리셋 이름, 번호, 모터 t · p, 멈춤 목록, 한 번 가기 목록, 가속 시간)
runs = []
for sub in sorted(x for x in D.iterdir() if x.is_dir() and (x / "runs.json").exists()):
    pre = json.load(open(sub / "preset.json", encoding="utf-8")) if (sub / "preset.json").exists() else {}
    t_acc = pre.get("vmax_mm_s", 100) / pre.get("amax_mm_s2", 50)
    for r in json.load(open(sub / "runs.json", encoding="utf-8")):
        if r.get("rc") != 0 or not r.get("motor_csv"):
            continue
        t, p = A.load_motion(r["motor_csv"]); dw = PC.dwells(t, p); legs = PC.leg_list(dw)
        if len(legs) < 2:
            print(f"{sub.name} {r['i']}번: 한 번 가기 {len(legs)}개 — 건너뜀"); continue
        runs.append((sub.name, r["i"], t, p, dw, legs, t_acc))
print(f"모터 운전 {len(runs)}번: " + ", ".join(sorted({x[0] for x in runs})))
if not runs:
    raise SystemExit("판정할 운전이 없다")
t_all0 = min(x[2][0] for x in runs); t_all1 = max(x[2][-1] for x in runs)
# 셀 시계 차는 프리셋(폴더)마다 잰다 — 같은 기록에 정지 장면과 움직이는 장면이 섞이면 장면마다 셀 시각이 다르다(11-10 칸 지도)
OFF = {}
for nm in sorted({x[0] for x in runs}):
    ts = [x[2] for x in runs if x[0] == nm]
    OFF[nm] = P.clock_offset(P.live(min(t[0] for t in ts) - 5, max(t[-1] for t in ts) + 5))
print("셀 시계 차(PC − 셀)", {k: round(v, 2) for k, v in OFF.items()})


def motor_dv(t, p, a, b):
    return A.motor_speed(t, p, [b])[0] - A.motor_speed(t, p, [a])[0]


def integ(it, ia, idt, a, b, bias):
    k = (it >= a) & (it <= b)
    return float(np.sum((ia[k] - bias) * idt[k])) if k.sum() >= 3 else None


def med(v):
    return round(st.median(v), 3) if v else None


def rng(v):
    return (round(min(v), 3), round(max(v), 3)) if v else (None, None)


rows = []
files = sorted(Path(summ["ftg"]).glob("ftg_*.bin")) + [g for x in EXTRA for g in sorted(x.glob("ftg_*.bin"))]
for f in files:
    s = int(f.name.split("_")[1])
    gps, e, o = R.read_file(f)
    if gps is None or (o is None and e is None):
        print(f"{s}: GPS 또는 IMU 없음"); continue
    gt0, fs = gps[0], gps[1]                            # GPS 시각(셀 축), 펌웨어 거른 속도(움직임 판단 프록시)
    parts = [(x[0], x[2], np.full(len(x[0]), d)) for x, d in ((o, 0.1), (e, 0.01)) if x is not None]
    it0 = np.concatenate([q[0] for q in parts]); acc = np.concatenate([q[1] for q in parts]); dt0 = np.concatenate([q[2] for q in parts])
    srt = np.argsort(it0, kind="stable"); itc, acc, dt0 = it0[srt], acc[srt], dt0[srt]
    per = {}
    for name, i, t, p, dw, legs, t_acc in runs:
        it0 = itc + OFF[name]; gt = gt0 + OFF[name]
        m = (it0 >= t[0] - 2) & (it0 <= t[-1] + 2)
        if m.sum() < 50:
            continue
        it = it0[m]; ax = acc[m].astype(np.float64) * R.MMS2; idt = dt0[m]
        spans = A.motion_spans(t, p)
        wins = [(max(t[0], x - 1.0), min(t[-1], y + 1.0)) for x, y in ([spans[0], spans[-1]] if len(spans) > 1 else spans)]
        accel = lambda g: A.motor_accel(t, p, g)
        itl = it.tolist(); axes = {k: ax[:, k].tolist() for k in range(3)}
        a0, b0 = wins[0]
        first = {k: A.find_offset(accel, itl, axes[k], a0, b0, 0.05, 3.0, signed=True) for k in range(3)}
        k = max(first, key=lambda n: abs(first[n]["r"])); sign = 1.0 if first[k]["r"] >= 0 else -1.0
        res = A.offsets(accel, itl, axes[k], wins, 0.05, 3.0, signed=True)
        tm = np.array([A.to_motor(x, res) for x in itl]); ia = sign * ax[:, k]
        gm = np.array([A.to_motor(x, res) for x in gt]); on = (fs > 0) & (fs < 32767)
        r_s, r_d, l_on, l_off = [], [], [], []
        for (a, b) in legs:
            s0, e0 = a[1], b[0]                          # 출발 = 앞 멈춤 끝, 멈춤 = 뒤 멈춤 시작
            d_start = e0 - t_acc                         # 감속 시작(사다리꼴)
            ks = (tm >= a[0] + 0.3) & (tm <= s0 - 0.1)
            bias_s = float(np.median(ia[ks])) if ks.sum() >= 3 else None
            kp = (tm >= s0 + t_acc + 0.3) & (tm <= d_start - 0.3)
            bias_d = float(np.median(ia[kp])) if kp.sum() >= 3 else None
            mv_s = motor_dv(t, p, s0 - PRE, s0 + t_acc + TAIL); mv_d = motor_dv(t, p, d_start - PRE, e0 + TAIL)
            if bias_s is not None and abs(mv_s) > 10:
                v = integ(tm, ia, idt, s0 - PRE, s0 + t_acc + TAIL, bias_s)
                if v is not None:
                    r_s.append(v / mv_s)
            if bias_d is not None and abs(mv_d) > 10:
                v = integ(tm, ia, idt, d_start - PRE, e0 + TAIL, bias_d)
                if v is not None:
                    r_d.append(v / mv_d)
            j = np.where((gm >= s0) & (gm <= s0 + LAG_MAX) & on)[0]          # 출발 뒤 처음 켜진 때
            if len(j):
                l_on.append(float(gm[j[0]] - s0))
            j = np.where((gm >= d_start) & (gm <= e0 + LAG_MAX) & ~on)[0]    # 감속 시작 뒤 처음 꺼진 때
            if len(j):
                l_off.append(float(gm[j[0]] - d_start))
        d = per.setdefault(name, {"r_start": [], "r_decel": [], "lag_on": [], "lag_off": [], "axis": [], "r0": [], "lag": []})
        d["r_start"] += r_s; d["r_decel"] += r_d; d["lag_on"] += l_on; d["lag_off"] += l_off
        d["axis"].append("xyz"[k]); d["r0"].append(round(first[k]["r"], 2)); d["lag"].append(round(res[0]["lag"], 3))
    row = {"cell": s, "slot": slot.get(s)}
    for name, d in per.items():
        lo_s, hi_s = rng(d["r_start"]); lo_d, hi_d = rng(d["r_decel"])
        row[name] = {"n_start": len(d["r_start"]), "r_start": med(d["r_start"]), "r_start_lo": lo_s, "r_start_hi": hi_s,
                     "n_decel": len(d["r_decel"]), "r_decel": med(d["r_decel"]), "r_decel_lo": lo_d, "r_decel_hi": hi_d,
                     "lag_on": med(d["lag_on"]), "lag_off": med(d["lag_off"]), "n_on": len(d["lag_on"]), "n_off": len(d["lag_off"]),
                     "axis": max(set(d["axis"]), key=d["axis"].count), "r0_med": med(d["r0"]), "lag_med": med(d["lag"])}
    rows.append(row)
    print(f"{s} 칸 {slot.get(s)}: " + " | ".join(f"{n} 축{v['axis']} 상관{v['r0_med']} 출발비 {v['r_start']}({v['n_start']}) 감속비 {v['r_decel']}({v['n_decel']}) 켜짐 {v['lag_on']}s 꺼짐 {v['lag_off']}s"
                                          for n, v in row.items() if isinstance(v, dict)), flush=True)
json.dump(rows, open(D / "judge_items.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n요약 — 프리셋별 중앙값(최소 ~ 최대)")
for name in sorted({k for r in rows for k in r if isinstance(r[k], dict)}):
    for key, lab in (("r_start", "출발 비"), ("r_decel", "감속 비"), ("lag_on", "켜짐 s"), ("lag_off", "꺼짐 s")):
        v = [r[name][key] for r in rows if name in r and r[name][key] is not None]
        if v:
            print(f"  {name} {lab}: 중앙 {st.median(v):.3f} ({min(v):.3f} ~ {max(v):.3f}) n={len(v)}")
