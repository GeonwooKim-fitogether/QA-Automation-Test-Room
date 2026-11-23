"""시험 전 저장량 확인(qc_round.storage_ready) — 셀 · 라이브 포트 없이 가짜 상태 응답과 가짜 받기로 본다(2026-11-23).
셀 저장이 가득 차면(약 243 MB, 추정) 측정과 실시간 데이터가 멈춘다. 11-20 19:45 뒤 70시간 셀 기록을 비우지 않은 채 시험을 시작하려다 셀이 빨간불로 깜빡였고,
그때 실행기는 회차가 끝날 때만 셀 기록을 받았다. 이제 실행기는 시작할 때 셀마다 저장 MB 를 묻고, 120 MB 이상이거나
앞에서 지우지 못한 셀(cells_not_cleared.json)은 먼저 받고 비우며, 그래도 남으면 시작하지 않는다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import qc_round as Q  # noqa: E402
import rail_cells as RC  # noqa: E402
from cellbench.cells import CellResult, PortBusy  # noqa: E402

MB = 1048576


def done(s: int, mb: float) -> CellResult:
    """다 받고 지운 결과."""
    return CellResult(s, "ip", size=int(mb * MB), got=int(mb * MB), ended=True, deleted=True)


def cut(s: int, mb: float) -> CellResult:
    """받기가 끊겨 지우지 못한 결과."""
    return CellResult(s, "ip", size=int(mb * MB), got=5 * MB, ended=False, deleted=False, error="받기 끊김")


@pytest.fixture
def cells(tmp_path, monkeypatch):
    """set_up(sizes, gone=, left=, got=) — sizes 는 실시간 데이터가 들어오는 셀의 상태 응답 {셀: MB, 못 읽으면 None}, gone 은 실시간 데이터가 끊긴 셀,
    left 는 앞에서 지우지 못한 셀(cells_not_cleared.json), got 은 받을 때 셀마다 돌려줄 결과(없으면 다 받고 지움)."""
    seen = {"reboot": [], "extract": [], "log": []}

    def set_up(sizes: dict, gone=(), left=(), got: dict | None = None):
        monkeypatch.setattr(RC, "NOT_CLEARED", tmp_path / "cells_not_cleared.json")
        if left:
            (tmp_path / "cells_not_cleared.json").write_text(json.dumps({str(s): {"size_mb": 150.0} for s in left}), encoding="utf-8")
        monkeypatch.setattr(RC, "serials", lambda name: sorted([*sizes, *gone]))

        def reboot(serials, say):
            seen["reboot"].append(sorted(serials))
            return {s: CellResult(s, "ip", size=None if v is None else int(v * MB), error="상태 응답 없음" if v is None else None)
                    for s, v in sizes.items()}

        def extract_cells(name, say, only=None):
            seen["extract"].append((name, list(only)))
            res = dict(got) if got is not None else {s: done(s, sizes.get(s) or 1.0) for s in only}
            return tmp_path / "ftg_y_20261123-090000", res

        monkeypatch.setattr(Q, "reboot", reboot)
        monkeypatch.setattr(RC, "extract_cells", extract_cells)
        return seen

    return set_up, seen


def say_to(seen):
    return seen["log"].append


def test_all_cells_below_the_line_clear_nothing_and_the_log_has_mb_per_cell(cells):
    set_up, seen = cells
    set_up({10680: 12.34, 10681: 119.9})
    doc = Q.storage_ready("Y-shift", say_to(seen))
    assert seen["reboot"] == [[10680, 10681]] and seen["extract"] == []
    assert doc["mb"] == {"10680": 12.3, "10681": 119.9} and doc["cleared"] == [] and doc["ftg"] is None
    line = next(m for m in seen["log"] if "셀마다 저장 MB" in m)
    assert line.startswith("저장량 확인") and "10680 12.3" in line and "10681 119.9" in line


def test_full_cells_and_cells_left_earlier_are_cleared_before_the_test(cells):
    """120 MB 이상인 셀과 앞에서 지우지 못한 셀만 받는다 — 나머지 셀의 기록은 그대로 둔다(--no-extract 로 이어 받는 경우)."""
    set_up, seen = cells
    set_up({10680: 12.0, 10681: 130.0, 10692: 40.0}, left=[10692])
    doc = Q.storage_ready("Y-shift", say_to(seen))
    assert seen["extract"] == [("Y", [10681, 10692])]
    assert doc["cleared"] == [10681, 10692] and doc["ftg"].endswith("ftg_y_20261123-090000")
    assert any("10681(130 MB)" in m and "10692(앞에서 지우지 못함)" in m for m in seen["log"])
    assert any(m.startswith("저장량 확인 끝 — 2대 받고 비움") for m in seen["log"])


def test_a_cell_still_left_after_clearing_stops_the_test(cells):
    set_up, seen = cells
    set_up({10680: 12.0, 10681: 158.0}, got={10681: cut(10681, 158.0)})   # 한 번 더 받아도 끊겼다
    with pytest.raises(SystemExit, match="10681"):
        Q.storage_ready("Y", say_to(seen))
    assert any("받고 비운 뒤에도 기록이 남은 셀 [10681]" in m and "시험을 시작하지 않는다" in m for m in seen["log"])   # 실행 기록에 한 줄


def test_a_cell_that_cannot_be_reached_for_clearing_stops_the_test(cells):
    """상태를 물은 뒤 측정으로 돌아오지 않아 받을 때 실시간 데이터가 끊긴 셀도 남은 셀이다."""
    set_up, seen = cells
    set_up({10681: 130.0}, got={})                                          # 받기 결과에 10681 이 없다
    with pytest.raises(SystemExit, match="10681"):
        Q.storage_ready("Y", say_to(seen))


def test_a_cell_whose_status_could_not_be_read_is_cleared_too(cells):
    set_up, seen = cells
    set_up({10680: None, 10681: 10.0})
    doc = Q.storage_ready("Y", say_to(seen))
    assert seen["extract"] == [("Y", [10680])] and doc["mb"]["10680"] is None
    assert any("10680(상태 못 읽음)" in m for m in seen["log"])


def test_cells_without_live_data_are_only_written_down(cells):
    """실시간 데이터가 끊긴 셀(꺼짐 · 독 밖 · 가득 참)은 저장량을 몰라 적기만 한다 — 독 밖 셀로 시험이 막히지 않게.
    가득 찬 셀도 실시간 데이터가 끊기므로(2026-11-23 관찰), 적는 줄에 가득 참 가능성과 할 일을 함께 쓴다."""
    set_up, seen = cells
    set_up({10680: 12.0}, gone=[10694], left=[10694])
    doc = Q.storage_ready("Y", say_to(seen))
    assert seen["extract"] == [] and doc["no_live"] == [10694]
    line = next(m for m in seen["log"] if "실시간 데이터가 끊긴 셀 [10694]" in m)
    assert "앞에서 지우지 못한 셀 [10694]" in line and "가득 차(약 243 MB)" in line and "빨간불" in line and "껐다 켜" in line


def test_the_firmware_that_does_not_delete_is_not_extracted_again_and_does_not_stop(cells):
    """10677(지우기에 응답하지 않는 펌웨어)은 받아도 남는다 — 시험마다 같은 기록을 다시 받지 않고 경고만 적는다."""
    set_up, seen = cells
    set_up({10677: 130.0, 10680: 12.0})
    doc = Q.storage_ready("Y", say_to(seen))
    assert seen["extract"] == [] and doc["cleared"] == []
    assert any("[10677] 는 지우기에 응답하지 않는 펌웨어라 비울 수 없다" in m for m in seen["log"])


def test_a_busy_live_port_stops_with_one_line(cells, monkeypatch):
    set_up, seen = cells
    set_up({10680: 12.0})

    def busy(serials, say):
        raise PortBusy("60222")

    monkeypatch.setattr(Q, "reboot", busy)
    with pytest.raises(SystemExit, match="라이브 포트"):
        Q.storage_ready("Y", say_to(seen))
    assert any("라이브 포트를 다른 프로그램이 쓰고 있어" in m for m in seen["log"])


# --- rail_cells.extract_cells(only=...) — 고른 셀만 받고, 셀마다 결과를 돌려준다 ---

class FakeLive:
    def __init__(self, cfg): pass
    def start(self): pass
    def stop(self): pass
    def alive(self): return [10680, 10692, 10693]


def test_extract_cells_takes_only_the_chosen_cells_with_live_data(tmp_path, monkeypatch):
    asked = []

    class FakeLink:
        def __init__(self, cfg, lv, log=None): pass
        def extract(self, serials, out):
            asked.append(sorted(serials))
            out.mkdir(parents=True, exist_ok=True)
            return {s: done(s, 130.0) for s in serials}

    monkeypatch.setattr(RC, "DATA", tmp_path)
    monkeypatch.setattr(RC, "NOT_CLEARED", tmp_path / "cells_not_cleared.json")
    monkeypatch.setattr(RC, "LiveListener", FakeLive)
    monkeypatch.setattr(RC, "CellLink", FakeLink)
    monkeypatch.setattr(RC, "serials", lambda name: [10680, 10692, 10693, 10694])
    monkeypatch.setattr(RC.time, "sleep", lambda s: None)
    (tmp_path / "cells_not_cleared.json").write_text(json.dumps({"10692": {}, "10694": {}}), encoding="utf-8")
    out, res = RC.extract_cells("Y", say=lambda m: None, only=[10692, 10694])
    assert asked == [[10692]] and list(res) == [10692]                       # 10694 는 실시간 데이터가 끊겨 받지 않는다
    assert list(RC.not_cleared()) == ["10694"]                              # 받은 10692 는 지우고, 받지 못한 10694 는 남긴다
    assert json.loads((out / "extract_result.json").read_text(encoding="utf-8"))[0]["serial"] == 10692
