"""모터 1 축 정의(방향 · Home 쪽)와 그 인터록 — 장비 없이 돈다. 진짜 pysoem 은 가져오지 않는다.

1) motionbench.axis 의 순수 판정 — load · problem · first_move_words.
2) run_motion.move · main 이 그 판정으로 입회 표지 검사와 어댑터 열기 전에 거부하는지(move · hold · slow), 통과하면 방향을 먼저
   말하는지, scan · monitor 는 축 정의를 보지 않는지 — test_motion_m2_modes 의 가짜 pysoem · 가짜 장비(rm · wire)를 그대로 쓴다.
축 정의 · 승인 · 입회 표지 · data 는 전부 tmp_path 안이다. 진짜 dynamic-bench/config/axis-motor1.json 은 맨 끝의 '깨지지 않았나'
검사에서만 읽는다 — home_side 는 사람이 현장에서 채우면 바뀌므로 동작 시험의 재료로 쓰지 않는다.
"""
from __future__ import annotations

import json
import signal
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from motionbench import axis, m2  # noqa: E402
from test_motion_m2 import GOOD, _marker  # noqa: E402
import test_motion_m2_modes as mm  # noqa: E402
from test_motion_m2_modes import AXIS_GOOD, D, P0, W, FakeMaster, Keys, run_move, wire  # noqa: E402
from test_motion_m2_modes import rm  # noqa: E402,F401 — fixture: 가짜 pysoem 을 끼운 run_motion (AXIS 는 tmp_path 의 AXIS_GOOD)

REAL_AXIS = HERE.parents[1] / "dynamic-bench" / "config" / "axis-motor1.json"
WORDS = "Home 에서 멀어지는 쪽(왼쪽)으로 10 mm (+)"          # AXIS_GOOD(+ 왼쪽 · Home 오른쪽) × GOOD(+1 · 10 mm)
TOWARD = "— 첫 이동은 Home 에서 멀어지는 쪽이어야 한다"
FIELDS = ("viewpoint", "plus_direction", "home_side", "confirmed_by", "confirmed_at")
GONE = object()                                               # _doc 에서 그 키를 뺀다


def _doc(**change) -> dict:
    """AXIS_GOOD 에서 몇 칸만 바꾼 축 정의."""
    d = dict(AXIS_GOOD)
    for k, v in change.items():
        if v is GONE:
            d.pop(k, None)
        else:
            d[k] = v
    return d


def _put(path: Path, doc, tmp_path: Path) -> Path:
    assert tmp_path in path.parents, path                     # 진짜 axis-motor1.json · 승인 파일은 절대 쓰지 않는다
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------- ① load — 없음 · 깨짐 · 객체 아님

def test_load_gives_reason_for_missing_broken_or_non_object(tmp_path):
    assert axis.load(tmp_path / "axis-motor1.json") == (None, "파일 없음")
    for i, text in enumerate(('{"home_side": 오른쪽}', '{"home_side": "오른쪽",}', "")):   # 따옴표 빠짐 · 끝 쉼표 · 빈 파일
        doc, why = axis.load(_put(tmp_path / f"broken{i}.json", text, tmp_path))
        assert doc is None and why.startswith("못 읽음 JSONDecodeError: "), (text, why)
    for i, text in enumerate(("[]", '"왼쪽"', "null", "3")):
        assert axis.load(_put(tmp_path / f"not-object{i}.json", text, tmp_path)) == (None, "JSON 객체가 아니다"), text
    ansi = tmp_path / "ansi.json"                             # 메모장 'ANSI'(CP949) 로 저장 — UTF-8 이 아니면 못 읽음으로 거부
    ansi.write_bytes(json.dumps(AXIS_GOOD, ensure_ascii=False).encode("cp949"))
    doc, why = axis.load(ansi)
    assert doc is None and why.startswith("못 읽음 UnicodeDecodeError"), why


