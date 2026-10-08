"""클라우드 전송 — TestPC 가 Supabase 프로젝트 cell-bench 에 상태·표본·사이클·이상을 올리고 원격 명령을 받아 온다.

방향은 항상 TestPC → 밖(HTTPS)이다. 회사망에 들어오는 구멍이 없어 Tailscale 같은 정책 문제가 없다.
키는 Windows 자격 증명 관리자(keyring service "cell-bench-cloud")의 url · service_key · bench_id 셋이다.
하나라도 없으면 꺼진 채(무음) 돈다 — 클라우드 때문에 시험이 멈추는 일은 없어야 한다.

전송은 큐 + 작업 스레드 하나. 실패는 1분에 한 번만 로그. 명령 가져오기(poll)만 동기 호출이고 5초 안에 끝난다.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

import keyring

SERVICE = "cell-bench-cloud"


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + "Z"


class Cloud:
    def __init__(self, log: Callable[[str], None] = print, sample_every_s: float = 60.0,
                 url: str | None = None, key: str | None = None, bench_id: str | None = None):
        self.log = log
        self.url = (url or _kr("url") or "").rstrip("/")
        self.key = key or _kr("service_key") or ""
        self.bench_id = bench_id or _kr("bench_id") or ""
        self.enabled = bool(self.url and self.key and self.bench_id)
        self.sample_every_s = sample_every_s
        self._last_sample = 0.0
        self._last_err_log = 0.0
        self._q: queue.Queue = queue.Queue(maxsize=500)
        if self.enabled:
            threading.Thread(target=self._worker, daemon=True, name="cloud-publisher").start()
            self.log(f"클라우드 전송 켜짐 · {self.url} · {self.bench_id}")
        else:
            self.log("클라우드 전송 꺼짐 (keyring cell-bench-cloud 의 url/service_key/bench_id 없음)")

    # ---- HTTP ----
    def _req(self, method: str, table: str, body=None, params: dict | None = None, prefer: str | None = None, timeout: float = 8.0):
        qs = ("?" + urllib.parse.urlencode(params)) if params else ""
        req = urllib.request.Request(f"{self.url}/rest/v1/{table}{qs}", method=method,
                                     data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None)
        req.add_header("apikey", self.key); req.add_header("Authorization", f"Bearer {self.key}")
        req.add_header("Content-Type", "application/json"); req.add_header("Accept", "application/json")
        if prefer:
            req.add_header("Prefer", prefer)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    def _submit(self, fn: Callable[[], None]) -> None:
        if not self.enabled:
            return
        try:
            self._q.put_nowait(fn)
        except queue.Full:
            self._err("전송 큐가 가득 참 — 이번 항목 버림")

    def _worker(self) -> None:
        while True:
            fn = self._q.get()
            try:
                fn()
            except Exception as e:          # 네트워크·서버 어떤 오류도 시험을 멈추지 않는다
                self._err(f"전송 실패: {e}")
            finally:
                self._q.task_done()

    def _err(self, msg: str) -> None:
        now = time.time()
        if now - self._last_err_log > 60:
            self._last_err_log = now
            self.log(f"클라우드: {msg}")

    # ---- 올리기 ----
    def state(self, payload: dict) -> None:
        row = {"bench_id": self.bench_id, "updated_at": _iso(time.time()),
               "cycle": payload.get("cycle"), "phase": payload.get("phase"), "payload": payload}
        self._submit(lambda: self._req("POST", "bench_state", [row], {"on_conflict": "bench_id"}, "resolution=merge-duplicates,return=minimal"))

    def sample(self, t: float, phase: str, cycle: int | None, plug_on: bool | None, watts: float | None, wh: float,
               batts: list[int], cells_alive: int) -> None:
        if t - self._last_sample < self.sample_every_s:
            return
        self._last_sample = t
        row = {"bench_id": self.bench_id, "t": _iso(t), "cycle": cycle, "phase": phase, "plug_on": plug_on,
               "watts": None if watts is None or watts != watts else round(watts, 2), "wh": round(wh, 3),
               "batt_min": min(batts) if batts else None, "batt_avg": round(sum(batts) / len(batts), 1) if batts else None,
               "batt_max": max(batts) if batts else None, "cells_alive": cells_alive}
        self._submit(lambda: self._req("POST", "bench_sample", [row], {"on_conflict": "bench_id,t"}, "resolution=ignore-duplicates,return=minimal"))

    def cycle(self, row: dict) -> None:
        body = {"bench_id": self.bench_id, "cycle": int(row["cycle"]), "row": row, "updated_at": _iso(time.time())}
        self._submit(lambda: self._req("POST", "bench_cycle", [body], {"on_conflict": "bench_id,cycle"}, "resolution=merge-duplicates,return=minimal"))

    def discharge(self, rows: list[dict]) -> None:
        body = [{"bench_id": self.bench_id, **r} for r in rows]
        self._submit(lambda: self._req("POST", "bench_discharge", body, {"on_conflict": "bench_id,cycle,serial"}, "resolution=merge-duplicates,return=minimal"))

    def event(self, t: float, cycle: int, phase: str, kind: str, serial, detail: str) -> None:
        row = {"bench_id": self.bench_id, "t": _iso(t), "cycle": cycle, "phase": phase, "kind": kind, "serial": str(serial), "detail": detail}
        self._submit(lambda: self._req("POST", "bench_event", [row], None, "return=minimal"))

    # ---- 명령 (동기) ----
    def poll_command(self) -> dict | None:
        """아직 집어 가지 않은 가장 오래된 명령 하나를 가져오고 taken_at 을 찍는다. 없으면 None."""
        if not self.enabled:
            return None
        try:
            rows = self._req("GET", "bench_command", None,
                             {"bench_id": f"eq.{self.bench_id}", "taken_at": "is.null", "order": "requested_at.asc", "limit": "1"}, timeout=5)
            if not rows:
                return None
            cmd = rows[0]
            self._req("PATCH", "bench_command", {"taken_at": _iso(time.time())}, {"id": f"eq.{cmd['id']}"}, "return=minimal", timeout=5)
            return cmd
        except Exception as e:
            self._err(f"명령 확인 실패: {e}")
            return None

    def ack(self, cmd_id: int, result: str) -> None:
        self._submit(lambda: self._req("PATCH", "bench_command", {"result": result}, {"id": f"eq.{cmd_id}"}, "return=minimal"))


class NoCloud:
    """클라우드를 쓰지 않을 때의 빈 구현."""
    enabled = False
    def state(self, *a, **k): pass
    def sample(self, *a, **k): pass
    def cycle(self, *a, **k): pass
    def discharge(self, *a, **k): pass
    def event(self, *a, **k): pass
    def poll_command(self): return None
    def ack(self, *a, **k): pass


def _kr(name: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, name)
    except Exception:
        return None
