"""다중 세트 8단계 — 신호등의 세트별 차선 · 세트 목록 · 두 호출부 배선 · engine_claims (명세 6절 · B-M5)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import serve_board  # noqa: E402
import test_supervisor as ts  # noqa: E402
from cellbench import health as H  # noqa: E402
from cellbench import register  # noqa: E402
from cellbench.config import DEFAULT_SET, Config, expand_serials  # noqa: E402
from cellbench.supervisor import tick  # noqa: E402

T = ts.T
S1 = expand_serials(DEFAULT_SET["serials"])
S2 = list(range(11594, 11618))
SET2 = {"id": 2, "plug_mac": "AA:BB:CC:DD:EE:02", "serials": "11594-11617", "run": True}


def _now(serials, t=T, gap=(), batt=60, phase="CHARGE"):
    cells = [{"serial": s, "battery": batt, "age": 99.0 if s in gap else 1.0, "waiting": False} for s in serials]
    return {"t": t, "beat": t, "phase": phase, "cycle": 1, "expected": len(serials), "cells": cells,
            "plug": {"on": True, "w": 60.0}, "metrics": {"plug": {"retries_1h": 0, "last_call_s": 0.5, "toggles_total": 1}}}


def _cfg(tmp, sets):
    return Config(data_dir=str(tmp), sets=sets)


def test_single_mode_compute_is_unchanged_by_set_materials(tmp_path):
    """G7 — 세트 1 만이면 재료를 넘겨도 판정이 바이트 단위로 같다."""
    cfg = _cfg(tmp_path, [dict(DEFAULT_SET)])
    a = H.compute(_now(S1), None, None, [], cfg, T)
    b = H.compute(_now(S1), None, None, [], cfg, T, sets_data=H.set_materials(tmp_path, cfg, {"exit": None}))
    assert json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def test_idle_set_reason_follows_pair_state(tmp_path):
    cfg = _cfg(tmp_path, [dict(DEFAULT_SET), SET2])
    d = tmp_path / "set2"; d.mkdir()
    md = lambda: H.set_materials(tmp_path, cfg, None)
    two = lambda: H.compute(_now(S1), None, None, [], cfg, T, sets_data=md())["sets"][1]
    assert two()["reason"] == "등록됨 · 짝 검증 전" and two()["running"] is False
    (d / "not_ready.txt").write_text("보정 실측(U2) 전", encoding="utf-8")
    assert two()["reason"] == "등록됨 · 짝 검증 전 — 보정 실측(U2) 전"
    (d / "pair.json").write_text(json.dumps({"state": "passed"}))
    assert two()["reason"].startswith("짝 검증 통과 · 운전 켜짐")
    (d / "hold").write_text("x")
    assert two()["reason"].startswith("세트 정지 표지")


def test_multi_mode_measures_each_set_and_prefixes_reasons(tmp_path):
    cfg = _cfg(tmp_path, [dict(DEFAULT_SET), SET2])
    d = tmp_path / "set2"; d.mkdir()
    (d / "now.json").write_text(json.dumps(_now(S2, gap=S2[:3])))
    eng = {"mode": "multi", "exit": None, "sets": [{"id": 1}, {"id": 2}]}
    h = H.compute(_now(S1), None, None, [], cfg, T, engine=eng, sets_data=H.set_materials(tmp_path, cfg, eng))
    live = next(l for l in h["lanes"] if l["key"] == "live")
    assert live["light"] == "yellow" and live["reason"].startswith("세트 2 · ") and "세트 2: " in live["value"]
    sets = {s["id"]: s for s in h["sets"]}
    assert sets[1]["light"] == "green" and sets[2]["running"] and sets[2]["light"] == "yellow"


def test_both_call_sites_pass_set_materials(tmp_path):
    """감시자 tick 과 결과판 서버 health_now 가 같은 세트별 재료를 쓴다 (B-M5)."""
    sets = [{"id": 1, "mac": "M1", "target": 5}, {"id": 2, "mac": "M2", "target": 5}]
    ts.put(tmp_path, ts.eng(mode="multi", sets=sets), _now(S1), cycles=1)
    (tmp_path / "beat.json").write_text(json.dumps({"t": T, "beat": T}))
    (tmp_path / "set2").mkdir()
    (tmp_path / "set2" / "now.json").write_text(json.dumps(_now(S2, gap=S2)))
    bench = tmp_path / "bench.json"
    bench.write_text(json.dumps({"sets": [dict(DEFAULT_SET), SET2]}), encoding="utf-8")
    cfg = Config.load(None, bench_path=bench)
    cfg.data_dir = str(tmp_path)
    w = ts.World(alive={4242: T - 3600})
    rep, _ = tick(cfg, tmp_path, None, w.deps(), T)
    s2 = next(s for s in rep["health"]["sets"] if s["id"] == 2)
    assert s2["running"] and s2["light"] == "red"                     # 세트 2 수신 0 — 감시자 판정
    h = serve_board.health_now(tmp_path, now_t=T, cfg=cfg, bench=bench)
    assert next(s for s in h["sets"] if s["id"] == 2)["light"] == "red"   # 결과판 서버도 같다


def test_engine_claims_uses_multi_sets(tmp_path):
    cfg = _cfg(tmp_path, [dict(DEFAULT_SET)])
    eng = {"mode": "multi", "exit": None, "sets": [{"id": 1, "mac": DEFAULT_SET["plug_mac"], "serials": "11733-11756"},
                                                   {"id": 2, "mac": "AA:BB:CC:DD:EE:02", "serials": "11594-11617"}]}
    c = register.engine_claims(cfg, {"cells": []}, eng, True)
    assert c["serials"].get(11594) == 2 and c["macs"].get("AA:BB:CC:DD:EE:02") == 2 and 11733 not in c["serials"]
