"""스마트 플러그(Tapo P110M) — 켜기·끄기·전력 읽기.

python-kasa 를 쓴다. 플러그는 Tapo 앱에서 "Third-Party Compatibility" 를 켜 둔 상태여야
예전 방식(KLAP)으로 응답한다. 계정은 Windows 자격 증명 관리자(keyring)에서 읽고 어디에도 찍지 않는다.
시험망에 인터넷이 없어 플러그의 날짜별 누적(kWh)은 쓸 수 없다 → 순간 전력(W)을 PC 가 적분한다.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

import keyring
from kasa import Credentials, Discover

from .config import Config
from .control import read_json, write_json_atomic


@dataclass(frozen=True)
class PlugReading:
    on: bool
    watts: float
    t: float


class Plug:
    # 신호등용 기록 (metrics()). 검사가 생성자를 건너뛰고 만들어도 돌도록 기본값을 클래스에 둔다
    stats_path: Path | None = None       # 릴레이 동작 누적 횟수를 남기는 파일 (엔진만 준다 — data/plug_stats.json)
    toggles_total: int = 0               # 켜짐↔꺼짐이 실제로 바뀐 횟수 누적 (릴레이 수명, FMEA 4.6)
    last_call_s: float | None = None     # 마지막 호출 한 번(재시도 포함)에 걸린 초
    _fails: tuple[float, ...] = ()       # 실패한 시도의 시각 (1시간치)
    _last_on: bool | None = None         # 마지막으로 읽은 켜짐 여부 — 명령 뒤 상태와 비교해 실제로 바뀌었는지 센다

    def __init__(self, cfg: Config, stats_path: str | Path | None = None):
        self.cfg = cfg
        user = keyring.get_password(cfg.keyring_service, "username")
        pw = keyring.get_password(cfg.keyring_service, "password")
        if not user or not pw:
            raise RuntimeError("TP-Link 계정이 자격 증명 관리자에 없다 (tools/plug_cli.py setup)")
        self._creds = Credentials(user, pw)
        self.ip: str | None = cfg.plug_ip_hint or None
        if stats_path:
            self.stats_path = Path(stats_path)
            self.toggles_total = int((read_json(self.stats_path) or {}).get("toggles_total") or 0)

    # --- 내부: 연결 ---
    async def _connect(self):
        if self.ip:
            try:
                dev = await Discover.discover_single(self.ip, credentials=self._creds, timeout=4)
                if dev.mac.upper().replace("-", ":") == self.cfg.plug_mac.upper():
                    return dev
            except Exception:
                pass
        found = await Discover.discover(target=self.cfg.broadcast, credentials=self._creds, discovery_timeout=5)
        for ip, dev in found.items():
            if (dev.mac or "").upper().replace("-", ":") == self.cfg.plug_mac.upper():
                self.ip = ip
                return dev
        raise RuntimeError(f"플러그 {self.cfg.plug_mac} 를 시험망에서 찾지 못함")

    async def _do(self, action: str) -> PlugReading:
        dev = await self._connect()
        try:
            if action == "on":
                await dev.turn_on()
            elif action == "off":
                await dev.turn_off()
            await dev.update()
            em = dev.modules.get("Energy")
            w = float(em.current_consumption) if em and em.current_consumption is not None else float("nan")
            return PlugReading(bool(dev.is_on), w, time.time())
        finally:
            await dev.disconnect()

    def _call(self, action: str) -> PlugReading:
        """호출 한 번마다 상한 시간(plug_call_timeout_s)을 둔다. 무선이 반쯤 끊겨 응답이 영영 안 오면
        흐름 전체가 그 자리에 멈추고, 방전 중이었다면 플러그 OFF 인 채로 셀이 다 꺼진다(FMEA 2.2)."""
        last: Exception | None = None
        limit = self.cfg.plug_call_timeout_s
        t0 = time.monotonic()
        try:
            for _ in range(self.cfg.plug_retries):
                try:
                    r = asyncio.run(asyncio.wait_for(self._do(action), timeout=limit))
                    self._count(action, r)
                    return r
                except asyncio.TimeoutError:
                    last = TimeoutError(f"{limit:.0f}초 안에 응답 없음")
                    self._failed()
                    time.sleep(2)
                except Exception as e:      # 무선이라 가끔 놓친다. 정해진 횟수만 재시도
                    last = e
                    self._failed()
                    time.sleep(2)
            raise RuntimeError(f"플러그 {action} 실패 ({self.cfg.plug_retries}회): {last}")
        finally:
            self.last_call_s = round(time.monotonic() - t0, 1)

    # --- 신호등용 기록 ---
    def _failed(self) -> None:
        now = time.time()
        self._fails = tuple(t for t in self._fails if now - t < 3600) + (now,)

    def _count(self, action: str, r: PlugReading) -> None:
        """켜기·끄기 뒤 상태가 직전에 읽은 상태와 다르면 릴레이가 한 번 움직인 것으로 센다(직전 상태를 모르면 센다 — 상한 쪽).
        이미 켜진 플러그에 켜기를 보내면 세지 않는다. 엔진만 파일에 남긴다(stats_path)."""
        if action in ("on", "off") and (self._last_on is None or self._last_on != r.on):
            self.toggles_total += 1
            if self.stats_path:
                write_json_atomic(self.stats_path, {"toggles_total": self.toggles_total, "t": time.time()})
        self._last_on = r.on

    def metrics(self, now: float | None = None) -> dict:
        """now.json metrics.plug — {retries_1h: 지난 1시간 실패한 시도 수, last_call_s, toggles_total}."""
        now = time.time() if now is None else now
        return {"retries_1h": sum(1 for t in self._fails if now - t < 3600),
                "last_call_s": self.last_call_s, "toggles_total": self.toggles_total}

    # --- 공개 ---
    def read(self) -> PlugReading:
        return self._call("read")

    def on(self) -> PlugReading:
        return self._call("on")

    def off(self) -> PlugReading:
        return self._call("off")

    def recharge(self, gap_s: float = 10.0) -> PlugReading:
        """OFF → gap_s 초 → ON. Dock 은 만충 뒤 충전을 멈추면 플러그가 켜진 채로는 다시 시작하지 않는다
        (2026-10-07 실측). 그래서 '충전 쪽 안전 상태'는 단순 ON 이 아니라 껐다 켜기여야 한다."""
        self._call("off")
        time.sleep(gap_s)
        return self._call("on")
