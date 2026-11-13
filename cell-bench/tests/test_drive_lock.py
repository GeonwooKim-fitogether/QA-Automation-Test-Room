"""레일 모터 드라이브를 막는 두 장치가 '막히는지'를 본다 — 진짜 어댑터 · 드라이브 · 레일 · LabSat 에 닿지 않는다.

장치 1. PC 전역 드라이브 잠금(motionbench/drive_lock.py) — 다른 폴더(작업 트리)의 프로그램이 잠금을 쥐고 있으면 이 폴더의
        run_motion.py(모든 모드) · run_rail.py 가 드라이브를 열지 않고 쥔 쪽을 한 줄로 남기고 멈추는가.
        '다른 폴더'는 drive_lock.py 를 임시 폴더에 복사해 그 사본을 자식 프로세스로 돌려 흉내 낸다. 잠금 자리는 conftest 가 옮긴
        임시 폴더라 진짜 잠금(%ProgramData%)은 건드리지 않는다.
장치 2. 시험 중 차단(tests/conftest.py) — pytest 안에서, 그리고 pytest 가 띄운 자식 프로세스에서 진짜 EtherCAT 마스터를 열면 실패하는가.
        자식 프로세스 길은 '진짜 pysoem 처럼 보이는 가짜'로 같은 프로세스 안에서 본다 — 진짜 run_motion.py 를 자식으로 띄우지 않는다
        (막는 장치가 고장 나 있으면 그 시험이 바로 2026-11-12 사고를 되풀이하기 때문이다).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

CB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CB))
sys.path.insert(0, str(CB / "tools"))
from motionbench import drive_lock as DL  # noqa: E402
import run_rail as R  # noqa: E402
from test_motion_m2_modes import FakeMaster, rm  # noqa: E402,F401 — fixture: 가짜 pysoem 을 끼운 run_motion

# 다른 체크아웃에서 도는 프로그램 흉내 — 그 폴더의 drive_lock 사본으로 잠금을 쥐고, stdin 이 닫힐 때까지 쥔다
CHILD = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from motionbench import drive_lock
try:
    held = getattr(drive_lock, sys.argv[2])(sys.argv[3])
except drive_lock.Busy as e:
    print("busy", e, flush=True)
    raise SystemExit(0)
print("held", os.getpid(), flush=True)
sys.stdin.readline()
"""
# .venv 의 python.exe 는 진짜 파이썬을 자식으로 한 번 더 띄운다 — 잠금에 적히는 pid 는 Popen.pid 가 아니라 자식이 밝힌 pid 다


@pytest.fixture
def other(tmp_path):
    """다른 체크아웃(작업 트리)의 cell-bench — drive_lock.py 만 복사해 둔다."""
    d = tmp_path / "other-worktree" / "cell-bench"
    (d / "motionbench").mkdir(parents=True)
    shutil.copy(CB / "motionbench" / "drive_lock.py", d / "motionbench" / "drive_lock.py")
    (d / "motionbench" / "__init__.py").write_text("", encoding="utf-8")
    return d


def child(checkout: Path, kind: str, who: str, env: dict | None = None) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-X", "utf8", "-c", CHILD, str(checkout), kind, who], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, encoding="utf-8", env=env)


def take_soon(fn, who: str, s: float = 5.0):
    """쥔 프로세스가 끝난 뒤 운영체제가 잠금을 푸는 데 잠깐 걸릴 수 있다 — s 초 안에 쥐면 된다."""
    end = time.monotonic() + s
    while True:
        try:
            return fn(who)
        except DL.Busy:
            if time.monotonic() > end:
                raise
            time.sleep(0.05)


@contextmanager
def held_elsewhere(checkout: Path, kind: str = "take_drive", who: str = "run_motion long"):
    p = child(checkout, kind, who)
    try:
        word, pid = p.stdout.readline().split()
        assert word == "held"
        yield int(pid)
    finally:
        p.stdin.close()
        p.wait(10)


def _no_leftover():
    assert not DL.holding(DL.ROUND) and not DL.holding(DL.DRIVE)


