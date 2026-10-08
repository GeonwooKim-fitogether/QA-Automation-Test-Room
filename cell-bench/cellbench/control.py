"""원격 제어 — 결과판 서버가 명령을 파일로 남기고, 시험 프로그램이 20초마다 집어 간다.

두 프로세스(serve_board · run_cycle)를 소켓으로 묶지 않고 파일 하나로 잇는다. 어느 쪽이 죽어도
다른 쪽이 영향받지 않고, 명령이 "받아들여졌는지"는 control_ack.json 으로 확인한다.

명령:
  plug_on    지금 플러그를 켠다. 방전 중이면 방전을 끝내고 충전으로 넘어간다
  plug_off   지금 플러그를 끈다. 충전 중이면 만충으로 치고 다음 사이클로 넘어간다
  stop_safe  플러그를 켠 채(충전 쪽이 안전) 프로그램을 멈춘다. 다시 시작은 PC 에서

PIN: 원격 명령은 Tailscale(내 기기만 통하는 사설망) 뒤에 있지만, 플러그를 끄는 명령은 실수 한 번이
셀 24대를 방전시킬 수 있어 PIN 을 한 번 더 받는다. PIN 은 Windows 자격 증명 관리자에 둔다.
"""
from __future__ import annotations

import hmac
import json
import os
import time
from pathlib import Path

COMMANDS = {"plug_on": "플러그 켜기", "plug_off": "플러그 끄기", "stop_safe": "안전 정지"}


class StopRequested(Exception):
    """사용자가 원격으로 안전 정지를 요청했다."""


def write_json_atomic(path: Path, payload: dict, tries: int = 10) -> bool:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return False
    for _ in range(tries):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.05)
    return False


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
    def post(self, cmd: str, source: str) -> dict:
        if cmd not in COMMANDS:
            raise ValueError(f"알 수 없는 명령 {cmd}")
        payload = {"cmd": cmd, "source": source, "t": time.time()}
        write_json_atomic(self.cmd_path, payload)
        return payload

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


class PinGuard:
    """PIN 대조 + 연속 실패 잠금. 5번 틀리면 10분 동안 그 주소의 시도를 받지 않는다."""

    def __init__(self, pin: str | None, max_fail: int = 5, lock_s: float = 600):
        self.pin, self.max_fail, self.lock_s = pin, max_fail, lock_s
        self._fails: dict[str, list[float]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.pin)

    def locked(self, who: str, now: float | None = None) -> bool:
        now = now or time.time()
        recent = [t for t in self._fails.get(who, []) if now - t < self.lock_s]
        self._fails[who] = recent
        return len(recent) >= self.max_fail

    def check(self, who: str, given: str, now: float | None = None) -> bool:
        now = now or time.time()
        if not self.enabled or self.locked(who, now):
            return False
        ok = hmac.compare_digest(str(given or ""), self.pin)
        if not ok:
            self._fails.setdefault(who, []).append(now)
        else:
            self._fails.pop(who, None)
        return ok
