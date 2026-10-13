"""자동 합류 — 문턱 자동 도출(cellbench/derive.py) 순수 판정 검사.

오탐(틀린 짝을 통과시킴) 방지가 최우선이라, 합성 보정 기록으로 다섯 경우를 만들어 각각의 판정을 본다.
  맞는 짝(세트 k 셀만 반응) · 틀린 짝(대조군이 플러그를 따라 내려감) · 일부 셀 무반응 · 노이즈만 · 대조군이 흔들림(세트 1 단계 전환)
그리고 도출한 문턱이 실제 판정 함수(pair.quick_judge · pattern_judge)에서 맞는 짝은 통과시키고 틀린 짝은 걸러내는지.
기록은 verifier 가 적는 것과 같은 모양이다 — 10초마다 한 줄, 켬 300초 · 끔 300초 · 켬 · 끔 · 켬 · 끔 · 켬(처음 켬은 예열 120초 포함).
"""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench import pair as P
from cellbench.config import DERIVE, PAIR
from cellbench.derive import derive_thresholds

GK = list(range(11594, 11618))
CTRL = list(range(11733, 11757))
OTHER = [12001, 12002]                      # 미등록 감지 셀 둘
PLAN = [True, False, True, False, True, False, True]
STEP_S, RUN_S = 10.0, 300.0
T0 = 1_800_000_000.0


def rows_for(gk_follow=True, ctrl_follow=False, dead_gk=(), ctrl_flip_at=None, noise=0.25, seed=7, plan=PLAN, gk=GK, ctrl=CTRL,
             skip_ctrl=(), other_follow=False):
    """합성 보정 기록 — 셀 잔량(실수)을 플러그 상태로 적분하고 노이즈를 얹어 정수 %로 적는다(실제 라이브 값처럼).
    gk_follow  세트 k 셀이 플러그를 따른다(켬 +0.4 · 끔 -0.34 %/분). dead_gk 는 따르지 않는 셀(다른 Dock 에 꽂힘).
    ctrl_follow  대조군이 플러그를 따른다(틀린 짝). ctrl_flip_at  그 시각(초)에 대조군 방향이 뒤집힌다(세트 1 의 단계 전환)."""
    rnd = random.Random(seed)
    level = {s: 62.0 + (s % 5) * 0.4 for s in gk}
    level.update({s: 55.0 + (s % 5) * 0.7 for s in ctrl})
    level.update({s: 40.0 for s in OTHER})
    out, t, ctrl_rate = [], T0, -0.34
    for on in plan:
        for _ in range(int(RUN_S / STEP_S)):
            el = t - T0
            for s in gk:
                follow = gk_follow and s not in dead_gk
                level[s] += (0.4 if on else -0.34) * STEP_S / 60 if follow else -0.34 * STEP_S / 60
            for s in ctrl:
                if ctrl_flip_at is not None and el >= ctrl_flip_at:
                    r = 0.4
                else:
                    r = (0.4 if on else -0.34) if ctrl_follow else -0.34
                level[s] += r * STEP_S / 60
            for s in OTHER:
                level[s] += ((0.4 if on else -0.34) if other_follow else -0.34) * STEP_S / 60
            row = {s: int(round(level[s] + rnd.uniform(-noise, noise))) for s in list(gk) + list(ctrl) + OTHER if s not in skip_ctrl}
            out.append((t, on, row))
            t += STEP_S
    return out


def derive(rows, gk=GK, ctrl=CTRL):
    return derive_thresholds(rows, gk, {"세트 1": ctrl, "미등록": OTHER}, PAIR, DERIVE)


