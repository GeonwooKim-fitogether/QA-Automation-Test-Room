"""다중 세트 9단계 — 결과판 서버의 세트 API · 프로그램 차선(다중) · 플러그 매달림 격리(실제 스레드 · 실시간).
명세 2-7 · 3-2 · 6절 · D11 · D13 · A-L8 · A-M6."""
import json
import threading
import time
from pathlib import Path

import pytest

import test_supervisor as ts
from test_register import server  # noqa: F401  (픽스처)
from cellbench import health as H
from cellbench.config import DEFAULT_SET, Config, expand_serials
from cellbench.verifier import fingerprint

S2 = {"id": 2, "plug_mac": "AA:BB:CC:DD:EE:02", "serials": "11594-11617", "registered_at": 7, "plug_alias": "Dock 2"}


def _bench(tmp_path, s2=None):
    b = tmp_path / "bench.json"
    b.write_text(json.dumps({"bench_id": "hq-bench-1", "sets": [dict(DEFAULT_SET), dict(s2 or S2)]}), encoding="utf-8")
    return b


def _multi_engine(data, ids=(1, 2)):
    (data / "engine.json").write_text(json.dumps({"exit": None, "mode": "multi", "pid": 1, "started": 0,
                                                  "sets": [{"id": k} for k in ids]}), encoding="utf-8")


def test_get_endpoints_take_set(tmp_path, server):
    call, data, b, _ = server(bench=_bench(tmp_path))
    (data / "now.json").write_text(json.dumps({"phase": "CHARGE", "cycle": 9}))
    (data / "set2").mkdir()
    (data / "set2" / "now.json").write_text(json.dumps({"phase": "DISCHARGE", "cycle": 2}))
    assert call("/api/now")[1]["cycle"] == 9 and call("/api/now?set=1")[1]["cycle"] == 9   # 없음 · 1 은 지금 그대로
    assert call("/api/now?set=2")[1]["cycle"] == 2
    assert call("/api/now?set=9")[0] == 400 and call("/api/cycles?set=3")[1] == []


def test_control_routes_to_set_only_when_running(tmp_path, server):
    call, data, b, _ = server(bench=_bench(tmp_path))
    code, j = call("/api/control", {"cmd": "plug_off", "pin": "1234", "set": 2})
    assert code == 409 and not (data / "set2" / "control.json").exists()           # 단일 모드 — 세트 2 는 운전 중이 아님
    code, j = call("/api/control", {"cmd": "plug_on", "pin": "1234"})
    assert code == 200 and "set" not in j["posted"]                                  # 지정 없음 = 지금과 같은 내용
    _multi_engine(data)
    (data / "set2").mkdir(exist_ok=True)
    code, j = call("/api/control", {"cmd": "stop_safe", "pin": "1234", "set": 2})
    assert code == 200 and json.loads((data / "set2" / "control.json").read_text(encoding="utf-8"))["cmd"] == "stop_safe"
    assert call("/api/control?set=2")[1]["pending"]["cmd"] == "stop_safe" and call("/api/control")[1]["running_sets"] == [1, 2]


def test_pair_request_and_view(tmp_path, server):
    call, data, b, _ = server(bench=_bench(tmp_path))
    assert call("/api/pair", {"pin": "0000", "set": 2, "action": "start"})[0] == 403
    assert call("/api/pair", {"pin": "1234", "set": 1, "action": "start"})[0] == 400       # 세트 1 은 면제
    code, j = call("/api/pair", {"pin": "1234", "set": 2, "action": "start", "mode": "calib"})
    assert code == 200 and json.loads((data / "set2" / "pair_request.json").read_text(encoding="utf-8"))["mode"] == "calib"
    v = call("/api/pair?set=2")[1]
    assert v["ok"] and v["calibrated"] is False and v["pair_ok"] is False and v["request"]["action"] == "start"


