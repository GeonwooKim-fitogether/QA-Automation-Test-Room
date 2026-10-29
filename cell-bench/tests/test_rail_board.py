"""레일 시험 결과판 서버 쪽 — serve_board 의 /api/qc_now · /api/rail/runs · /api/rail/run · /rail-live 와 데모 생성기
(board/demo_qc_now.py)가 프로토콜 v4 약속(dynamic-bench/design/qc-protocol-v4.md 6-3 · 7절)대로 읽고 쓰는지 본다.
판정은 다시 계산하지 않고 verdict.판정 글자만 센다. 장비 · 브라우저 없이 돈다."""
import importlib.util
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import serve_board as sb
from cellbench.control import PinGuard

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures" / "rail"
V1, V4_SIM, V4_REP = "protocol_20261029-155408.json", "protocol_20261030-090000.json", "protocol_20261030-140000.json"

_spec = importlib.util.spec_from_file_location("demo_qc_now", ROOT / "board" / "demo_qc_now.py")
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)

# qc-protocol-v4.md 7-5 의 3 — qc_now.json 의 칸
TOP = {"t", "stamp", "conditions", "part", "step", "motor", "cells", "grid"}
STEP = {"key", "index", "started", "ends", "v_mm_s", "a_mm_s2"}
SCENE_NOW = {"v_kmh", "heading_deg", "lat", "lon"}
MOTOR = {"pos_mm", "v_mm_s", "ferr_max_um", "ferr_limit_um", "alarms", "cycle_ms_max", "state"}
CELL = {"age_s", "packets_10s", "fix", "cn0", "sats", "sog_raw", "sog_err", "pos_err_m", "moving", "clock_dev_ms", "battery", "fix_age_s"}
# 7-5 의 2 고정 이름 + 6-3 의 conditions 칸
COND = {"site", "sky", "repeater", "corrections", "set", "cell_type", "fw", "dock", "protocol", "part", "scene", "speeds_mm_s",
        "accels_mm_s2", "still_s", "dwell_s", "simulator", "rf", "judge_commit"}
SCENE = {"name", "mode", "file", "t0_gps", "origin"}


def fixtures(dst: Path, *names) -> Path:
    for n in names or (V1, V4_SIM, V4_REP):
        shutil.copy(FIX / n, dst / n)
    return dst