def test_load_reads_utf8_with_or_without_bom(tmp_path):
    assert axis.load(_put(tmp_path / "plain.json", AXIS_GOOD, tmp_path)) == (AXIS_GOOD, None)
    bom = tmp_path / "bom.json"                               # 메모장이 BOM 을 붙여 저장해도 읽힌다 (utf-8-sig)
    bom.write_bytes(b"\xef\xbb\xbf" + json.dumps(AXIS_GOOD, ensure_ascii=False).encode("utf-8"))
    assert axis.load(bom) == (AXIS_GOOD, None)


# ---------------------------------------------------------------- ② problem — 칸마다 맞는 거부 이유

@pytest.mark.parametrize("doc", [None, [], "왼쪽", 3], ids=["null", "list", "str", "int"])
def test_problem_refuses_non_object(doc):
    assert axis.problem(doc, 1) == "JSON 객체가 아니다"


def test_problem_refuses_empty_viewpoint():
    for v in ("", "   ", None, GONE, 3):
        assert axis.problem(_doc(viewpoint=v), 1) == "viewpoint 가 비었다 — 어느 쪽에서 본 왼쪽 · 오른쪽인지 모른다", v


def test_problem_refuses_plus_direction_other_than_left_right():
    for v in ("left", "왼", "왼쪽 ", "+", "", None, 1):
        assert axis.problem(_doc(plus_direction=v), 1) == f'plus_direction {v!r} — "왼쪽" 또는 "오른쪽" 이어야 한다', v
    assert axis.problem(_doc(plus_direction=GONE), 1) == 'plus_direction None — "왼쪽" 또는 "오른쪽" 이어야 한다'


def test_problem_refuses_unconfirmed_home():
    """home_side 가 null · 없음이면 어느 방향이든 거부. 셋째 = 오늘의 진짜 파일 모양(home_side · confirmed_* 가 null) —
    confirmed_by 가 아니라 'Home 쪽이 확인되지 않았다'(사람이 할 일)를 말해야 한다."""
    for change in ({"home_side": None}, {"home_side": GONE}, {"home_side": None, "confirmed_by": None, "confirmed_at": None}):
        for d in (1, -1):
            why = axis.problem(_doc(**change), d)
            assert why.startswith("Home 쪽이 확인되지 않았다 — ") and "home_side · confirmed_by · confirmed_at" in why, (change, d, why)


def test_problem_refuses_home_side_other_than_left_right():
    for v in ("left", "", "오른쪽 ", "Home", 0, False):
        assert axis.problem(_doc(home_side=v), 1) == f'home_side {v!r} — "왼쪽" 또는 "오른쪽" 이어야 한다', v


def test_problem_refuses_empty_confirmed_by():
    for v in ("", "  ", None, GONE, 7):
        assert axis.problem(_doc(confirmed_by=v), 1) == "confirmed_by 가 비었다", v


def test_problem_refuses_confirmed_at_without_timezone():
    for v in ("2026-10-27T15:00:00", "2026-10-27"):
        assert axis.problem(_doc(confirmed_at=v), 1) == "confirmed_at 에 시간대가 없다", v


def test_problem_refuses_confirmed_at_not_iso8601():
    for v in ("어제", "2026/10/27 15:00", "", None, 20261027):
        assert axis.problem(_doc(confirmed_at=v), 1) == f"confirmed_at {v!r} 가 ISO8601 이 아니다", v
    assert axis.problem(_doc(confirmed_at=GONE), 1) == "confirmed_at None 가 ISO8601 이 아니다"


def test_problem_accepts_iso8601_with_timezone():
    for v in ("2026-10-27T15:00:00+09:00", "2026-10-27 15:00+09:00", "2026-10-27T06:00:00+00:00"):
        assert axis.problem(_doc(confirmed_at=v), 1) is None, v


