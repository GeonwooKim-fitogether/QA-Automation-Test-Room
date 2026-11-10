"""archive_raw — 오래된 원값만 묶고, 확인한 뒤에만 지우고, 다시 풀면 같은 바이트가 돌아오는가."""
import os
import sys
import time
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import archive_raw as A  # noqa: E402

OLD = time.time() - 30 * 86400


def _make(data: Path) -> dict[str, bytes]:
    """오래된 셀 기록 폴더 · 라이브 원값 하나씩과 최근 것 하나씩."""
    files = {
        "ftg_y_20261009-181416/ftg_10679_20261009_181417.bin": os.urandom(5000),
        "ftg_y_20261009-181416/extract_result.json": b'{"ok": true}',
        "live_raw/20261009-120000.bin": b"\x00" * 20000,
        "ftg_x_20261109-181416/ftg_10700_20261109_181417.bin": b"new",
        "live_raw/20261109-120000.bin": b"new live",
        "protocol_20261009-170403.json": b"{}",
    }
    for rel, b in files.items():
        p = data / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)
        if "20261009" in rel:
            os.utime(p, (OLD, OLD))
    return files


@pytest.fixture
def env(tmp_path, monkeypatch):
    data, drive = tmp_path / "data", tmp_path / "drive"
    drive.mkdir()
    monkeypatch.setenv("RAIL_ARCHIVE_DIR", str(drive / "레일 시험 원값"))
    return data, data / "archive", drive / "레일 시험 원값", _make(data)


def test_묶을_대상은_오래된_원값만(env):
    data, *_ = env
    got = A.candidates(data, keep_days=7)
    assert list(got) == ["20261009"]
    assert sorted(p.name for p in got["20261009"]) == ["20261009-120000.bin", "ftg_y_20261009-181416"]


def test_묶고_드라이브로_옮기고_원본을_지운다(env):
    data, local, drive, files = env
    assert A.run(keep_days=7, data=data, local=local, say=lambda s: None) == 1
    assert not (data / "ftg_y_20261009-181416").exists()
    assert not (data / "live_raw/20261009-120000.bin").exists()
    assert (data / "ftg_x_20261109-181416").exists() and (data / "protocol_20261009-170403.json").exists()
    assert not list(local.glob("*.zip"))
    z = drive / "rail_raw_20261009.zip"
    with zipfile.ZipFile(z) as f:
        assert sorted(f.namelist()) == sorted(r for r in files if r.startswith(("ftg_y", "live_raw/20261009")))


def test_다시_풀면_같은_바이트(env):
    data, local, _, files = env
    A.run(keep_days=7, data=data, local=local, say=lambda s: None)
    assert A.restore("20261009", data=data, local=local, say=lambda s: None) == 1
    for rel, b in files.items():
        assert (data / rel).read_bytes() == b


def test_확인이_틀리면_원본을_남긴다(env, monkeypatch):
    data, local, drive, _ = env
    monkeypatch.setattr(A, "verify", lambda z, files: "일부러 틀림")
    assert A.run(keep_days=7, data=data, local=local, say=lambda s: None) == 0
    assert (data / "ftg_y_20261009-181416").exists() and (data / "live_raw/20261009-120000.bin").exists()
    assert not list(local.glob("*.zip*")) and not list(drive.glob("*.zip"))


def test_드라이브가_없으면_PC_에_두었다가_나중에_옮긴다(env, monkeypatch):
    data, local, drive, _ = env
    monkeypatch.setattr(A, "drive_dir", lambda: None)
    A.run(keep_days=7, data=data, local=local, say=lambda s: None)
    assert [z.name for z in local.glob("*.zip")] == ["rail_raw_20261009.zip"]
    monkeypatch.setattr(A, "drive_dir", lambda: drive)
    assert A.ship(local, say=lambda s: None) == 1
    assert (drive / "rail_raw_20261009.zip").exists() and not list(local.glob("*.zip"))


def test_공간이_충분하면_묶지_않는다(env, monkeypatch):
    data, local, *_ = env
    monkeypatch.setattr(A, "free_gb", lambda path=None: 100.0)
    assert A.run(keep_days=7, until_free=50, data=data, local=local, say=lambda s: None) == 0
    assert (data / "ftg_y_20261009-181416").exists()