def test_run_needs_pair_pass_and_uses_bench_lock(tmp_path, server):
    call, data, b, _ = server(bench=_bench(tmp_path))
    code, j = call("/api/run", {"pin": "1234", "set": 2, "on": True})
    assert code == 409 and "짝 검증" in j["error"]
    assert "run" not in json.loads(b.read_text(encoding="utf-8"))["sets"][1]
    (data / "set2").mkdir(exist_ok=True)
    (data / "set2" / "pair.json").write_text(json.dumps({"state": "passed", "fingerprint": fingerprint(S2)}))
    (data / "set2" / "hold").write_text("x")
    code, j = call("/api/run", {"pin": "1234", "set": 2, "on": True})
    assert code == 200 and json.loads(b.read_text(encoding="utf-8"))["sets"][1]["run"] is True
    assert not (data / "set2" / "hold").exists()                                      # 다시 켜기는 정지 표지를 지운다
    _multi_engine(data)
    code, j = call("/api/run", {"pin": "1234", "set": 2, "on": False})
    assert code == 200 and j["running"] is True                                         # 엔진이 HOLD 를 알릴 때까지 '운전 중'
    assert json.loads((data / "set2" / "control.json").read_text(encoding="utf-8"))["cmd"] == "stop_safe"
    assert call("/api/run", {"pin": "1234", "set": 1, "on": False})[0] == 400


def test_remove_refuses_running_and_archives(tmp_path, server):
    call, data, b, _ = server(bench=_bench(tmp_path, {**S2, "run": True}))
    code, j = call("/api/register/remove", {"pin": "1234", "set_id": 2})
    assert code == 400 and "운전이 켜진" in j["error"]
    b.write_text(json.dumps({"sets": [dict(DEFAULT_SET), dict(S2)]}), encoding="utf-8")
    _multi_engine(data)
    assert call("/api/register/remove", {"pin": "1234", "set_id": 2})[0] == 400     # 엔진이 쥔 세트
    (data / "engine.json").write_text(json.dumps({"exit": "done"}))
    (data / "set2").mkdir(exist_ok=True)
    (data / "set2" / "pair.json").write_text("{}")
    code, j = call("/api/register/remove", {"pin": "1234", "set_id": 2})
    assert code == 200 and not (data / "set2").exists() and list((data / "archive").glob("set2-*/pair.json"))


def test_new_set_saves_plug_ip():
    from cellbench.register import new_set
    assert new_set(2, "aa:bb:cc:dd:ee:02", [1, 2], ip="192.168.1.120")["plug_ip"] == "192.168.1.120"
    assert "plug_ip" not in new_set(2, "aa:bb:cc:dd:ee:02", [1, 2])


# ---------- 프로그램 차선 (다중) ----------

def _now(t, phase="CHARGE", since=None, ev=0):
    cells = [{"serial": s, "battery": 60, "age": 1.0, "waiting": False} for s in expand_serials(DEFAULT_SET["serials"])]
    return {"t": t, "beat": t, "phase": phase, "phase_since": since or t, "cycle": 1, "expected": 24, "cells": cells,
            "plug": {"on": True, "w": 60.0}, "metrics": {"events_1h": ev}}


def test_program_lane_uses_beat_json_dwell_per_set_and_summed_events(tmp_path):
    T = ts.T
    cfg = Config(data_dir=str(tmp_path), sets=[dict(DEFAULT_SET), {**S2, "run": True}])
    (tmp_path / "set2").mkdir()
    (tmp_path / "set2" / "now.json").write_text(json.dumps(_now(T, "DISCHARGE", since=T - 9 * 3600, ev=2)))
    (tmp_path / "beat.json").write_text(json.dumps({"t": T - 5, "beat": T - 5}))
    eng = {"mode": "multi", "exit": None, "sets": [{"id": 1}, {"id": 2}]}
    h = H.compute(_now(T - 400, ev=1), None, None, [], cfg, T, engine=eng, sets_data=H.set_materials(tmp_path, cfg, eng))
    prog = next(l for l in h["lanes"] if l["key"] == "program")
    reasons = " | ".join(i["reason"] for i in prog["issues"])
    assert "엔진 심박이 멈춤" not in reasons                                   # 세트 1 now.json 은 400초 묵었지만 조정자는 살아 있다
    assert "세트 2 · 방전 단계가" in reasons                                     # 세트 2 체류 9시간 > 5 × 1.5
    assert "지난 1시간 이상 3건" in reasons                                      # 1 + 2 (세트 합)


