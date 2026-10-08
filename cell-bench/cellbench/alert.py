"""알림 — 이상이 나면 휴대폰으로 메시지. Slack 수신 웹훅 하나면 된다.

웹훅 주소는 Windows 자격 증명 관리자(keyring)에 두고 코드·설정 파일에 넣지 않는다.
주소가 없으면 아무것도 보내지 않고 조용히 지나간다 — 알림 때문에 시험이 멈추는 일은 없어야 한다.
PC 는 유선 인터넷이 있으므로 시험망(인터넷 없음)과 무관하게 나간다.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Callable

import keyring

SERVICE = "cell-bench-remote"       # keyring: slack_webhook · pin


def make_notifier(min_gap_s: float = 30.0, hook: str | None = None) -> Callable[[str], None]:
    """같은 글이 min_gap_s 안에 반복되면 한 번만 보낸다. 전송은 다른 스레드에서, 실패는 삼킨다."""
    url = hook if hook is not None else _webhook()
    last: dict[str, float] = {}

    def send(text: str) -> None:
        if not url:
            return
        now = time.time()
        if now - last.get(text, 0) < min_gap_s:
            return
        last[text] = now
        threading.Thread(target=_post, args=(url, text), daemon=True).start()

    return send


def _webhook() -> str | None:
    try:
        return keyring.get_password(SERVICE, "slack_webhook")
    except Exception:
        return None


def _post(url: str, text: str) -> None:
    try:
        req = urllib.request.Request(url, data=json.dumps({"text": text}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass
