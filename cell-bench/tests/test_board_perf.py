"""성능 비교 화면 — serve_board.perf_runs 가 data/compare_*.csv 를 docs/perf-results.md 대로 읽는지, 결과판이 그 화면을
제어 PC 에서만 여는지 본다. 장비 · 브라우저 없이 돈다."""
import csv
import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import serve_board as sb
from cellbench.control import PinGuard

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "board" / "index.html").read_text(encoding="utf-8")
DOC = (ROOT / "docs" / "perf-results.md").read_text(encoding="utf-8")

# compare_motion_cell.py 가 쓰는 칸 순서 그대로(셀 줄 칸 → IMU 줄 칸). 결과판이 읽지 않는 칸도 섞어 둔다
COLS = ["kind", "cell", "packets", "fix", "sats", "sats_ios", "cn0", "validity", "hist_peak", "status", "lag_A_s", "r_A", "width_A_s",
        "edge_A", "lag_B_s", "drift_ppm", "hist_n", "hist_k", "hist_mean", "hist_rms", "hist_p95", "hist_max", "hspeed_rms",
        "file", "imu_from", "imu_to", "axis", "acc_k", "acc_rms", "acc_p95", "acc_max"]


def write(path: Path, rows: list[dict]):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, COLS)
        w.writeheader()
        w.writerows(rows)


def cell(serial, status, lag=None, rms=None, k=None):
    return {"kind": "cell", "cell": serial, "packets": 120, "fix": "A:120", "sats": 9.5, "cn0": 38.2, "status": status,
            "lag_A_s": lag, "r_A": 0.81 if lag is not None else None, "hist_k": k, "hist_rms": rms, "hist_p95": rms, "hist_max": rms,
            "validity": "0x01", "hspeed_rms": 3}


def test_runs_are_read_newest_first_with_time_from_name(tmp_path):
    write(tmp_path / "compare_motion_m2_20261027-104053.csv", [cell(11733, "정렬됨", 0.1, 5)])
    write(tmp_path / "compare_motion_m2_20261028-090000.csv", [cell(11733, "정렬됨", 0.2, 6)])
    (tmp_path / "motion_m2_20261027-104053.csv").write_text("t,pos\n", encoding="utf-8")   # 비교 결과가 아닌 파일은 읽지 않는다
    runs = sb.perf_runs(tmp_path)
    assert [r["name"] for r in runs] == ["compare_motion_m2_20261028-090000", "compare_motion_m2_20261027-104053"]
    assert time.strftime("%Y%m%d-%H%M%S", time.localtime(runs[1]["t"])) == "20261027-104053"


def test_summary_counts_status_and_medians_only_from_aligned_cells(tmp_path):
    write(tmp_path / "compare_motion_20261027-104053.csv", [
        cell(11733, "정렬됨", 0.10, 4.0, 1.01), cell(11734, "정렬됨", 0.30, 8.0, 0.99), cell(11735, "약함", 0.20, 6.0, 1.0),
        cell(11736, "fix 없음"), cell(11737, "정렬 불가", 9.9, 999.0),
        {"kind": "imu", "file": "imu_11733.csv", "axis": "acc_x", "status": "정렬됨", "lag_A_s": 12.5, "r_A": 0.7, "acc_k": 9.8,
         "acc_rms": 30.0, "acc_p95": 50, "acc_max": 70}])
    (tmp_path / "compare_motion_20261027-104053.md").write_text("# 보고\n", encoding="utf-8")
    run, = sb.perf_runs(tmp_path)
    s = run["summary"]
    assert s["cells"] == 5 and s["aligned"] == 3
    assert s["status"] == {"정렬됨": 2, "약함": 1, "fix 없음": 1, "정렬 불가": 1}
    assert s["lag_s"] == 0.20 and s["speed_rms"] == 6.0          # '정렬 불가'의 9.9 초 · 999 mm/s 는 중앙값에 들어가지 않는다
    assert s["imu"] == 1 and s["acc_rms"] == 30.0
    assert run["report"] == "compare_motion_20261027-104053.md"


def test_rows_carry_only_contract_columns_and_blank_means_none(tmp_path):
    write(tmp_path / "compare_motion_20261027-104053.csv", [cell(11736, "fix 없음")])
    run, = sb.perf_runs(tmp_path)
    r = run["cells"][0]
    assert set(r) == set(sb.PERF_CELL_COLS)                       # 결과판이 읽는 칸만 — 계약 밖 칸(hspeed_rms 등)은 넘기지 않는다
    assert r["cell"] == "11736" and r["status"] == "fix 없음" and r["packets"] == 120.0
    assert r["lag_A_s"] is None and r["hist_rms"] is None
    assert run["summary"]["lag_s"] is None and run["report"] is None


def test_contract_doc_names_every_column_the_board_reads():
    for col in sb.PERF_CELL_COLS + sb.PERF_IMU_COLS:
        assert f"`{col}`" in DOC or col in DOC, col


def test_api_perf_serves_the_runs(tmp_path):
    write(tmp_path / "compare_motion_20261027-104053.csv", [cell(11733, "정렬됨", 0.1, 5.0)])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), sb.make_handler(tmp_path, PinGuard(None), tmp_path / "bench.json"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/perf", timeout=5) as r:
            body = json.loads(r.read().decode("utf-8"))
    finally:
        srv.shutdown()
    assert body[0]["name"] == "compare_motion_20261027-104053" and body[0]["summary"]["aligned"] == 1


def test_board_opens_perf_only_on_the_control_pc():
    assert "...(CLOUD?[]:[['perf','성능 비교','monitor']])" in SRC             # 메뉴: 클라우드에는 없다
    assert "...(CLOUD?[]:['perf'])].includes(v)" in SRC                       # 주소 #perf 도 클라우드에서는 열지 않는다
    assert "(CLOUD&&U.view==='perf')" in SRC                                  # 저장된 화면이 perf 여도 클라우드는 관제로
    assert "perf:()=>getJ('/api/perf')" in SRC and "V.perf={nav:'perf'" in SRC


def test_board_shows_numbers_without_pass_fail():
    assert "합격선 없음 · 숫자만" in SRC
    assert "compare_motion_cell.py --motion" in SRC                           # 결과가 없을 때 무엇을 돌리면 되는지 알려 준다