def test_problem_reports_the_first_problem_in_order():
    """여러 칸이 틀리면 앞의 것 하나 — viewpoint → plus_direction → home_side → confirmed_by → confirmed_at → 방향."""
    assert axis.problem({}, 1).startswith("viewpoint 가 비었다")
    assert axis.problem(_doc(plus_direction="left", home_side=None), 1).startswith("plus_direction 'left'")
    assert axis.problem(_doc(home_side="left", confirmed_by=""), 1).startswith("home_side 'left'")
    assert axis.problem(_doc(confirmed_by=None, confirmed_at=None), 1) == "confirmed_by 가 비었다"   # home_side 만 채우고 나머지를 잊음
    assert axis.problem(_doc(confirmed_at="어제", home_side="왼쪽"), 1).startswith("confirmed_at '어제'")   # Home 쪽으로 가는 것보다 앞


def _ids(rows):
    return [f"home-{'L' if h == '왼쪽' else 'R'}_plus-{'L' if p == '왼쪽' else 'R'}_{'+' if d == 1 else '-'}" for h, p, d in rows]


TOWARD_HOME = [("왼쪽", "왼쪽", 1), ("오른쪽", "오른쪽", 1), ("오른쪽", "왼쪽", -1), ("왼쪽", "오른쪽", -1)]
AWAY_FROM_HOME = [("오른쪽", "왼쪽", 1), ("왼쪽", "왼쪽", -1), ("왼쪽", "오른쪽", 1), ("오른쪽", "오른쪽", -1)]


@pytest.mark.parametrize("home, plus, d", TOWARD_HOME, ids=_ids(TOWARD_HOME))
def test_problem_refuses_first_move_toward_home(home, plus, d):
    """첫 이동이 가는 쪽 = + 면 plus_direction, - 면 그 반대. 그 쪽이 Home 이면 거부."""
    sign = "+" if d == 1 else "-"
    why = axis.problem(_doc(home_side=home, plus_direction=plus), d)
    assert why.startswith(f"첫 이동({sign})이 Home 쪽({home})으로 간다 {TOWARD}"), why
    assert f"first_move_dir 를 {-d} 로" in why, why          # 고치는 길은 반대 방향의 새 승인 — 부호가 거꾸로면 같은 잘못을 또 시킨다


@pytest.mark.parametrize("home, plus, d", AWAY_FROM_HOME, ids=_ids(AWAY_FROM_HOME))
def test_problem_passes_first_move_away_from_home(home, plus, d):
    assert axis.problem(_doc(home_side=home, plus_direction=plus), d) is None


def test_first_move_words():
    assert axis.first_move_words(AXIS_GOOD, 1, 10) == WORDS
    assert axis.first_move_words(_doc(home_side="왼쪽"), -1, 10) == "Home 에서 멀어지는 쪽(오른쪽)으로 10 mm (-)"
    assert axis.first_move_words(AXIS_GOOD, 1, 2.5) == "Home 에서 멀어지는 쪽(왼쪽)으로 2.5 mm (+)"
    # 거부되는 정의 — "Home 에서 멀어지는" 을 붙이지 않고 가는 쪽만 말한다
    assert axis.first_move_words(_doc(home_side="왼쪽"), 1, 10) == "왼쪽으로 10 mm (+)"            # Home 쪽으로 간다
    assert axis.first_move_words(_doc(home_side=None), 1, 10) == "왼쪽으로 10 mm (+)"              # 확인 안 됨
    assert axis.first_move_words(_doc(home_side=None), -1, 10) == "오른쪽으로 10 mm (-)"
    assert axis.first_move_words(_doc(confirmed_by=""), 1, 10) == "왼쪽으로 10 mm (+)"             # 쪽은 맞아도 확인한 사람이 없다


