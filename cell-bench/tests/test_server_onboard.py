"""자동 합류 — 결과판 서버 API (POST /api/pair mode=auto · GET /api/pair 의 onboard · 보정 관문).
PIN 오류 · 잠금 · 진행 중 거부 · 도출한 문턱이 있으면 '보정 실측' 관문이 열림."""
import json

import test_supervisor as ts  # noqa: F401
from test_register import server  # noqa: F401  (픽스처)
from cellbench.config import DEFAULT_SET
from cellbench.verifier import fingerprint

S2 = {"id": 2, "plug_mac": "AA:BB:CC:DD:EE:02", "serials": "11594-11617", "registered_at": 7, "plug_alias": "Dock 2"}


def _bench(tmp_path):
    b = tmp_path / "bench.json"
    b.write_text(json.dumps({"bench_id": "hq-bench-1", "sets": [dict(DEFAULT_SET), dict(S2)]}), encoding="utf-8")
    return b


def test_auto_request_needs_pin_and_writes_mode_auto(tmp_path, server):
    call, data, b, _ = server(bench=_bench(tmp_path))
    assert call("/api/pair", {"pin": "0000", "set": 2, "action": "start", "mode": "auto"})[0] == 403          # PIN 이 틀림
    assert not (data / "set2" / "pair_request.json").exists()
    assert call("/api/pair", {"pin": "1234", "set": 1, "action": "start", "mode": "auto"})[0] == 400          # 세트 1 은 합류할 것이 없다
    code, j = call("/api/pair", {"pin": "1234", "set": 2, "action": "start", "mode": "auto"})
    assert code == 200 and j["request"]["mode"] == "auto" and j["request"]["action"] == "start"
    assert json.loads((data / "set2" / "pair_request.json").read_text(encoding="utf-8"))["mode"] == "auto"
    # 모르는 mode 는 판정(verify)으로 — 자동 합류가 되지 않는다
    call("/api/pair", {"pin": "1234", "set": 2, "action": "abort"})
    code, j = call("/api/pair", {"pin": "1234", "set": 2, "action": "start", "mode": "x"})
    assert j["request"]["mode"] == "verify"


def test_auto_request_locks_after_wrong_pins_like_other_actions(tmp_path, server):
    call, data, b, _ = server(bench=_bench(tmp_path))
    for _ in range(5):
        call("/api/pair", {"pin": "0000", "set": 2, "action": "start", "mode": "auto"})
    code, j = call("/api/pair", {"pin": "1234", "set": 2, "action": "start", "mode": "auto"})
    assert code == 429 and not (data / "set2" / "pair_request.json").exists()                                # 맞는 PIN 도 10분 잠김


def test_auto_request_refused_while_running_or_in_operation(tmp_path, server):
    call, data, b, _ = server(bench=_bench(tmp_path))
    (data / "set2").mkdir()
    (data / "set2" / "onboard.json").write_text(json.dumps({"set": 2, "step": "calib", "result": None}), encoding="utf-8")
    code, j = call("/api/pair", {"pin": "1234", "set": 2, "action": "start", "mode": "auto"})
    assert code == 409 and "진행 중" in j["error"] and not (data / "set2" / "pair_request.json").exists()
    assert call("/api/pair", {"pin": "1234", "set": 2, "action": "abort"})[0] == 200                          # 멈춤은 받는다
    (data / "set2" / "onboard.json").write_text(json.dumps({"set": 2, "step": "joined", "result": "joined"}), encoding="utf-8")
    (data / "set2" / "pair_request.json").unlink()
    (data / "engine.json").write_text(json.dumps({"exit": None, "mode": "multi", "pid": 1, "started": 0,
                                                  "sets": [{"id": 1}, {"id": 2}]}), encoding="utf-8")
    assert call("/api/pair", {"pin": "1234", "set": 2, "action": "start", "mode": "auto"})[0] == 409          # 운전 중인 세트


def test_view_reports_onboard_and_per_set_calibration(tmp_path, server):
    call, data, b, _ = server(bench=_bench(tmp_path))
    v = call("/api/pair?set=2")[1]
    assert v["onboard"] is None and v["onboarding"] is False and v["calibrated"] is False and v["thresholds"] is None
    assert next(g for g in v["gates"] if g["name"] == "보정 실측")["state"] == "no"
    (data / "set2").mkdir()
    (data / "set2" / "onboard.json").write_text(json.dumps({"set": 2, "step": "verify", "result": None, "why": ""}), encoding="utf-8")
    (data / "set2" / "pair_thresholds.json").write_text(json.dumps(
        {"state": "derived", "fingerprint": fingerprint(S2), "thresholds": {"quick_r_cell": 0.38, "quick_r_median": 0.38}}), encoding="utf-8")
    v = call("/api/pair?set=2")[1]
    assert v["onboarding"] is True and v["onboard"]["step"] == "verify"
    assert v["calibrated"] is True and v["thresholds"]["quick_r_cell"] == 0.38                               # '판정' 짝 검증 단추가 열린다
    gate = next(g for g in v["gates"] if g["name"] == "보정 실측")
    assert gate["state"] == "ok" and "자동 합류" in gate["text"]
    # 지문이 다른(재등록) 문턱은 무효
    (data / "set2" / "pair_thresholds.json").write_text(json.dumps(
        {"state": "derived", "fingerprint": "0000", "thresholds": {"quick_r_cell": 0.38}}), encoding="utf-8")
    assert call("/api/pair?set=2")[1]["calibrated"] is False


def test_board_has_auto_button_and_event_names():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "board" / "index.html").read_text(encoding="utf-8")
    assert 'data-pair="auto"' in html and "자동 합류 시작" in html and "onboard_failed:'자동 합류 실패" in html
    assert "'onboard_failed'" in html.split("const EV_RED")[1].split("const EV_KO")[0]               # 실패는 빨강
