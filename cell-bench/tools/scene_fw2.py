"""GNSS-MOVE 장면 재료(2026-11-12 까지 FIRMWARE-2 로 부르던 장면 동기 운전 — 파일 이름은 그대로 둔다) — 레일이 움직일 경로를 SatGen 이 받는 NMEA GGA(10 Hz)와 정답 표로 만든다. 체험판 한도 120초 안.

  python cell-bench/tools/scene_fw2.py --v 100 --cycles 2 --name S04_RailB100
  python cell-bench/tools/scene_fw2.py --v 500 --cycles 5 --name S05_RailB500
→ cell-bench/data/scenes/<이름>/<이름>.nmea · <이름>_truth.csv (t_s, pos_mm, v_mm_s)

위성-12 장면(2026-11-13, 운전-등속 6단계 · 가속도 8 m/s²):
  python cell-bench/tools/scene_fw2.py --v 1000 --a 8000 --cycles 0 --start 16:00:00 --name S09_Move1000 --out <장면 폴더>
  python cell-bench/tools/scene_fw2.py --v 2000 --a 8000 --cycles 10 --length 1200 --start 16:00:00 --name S11_Move2000 --out <장면 폴더>
  --cycles 0 은 120초 안에 들어가는 가장 많은 왕복 수(끝 멈춤 3초 이상 남김). 레일 실행기의 왕복 상한은 10번이라 넘으면 10 을 준다.
  2.0 m/s 만 1,200 mm(레일 250 ↔ 1,450 mm) — 급정지 거리 200 mm 를 더하면 1,500 mm 는 레일 여유를 넘는다(2026-11-13 사용자 결정 A).
  그다음 satgen_scene.py 로 장면 파일(.LS3)을 만든다 — 장면 시각은 SatGen 설정의 시작 시각이 정하고, 경로 GGA 시각은 그와 같게 둔다
  (SatGen 화면 프로그램은 앞에 정지 80초를 붙인 <이름>.nmea.pad 를 만들지만 장면 파일에는 0초부터 들어간다). 옛 장면은 00:00:00 UTC ·
  앙각 마스크 20° 라 GPS 위성 6개만 들어갔다(LS3 에서 PRN 10 · 15 · 18 · 20 · 23 · 24 포착). 2026-10-30 16:00:00 UTC ·
  마스크 10° 이면 알마낙 2026-303 기준 12개(PRN 3 · 4 · 6 · 9 · 11 · 12 · 13 · 14 · 17 · 19 · 21 · 22)가 장면 내내 그대로다.

자리: 정지 장면 S01 의 자리(LabSat MON:LOC 37°33.05351'N 126°58.36159'E, 높이 101 m) — 셀이 이미 이 자리에 고정해 있다.
움직임: 20초 정지 → 동쪽으로 1,500 mm 왕복 cycles 번(가속도 · 끝 멈춤은 레일 승인 값과 같게) → 끝나면 처음 자리에 멈춘다.
2026-10-30 S03_RailB120(300 mm/s · 5번)을 만든 스크래치 스크립트를 속도 · 왕복 수를 받게 옮긴 것이다.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAT0, LON0, H = 37 + 33.05351 / 60, 126 + 58.36159 / 60, 101.0
DT, T_END, T_STILL, L = 0.1, 119.9, 20.0, 1500.0


def make_pos(v: float, a: float, dwell: float, cycles: int, L: float = L):
    ta = v / a
    da = 0.5 * a * ta * ta
    t_leg = 2 * ta + (L - 2 * da) / v

    def leg(t):
        if t < ta:
            return 0.5 * a * t * t, a * t
        if t < t_leg - ta:
            return da + v * (t - ta), v
        if t < t_leg:
            u = t_leg - t
            return L - 0.5 * a * u * u, a * u
        return L, 0.0

    def pos(t):
        if t < T_STILL:
            return 0.0, 0.0
        k, r = divmod(t - T_STILL, t_leg + dwell)
        if k >= 2 * cycles:
            return 0.0, 0.0
        d, s = leg(r) if r < t_leg else (L, 0.0)
        return (d, s) if int(k) % 2 == 0 else (L - d, -s)

    end = T_STILL + 2 * cycles * (t_leg + dwell)
    return pos, end


def nmea_deg(x: float, w: int) -> str:
    d = int(abs(x)); m = (abs(x) - d) * 60
    return f"{d:0{w}d}{m:09.6f}"


def cs(s: str) -> str:
    c = 0
    for ch in s:
        c ^= ord(ch)
    return f"{c:02X}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--v", type=float, required=True, help="속도 mm/s")
    ap.add_argument("--a", type=float, default=500.0, help="가속도 mm/s² (기본 500)")
    ap.add_argument("--dwell", type=float, default=3.0)
    ap.add_argument("--cycles", type=int, required=True, help="왕복 수. 0 이면 120초 안에 들어가는 최대(끝 멈춤 3초 이상)")
    ap.add_argument("--start", default="00:00:00", help="장면 첫 GGA 의 UTC 시각 HH:MM:SS (기본 00:00:00 — 옛 장면)")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "scenes", help="장면 폴더(그 아래 <이름>/ 을 만든다)")
    ap.add_argument("--length", type=float, default=L, help=f"한 번 가기 거리 mm (기본 {L:g}). 2.0 m/s 는 레일 급정지 여유 때문에 1200")
    ap.add_argument("--name", required=True)
    a = ap.parse_args(argv)
    if a.cycles == 0:
        while make_pos(a.v, a.a, a.dwell, a.cycles + 1, a.length)[1] <= T_END - 3.0:
            a.cycles += 1
        if a.cycles == 0:
            raise SystemExit("왕복 한 번도 120초 안에 들어가지 않는다")
    pos, end = make_pos(a.v, a.a, a.dwell, a.cycles, a.length)
    if end > T_END:
        raise SystemExit(f"움직임이 {end:.1f}초에 끝나 체험판 한도 {T_END + 0.1:.0f}초를 넘는다 — 왕복 수를 줄인다")
    h0, m0, s0 = (int(x) for x in a.start.split(":"))
    t_start = h0 * 3600 + m0 * 60 + s0
    out = a.out / a.name
    out.mkdir(parents=True, exist_ok=True)
    k_lon = 111320.0 * math.cos(math.radians(LAT0))
    n = int(round(T_END / DT)) + 1
    with open(out / f"{a.name}.nmea", "w", newline="\r\n") as f, open(out / f"{a.name}_truth.csv", "w", encoding="utf-8") as g:
        g.write("t_s,pos_mm,v_mm_s\n")
        for i in range(n):
            t = i * DT
            p, v = pos(t)
            lat, lon = LAT0, LON0 + (p / 1000.0) / k_lon
            tod = t_start + t
            hh, mm_, ss = int(tod // 3600), int(tod % 3600 // 60), tod % 60
            body = (f"GPGGA,{hh:02d}{mm_:02d}{ss:05.2f},{nmea_deg(lat, 2)},N,{nmea_deg(lon, 3)},E,1,12,0.8,{H:.1f},M,0.0,M,,")
            f.write(f"${body}*{cs(body)}\n")
            g.write(f"{t:.1f},{p:.1f},{v:.1f}\n")
    print(f"{a.name}: {a.v:g} mm/s · {a.a:g} mm/s² · {a.length:g} mm 왕복 {a.cycles}번 · 움직임 끝 {end:.1f}초 · 시작 {a.start} UTC → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
