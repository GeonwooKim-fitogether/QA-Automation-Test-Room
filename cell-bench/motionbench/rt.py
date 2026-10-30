"""모터 제어 우선순위 — Windows 에서 모터 프로그램을 다른 프로그램보다 먼저 CPU 를 받게 한다.

2026-10-29 오후, 백신 · 보안 프로그램이 바쁜 동안 2 ms 주기가 60 ms 늦어져 Quick stop 이 두 번 났다(오전에는 10 ms 넘는 지연 0번).
프로세스는 '높음'(HIGH), 2 ms 주기 스레드는 그 안에서 '최상위'(TIME_CRITICAL)로 둔다 — 보통 프로그램의 스레드가 가질 수 있는 가장 높은 단계(15)다.
프로세스 전체를 '실시간'(REALTIME) 으로 올리지는 않는다: 주기 밖의 일(기록 쓰기 · 결과 저장)까지 Windows 자신의 일보다 앞서
마우스 · 키보드 · 디스크가 멈출 수 있고, 그러면 Ctrl+C 로 멈추는 길도 막힌다.
Windows 가 아니거나 호출이 실패하면 아무것도 바꾸지 않고 그 사실만 돌려준다 — 우선순위는 안전 장치가 아니라 지연을 줄이는 장치다.
"""
from __future__ import annotations

import sys

HIGH_PRIORITY_CLASS = 0x00000080
THREAD_PRIORITY_TIME_CRITICAL = 15


def _k32():
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    # 핸들은 64비트 — 형을 밝히지 않으면 GetCurrentProcess 의 가짜 핸들(-1)이 32비트로 잘려 '잘못된 핸들(6)'이 된다(2026-10-29 실측)
    k.GetCurrentProcess.restype = k.GetCurrentThread.restype = wintypes.HANDLE
    k.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
    k.SetPriorityClass.restype = k.SetThreadPriority.restype = wintypes.BOOL
    return k


def raise_process() -> str:
    """이 프로세스를 '높음' 으로. 돌려주는 글은 결과 JSON 에 남긴다."""
    k = _k32()
    if k is None:
        return "바꾸지 않음 (Windows 아님)"
    ok = k.SetPriorityClass(k.GetCurrentProcess(), HIGH_PRIORITY_CLASS)
    return "프로세스 높음" if ok else f"프로세스 우선순위 실패 (오류 {_err()})"


def raise_thread() -> str:
    """부른 스레드(2 ms 주기 스레드)를 '최상위' 로."""
    k = _k32()
    if k is None:
        return "바꾸지 않음 (Windows 아님)"
    ok = k.SetThreadPriority(k.GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL)
    return "주기 스레드 최상위" if ok else f"주기 스레드 우선순위 실패 (오류 {_err()})"


def _err() -> int:
    import ctypes
    return ctypes.get_last_error()
