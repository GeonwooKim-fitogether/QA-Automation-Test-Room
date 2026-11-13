"""레일 모터 드라이브(EtherCAT) PC 전역 잠금 — 이 PC 의 프로그램이 한 번에 하나만 드라이브에 닿게 한다.

왜 있나 (2026-11-12 16:43 사고): 메인 체크아웃(C:\\Dev\\dynamic-bench)에서 FIRMWARE-2 운전(지금 이름 GNSS-2)이 도는 동안, 다른 작업 트리의
pytest 가 run_motion.py scan 을 띄워 같은 드라이브에 상태 바꾸기(ESM) 요청을 보냈다. 드라이브는 Err88.2(운전 중 ESM 요청)로 멈췄다.
그때의 잠금(data/motion.lock)은 체크아웃마다 그 폴더 안에 생겨, 다른 폴더의 프로그램은 서로를 보지 못했다.

그래서 잠금 자리는 체크아웃과 상관없는 한 곳이다: %ProgramData%\\QA-Automation-Test-Room\\rail-lock\\ (lock_dir()).
잠금은 두 개다.
  round.lock — 레일 라운드의 주인. run_rail · 레일 준비(qc_round.rail_prep ~ rail_finish) · 하루 수집 실행기(qc_loop)가
               도는 내내 쥔다. 운전과 운전 사이 몇 초의 틈(드라이브 점검 · LabSat 다시 틀기 · 충전 확인)에도 남이 끼어들지 못한다.
               주인은 환경 변수 RAIL_DRIVE_LEASE 에 표(token)를 적어, 자기가 띄운 자식(run_motion.py)에게만 빌려준다.
  drive.lock — EtherCAT 어댑터를 실제로 여는 프로세스(run_motion.py 의 모든 모드 — scan 도). 빌려주지 않는다.
               주인이 죽어 round.lock 이 풀려도, 아직 어댑터를 쥔 자식이 있으면 남은 열지 못한다.
run_motion.py 는 round.lock 을 스스로 쥐거나(혼자 띄웠을 때) 주인이 빌려준 표를 보이고, 그다음 drive.lock 을 쥔다.
못 쥐면 드라이브를 열지 않고 쥔 쪽(pid · 무엇 · 어느 폴더 · 언제부터)을 한 줄로 남기고 멈춘다.

잠금은 운영체제가 쥐고 있어, 쥔 프로세스가 어떻게 끝나든(강제 종료 포함) 저절로 풀린다(cellbench/proc.acquire_lock 과 같은 방식).
다만 파일 맨 앞이 아니라 멀리 떨어진 한 바이트(LOCK_AT)를 잠가, 다른 프로세스가 앞부분의 '쥔 쪽' 정보를 읽을 수 있게 했다.
시험은 RAIL_LOCK_DIR 로 자리를 임시 폴더로 옮긴다(tests/conftest.py) — 시험이 진짜 잠금을 쥐어 운전을 막는 일이 없게.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

BUSY = "다른 모션 엔진이 이미 돈다"     # tools/run_rail.py 가 이 글을 보고 잠시 뒤 다시 띄운다 — 바꾸지 않는다
ENV_DIR = "RAIL_LOCK_DIR"               # 잠금 자리를 옮긴다 — 시험(tests/conftest.py)만 쓴다
ENV_LEASE = "RAIL_DRIVE_LEASE"          # 라운드 주인이 자식에게 빌려주는 표
ROUND, DRIVE = "round.lock", "drive.lock"
LOCK_AT = 1 << 20                       # 잠그는 바이트의 자리 — 앞부분(쥔 쪽 정보)은 남이 읽을 수 있게 비워 둔다
INFO_BYTES = 1024
CHECKOUT = str(Path(__file__).resolve().parents[1])     # 이 파일이 든 체크아웃의 cell-bench — 쥔 쪽 정보에 적는다


class Busy(SystemExit):
    """드라이브를 다른 프로그램이 쥐고 있다(또는 잠금 자리를 못 연다) — 잡지 않으면 그 메시지로 프로그램이 끝난다."""


def lock_dir() -> Path:
    """잠금 자리 — 체크아웃 폴더와 상관없이 이 PC 에 하나."""
    if os.environ.get(ENV_DIR):
        return Path(os.environ[ENV_DIR])
    base = os.environ.get("PROGRAMDATA") or (r"C:\ProgramData" if os.name == "nt" else "/tmp")
    return Path(base) / "QA-Automation-Test-Room" / "rail-lock"


_held: dict[str, list] = {}             # 이 프로세스가 쥔 잠금: 이름 → [fd, 겹쳐 쥔 수, 표]


def _open(name: str) -> int:
    try:
        d = lock_dir()
        d.mkdir(parents=True, exist_ok=True)
        return os.open(d / name, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o666)
    except OSError as e:                # 막히면 열지 않는 쪽으로 — BUSY 를 붙이지 않아 run_rail 이 다시 띄우지 않는다
        raise Busy(f"드라이브 잠금 자리({lock_dir()})를 열지 못해 드라이브를 열지 않는다 — {type(e).__name__}: {e}")


def _lock(fd: int) -> bool:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, LOCK_AT, 0)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock_close(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, LOCK_AT, 0)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        os.close(fd)


def _read(name: str) -> dict:
    """쥔 쪽 정보 — 못 읽거나 깨졌으면 빈 dict."""
    try:
        with open(lock_dir() / name, "rb") as f:
            return json.loads(f.read(INFO_BYTES).decode("utf-8").strip() or "{}")
    except (OSError, ValueError):
        return {}


def _locked_by_someone(name: str) -> bool:
    """지금 누가 그 잠금을 쥐고 있나 — 잠깐 쥐어 보고 되면 바로 놓는다."""
    if name in _held:
        return True
    fd = _open(name)
    if _lock(fd):
        _unlock_close(fd)
        return False
    os.close(fd)
    return True


def holder(name: str) -> str:
    """사람이 읽는 한 줄 — 'pid 1234 · run_motion long · C:\\Dev\\dynamic-bench\\cell-bench · 11-12 16:53:10부터'."""
    i = _read(name)
    if not i:
        return "쥔 쪽 정보를 읽지 못함"
    since = time.strftime("%m-%d %H:%M:%S", time.localtime(i.get("since", 0)))
    return f"pid {i.get('pid')} · {i.get('who')} · {i.get('where')} · {since}부터"


def _take(name: str, who: str, token: str | None = None) -> bool:
    if name in _held:                   # 같은 프로세스 안에서 겹쳐 쥐기(묶음 실행기 → run_rail.main)
        _held[name][1] += 1
        return True
    fd = _open(name)
    if not _lock(fd):
        os.close(fd)
        return False
    info = {"pid": os.getpid(), "who": who, "where": CHECKOUT, "since": time.time(), "token": token}
    os.lseek(fd, 0, 0)
    os.write(fd, json.dumps(info, ensure_ascii=False).encode("utf-8").ljust(INFO_BYTES)[:INFO_BYTES])
    _held[name] = [fd, 1, token]
    return True


def _drop(name: str) -> None:
    h = _held.get(name)
    if not h:
        return
    h[1] -= 1
    if h[1] > 0:
        return
    del _held[name]
    if name == ROUND and os.environ.get(ENV_LEASE) == h[2]:
        os.environ.pop(ENV_LEASE, None)
    _unlock_close(h[0])


def holding(name: str = DRIVE) -> bool:
    """이 프로세스가 그 잠금을 쥐고 있나 — ecat.open_adapter 가 어댑터를 열기 직전에 본다."""
    return name in _held


def _leased() -> bool:
    """라운드 주인이 빌려준 표를 가졌나 — 환경 변수의 표가 round.lock 에 적힌 표와 같고, 그 잠금을 지금 누가 쥐고 있어야 한다
    (주인이 끝난 뒤 남은 옛 표로 들어오지 못하게)."""
    tok = os.environ.get(ENV_LEASE)
    return bool(tok) and _read(ROUND).get("token") == tok and _locked_by_someone(ROUND)


class Held:
    """쥔 잠금 — release() 또는 with 블록 끝에서 놓는다. 프로세스가 끝나면 운영체제가 놓는다."""

    def __init__(self, names: list[str]):
        self.names = names

    def release(self) -> None:
        for n in reversed(self.names):
            _drop(n)
        self.names = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


def take_round(who: str) -> Held:
    """레일 라운드를 쥔다. 다른 프로그램이 쥐고 있으면 Busy. 쥔 동안 자식 프로세스는 표를 물려받아 드라이브를 열 수 있다."""
    if ROUND not in _held and _leased():              # 주인이 띄운 자식은 주인의 라운드 안에서 돈다
        return Held([])
    tok = _held[ROUND][2] if ROUND in _held else secrets.token_hex(8)
    if not _take(ROUND, who, tok):
        raise Busy(f"{BUSY} — 레일 라운드를 다른 프로그램이 쥐고 있다({holder(ROUND)}). 그 운전이 끝난 뒤에 띄운다")
    os.environ[ENV_LEASE] = tok
    return Held([ROUND])


def take_drive(who: str) -> Held:
    """EtherCAT 어댑터를 열 권리를 쥔다 — run_motion.py 의 모든 모드가 마스터를 만들기 전에 부른다.
    라운드는 스스로 쥐거나 주인이 빌려준 표로 들어가고, 어댑터 잠금(drive.lock)은 늘 스스로 쥔다. 못 쥐면 Busy."""
    names: list[str] = []
    try:
        if not _leased():
            if not _take(ROUND, who, secrets.token_hex(8)):
                raise Busy(f"{BUSY} — 레일 라운드를 다른 프로그램이 쥐고 있다({holder(ROUND)})")
            names.append(ROUND)
        if not _take(DRIVE, who):
            raise Busy(f"{BUSY} — 드라이브를 다른 프로그램이 열고 있다({holder(DRIVE)})")
        names.append(DRIVE)
    except BaseException:
        for n in reversed(names):
            _drop(n)
        raise
    return Held(names)


def blocked_reason() -> str | None:
    """시험 중이면 진짜 장비를 열지 않는 이유 — tests/conftest.py 가 RAIL_NO_HARDWARE 를 켠다. 자식 프로세스도 물려받는다."""
    v = os.environ.get("RAIL_NO_HARDWARE")
    return f"시험 중(RAIL_NO_HARDWARE={v}) — 진짜 EtherCAT 어댑터를 열지 않는다" if v else None
