"""입회 표지 쓰기(attend.make_marker) — 명령 창과 입회 창이 같은 함수를 쓴다. 장비 없이 돈다."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import attend as A  # noqa: E402


def test_make_marker_writes_same_shape(tmp_path):
    m = tmp_path / "motion_attended"
    until = A.make_marker(" 김건우 ", 90, m)
    d = json.loads(m.read_text(encoding="utf-8"))
    assert d["by"] == "김건우" and d["until"] == until and abs(until - time.time() - 5400) < 5
    assert "90분" in d["reason"]


@pytest.mark.parametrize("by, minutes", [("", 90), ("  ", 90), ("a", 5), ("a", 601)])
def test_make_marker_refuses(tmp_path, by, minutes):
    m = tmp_path / "motion_attended"
    with pytest.raises(ValueError):
        A.make_marker(by, minutes, m)
    assert not m.exists()