# ---------------------------------------------------------------- 장치 1. PC 전역 잠금

def test_lock_place_is_one_per_pc_not_per_checkout(other, monkeypatch):
    """잠금 자리는 체크아웃 폴더와 상관없다 — 이 폴더의 drive_lock 과 다른 폴더의 사본이 같은 자리를 가리킨다."""
    monkeypatch.delenv(DL.ENV_DIR)
    here = DL.lock_dir()
    env = {k: v for k, v in os.environ.items() if k != DL.ENV_DIR}
    there = subprocess.run([sys.executable, "-X", "utf8", "-c", f"import sys; sys.path.insert(0, {str(other)!r}); "
                            "from motionbench import drive_lock; print(drive_lock.lock_dir())"],
                           capture_output=True, text=True, encoding="utf-8", env=env).stdout.strip()
    assert Path(there) == here
    assert "cell-bench" not in str(here) and "worktrees" not in str(here)
    if os.name == "nt":
        assert str(here).lower().startswith(os.environ["PROGRAMDATA"].lower())


def test_drive_held_in_other_folder_refuses_here_and_names_the_holder(other):
    _no_leftover()
    with held_elsewhere(other, "take_drive", "run_motion long") as pid:
        with pytest.raises(DL.Busy) as e:
            DL.take_drive("run_motion scan")
        msg = str(e.value)
        assert DL.BUSY in msg and f"pid {pid}" in msg and "run_motion long" in msg and "other-worktree" in msg
        with pytest.raises(DL.Busy):
            DL.take_round("run_rail GNSS-2")
        _no_leftover()                                  # 실패한 쪽은 아무것도 쥐고 있지 않다
    with take_soon(DL.take_drive, "run_motion scan"):   # 그쪽이 끝나면(프로세스가 끝나면) 쥘 수 있다
        assert DL.holding(DL.DRIVE)
    _no_leftover()


def test_round_held_elsewhere_blocks_even_in_the_gap_between_runs(other):
    """실행기가 운전과 운전 사이(어댑터는 닫힘)에 있어도, 라운드를 쥔 동안 남의 scan 은 드라이브를 열지 못한다."""
    with held_elsewhere(other, "take_round", "run_rail GNSS-2"):
        with pytest.raises(DL.Busy, match="레일 라운드"):
            DL.take_drive("run_motion scan")
    _no_leftover()


def test_round_owner_lends_only_to_its_own_children(other):
    """라운드 주인이 띄운 자식(run_motion)은 표를 물려받아 들어가고, 표가 없는 프로그램은 막힌다. 어댑터 잠금은 빌려주지 않는다."""
    with DL.take_round("run_rail GNSS-2"):
        assert os.environ.get(DL.ENV_LEASE)
        kid = child(other, "take_drive", "run_motion long")              # 환경을 물려받은 자식
        assert kid.stdout.readline().startswith("held")
        try:
            stranger = {k: v for k, v in os.environ.items() if k != DL.ENV_LEASE}
            out = child(other, "take_drive", "run_motion scan", env=stranger).communicate(timeout=10)[0]
            assert out.startswith("busy") and "레일 라운드" in out             # 표 없는 프로그램 — 라운드에서 막힌다
            out = child(other, "take_drive", "run_motion scan").communicate(timeout=10)[0]
            assert out.startswith("busy") and "드라이브를 다른 프로그램이 열고 있다" in out   # 표가 있어도 어댑터는 한 프로세스만
        finally:
            kid.stdin.close()
            kid.wait(10)
    assert DL.ENV_LEASE not in os.environ
    _no_leftover()


def test_round_is_reentrant_within_one_process():
    """묶음 실행기(rail_prep) 안에서 run_rail.main 이 다시 쥐어도 막히지 않고, 바깥이 놓을 때 풀린다."""
    outer = DL.take_round("레일 준비")
    inner = DL.take_round("run_rail IMU-2")
    inner.release()
    assert DL.holding(DL.ROUND)
    with DL.take_drive("run_motion long"):              # 같은 프로세스가 연다(시험의 rm.main 같은 경우)
        assert DL.holding(DL.DRIVE)
    outer.release()
    _no_leftover()