def test_correct_pair_derives_conservative_thresholds():
    res = derive(rows_for())
    assert res["verdict"] == "derived" and res["code"] == "ok" and not res["fault"], res["why"]
    th, ev = res["thresholds"], res["evidence"]
    # 응답 하한(r ≈ 0.74 %/분)의 절반 근처 — 더 엄격한 쪽(응답의 50 % 와 대조군 변동의 3배 중 큰 쪽)이고 절대 하한 위
    assert ev["resp_floor"] > 0.5 and abs(th["quick_r_cell"] - max(0.5 * ev["resp_floor"], 3 * ev["noise_cell"], DERIVE["min_r_cell"])) < 0.002
    assert th["quick_r_cell"] >= DERIVE["min_r_cell"] and th["quick_r_median"] >= max(th["quick_r_cell"], DERIVE["min_r_median"])
    assert th["quick_other_max"] >= DERIVE["min_other_max"] and th["quick_other_max"] <= 0.5 * ev["resp_group"]
    assert th["follow_delta"] >= DERIVE["min_delta"] and th["quick_cells_min"] == math.ceil(0.9 * 24) == 22
    assert th["quick_r_cell"] < ev["resp_floor"] and th["quick_r_median"] < ev["resp_group"]      # 맞는 짝 자신은 넉넉히 통과한다
    assert ev["control_cells"] == 24 and ev["transitions"] >= 5


def test_stricter_of_the_two_rules_wins(monkeypatch):
    """대조군 변동의 배수가 응답 비율보다 크면 그쪽(더 엄격한 쪽)이 문턱이다."""
    rule = {**DERIVE, "resp_ratio": 0.05}                                   # 비율쪽을 일부러 낮춘다 — 변동 배수가 이겨야 한다
    res = derive_thresholds(rows_for(noise=0.45), GK, {"세트 1": CTRL, "미등록": OTHER}, PAIR, rule)
    ev = res["evidence"]
    assert res["verdict"] == "derived"
    assert res["thresholds"]["quick_r_cell"] >= round(3 * ev["noise_cell"], 3) - 0.002
    assert res["thresholds"]["quick_r_cell"] > 0.05 * ev["resp_floor"]


def test_thresholds_pass_correct_pair_and_reject_wrong_pairs_in_real_judges():
    """도출한 문턱을 실제 판정 함수에 넣는다 — 같은 종류의 새 기록(다른 노이즈)에서 맞는 짝은 통과, 틀린 짝은 실패."""
    th = derive(rows_for(seed=1))["thresholds"]
    rule = {**PAIR, **th, "calibrated": True}

    def judge(rows, gk=GK, groups=None):
        groups = groups or {"세트 1": CTRL, "미등록": OTHER}
        samples = [(t, b) for t, _, b in rows]
        # 빠른 검증 창 = 첫 켬 구간의 끝 180초 · 끔 구간 / 패턴 = 켬 끔 켬 끔 켬 (뒤 다섯 구간)
        on0 = T0 + 120
        q = P.quick_judge(samples, (on0, T0 + 300), (T0 + 300, T0 + 600), gk, groups, rule)
        segs = [(T0 + 600 + 300 * i, T0 + 900 + 300 * i, i % 2 == 0) for i in range(5)]
        pt = P.pattern_judge(samples, segs, gk, rule)
        return q["verdict"], pt["verdict"]
    assert judge(rows_for(seed=2)) == ("pass", "pass")
    # 틀린 짝 ① 세트 k 셀이 아무것도 안 따름 ② 세트 1 이 플러그를 따름 ③ 일부 셀이 안 따름 ④ 다른 무리의 셀이 따름
    assert judge(rows_for(seed=3, gk_follow=False))[0] == "not_feeding"
    assert judge(rows_for(seed=4, gk_follow=False, ctrl_follow=True))[0] == "feeds_other"
    assert judge(rows_for(seed=5, dead_gk=GK[:6]))[1] == "fail"
    assert judge(rows_for(seed=6, other_follow=True))[1] == "fail"


# ---------- 애매하면 도출하지 않는다 ----------

def test_wrong_pair_control_follows_plug_is_a_fault():
    """세트 1 플러그를 끈 것처럼 대조군이 같이 내려간다 — 도출하지 않고, 짝이 틀렸다는 증거(fault)로 올린다."""
    res = derive(rows_for(gk_follow=False, ctrl_follow=True))
    assert res["verdict"] == "inconclusive" and res["code"] == "foreign_follows" and res["fault"] and res["thresholds"] is None
    assert "세트 1" in res["why"]


