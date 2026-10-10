"""시험대 설정(bench.json) — 불러오는 순서 · 운전 중인 세트의 단일 원천 · 시리얼 펼치기 · 설정 검사.

검사는 실제 cell-bench/bench.json 을 읽지 않도록 bench_path 를 늘 임시 폴더로 준다.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench.config import Config, DEFAULT_SET, expand_serials, validate


def _w(path: Path, obj, bom=False) -> Path:
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    return path


# ---------- 불러오는 순서 ----------

def test_defaults_when_no_files(tmp_path):
    cfg = Config.load(None, bench_path=tmp_path / "없음.json")
    assert cfg.bench_id == "hq-bench-1"
    assert cfg.serials == list(range(11733, 11757))
    assert cfg.plug_mac == DEFAULT_SET["plug_mac"]
    assert cfg.sets[0]["serials"] == "11733-11756"


def test_code_defaults_come_from_set1():
    """기본 serials·plug_mac 이 세트 1 의 값과 따로 놀지 않는다 (단일 원천)."""
    cfg = Config()
    assert cfg.serials == expand_serials(cfg.sets[0]["serials"])
    assert cfg.plug_mac == cfg.sets[0]["plug_mac"]


def test_merge_order_default_then_bench_then_config(tmp_path):
    bench = _w(tmp_path / "bench.json", {"bench_id": "b-1", "poll_s": 30, "board_port": 9000})
    extra = _w(tmp_path / "run.json", {"poll_s": 5})
    cfg = Config.load(extra, bench_path=bench)
    assert cfg.bench_id == "b-1"          # bench.json 이 기본값을 덮고
    assert cfg.board_port == 9000
    assert cfg.poll_s == 5                # --config 가 bench.json 을 덮는다
    assert cfg.discharge_stop_pct == 30   # 아무도 안 준 값은 기본값


def test_old_style_load_still_works(tmp_path):
    """기존 호출 Config.load(path) 는 그대로 동작한다 (bench.json 이 없으면 예전과 같다)."""
    extra = _w(tmp_path / "my.json", {"poll_s": 7})
    assert Config.load(extra, bench_path=tmp_path / "없음.json").poll_s == 7
    assert Config.load(None, bench_path=None).poll_s == 20.0


def test_unknown_key_rejected_in_both_files(tmp_path):
    with pytest.raises(KeyError):
        Config.load(None, bench_path=_w(tmp_path / "bench.json", {"pol_s": 5}))
    with pytest.raises(KeyError):
        Config.load(_w(tmp_path / "x.json", {"dump": 1}), bench_path=None)   # 메서드 이름도 설정이 아니다


def test_bom_written_by_notepad_is_accepted(tmp_path):
    cfg = Config.load(None, bench_path=_w(tmp_path / "bench.json", {"bench_name": "시험대 2"}, bom=True))
    assert cfg.bench_name == "시험대 2"


def test_missing_config_file_is_an_error(tmp_path):
    with pytest.raises(OSError):
        Config.load(tmp_path / "없는.json", bench_path=None)


# ---------- 운전 중인 세트 = sets[0] ----------

def test_running_set_filled_from_sets0(tmp_path):
    bench = _w(tmp_path / "bench.json", {"sets": [
        {"id": 2, "plug_mac": "AA:BB:CC:DD:EE:01", "serials": "11594-11617", "label": "B"},
        {"id": 1, "plug_mac": "20:E1:5D:E6:9C:77", "serials": "11733-11756", "label": "A"}]})
    cfg = Config.load(None, bench_path=bench)
    assert cfg.serials == list(range(11594, 11618))
    assert cfg.plug_mac == "AA:BB:CC:DD:EE:01"


def test_explicit_serials_and_mac_win_over_sets(tmp_path):
    bench = _w(tmp_path / "bench.json", {"sets": [{"id": 1, "plug_mac": "AA:BB:CC:DD:EE:01", "serials": "1-3"}]})
    extra = _w(tmp_path / "run.json", {"serials": [7, 8], "plug_mac": "AA:BB:CC:DD:EE:09"})
    cfg = Config.load(extra, bench_path=bench)
    assert cfg.serials == [7, 8] and cfg.plug_mac == "AA:BB:CC:DD:EE:09"


def test_top_level_serials_accept_range_string(tmp_path):
    cfg = Config.load(_w(tmp_path / "run.json", {"serials": "11733-11735"}), bench_path=None)
    assert cfg.serials == [11733, 11734, 11735]


# ---------- 시리얼 펼치기 ----------

def test_expand_serials_forms():
    assert expand_serials("11733-11736") == [11733, 11734, 11735, 11736]
    assert expand_serials("11733-11734, 11750") == [11733, 11734, 11750]
    assert expand_serials([11733, "11740-11741"]) == [11733, 11740, 11741]
    assert expand_serials(11733) == [11733]
    assert expand_serials("") == [] and expand_serials([]) == []
    assert expand_serials("5,5") == [5, 5]                  # 중복은 그대로 — validate 가 알린다


@pytest.mark.parametrize("bad", ["11756-11733", "abc", "117-33-1", 1.5, None, True, {"a": 1}, "1-99999"])
def test_expand_serials_rejects_bad(bad):
    with pytest.raises(ValueError):
        expand_serials(bad)


# ---------- 설정 검사 ----------

def test_validate_default_is_clean():
    assert validate(Config()) == []


def _cfg(sets, **kw):
    cfg = Config(sets=sets, **kw)
    return cfg


def test_validate_finds_each_problem():
    sets = [
        {"id": 1, "plug_mac": "20:E1:5D:E6:9C:77", "serials": "11733-11756"},
        {"id": 1, "plug_mac": "20-E1-5D-E6-9C-78", "serials": "11750-11760"},   # id 중복 · MAC 형식 · 시리얼 겹침
        {"id": 3, "plug_mac": "AA:BB:CC:DD:EE:FF", "serials": ""},              # 빈 시리얼
        {"id": 4, "plug_mac": "AA:BB:CC:DD:EE:F0", "serials": [5, 6, 5]},       # 세트 안 중복
    ]
    probs = validate(_cfg(sets))
    text = "\n".join(probs)
    assert "세트 id 1 가 중복" in text
    assert "20-E1-5D-E6-9C-78" in text
    assert "겹친다" in text and "11750" in text
    assert "세트 3: 시리얼이 비어 있다" in text
    assert "세트 4: 시리얼이 중복된다: 5" in text


def test_validate_running_set_problems():
    assert any("운전 중인 시리얼이 중복" in p for p in validate(Config(serials=[1, 2, 2])))
    assert any("운전 중인 시리얼이 비어" in p for p in validate(Config(serials=[])))
    assert any("플러그 MAC 형식" in p for p in validate(Config(plug_mac="nope")))


def test_validate_survives_garbage_sets():
    assert validate(Config(sets=[])) == ["세트가 하나도 없다"]
    assert any("객체가 아니다" in p for p in validate(Config(sets=["x"])))
    assert any("읽을 수 없다" in p for p in validate(Config(sets=[{"id": 1, "plug_mac": "AA:BB:CC:DD:EE:FF", "serials": "x-y"}])))
