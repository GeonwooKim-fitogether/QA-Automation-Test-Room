"""GNSS-이동 합격선(2026-11-17 사용자 결정) — 운전마다 중앙 오차 ±50 mm/s, 신호 세기는 기록만. 장비 없이 돈다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tools"):
    sys.path.insert(0, str(p))
import protocol_v4 as P  # noqa: E402
from motionbench import qc_v4 as V  # noqa: E402

BASE = {"version": "1차", "tests": {"GNSS-MOVE": {"items": {
    "cn0_sim": {"lo": 43.5, "hi": None, "kind": "합불"},
    "gps_move": {"lo": 0.958, "hi": 1.018, "kind": "합불", "key": {"v_ratio": "300"}}}}}}


def test_pass_line_comes_from_qc_tests_json():
    q = json.loads(P.QC_TESTS_FILE.read_text(encoding="utf-8"))["tests"]["GNSS-MOVE"]
    assert q["err_center_max_mm_s"] == 50.0
    c = P.gnss_move_crit(BASE)
    it = c["tests"]["GNSS-MOVE"]["items"]
    assert it["gps_err"]["kind"] == "합불" and (it["gps_err"]["lo"], it["gps_err"]["hi"]) == (-50.0, 50.0)
    assert it["cn0_sim"]["kind"] == "살펴봄" and it["gps_move"]["kind"] == "살펴봄"     # 신호 세기 · 속도 비는 기록만
    assert BASE["tests"]["GNSS-MOVE"]["items"]["cn0_sim"]["kind"] == "합불"            # 1차 기준 파일 자체는 바꾸지 않는다


def test_none_criteria_stays_none():
    assert P.gnss_move_crit(None) is None


def test_judge_uses_mm_s_line_not_signal_strength():
    """오차 +49 · −51 mm/s 와 신호 세기 40 dB-Hz(옛 합불선 43.5 아래)인 셀 — 오차만으로 갈린다."""
    it = P.gnss_move_crit(BASE)["tests"]["GNSS-MOVE"]["items"]
    ci = P._crit_items({"tests": {"GNSS-MOVE": {"items": it}}}, "GNSS-MOVE", {"gps_move": {"v_ratio": "300"}})
    kinds = {i: c["kind"] for i, c in ci.items()}
    g = V.pass_range({"a": 49.0, "b": -51.0, "c": None}, "gps_err", -50.0, 50.0)
    assert [g[s]["verdict"] for s in "abc"] == ["pass", "fail", "hold"]
    items = {"gps_err": g["a"], "cn0_sim": V.item(40.0, "dBHz", "flag")}
    assert V.overall(items, kinds)["overall"] == "pass"


def test_still45_scene_table_and_still_seconds(monkeypatch):
    """정지 45초 확인 시험 — S12 는 1.0 m/s 만, 실행기는 장면 정답 파일에서 정지 초를 읽는다(S09 20초 · S12 45초)."""
    import qc_round as Q
    import run_rail as R
    assert sorted(Q.CONST_SCENES_S45) == [100, 300, 500, 1000, 1400]
    scene, truth, preset = Q.CONST_SCENES_S45[1000]
    assert scene == "S12_Move1000s45" and preset == "RUN-CONST_v1000a8000c7" and Q.scene_truth(scene) == truth
    rows = "t_s,pos_mm,v_mm_s\n" + "".join(f"{t / 10:.1f},150,0\n" for t in range(451)) + "45.1,150.1,8\n45.2,151,16\n"
    fake = Path(__file__).with_name("_still_tmp.csv")
    fake.write_text(rows, encoding="utf-8")
    try:
        monkeypatch.setattr(Q, "scene_truth", lambda s: fake)
        assert R.scene_still_s("S12_Move1000s45") == 45.0
        monkeypatch.setattr(Q, "scene_truth", lambda s: None)
        assert R.scene_still_s("없는장면") == R.STILL_S
    finally:
        fake.unlink()
