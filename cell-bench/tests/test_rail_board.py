"""레일 시험 결과판 서버 쪽 — serve_board 의 /api/qc_now · /api/rail/runs · /api/rail/run · /rail-live 와 데모 생성기
(board/demo_qc_now.py), 표본(tests/fixtures/rail/)이 프로토콜 v4 약속(dynamic-bench/design/qc-protocol-v4.md 6-3 · 7절,
칸 이름은 7-6)대로 읽고 쓰는지 본다. 판정은 다시 계산하지 않고 셀 종합 판정 글자만 센다 — v4 는 verdict.overall,
v1 은 verdict.판정. 장비 · 브라우저 없이 돈다."""
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
V1 = "protocol_20261029-155408.json"         # 실제 v1 시험(한글 키 verdict.판정)
SIM = "protocol_20261029-172000.json"        # v4 시뮬레이터 A · C, sog_band 등급 경계 4개
TER = "protocol_20261029-184000.json"        # v4 테라스 C · B, gps_move
VOID = "protocol_20261029-193000.json"       # v4 장비 무효 — 24대 모두 void
V5 = "protocol_20261029-210000.json"         # 결과판이 모르는 판
FAC = "protocol_20261030-090000.json"        # v4 공장 A · C, sog_band 등급 경계 없음, gate_* 갈래 보류, FTG 못 받음
REP = "protocol_20261030-140000.json"        # v4 중계기 C, gps_rx 정보용
ALL = (V1, SIM, TER, VOID, V5, FAC, REP)
V4 = (SIM, TER, VOID, FAC, REP)


