"""다중 세트 3단계 — 짝 검증 판정(순수). 명세 3-3."""
import math
import random

from cellbench import pair
from cellbench.config import PAIR

GK = list(range(100, 124))
G2 = list(range(200, 224))
G3 = list(range(300, 324))


def _world(seg, feeds, seed=0, start=60.0, full=None, noise=True):
    """seg = [(t0, t1, 켬)] — 켬이면 '그 플러그가 먹이는' 무리는 +0.4 %p/분, 끔이면 −0.34. 다른 무리는 늘 +0.4(자기 플러그로 충전 중)."""
    rnd = random.Random(seed)
    v = {s: start + (s % 4) * 0.5 for s in GK + G2 + G3}
    if full:
        for s in full:
            v[s] = 99.0
    out = []
    t = seg[0][0]
    while t < seg[-1][1]:
        on = next(o for a, b, o in seg if a <= t < b)
        for s in v:
            fed = s in feeds
            rate = (0.4 if on else -0.34) if fed else 0.4
            if s in (full or ()):
                rate = 0.0
            v[s] = min(100.0, v[s] + rate * 10 / 60)
        out.append((t, {s: int(round(x + (rnd.choice((-1, 0, 0, 0, 1)) if noise else 0))) for s, x in v.items()}))
        t += 10
    return out


QUICK = [(0, 180, True), (180, 480, False)]
PATTERN = [(i * 300, (i + 1) * 300, i % 2 == 0) for i in range(5)]


def test_slope():
    assert abs(pair.slope([(0, 0), (60, 1), (120, 2)]) - 1.0) < 1e-9
    assert math.isnan(pair.slope([(0, 1), (60, 2)])) and math.isnan(pair.slope([(5, 1), (5, 2), (5, 3)]))


def test_quick_pass_when_plug_feeds_gk_only():
    s = _world(QUICK, set(GK))
    r = pair.quick_judge(s, (0, 180), (180, 480), GK, {"set2": G2}, PAIR)
    assert r["verdict"] == "pass", r["why"]
    assert r["r_median"] > 0.5 and r["cells_ok"] >= 20


def test_quick_not_feeding_and_feeds_other():
    r = pair.quick_judge(_world(QUICK, set()), (0, 180), (180, 480), GK, {"set2": G2}, PAIR)
    assert r["verdict"] == "not_feeding"
    r = pair.quick_judge(_world(QUICK, set(G2)), (0, 180), (180, 480), GK, {"set2": G2}, PAIR)
    assert r["verdict"] == "feeds_other" and "set2" in r["why"]
    # 그 무리는 창 안에 자기 플러그가 바뀌었다 → ③ 에서 뺀다 (명세 3-2)
    r = pair.quick_judge(_world(QUICK, set(GK) | set(G2)), (0, 180), (180, 480), GK, {"set2": G2}, PAIR, skip_groups={"set2"})
    assert r["verdict"] == "pass"


def test_pattern_pass_fail_and_foreign():
    assert pair.pattern_judge(_world(PATTERN, set(GK)), PATTERN, GK, PAIR)["verdict"] == "pass"
    moved = set(GK) - {101, 102} | {205}                    # 셀 2대가 다른 Dock 에, 세트 2 의 셀 하나가 이 Dock 에
    r = pair.pattern_judge(_world(PATTERN, moved), PATTERN, GK, PAIR)
    assert r["verdict"] == "fail" and r["not_following"] == [101, 102] and r["foreign"] == [205]


def test_pattern_other_cells_rarely_false_follow():
    """v2 의 'ρ < 0.5' 규칙은 밖의 셀 100대 중 약 20대를 우연히 걸었다(A-M7). 크기(Δ)를 함께 요구하면 0 이어야 한다."""
    for seed in range(10):
        r = pair.pattern_judge(_world(PATTERN, set(GK), seed=seed), PATTERN, GK, PAIR)
        assert r["foreign"] == [], (seed, r["foreign"])


def test_pattern_full_cells_unknown_and_nan_not_following():
    r = pair.pattern_judge(_world(PATTERN, set(GK), full=[100, 101]), PATTERN, GK, PAIR)
    assert r["verdict"] == "pass" and r["unknown"] == [100, 101]
    r = pair.pattern_judge(_world(PATTERN, set(GK), full=[100, 101, 102]), PATTERN, GK, PAIR)
    assert r["verdict"] == "unknown"
    s = [(t, {k: v for k, v in b.items() if not (k == 110 and 300 <= t < 600)}) for t, b in _world(PATTERN, set(GK))]
    r = pair.pattern_judge(s, PATTERN, GK, PAIR)               # 한 구간을 못 들은 셀은 기울기 NaN → 따르지 않는다
    assert r["verdict"] == "fail" and r["not_following"] == [110]


def test_follows_function_is_pluggable():
    """U6 — state 바이트 판정으로 갈아 끼울 수 있다."""
    always = lambda sl, st, rule: (True, 1.0, 1.0)
    r = pair.pattern_judge(_world(PATTERN, set()), PATTERN, GK, PAIR, follows=always)
    assert r["verdict"] == "fail" and r["not_following"] == [] and len(r["foreign"]) == 48
