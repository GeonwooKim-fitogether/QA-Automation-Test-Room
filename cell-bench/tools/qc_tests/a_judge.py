"""시험 A(안테나 · 수신 회로) 판정 — collectA_<날짜>/r0k 의 30분 창마다 셀 기록(FTG)에서 신호 세기 · 위성 수 · 위치 비율 · 위치 흩어짐을 내고,
칸의 평균(그 셀 자신은 빼고, 다른 바퀴에 그 칸에 앉았던 셀들)을 뺀 값으로 셀을 본다.
가설(v1): 멀쩡한 셀은 칸 평균을 뺀 세기가 ±2 안. −3 이하가 3바퀴 중 2바퀴 이상이면 안테나 · 수신 회로 불량 후보.
사용: python -X utf8 a_judge.py [collectA_20261109]"""
import sys, json, glob, statistics as st, collections, math
from pathlib import Path
from datetime import datetime
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"C:\Dev\ohcoach-cell-tools\cores\fitogether_data_parser")
import numpy as np, pandas as pd
from fitogether_data_parser.ftg.parser import FtgParser
from fitogether_data_parser.ftg.messages.gps_message import GPS_MESSAGE_DATA_TYPE_V2 as GT
from fitogether_data_parser.ftg.utils.ftg_message_utils import decode_datetime_vectorized
DATA = Path(r"C:\Dev\dynamic-bench\cell-bench\data")
sys.path.insert(0, r"C:\Dev\dynamic-bench\cell-bench\tools"); sys.path.insert(0, r"C:\Dev\dynamic-bench\cell-bench")
import protocol_v4 as P
import re


def scene_end(log: Path):
    """round.log 의 'HH:MM:SS LabSat ('S01-…', 경과초)' 줄로 8시간 장면이 끝나는 PC 시각을 센다."""
    try:
        for line in log.read_text(encoding="utf-8").splitlines():
            m = re.match(r"(\d\d):(\d\d):(\d\d) LabSat \('S01-Static-GLE-8h', (\d+)\)", line)
            if m:
                day = datetime.fromtimestamp(log.stat().st_mtime).replace(hour=int(m[1]), minute=int(m[2]), second=int(m[3]), microsecond=0)
                return day.timestamp() + (28800 - int(m[4]))
    except OSError:
        pass
    return None
COLL = DATA / (sys.argv[1] if len(sys.argv) > 1 else "collectA_20261109")
LAT0, LON0 = 37 + 33.05351 / 60, 126 + 58.36159 / 60; KLON = 111320.0 * math.cos(math.radians(LAT0))
KST = 9 * 3600


