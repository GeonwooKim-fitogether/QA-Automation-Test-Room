"""프로세스 도우미 — 살아 있나 · 언제 태어났나 · 끝내기 · 창 없이 띄우기 · 하나만 돌기(잠금).

감시자(supervise.py)와 엔진(run_cycle.py)이 함께 쓴다. psutil 없이 표준 라이브러리(ctypes)만 쓴다 —
제어 PC 에 설치할 것을 늘리지 않으려고.

pid 만으로는 '그 엔진'인지 알 수 없다. 재부팅 뒤에는 같은 번호를 전혀 다른 프로그램이 받을 수 있고, 그것을
멈춘 엔진으로 알고 끝내면 사고다. 그래서 살아 있는지와 함께 '태어난 시각'을 돌려주고, 감시자는 그 시각이
engine.json 의 started 와 맞는지로 같은 프로세스인지 가린다.
"""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_WIN = sys.platform == "win32"
_STILL_ACTIVE = 259
_ACCESS_DENIED = 5
_QUERY_LIMITED = 0x1000          # PROCESS_QUERY_LIMITED_INFORMATION — 관리자 권한으로 뜬 프로세스도 이것은 열린다
_EPOCH_DIFF_S = 11644473600.0    # 1601-01-01(FILETIME 기준) → 1970-01-01


def _k32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    k.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_uint64)] * 4
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.GetTickCount64.restype = ctypes.c_uint64
    return k


def created(pid: int) -> float | None:
    """pid 가 살아 있으면 그 프로세스가 태어난 시각(epoch 초), 없으면 None.

    살아 있는데 권한 때문에 시각을 못 읽으면 0.0 — '있긴 한데 누군지는 모름'.
    """
    if not pid or pid <= 0:
        return None
    if not _WIN:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            return 0.0
        return 0.0
    k = _k32()
    h = k.OpenProcess(_QUERY_LIMITED, False, int(pid))
    if not h:
        return 0.0 if ctypes.get_last_error() == _ACCESS_DENIED else None
    try:
        code = ctypes.c_uint32()
        if not k.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != _STILL_ACTIVE:
            return None
        t = [ctypes.c_uint64() for _ in range(4)]
        if not k.GetProcessTimes(h, *[ctypes.byref(x) for x in t]):
            return 0.0
        return t[0].value / 1e7 - _EPOCH_DIFF_S
    finally:
        k.CloseHandle(h)


def boot_time() -> float:
    """이 PC 가 마지막으로 켜진 시각(epoch 초). 그보다 먼저 시작한 엔진은 살아 있을 수 없다."""
    if _WIN:
        return time.time() - _k32().GetTickCount64() / 1000.0
    try:
        return time.time() - float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def kill(pid: int, wait_s: float = 10.0) -> bool:
    """프로세스를 끝내고(Windows 는 TerminateProcess) 사라질 때까지 기다린다. 사라졌으면 True."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if created(pid) is None:
            return True
        time.sleep(0.2)
    return created(pid) is None


def console_python() -> str:
    """창 없이 띄울 때 쓸 python.exe — 감시자가 pythonw.exe 로 돌아도 같은 폴더의 python.exe 를 고른다.
    python.exe 를 CREATE_NO_WINDOW 로 띄우면 창은 없고, 처리되지 않은 예외의 흔적은 오류 파일로 받을 수 있다."""
    exe = Path(sys.executable)
    cand = exe.with_name("python.exe" if _WIN else "python3")
    return str(cand if cand.exists() else exe)


def spawn(argv: list[str], cwd: Path, err_path: Path) -> int:
    """창 없이 띄우고 pid 를 돌려준다. 표준 출력은 버리고(엔진은 run.log 에 따로 쓴다) 오류 출력은 err_path 에 덧붙인다.

    작업 스케줄러가 감시자를 '작업'으로 끝낼 때 자식까지 함께 끝내지 않도록 작업 묶음(job)에서 떼어 띄운다.
    묶음이 떼기를 허락하지 않으면 붙은 채로 띄운다(그때는 감시자 작업을 끝내면 엔진도 끝날 수 있다).
    감시자는 우선순위가 낮게 뜨므로(작업 스케줄러 기본) 자식은 보통 우선순위로 띄운다.
    """
    err_path.parent.mkdir(parents=True, exist_ok=True)
    flags = [0]
    if _WIN:
        base = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.NORMAL_PRIORITY_CLASS
        flags = [base | subprocess.CREATE_BREAKAWAY_FROM_JOB, base]
    with open(err_path, "ab") as err:
        for i, fl in enumerate(flags):
            try:
                p = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=err, creationflags=fl, close_fds=True)
                return p.pid
            except OSError:
                if i == len(flags) - 1:
                    raise
    raise RuntimeError("unreachable")


# 명령줄로 프로세스를 찾는다 — 표준 라이브러리로는 남의 명령줄을 읽기 어려워 PowerShell(CIM)에 한 번 묻는다.
# 자기 자신(이 질의를 도는 PowerShell)은 명령줄에 찾는 말이 들어 있으므로 $PID 로 뺀다.
_SCAN_PS = r"""
$ps = Get-CimInstance Win32_Process -Filter "Name='powershell.exe' or Name='pwsh.exe' or Name='python.exe' or Name='pythonw.exe'"
foreach ($p in $ps) {
  if ($p.ProcessId -eq $PID) { continue }
  $c = [string]$p.CommandLine
  if ($p.Name -match '^(powershell|pwsh)\.exe$' -and $c -like '*watchdog.ps1*') { "watchdog $($p.ProcessId)" }
  elseif ($p.Name -like 'python*' -and $c -like '*run_cycle.py*' -and $c -notlike '*--dry-run*') { "engine $($p.ProcessId)" }
}
"""


def scan_others(timeout_s: float = 30.0) -> dict[str, list[int]] | None:
    """이 PC 에서 도는 '옛 임시 감시자'(tools/watchdog.ps1 을 돌리는 PowerShell)와 '엔진'(run_cycle.py, 모의 실행 제외)의 pid.

    {"watchdog": [...], "engine": [...]} — 알아내지 못하면 None (감시자는 그때 이 정보 없이 판단한다).
    engine.json 을 남기지 않는 옛 코드 엔진이 멈췄을 때 끝낼 대상을 찾는 데도 쓴다.
    """
    if not _WIN:
        return None
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCAN_PS],
                           capture_output=True, text=True, timeout=timeout_s,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    out: dict[str, list[int]] = {"watchdog": [], "engine": []}
    for line in r.stdout.splitlines():
        kind, _, pid = line.strip().partition(" ")
        if kind in out and pid.isdigit():
            out[kind].append(int(pid))
    return out


def acquire_lock(path: Path):
    """이 잠금 파일을 독점으로 쥔다. 다른 프로세스가 이미 쥐고 있으면 None.

    잠금은 운영체제가 쥐고 있어, 쥔 프로세스가 어떻게 끝나든(강제 종료·전원 차단 포함) 자동으로 풀린다.
    pid 를 파일에 적어 두고 '살아 있나'를 보는 방식은, 죽은 뒤 남은 파일과 재부팅 뒤 pid 재사용을 따로
    가려내야 해서 쓰지 않는다. 파일 안의 pid 는 사람이 보라고 적는 것이다.
    돌려준 파일 객체를 프로세스가 끝날 때까지 들고 있어야 잠금이 유지된다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    try:
        f.seek(0)
        if _WIN:
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    f.seek(0); f.truncate(); f.write(f"{os.getpid()}\n"); f.flush()
    return f
