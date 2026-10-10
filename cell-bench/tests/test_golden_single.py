"""골든 기록 검사 G1 · G1b · G2 · G11 — 세트 1 만 도는 단일 모드가 다중 세트 작업 전후로 같은지 못 박는다.

골든은 다중 세트 작업을 시작하기 전 코드(8절 0단계)로 녹화했다(tests/golden/single/). 다시 녹화하려면
    CELLBENCH_RECORD_GOLDEN=1 python -m pytest tests/test_golden_single.py
녹화는 '허용 차이'(docs/design/multi-set-plan.md 7-2)가 생길 때만 한다 — 그때는 ALLOWED_NEW 처럼 이 파일에 이유를 남긴다.

비교하는 것: data/ 의 파일 목록 · 작은 파일(cycles · events · now · engine · control_ack)은 줄 전부 · 나머지(표본 · 셀 · 방전 CSV ·
run.log)는 SHA-256 과 줄 수 · 클라우드 요청 전부의 SHA-256(방법 · 표 · 매개변수 · Prefer · 본문) · Slack 글 · Wi-Fi 재연결 수 · 플러그 호출 수.
"""
import json
import os
import re
from pathlib import Path

import pytest

import sim_world as sw
from cellbench.control import ControlInbox

GOLDEN = Path(__file__).resolve().parent / "golden" / "single"
RECORD = os.environ.get("CELLBENCH_RECORD_GOLDEN") == "1"
# 허용 차이로 새로 생겨도 되는 파일 (명세 7-2). 골든에 없을 때만 무시한다 — 있으면 내용까지 비교한다
ALLOWED_NEW: set[str] = set()


def _world(**kw):
    return sw.World([sw.Dock(sw.SET1_MAC, sw.SET1)], **kw)


def _post(world, tmp, cmd):
    return lambda: ControlInbox(tmp / "data").post(cmd, "golden")


def scenario(name, tmp, monkeypatch):
    """시나리오 하나를 돌리고 기록을 돌려준다."""
    if name == "g1_two_cycles":
        w = _world()
        code = sw.run_main(monkeypatch, tmp, w, ["--precharge", "--cycles", "2"])
    elif name == "g1b_config_serials22":
        w = _world()
        (tmp / "rc.json").write_text(json.dumps({"serials": "11733-11754"}), encoding="utf-8")
        code = sw.run_main(monkeypatch, tmp, w, ["--cycles", "1", "--config", str(tmp / "rc.json")])
    elif name == "g1b_config_sets_set2_registered":
        w = _world()
        bench = {"bench_id": "hq-bench-1", "sets": [dict(sw.configmod.DEFAULT_SET),
                                                     {"id": 2, "plug_mac": "AA:BB:CC:DD:EE:02", "serials": "11594-11617"}]}
        code = sw.run_main(monkeypatch, tmp, w, ["--cycles", "1"], bench=bench)
    elif name == "g2_remote_plug_on":
        w = _world()
        w.at(40 * 60, _post(w, tmp, "plug_on"))           # 방전 중 (시작 80 % · 분당 2 %p)
        code = sw.run_main(monkeypatch, tmp, w, ["--cycles", "1"])
    elif name == "g2_stop_safe":
        w = _world()
        w.at(60 * 60, _post(w, tmp, "stop_safe"))         # 첫 사이클 충전 중
        code = sw.run_main(monkeypatch, tmp, w, ["--cycles", "2"])
    elif name == "g2_blind_recover":
        w = _world()
        w.blind_from, w.blind_until = sw.T0 + 10 * 60, sw.T0 + 17 * 60   # 방전 중 7분 동안 셀이 하나도 안 들림
        code = sw.run_main(monkeypatch, tmp, w, ["--cycles", "1"])
    elif name == "g2_ctrl_c":
        w = _world()

        def boom():
            raise KeyboardInterrupt
        w.at(15 * 60, boom)
        code = sw.run_main(monkeypatch, tmp, w, ["--cycles", "1"])
    else:
        raise KeyError(name)
    return sw.snapshot(tmp, w, code)


SCENARIOS = ["g1_two_cycles", "g1b_config_serials22", "g1b_config_sets_set2_registered",
             "g2_remote_plug_on", "g2_stop_safe", "g2_blind_recover", "g2_ctrl_c"]


@pytest.mark.parametrize("name", SCENARIOS)
def test_golden_single_mode(name, tmp_path, monkeypatch):
    got = scenario(name, tmp_path, monkeypatch)
    path = GOLDEN / f"{name}.json"
    if RECORD:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(got, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip("골든 녹화")
    want = json.loads(path.read_text(encoding="utf-8"))
    extra = {k for k in got["files"] if k not in want["files"] and k.rstrip("/").split("/")[-1] in ALLOWED_NEW}
    for k in extra:
        got["files"].pop(k)
    assert json.loads(json.dumps(got, ensure_ascii=False)) == want


def test_golden_scenarios_really_ran(tmp_path, monkeypatch):
    """골든이 '아무것도 안 함'을 녹화하지 않았는지 — 두 사이클이 실제로 끝까지 돌았다."""
    want = json.loads((GOLDEN / "g1_two_cycles.json").read_text(encoding="utf-8"))
    rows = want["files"]["cycles.csv"]
    assert len(rows) == 3 and want["exit"] == 0
    assert all(r.split(",")[12] for r in rows[1:])             # full_at 이 채워졌다 (만충까지 돌았다)
    assert want["cloud"]["count"].get("POST bench_cycle") == 2


# ---------- G11 클라우드 결과판 질의 골든 ----------
BOARD = Path(__file__).resolve().parents[1] / "board" / "index.html"


def cloud_board_queries() -> list[str]:
    """클라우드 결과판(cloudApi)이 Supabase 에 보내는 질의 모양 — rest(`…`) · restAll(`…`) · fetch(`…`) 의 틀 글자."""
    src = BOARD.read_text(encoding="utf-8")
    body = src[src.index("function cloudApi(cfg){"):src.index("const API=CLOUD?")]
    return re.findall(r"(?:rest|restAll|fetch)\((`[^`]*`|'[^']*')", body)


def test_g11_cloud_board_queries():
    got = cloud_board_queries()
    path = GOLDEN / "g11_cloud_board_queries.json"
    if RECORD:
        path.write_text(json.dumps(got, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        pytest.skip("골든 녹화")
    assert got == json.loads(path.read_text(encoding="utf-8"))
    assert not any("bench_health" in q and "set_id" in q for q in got)   # 신호등 표에는 세트 칸이 없다 (B-M2)
