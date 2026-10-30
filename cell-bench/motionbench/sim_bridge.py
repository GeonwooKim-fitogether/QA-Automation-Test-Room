"""시뮬레이터 다리 — 레일 위치(모터 프로그램의 UDP 탭)를 받아 NMEA(GGA · RMC)로 바꿔 LabSat(SatGen Real-Time+)에 보낸다. 프로토콜 v4 파트 B.

모터 프로그램(run_motion.py long)이 50 Hz 로 127.0.0.1:60333 에 [t_wall, pos µm, v µm/s] 를 떨어뜨리면, 이 프로그램이
그것을 받아 정해진 주기(기본 20 Hz)로 NMEA 를 만들어 보낸다. 보내는 길은 셋 중 하나 — UDP · TCP(이더넷) · 시리얼(RS232).
레일 데이터가 없을 때(시험 전 · 후)는 원점(0 mm) 정지 위치를 계속 보내 하늘이 고정을 잃지 않게 한다.
--lead 로 지연 상쇄: 마지막 속도로 lead 초 뒤의 위치를 미리 보낸다(LabSat 처리 지연 ~수십 ms, 미확인 → 실측 뒤 조정).
보낸 것은 전부 data/sim_bridge_<시각>.csv 에 남긴다(t_wall, pos_mm, v_mm_s, lat, lon) — 셀 FTG 와 맞춰 sync_lag 를 잰다.
모터 프로그램과 다른 프로세스에서 돈다. 2 ms 주기를 흔들지 않도록 보통 우선순위 그대로.

사용 (작업 트리 루트에서):
  .venv/Scripts/python.exe cell-bench/motionbench/sim_bridge.py --to udp:192.168.1.50:5001 --origin 37.548646 126.972299 53.3 --bearing 99
  .venv/Scripts/python.exe cell-bench/motionbench/sim_bridge.py --to serial:COM5:115200 ...
  .venv/Scripts/python.exe cell-bench/motionbench/sim_bridge.py --to print ...     (보내지 않고 화면에만 — 점검용)
"""
from __future__ import annotations

import argparse
import csv
import socket
import struct
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motionbench import geo  # noqa: E402
from motionbench.motion_record import POS_TAP_PORT, PosTap  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
STALE_S = 0.5            # 이보다 오래 레일 데이터가 없으면 "정지 · 원점" 으로 본다


class Sink:
    """NMEA 를 보내는 곳. udp:host:port · tcp:host:port · serial:COMx:baud · print"""

    def __init__(self, spec: str):
        self.spec = spec
        kind, _, rest = spec.partition(":")
        self.kind = kind
        if kind == "udp":
            host, port = rest.rsplit(":", 1)
            self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.addr = (host, int(port))
        elif kind == "tcp":
            host, port = rest.rsplit(":", 1)
            self.s = socket.create_connection((host, int(port)), timeout=5)
        elif kind == "serial":
            import serial  # pyserial — requirements.txt
            dev, baud = rest.rsplit(":", 1)
            self.s = serial.Serial(dev, int(baud), timeout=0)
        elif kind == "print":
            self.s = None
        else:
            raise ValueError(f"--to 는 udp: · tcp: · serial: · print 중 하나 ({spec!r})")

    def send(self, text: str) -> None:
        b = text.encode("ascii")
        if self.kind == "udp":
            self.s.sendto(b, self.addr)
        elif self.kind == "tcp":
            self.s.sendall(b)
        elif self.kind == "serial":
            self.s.write(b)
        else:
            sys.stdout.write(text)


def run(sink: Sink, origin: dict, bearing: float, hz: float, lead_s: float, log_path: Path, tap_port: int = POS_TAP_PORT,
        seconds: float | None = None, log=print) -> dict:
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", tap_port)); rx.setblocking(False)
    fmt = struct.Struct(PosTap.FMT)
    last = None                                    # (t_wall, pos_um, v_um_s) 마지막 레일 표본
    n_sent = n_rail = 0
    period = 1.0 / hz
    t_end = None if seconds is None else time.monotonic() + seconds
    nxt = time.monotonic()
    with open(log_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["t_wall", "t_rail", "pos_mm", "v_mm_s", "lat", "lon", "stale"])
        log(f"{time.strftime('%H:%M:%S')} 다리 시작 → {sink.spec} · {hz:g} Hz · lead {lead_s * 1000:.0f} ms · 원점 {origin} · 방위 {bearing}° · 기록 {log_path.name}")
        try:
            while t_end is None or time.monotonic() < t_end:
                while True:                        # 쌓인 탭 표본은 마지막 것만 쓴다
                    try:
                        d, _ = rx.recvfrom(64)
                    except (BlockingIOError, OSError):
                        break
                    if len(d) == fmt.size:
                        last = fmt.unpack(d); n_rail += 1
                now = time.time()
                stale = last is None or now - last[0] > STALE_S
                if stale:
                    pos_mm, v = 0.0, 0.0
                else:
                    pos_mm = (last[1] + last[2] * (now - last[0] + lead_s)) / 1000   # 마지막 속도로 지금 + lead 까지 외삽
                    v = last[2] / 1000
                t = geo.now_utc()
                sink.send(geo.nmea_pair(t, pos_mm, v, origin, bearing))
                lat, lon, _ = geo.rail_to_geo(pos_mm, origin, bearing)
                w.writerow([f"{now:.6f}", "" if last is None else f"{last[0]:.6f}", f"{pos_mm:.3f}", f"{v:.1f}", f"{lat:.9f}", f"{lon:.9f}", int(stale)])
                n_sent += 1
                if n_sent % int(hz * 10) == 0:
                    log(f"{time.strftime('%H:%M:%S')} 보냄 {n_sent} · 레일 표본 {n_rail} · 지금 {pos_mm:.1f} mm · {v:.0f} mm/s{' · 레일 없음(원점 고정)' if stale else ''}")
                nxt += period
                wait = nxt - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                else:
                    nxt = time.monotonic()
        except KeyboardInterrupt:
            pass
    rx.close()
    return {"sent": n_sent, "rail_samples": n_rail, "log": str(log_path)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--to", required=True, help="udp:host:port · tcp:host:port · serial:COMx:baud · print")
    ap.add_argument("--origin", nargs=3, type=float, required=True, metavar=("LAT", "LON", "H_M"), help="레일 0 mm 의 위도 경도 타원체고")
    ap.add_argument("--bearing", type=float, required=True, help="레일 +pos 방향, 북에서 시계 방향 도")
    ap.add_argument("--hz", type=float, default=20.0, help="NMEA 보내는 주기 (기본 20, LabSat Real-Time+ 5~100)")
    ap.add_argument("--lead", type=float, default=0.0, help="미리 보낼 시간 초 (시뮬레이터 지연 상쇄, 실측 뒤 조정)")
    ap.add_argument("--seconds", type=float, default=None, help="이 시간만 돌고 끝 (기본 Ctrl+C 까지)")
    ap.add_argument("--tap-port", type=int, default=POS_TAP_PORT)
    a = ap.parse_args(argv)
    DATA.mkdir(exist_ok=True)
    origin = {"lat": a.origin[0], "lon": a.origin[1], "h": a.origin[2]}
    r = run(Sink(a.to), origin, a.bearing, a.hz, a.lead, DATA / f"sim_bridge_{time.strftime('%Y%m%d-%H%M%S')}.csv", a.tap_port, a.seconds)
    print(f"끝 — 보냄 {r['sent']} · 레일 표본 {r['rail_samples']} → {r['log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