@pytest.mark.parametrize("mode", ["scan", "monitor", "long", "home", "goto"])
def test_run_motion_every_mode_refuses_while_other_folder_holds(rm, other, monkeypatch, capsys, mode):
    rm.test_held.release()                              # 픽스처가 쥔 잠금을 놓고, 다른 폴더가 쥔 상태를 만든다
    monkeypatch.setattr(rm.proc, "safe_stdio", lambda: None)
    monkeypatch.setattr(sys, "argv", ["run_motion.py", mode])
    with held_elsewhere(other, "take_drive", "run_motion long"):
        FakeMaster.made = []
        assert rm.main() == 1
    out = capsys.readouterr().out
    assert DL.BUSY in out and "other-worktree" in out and "시작하지 않는다" in out
    assert FakeMaster.made == [] and rm.plug_calls == []      # 마스터도 만들지 않고 플러그도 건드리지 않는다
    assert not (rm.DATA / "motion.lock").exists() and not (rm.DATA / "drives.json").exists()


def test_open_adapter_refuses_without_the_pc_lock(rm):
    rm.test_held.release()
    m = FakeMaster()
    with pytest.raises(rm.ecat.Stop, match="PC 전역 드라이브 잠금"):
        rm.ecat.open_adapter(m)
    assert m.opens == []
    with DL.take_drive("run_motion scan"):
        rm.ecat.open_adapter(m)
    assert m.opens == ["bogus-no-adapter"]


def test_run_rail_refuses_to_start_while_other_folder_holds_round(other, tmp_path, monkeypatch):
    monkeypatch.setattr(R, "install_preset", lambda *a: pytest.fail("잠금에 막혔는데 프리셋을 깔았다"))
    with held_elsewhere(other, "take_round", "qc_loop 하루 수집"):
        with pytest.raises(SystemExit) as e:
            R.main(["FIRMWARE-1", "--out", str(tmp_path / "out")])
    assert DL.BUSY in str(e.value) and "qc_loop 하루 수집" in str(e.value)
    _no_leftover()


def test_rail_prep_takes_round_before_touching_plug_or_labsat(other, tmp_path, monkeypatch):
    import qc_round as Q
    monkeypatch.setattr(Q.charge, "plug_set", lambda *a, **k: pytest.fail("잠금에 막혔는데 플러그를 건드렸다"))
    monkeypatch.setattr(Q, "LabSat", lambda *a, **k: pytest.fail("잠금에 막혔는데 LabSat 에 붙었다"))
    with held_elsewhere(other, "take_round", "run_rail GNSS-2"):
        with pytest.raises(SystemExit, match=DL.BUSY):
            Q.rail_prep(["IMU-2"], 10, tmp_path, print)
    _no_leftover()


def test_drive_ok_does_not_trust_an_old_drives_json(tmp_path, monkeypatch):
    """scan 이 잠금에 막혀 drives.json 을 쓰지 않았으면, 예전 '시간 다 됨' 을 믿지 않고 scan 의 마지막 줄을 이유로 돌려준다."""
    monkeypatch.setattr(R.drive_lock, "blocked_reason", lambda: None)      # 환경 변수는 그대로 — 혹시 자식이 떠도 그쪽에서 막힌다
    monkeypatch.setattr(R, "DATA", tmp_path)
    (tmp_path / "drives.json").write_text(json.dumps({"t": time.time() - 3600, "slaves": 1, "exit": "시간 다 됨"}), encoding="utf-8")
    line = f"{DL.BUSY} — 드라이브를 다른 프로그램이 열고 있다(pid 1 · run_motion long · …) — 시작하지 않는다"
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=line.encode("utf-8")))
    assert R.drive_ok() == (False, line)

    def fresh(*a, **k):
        (tmp_path / "drives.json").write_text(json.dumps({"t": time.time(), "slaves": 1, "exit": "시간 다 됨"}), encoding="utf-8")
        return SimpleNamespace(stdout=b"")
    monkeypatch.setattr(R.subprocess, "run", fresh)
    assert R.drive_ok() == (True, "시간 다 됨")


