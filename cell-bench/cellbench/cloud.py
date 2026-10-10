"""클라우드 전송 — TestPC 가 Supabase 프로젝트 cell-bench 에 상태·표본·사이클·이상을 올리고 원격 명령을 받아 온다.

방향은 항상 TestPC → 밖(HTTPS)이다. 회사망에 들어오는 구멍이 없어 Tailscale 같은 정책 문제가 없다.
키는 Windows 자격 증명 관리자(keyring service "cell-bench-cloud")의 url · service_key · bench_id 셋이다.
하나라도 없으면 꺼진 채(무음) 돈다 — 클라우드 때문에 시험이 멈추는 일은 없어야 한다.

전송은 보낼 일 목록 + 작업 스레드 하나. 명령 가져오기(poll)만 동기 호출이고 5초 안에 끝난다.

실패를 버리지 않는다 (FMEA 7.4 · 7.5 · 3.7)
  재시도      실패한 전송은 30초 · 2분 · 10분 뒤에 다시 해 본다(최대 3번). 그래도 안 되면 버리고 dropped 로 센다.
              상태(bench_state)·신호등(bench_health)은 더 새것이 이미 들어와 있으면 옛것을 다시 보내지 않는다 — 옛 화면으로 덮어쓰지 않게.
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
DROP_FIRST = ("sample", "state", "health")   # 가득 차면 이 순서로, 그 종류 중 가장 오래된 것부터 버린다
LATEST_ONLY = ("state", "health")       # 한 줄을 계속 덮어쓰는 표 — 더 새것이 들어와 있으면 옛것은 보내지 않는다
AUTH_FAIL_ALERT_AFTER = 3               # 401·403 이 이만큼 연속이면 키 만료로 보고 알린다
FAIL_HOOK_GAP_S = 600.0                 # on_fail 은 이 간격에 한 번만
KEY_REJECTED = "클라우드 키가 거부됨 — 키 만료·교체 확인 (tools/cloud_setup.py)"
# 다중 세트 (명세 5-2) — 이 코드가 아는 클라우드 스키마 판. 엔진이 now.json(bench_state payload)의 cloud_schema 에 싣는다 —
# 마이그레이션 적용 절차의 첫 줄이 '도는 엔진이 판 2 를 아는가'를 이것으로 확인한다
SCHEMA_AWARE = 2
REPROBE_CODES = ("42P10", "PGRST204", "42703")   # 이 400 이 오면 스키마가 바뀐 것이다(적용 · 되돌림) — 다시 탐침


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + "Z"


def _why(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    return f"{type(e).__name__}: {e}"


@dataclass
class _Job:
    kind: str                   # state · health · sample · cycle · discharge · event · ack
    fn: Callable[[], object]    # 보낼 때 요청 모양을 짓는다(스키마 판을 그때 본다 — 명세 5-2)
    seq: int                    # 넣은 순서 — 작을수록 오래된 것
    tries: int = 0              # 지금까지 잡은 재시도 수
    due: float = 0.0            # 이 시각부터 보낸다 (처음은 0 = 바로)
    key: tuple = ()             # '최신만 보내기' 열쇠 — (종류,) 또는 (종류, 세트)


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
        self._seq = 0
        self._latest: dict[str, int] = {}       # LATEST_ONLY 종류마다 가장 최근에 넣은 순번
        self._ok_t: float | None = None
        self._fail_t: float | None = None
        self._fails: deque[float] = deque()
        self._auth_fail = 0
        self._auth_alerted = False
        self._dropped = 0
        self._hook_t = float("-inf")
        self._hook_n = 0
        self.schema: int | None = None          # 클라우드 스키마 판 — None(모름) · 1 은 지금 모양, 2 이상은 세트 칸이 있는 모양
        self._want_probe = False                # 작업 스레드가 다음 일 전에 탐침한다 (start_probe · 400 계열 오류)
        self._probing = False                   # start_probe 를 부른 Cloud 만 탐침한다 — 검사의 가짜 Cloud 는 지금처럼
        self._last_sample_set: dict = {}         # 세트별 표본 간격 (세트 지정이 없으면 _last_sample 그대로)
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
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            try:
                e.body = e.read().decode("utf-8", "replace")[:500]   # 400 의 오류 코드(42P10 등)로 재탐침을 가른다
            except Exception:
                e.body = ""
            raise

    # ---- 스키마 판 (명세 5-2) ----
    def start_probe(self) -> None:
        """스키마 판 탐침을 켠다 — 엔진 · 감시자의 실물 배선만 부른다. 탐침은 작업 스레드가 다음 일 전에 한다(시작 길에 네트워크 없음)."""
        if not self.enabled:
            return
        self._probing = self._want_probe = True
        with self._q._cv:
            self._q._cv.notify()

    def _fetch_schema(self) -> int | None:
        """RPC bench_schema_version 을 한 번 부른다. 함수 없음(404) · 오류는 None."""
        req = urllib.request.Request(f"{self.url}/rest/v1/rpc/bench_schema_version", data=b"{}", method="POST")
        req.add_header("apikey", self.key); req.add_header("Authorization", f"Bearer {self.key}")
        req.add_header("Content-Type", "application/json"); req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=5) as r:
            v = json.loads(r.read() or b"null")
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    def _probe(self) -> None:
        """판을 다시 잰다. 실패는 판 1 — 전송 실패로 세지 않는다(신호등 '클라우드' 차선의 실패 수가 지금과 같게)."""
        self._want_probe = False
        try:
            v = self._fetch_schema()
        except Exception:
            v = None
        old, self.schema = self.schema, v if v is not None else 1
        # 처음 잰 판 1 은 적지 않는다 — 마이그레이션 전 run.log 가 지금과 같게. 판 2 이거나 바뀌었을 때만
        if self.schema >= 2 if old is None else old != self.schema:
            self.log(f"클라우드 스키마 판 {self.schema} ({'세트 칸 있음' if self.schema >= 2 else '세트 칸 없음 — 지금 모양'})")

    def v2(self) -> bool:
        return (self.schema or 1) >= 2

    def _oc(self, cols: str) -> dict:
        """on_conflict — 판 2 면 bench_id 뒤에 set_id 를 넣는다."""
        return {"on_conflict": cols.replace("bench_id", "bench_id,set_id", 1) if self.v2() else cols}

    def _with_set(self, row: dict, set_id) -> dict:
        return {**row, "set_id": int(set_id or 1)} if self.v2() else row

    def _submit(self, kind: str, fn: Callable[[], object], set_id=None) -> None:
        if not self.enabled:
            return
        key = (kind,) if set_id is None else (kind, set_id)
        with self._lock:
            self._seq += 1
            seq = self._seq
            if kind in LATEST_ONLY:
                self._latest[key] = seq
        dropped = self._q.put(_Job(kind, fn, seq, key=key))
        if dropped is not None:
            self._drop(dropped, "전송 목록이 가득 참")

    def _worker(self) -> None:
        while True:
            if self._want_probe:
                self._probe()
            self._run(self._q.get())

    def _run(self, job: _Job) -> None:
        """한 건 보낸다. 네트워크·서버 어떤 오류도 시험을 멈추지 않는다 — 실패는 재시도 목록으로."""
        try:
            if job.kind in LATEST_ONLY and job.seq < self._latest.get(job.key or (job.kind,), 0):
                return                      # 더 새것이 이미 들어와 있다 — 옛것으로 덮어쓰지 않는다
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
        if self._probing and isinstance(e, urllib.error.HTTPError) and e.code == 400 and any(
                c in str(getattr(e, "body", "") or "") for c in REPROBE_CODES):
            self._want_probe = True              # 스키마가 바뀌었다 — 재시도는 보낼 때 새 판으로 짓는다
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
    def state(self, payload: dict, set_id=None) -> None:
        row = {"bench_id": self.bench_id, "updated_at": _iso(time.time()),
               "cycle": payload.get("cycle"), "phase": payload.get("phase"), "payload": payload}
        self._submit("state", lambda: self._req("POST", "bench_state", [self._with_set(row, set_id)], self._oc("bench_id"),
                                                "resolution=merge-duplicates,return=minimal"), set_id)

    def health(self, payload: dict) -> None:
        """신호등 판정(cellbench/health.py) — 감시자가 1분마다 올린다. 시험대마다 한 줄을 덮어쓴다(state 와 같은 꼴).
        클라우드 결과판은 updated_at 이 cloud_board_stale_s(5분)보다 묵으면 스스로 전체를 '신호 없음'으로 본다."""
        row = {"bench_id": self.bench_id, "updated_at": _iso(time.time()), "light": payload.get("light"),
               "reason": payload.get("reason"), "payload": payload}
        self._submit("health", lambda: self._req("POST", "bench_health", [row], {"on_conflict": "bench_id"}, "resolution=merge-duplicates,return=minimal"))

    def sample(self, t: float, phase: str, cycle: int | None, plug_on: bool | None, watts: float | None, wh: float,
               batts: list[int], cells_alive: int, set_id=None) -> None:
        if set_id is None:
            if t - self._last_sample < self.sample_every_s:
                return
            self._last_sample = t
        else:                                       # 세트마다 따로 센다 — 세트 2 표본이 세트 1 의 1분 간격에 걸려 버려지지 않게
            if t - self._last_sample_set.get(set_id, float("-inf")) < self.sample_every_s:
                return
            self._last_sample_set[set_id] = t
        row = {"bench_id": self.bench_id, "t": _iso(t), "cycle": cycle, "phase": phase, "plug_on": plug_on,
               "watts": None if watts is None or watts != watts else round(watts, 2), "wh": round(wh, 3),
               "batt_min": min(batts) if batts else None, "batt_avg": round(sum(batts) / len(batts), 1) if batts else None,
               "batt_max": max(batts) if batts else None, "cells_alive": cells_alive}
        self._submit("sample", lambda: self._req("POST", "bench_sample", [self._with_set(row, set_id)], self._oc("bench_id,t"),
                                                 "resolution=ignore-duplicates,return=minimal"), set_id)

    def cycle(self, row: dict, set_id=None) -> None:
        body = {"bench_id": self.bench_id, "cycle": int(row["cycle"]), "row": row, "updated_at": _iso(time.time())}
        self._submit("cycle", lambda: self._req("POST", "bench_cycle", [self._with_set(body, set_id)], self._oc("bench_id,cycle"),
                                                "resolution=merge-duplicates,return=minimal"), set_id)

    def discharge(self, rows: list[dict], set_id=None) -> None:
        body = [{"bench_id": self.bench_id, **r} for r in rows]
        self._submit("discharge", lambda: self._req("POST", "bench_discharge", [self._with_set(b, set_id) for b in body],
                                                    self._oc("bench_id,cycle,serial"), "resolution=merge-duplicates,return=minimal"),
                     set_id)

    def event(self, t: float, cycle: int, phase: str, kind: str, serial, detail: str, set_id=None) -> None:
        row = {"bench_id": self.bench_id, "t": _iso(t), "cycle": cycle, "phase": phase, "kind": kind, "serial": str(serial), "detail": detail}
        self._submit("event", lambda: self._req("POST", "bench_event", [self._with_set(row, set_id)], None, "return=minimal"), set_id)

    def for_set(self, set_id) -> "SetCloud":
        """세트 k(id ≠ 1)의 Recorder 가 쓰는 창 — 판 2 가 확인되기 전에는 아무것도 보내지 않는다(명세 5-2 · B-H2)."""
        return SetCloud(self, set_id)

    def poll_commands(self, limit: int) -> list[dict]:
        """명령을 한 번에 limit 개까지 (다중 모드의 조정자 — 운전 세트 수만큼, A-L4). 가져온 것마다 taken_at 을 찍는다."""
        out = []
        for _ in range(max(1, int(limit))):
            c = self.poll_command()
            if not c:
                break
            out.append(c)
        return out

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


SLACK_HOOK_PREFIX = "https://hooks.slack.com/"


def fetch_slack_webhook(url: str | None = None, key: str | None = None, timeout: float = 5.0) -> str | None:
    """Slack 웹훅을 클라우드 Vault 에서 받아 온다 — RPC bench_slack_webhook 이 비밀 cell_bench_slack_webhook 을 돌려준다
    (supabase/migrations/20261010061544_slack_webhook_rpc.sql · service_role 만 부를 수 있다).

    url · key 가 None 이면 자격 증명 관리자(cell-bench-cloud)의 url · service_key 를 쓰고, 둘 중 하나라도 없으면 부르지 않는다.
    https://hooks.slack.com/ 으로 시작하는 문자열만 돌려준다. 그 밖(비밀 없음 · 함수 없음 · 오류 · 이상한 값)은 전부 None.
    실패는 삼키고 아무것도 로그에 남기지 않는다 — 값이 어디에도 찍히지 않게. 받은 값은 부른 쪽이 메모리에만 둔다(alert.py).
    """
    url = ((_kr("url") if url is None else url) or "").rstrip("/")
    key = (_kr("service_key") if key is None else key) or ""
    if not (url and key):
        return None
    try:
        req = urllib.request.Request(f"{url}/rest/v1/rpc/bench_slack_webhook", data=b"{}", method="POST")
        req.add_header("apikey", key); req.add_header("Authorization", f"Bearer {key}")
        req.add_header("Content-Type", "application/json"); req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            hook = json.loads(r.read() or b"null")
    except Exception:
        return None
    return hook if isinstance(hook, str) and hook.startswith(SLACK_HOOK_PREFIX) else None


class SetCloud:
    """id ≠ 1 세트의 클라우드 창. 판 2 전에는 NoCloud 처럼 아무것도 보내지 않는다 — 마이그레이션 전의 검증 · 운전 기록이
    세트 1 의 bench_state 를 덮거나 이상으로 섞이지 않게(B-H2). 명령 가져오기는 조정자만 하므로 여기서는 하지 않는다."""

    def __init__(self, cloud: Cloud, set_id):
        self.cloud, self.set_id = cloud, set_id

    @property
    def enabled(self) -> bool:
        return bool(self.cloud.enabled and self.cloud.v2())

    def _go(self, name: str, *a):
        if self.enabled:
            getattr(self.cloud, name)(*a, set_id=self.set_id)

    def state(self, payload): self._go("state", payload)
    def sample(self, *a): self._go("sample", *a)
    def cycle(self, row): self._go("cycle", row)
    def discharge(self, rows): self._go("discharge", rows)
    def event(self, *a): self._go("event", *a)
    def health(self, *a, **k): pass
    def poll_command(self): return None
    def ack(self, *a, **k): pass
    def stats(self): return self.cloud.stats()


class NoCloud:
    """클라우드를 쓰지 않을 때의 빈 구현."""
    enabled = False
    def state(self, *a, **k): pass
    def health(self, *a, **k): pass
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