def test_problem_refuses_list_or_object_values_instead_of_raising():
    """손 편집으로 "home_side": ["오른쪽"] 처럼 목록 · 객체를 적어도 거부 '이유'가 나와야 한다 — 예외가 아니라.
    move() 가 같은 경우에 거부 이유로 끝나고 어댑터를 열지 않는 것은 test_list_value_in_axis_file_refused_cleanly_before_adapter 가 본다."""
    got = {}
    for field, value in (("plus_direction", ["왼쪽"]), ("plus_direction", {"쪽": "왼쪽"}), ("home_side", ["오른쪽"]), ("home_side", {})):
        try:
            why = axis.problem(_doc(**{field: value}), 1)
        except Exception as e:
            why = f"예외 {type(e).__name__}: {e}"
        got[(field, repr(value))] = why
    bad = {k: v for k, v in got.items() if not (isinstance(v, str) and v.startswith(f"{k[0]} "))}
    assert not bad, bad


# ---------------------------------------------------------------- ③ run_motion.move — 표지 · 어댑터 전에 거부

def _axis_file(rm, tmp_path) -> Path:
    assert rm.AXIS == tmp_path / "axis-motor1.json" and rm.AXIS != REAL_AXIS    # fixture 가 tmp 로 바꿔 끼웠다
    return rm.AXIS


def _breach_stops(rm, monkeypatch) -> None:
    """축 정의 검사가 뚫려 어댑터까지 오더라도 움직이기 전에 끝나게 — bring_up 이 바로 멈춘다."""
    def bring_up(m, info):
        raise rm.ecat.Stop("축 정의 검사를 지나 어댑터까지 왔다")
    monkeypatch.setattr(rm.ecat, "bring_up", bring_up)


BAD_AXIS = {    # 사례 → (축 정의 파일 내용 · None 이면 파일 없음, 거부 이유의 앞부분)
    "missing": (None, "파일 없음"),
    "broken": ('{"home_side": 오른쪽}', "못 읽음 JSONDecodeError"),
    "unconfirmed": (_doc(home_side=None, confirmed_by=None, confirmed_at=None), "Home 쪽이 확인되지 않았다 — "),
}   # "Home 쪽으로 가는 첫 이동" 은 2026-10-28 부터 거부 사유가 아니다 — 레일 한계(motionbench/rail.py)가 판정한다


@pytest.mark.parametrize("case", list(BAD_AXIS))
@pytest.mark.parametrize("mode", ["move", "hold", "slow"])
def test_bad_axis_file_refused_before_marker_and_adapter(rm, monkeypatch, tmp_path, capsys, mode, case):
    _marker(tmp_path)                                         # 표지는 유효하다 — 막는 것은 축 정의여야 한다
    box = wire(rm, monkeypatch)
    _breach_stops(rm, monkeypatch)
    path, (content, says) = _axis_file(rm, tmp_path), BAD_AXIS[case]
    if content is None:
        path.unlink()
    else:
        _put(path, content, tmp_path)
    m, info = FakeMaster(), {}
    reason = rm.move(m, info, mode, 0.5, 2.0)
    assert reason.startswith(f"거부 — 축 정의: {says}") and reason.endswith(f" ({path})"), reason
    assert m.opens == [] and "marker_start" not in info and "axis" not in info and "cyc" not in box, info
    assert info["approval"] == GOOD                           # 승인은 지났다 — 그 다음 축 정의에서 멈췄다
    out = capsys.readouterr().out
    assert "[PLAN]" not in out and "입회 표지 검사" not in out, out   # 확인 안 된 방향을 화면에 말하지 않는다


@pytest.mark.parametrize("mode", ["move", "hold", "slow"])
def test_confirmed_axis_states_direction_before_marker_check(rm, monkeypatch, tmp_path, capsys, mode):
    box = wire(rm, monkeypatch)                               # 표지 파일이 없다 — 축 정의를 지나 표지 검사에서 멈춘다
    path = _axis_file(rm, tmp_path)
    m, info = FakeMaster(), {}
    reason = rm.move(m, info, mode, 0.5, 2.0)
    assert reason == "입회 표지 무효 — 서보 온 거부: 파일 없음", reason
    assert info["axis"] == {"path": str(path), **{k: AXIS_GOOD[k] for k in FIELDS}, "words": WORDS}
    assert info["marker_start"]["valid"] is False and m.opens == [] and "cyc" not in box
    lines = capsys.readouterr().out.splitlines()
    plan = [i for i, s in enumerate(lines) if s.startswith("[PLAN]")]
    mark = [i for i, s in enumerate(lines) if "입회 표지 검사" in s]
    assert len(plan) == len(mark) == 1 and plan[0] < mark[0], lines
    assert lines[plan[0]] == f"[PLAN] 방향: {WORDS} — 반대로 가면 바로 Ctrl+C"


