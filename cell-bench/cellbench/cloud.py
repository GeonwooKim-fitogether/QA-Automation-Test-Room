"""클라우드 전송 — TestPC 가 Supabase 프로젝트 cell-bench 에 상태·표본·사이클·이상을 올리고 원격 명령을 받아 온다.

방향은 항상 TestPC → 밖(HTTPS)이다. 회사망에 들어오는 구멍이 없어 Tailscale 같은 정책 문제가 없다.
키는 Windows 자격 증명 관리자(keyring service "cell-bench-cloud")의 url · service_key · bench_id 셋이다.
하나라도 없으면 꺼진 채(무음) 돈다 — 클라우드 때문에 시험이 멈추는 일은 없어야 한다.

전송은 보낼 일 목록 + 작업 스레드 하나. 명령 가져오기(poll)만 동기 호출이고 5초 안에 끝난다.

실패를 버리지 않는다 (FMEA 7.4 · 7.5 · 3.7)
  재시도      실패한 전송은 30초 · 2분 · 10분 뒤에 다시 해 본다(최대 3번). 그래도 안 되면 버리고 dropped 로 센다.
              상태(bench_state)는 더 새 상태가 이미 들어와 있으면 옛것을 다시 보내지 않는다 — 옛 화면으로 덮어쓰지 않게.
  상한        목록은 QUEUE_MAX 건까지. 가득 차면 가장 오래된 표본부터 버린다(그다음 상태) — 사이클·이상·명령 응답이 먼저다.
  키 만료     401 · 403 이 연속 3번이면 on_alert 로 한 번 알린다. 한 번이라도 성공하면 다시 셀 수 있게 풀린다.
  실패 기록   on_fail(종류, 내용) — 10분에 한 번으로 묶어 부른다. 엔진이 events 에 cloud_fail 로 남길 수 있게.
  통계        stats() — 마지막 성공·실패 시각, 최근 1시간 실패 수, 연속 키 거부 수, 남은 목록, 버린 수 (신호등이 읽는다).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Callable

import keyring

SERVICE = "cell-bench-cloud"

RETRY_DELAYS_S = (30.0, 120.0, 600.0)   # 실패 뒤 다시 해 보는 간격 — 이 수만큼(3번) 다시 한다
QUEUE_MAX = 500                         # 보낼 일 목록 상한 (재시도를 기다리는 것 포함)
DROP_FIRST = ("sample", "state")        # 가득 차면 이 순서로, 그 종류 중 가장 오래된 것부터 버린다
AUTH_FAIL_ALERT_AFTER = 3               # 401·403 이 이만큼 연속이면 키 만료로 보고 알린다
FAIL_HOOK_GAP_S = 600.0                 # on_fail 은 이 간격에 한 번만
KEY_REJECTED = "클라우드 키가 거부됨 — 키 만료·교체 확인 (tools/cloud_setup.py)"


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + "Z"


def _why(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    return f"{type(e).__name__}: {e}"


@dataclass
class _Job:
    kind: str                   # state · sample · cycle · discharge · event · ack
    fn: Callable[[], object]
    seq: int                    # 넣은 순서 — 작을수록 오래된 것
    tries: int = 0              # 지금까지 잡은 재시도 수
    due: float = 0.0            # 이 시각부터 보낸다 (처음은 0 = 바로)


class _Outbox:
    """보낼 일 목록 — 지금 보낼 것과 재시도를 기다리는 것을 한 목록에 둔다(상한 cap). 작업 스레드 하나가 꺼낸다."""

    def __init__(self, cap: int, clock: Callable[[], float]):
        self.cap = cap
        self._clock = clock
        self._jobs: list[_Job] = []
        self._busy = 0
        self._cv = threading.Condition()

    def __len__(self) -> int:
        with self._cv:
            return len(self._jobs)

    @property
    def unfinished_tasks(self) -> int:
        """아직 끝나지 않은 일 (목록 + 보내는 중). queue.Queue 와 같은 이름 — 검사가 다 보냈는지 기다릴 때 쓴다."""
        with self._cv:
            return len(self._jobs) + self._busy

    def put(self, job: _Job) -> _Job | None:
        """넣는다. 가득 찼으면 하나를 버리고 그것을 돌려준다(새로 넣으려던 것일 수도 있다)."""
        with self._cv:
            dropped = None
            if len(self._jobs) >= self.cap:
                dropped = self._victim(job)
                if dropped is job:
                    return job
                self._jobs.remove(dropped)
            self._jobs.append(job)
            self._cv.notify()
            return dropped

    def _victim(self, new: _Job) -> _Job:
        for kind in DROP_FIRST:
            old = [j for j in self._jobs if j.kind == kind]
            if old:
                return min(old, key=lambda j: j.seq)
            if new.kind == kind:
                return new
        return min(self._jobs, key=lambda j: j.seq)

    def _take(self, now: float) -> _Job | None:
        due = [j for j in self._jobs if j.due <= now]
        if not due:
            return None
        job = min(due, key=lambda j: j.seq)
        self._jobs.remove(job)
        self._busy += 1
        return job

    def take_due(self, now: float) -> _Job | None:
        """지금 보낼 일 하나 (없으면 None). 기다리지 않는다."""
        with self._cv:
            return self._take(now)

    def get(self) -> _Job:
        """다음 보낼 일. 없으면 새 일이 들어오거나 재시도 시각이 될 때까지 기다린다."""
        with self._cv:
            while True:
                now = self._clock()
                job = self._take(now)
                if job:
                    return job
                nxt = min((j.due for j in self._jobs), default=None)
                self._cv.wait(timeout=None if nxt is None else max(0.05, nxt - now))

    def done(self) -> None:
        with self._cv:
            self._busy -= 1


class Cloud:
    def __init__(self, log: Callable[[str], None] = print, sample_every_s: float = 60.0,
                 url: str | None = None, key: str | None = None, bench_id: str | None = None,
                 on_alert: Callable[[str], None] | None = None,
                 on_fail: Callable[[str, str], None] | None = None,
                 clock: Callable[[], float] = time.time):
        self.log = log
        self.on_alert = on_alert or (lambda msg: None)          # 사람이 알아야 하는 것 (키 거부) — 엔진이 Slack 으로 잇는다
        self.on_fail = on_fail or (lambda kind, detail: None)   # 전송 실패 기록 — 엔진이 events 의 cloud_fail 로 잇는다
        self._clock = clock
        # 인자가 None 이면 자격 증명 관리자에서 읽고, 빈 문자열이면 "없음" 으로 둔다 (검사에서 끄기 위해)
        pick = lambda v, k: _kr(k) if v is None else v
        self.url = (pick(url, "url") or "").rstrip("/")
        self.key = pick(key, "service_key") or ""
        self.bench_id = pick(bench_id, "bench_id") or ""
        self.enabled = bool(self.url and self.key and self.bench_id)
        self.sample_every_s = sample_every_s
        self._last_sample = 0.0
        self._last_err_log = 0.0
        self._q = _Outbox(QUEUE_MAX, clock)
        self._lock = threading.Lock()
        self._seq = self._state_seq = 0
        self._ok_t: float | None = None
        self._fail_t: float | None = None
        self._fails: deque[float] = deque()
        self._auth_fail = 0
        self._auth_alerted = False
        self._dropped = 0
        self._hook_t = float("-inf")
        self._hook_n = 0
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

    def _submit(self, kind: str, fn: Callable[[], object]) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._seq += 1
            seq = self._seq
            if kind == "state":
                self._state_seq = seq
        dropped = self._q.put(_Job(kind, fn, seq))
        if dropped is not None:
            self._drop(dropped, "전송 목록이 가득 참")

    def _worker(self) -> None:
        while True:
            self._run(self._q.get())

    def _run(self, job: _Job) -> None:
        """한 건 보낸다. 네트워크·서버 어떤 오류도 시험을 멈추지 않는다 — 실패는 재시도 목록으로."""
        try:
            if job.kind == "state" and job.seq < self._state_seq:
                return                      # 더 새 상태가 이미 들어와 있다 — 옛 상태로 덮어쓰지 않는다
            job.fn()
            self._ok()
        except Exception as e:
            self._failed(job, e)
        finally:
            self._q.done()

    # ---- 성공·실패 기록 ----
    def _ok(self) -> None:
        with self._lock:
            self._ok_t = self._clock()
            self._auth_fail, self._auth_alerted = 0, False

    def _note_fail(self, e: Exception) -> tuple[bool, int]:
        """실패를 센다. (키 거부 알림을 낼 차례인가, on_fail 을 부를 차례면 그동안의 실패 수 · 아니면 0)."""
        now = self._clock()
        auth = isinstance(e, urllib.error.HTTPError) and e.code in (401, 403)
        with self._lock:
            self._fail_t = now
            self._fails.append(now)
            while self._fails and now - self._fails[0] > 3600:
                self._fails.popleft()
            alert = False
            if auth:
                self._auth_fail += 1
                if self._auth_fail >= AUTH_FAIL_ALERT_AFTER and not self._auth_alerted:
                    self._auth_alerted = alert = True
            self._hook_n += 1
            n = 0
            if now - self._hook_t >= FAIL_HOOK_GAP_S:
                n, self._hook_n, self._hook_t = self._hook_n, 0, now
        return alert, n

    def _failed(self, job: _Job, e: Exception) -> None:
        alert, n = self._note_fail(e)
        if job.tries < len(RETRY_DELAYS_S):
            delay = RETRY_DELAYS_S[job.tries]
            job.tries += 1
            job.due = self._clock() + delay
            what = f"{job.tries}번째 재시도를 {int(delay)}초 뒤에"
            dropped = self._q.put(job)
            if dropped is not None:
                self._drop(dropped, "전송 목록이 가득 참")
        else:
            with self._lock:
                self._dropped += 1
            what = f"{len(RETRY_DELAYS_S)}번 다시 해도 실패해 버림"
        detail = f"{job.kind} 전송 실패 ({_why(e)}) — {what}"
        self._err(detail)
        self._after_fail(job.kind, detail, alert, n)

    def _after_fail(self, kind: str, detail: str, alert: bool, n: int) -> None:
        """알림·실패 기록 훅을 부른다. 훅이 무엇을 하든(파일 쓰기 실패 등) 전송 스레드를 멈추지 않는다."""
        if alert:
            self.log(f"클라우드: {KEY_REJECTED}")
            try:
                self.on_alert(KEY_REJECTED)
            except Exception:
                pass
        if n:
            try:
                self.on_fail(kind, f"{detail} · 지난 {int(FAIL_HOOK_GAP_S // 60)}분 사이 실패 {n}건")
            except Exception:
                pass

    def _drop(self, job: _Job, why: str) -> None:
        with self._lock:
            self._dropped += 1
        self._err(f"{why} — {job.kind} 1건 버림")

    def _err(self, msg: str) -> None:
        now = self._clock()
        if now - self._last_err_log > 60:
            self._last_err_log = now
            self.log(f"클라우드: {msg}")

    def stats(self) -> dict:
        """신호등이 읽는 전송 건강 상태. 시각은 epoch 초(없으면 None)."""
        now = self._clock()
        with self._lock:
            while self._fails and now - self._fails[0] > 3600:
                self._fails.popleft()
            return {"enabled": self.enabled, "ok_t": self._ok_t, "fail_t": self._fail_t, "fail_1h": len(self._fails),
                    "consecutive_auth_fail": self._auth_fail, "queue": len(self._q), "dropped": self._dropped}

    # ---- 올리기 ----
    def state(self, payload: dict) -> None:
        row = {"bench_id": self.bench_id, "updated_at": _iso(time.time()),
               "cycle": payload.get("cycle"), "phase": payload.get("phase"), "payload": payload}
        self._submit("state", lambda: self._req("POST", "bench_state", [row], {"on_conflict": "bench_id"}, "resolution=merge-duplicates,return=minimal"))

    def sample(self, t: float, phase: str, cycle: int | None, plug_on: bool | None, watts: float | None, wh: float,
               batts: list[int], cells_alive: int) -> None:
        if t - self._last_sample < self.sample_every_s:
            return
        self._last_sample = t
        row = {"bench_id": self.bench_id, "t": _iso(t), "cycle": cycle, "phase": phase, "plug_on": plug_on,
               "watts": None if watts is None or watts != watts else round(watts, 2), "wh": round(wh, 3),
               "batt_min": min(batts) if batts else None, "batt_avg": round(sum(batts) / len(batts), 1) if batts else None,
               "batt_max": max(batts) if batts else None, "cells_alive": cells_alive}
        self._submit("sample", lambda: self._req("POST", "bench_sample", [row], {"on_conflict": "bench_id,t"}, "resolution=ignore-duplicates,return=minimal"))

    def cycle(self, row: dict) -> None:
        body = {"bench_id": self.bench_id, "cycle": int(row["cycle"]), "row": row, "updated_at": _iso(time.time())}
        self._submit("cycle", lambda: self._req("POST", "bench_cycle", [body], {"on_conflict": "bench_id,cycle"}, "resolution=merge-duplicates,return=minimal"))

    def discharge(self, rows: list[dict]) -> None:
        body = [{"bench_id": self.bench_id, **r} for r in rows]
        self._submit("discharge", lambda: self._req("POST", "bench_discharge", body, {"on_conflict": "bench_id,cycle,serial"}, "resolution=merge-duplicates,return=minimal"))

    def event(self, t: float, cycle: int, phase: str, kind: str, serial, detail: str) -> None:
        row = {"bench_id": self.bench_id, "t": _iso(t), "cycle": cycle, "phase": phase, "kind": kind, "serial": str(serial), "detail": detail}
        self._submit("event", lambda: self._req("POST", "bench_event", [row], None, "return=minimal"))

    # ---- 명령 (동기) ----
    def poll_command(self) -> dict | None:
        """아직 집어 가지 않은 가장 오래된 명령 하나를 가져오고 taken_at 을 찍는다. 없으면 None.
        실패는 재시도하지 않는다(20초 뒤 다음 확인이 곧 재시도다). 다만 통계·키 거부 감지에는 센다."""
        if not self.enabled:
            return None
        try:
            rows = self._req("GET", "bench_command", None,
                             {"bench_id": f"eq.{self.bench_id}", "taken_at": "is.null", "order": "requested_at.asc", "limit": "1"}, timeout=5)
            self._ok()
            if not rows:
                return None
            cmd = rows[0]
            self._req("PATCH", "bench_command", {"taken_at": _iso(time.time())}, {"id": f"eq.{cmd['id']}"}, "return=minimal", timeout=5)
            return cmd
        except Exception as e:
            alert, n = self._note_fail(e)
            detail = f"명령 확인 실패 ({_why(e)})"
            self._err(detail)
            self._after_fail("poll", detail, alert, n)
            return None

    def ack(self, cmd_id: int, result: str) -> None:
        self._submit("ack", lambda: self._req("PATCH", "bench_command", {"result": result}, {"id": f"eq.{cmd_id}"}, "return=minimal"))


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
    def stats(self): return {"enabled": False}


def _kr(name: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, name)
    except Exception:
        return None