# ---------- 플러그 매달림 격리 — 가상 시계 밖 검사 (실제 스레드 · 실시간) ----------

def test_slow_plug_in_set2_does_not_delay_set1_samples(tmp_path):
    """[가상 시계 밖 · 실제 스레드 · 실시간 약 4초] 세트 2 플러그 읽기가 3초 매달리는 동안 세트 1 표본 간격이 늘지 않는다.
    실물의 66초(3번 × 20초 + 2초) 대신 3초로 줄였다. 플러그 호출은 잠금을 쥐지 않으므로(명세 2-4) 세트마다 따로 막힌다."""
    from cellbench.cycle import CycleRunner, CycleState
    from cellbench.cells import CellLive
    from cellbench.plug import PlugReading
    from cellbench.record import Recorder

    class Live:
        ip_changes = 0
        def snapshot(self):
            now = time.time()
            return {s: CellLive("1.1.1.1", 60, 0, 8, -50, now) for s in range(1, 49)}

    class Plug:
        def __init__(self, delay): self.delay = delay
        def read(self):
            time.sleep(self.delay)
            return PlugReading(True, 60.0, time.time())

    def runner(k, delay):
        cfg = Config(data_dir=str(tmp_path / f"s{k}"), serials=list(range(1 + 24 * (k - 1), 25 + 24 * (k - 1))), poll_s=0.2)
        r = CycleRunner(cfg, Live(), Plug(delay), None, Recorder(cfg.data_dir))
        r.name = str(k)
        return r

    r1, r2 = runner(1, 0.0), runner(2, 3.0)
    stamps = {1: [], 2: []}

    def loop(r, k, n):
        st = CycleState(cycle=1); st.phase = "CHARGE"
        r.rec.begin_cycle(1)
        wh, prev = 0.0, None
        for _ in range(n):
            wh, prev, _w = r._poll(st, wh, prev)
            stamps[k].append(time.time())
            time.sleep(0.2)

    t2 = threading.Thread(target=loop, args=(r2, 2, 1), daemon=True)
    t1 = threading.Thread(target=loop, args=(r1, 1, 12), daemon=True)
    t2.start(); time.sleep(0.05); t1.start()
    t1.join(10); t2.join(10)
    gaps = [b - a for a, b in zip(stamps[1], stamps[1][1:])]
    assert len(stamps[1]) == 12 and len(stamps[2]) == 1
    assert stamps[1][-1] < stamps[2][0]                    # 세트 1 은 세트 2 의 매달림이 끝나기 전에 12번을 다 돌았다
    assert max(gaps) < 1.0, gaps                           # 표본 간격이 3초 매달림만큼 늘지 않았다


def test_pair_start_refused_for_running_or_holding_set(tmp_path, server):
    """코드 검토 5 — 운전 목록(세트 정지 HOLD 포함)에 있는 세트의 짝 검증 시작은 409. 멈춤(abort)은 받는다."""
    call, data, b, _ = server(bench=_bench(tmp_path))
    _multi_engine(data)
    (data / "set2").mkdir(exist_ok=True)
    (data / "set2" / "hold").write_text("세트 정지")                                  # HOLD 중이어도 운전 목록에 있다
    code, j = call("/api/pair", {"pin": "1234", "set": 2, "action": "start", "mode": "calib"})
    assert code == 409 and "운전 중" in j["error"] and not (data / "set2" / "pair_request.json").exists()
    assert call("/api/pair", {"pin": "1234", "set": 2, "action": "abort"})[0] == 200
    (data / "engine.json").write_text(json.dumps({"exit": "reconfigure", "mode": "multi", "sets": [{"id": 1}, {"id": 2}]}))
    assert call("/api/pair", {"pin": "1234", "set": 2, "action": "start", "mode": "calib"})[0] == 200   # 엔진이 끝났으면 받는다