def test_first_move_toward_home_is_judged_by_rail_not_axis(rm, monkeypatch, tmp_path):
    """2026-10-28 — Home 쪽 첫 이동을 축 정의가 막지 않는다. 축 정의는 방향 말만 정하고, 끝까지의 거리는 레일 한계가 본다."""
    wire(rm, monkeypatch)
    _put(rm.APPROVAL, {**GOOD, "first_move_dir": -1}, tmp_path)
    m, info = FakeMaster(), {}
    reason = rm.move(m, info, "move")                         # AXIS_GOOD 는 Home 오른쪽 — -1 의 첫 이동이 Home 쪽이다
    assert reason == "입회 표지 무효 — 서보 온 거부: 파일 없음", reason   # 축 정의를 지나 표지에서 멈췄다
    assert info["axis"]["words"] == "오른쪽으로 10 mm (-)" and m.opens == []


@pytest.mark.parametrize("mode", ["move", "hold", "slow"])
def test_main_refuses_unconfirmed_axis_and_records_why(rm, monkeypatch, tmp_path, mode):
    """명령줄 입구(main)에서도 같은 거부가 결과 파일에 남는다 — 오늘의 진짜 파일 모양(home_side · confirmed_* 가 null)을 tmp 에 만든다."""
    _marker(tmp_path)
    wire(rm, monkeypatch)
    _breach_stops(rm, monkeypatch)
    _put(_axis_file(rm, tmp_path), _doc(home_side=None, confirmed_by=None, confirmed_at=None), tmp_path)
    monkeypatch.setattr(rm.proc, "safe_stdio", lambda: None)  # pytest 의 화면 포착을 건드리지 않게
    monkeypatch.setattr(sys, "argv", ["run_motion.py", mode])
    sw, before = sys.getswitchinterval(), signal.getsignal(signal.SIGINT)
    try:
        code = rm.main()
    finally:
        sys.setswitchinterval(sw)
    found = list(rm.DATA.glob("motion_m2*.json"))
    assert code == 1 and len(found) == 1, found
    doc = json.loads(found[0].read_text(encoding="utf-8"))
    assert doc["exit"].startswith("거부 — 축 정의: Home 쪽이 확인되지 않았다") and doc["mode"] == mode, doc["exit"]
    assert "axis" not in doc and "marker_start" not in doc
    assert [mm.opens for mm in FakeMaster.made] == [[]] and signal.getsignal(signal.SIGINT) == before


def test_list_value_in_axis_file_refused_cleanly_before_adapter(rm, monkeypatch, tmp_path):
    """손 편집으로 "home_side": ["오른쪽"] 이 와도 예외(TypeError: unhashable)가 move() 밖으로 새지 않고 거부 이유로 끝난다.
    어댑터는 열리지 않는다 — 2026-10-27 15:08 고침(axis.SIDES)의 회귀 시험."""
    _marker(tmp_path)
    box = wire(rm, monkeypatch)
    _breach_stops(rm, monkeypatch)
    path = _put(_axis_file(rm, tmp_path), _doc(home_side=["오른쪽"]), tmp_path)
    m, info = FakeMaster(), {}
    reason = rm.move(m, info, "move")
    assert reason == f"거부 — 축 정의: home_side ['오른쪽'] — \"왼쪽\" 또는 \"오른쪽\" 이어야 한다 ({path})", reason
    assert m.opens == [] and "marker_start" not in info and "axis" not in info and "cyc" not in box


