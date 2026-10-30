"""시뮬레이터 동기 — 좌표 변환 · NMEA · 위치 탭 · 다리 · 장면 생성기. 장비 없이 돈다."""
import csv
import socket
import struct
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from motionbench import geo, sim_bridge  # noqa: E402
from motionbench.motion_record import PosTap  # noqa: E402
import scene_gen  # noqa: E402

ORIGIN = {"lat": 37.548646, "lon": 126.972299, "h": 53.3}


def test_rail_geo_roundtrip():
    for pos in (0, 812.5, 1500, -300):
        lat, lon, h = geo.rail_to_geo(pos, ORIGIN, 99)
        assert abs(geo.geo_to_rail(lat, lon, ORIGIN, 99) - pos) < 0.01 and h == 53.3
    lat, lon, _ = geo.rail_to_geo(1000, ORIGIN, 0)          # 북쪽 1 m
    assert abs((lat - ORIGIN["lat"]) * 110_574 - 1.0) < 1e-6 and lon == ORIGIN["lon"]


def test_nmea_sentences_checksum_and_fields():
    t = datetime(2026, 10, 30, 1, 2, 3, 450_000, tzinfo=timezone.utc)
    s = geo.nmea_pair(t, 500.0, -300.0, ORIGIN, 99)
    gga, rmc = s.strip().split("\r\n")
    for line in (gga, rmc):
        body, cs = line[1:].split("*")
        assert geo.checksum(body) == cs
    assert gga.startswith("$GPGGA,010203.45,3732.") and ",N,12658." in gga and ",E,1,12," in gga
    f = rmc.split(",")
    assert f[0] == "$GPRMC" and f[2] == "A" and abs(float(f[7]) - 0.583) < 0.01 and f[8] == "279.0" and f[9] == "301026"


def test_pos_tap_sends_velocity():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); rx.bind(("127.0.0.1", 0)); rx.settimeout(1)
    tap = PosTap(rx.getsockname()[1])
    tap.send(100.0, 0); tap.send(100.1, 50_000)
    rx.recvfrom(64); d, _ = rx.recvfrom(64)
    t, pos, v = struct.unpack(PosTap.FMT, d)
    assert pos == 50_000 and abs(v - 500_000) < 2_000 and tap.sent == 2
    rx.close()


def test_bridge_extrapolates_and_logs(tmp_path):
    out = []

    class Cap(sim_bridge.Sink):
        def __init__(self): self.spec = "cap"; self.kind = "cap"
        def send(self, text): out.append(text)

    rx_port = 61333
    res = {}
    th = threading.Thread(target=lambda: res.update(sim_bridge.run(Cap(), ORIGIN, 99, 20, 0.05, tmp_path / "b.csv", rx_port, seconds=1.2, log=lambda m: None)))
    th.start(); time.sleep(0.3)
    tap = PosTap(rx_port)
    tap.send(time.time(), 1_000_000); time.sleep(0.1); tap.send(time.time(), 1_050_000)   # 1.0 → 1.05 m, 0.5 m/s
    th.join(3)
    rows = list(csv.DictReader(open(tmp_path / "b.csv", encoding="utf-8")))
    assert res["sent"] >= 20 and res["rail_samples"] == 2 and any(r["stale"] == "0" for r in rows) and rows[0]["stale"] == "1"
    live = [r for r in rows if r["stale"] == "0"]
    assert float(live[-1]["pos_mm"]) > 1050 and float(live[-1]["v_mm_s"]) > 400   # 두 번째 표본 뒤: lead · 외삽으로 마지막 표본보다 앞서 있다
    assert out and out[-1].startswith("$GPGGA")


def test_scene_bands_profile_speeds_and_rest(tmp_path):
    prof = scene_gen.profile_bands([5, 28], cruise_s=2, accel_s=1, dwell_s=1, still0_s=1)
    vmax = max(abs(v) for _, _, v, _ in prof)
    assert abs(vmax - 28 / 3.6 * 1000) < 1 and prof[0][3] == "still" and prof[-1][3] == "dwell_28"
    assert abs(prof[-1][1]) < 1.0                                      # 왕복이라 제자리로
    pc, pn = scene_gen.write("t", prof, ORIGIN, 99, datetime(2026, 10, 30, tzinfo=timezone.utc), tmp_path)
    rows = list(csv.DictReader(open(pc, encoding="utf-8")))
    assert len(rows) == len(prof) and set(rows[0]) >= {"t_gps", "lat", "lon", "pos_mm", "v_kmh", "heading_deg", "step"}
    assert open(pn, encoding="ascii").read().count("$GPRMC") == len(prof)


def test_scene_rail_triangle_profile():
    prof = scene_gen.profile_rail(600, [100, 300], [20, 500], 1, 1)
    assert max(p for _, p, _, _ in prof) <= 600.5 and abs(prof[-1][1]) < 1.0
    assert any(s.startswith("v100_a20") for _, _, _, s in prof) and any(s.startswith("v300_a500") for _, _, _, s in prof)