def test_set_commands_refused_while_stop_all_pending(tmp_path, server):
    """QA D-2 — 다중 모드에서 전체 정지(세트 지정 없는 안전 정지)가 대기 중이면 세트 명령은 409. 파일 모양은 그대로다."""
    call, data, b, _ = server(bench=_bench(tmp_path))
    _multi_engine(data)
    (data / "set2").mkdir(exist_ok=True)
    assert call("/api/control", {"cmd": "stop_safe", "pin": "1234"})[0] == 200                 # 전체 정지
    full = json.loads((data / "control.json").read_text(encoding="utf-8"))
    assert full["cmd"] == "stop_safe" and "set" not in full
    for k in (1, 2):
        code, j = call("/api/control", {"cmd": "plug_on", "pin": "1234", "set": k})
        assert code == 409 and "전체 정지" in j["error"]
    assert json.loads((data / "control.json").read_text(encoding="utf-8")) == full             # 덮이지 않았다
    assert not (data / "set2" / "control.json").exists()
    for k in ("", "?set=2"):
        assert call(f"/api/control{k}")[1]["stop_all_pending"]["cmd"] == "stop_safe"          # 세트 1 · 2 화면 모두 안다
    (data / "control.json").unlink()                                                           # 엔진이 집어 갔다
    assert call("/api/control", {"cmd": "plug_on", "pin": "1234", "set": 2})[0] == 200
    assert call("/api/control")[1]["stop_all_pending"] is None


def test_single_mode_stop_is_not_a_stop_all(tmp_path, server):
    call, data, b, _ = server(bench=_bench(tmp_path))
    assert call("/api/control", {"cmd": "stop_safe", "pin": "1234"})[0] == 200
    assert call("/api/control")[1]["stop_all_pending"] is None                                  # 단일 모드에서는 그냥 안전 정지


@pytest.mark.parametrize("bad", [1.5, 2.0, "2a", True, [2], "1.5"])
def test_only_integer_set_numbers(tmp_path, server, bad):
    """QA D-10 — 세트 번호는 정수만. 1.5 가 int() 로 1 이 돼 세트 1 로 가던 구멍."""
    call, data, b, _ = server(bench=_bench(tmp_path))
    _multi_engine(data)
    code, j = call("/api/control", {"cmd": "plug_on", "pin": "1234", "set": bad})
    assert code == 400 and not (data / "control.json").exists()
    assert call("/api/pair", {"pin": "1234", "set": bad, "action": "start"})[0] == 400
    assert call("/api/run", {"pin": "1234", "set": bad, "on": False})[0] == 400
    assert call("/api/register/remove", {"pin": "1234", "set_id": bad})[0] == 400
    if isinstance(bad, (str, float)) and not isinstance(bad, bool):
        assert call(f"/api/now?set={bad}")[0] == 400


def test_pair_view_lists_every_gate_and_run_lock(tmp_path, server):
    """QA D-4 — 관문 전부(보정 · 짝 · 클라우드 판 · 설정 · 운전 켜기)와 상태, 그리고 '운전 켜기'가 잠긴 이유."""
    call, data, b, _ = server(bench=_bench(tmp_path))
    v = call("/api/pair?set=2")[1]
    names = [g["name"] for g in v["gates"]]
    assert names == ["보정 실측", "짝 검증", "클라우드 판", "설정", "운전 켜기"]
    st = {g["name"]: g["state"] for g in v["gates"]}
    assert st["보정 실측"] == "no" and st["짝 검증"] == "no" and st["클라우드 판"] == "unknown" and st["운전 켜기"] == "no"
    assert "짝 검증" in v["run_lock"]
    (data / "set2").mkdir(exist_ok=True)
    (data / "set2" / "not_ready.txt").write_text("보정 실측(U2) 전 · 클라우드 스키마 판 2 확인 전(U1)", encoding="utf-8")
    (data / "set2" / "pair.json").write_text(json.dumps({"state": "passed", "fingerprint": fingerprint(S2)}))
    v = call("/api/pair?set=2")[1]
    assert {g["name"]: g["state"] for g in v["gates"]}["클라우드 판"] == "no" and v["run_lock"] is None