# ---------------------------------------------------------------- ④ 확인된 축 정의로 끝까지 — 정상 종료 · 방향 안내

def test_move_full_run_with_confirmed_axis_ends_normally(rm, monkeypatch, tmp_path, capsys):
    _marker(tmp_path)
    box = wire(rm, monkeypatch)
    reason, info, m, took, before = run_move(rm, "move")
    c = box["cyc"]
    assert reason == rm.DONE_M2, (reason, info.get("alarms"))
    assert [g["target"] for g in info["legs"]] == [P0 + D, P0] * 3 and "qs_at" in info["legs"][4] and info["resume"]
    assert c.cws[-3:] == [m2.QUICK_STOP, m2.SHUTDOWN, 0] and info["final"]["state"] == "SWITCH_ON_DISABLED" and not c.bad
    assert info["axis"]["words"] == WORDS and m.opens == ["bogus-no-adapter"] and info["closed"] == "INIT"
    assert signal.getsignal(signal.SIGINT) == before
    lines = capsys.readouterr().out.splitlines()
    plan = [i for i, s in enumerate(lines) if s == f"[PLAN] 방향: {WORDS} — 반대로 가면 바로 Ctrl+C"]
    mark = next(i for i, s in enumerate(lines) if "입회 표지 검사" in s)
    servo = next(i for i, s in enumerate(lines) if "서보 온 (Operation enabled)" in s)
    first_leg = next(i for i, s in enumerate(lines) if '"cycle": 1' in s)
    assert len(plan) == 2 and plan[0] < mark < servo < plan[1] < first_leg, lines   # 서보 온 뒤 · 첫 이동 전에 한 번 더


@pytest.mark.parametrize("mode", ["hold", "slow"])
def test_attended_plan_and_prompt_say_the_direction(rm, monkeypatch, tmp_path, capsys, mode):
    _marker(tmp_path)
    box = wire(rm, monkeypatch)
    monkeypatch.setattr(rm, "_enter_key", Keys(None))         # Enter 를 누르지 않는다 — 첫 기다림이 시간으로 끝난다
    reason, info, m, took, before = run_move(rm, mode, hold_s=0.5)
    c, out = box["cyc"], capsys.readouterr().out
    assert c.cws[-3:] == [m2.QUICK_STOP, m2.SHUTDOWN, 0] and info["final"]["state"] == "SWITCH_ON_DISABLED" and not c.bad
    plan = [s for s in out.splitlines() if s.startswith("[PLAN] p0 ")]
    assert len(plan) == 1 and f" · 방향: {WORDS} · " in plan[0] and plan[0].endswith("— 반대로 가면 바로 Ctrl+C"), plan
    if mode == "hold":
        assert reason == "유지 시간 다 됨 — +10 mm 자리에서 움직이지 않고 끝냄", reason
        assert f"[MEASURE] ① 슬라이더가 {WORDS} 갔나요? ② 지금 +10 mm 위치" in out, out
    else:
        assert reason == "시작 대기 시간 다 됨 — 움직이지 않고 끝냄" and info["legs"] == [], reason


# ---------------------------------------------------------------- ⑤ scan · monitor 는 축 정의를 보지 않는다

