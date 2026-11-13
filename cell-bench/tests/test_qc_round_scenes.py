"""GNSS-MOVE 장면 표(qc_round.CONST_SCENES)와 하루 수집 판정(judge_all)의 정답 고르기 — 장비 없이 돈다.
2026-11-13: 위성-12 장면 S06 ~ S11 이 정본이 되고, 위성-6 장면(S03 · S04 · S05)은 그 기간 기록을 다시 판정할 때만 쓴다."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qc_round as Q  # noqa: E402

PRESETS = Path(__file__).resolve().parents[2] / "dynamic-bench" / "dev" / "presets"


def test_const_scenes_are_sat12_with_approved_presets():
    assert Q.CONST_SPEEDS == [100, 300, 500, 1000, 1400, 2000]
    for v, (scene, truth, preset) in Q.CONST_SCENES.items():
        assert scene.endswith(f"_Move{v}") and truth.name == f"{scene}_truth.csv" and preset == f"RUN-CONST_v{v}a8000"
        a = json.loads((PRESETS / f"{preset}.json").read_text(encoding="utf-8"))      # 승인 프리셋이 저장소에 있다
        assert a["vmax_mm_s"] == v and a["amax_mm_s2"] == 8000 and a["speeds_mm_s"] == [v]
    span = {v: json.loads((PRESETS / f"RUN-CONST_v{v}a8000.json").read_text(encoding="utf-8")) for v in Q.CONST_SPEEDS}
    assert {v: s["b_from_left_mm"] - s["a_from_left_mm"] for v, s in span.items()} == {**dict.fromkeys(Q.CONST_SPEEDS, 1500), 2000: 1200}


def test_scene_truth_knows_old_and_new_scenes():
    assert Q.scene_truth("S11_Move2000").name == "S11_Move2000_truth.csv"
    assert Q.scene_truth("S03_RailB120").name == "rail_B_120s_truth.csv"      # 위성-6 기록 다시 판정
    assert Q.scene_truth("없는장면") is None


def test_judge_all_picks_truth_by_scene_in_runs_json(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(Q, "judge", lambda argv, say: calls.append(argv) or 0)
    for name, scene in (("RUN-CONST_v300a8000", "S07_Move300"), ("RUN-CONST_v300", "S03_RailB120"), ("RUN-CONST_v100", None)):
        (tmp_path / name).mkdir()
        runs = [{"i": 1, "motor_csv": "x.csv"} | ({"scene": scene} if scene else {})]
        (tmp_path / name / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
    rcs = Q.judge_all(tmp_path, "Y-shift", [], [100, 300], None, print)
    got = {Path(a[a.index("--runs") + 1]).parent.name: (a[a.index("--scene") + 1], Path(a[a.index("--truth") + 1]).name) for a in calls}
    assert rcs == [0, 0, 0]
    assert got == {"RUN-CONST_v300a8000": ("S07_Move300", "S07_Move300_truth.csv"),     # 위성-12 회차
                   "RUN-CONST_v300": ("S03_RailB120", "rail_B_120s_truth.csv"),         # 위성-6 회차
                   "RUN-CONST_v100": ("S04_RailB100", "S04_RailB100_truth.csv")}        # 장면 이름이 없는 옛 기록은 속도로