def deg(v):
    v = v.astype(np.float64); s = np.where(v > 0, 1.0, -1.0); a = np.abs(v)
    return s * (a // 1e10 + (a % 1e10) / 1e8 / 60)


def gps_frame(f: Path):
    data = f.read_bytes(); p = FtgParser("CLBY-4B-00000_0.99_0_1761724328_0.ftg", filecontents=data)
    g = [bytes(pl) for ty, cd, pl, crc in p.raw_parser(memoryview(data)) if ty == 1 and FtgParser._crc_check(cd, crc)]
    if not g:
        return None
    r = pd.DataFrame(np.frombuffer(b"".join(g), dtype=GT))
    dt = pd.to_datetime(pd.Series(decode_datetime_vectorized(r["date"].values, r["time_utc"].values)), errors="coerce", utc=True)
    r["t"] = dt.astype("int64").to_numpy() / 1e9
    r["lat"] = deg(r["gps_nmea_latitude"].to_numpy()); r["lon"] = deg(r["gps_nmea_longitude"].to_numpy())
    r["fileidx"] = np.arange(len(r))
    return r[dt.notna().to_numpy()]


def window_items(r, t0, t1, off):
    """셀 시각 = PC 시각 − off. 정지 장면은 재생마다 같은 시각을 되풀이하므로, 이 창의 기록 구간은 '파일에서 마지막으로 그 시각대가 나온 구간'으로 고른다
    (이번 수집은 바퀴마다 기록을 받아 비웠으므로 파일 안에 그 시각대는 한 번만 있어야 한다 — 두 번 이상이면 알린다)."""
    lo, hi = t0 - off, t1 - off
    m = (r["t"] >= lo) & (r["t"] <= hi)
    sub = r[m]
    if len(sub) == 0:
        return {"n": 0}
    gaps = np.where(np.diff(sub["fileidx"].to_numpy()) > 50)[0]
    note = f"구간 {len(gaps) + 1}개" if len(gaps) else ""
    if len(gaps):
        # 같은 장면 시각대가 지난 재생(11-03 · 11-05 …)에도 있어 여러 구간이 잡힌다 — 오늘 기록은 파일 끝쪽이므로 마지막 구간을 쓴다
        sub = sub.iloc[gaps[-1] + 1:]
    fix = ~((sub["lat"] == 0) & (sub["lon"] == 0)) & (sub["navigation_satellites"] > 0)
    out = {"n": int(len(sub)), "fix_pct": round(100.0 * fix.mean(), 1), "note": note}
    if fix.sum() < 30:
        out["cn0"] = 0.0; out["sats"] = 0.0
        return out
    s = sub[fix]
    out["cn0"] = float(s["avg_cn0_scaled"].median()); out["sats"] = float(s["navigation_satellites"].median())
    e, n = (s["lon"] - LON0) * KLON, (s["lat"] - LAT0) * 110574.0
    out["pos_sd"] = round(float(np.sqrt(((e - e.median()) ** 2 + (n - n.median()) ** 2).mean())), 3)
    sog = s["sog_scaled"].astype(float); sog = sog[(sog >= 0) & (sog <= 12000)]
    out["sog_rms"] = round(float(np.sqrt((sog ** 2).mean())), 1) if len(sog) else None
    out["h_acc"] = round(float(s["h_acc_scaled"].median()) / 1000, 3)
    return out


def main():
    rounds = []
    for sm in sorted(COLL.glob("r*/summary.json")):
        j = json.load(open(sm, encoding="utf-8"))
        if "A" not in j or "ftg" not in j:
            continue
        rounds.append(j)
    print(f"{COLL.name}: 바퀴 {len(rounds)}개 — " + ", ".join(f"r{j['round']:02d} {j['layout']} 감쇠 {j.get('attn_db')} dB" for j in rounds))
    rows = []   # (round, cell, slot, items)
    for j in rounds:
        t0 = datetime.strptime(j["A"]["from"], "%Y-%m-%d %H:%M:%S").timestamp(); t1 = datetime.strptime(j["A"]["to"], "%Y-%m-%d %H:%M:%S").timestamp()
        slot = {s: (c + 1, r + 1) for c, col in enumerate(j["columns"]) for r, s in enumerate(col)}
        ftg = Path(j["ftg"])
        off = P.clock_offset(P.live(t0, t1))          # PC 시각 − 셀(장면) 시각, 라이브 패킷으로 잰다
        t1c = min(t1, scene_end(COLL / f"r{j['round']:02d}" / "round.log") or t1)   # 정지 장면은 8시간이라 그 끝(되감김)에서 자른다
        print(f"r{j['round']:02d}: 창 {j['A']['from'][11:]}~{datetime.fromtimestamp(t1c).strftime('%H:%M:%S')} · 셀 시계 차 {off:.1f}초" + (" · 장면 끝에서 잘랐다" if t1c < t1 else ""))
        for f in sorted(ftg.glob("ftg_*.bin")):
            s = int(f.name.split("_")[1])
            r = gps_frame(f)
            if r is None:
                continue
            # 셀 시계 차: 정지 장면 아래 셀 시각은 장면 시각 — 이 창의 PC 시각과 가장 많이 겹치는 구간을 찾는 대신, 라이브 clock_offset 과 같은 값을 쓴다.
            # 여기서는 장면 재시작마다 값이 바뀌므로, 창 길이(30분)와 같은 길이의 구간 중 '파일 끝에 가장 가까운 연속 구간'을 고른다.
            items = window_items(r, t0, t1c, off)
            items["slot"] = slot.get(s)
            rows.append((j["round"], s, slot.get(s), items))
            print(f"  r{j['round']:02d} {s} 칸 {slot.get(s)}: 세기 {items.get('cn0')} 위성 {items.get('sats')} 위치 {items.get('fix_pct')}% 흩어짐 {items.get('pos_sd')} m {items.get('note', '')}", flush=True)
    json.dump([{"round": a, "cell": b, "slot": c, **d} for a, b, c, d in rows], open(COLL / "a_items.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 칸 평균 빼기 (자기 자신 제외)
    by_slot = collections.defaultdict(list)
    for a, b, c, d in rows:
        if c and d.get("cn0"): by_slot[tuple(c)].append((b, d["cn0"]))      # 위치를 못 잡은 바퀴(세기 0)는 칸 평균에 넣지 않는다
    print("\n칸 평균을 뺀 세기 (자기 자신은 빼고 계산; 바퀴마다 한 줄)")
    resid = collections.defaultdict(list)
    for a, b, c, d in rows:
        if not c or d.get("cn0") is None: continue
        others = [v for s2, v in by_slot[tuple(c)] if s2 != b]
        if not others: continue
        val = -99.0 if not d["cn0"] else round(d["cn0"] - st.median(others), 1)     # -99 = 그 바퀴에 위치를 못 잡음
        resid[b].append((a, val, d["cn0"], d["sats"], c))
    flagged = []
    for b, lst in sorted(resid.items()):
        vals = [x[1] for x in lst]; bad = sum(1 for v in vals if v <= -3)
        mark = " ← 후보" if bad >= 2 or (len(lst) == 1 and bad == 1) else ""
        if mark: flagged.append(b)
        print(f"  {b}: " + "  ".join((f"r{a:02d} 위치 못 잡음(칸 {sl[0]}/{sl[1]})" if v == -99.0 else f"r{a:02d} {v:+.1f}(세기 {c0:.0f}, 위성 {sa:.0f}, 칸 {sl[0]}/{sl[1]})") for a, v, c0, sa, sl in lst) + mark)
    print("\n불량 후보:", flagged or "없음")


def pick_last_window(r, length):
    """파일 끝 쪽에서 연속 기록(시각이 뒤로 뛰지 않는 구간) 중 마지막 것을 고르고, 그 끝에서 length 초만큼을 창으로 쓴다.
    바퀴마다 받으면서 비우므로 파일의 마지막 연속 구간이 이번 바퀴의 기록이다. 기록 받기 전 '버림 5분'과 다시 켜기 구간은 앞에 있어 빠진다."""
    t = r["t"].to_numpy()
    cut = np.where(np.diff(t) < -1.0)[0] + 1
    start = cut[-1] if len(cut) else 0
    seg = r.iloc[start:]
    end = seg["t"].iloc[-1]
    sub = seg[seg["t"] >= end - length]
    fix = ~((sub["lat"] == 0) & (sub["lon"] == 0))
    out = {"n": int(len(sub)), "fix_pct": round(100.0 * fix.mean(), 1), "seg_min": round((seg["t"].iloc[-1] - seg["t"].iloc[0]) / 60, 1)}
    if fix.sum() < 30:
        out["cn0"] = 0.0; out["sats"] = 0.0; return out
    s = sub[fix]
    out["cn0"] = float(s["avg_cn0_scaled"].median()); out["sats"] = float(s["navigation_satellites"].median())
    e, n = (s["lon"] - LON0) * KLON, (s["lat"] - LAT0) * 110574.0
    out["pos_sd"] = round(float(np.sqrt(((e - e.median()) ** 2 + (n - n.median()) ** 2).mean())), 3)
    sog = s["sog_scaled"].astype(float); sog = sog[(sog >= 0) & (sog <= 12000)]
    out["sog_rms"] = round(float(np.sqrt((sog ** 2).mean())), 1) if len(sog) else None
    out["h_acc"] = round(float(s["h_acc_scaled"].median()) / 1000, 3)
    return out


if __name__ == "__main__":
    main()