# ---------------------------------------------------------------- 장치 2. 시험 중 차단

def test_block_is_inherited_by_child_processes():
    """pytest 가 띄우는 자식도 차단 표시와 임시 잠금 자리를 물려받는다 — 사고 때의 scan 은 자식 프로세스였다."""
    out = subprocess.run([sys.executable, "-X", "utf8", "-c", "import os; print(os.environ.get('RAIL_NO_HARDWARE')); print(os.environ.get('RAIL_LOCK_DIR'))"],
                         capture_output=True, text=True, encoding="utf-8").stdout.splitlines()
    assert out[0] == "pytest" and "rail-lock-pytest-" in out[1]


def test_real_pysoem_master_cannot_open_inside_pytest(real_pysoem):
    """제어 PC 의 .venv(진짜 pysoem)에서 시험을 돌려도 Master 를 여는 순간 실패한다. 없는 어댑터 이름이라 막는 장치가 고장 나도 장비에 닿지 않는다."""
    with pytest.raises(Exception) as e:
        real_pysoem.Master().open("pytest-no-such-adapter")
    assert type(e.value).__name__ == "HardwareBlocked" and "pytest" in str(e.value)


def test_ecat_refuses_real_pysoem_under_pytest_even_with_the_lock(rm, monkeypatch):
    """시험이 띄운 자식 프로세스의 상황 — 진짜 pysoem 과 물려받은 RAIL_NO_HARDWARE. 잠금을 쥐어도 어댑터를 열지 않는다."""
    monkeypatch.setattr(rm.ecat.pysoem, "__file__", "site-packages/pysoem/__init__.py", raising=False)   # 진짜처럼 보이게
    assert "RAIL_NO_HARDWARE=pytest" in rm.ecat.real_blocked()
    m = FakeMaster()
    with pytest.raises(rm.ecat.Stop, match="RAIL_NO_HARDWARE"):
        rm.ecat.open_adapter(m)
    assert m.opens == []


@pytest.mark.parametrize("mode", ["scan", "long"])
def test_run_motion_as_pytest_child_stops_before_lock_plug_or_master(rm, monkeypatch, capsys, mode):
    """사고의 길(pytest → run_rail.preflight → run_motion.py scan 자식)을 같은 프로세스에서 흉내 낸다 — 진짜 pysoem 이면 아무것도 하지 않고 끝난다."""
    rm.test_held.release()
    monkeypatch.setattr(rm.ecat.pysoem, "__file__", "site-packages/pysoem/__init__.py", raising=False)
    monkeypatch.setattr(rm.proc, "safe_stdio", lambda: None)
    monkeypatch.setattr(sys, "argv", ["run_motion.py", mode])
    FakeMaster.made = []
    assert rm.main() == 1
    assert "RAIL_NO_HARDWARE" in capsys.readouterr().out
    assert FakeMaster.made == [] and rm.plug_calls == [] and not (rm.DATA / "motion.lock").exists()
    _no_leftover()


def test_run_rail_never_spawns_scan_or_motor_under_pytest(tmp_path, monkeypatch):
    """run_rail 의 드라이브 점검 · 대기 · 모터 띄우기는 시험 중이면 자식 프로세스를 띄우지 않는다."""
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: pytest.fail("시험 중인데 run_motion.py scan 을 띄웠다"))
    monkeypatch.setattr(R.subprocess, "call", lambda *a, **k: pytest.fail("시험 중인데 run_motion.py long 을 띄웠다"))
    monkeypatch.setattr(R, "DATA", tmp_path)
    ok, why = R.drive_ok()
    assert not ok and "RAIL_NO_HARDWARE" in why
    with pytest.raises(SystemExit, match="RAIL_NO_HARDWARE"):
        R.wait_drive()
    assert not (tmp_path / "drive_needed.json").exists()
    rc, why, csvp, _ = R.motor_once(tmp_path / "run1_motor.log")
    assert rc == 1 and "RAIL_NO_HARDWARE" in why and csvp is None
