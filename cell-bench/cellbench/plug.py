"""스마트 플러그(Tapo P110M) — 켜기·끄기·전력 읽기.

python-kasa 를 쓴다. 플러그는 Tapo 앱에서 "Third-Party Compatibility" 를 켜 둔 상태여야
예전 방식(KLAP)으로 응답한다. 계정은 Windows 자격 증명 관리자(keyring)에서 읽고 어디에도 찍지 않는다.
시험망에 인터넷이 없어 플러그의 날짜별 누적(kWh)은 쓸 수 없다 → 순간 전력(W)을 PC 가 적분한다.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import keyring
from kasa import Credentials, Discover

from .config import Config


@dataclass(frozen=True)
class PlugReading:
    on: bool
    watts: float
    t: float


class Plug:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        user = keyring.get_password(cfg.keyring_service, "username")
        pw = keyring.get_password(cfg.keyring_service, "password")
        if not user or not pw:
            raise RuntimeError("TP-Link 계정이 자격 증명 관리자에 없다 (tools/plug_cli.py setup)")
        self._creds = Credentials(user, pw)
        self.ip: str | None = cfg.plug_ip_hint or None

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
        for _ in range(self.cfg.plug_retries):
            try:
                return asyncio.run(asyncio.wait_for(self._do(action), timeout=limit))
            except asyncio.TimeoutError:
                last = TimeoutError(f"{limit:.0f}초 안에 응답 없음")
                time.sleep(2)
            except Exception as e:      # 무선이라 가끔 놓친다. 정해진 횟수만 재시도
                last = e
                time.sleep(2)
        raise RuntimeError(f"플러그 {action} 실패 ({self.cfg.plug_retries}회): {last}")

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
