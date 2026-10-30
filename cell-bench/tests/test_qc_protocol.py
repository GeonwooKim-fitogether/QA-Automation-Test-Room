"""셀 시험 프로토콜 v1 판정(motionbench/qc_protocol.py) — 가짜 셀 값으로 판정 규칙을 확인한다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motionbench import qc_protocol as P


def test_robust_z_flags_the_outlier_only():
    vals = {s: 35.0 for s in range(20)} | {99: 15.0}
    z = P.robust_z(vals, floor=1.0)
    assert abs(z[99]) >= 3 and all(abs(z[s]) < 3 for s in range(20))


def test_robust_z_keeps_none():
    assert P.robust_z({1: None, 2: 3.0})[1] is None


def test_loss_pct_counts_gaps_and_survives_reboot():
    assert P.loss_pct([1, 2, 3, 5]) == 20.0                 # 1~5 중 4 하나 빠짐
    assert P.loss_pct([10, 11, 12, 1, 2, 3]) == 0.0         # 재부팅으로 번호가 되돌아가도 빠진 것 없음
    assert P.loss_pct([]) is None


def test_static_metrics_scatter_only_from_fixed():
    rows = [("R", 40, 25, 15, float(i % 3), 0.0) for i in range(40)] + [("A", 38, 20, 30, 5000.0, 0.0)] * 10
    m = P.static_metrics(rows)
    assert m["r_share"] == 80.0 and m["r_p95_mm"] <= 1.0    # 단독 점(5 m)은 흔들림에 넣지 않는다


def _cell(loss=0.1, clock=300.0, imu=None, cn0=40, sats=25, r=95.0, still=15):
    return {"loss_pct": loss, "clock_ms": clock, "imu_ratio": imu,
            "static": {"cn0": cn0, "sats": sats, "r_share": r, "still_speed": still}}


def test_judge_pass_fail_and_gps_flag():
    cells = {s: _cell() for s in range(20)}
    cells[100] = _cell(loss=2.0)                            # 수신 불합격
    cells[101] = _cell(clock=330.0)                         # 무리에서 30 ms — 시각 불합격
    cells[102] = _cell(imu=0.8)                             # IMU 20 % 작음 — 불합격
    cells[103] = _cell(cn0=25)                              # GPS 만 벗어남 — 합격이지만 살펴볼 셀
    v = P.judge(cells)
    assert v[0]["판정"] == "합격" and v[0]["GPS"] == "정상"
    assert v[100]["수신"] is False and v[100]["판정"] == "불합격"
    assert v[101]["시각"] is False and v[101]["판정"] == "불합격"
    assert v[102]["IMU"] is False and v[102]["판정"] == "불합격"
    assert v[103]["판정"] == "합격" and v[103]["GPS"] == "살펴볼 셀" and "cn0" in v[103]["GPS_z"]


def test_judge_holds_when_data_missing():
    assert P.judge({1: {"loss_pct": None, "clock_ms": None}})[1]["판정"] == "보류"


def test_rig_verdict():
    ok = {"exit": "오래 왕복 다 됨", "alarms": [], "legs": [{"err_um": 3}, {"err_um": -4}],
          "long": {"steps": [{"v_mm_s": 200, "ferr_max": 2987, "ferr_limit_um": 4000}]}, "pdo": {"cycle_ms": {"max": 8.1}}}
    assert P.rig_verdict(ok)["유효"] is True
    r = P.rig_verdict({**ok, "exit": "Quick stop", "pdo": {"cycle_ms": {"max": 55.3}}})
    assert r["유효"] is False and len(r["이유"]) == 2


def test_layout_grid_column_major_and_fills_rest():
    g = P.layout_grid([[1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 12]], list(range(1, 15)))
    assert g[0][0] == 1 and g[5][0] == 6 and g[0][1] == 7      # 열마다 위에서부터
    assert g[0][2] == 13 and g[1][2] == 14 and g[2][2] is None  # 배치에 없는 셀은 빈 칸에 번호 순
