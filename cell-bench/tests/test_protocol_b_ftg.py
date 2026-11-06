"""파트 B 셀 기록(FTG) 항목 — protocol_v4.ftg_b_items · ftg_speed · truth_dist, 그리고 기준의 바닥값 기록. 장비 · 파일 없이 돈다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from motionbench import qc_criteria as Q  # noqa: E402
import protocol_v4 as P  # noqa: E402


def sky(x):            # 0~10초 정지, 10~20초 300 mm/s, 20~30초 정지
    return 300.0 if 10 <= x < 20 else 0.0


W = [i / 10 for i in range(300)]          # 10 Hz, 30초


def test_ftg_speed_codes():
    assert P.ftg_speed(300) == 300.0
    assert P.ftg_speed(-1) == 0.0 and P.ftg_speed(-3) == 0.0     # 필터가 끈 상태
    assert P.ftg_speed(-27649) is None and P.ftg_speed(32767) is None and P.ftg_speed(25463) is None


def test_truth_dist_after_still():
    tt, tv = [0, 10, 10.0001, 20, 20.0001, 30], [0, 0, 300, 300, 0, 0]
    assert abs(P.truth_dist(tt, tv, 5) - 3000) < 1          # 300 mm/s × 10초 = 3 m


def test_follows_truth_gives_one():
    fs = [sky(x) for x in W]
    r = P.ftg_b_items(W, fs, sky, 0, 30, 300, 270, 3000)
    assert r["ftg_move"] == 1.0 and r["ftg_on"] == 100.0 and abs(r["ftg_dist"] - 1) < 0.02


def test_filter_off_half_the_time():
    # 움직이는 10초 중 앞 6초는 필터가 꺼져(−1) 0 — 중앙값 0, 켜짐 40 %, 거리 40 %
    fs = [(-1 if x < 16 else 300) if 10 <= x < 20 else 0 for x in W]
    r = P.ftg_b_items(W, fs, sky, 0, 30, 300, 270, 3000)
    assert r["ftg_move"] == 0.0 and r["ftg_on"] == 40.0 and abs(r["ftg_dist"] - 0.4) < 0.02


def test_gaps_and_garbage():
    # 기록이 절반만 있으면 거리 비율은 내지 않는다(구간의 80 % 필요), 이상값은 빼고 센다
    w = [x for x in W if x < 15]
    fs = [-27649 if 12 <= x < 13 else sky(x) for x in w]
    r = P.ftg_b_items(w, fs, sky, 0, 30, 300, 270, 3000)
    assert r["ftg_dist"] is None and r["ftg_move"] == 1.0 and r["ftg_on"] == 100.0
    assert P.ftg_b_items([], [], sky, 0, 30, 300, 270, 3000) == {"ftg_move": None, "ftg_on": None, "ftg_dist": None}


def test_derive_records_floor_and_notes_no_spread():
    # 셀 사이 차이가 바닥값(0.01)보다 작다 — 자격은 그대로 내되, 실제 차이와 바닥값을 따로 남기고 표시한다
    per = {str(s): [1.0 + s * 0.0005 + (0.0001 if r % 2 else 0)] * 1 for s in range(8) for r in range(1)}
    per = {s: [v[0], v[0] + 0.0002, v[0] - 0.0002] for s, v in per.items()}
    c = Q.derive(per, "ftg_move")
    assert c["floor"] == 0.01 and c["between"] == 0.01 and c["between_raw"] < 0.01
    assert "셀 사이 차이 없음" in c["note"]


def test_derive_no_note_when_real_spread():
    per = {str(s): [1.0 + s * 0.05, 1.0 + s * 0.05 + 0.001, 1.0 + s * 0.05 - 0.001] for s in range(8)}
    c = Q.derive(per, "ftg_move")
    assert c["between_raw"] > c["floor"] and "note" not in c


def seg(t0, n, lat0, step=1e-5):
    w = [t0 + i / 10 for i in range(n)]
    return (w, [300] * n, [0] * n, [lat0 + i * step for i in range(n)], [127.0] * n)


def test_match_picks_segment_with_same_positions():
    # 같은 GPS 시각을 갖는 구간 셋(장면을 세 번 튼 것) — 라이브 위치와 그대로 같은 구간만 고른다
    a, b, c = seg(100, 50, 37.0), seg(100, 50, 37.00001), seg(100, 50, 37.00002)
    live = [(b[0][i], b[3][i], b[4][i]) for i in range(0, 50, 5)]
    assert P.ftg_match([a, b, c], live) is b
    assert P.ftg_match([a, c], live) is None          # 맞는 구간이 없으면 고르지 않는다
    assert P.ftg_match([seg(500, 50, 37.00001)], live) is None   # 시각이 겹치지 않으면 고르지 않는다
