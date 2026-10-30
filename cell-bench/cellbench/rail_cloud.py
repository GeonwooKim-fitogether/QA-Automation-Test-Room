"""레일 판정 파일을 클라우드 표 rail_run 에 올린다 — 클라우드 결과판이 셀 내구 시험과 함께 레일 시험 결과를 읽는다.

제어 PC2 의 serve_board.py 가 1분마다 data/ 를 훑어, 새로 생겼거나 다시 쓴 protocol_<시각>.json 을 올린다(upsert).
실시간 값(data/qc_now.json)은 2초마다 살펴 바뀌었을 때만 표 rail_now 의 한 줄을 덮어쓴다 — 시험 중에만 쓰고, 시험이 끝나면
'시험 없음'을 한 번 쓰고 멈춘다. 클라우드 결과판의 '레일 시험 > 실시간' 화면이 그 줄을 읽는다.
판정 코드는 아무것도 부르지 않아도 된다. 손으로 한 번 올리려면 python serve_board.py --rail-sync.

키는 cloud.py 와 같은 Windows 자격 증명 관리자(keyring "cell-bench-cloud")의 url · service_key 둘이다. bench_id 는 쓰지 않는다.
키가 없으면 꺼진 채 돈다(상태 off) — 결과판이 레일 목록 위에 그 사실을 한 줄로 보인다.
판정 파일이 없는 PC(TestPC)는 클라우드에 묻지도 않는다.

다시 계산하지 않는다 — 파일은 그대로(키 순서까지) 올리고, 목록 한 줄은 serve_board.rail_summary 가 만든 것을 함께 싣는다.
NaN 이 든 파일은 올리지 않고 형식 오류로 남긴다(qc-protocol-v4.md 7절: 값 없음은 null). 그 파일을 다시 쓰면 다시 해 본다.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from cellbench.cloud import _kr, _why

EVERY_S = 60.0          # data/ 를 훑는 간격
LIVE_EVERY_S = 2.0      # 실시간 값(qc_now.json)을 살피는 간격 — 바뀌었을 때만 rail_now 한 줄을 덮어쓴다
LIVE_IDLE = {"step": {"key": "idle", "index": None}}   # qc-protocol-v4.md 7-6 의 4 — 시험 없음
SETTLE_S = 2.0          # 이보다 최근에 쓴 파일은 아직 쓰는 중일 수 있어 다음 번에 올린다
SAME_MTIME_S = 1.0      # 클라우드의 file_mtime 과 이만큼 안쪽이면 같은 파일로 본다
FAIL_LOG_GAP_S = 600.0  # 같은 실패를 로그에 다시 적는 간격


def _iso(t: float | None) -> str | None:
    return None if t is None else datetime.fromtimestamp(t, timezone.utc).isoformat()


def _epoch(s) -> float | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class RailCloud:
    """summarize(name, doc) 는 serve_board.rail_summary, name_ok(name) 는 serve_board.rail_name_ok — 목록 한 줄과 이름 규칙을
    결과판 서버와 똑같이 쓰려고 받는다."""

    def __init__(self, data: Path, summarize: Callable[[str, dict], dict], name_ok: Callable[[str], bool],
                 url: str | None = None, key: str | None = None, source: str | None = None,
                 log: Callable[[str], None] = print, clock: Callable[[], float] = time.time):
        pick = lambda v, k: _kr(k) if v is None else v
        self.url = (pick(url, "url") or "").rstrip("/")
        self.key = pick(key, "service_key") or ""
        self.enabled = bool(self.url and self.key)
        self.data = Path(data)
        self.summarize, self.name_ok = summarize, name_ok
        self.source = source or socket.gethostname()
        self.log, self._clock = log, clock
        self._lock = threading.Lock()
        self._cloud: dict[str, float | None] = {}     # 클라우드에 있는 파일 → 그때의 file_mtime
        self._known = False                           # 클라우드 목록을 한 번이라도 받았나
        self._bad: dict[str, tuple[float, str]] = {}  # 올리지 못한 파일 → (그때의 mtime, 이유)
        self._ok_t: float | None = None
        self._err: str | None = None
        self._err_t: float | None = None
        self._err_logged: str | None = None
        self._err_log_t = float("-inf")
        self._live_lock = threading.Lock()
        self._live_mtime: float | None = None         # 마지막으로 올린 qc_now.json 의 mtime
        self._live_last: str | None = None            # 마지막으로 올린 것: run · idle · err (None = 아직 안 올림)
        self._live_ok_t: float | None = None
        self._live_err: str | None = None

    # ---- HTTP ----
    def _req(self, method: str, path: str, body=None, prefer: str | None = None, timeout: float = 8.0):
        req = urllib.request.Request(f"{self.url}/rest/v1/{path}", method=method,
                                     data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
                                     if body is not None else None)
        req.add_header("apikey", self.key); req.add_header("Authorization", f"Bearer {self.key}")
        req.add_header("Content-Type", "application/json"); req.add_header("Accept", "application/json")
        if prefer:
            req.add_header("Prefer", prefer)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    # ---- 훑기 ----
    def local(self) -> dict[str, float]:
        out = {}
        for p in self.data.glob("protocol_*.json"):
            if self.name_ok(p.name):
                try:
                    out[p.name] = p.stat().st_mtime
                except OSError:
                    pass
        return out

    def _same(self, name: str, mtime: float) -> bool:
        m = self._cloud.get(name)
        return m is not None and abs(m - mtime) <= SAME_MTIME_S

    def _due(self, files: dict[str, float], now: float) -> list[str]:
        return [n for n, m in sorted(files.items())
                if now - m >= SETTLE_S and not self._same(n, m) and self._bad.get(n, (None,))[0] != m]

    def sync(self) -> int:
        """한 번 훑어 올릴 것을 올리고 올린 개수를 돌려준다. 실패는 상태에 남기고 던지지 않는다 — 다음 번에 다시 한다."""
        if not self.enabled:
            return 0
        with self._lock:
            files = self.local()
            todo = self._due(files, self._clock())
            if not todo:
                return 0
            sent = 0
            try:
                if not self._known:
                    for r in self._req("GET", "rail_run?select=name,file_mtime") or []:
                        self._cloud[r["name"]] = _epoch(r.get("file_mtime"))
                    self._known = True
                    todo = [n for n in todo if not self._same(n, files[n])]
                for name in todo:
                    sent += self._push(name, files[name])
                self._ok_t, self._err = self._clock(), None
            except Exception as e:
                self._fail(_why(e))
            if sent:
                self.log(f"레일 클라우드: 판정 파일 {sent}개 올림")
            return sent

    def _push(self, name: str, mtime: float) -> int:
        try:
            doc = json.loads((self.data / name).read_text(encoding="utf-8"))
            json.dumps(doc, allow_nan=False)
        except (OSError, ValueError) as e:
            why = "파일 형식 오류 — NaN" if isinstance(e, ValueError) and "Out of range float" in str(e) else "읽지 못함"
            self._bad[name] = (mtime, why)
            return 0
        if not isinstance(doc, dict):
            self._bad[name] = (mtime, "읽지 못함")
            return 0
        summary = self.summarize(name, doc)
        self._req("POST", "rail_run?on_conflict=name", prefer="resolution=merge-duplicates,return=minimal",
                  body={"name": name, "t": _iso(summary.get("t")), "summary": summary, "doc": doc, "source": self.source,
                        "file_mtime": _iso(mtime), "uploaded_at": _iso(self._clock())})
        self._cloud[name] = mtime
        self._bad.pop(name, None)
        return 1

    def push_now(self) -> bool:
        """qc_now.json 이 바뀌었으면 rail_now 한 줄을 덮어쓰고 True. 시험 없음은 한 번만 쓴다 — 파일이 없어졌을 때는 이 PC 가
        시험 중 값을 올린 적이 있을 때만(판정 파일 · 실시간 파일이 없는 TestPC 는 클라우드에 쓰지 않는다)."""
        if not self.enabled:
            return False
        with self._live_lock:
            p = self.data / "qc_now.json"
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = None
            if mtime is None:
                if self._live_last != "run":
                    return False
                payload, kind, mtime = LIVE_IDLE, "idle", self._clock()
            else:
                if mtime == self._live_mtime:
                    return False
                try:
                    payload = json.loads(p.read_text(encoding="utf-8"))
                    json.dumps(payload, allow_nan=False)
                    step = payload.get("step") if isinstance(payload, dict) else None
                    kind = "idle" if isinstance(step, dict) and step.get("key") == "idle" else "run"
                except OSError:
                    return False                      # 쓰는 프로그램이 바꿔치기하는 순간 — 다음 차례에 다시
                except ValueError:
                    payload, kind = {"_error": "qc_now.json 형식 오류 — 쓰는 프로그램 확인"}, "err"
                if kind == "idle" and self._live_last == "idle":
                    self._live_mtime = mtime
                    return False
            try:
                self._req("POST", "rail_now?on_conflict=id", prefer="resolution=merge-duplicates,return=minimal",
                          body={"id": "rail", "payload": payload, "file_mtime": _iso(mtime), "source": self.source,
                                "updated_at": _iso(self._clock())})
            except Exception as e:
                self._live_err = _why(e)
                self._log_fail(f"실시간 값 — {self._live_err}", "2초 뒤 다시")
                return False
            self._live_mtime, self._live_last = mtime, kind
            self._live_ok_t, self._live_err = self._clock(), None
            return True

    def _fail(self, why: str) -> None:
        self._err, self._err_t = why, self._clock()
        self._log_fail(why, "1분 뒤 다시")

    def _log_fail(self, why: str, again: str) -> None:
        now = self._clock()
        if why != self._err_logged or now - self._err_log_t >= FAIL_LOG_GAP_S:
            self.log(f"레일 클라우드 올리기 실패 — {why} ({again})")
            self._err_logged, self._err_log_t = why, now

    def status(self) -> dict:
        """결과판 레일 목록 위 한 줄 — state: off(키 없음) · wait(아직 확인 전) · ok · fail(마지막 시도가 실패)."""
        files = self.local()
        if not self.enabled:
            state = "off"
        elif self._err is not None:
            state = "fail"
        elif not self._known and files:
            state = "wait"
        else:
            state = "ok"
        return {"state": state, "local": len(files),
                "uploaded": sum(1 for n, m in files.items() if self._same(n, m)),
                "bad": {n: why for n, (m, why) in self._bad.items() if files.get(n) == m},
                "last_ok": self._ok_t, "error": self._err, "error_t": self._err_t,
                "live": {"last": self._live_last, "last_ok": self._live_ok_t, "error": self._live_err}}

    def start(self, every_s: float = EVERY_S, live_every_s: float = LIVE_EVERY_S) -> threading.Thread | None:
        if not self.enabled:
            self.log("레일 클라우드 꺼짐 (keyring cell-bench-cloud 의 url/service_key 없음)")
            return None

        def run():
            while True:
                self.sync()
                time.sleep(every_s)

        def live():
            while True:
                self.push_now()
                time.sleep(live_every_s)

        threading.Thread(target=live, daemon=True, name="rail-cloud-live").start()
        th = threading.Thread(target=run, daemon=True, name="rail-cloud")
        th.start()
        self.log(f"레일 클라우드 켜짐 · {self.url} · 판정 파일은 {every_s:.0f}초마다, 실시간 값은 {live_every_s:.0f}초마다 {self.data} 를 본다")
        return th
