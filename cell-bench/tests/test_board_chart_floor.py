"""세트 상세 그래프의 방전 바닥 — 묶음 간격 선택(서버), 방전 종료 표지(순수 함수), 그리고 그 배선이 그대로 있는지 못 박는다.

배경(2026-10-15 실측, 5일 19사이클): 방전이 시간당 16~19 %p 라 1시간 묶음 평균의 골짜기는 41~49 % 로 보였고 실제 바닥은 33 % 였다.
그래서 (1) '전체' 범위는 28일까지 20분 묶음, (2) 사이클 기록(discharge_end · min_batt_at_stop · 셀별 end_pct 중앙값)으로 실제 바닥을
그래프 위에 찍는다. 순수 함수(dischargeFloors)는 index.html 의 <chart-floor> 블록을 떼어 헤드리스 Chrome 으로 돌려 본다.
렌더(표지 6개 · 범례 · 호버 값)는 가짜 fetch 스크립트로 스크린샷을 찍어 확인했다 — 그 검사는 저장소에 브라우저 검사 도구가 없어 pytest 밖이다.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from serve_board import bucket_rows, pick_bucket, rows_span_s  # noqa: E402
from cellbench.control import PinGuard  # noqa: E402

SRC = (ROOT / "board" / "index.html").read_text(encoding="utf-8")
CHROMES = [shutil.which("chrome"), shutil.which("msedge"),
           r"C:\Program Files\Google\Chrome\Application\chrome.exe",
           r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
CHROME = next((c for c in CHROMES if c and Path(c).exists()), None)


def _row(t, a, mn, mx, cyc=1, phase="DISCHARGE"):
    return {"time": t, "phase": phase, "plug": "off", "watts": "0", "wh": "0", "cells": "24",
            "batt_min": str(mn), "batt_avg": str(a), "batt_max": str(mx), "hr_cells": "0", "cycle": cyc}


# ---- 서버: 묶음 간격 · 띠가 묶음 안의 최저 · 최고인지 ----
def test_pick_bucket_fine_until_28_days_then_hourly():
    assert pick_bucket("recent", 0) == 600
    assert pick_bucket("all", 0) == 1200
    assert pick_bucket("all", 28 * 86400) == 1200
    assert pick_bucket("all", 28 * 86400 + 1) == 3600


def test_rows_span_uses_first_and_last_time():
    rows = [_row("2026-10-10 10:00:00", 90, 88, 92), _row("bad", 1, 1, 1), _row("2026-10-12 10:00:00", 50, 40, 60)]
    assert rows_span_s(rows) == 2 * 86400
    assert rows_span_s([]) == 0 and rows_span_s([_row("bad", 1, 1, 1)]) == 0


def test_bucket_band_is_min_and_max_of_the_window_not_of_the_average():
    """한 묶음 안에서 잔량이 45 → 31 로 떨어지면: 평균은 올라가 보여도 띠 아래는 그 구간의 실제 최저(31)다."""
    rows = [_row("2026-10-10 13:00:10", 45.0, 43, 47), _row("2026-10-10 13:05:10", 38.0, 36, 40), _row("2026-10-10 13:10:10", 32.5, 31, 34)]
    out = bucket_rows(rows, 1200)
    assert len(out) == 1
    assert out[0]["batt_min"] == 31 and out[0]["batt_max"] == 47 and out[0]["batt_avg"] == "38.5"


def test_samples_all_response_carries_bucket_s(tmp_path):
    """/api/samples?range=all 이 bucket_s 를 함께 준다 — 화면이 범례 · 다시 묶기의 기준으로 쓴다."""
    import threading
    from http.server import ThreadingHTTPServer
    from serve_board import make_handler
    (tmp_path / "samples_0001.csv").write_text(
        "time,phase,plug,watts,wh,cells,batt_min,batt_avg,batt_max,hr_cells\n"
        "2026-10-10 13:00:10,DISCHARGE,off,0,0,24,43,45.0,47,0\n2026-10-10 13:30:10,DISCHARGE,off,0,0,24,31,32.5,34,0\n", encoding="utf-8")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path, PinGuard(None), tmp_path / "bench.json", devices=object()))
    th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
    try:
        import urllib.request
        j = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/samples?range=all").read())
        assert j["bucket_s"] == 1200 and len(j["rows"]) == 2 and j["rows"][1]["batt_min"] == 31
        j = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/samples?range=recent").read())
        assert j["bucket_s"] == 600
    finally:
        srv.shutdown(); srv.server_close()


# ---- 화면: 방전 종료 표지 (순수 함수) ----
def block() -> str:
    m = re.search(r"// <chart-floor>.*?// </chart-floor>", SRC, re.S)
    assert m, "<chart-floor> 블록이 없다"
    helpers = [re.search(r"^const parseTs=.*$", SRC, re.M).group(0), re.search(r"^const has=.*$", SRC, re.M).group(0)]
    return "\n".join(helpers) + "\n" + m.group(0)


def run_js(cases: dict) -> dict:
    page = ("<pre id=o></pre><script>" + block() + "\nconst r={};" +
            "".join(f"r[{json.dumps(k)}]=({v});" for k, v in cases.items()) +
            "document.getElementById('o').textContent='@@'+JSON.stringify(r)+'@@';</script>")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.html"
        f.write_text(page, encoding="utf-8")
        out = subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox", f"--user-data-dir={d}/p",
                              "--dump-dom", f.as_uri()], capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
    return json.loads(re.search(r"@@(.*?)@@", out, re.S).group(1))


@pytest.mark.skipif(CHROME is None, reason="Chrome/Edge 없음")
def test_discharge_floors_pure_logic():
    cycles = ("[{cycle:'2',discharge_end:'2026-10-10 14:02:37',min_batt_at_stop:'30'},"
              " {cycle:'1',discharge_end:'2026-10-10 10:02:37',min_batt_at_stop:''},"          # 최저 칸이 비면 셀 기록의 최저로
              " {cycle:'3',discharge_end:'',min_batt_at_stop:'31'},"                            # 종료 시각 없음 → 표지 없음
              " {cycle:'4',discharge_end:'2026-10-10 20:00:00',min_batt_at_stop:''}]")          # 아무 기록도 없음 → 표지 없음
    dis = ("[{cycle:'2',end_pct:'34'},{cycle:'2',end_pct:'30'},{cycle:'2',end_pct:'33'},{cycle:'2',end_pct:'36'},"   # 짝수 개 → 가운데 둘 평균 33.5
           " {cycle:'1',end_pct:'32'},{cycle:'1',end_pct:'35'},{cycle:'1',end_pct:''}]")
    r = run_js({
        "f": f"dischargeFloors({cycles},{dis})",
        "none": "dischargeFloors(null,null)",
        "nodis": "dischargeFloors([{cycle:'7',discharge_end:'2026-10-10 14:02:37',min_batt_at_stop:'29'}],[])",
        "med_odd": "medianOf(['3','1','2'])", "med_even": "medianOf([1,4,2,3])", "med_none": "medianOf(['',null])",
    })
    f = r["f"]
    assert [x["cycle"] for x in f] == [1, 2]                                    # 시각순 · 3 과 4 는 빠진다
    assert f[1]["min"] == 30 and f[1]["med"] == 33.5
    assert f[0]["min"] == 32 and f[0]["med"] == 33.5                            # min_batt_at_stop 이 비면 end_pct 최저
    assert f[1]["t"] - f[0]["t"] == 4 * 3600
    assert r["none"] == [] and r["nodis"] == [{"cycle": 7, "t": f[1]["t"], "min": 29, "med": None}]
    assert r["med_odd"] == 2 and r["med_even"] == 2.5 and r["med_none"] is None


def test_chart_floor_wiring():
    assert "bucketS:(smp&&+smp.bucket_s)||null" in SRC                            # 서버가 준 간격을 쓴다
    assert "const baseBucket=()=>S.bucketS||BUCKET_S[R]||0;" in SRC
    assert "const floors=bucket?dischargeFloors(S.cycles,S.dis).filter(f=>f.t>=tA&&f.t<=tB):[];" in SRC   # 여러 사이클 범위에서만
    assert "`잔량 · ${grpLab} 평균`" in SRC and "`${grpLab} 안`" in SRC            # 범례가 '묶음 평균'임을 밝힌다
    assert '<i class="flo"></i>방전 종료 최저 · 중앙값' in SRC and ".lgd i.flo{" in SRC
    assert "G.floors.filter(q=>G.near(q.t)===i)" in SRC                           # 호버 · 고정 값 읽기에 표지 값
    assert "rpc/bench_sample_bucket?bucket_s=1200" in SRC and "bucket_s:fine?1200:3600" in SRC   # 클라우드도 같은 뜻
    assert "BUCKET[R]" not in SRC


def test_deploy_copy_matches_board():
    dep = ROOT / "deploy" / "board" / "index.html"
    assert dep.read_text(encoding="utf-8") == SRC, "python tools/build_cloud_board.py 로 배포 사본을 다시 만든다"
