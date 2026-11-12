"""cell-bench 시험 공통 — 시험이 진짜 레일 모터 드라이브(EtherCAT)에 닿지 못하게 막는다.

2026-11-12 16:43 사고: 작업 트리에서 돌린 tests/test_run_rail.py 의 preflight 시험이 wait_drive 를 가짜로 바꾸지 않아,
진짜 run_motion.py scan 을 자식 프로세스로 띄웠다. 그 scan 이 제어 PC 의 진짜 어댑터를 열어 운전 중인 드라이브에 상태 바꾸기
요청을 보냈고, 드라이브는 Err88.2 로 멈췄다. 시험 안의 가짜 처리(monkeypatch)는 자식 프로세스까지 닿지 않는다.

그래서 이 파일이 시험을 모으기 전에 세 가지를 한다.
  1. RAIL_NO_HARDWARE — 이 프로세스와 시험이 띄우는 모든 자식 프로세스가 물려받는 환경 변수.
     run_motion.py 는 진짜 pysoem 이면 플러그 · 잠금 · 어댑터 어느 것도 건드리지 않고 끝나고, ecat.open_adapter 는 어댑터를 열지 않고,
     tools/run_rail.py 는 scan · run_motion 을 띄우지 않는다(motionbench/drive_lock.blocked_reason).
  2. RAIL_LOCK_DIR — PC 전역 드라이브 잠금(motionbench/drive_lock.py)의 자리를 임시 폴더로 옮긴다. 시험이 진짜 잠금을 쥐어
     운전 중인 실행기를 막는 일이 없게. 운전 중인 실행기가 빌려준 표(RAIL_DRIVE_LEASE)도 지운다.
  3. 진짜 pysoem 이 깔린 파이썬(제어 PC 의 .venv)이면 pysoem.Master 를 '여는 순간 실패하는 것'으로 바꿔 끼운다.
     시험이 쓰는 가짜 pysoem(test_motion_m2_modes._fake_pysoem)은 그대로 돈다.
"""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest

os.environ["RAIL_NO_HARDWARE"] = "pytest"
_LOCK_DIR = tempfile.mkdtemp(prefix="rail-lock-pytest-")
os.environ["RAIL_LOCK_DIR"] = _LOCK_DIR
os.environ.pop("RAIL_DRIVE_LEASE", None)


class HardwareBlocked(RuntimeError):
    """시험 안에서 진짜 EtherCAT 어댑터를 열려고 했다."""


def _blocked_master():
    class BlockedMaster:
        """pytest 동안의 pysoem.Master — 만들 수는 있지만 여는 순간 실패한다. 진짜 어댑터 · 네트워크에 닿는 코드가 없다."""

        def __init__(self, *a, **kw):
            pass

        def open(self, *a, **kw):
            raise HardwareBlocked("pytest 안에서는 진짜 EtherCAT 어댑터를 열지 않는다(tests/conftest.py) — 가짜 pysoem 을 끼운다")

        def __getattr__(self, name):
            raise HardwareBlocked(f"pytest 안에서는 진짜 pysoem.Master.{name} 를 쓰지 않는다(tests/conftest.py)")

    return BlockedMaster


try:
    import pysoem as _pysoem          # 제어 PC 의 .venv 에만 있다
except Exception:                    # 없는 파이썬 — 막을 것이 없다
    _pysoem = None
if _pysoem is not None:
    _pysoem.Master = _blocked_master()


def pytest_unconfigure(config):
    shutil.rmtree(_LOCK_DIR, ignore_errors=True)


@pytest.fixture
def real_pysoem():
    """진짜 pysoem 모듈(바꿔 끼운 Master 그대로) — 없는 파이썬이면 그 시험을 건너뛴다."""
    if _pysoem is None:
        pytest.skip("pysoem 이 없는 파이썬")
    return _pysoem
