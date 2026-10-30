"""장면 파일 생성기 — 프로토콜 v4 파트 A(셀 정지 + 장면)와 파트 B 대비책(레일 경로를 미리 파일로)의 정답 경로를 만든다.

내는 것 둘(둘 다 data/ 아래, 결과판 약속):
  scene_<이름>.csv  — t_s, t_gps(epoch), lat, lon, h_m, pos_mm(직선 거리), v_kmh, heading_deg, step   ← 분석 · 결과판의 정답
  scene_<이름>.nmea — 같은 경로의 GGA · RMC (10 Hz)                                                  ← SatGen 에 넣는 경로(가져오기 형식은 미확인)
장면 종류:
  still    : 정지 (--seconds)
  bands    : FIFA 속도 구간 직선 왕복 — 속도마다 가속 → 등속 → 감속 → 멈춤 (--speeds km/h --cruise 초 --accel 초 --dwell 초)
  rail     : 레일 프로토콜 그대로 — 거리 · 속도 · 가속도 계단 · 왕복 수 (파트 B 대비책, 단위 mm · mm/s · mm/s²)
사용: .venv/Scripts/python.exe cell-bench/tools/scene_gen.py bands --name A_bands --origin 37.548646 126.972299 53.3 --bearing 99 \
        --speeds 5 11 18 23 28 --cruise 20 --accel 2 --dwell 10 --t0 2026-10-30T01:00:00Z
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motionbench import geo  # noqa: E402

DT = 0.1


def profile_still(seconds: float) -> list[tuple[float, float, float, str]]:
    """[(t, pos_mm, v_mm_s, step)]"""
    return [(i * DT, 0.0, 0.0, "still") for i in range(int(seconds / DT) + 1)]


def _leg(t0: float, p0: float, v_peak: float, a: float, cruise_s: float, direction: int, step: str) -> list[tuple[float, float, float, str]]:
    """멈춤 → 가속(a) → 등속(cruise_s) → 감속 → 멈춤, direction ±1. 시간 · 위치는 누적."""
    out = []; t = t0; p = p0; v = 0.0
    ta = v_peak / a
    while t < t0 + ta:
        v = a * (t - t0); p = p0 + direction * 0.5 * a * (t - t0) ** 2; out.append((t, p, direction * v, step)); t += DT
    p1 = p0 + direction * 0.5 * a * ta * ta; t1 = t0 + ta
    while t < t1 + cruise_s:
        p = p1 + direction * v_peak * (t - t1); out.append((t, p, direction * v_peak, step)); t += DT
    p2 = p1 + direction * v_peak * cruise_s; t2 = t1 + cruise_s
    while t < t2 + ta:
        v = v_peak - a * (t - t2); p = p2 + direction * (v_peak * (t - t2) - 0.5 * a * (t - t2) ** 2); out.append((t, p, direction * v, step)); t += DT
    out.append((t2 + ta, p2 + direction * 0.5 * v_peak * ta, 0.0, step))     # 멈춘 점을 정확히 — 다음 다리가 여기서 시작한다
    return out


def profile_bands(speeds_kmh: list[float], cruise_s: float, accel_s: float, dwell_s: float, still0_s: float = 60) -> list:
    out = profile_still(still0_s); t = out[-1][0]; p = 0.0
    for v_kmh in speeds_kmh:
        v = v_kmh / 3.6 * 1000; a = v / accel_s
        for direction in (1, -1):
            leg = _leg(t + DT, p, v, a, cruise_s, direction, f"band_{v_kmh:g}")
            out += leg; t, p = leg[-1][0], leg[-1][1]
            for i in range(int(dwell_s / DT)):
                t += DT; out.append((t, p, 0.0, f"dwell_{v_kmh:g}"))
    return out


def profile_rail(dist_mm: float, speeds: list[float], accels: list[float], cycles: int, dwell_s: float) -> list:
    """레일 프로토콜: (속도, 가속도) 짝마다 왕복 cycles 번. speeds 와 accels 길이가 같아야 한다."""
    out = profile_still(5); t = out[-1][0]; p = 0.0
    for v, a in zip(speeds, accels):
        for _ in range(cycles):
            for direction in (1, -1):
                ta = v / a; cruise = max(0.0, (dist_mm - a * ta * ta) / v)   # 삼각형 프로파일이면 등속 0
                leg = _leg(t + DT, p, v, a, cruise, direction, f"v{v:g}_a{a:g}")
                out += leg; t, p = leg[-1][0], leg[-1][1]
                for i in range(int(dwell_s / DT)):
                    t += DT; out.append((t, p, 0.0, f"dwell_v{v:g}"))
    return out


def write(name: str, prof: list, origin: dict, bearing: float, t0: datetime, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pc, pn = out_dir / f"scene_{name}.csv", out_dir / f"scene_{name}.nmea"
    with open(pc, "w", newline="", encoding="utf-8") as f, open(pn, "w", newline="", encoding="ascii") as g:
        w = csv.writer(f); w.writerow(["t_s", "t_gps", "lat", "lon", "h_m", "pos_mm", "v_kmh", "heading_deg", "step"])
        for t, p, v, step in prof:
            tt = t0 + timedelta(seconds=t)
            lat, lon, h = geo.rail_to_geo(p, origin, bearing)
            head = bearing % 360 if v >= 0 else (bearing + 180) % 360
            w.writerow([f"{t:.1f}", f"{tt.timestamp():.1f}", f"{lat:.9f}", f"{lon:.9f}", f"{h:.1f}", f"{p:.1f}", f"{abs(v) * 3.6 / 1000:.3f}", f"{head:.1f}", step])
            g.write(geo.nmea_pair(tt, p, v, origin, bearing))
    return pc, pn


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("kind", choices=["still", "bands", "rail"])
    ap.add_argument("--name", required=True)
    ap.add_argument("--origin", nargs=3, type=float, required=True, metavar=("LAT", "LON", "H_M"))
    ap.add_argument("--bearing", type=float, required=True)
    ap.add_argument("--t0", default=None, help="장면 시작 GPS 시각 ISO (기본 지금 UTC)")
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--speeds", nargs="*", type=float, default=[5, 11, 18, 23, 28], help="bands: km/h · rail: mm/s")
    ap.add_argument("--accels", nargs="*", type=float, default=None, help="rail: mm/s² (speeds 와 같은 개수)")
    ap.add_argument("--cruise", type=float, default=20); ap.add_argument("--accel", type=float, default=2)
    ap.add_argument("--dwell", type=float, default=10); ap.add_argument("--dist", type=float, default=1500); ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--out", default=str(ROOT / "data"))
    a = ap.parse_args(argv)
    origin = {"lat": a.origin[0], "lon": a.origin[1], "h": a.origin[2]}
    t0 = datetime.fromisoformat(a.t0.replace("Z", "+00:00")) if a.t0 else geo.now_utc()
    if a.kind == "still":
        prof = profile_still(a.seconds)
    elif a.kind == "bands":
        prof = profile_bands(a.speeds, a.cruise, a.accel, a.dwell)
    else:
        accels = a.accels or [500.0] * len(a.speeds)
        if len(accels) != len(a.speeds):
            ap.error("--accels 는 --speeds 와 개수가 같아야 한다")
        prof = profile_rail(a.dist, a.speeds, accels, a.cycles, a.dwell)
    pc, pn = write(a.name, prof, origin, a.bearing, t0, Path(a.out))
    print(f"{a.kind} · {len(prof)}표본 · {prof[-1][0]:.0f}초 · 최대 {max(abs(v) for _, _, v, _ in prof) * 3.6 / 1000:.1f} km/h → {pc.name} · {pn.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
