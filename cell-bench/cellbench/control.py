"""원격 제어 — 결과판 서버가 명령을 파일로 남기고, 시험 프로그램이 20초마다 집어 간다.

두 프로세스(serve_board · run_cycle)를 소켓으로 묶지 않고 파일 하나로 잇는다. 어느 쪽이 죽어도
다른 쪽이 영향받지 않고, 명령이 "받아들여졌는지"는 control_ack.json 으로 확인한다.

명령:
  plug_on    지금 플러그를 켠다. 방전 중이면 방전을 끝내고 충전으로 넘어간다
  plug_off   지금 플러그를 끈다. 충전 중이면 만충으로 치고 다음 사이클로 넘어간다
  stop_safe  플러그를 켠 채(충전 쪽이 안전) 프로그램을 멈춘다. 다시 시작은 PC 에서

PIN: 원격 명령은 Tailscale(내 기기만 통하는 사설망) 뒤에 있지만, 플러그를 끄는 명령은 실수 한 번이
셀 24대를 방전시킬 수 있어 PIN 을 한 번 더 받는다. PIN 은 Windows 자격 증명 관리자에 둔다.
실패 횟수는 서버 전체에 하나로 센다 — 보낸 사람(헤더)마다 세면 헤더만 바꿔 잠금을 피할 수 있다(QA H-3, 10-10).
안전 정지는 잠금 중에도 맞는 PIN 이면 통과한다 — 남이 틀린 PIN 을 넣어 내 안전 정지를 막지 못하게(QA H-2).

명령에는 보낸 시각이 붙고, 시험 프로그램은 command_max_age_s(기본 2분)보다 오래된 명령을 실행하지 않고 버린다
(QA C-1 — 엔진이 멈춘 동안 받은 명령이 몇 시간 뒤 재시작 때 실행되던 것).
"""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

COMMANDS = {"plug_on": "플러그 켜기", "plug_off": "플러그 끄기", "stop_safe": "안전 정지"}


class StopRequested(Exception):
    """사용자가 원격으로 안전 정지를 요청했다."""


def write_json_atomic(path: Path, payload: dict, tries: int = 10) -> bool:
    """임시 파일에 쓴 뒤 바꿔치기. 임시 파일 이름은 부를 때마다 다르다 — 이름이 하나뿐이면 두 요청이 동시에 쓸 때
    한쪽이 다른 쪽의 임시 파일을 가져가 FileNotFoundError 로 처리기가 죽었다(QA M-8). 실패하면 False, 예외는 내지 않는다."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return False
    try:
        for _ in range(tries):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:          # Windows: 다른 쪽이 그 파일을 읽는 중
                time.sleep(0.05)
            except OSError:
                return False
        return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass                              # 바꿔치기에 성공했으면 이미 없다


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class ControlInbox:
    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cmd_path = self.dir / "control.json"
        self.ack_path = self.dir / "control_ack.json"

    # 서버 쪽
    def post(self, cmd: str, source: str) -> dict | None:
        """명령을 파일로 남긴다. 쓰지 못하면 None(받지 못한 명령을 받았다고 답하지 않게)."""
        if cmd not in COMMANDS:
            raise ValueError(f"알 수 없는 명령 {cmd}")
        payload = {"cmd": cmd, "source": source, "t": time.time()}
        return payload if write_json_atomic(self.cmd_path, payload) else None

    def pending(self) -> dict | None:
        return read_json(self.cmd_path) if self.cmd_path.exists() else None

    def last_ack(self) -> dict | None:
        return read_json(self.ack_path) if self.ack_path.exists() else None

    # 시험 프로그램 쪽
    def take(self) -> dict | None:
        if not self.cmd_path.exists():
            return None
        cmd = read_json(self.cmd_path)
        for _ in range(10):
            try:
                self.cmd_path.unlink()
                break
            except PermissionError:
                time.sleep(0.05)
            except FileNotFoundError:
                break
        return cmd

    def ack(self, cmd: str, result: str) -> None:
        write_json_atomic(self.ack_path, {"cmd": cmd, "result": result, "t": time.time()})


def command_time(cmd: dict | None) -> float | None:
    """명령을 보낸 시각(초). 로컬 파일은 t, 클라우드 표는 requested_at(ISO). 모르면 None."""
    if not isinstance(cmd, dict):
        return None
    t = cmd.get("t")
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        return float(t)
    at = cmd.get("requested_at")
    if isinstance(at, str) and at:
        try:
            return datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def command_expired(cmd: dict | None, max_age_s: float, now: float | None = None) -> str | None:
    """실행하면 안 되는 오래된 명령이면 그 이유 한 줄, 아니면 None. 보낸 시각을 모르면 나이를 잴 수 없으니 버린다."""
    t = command_time(cmd)
    if t is None:
        return "보낸 시각을 모름"
    age = (time.time() if now is None else now) - t
    if age > max_age_s:
        return f"{time.strftime('%H:%M:%S', time.localtime(t))} 보냄 · {age:.0f}초 지남(한도 {max_age_s:.0f}초)"
    return None


class PinGuard:
    """PIN 대조 + 연속 실패 잠금. 5번 틀리면 10분 동안 시도를 받지 않는다.

    실패는 서버 전체에 하나로 센다 — who 는 기록용으로만 받고 잠금 판단에 쓰지 않는다(헤더 위조 우회 방지, QA H-3).
    allow_locked=True(안전 정지)면 잠금 중에도 맞는 PIN 은 통과한다. 틀리면 실패로 세기만 한다(QA H-2)."""

    _ALL = "*"

    def __init__(self, pin: str | None, max_fail: int = 5, lock_s: float = 600):
        self.pin, self.max_fail, self.lock_s = pin, max_fail, lock_s
        self._fails: dict[str, list[float]] = {}
        self._mu = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.pin)

    def _recent(self, now: float) -> list[float]:
        recent = [t for t in self._fails.get(self._ALL, []) if now - t < self.lock_s]
        self._fails[self._ALL] = recent
        return recent

    def locked(self, who: str = "", now: float | None = None) -> bool:
        now = now or time.time()
        with self._mu:
            return len(self._recent(now)) >= self.max_fail

    def left(self, now: float | None = None) -> int:
        """잠기기까지 남은 시도 횟수."""
        now = now or time.time()
        with self._mu:
            return max(0, self.max_fail - len(self._recent(now)))

    def check(self, who: str, given: str, now: float | None = None, allow_locked: bool = False) -> bool:
        now = now or time.time()
        if not self.enabled:
            return False
        with self._mu:
            if len(self._recent(now)) >= self.max_fail and not allow_locked:
                return False
            ok = hmac.compare_digest(str(given or ""), self.pin)
            if ok:
                self._fails.pop(self._ALL, None)
            else:
                self._fails.setdefault(self._ALL, []).append(now)
            return ok