@pytest.mark.parametrize("argv", [["scan"], ["monitor", "--minutes", "0.01"]], ids=["scan", "monitor"])
def test_scan_monitor_never_read_axis_file(rm, monkeypatch, tmp_path, argv):
    wire(rm, monkeypatch)
    _put(_axis_file(rm, tmp_path), _doc(home_side=None, confirmed_by=None, confirmed_at=None), tmp_path)   # 오늘처럼 확인 안 됨
    read = []

    def tripwire(*a, **k):
        read.append(a)
        raise AssertionError("scan · monitor 가 축 정의를 읽었다")
    monkeypatch.setattr(rm.axis, "load", tripwire)
    monkeypatch.setattr(rm.axis, "problem", tripwire)

    def bring_up(m, info):
        raise rm.ecat.Stop("fake")
    monkeypatch.setattr(rm.ecat, "bring_up", bring_up)
    monkeypatch.setattr(rm.proc, "safe_stdio", lambda: None)
    monkeypatch.setattr(sys, "argv", ["run_motion.py", *argv])
    sw = sys.getswitchinterval()
    try:
        code = rm.main()
    finally:
        sys.setswitchinterval(sw)
    assert code == 1 and read == []
    doc = json.loads((rm.DATA / "drives.json").read_text(encoding="utf-8"))
    assert doc["exit"] == "fake" and doc["mode"] == argv[0] and doc["closed"] == "INIT" and "axis" not in doc
    assert [mm.opens for mm in FakeMaster.made] == [["bogus-no-adapter"]]   # 축 정의와 상관없이 어댑터까지 간다


# ---------------------------------------------------------------- ⑦ 시작 위치 — 원점(전원을 넣은 자리)에서 2 mm 안이어야 움직인다

FAR = ("시작 위치 p0 {p0} pulse 가 원점(전원을 넣은 자리)에서 {mm} mm — 2 mm 밖이다. "
       "서보가 꺼진 채 슬라이더를 원점(Home)으로 되돌려 scan 으로 확인하거나, 확인된 Home 끝에서 전원을 다시 넣은 뒤 돈다")


def test_start_problem_boundaries():
    assert axis.ORIGIN_TOL == 2000
    for p0 in (0, 2000, -2000, 1, -1999):
        assert axis.start_problem(p0) is None, p0
    for p0, mm in ((2001, "+2.001"), (-2001, "-2.001"), (9566, "+9.566"), (-5067, "-5.067")):
        assert axis.start_problem(p0) == FAR.format(p0=p0, mm=mm), p0


def _bring_up_at(rm, monkeypatch, p0: int) -> None:
    """wire 의 가짜 bring_up 그대로, 시작 위치만 p0 로 — 앞 실행이 옮겨 놓은 자리에서 전원이 켜진 채 다시 도는 날을 흉내 낸다."""
    fake = rm.ecat.bring_up
    monkeypatch.setattr(rm.ecat, "bring_up", lambda m, info: (fake(m, info)[0], p0))


def test_start_far_from_origin_runs_when_rail_known(rm, monkeypatch, tmp_path):
    """2026-10-28 — 원점(전원을 넣은 자리) 2 mm 검사는 없다. 같은 전원의 레일 파일이 있으면 어디서 출발하든 레일 한계 안이면 돈다."""
    _marker(tmp_path)
    box = wire(rm, monkeypatch)
    _bring_up_at(rm, monkeypatch, 9566)
    reason, info, m, took, before = run_move(rm, "move")
    assert reason == rm.DONE_M2, (reason, info.get("checks", {}).get("result"), info.get("alarms"))
    assert info["checks"]["p0"] == 9566 and [g["target"] for g in info["legs"]] == [9566 + D, 9566] * 3
    lo, hi = mm.LIMITS
    assert box["slave"].puts[3:5] == [(0x607D, 1, lo), (0x607D, 2, hi)]       # 창이 아니라 레일 표시를 쓴다
    assert info["closed"] == "INIT" and signal.getsignal(signal.SIGINT) == before


# ---------------------------------------------------------------- ⑥ 진짜 축 정의 파일 — 깨지지 않았나만 (확인 여부는 보지 않는다)

def test_real_axis_file_is_a_valid_definition():
    doc, why = axis.load(REAL_AXIS)
    assert why is None and isinstance(doc, dict), why
    assert isinstance(doc.get("viewpoint"), str) and doc["viewpoint"].strip()
    assert doc.get("plus_direction") in ("왼쪽", "오른쪽")
    assert doc.get("home_side", "빠짐") in (None, "왼쪽", "오른쪽")      # 키는 있어야 한다 — 값은 사람이 채우기 전까지 null
    assert isinstance(doc.get("evidence"), list)
