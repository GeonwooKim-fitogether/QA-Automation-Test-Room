"""알림 — 이상이 나면 휴대폰으로 메시지. Slack 수신 웹훅 하나면 된다.

웹훅 주소는 Windows 자격 증명 관리자(keyring)에 두고 코드·설정 파일에 넣지 않는다.
주소가 없으면 아무것도 보내지 않고 조용히 지나간다 — 알림 때문에 시험이 멈추는 일은 없어야 한다.
PC 는 유선 인터넷이 있으므로 시험망(인터넷 없음)과 무관하게 나간다.

두 가지가 있다.
  make_notifier()  엔진(run_cycle.py)이 쓰는 단순 알림 — 같은 글을 30초 안에 한 번만.
  Alerter          감시자가 쓰는 신호등 알림 — 불(정상·준비·조치·미확인)이 바뀔 때와 조치가 이어지는 동안만 보낸다.

신호등 알림 규칙 (FMEA P3 — 주말·야간에 아무도 안 봐서 고장이 48시간 뒤 발견되던 것을 막는다)
  준비(노랑)   새로 켜지면 1건. 같은 키·같은 이유는 1시간 안에 다시 보내지 않는다.
  조치(빨강)   켜지는 즉시 1건, 그 뒤 15분마다 다시 — 사람이 '확인'(data/alert_ack.json)을 누를 때까지.
  미확인(회색) 판정할 수 없음. 빨강과 똑같이 다룬다.
  복구         빨강·회색이 노랑·초록으로 돌아오면 1건.
메시지 머리말에 불 이름을 글자로 쓴다(색에만 기대지 않는다): "[셀 시험대 hq-bench-1] 조치 · 엔진 · ...".
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

import keyring

from .control import read_json, write_json_atomic

SERVICE = "cell-bench-remote"       # keyring: slack_webhook · pin

STATE_FILE = "alert_state.json"     # 신호등 알림이 무엇을 언제 보냈나 — 감시자가 다시 떠도 중복 발송하지 않게
ACK_FILE = "alert_ack.json"         # {키: 확인 시각} — 결과판의 '확인' 버튼이 쓴다(write_ack)

LIGHTS = {"green": "정상", "yellow": "준비", "red": "조치", "unknown": "미확인"}
URGENT = {"red", "unknown"}         # 사람이 와야 하는 불 — 확인까지 반복
KEY_LABELS = {"engine": "엔진", "plug_on": "플러그", "board": "결과판", "net": "시험망"}

YELLOW_GAP_S = 3600.0               # 같은 키·같은 이유의 노랑은 이 안에 다시 보내지 않는다
RED_REPEAT_S = 900.0                # 빨강·회색은 확인 전까지 이 간격으로 다시 보낸다
WEBHOOK_RECHECK_S = 300.0           # 웹훅이 없으면 이만큼마다 자격 증명 관리자를 다시 본다 (감시자를 다시 띄우지 않아도 이어 받게)
PENDING_MAX = 20                    # 보내지 못한 메시지를 이만큼까지 들고 있다가 다음에 함께 보낸다
SEND_TIMEOUT_S = 5.0


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


def _request(url: str, text: str) -> urllib.request.Request:
    return urllib.request.Request(url, data=json.dumps({"text": text}).encode("utf-8"),
                                  headers={"Content-Type": "application/json"})


def _post(url: str, text: str) -> None:
    try:
        urllib.request.urlopen(_request(url, text), timeout=10).read()
    except Exception:
        pass


class SlackSender:
    """웹훅으로 한 건을 바로 보낸다. 묶기·중복 판단은 하지 않는다(Alerter 가 한다).

    웹훅이 없으면 아무것도 하지 않고 돌아온다(무음). 보내다 실패하면 예외를 그대로 올린다 — Alerter 가 들고 있다가
    다음 점검에서 다시 보낸다. 웹훅이 없으면 WEBHOOK_RECHECK_S 마다 자격 증명 관리자를 다시 읽어, 감시자가 도는 중에
    tools/remote_setup.py 로 웹훅을 넣어도 감시자를 다시 띄우지 않고 이어 받는다. hook 을 주면(검사) 다시 읽지 않는다.
    """

    def __init__(self, hook: str | None = None, clock: Callable[[], float] = time.time,
                 post: Callable[[str, str], None] | None = None):
        self._fixed = hook is not None
        self._url = hook if hook is not None else _webhook()
        self._clock = clock
        self._checked = clock()
        self._post = post or (lambda url, text: urllib.request.urlopen(_request(url, text), timeout=SEND_TIMEOUT_S).read())

    def has_webhook(self) -> bool:
        if not self._url and not self._fixed and self._clock() - self._checked >= WEBHOOK_RECHECK_S:
            self._checked = self._clock()
            self._url = _webhook()
        return bool(self._url)

    def __call__(self, text: str) -> None:
        if self.has_webhook():
            self._post(self._url, text)


def write_ack(ack_path: Path, key: str, t: float | None = None) -> dict:
    """사람이 '확인'을 눌렀다 — {키: 확인 시각} 을 더해 쓴다. 그 키의 빨강이 이 시각 전에 시작했으면 반복을 멈춘다.
    결과판(뒤 단계)이 부른다. 다른 키의 확인은 그대로 둔다."""
    acks = read_json(Path(ack_path)) or {}
    acks[str(key)] = time.time() if t is None else float(t)
    write_json_atomic(Path(ack_path), acks)
    return acks


def _dur(seconds: float) -> str:
    m = max(0, int(seconds // 60))
    return f"{m // 60}시간 {m % 60}분" if m >= 120 else f"{m}분"


class Alerter:
    """신호등 알림기 — 키마다 불을 받아 직전 불과 비교하고, 위 규칙대로 보낼 것만 보낸다.

    send: 메시지 한 건을 보내는 함수(SlackSender). 보낼 것이 여러 건이면 한 메시지에 줄을 나눠 한 번에 보낸다.
    state_path: 무엇을 언제 보냈나를 남기는 파일(원자적 저장). None 이면 메모리에만 둔다(모의 점검 · 검사).
    ack_path: 사람이 누른 '확인'(write_ack). None 이면 확인이 없는 것으로 본다.
    웹훅이 없어도 상태는 똑같이 기록한다 — 신호등이 'Slack 미연결'과 지금 불을 보여 줄 수 있게(has_webhook).
    """

    def __init__(self, send: Callable[[str], None], state_path: Path | None, ack_path: Path | None,
                 clock: Callable[[], float] = time.time, bench: str = ""):
        self._send = send
        self.state_path = Path(state_path) if state_path else None
        self.ack_path = Path(ack_path) if ack_path else None
        self._clock = clock
        self.bench = bench
        st = (read_json(self.state_path) if self.state_path else None) or {}
        self._st = {"keys": dict(st.get("keys") or {}), "yellow_sent": dict(st.get("yellow_sent") or {}),
                    "pending": list(st.get("pending") or [])}
        self._test_text: str | None = None
        self._test_done = False

    # ---- 바깥에서 보는 것 ----
    def has_webhook(self) -> bool:
        h = getattr(self._send, "has_webhook", None)
        return bool(h()) if callable(h) else True

    def snapshot(self) -> dict:
        """{키: {light, since, reason, acked}} — data/supervisor.json 의 alerts 로 신호등이 읽는다."""
        acks = self._acks()
        return {k: {"light": r.get("light"), "since": r.get("since"), "reason": r.get("reason", ""),
                    "acked": r.get("light") in URGENT and self._acked(acks, k, r.get("since"))}
                for k, r in self._st["keys"].items()}

    def test(self, text: str) -> bool:
        """시작 시 시험 알림 — 이 프로세스에서 한 번. 지금 보냈으면 True.
        웹훅이 아직 없거나 보내다 실패하면 들고 있다가, 웹훅이 생기는(또는 전송이 되는) 첫 update() 에서 보낸다."""
        if not (self._test_done or self._test_text):
            self._test_text = self._fmt("시험", None, text)
            self._try_test()
        return self._test_done

    # ---- 불 받기 ----
    def update(self, lights: dict, now_t: float | None = None) -> list[str]:
        """lights = {키: (불, 이유 한 줄)}. 이번에 새로 보낼 메시지들을 돌려준다(웹훅이 없어도 '보낸 것'으로 기록한다).

        lights 에 없는 키는 건드리지 않는다 — 다른 단계가 다른 키를 맡을 수 있다.
        모르는 불 이름은 판정할 수 없는 것으로 보고 '미확인'으로 다룬다.
        """
        now = self._clock() if now_t is None else float(now_t)
        st = self._st
        before = json.dumps(st, sort_keys=True, ensure_ascii=False)
        acks = self._acks()
        keys, ysent = st["keys"], st["yellow_sent"]
        out: list[str] = []
        for key, val in lights.items():
            light, reason = (val[0], str(val[1] or "")) if isinstance(val, (list, tuple)) else (val, "")
            light = light if light in LIGHTS else "unknown"
            prev = keys.get(key) or {"light": "green", "reason": "", "since": now}
            rec = dict(prev)
            if light in URGENT:
                if light != prev.get("light") or reason != prev.get("reason"):
                    # 새 사건 — 빨강이 아니던 키가 빨강이 됐거나, 빨강의 원인이 바뀌었다 (원인이 바뀌면 확인도 새로 받는다)
                    rec = {"light": light, "reason": reason, "since": now, "sent_t": now, "sent_n": 1}
                    out.append(self._fmt(LIGHTS[light], key, reason))
                elif (not self._acked(acks, key, rec.get("since"))
                      and now - float(rec.get("sent_t") or 0) >= RED_REPEAT_S):
                    rec["sent_t"], rec["sent_n"] = now, int(rec.get("sent_n") or 1) + 1
                    word = f"{LIGHTS[light]} (계속 · {_dur(now - float(rec['since']))}째 · 확인 전까지 15분마다)"
                    out.append(self._fmt(word, key, reason))
            else:
                if prev.get("light") in URGENT:
                    now_is = LIGHTS[light] + (f": {reason}" if light == "yellow" and reason else "")
                    out.append(self._fmt("복구", key, f"지금 {now_is} ({_dur(now - float(prev.get('since') or now))} 만에) · "
                                                      f"이전: {prev.get('reason', '')}"))
                    if light == "yellow":
                        ysent[f"{key}|{reason}"] = now
                elif light == "yellow" and (prev.get("light") != "yellow" or prev.get("reason") != reason):
                    k = f"{key}|{reason}"
                    if now - float(ysent.get(k, float("-inf"))) >= YELLOW_GAP_S:
                        ysent[k] = now
                        out.append(self._fmt(LIGHTS["yellow"], key, reason))
                if light != prev.get("light"):
                    rec = {"light": light, "reason": reason, "since": now}
                rec["reason"] = reason
            keys[key] = rec
        for k in [k for k, t in ysent.items() if now - float(t) >= YELLOW_GAP_S]:
            del ysent[k]
        if out:
            st["pending"] = (st["pending"] + out)[-PENDING_MAX:]
        self._flush()
        self._try_test()
        if json.dumps(st, sort_keys=True, ensure_ascii=False) != before:
            self._save()
        return out

    # ---- 안쪽 ----
    def _fmt(self, word: str, key: str | None, text: str) -> str:
        head = f"[셀 시험대 {self.bench}] {word}" if self.bench else f"[셀 시험대] {word}"
        return f"{head} · {KEY_LABELS.get(key, key)} · {text}" if key else f"{head} · {text}"

    def _flush(self) -> None:
        """들고 있는 메시지를 한 번에 보낸다. 실패하면 그대로 들고 있다가 다음 update() 에서 다시."""
        if not self._st["pending"]:
            return
        try:
            self._send("\n".join(self._st["pending"]))
        except Exception:
            return
        self._st["pending"] = []

    def _try_test(self) -> None:
        if self._test_text and self.has_webhook():
            try:
                self._send(self._test_text)
            except Exception:
                return
            self._test_text, self._test_done = None, True

    def _acks(self) -> dict:
        return (read_json(self.ack_path) if self.ack_path else None) or {}

    @staticmethod
    def _acked(acks: dict, key: str, since) -> bool:
        try:
            return float(acks[key]) >= float(since)
        except (KeyError, TypeError, ValueError):
            return False

    def _save(self) -> None:
        if self.state_path:
            try:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                write_json_atomic(self.state_path, self._st)
            except OSError:
                pass