def test_control_and_set_both_follow_is_still_a_fault():
    res = derive(rows_for(gk_follow=True, ctrl_follow=True))
    assert res["code"] == "foreign_follows" and res["fault"]


def test_foreign_unregistered_cells_following_is_a_fault():
    res = derive(rows_for(other_follow=True))
    assert res["code"] == "foreign_follows" and res["fault"] and "미등록" in res["why"]


def test_only_set_k_cells_respond_is_derived_even_with_noisy_control():
    res = derive(rows_for(noise=0.4, seed=11))
    assert res["verdict"] == "derived"


def test_some_cells_no_response_stops():
    res = derive(rows_for(dead_gk=GK[:3]))
    assert res["verdict"] == "inconclusive" and res["code"] == "cell_no_response" and not res["fault"]
    assert set(res["evidence"]["weak_cells"]) == set(GK[:3]) and res["thresholds"] is None


def test_one_dead_cell_is_enough_to_stop():
    res = derive(rows_for(dead_gk=GK[7:8]))
    assert res["code"] == "cell_no_response" and res["evidence"]["weak_cells"] == GK[7:8]


def test_noise_only_stops():
    res = derive(rows_for(gk_follow=False, noise=0.5, seed=21))
    assert res["verdict"] == "inconclusive" and res["code"] == "weak" and not res["fault"]


def test_control_instability_stops():
    """세트 1 이 기록 도중 단계가 바뀌었다(방전 → 충전) — 그 기록으로 기준을 정하지 않는다."""
    res = derive(rows_for(ctrl_flip_at=1500.0))
    assert res["verdict"] == "inconclusive" and res["code"] in ("control_unstable", "indistinct") and not res["fault"], res
    assert res["thresholds"] is None


def test_too_few_runs_or_samples_stops():
    short = rows_for(plan=[True, False, True])
    assert derive(short)["code"] == "samples"
    # 표본이 듬성듬성(구간마다 24개 미만)
    thin = [r for i, r in enumerate(rows_for()) if i % 2 == 0]
    assert derive(thin)["code"] in ("samples", "no_pattern")


def test_missing_set_cells_or_control_stops():
    gone = GK[:2]
    rows = [(t, on, {s: v for s, v in b.items() if s not in gone}) for t, on, b in rows_for()]
    assert derive(rows)["code"] == "cells_missing"
    few = [(t, on, {s: v for s, v in b.items() if s not in CTRL[:20]}) for t, on, b in rows_for()]
    assert derive(few)["code"] == "control_missing"


def test_pattern_section_missing_stops():
    """켬 끔 켬 끔 켬 다섯 구간이 없는 기록(끔으로 시작해 끔으로 끝나는 다섯 구간)."""
    res = derive(rows_for(plan=[False, True, False, True, False]))
    assert res["verdict"] == "inconclusive" and res["code"] == "no_pattern"


def test_gap_in_recording_splits_runs_but_keeps_derivation():
    """검증기가 세트 1 단계 전환을 기다린 틈(표본이 1분 넘게 빔)이 있어도 구간이 끊길 뿐 도출은 된다."""
    rows = rows_for()
    t_gap = T0 + 600
    shifted = [(t + (90 if t >= t_gap else 0), on, b) for t, on, b in rows]
    # 틈 앞뒤가 같은 켬 구간을 둘로 나눠도 나머지 전환으로 도출한다 (틈이 구간 사이에 있으면 구간 경계가 그대로 보존된다)
    assert derive(shifted)["verdict"] == "derived"


def test_derive_is_pure_and_deterministic():
    rows = rows_for()
    a, b = derive(rows), derive(list(rows))
    assert a == b


def test_derived_thresholds_never_looser_than_pair_defaults():
    """Minor 2 — quick_r_cell 은 PAIR 이상, quick_other_max 는 PAIR 이하, quick_r_median 은 quick_r_cell 이상."""
    res = derive(rows_for())
    assert res["verdict"] == "derived", res
    th = res["thresholds"]
    assert th["quick_r_cell"] >= PAIR["quick_r_cell"] and th["quick_other_max"] <= PAIR["quick_other_max"]
    assert th["quick_r_median"] >= th["quick_r_cell"]
