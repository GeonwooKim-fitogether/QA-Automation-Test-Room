"""FIRMWARE-2 셀 기록(FTG) 항목 — protocol_v4.ftg_fw2_items · ftg_speed · truth_dist, 그리고 기준의 바닥값 기록. 장비 · 파일 없이 돈다."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
    r = P.ftg_fw2_items(W, fs, sky, 0, 30, 300, 270, 3000)
    assert r["ftg_move"] == 1.0 and r["ftg_on"] == 100.0 and abs(r["ftg_dist"] - 1) < 0.02


def test_filter_off_half_the_time():
    # 움직이는 10초 중 앞 6초는 필터가 꺼져(−1) 0 — 중앙값 0, 켜짐 40 %, 거리 40 %
    fs = [(-1 if x < 16 else 300) if 10 <= x < 20 else 0 for x in W]
    r = P.ftg_fw2_items(W, fs, sky, 0, 30, 300, 270, 3000)
    assert r["ftg_move"] == 0.0 and r["ftg_on"] == 40.0 and abs(r["ftg_dist"] - 0.4) < 0.02


def test_gaps_and_garbage():
    # 기록이 절반만 있으면 거리 비율은 내지 않는다(구간의 80 % 필요), 이상값은 빼고 센다
    w = [x for x in W if x < 15]
    fs = [-27649 if 12 <= x < 13 else sky(x) for x in w]
    r = P.ftg_fw2_items(w, fs, sky, 0, 30, 300, 270, 3000)
    assert r["ftg_dist"] is None and r["ftg_move"] == 1.0 and r["ftg_on"] == 100.0
    assert P.ftg_fw2_items([], [], sky, 0, 30, 300, 270, 3000) == {"ftg_move": None, "ftg_on": None, "ftg_dist": None}


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
    assert P.ftg_match([a, b, c], live) == (b, "one")
    assert P.ftg_match([a, c], live) == (None, "none")          # 맞는 구간이 없으면 고르지 않는다
    assert P.ftg_match([seg(500, 50, 37.00001)], live) == (None, "none")   # 시각이 겹치지 않으면 고르지 않는다


def test_match_tie_broken_by_wider_live_or_left_out():
    # 정지 장면을 다시 틀면 판정 창 안에서는 다른 재생의 위치까지 그대로 같을 수 있다 — 넓힌 같은 재생의 라이브로 가리고,
    # 그래도 같으면 고르지 않는다(앞에 적힌 구간을 고르면 다른 바퀴 값이 들어간다)
    a = seg(100, 300, 37.0, step=0)                        # 30초 내내 같은 위치
    b = (a[0], a[1], a[2], [37.0] * 100 + [37.00003] * 200, a[4])   # 앞 10초만 a 와 같다
    live = [(b[0][i], b[3][i], b[4][i]) for i in range(0, 100, 5)]  # 판정 창 = 앞 10초
    wide = lambda: [(b[0][i], b[3][i], b[4][i]) for i in range(0, 300, 5)]
    assert P.ftg_match([a, b], live) == (None, "tie")
    assert P.ftg_match([a, b], live, wide) == (b, "wide")
    assert P.ftg_match([a, b], live, lambda: live) == (None, "tie")


def test_match_tie_of_same_record_in_two_folders():
    # 셀이 기록을 지우지 않으면 같은 재생이 두 폴더에 길이만 달리 들어 있다 — 창 안 기록이 같으면 어느 쪽을 써도 값이 같다
    a = seg(100, 300, 37.0, step=0)
    short = tuple(c[:150] for c in a)
    live = [(a[0][i], a[3][i], a[4][i]) for i in range(0, 100, 5)]
    assert P.ftg_match([short, a], live, None, lambda tied: True) == (short, "same")
    assert P.ftg_match([short, a], live, None, lambda tied: False) == (None, "tie")


class G:
    def __init__(self, utc, lat, lon):
        self.fix, self.utc, self.lat_deg, self.lon_deg = "A", utc, lat, lon


def test_pick_uses_only_same_play_when_widening():
    # 넓힌 라이브에는 다른 재생(시계 차가 수 분 다른) 패킷도 섞인다 — 같은 재생 것만 써서 가린다
    a = seg(100, 300, 37.0, step=0)
    b = (a[0], [0] * 300, a[2], [37.0] * 100 + [37.00003] * 200, a[4])
    pk = [(1000 + i / 10, G(b[0][i], 37.0, 127.0)) for i in range(0, 100, 5)]       # PC 1000~1010 초, 시계 차 900 초
    other = [(5000 + i / 10, G(a[0][i], 37.0, 127.0)) for i in range(100, 300, 5)]  # 다른 재생 — 시계 차 4900 초, a 와 같다
    mine = [(1000 + i / 10, G(b[0][i], b[3][i], 127.0)) for i in range(100, 300, 5)]
    picks: dict = {}
    assert P.ftg_pick([a, b], 7, pk, 1000, 1010, lambda: {7: pk + mine + other}, picks) is b
    assert P.ftg_pick([a, b], 7, pk, 1000, 1010, lambda: {7: pk + other}, picks) is None
    dup = tuple(c[:150] for c in b)                      # b 의 앞 15초만 담은 다른 폴더의 같은 기록 — 창(앞 10초) 안은 b 와 같다
    assert P.ftg_pick([dup, b], 7, pk, 1000, 1010, lambda: {}, picks) is dup
    assert picks == {"wide": 1, "tie": 1, "same": 1}


def test_segments_keep_runs_with_same_times(monkeypatch):
    # 같은 장면을 두 번 틀어 시작 · 끝 시각과 길이가 같은 두 회차 + 다른 폴더에 겹친 첫 회차 — 회차 둘만 남아야 한다
    np = pytest.importorskip("numpy")         # 셀 기록 읽기 쪽(PY310)에만 있다
    a, b = seg(100, 50, 37.0), seg(100, 50, 37.00001)
    one = tuple(np.array(x, dtype=float) for x in a)
    both = tuple(np.concatenate([np.array(x, dtype=float), np.array(y, dtype=float)]) for x, y in zip(a, b))
    monkeypatch.setattr(P, "_ftg_files", lambda folders: iter([(11572, both), (11572, one)]))
    P._FTGSEG.clear()
    segs = P.ftg_segments(["f1", "f2"])[11572]
    P._FTGSEG.clear()
    assert len(segs) == 2 and sorted(s[3][0] for s in segs) == [37.0, 37.00001]