def serve(data: Path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), sb.make_handler(data, PinGuard(None), data / "bench.json"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def get(srv, path):
    """(상태, 본문 바이트, Content-Type) — 4xx 도 예외 대신 그대로 돌려준다."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}{path}", timeout=5) as r:
            return r.status, r.read(), r.headers.get("Content-Type")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type")


# ---- 목록 요약 ---------------------------------------------------------------------------------------------------------
def test_runs_list_newest_first_and_summarize_v1_and_v4(tmp_path):
    runs = sb.rail_runs(fixtures(tmp_path))
    assert [r["name"] for r in runs] == [V4_REP, V4_SIM, V1]
    rep, sim, v1 = runs

    # v1 — conditions 가 없다: 조건 칸은 모두 None(화면이 '이 시험에 없음'), protocol 도 None
    assert {k: v1[k] for k in ("protocol", "sky", "part", "set", "cell_type", "site", "scene", "judge_commit")} == dict.fromkeys(
        ("protocol", "sky", "part", "set", "cell_type", "site", "scene", "judge_commit"))
    assert v1["n_cells"] == 24 and v1["verdicts"] == {"합격": 23, "불합격": 1, "보류": 0, "무효": 0}
    assert v1["rig_valid"] is True and v1["sample"] is False and v1["stamp"] == "20261029-155408"
    assert time.strftime("%Y%m%d-%H%M%S", time.localtime(v1["t"])) == "20261029-155408"

    assert sim["protocol"] == "v4" and sim["sky"] == "simulator" and sim["part"] == ["A", "C"] and sim["site"] == "factory"
    assert sim["set"] == "Y" and sim["cell_type"] == "Y" and sim["scene"] == "sample-factory-5band" and sim["judge_commit"] == "sample01"
    assert sim["verdicts"] == {"합격": 20, "불합격": 2, "보류": 2, "무효": 0} and sim["rig_valid"] is True and sim["sample"] is True

    assert rep["sky"] == "repeater" and rep["part"] == "C" and rep["scene"] is None       # 장면 이름이 없는 시험
    assert rep["verdicts"] == {"합격": 0, "불합격": 0, "보류": 0, "무효": 24} and rep["rig_valid"] is False
    assert "error" not in rep and "error" not in v1


def test_counts_verdict_strings_without_judging_again(tmp_path):
    doc = {"conditions": {"protocol": "v9", "sky": "terrace"}, "rig": {"유효": True},
           "cells": {"1": {"clock": {"judge": "불합격"}, "verdict": {"판정": "합격"}},     # 항목이 불합격이어도 판정 글자 그대로 센다
                     "2": {"verdict": {"판정": "보류"}}, "3": {"verdict": {"판정": "재시험"}},   # 모르는 글자는 그 글자로
                     "4": {"verdict": {}}, "5": {"verdict": "합격"}, "6": "깨진 칸"}}         # 판정이 없으면 '없음'
    (tmp_path / "protocol_20261101-120000.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    run, = sb.rail_runs(tmp_path)
    assert run["verdicts"] == {"합격": 1, "불합격": 0, "보류": 1, "무효": 0, "재시험": 1, "없음": 3}
    assert run["n_cells"] == 6
    assert run["protocol"] == "v9" and run["sky"] == "terrace"      # 결과판이 모르는 판도 거부하지 않고 그대로 싣는다
    assert run["part"] is None and run["scene"] is None


def test_runs_read_only_protocol_files_and_flag_broken_ones(tmp_path):
    fixtures(tmp_path, V1)
    (tmp_path / "protocol_20261029-155408.csv").write_text("cell,verdict\n", encoding="utf-8")   # 같은 시험의 csv — 읽지 않는다
    (tmp_path / "protocol_latest.json").write_text("{}", encoding="utf-8")                      # 이름 규칙 밖 — 읽지 않는다
    (tmp_path / "qc_now.json").write_text("{}", encoding="utf-8")
    (tmp_path / "protocol_20261031-080000.json").write_text('{"cells": {', encoding="utf-8")    # 깨진 파일은 숨기지 않는다
    runs = sb.rail_runs(tmp_path)
    assert [r["name"] for r in runs] == ["protocol_20261031-080000.json", V1]
    assert runs[0]["error"] == "읽지 못함" and runs[0]["n_cells"] == 0


# ---- 이름 검사 ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", [V1, V4_SIM, "protocol_20000101-000000.json"])
def test_name_check_accepts_the_rule(name):
    assert sb.rail_name_ok(name)


@pytest.mark.parametrize("name", [
    "../protocol_20261029-155408.json", "..\\protocol_20261029-155408.json", "sub/protocol_20261029-155408.json",
    "protocol_20261029-155408.json\n", "protocol_20261029-155408.csv", "protocol_20261029-155408.json.bak",
    "protocol_2026102-155408.json", "protocol_20261029_155408.json", "PROTOCOL_20261029-155408.JSON",
    "C:\\Windows\\win.ini", "/etc/passwd", "bench.json", "now.json", "", None, 123])
def test_name_check_refuses_everything_else(name):
    assert not sb.rail_name_ok(name)


# ---- qc_now -----------------------------------------------------------------------------------------------------------
def test_qc_now_missing_is_idle(tmp_path):
    assert sb.qc_now(tmp_path) == {"step": {"key": "idle"}, "_file_age_s": None}


def test_qc_now_passes_the_file_and_adds_only_its_age(tmp_path):
    body = {"t": 1.0, "step": {"key": "A1", "index": 1}, "cells": {"10677": {"fix": "A"}}, "source": "demo"}
    p = tmp_path / "qc_now.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    got = sb.qc_now(tmp_path, now_t=p.stat().st_mtime + 3.0)
    assert got == {**body, "_file_age_s": 3.0}


def test_qc_now_unreadable_file_is_idle_with_a_reason(tmp_path):
    (tmp_path / "qc_now.json").write_text('{"step": ', encoding="utf-8")
    got = sb.qc_now(tmp_path)
    assert got["step"] == {"key": "idle"} and got["_error"] and got["_file_age_s"] is not None


# ---- HTTP -------------------------------------------------------------------------------------------------------------
def test_http_endpoints(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    fixtures(data)
    monkeypatch.setattr(sb, "RAIL_LIVE", tmp_path / "rail-live.html")          # 화면 파일은 다음 단계에 생긴다
    srv = serve(data)
    try:
        code, body, ctype = get(srv, "/api/qc_now")
        assert code == 200 and json.loads(body)["step"] == {"key": "idle"}

        code, body, _ = get(srv, "/api/rail/runs")
        assert code == 200 and [r["name"] for r in json.loads(body)] == [V4_REP, V4_SIM, V1]

        code, body, ctype = get(srv, f"/api/rail/run?name={V1}")
        assert code == 200 and "application/json" in ctype
        assert json.loads(body) == json.loads((FIX / V1).read_text(encoding="utf-8"))   # 파일 그대로

        for bad in ("..%2Fbench.json", "..%5C..%5Cserve_board.py", "protocol_20261029-155408.csv", "", "now.json"):
            code, body, _ = get(srv, f"/api/rail/run?name={bad}")
            assert code == 400 and json.loads(body)["ok"] is False, bad
        code, _, _ = get(srv, "/api/rail/run")
        assert code == 400
        code, _, _ = get(srv, "/api/rail/run?name=protocol_20991231-000000.json")
        assert code == 404

        code, body, ctype = get(srv, "/rail-live")
        assert code == 404 and "아직 없다" in body.decode("utf-8") and "charset=utf-8" in ctype
        (tmp_path / "rail-live.html").write_text("<!doctype html><title>실시간</title>", encoding="utf-8")
        code, body, ctype = get(srv, "/rail-live")
        assert code == 200 and ctype.startswith("text/html") and "실시간" in body.decode("utf-8")

        code, body, _ = get(srv, "/api/rail/runs?set=2")                       # ?set=k 는 기존 끝점과 같이 data/set2/ 를 본다
        assert code == 200 and json.loads(body) == []
        code, _, _ = get(srv, "/api/rail/runs?set=9")
        assert code == 400
    finally:
        srv.shutdown()


# ---- 데모 생성기 -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("part", ["A", "B", "C", "cycle"])
def test_demo_frame_carries_every_contract_field(part):
    d = demo.Demo(part, w0=1_000_000.0)
    fs = [d.frame(1_000_000.0 + t) for t in range(0, int(d.period), 7)]       # 한 회차를 7초 간격으로
    live = [f for f in fs if f["step"]["key"] != "idle"]
    assert live
    for f in live:
        assert f["source"] == "demo" and TOP <= set(f)
        assert COND <= set(f["conditions"]) and SCENE <= set(f["conditions"]["scene"])
        assert f["conditions"]["protocol"] == "v4" and f["conditions"]["part"] == f["part"]
        assert STEP <= set(f["step"]) and f["step"]["started"] <= f["t"] <= f["step"]["ends"]
        assert ("scene_now" in f["step"]) == (f["part"] == "A") and SCENE_NOW <= set(f["step"].get("scene_now", SCENE_NOW))
        assert set(f["motor"]) == MOTOR and f["motor"]["state"] in ("ok", "alarm", "stopped")
        assert len(f["cells"]) == 24 and all(set(c) == CELL for c in f["cells"].values())
        assert len(f["grid"]) == 6 and all(len(r) == 4 for r in f["grid"])
        assert sorted(str(s) for r in f["grid"] for s in r) == sorted(f["cells"])
        json.dumps(f, allow_nan=False)                                         # 브라우저 JSON.parse 가 못 읽는 NaN 이 없다
    assert {f["part"] for f in live} == ({"A", "B", "C"} if part == "cycle" else {part})


def test_demo_cycle_shows_every_screen_state():
    d = demo.Demo("cycle", w0=0.0)
    fs = [d.frame(float(t)) for t in range(int(d.period))]
    live = [f for f in fs if f["step"]["key"] != "idle"]
    assert {f["step"]["key"] for f in live} == {"A0", "A1", "A2", "A3", "A4", "A5", "accel", "speed"}
    assert any(f["step"]["key"] == "idle" for f in fs)                         # 회차 사이 '시험 없음'
    assert {f["step"]["a_mm_s2"] for f in live if f["part"] == "C"} == {20.0, 50.0, 100.0, 200.0, 500.0}
    assert {f["step"]["v_mm_s"] for f in live if f["part"] == "B"} == {100.0, 300.0, 500.0}
    assert {round(f["step"]["scene_now"]["v_kmh"]) for f in live if f["step"]["key"] == "A2"} >= {5, 11, 18, 23, 28}
    a0 = [f for f in live if f["step"]["key"] == "A0"]
    assert any(c["fix"] == "N" for c in a0[0]["cells"].values())               # 고정 대기 — 아직 못 잡은 셀이 있다
    assert any(f["cells"]["11620"]["fix"] == "N" for f in live if f["step"]["key"] == "A1")   # 고정 늦음
    drop = [f["cells"]["10696"] for f in live]
    assert max(c["age_s"] for c in drop) > 5 and min(c["packets_10s"] for c in drop) < 20     # 수신 끊김
    assert any(abs(f["cells"]["10681"]["sog_err"] or 0) > 200 for f in live if f["part"] == "A")   # 무리에서 벗어남
    assert any(f["motor"]["cycle_ms_max"] and f["motor"]["cycle_ms_max"] >= 30 for f in live)    # 주기 지연 경고
    c20 = [f for f in live if f["part"] == "C" and f["step"]["a_mm_s2"] == 20.0]
    assert any(sum(c["moving"] for c in f["cells"].values()) >= 20 for f in c20)               # 움직임 판단이 켜진다
    assert not any(f["cells"]["10696"]["moving"] for f in c20)                                 # 10696 은 낮은 가속도에서 안 켜짐
    assert all(f["motor"]["state"] == "stopped" for f in live if f["part"] == "A")


def test_demo_main_writes_one_frame_and_can_overwrite_its_own(tmp_path):
    out = tmp_path / "demo"
    assert demo.main(["--out", str(out), "--once", "--part", "C"]) == 0
    f = json.loads((out / "qc_now.json").read_text(encoding="utf-8"))
    assert f["source"] == "demo" and f["part"] == "C" and TOP <= set(f)
    assert demo.main(["--out", str(out), "--once"]) == 0                       # 데모가 쓴 파일은 다시 덮어쓴다
    assert not [p for p in out.iterdir() if p.name != "qc_now.json"]          # 임시 파일이 남지 않는다
    srv = serve(out)
    try:
        code, body, _ = get(srv, "/api/qc_now")
        got = json.loads(body)
        assert code == 200 and got["source"] == "demo" and isinstance(got["_file_age_s"], float)
    finally:
        srv.shutdown()


def test_demo_refuses_the_real_data_folder(tmp_path):
    real = ROOT / "data"
    before = (real / "qc_now.json").exists()
    assert demo.main(["--out", str(real), "--once"]) == 2
    assert (real / "qc_now.json").exists() == before
    for p in (tmp_path / "cell-bench" / "data", tmp_path / "Cell-Bench" / "data" / "set2",
              Path(r"C:\Dev\dynamic-bench\cell-bench\data")):
        assert demo.refuse(p), p
    assert demo.main(["--out", str(tmp_path / "cell-bench" / "data" / "set2"), "--once"]) == 2
    assert not (tmp_path / "cell-bench").exists()                               # 거부하면 폴더도 만들지 않는다


def test_demo_does_not_overwrite_a_file_it_did_not_write(tmp_path):
    real = {"t": time.time(), "step": {"key": "accel"}, "conditions": {"protocol": "v4"}}     # source 칸 없음 = 실제 쓰는 프로그램
    p = tmp_path / "qc_now.json"
    p.write_text(json.dumps(real), encoding="utf-8")
    assert demo.main(["--out", str(tmp_path), "--once"]) == 2
    assert json.loads(p.read_text(encoding="utf-8")) == real
    p.write_text("{깨진", encoding="utf-8")
    assert demo.main(["--out", str(tmp_path), "--once"]) == 2


def test_demo_does_not_touch_devices_or_network():
    src = (ROOT / "board" / "demo_qc_now.py").read_text(encoding="utf-8")
    for word in ("socket", "serial", "kasa", "urllib", "requests", "motionbench", "cellbench.cells", "cellbench.plug"):
        assert f"import {word}" not in src and f"from {word}" not in src, word
    assert os.path.basename(demo.__file__) == "demo_qc_now.py"
