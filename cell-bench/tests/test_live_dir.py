"""실시간 파일 자리(cellbench.control.live_dir) — 기본 data 폴더면 C:\\Dev 밖, 다른 폴더면 그 폴더 그대로 (2026-11-06 누수 시험)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench import control  # noqa: E402


def test_default_data_goes_outside_dev(tmp_path, monkeypatch):
    monkeypatch.delenv("CELLBENCH_LIVE_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = control.live_dir()
    assert d == tmp_path / "cellbench" / "live" and d.is_dir()


def test_other_data_stays_put(tmp_path, monkeypatch):
    monkeypatch.delenv("CELLBENCH_LIVE_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    assert control.live_dir(tmp_path) == tmp_path          # 시험의 tmp · 서버의 --data 는 그대로
    assert not (tmp_path / "la").exists()


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CELLBENCH_LIVE_DIR", str(tmp_path / "x"))
    assert control.live_dir() == tmp_path / "x" and (tmp_path / "x").is_dir()
