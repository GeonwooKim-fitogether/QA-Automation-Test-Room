"""레일 실시간 화면 정지 최소판 (motionbench/qc_live.py) — 장비 없이 돈다. 칸은 qc-protocol-v4 7-5 의 3 · 7-6."""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cellbench import protocol as P  # noqa: E402
from motionbench import qc_live  # noqa: E402

COLS = [[10677, 11619], [10683, 10684]]


def _pkt(serial, fix=b"A", cn0=40, sats=10, battery=77, sog=12):
    d = bytearray(P.LIVE_LEN); d[:4] = P.HEADER; d[4] = P.MSG_LIVE
    struct.pack_into("<H", d, 11, serial)
    d[47], d[48], d[49], d[52] = fix[0], sats << 3, cn0, battery
    struct.pack_into("<H", d, P.GPS_SPEED_AT + 2 * (P.GPS_SPEED_N - 1), sog)   # 끝 칸 = 최신
    return bytes(d)


def test_frame_has_v4_fields_and_cell_values():
    q = qc_live.QcLive(COLS, 100.0)
    for t in (101.0, 101.5, 102.0):
        q.feed(_pkt(10677), t)
    q.feed(_pkt(11619, fix=b"N"), 102.0)
    q.feed(_pkt(65000), 102.0)                                             # 독에 없는 셀은 버린다
    f = q.frame(102.5)
    assert f["step"]["key"] == "A0" and f["step"]["scene_step"] == "still" and f["motor"]["state"] == "stopped"
    assert f["conditions"]["protocol"] == "v4" and f["grid"] == [[10677, 10683], [11619, 10684]]
    c = f["cells"]["10677"]
    assert (c["fix"], c["cn0"], c["sats"], c["battery"], c["sog_raw"], c["sog_err"]) == ("A", 40, 10, 77, 12, 12)
    assert c["packets_10s"] == 3 and c["age_s"] == 0.5 and c["fix_age_s"] == 1.5 and c["dock"] == {"col": 1, "row": 1}
    n = f["cells"]["11619"]
    assert n["fix"] == "N" and n["sog_raw"] is None and n["fix_age_s"] is None   # 고정 없으면 속도를 싣지 않는다
    assert f["cells"]["10683"]["age_s"] is None and "65000" not in f["cells"]
    json.dumps(f, allow_nan=False)                                          # NaN · Infinity 없음 (7-6 의 6)


def test_idle_frame():
    f = qc_live.idle(5.0)
    assert f["step"] == {"key": "idle", "index": None}


def test_layout_reads_the_y_set():
    cols = qc_live.layout()
    assert len(cols) == 4 and all(len(c) == 6 for c in cols)
