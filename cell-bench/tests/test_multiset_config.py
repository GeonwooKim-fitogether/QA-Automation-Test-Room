"""다중 세트 1단계 — 세트 설정 보기 · 폴더 · 운전 관문(설정 몫). 명세 docs/design/multi-set-plan.md 2-1 · 5-1."""
import json
import sys
from dataclasses import asdict, fields
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench.config import (DEFAULT_SET, MAX_SETS, PAIR, SAFETY, Config, other_plugs, run_gate_problems,  # noqa: E402
                              set_cfg, set_dir, validate)

S2 = {"id": 2, "plug_mac": "AA:BB:CC:DD:EE:02", "serials": "11594-11617", "plug_ip": "192.168.1.120", "run": True}


def _cfg(tmp_path, bench: dict, config: dict | None = None) -> Config:
    b = tmp_path / "bench.json"
    b.write_text(json.dumps(bench), encoding="utf-8")
    c = None
    if config is not None:
        c = tmp_path / "rc.json"
        c.write_text(json.dumps(config), encoding="utf-8")
    return Config.load(c, bench_path=b)


def test_new_tunables_are_not_config_fields():
    """짝 · 안전 문턱은 상수다 — Config 의 칸이 늘면 run.log 의 '설정:' 줄이 바뀐다(명세 7-1)."""
    names = {f.name for f in fields(Config)}
    assert not names & {"pair", "safety", "PAIR", "SAFETY"}
    assert SAFETY["discharge_max_h"] == 7.0 and PAIR["follow_rho"] == 0.8 and MAX_SETS == 5


def test_set1_effective_config_is_current_cfg_even_with_run_config(tmp_path):
    """세트 1 의 실효 설정은 --config 로 덮은 지금 cfg 그대로 (B-H1 — 22대로 도는 TestPC)."""
    cfg = _cfg(tmp_path, {"sets": [dict(DEFAULT_SET), S2]}, {"serials": "11733-11754", "plug_mac": "11:22:33:44:55:66"})
    one = set_cfg(cfg, 1)
    assert one is cfg and len(one.serials) == 22 and one.plug_mac == "11:22:33:44:55:66"
    assert set_dir(cfg, 1) == Path(cfg.data_dir)


def test_set_k_config_view(tmp_path):
    cfg = _cfg(tmp_path, {"sets": [dict(DEFAULT_SET), S2]})
    k = set_cfg(cfg, 2)
    assert k.serials[0] == 11594 and len(k.serials) == 24 and k.plug_mac == S2["plug_mac"]
    assert k.plug_ip_hint == "192.168.1.120"                        # 세트 1 의 plug_ip_hint 를 물려받지 않는다 (D13)
    assert Path(k.data_dir) == Path(cfg.data_dir) / "set2"
    assert cfg.serials[0] == 11733 and cfg.plug_ip_hint == "192.168.1.103"   # 원래 설정은 그대로
    no_ip = _cfg(tmp_path, {"sets": [dict(DEFAULT_SET), {k_: v for k_, v in S2.items() if k_ != "plug_ip"}]})
    assert set_cfg(no_ip, 2).plug_ip_hint == ""                      # 비우면 Plug 가 MAC 방송 탐색으로 찾는다
    with pytest.raises(KeyError):
        set_cfg(cfg, 3)


def test_other_plugs_skip_set1_effective_mac_and_bad_rows(tmp_path):
    cfg = _cfg(tmp_path, {"sets": [dict(DEFAULT_SET), S2, {"id": 3, "plug_mac": "bad", "serials": "1-24"}]})
    assert other_plugs(cfg) == [{"id": 2, "mac": "AA:BB:CC:DD:EE:02", "ip": "192.168.1.120"}]
    same = _cfg(tmp_path, {"sets": [dict(DEFAULT_SET), S2]}, {"plug_mac": S2["plug_mac"]})
    assert other_plugs(same) == []


def test_run_gate_problems(tmp_path):
    ok = _cfg(tmp_path, {"sets": [dict(DEFAULT_SET), S2]})
    assert run_gate_problems(ok, 2) == [] and run_gate_problems(ok, 1) == []
    off = _cfg(tmp_path, {"sets": [dict(DEFAULT_SET), {**S2, "run": False}]})
    assert any("run" in p for p in run_gate_problems(off, 2))
    # --config 가 세트 1 의 시리얼을 세트 2 쪽으로 덮었다 → 세트 2 는 못 돈다 (D12), 그래도 validate 는 지금처럼 통과
    clash = _cfg(tmp_path, {"sets": [dict(DEFAULT_SET), S2]}, {"serials": "11590-11613"})
    assert any("겹친다" in p for p in run_gate_problems(clash, 2)) and validate(clash) == []
    mac = _cfg(tmp_path, {"sets": [dict(DEFAULT_SET), S2]}, {"plug_mac": S2["plug_mac"]})
    assert any("MAC" in p for p in run_gate_problems(mac, 2))
    bad_id = _cfg(tmp_path, {"sets": [dict(DEFAULT_SET), {**S2, "id": "2"}]})
    assert any("정수" in p for p in run_gate_problems(bad_id, 2)) and validate(bad_id) == []   # 단일 모드는 막지 않는다
    no1 = _cfg(tmp_path, {"sets": [{**DEFAULT_SET, "id": 7}, S2]})
    assert any("세트 1" in p for p in run_gate_problems(no1, 2))


def test_dump_unchanged_by_new_set_keys(tmp_path):
    """세트 줄 안의 새 키(run · plug_ip)는 sets 에 그대로 실릴 뿐 새 칸을 만들지 않는다."""
    cfg = _cfg(tmp_path, {"sets": [dict(DEFAULT_SET)]})
    assert set(asdict(cfg)) == {f.name for f in fields(Config)}
