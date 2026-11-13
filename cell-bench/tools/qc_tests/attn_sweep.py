"""LabSat 출력 감쇠를 단계별로 올리며 셀 23대의 신호 세기 · 위성 수 · 위치 잡힘을 본다 — "멀쩡한 셀이 겨우 잡는 세기" 찾기.
단계마다: ATTN 설정 → 60초 안정 → 90초 라이브 기록 → 셀별 중앙값. 끝나면 ATTN:00 으로 되돌린다. 모터 없음.
사용: python -X utf8 attn_sweep.py 0 6 12 18 24 30"""
import sys, time, glob, json, collections, statistics as st
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT))   # <작업 트리>/cell-bench
sys.stdout.reconfigure(encoding="utf-8")
import qc_round as Q, protocol_v4 as P
from motionbench.labsat import LabSat, clean
from cellbench.protocol import parse_gps
HERE = Path(__file__).resolve().parent
LEVELS = [int(x) for x in sys.argv[1:]] or [0, 6, 12, 18, 24, 30]
FOIL = (10684, 10688)
LAYOUT = json.load(open(ROOT / "motionbench" / "dock_layout.json", encoding="utf-8"))["Y"]["columns"]
SLOT = {s: (c + 1, r + 1) for c, col in enumerate(LAYOUT) for r, s in enumerate(col)}
ls = LabSat()
print("LabSat", ls.status(), "· 지금 ATTN", clean(ls._one("ATTN:?")), flush=True)
out = {"levels": [], "slot": {str(k): v for k, v in SLOT.items()}}
try:
    for lv in LEVELS:
        r = clean(ls._one(f"ATTN:{lv:02d}"))
        if "ERR" in r.upper():
            print(f"ATTN:{lv:02d} → {r!r} — 이 값은 받지 않는다. 여기서 멈춘다", flush=True); break
        print(f"\n== ATTN {lv} dB (응답 {r!r}) · 60초 안정", flush=True); time.sleep(60)
        t0 = time.time(); tap = Q.start_tap("GNSS-STATIC", 2, HERE / "attn_tap.log"); time.sleep(90); Q.stop_tap(tap)
        f = sorted(glob.glob(str(Q.DATA / "live_raw" / "*.bin")), key=lambda x: Path(x).stat().st_mtime)[-1]
        by = collections.defaultdict(list)
        for t, d in P.read_raw(f):
            g = parse_gps(d)
            if g and t >= t0:
                by[g.serial].append(g)
        rows = {}
        for s, gs in sorted(by.items()):
            fixp = 100.0 * sum(1 for g in gs if g.fix in ("A", "D", "F", "R")) / len(gs)
            rows[s] = {"n": len(gs), "cn0": st.median(g.cn0 for g in gs), "sats": st.median(g.sats for g in gs), "fix_pct": round(fixp, 1), "slot": SLOT.get(s)}
        heard = sorted(rows); fixed = [s for s in heard if rows[s]["fix_pct"] >= 90]
        strong = [s for s in heard if s not in FOIL and SLOT.get(s, (9, 9))[0] >= 2 and 2 <= SLOT.get(s, (9, 9))[1] <= 6]
        print(f"들리는 셀 {len(heard)} · 위치 잡은 셀(90 % 이상) {len(fixed)} · 센 칸(열 2~4, 줄 2~6) 셀 신호 세기 중앙 {st.median(rows[s]['cn0'] for s in strong) if strong else None} · 위성 중앙 {st.median(rows[s]['sats'] for s in strong) if strong else None}", flush=True)
        for s in FOIL:
            if s in rows: print(f"  호일 {s}: 세기 {rows[s]['cn0']:.0f} · 위성 {rows[s]['sats']:.0f} · 위치 {rows[s]['fix_pct']}%", flush=True)
        lost = [f"{s}(칸 {SLOT.get(s)})" for s in heard if rows[s]["fix_pct"] < 90]
        print("  위치 놓친 셀:", lost or "없음", flush=True)
        print("  열별 세기 중앙:", {c: st.median(rows[s]["cn0"] for s in heard if SLOT.get(s, (0,))[0] == c) for c in (1, 2, 3, 4) if any(SLOT.get(s, (0,))[0] == c for s in heard)}, flush=True)
        out["levels"].append({"attn": lv, "t0": t0, "cells": {str(s): v for s, v in rows.items()}})
        json.dump(out, open(HERE / "attn_sweep.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
finally:
    r = clean(ls._one("ATTN:00")); print(f"\nATTN 을 0 으로 되돌림 → {r!r} · 확인 {clean(ls._one('ATTN:?'))!r}", flush=True)
