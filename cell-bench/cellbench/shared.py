"""다중 세트의 공용 자원 — 추출 문 · 재연결 관문 (명세 2-4).

단일 모드에서는 둘 다 쓰지 않는다(CycleRunner 의 기본값 None — 지금 동작). 다중 모드와, 단일 모드에서 짝 검증기가 도는 동안
조정자가 만들어 실행기들에 나눠 준다.

잠금 순서 (검토 A-L3): 추출 문 → Recorder → LiveListener. 이 모듈의 내부 잠금은 '잎 잠금'이다 — 쥔 채 다른 잠금을 잡거나
플러그 · 네트워크를 부르지 않는다. 추출 문을 '쥔다'는 것은 이 잠금을 쥐는 것이 아니라 토큰을 갖는 것이고, 토큰을 가진 채로는
Recorder · LiveListener 잠금을 잡아도 된다(추출 중 심박 · 신원 확인).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Token:
    owner: str
    t: float
    n: int


class ExtractGate:
    """TCP 60222 와 LiveHub 대역 — 한 번에 하나. 먼저 줄을 선 쪽부터(FIFO), 같은 순간이면 줄을 선 순서.

    줄 서기(queue) → 차례가 되면 try_take 가 토큰을 준다 → 끝나면 release(토큰). 멈춘 소유자의 토큰은 여기서 빼앗지 않는다 —
    울타리(엔진 재시작, D9)가 풀어 준다. 기다리는 쪽은 한도를 넘으면 leave 로 줄에서 빠진다.
    """

    def __init__(self, clock=time.time):
        self._mu = threading.Lock()
        self._queue: list[str] = []
        self._token: Token | None = None
        self._n = 0
        self._clock = clock

    @property
    def holder(self) -> Token | None:
        with self._mu:
            return self._token

    def queue(self, owner: str) -> None:
        with self._mu:
            if owner not in self._queue:
                self._queue.append(owner)

    def leave(self, owner: str) -> None:
        with self._mu:
            if owner in self._queue:
                self._queue.remove(owner)

    def try_take(self, owner: str, wait_turn: bool = True) -> Token | None:
        """비어 있고 줄의 맨 앞이 owner(또는 줄이 비었음)면 토큰을 준다. wait_turn=False(대기 셀 깨우기)는 줄을 서지 않는 쪽이라
        사실상 '줄이 비었을 때만'이다 — 기다리는 추출을 앞지르지 않는다."""
        with self._mu:
            if self._token is not None:
                return None
            if self._queue and self._queue[0] != owner:   # 줄의 맨 앞이 아니면 (wait_turn=False 는 줄을 서지 않으므로 줄이 비어야 한다)
                return None
            if owner in self._queue:
                self._queue.remove(owner)
            self._n += 1
            self._token = Token(owner, self._clock(), self._n)
            return self._token

    def release(self, token: Token | None) -> bool:
        """그 토큰이 지금 소유자일 때만 놓는다(낡은 토큰으로 남의 추출을 풀지 않게). 놓았으면 True."""
        with self._mu:
            if token is None or self._token != token:
                return False
            self._token = None
            return True


class LinkGuard:
    """PC Wi-Fi 재연결 관문 — 운전 중인 세트가 '모두' 안 들릴 때만, 간격을 모두가 함께 센다(명세 2-4).
    세트 하나의 Dock 이 꺼졌다고 PC 전체를 끊어 다른 세트의 추출 · 라이브를 깨뜨리지 않게."""

    def __init__(self, members, gap_s: float = 120.0):
        self._mu = threading.Lock()
        self._blind: dict[str, float | None] = {str(m): None for m in members}
        self._last = float("-inf")
        self.gap_s = gap_s

    def report(self, owner: str, blind_since: float | None) -> None:
        with self._mu:
            self._blind[str(owner)] = blind_since

    def remove(self, owner: str) -> None:
        """HOLD 로 간 세트는 '운전 중'에서 뺀다."""
        with self._mu:
            self._blind.pop(str(owner), None)

    def may_reconnect(self, now: float, after_s: float) -> bool:
        with self._mu:
            if not self._blind or now - self._last < self.gap_s:
                return False
            return all(b is not None and now - b >= after_s for b in self._blind.values())

    def spaced(self, now: float) -> bool:
        """복구 대기의 재연결 — 시험망이 안 닿으면(그것 자체가 전체 고장이다) 간격만 함께 센다."""
        with self._mu:
            return now - self._last >= self.gap_s

    def did_reconnect(self, now: float) -> None:
        with self._mu:
            self._last = now
