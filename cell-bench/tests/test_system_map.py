"""시스템 지도(docs/system-map.md)가 코드와 어긋나지 않았는지 — 이름만 대조한다.

지도는 사람이 쓰는 글이라 뜻이 바뀐 것까지는 기계가 잡지 못한다. 대신 구조의 뼈대(모듈 · 사이클 단계 · 엔진이 끝나는 이유 ·
신호등 차선 · 원격 명령)의 이름이 지도에 없으면 실패해, 구조를 바꾸는 PR 에서 지도도 함께 고치게 한다.
실패하면 그 이름이 무엇을 하는지 docs/system-map.md 의 알맞은 장에 적는다.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench.alert import KEY_LABELS
from cellbench.control import COMMANDS
from cellbench.cycle import PHASES
from cellbench.health import LANE_KEYS
from cellbench.supervisor import EXIT_VERDICT

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "docs" / "system-map.md"
TEXT = MAP.read_text(encoding="utf-8")
FIX = "docs/system-map.md 에 적는다 (구조를 바꾸면 지도도 같은 PR 에서 고친다)"


def _missing(names) -> list[str]:
    return [n for n in names if n not in TEXT]


def test_every_module_is_on_the_map():
    mods = [p.name for p in sorted((ROOT / "cellbench").glob("*.py")) if p.name != "__init__.py"]
    mods += [p.name for p in sorted(ROOT.glob("*.py"))]                    # 진입점 셋
    assert not _missing(mods), f"지도에 없는 모듈: {_missing(mods)} — {FIX}"


def test_every_cycle_phase_is_on_the_map():
    phases = PHASES + ["RECOVER", "DONE", "STOPPED"]
    assert not _missing(phases), f"지도에 없는 사이클 단계: {_missing(phases)} — {FIX}"


def test_every_engine_exit_is_on_the_map():
    exits = [f"`{k}`" for k in list(EXIT_VERDICT) + ["no_cells"]]          # 2.4 끝나는 길의 표
    assert not _missing(exits), f"지도의 '끝나는 길'에 없는 exit: {_missing(exits)} — {FIX}"


def test_every_signal_lane_is_on_the_map():
    lanes = [KEY_LABELS[k] for k in LANE_KEYS]
    assert not _missing(lanes), f"지도의 '신호등'에 없는 차선: {_missing(lanes)} — {FIX}"


def test_every_remote_command_is_on_the_map():
    cmds = [f"`{k}`" for k in COMMANDS]
    assert not _missing(cmds), f"지도의 '원격 명령'에 없는 명령: {_missing(cmds)} — {FIX}"


def test_map_states_its_base_commit():
    assert re.search(r"\*\*기준:\*\* main `[0-9a-f]{7,}`", TEXT), "지도 머리말에 '기준: main `커밋`' 줄이 있어야 한다"