def load(path):
    spec = importlib.util.spec_from_file_location(Path(path).stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


demo = load(ROOT / "board" / "demo_qc_now.py")
make = load(FIX / "make_samples.py")

# qc-protocol-v4.md 7-5 의 3 · 7-6 — qc_now.json 의 칸
TOP = {"t", "stamp", "conditions", "part", "step", "motor", "cells", "grid"}
STEP = {"key", "index", "started", "ends", "v_mm_s", "a_mm_s2"}
STEP_A = {"scene_now", "band_kmh", "scene_step"}                              # 7-6 의 2 — 파트 A 에만
SCENE_NOW = {"v_kmh", "heading_deg", "lat", "lon"}
MOTOR = {"pos_mm", "v_mm_s", "ferr_max_um", "ferr_limit_um", "alarms", "cycle_ms_max", "state"}
CELL = {"age_s", "packets_10s", "fix", "cn0", "sats", "sog_raw", "sog_err", "pos_err_m", "moving", "clock_dev_ms", "battery", "fix_age_s"}
# 7-5 의 2 고정 이름 + 6-3 의 conditions 칸
COND = {"site", "sky", "repeater", "corrections", "set", "cell_type", "fw", "dock", "protocol", "part", "scene", "speeds_mm_s",
        "accels_mm_s2", "still_s", "dwell_s", "simulator", "rf", "judge_commit"}
SCENE = {"name", "mode", "file", "t0_gps", "origin"}
# 7-6 의 5 · 7 — 셀 항목 · 종합 · 기준 갈래
ITEM = {"value", "unit", "verdict", "z", "basis"}
KIND_VERDICTS = {"합불": {"pass", "fail", "hold"}, "살펴봄": {"pass", "flag", "hold"}, "정보용": {"info"}, "보류": {"hold"}}
STATUS = {"제안값", "미정", "확정"}


def fixtures(dst: Path, *names) -> Path:
    for n in names or ALL:
        shutil.copy(FIX / n, dst / n)
    return dst


def fx(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


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


def counts(p=0, f=0, h=0, v=0, **more):
    return {"합격": p, "불합격": f, "보류": h, "무효": v, **more}


# ---- 목록 요약 ---------------------------------------------------------------------------------------------------------
def test_runs_list_newest_first_and_summarize_v1_and_v4(tmp_path):
    runs = {r["name"]: r for r in sb.rail_runs(fixtures(tmp_path))}
    assert list(runs) == [REP, FAC, V5, VOID, TER, SIM, V1]
    assert not any("error" in r for r in runs.values())

    v1 = runs[V1]     # v1 — conditions 가 없다: 조건 칸은 모두 None(화면이 '이 시험에 없음'), protocol 도 None
    assert {k: v1[k] for k in ("protocol", "sky", "part", "set", "cell_type", "site", "scene", "judge_commit")} == dict.fromkeys(
        ("protocol", "sky", "part", "set", "cell_type", "site", "scene", "judge_commit"))
    assert v1["n_cells"] == 24 and v1["verdicts"] == counts(23, 1)
    assert v1["rig_valid"] is True and v1["sample"] is False and v1["stamp"] == "20261029-155408"
    assert time.strftime("%Y%m%d-%H%M%S", time.localtime(v1["t"])) == "20261029-155408"

    sim = runs[SIM]   # v4 — verdict.overall(pass · fail · hold · void)을 화면 글자로 센다
    assert sim["protocol"] == "v4" and sim["sky"] == "simulator" and sim["part"] == ["A", "C"] and sim["site"] == "lab"
    assert sim["set"] == "Y" and sim["cell_type"] == "CLBY" and sim["scene"] == "stadium-01" and sim["judge_commit"] == "a1f3c9e"
    assert sim["verdicts"] == counts(20, 2, 2) and sim["rig_valid"] is True and sim["sample"] is True and sim["n_cells"] == 24

    assert runs[TER]["sky"] == "terrace" and runs[TER]["verdicts"] == counts(22, 2)
    assert runs[VOID]["verdicts"] == counts(v=24) and runs[VOID]["rig_valid"] is False
    assert runs[FAC]["site"] == "factory" and runs[FAC]["scene"] == "factory-5band" and runs[FAC]["verdicts"] == counts(20, 2, 2)
    assert runs[REP]["sky"] == "repeater" and runs[REP]["part"] == "C" and runs[REP]["scene"] is None   # 장면 이름이 없는 시험
    assert runs[REP]["verdicts"] == counts(23, 1)

    v5 = runs[V5]     # 모르는 판 — 거부하지 않고 protocol 을 그대로 싣고, 판정은 v1 방식(verdict.판정)으로만 센다
    assert v5["protocol"] == "v5" and v5["sample"] is True and v5["verdicts"] == counts(없음=24)
    assert all(runs[n]["sample"] is True for n in ALL if n != V1)


def test_counts_verdict_strings_without_judging_again(tmp_path):
    v1ish = {"conditions": {"protocol": "v9", "sky": "terrace"}, "rig": {"유효": True},
             "cells": {"1": {"clock": {"judge": "불합격"}, "verdict": {"판정": "합격"}},     # 항목이 불합격이어도 판정 글자 그대로 센다
                       "2": {"verdict": {"판정": "보류"}}, "3": {"verdict": {"판정": "재시험"}},   # 모르는 글자는 그 글자로
                       "4": {"verdict": {}}, "5": {"verdict": "합격"}, "6": "깨진 칸",         # 판정이 없으면 '없음'
                       "7": {"verdict": {"overall": "pass"}}}}                               # v4 가 아니면 overall 은 보지 않는다
    v4 = {"conditions": {"protocol": "v4"},
          "cells": {"1": {"items": {"rx": {"verdict": "fail"}}, "verdict": {"overall": "pass"}},   # 항목은 보지 않는다
                    "2": {"verdict": {"overall": "fail"}}, "3": {"verdict": {"overall": "hold"}}, "4": {"verdict": {"overall": "void"}},
                    "5": {"verdict": {"overall": "retest"}},                                  # 모르는 값은 그 값으로
                    "6": {"verdict": {"판정": "합격"}}, "7": {"verdict": {"overall": ""}}, "8": "깨진 칸"}}   # overall 이 없으면 '없음'
    (tmp_path / "protocol_20261101-120000.json").write_text(json.dumps(v1ish, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "protocol_20261101-130000.json").write_text(json.dumps(v4, ensure_ascii=False), encoding="utf-8")
    r4, r9 = sb.rail_runs(tmp_path)
    assert r9["verdicts"] == counts(1, 0, 1, 재시험=1, 없음=4) and r9["n_cells"] == 7
    assert r9["protocol"] == "v9" and r9["sky"] == "terrace"      # 결과판이 모르는 판도 거부하지 않고 그대로 싣는다
    assert r9["part"] is None and r9["scene"] is None
    assert r4["verdicts"] == counts(1, 1, 1, 1, retest=1, 없음=3) and r4["n_cells"] == 8


def test_runs_read_only_protocol_files_and_flag_broken_ones(tmp_path):
    fixtures(tmp_path, V1)
    (tmp_path / "protocol_20261029-155408.csv").write_text("cell,verdict\n", encoding="utf-8")   # 같은 시험의 csv — 읽지 않는다
    (tmp_path / "protocol_latest.json").write_text("{}", encoding="utf-8")                      # 이름 규칙 밖 — 읽지 않는다
    (tmp_path / "qc_now.json").write_text("{}", encoding="utf-8")
    (tmp_path / "protocol_20261031-080000.json").write_text('{"cells": {', encoding="utf-8")    # 깨진 파일은 숨기지 않는다
    runs = sb.rail_runs(tmp_path)
    assert [r["name"] for r in runs] == ["protocol_20261031-080000.json", V1]
    assert runs[0]["error"] == "읽지 못함" and runs[0]["n_cells"] == 0


# ---- 표본 — 7-6 칸 이름 ------------------------------------------------------------------------------------------------
def test_sample_set_is_exactly_what_the_generator_makes():
    """서버 · 화면 · 검사가 같은 표본을 쓰도록 표본은 make_samples.py 하나가 만든다 — 손으로 고친 표본 · 남은 옛 표본이 없다."""
    made = make.samples()
    assert sorted(p.name for p in FIX.glob("protocol_*.json")) == sorted({V1, *made})
    assert set(made) == set(ALL) - {V1}
    for name, d in made.items():
        assert (FIX / name).read_text(encoding="utf-8") == make.dumps(d), f"{name} 이 생성기와 다르다 — make_samples.py 를 다시 돌린다"


@pytest.mark.parametrize("name", V4)
def test_v4_samples_follow_7_6(name):
    d = fx(name)
    assert d["_sample"] is True and d["conditions"]["protocol"] == "v4"
    json.dumps(d, allow_nan=False)                                          # 7-6 의 6 — NaN · Infinity 없음
    crit, grid, void = d["criteria"], d["grid"], d["rig"]["유효"] is False
    for k, c in crit.items():
        assert c["kind"] in KIND_VERDICTS and c["status"] in STATUS, k
    for s, cell in d["cells"].items():
        assert grid[cell["dock"]["row"] - 1][cell["dock"]["col"] - 1] == int(s)   # 독 자리는 1 부터, grid 와 같다
        items, v = cell["items"], cell["verdict"]
        for k, item in items.items():
            assert set(item) == ITEM, (s, k)
            kind = crit[k]["kind"]                                          # 셀 항목은 모두 criteria 에 갈래가 있다
            assert item["verdict"] in KIND_VERDICTS[kind], (s, k, kind, item["verdict"])
            z_num = isinstance(item["z"], (int, float))
            assert z_num == (kind == "살펴봄" and item["verdict"] in ("pass", "flag")), (s, k)   # z 는 살펴봄 항목에만
        assert set(v) == {"overall", "flags", "reasons"} and v["overall"] in ("pass", "fail", "hold", "void")
        fails = {k for k, i in items.items() if crit[k]["kind"] == "합불" and i["verdict"] == "fail"}
        holds = {k for k, i in items.items() if crit[k]["kind"] == "합불" and i["verdict"] == "hold"}
        if void:
            assert v["overall"] == "void" and v["flags"] == [] and v["reasons"]
            continue
        assert v["flags"] == [k for k, i in items.items() if i["verdict"] == "flag"], s
        assert (v["overall"] == "fail") == bool(fails), s                   # 합불 항목이 하나라도 fail 이면 셀 fail
        if v["overall"] == "pass":
            assert not holds and not v["reasons"], s
        else:
            assert v["reasons"], s


def test_samples_cover_both_sog_band_boundary_states_and_every_kind():
    """7-6 의 8 — 등급 경계가 없는 시험(null)과 4개 있는 시험이 둘 다 있어야 화면이 두 경우를 보인다. 7-6 의 7 — 갈래 넷."""
    bounds = {n: fx(n)["criteria"]["sog_band"] for n in V4 if "sog_band" in fx(n)["criteria"]}
    for c in bounds.values():
        assert c["kind"] == "정보용"
        assert c["value"] is None or (len(c["value"]) == 4 and c["value"] == sorted(c["value"]))
    assert bounds[FAC]["value"] is None and bounds[FAC]["status"] == "미정"
    assert bounds[SIM]["value"] == [150, 300, 450, 600]
    kinds = {c["kind"] for n in V4 for c in fx(n)["criteria"].values()}
    assert kinds == set(KIND_VERDICTS)
    assert fx(REP)["criteria"]["gps_rx"]["kind"] == "정보용"                 # 7-2 — 중계기 조건의 GPS 숫자는 정보용
    assert {c["verdict"]["overall"] for n in V4 for c in fx(n)["cells"].values()} == {"pass", "fail", "hold", "void"}


def test_v5_sample_is_unknown_to_the_board():
    d = fx(V5)
    assert d["_sample"] is True and d["conditions"]["protocol"] not in ("v4", None) and len(d["cells"]) == 24


# ---- 이름 검사 ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", [*ALL, "protocol_20000101-000000.json"])
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
    assert sb.qc_now(tmp_path) == {"step": {"key": "idle", "index": None}, "_file_age_s": None}     # 7-6 의 4


def test_qc_now_passes_the_file_and_adds_only_its_age(tmp_path):
    body = {"t": 1.0, "step": {"key": "A1", "index": 1}, "cells": {"10677": {"fix": "A"}}, "source": "demo"}
    p = tmp_path / "qc_now.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    got = sb.qc_now(tmp_path, now_t=p.stat().st_mtime + 3.0)
    assert got == {**body, "_file_age_s": 3.0}


def test_qc_now_unreadable_file_is_idle_with_a_reason(tmp_path):
    (tmp_path / "qc_now.json").write_text('{"step": ', encoding="utf-8")
    got = sb.qc_now(tmp_path)
    assert got["step"] == {"key": "idle", "index": None} and got["_error"] and got["_file_age_s"] is not None


# ---- HTTP -------------------------------------------------------------------------------------------------------------
def test_http_endpoints(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    fixtures(data)
    monkeypatch.setattr(sb, "RAIL_LIVE", tmp_path / "rail-live.html")          # 화면 파일은 다음 단계에 생긴다
    srv = serve(data)
    try:
        code, body, ctype = get(srv, "/api/qc_now")
        assert code == 200 and json.loads(body)["step"] == {"key": "idle", "index": None}

        code, body, _ = get(srv, "/api/rail/runs")
        runs = json.loads(body)
        assert code == 200 and [r["name"] for r in runs] == [REP, FAC, V5, VOID, TER, SIM, V1]
        assert {r["name"]: r["verdicts"]["합격"] for r in runs}[SIM] == 20

        for name in (V1, SIM, V5):
            code, body, ctype = get(srv, f"/api/rail/run?name={name}")
            assert code == 200 and "application/json" in ctype
            assert json.loads(body) == fx(name)                                 # 파일 그대로

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
        st = f["step"]
        assert STEP <= set(st) and st["started"] <= f["t"] <= st["ends"]
        assert st["key"] == f"{f['part']}{st['index']}" and isinstance(st["index"], int) and st["index"] >= 0   # 7-6 의 4
        assert (STEP_A <= set(st)) == (f["part"] == "A") and SCENE_NOW <= set(st.get("scene_now", SCENE_NOW))
        if f["part"] == "A":                                                   # 7-6 의 2 — 등속 구간만 숫자
            assert isinstance(st["scene_step"], str) and st["scene_step"]
            assert (st["band_kmh"] is not None) == st["scene_step"].startswith("band_")
            assert st["band_kmh"] is None or st["scene_step"] == f"band_{st['band_kmh']:g}"
        assert set(f["motor"]) == MOTOR and f["motor"]["state"] in ("ok", "alarm", "stopped")
        for at, why in f["motor"]["alarms"]:                                   # 7-6 의 3 — [시각, 이유] 쌍
            assert time.strptime(at, "%Y-%m-%d %H:%M:%S") and isinstance(why, str) and why
        assert len(f["cells"]) == 24 and all(set(c) == CELL for c in f["cells"].values())
        assert len(f["grid"]) == 6 and all(len(r) == 4 for r in f["grid"])
        assert sorted(str(s) for r in f["grid"] for s in r) == sorted(f["cells"])
        json.dumps(f, allow_nan=False)                                         # 브라우저 JSON.parse 가 못 읽는 NaN 이 없다
    assert {f["part"] for f in live} == ({"A", "B", "C"} if part == "cycle" else {part})
    assert all(f["step"] == {"key": "idle", "index": None} for f in fs if f["step"]["key"] == "idle")


def test_demo_cycle_shows_every_screen_state():
    d = demo.Demo("cycle", w0=0.0)
    fs = [d.frame(float(t)) for t in range(int(d.period))]
    live = [f for f in fs if f["step"]["key"] != "idle"]
    assert {f["step"]["key"] for f in live} == {"A0", "A1", "A2", "A3", "A4", "A5", "C0", "C1", "C2", "C3", "C4", "B0", "B1", "B2"}
    assert [f["step"]["a_mm_s2"] for f in live if f["step"]["key"] == "C3"][0] == 200.0      # 순번은 accels_mm_s2 의 차례
    assert [f["step"]["v_mm_s"] for f in live if f["step"]["key"] == "B1"][0] == 300.0       # 순번은 speeds_mm_s 의 차례
    a2 = [f["step"] for f in live if f["step"]["key"] == "A2"]
    assert {s["band_kmh"] for s in a2} == {None, 5.0, 11.0, 18.0, 23.0, 28.0}
    assert {"band_11", "dwell_11"} <= {s["scene_step"] for s in a2}
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
    alarm = [f for f in live if f["motor"]["state"] == "alarm"]                                # 50 ms 넘는 주기 지연 — 경고 기록
    assert alarm and all(f["motor"]["cycle_ms_max"] > 50 and f["motor"]["alarms"] for f in alarm)
    later = [f for f in live if f["part"] == alarm[0]["part"] and f["t"] > alarm[-1]["t"]]
    assert later and all(f["motor"]["alarms"] == alarm[-1]["motor"]["alarms"] for f in later)  # 실행이 끝날 때까지 쌓여 남는다
    assert all(f["motor"]["alarms"] == [] for f in live if f["part"] == "A")
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
