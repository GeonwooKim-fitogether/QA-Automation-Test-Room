"""새 FTG 를 따라가며 셀 배터리 IC 집계를 이어 붙이고(gauge.csv · fg_cycles.csv), 방전 용량을 클라우드 표 bench_fg_cycle 에 올린다.

결과판 서버(serve_board.py)가 10분마다 부른다. 엔진(run_cycle.py)은 이것을 모른다 — 엔진을 다시 시작하지 않아도 새 사이클이 반영된다.
끝난 사이클(cycles.csv 에 줄이 있는 것)만 센다. 계산은 tools/gauge_from_ftg.py · tools/fg_cycles.py 의 run() 을 그대로 부른다(증분).
한 회차는 두 단계다 — 먼저 모든 세트의 로컬 계산을 끝내고, 그다음 클라우드에 올린다. 클라우드가 실패해도 로컬 계산은 멈추지 않는다(QA #67-1).
클라우드 키는 cloud.py 와 같은 keyring "cell-bench-cloud" 의 url · service_key · bench_id. 없으면 파일만 쓰고 클라우드는 꺼진 채 돈다.
올릴 것은 (세트, 사이클)마다 클라우드의 행 수와 로컬 행 수를 비교해 고른다 — 클라우드 쪽이 모자란 사이클은 통째로 다시 보낸다(upsert 라 겹쳐도 중복이 없다).
묶음은 사이클 경계에서 자른다 — 전송이 중간에 끊겨도 한 사이클의 일부 셀만 남는 일이 없다(QA #67-2).
표가 없으면(마이그레이션 전) 실패로 남기고 10분 뒤 다시 해 본다 — 시험 · 결과판에는 영향이 없다.
"""
from __future__ import annotations

import csv
import json
import threading
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Callable

from cellbench import ftglog
from cellbench.cloud import _kr, _why

EVERY_S = 600.0
FIRST_DELAY_S = 30.0
BATCH = 500                   # 한 번에 올리는 행 수의 대략 상한 — 사이클 경계에서 자른다
TABLE = "bench_fg_cycle"
NUM_COLS = ftglog.FG_COLUMNS[2:]


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("cycle")]


def cloud_row(bench_id: str, set_id: int, r: dict) -> dict:
    num = lambda v: None if v in ("", None) else float(v)
    return {"bench_id": bench_id, "set_id": set_id, "cycle": int(r["cycle"]), "serial": int(r["serial"]),
            **{k: (int(float(r[k])) if k == "n_rec" and r.get(k) not in ("", None) else num(r.get(k))) for k in NUM_COLS}}


def batches(rows: list[dict], cap: int = BATCH) -> list[list[dict]]:
    """사이클 경계에서 자른 묶음 — 한 사이클은 한 묶음 안에 통째로 들어간다(한 사이클이 cap 보다 크면 그 사이클만 한 묶음)."""
    out, cur, last = [], [], None
    for r in sorted(rows, key=lambda r: (int(r["cycle"]), int(r["serial"]))):
        c = int(r["cycle"])
        if cur and c != last and len(cur) >= cap:
            out.append(cur)
            cur = []
        cur.append(r)
        last = c
    if cur:
        out.append(cur)
    return out


class FgFollow:
    def __init__(self, data: Path, sets: Callable[[], list[int]], runners: list[Callable],
                 url: str | None = None, key: str | None = None, bench_id: str | None = None,
                 log: Callable[[str], None] = print, clock: Callable[[], float] = time.time):
        """sets() → 따라갈 세트 번호들. runners = [fn(data, set_id, log=…)] — 증분 계산(gauge · fg_cycles)."""
        pick = lambda v, k: _kr(k) if v is None else v
        self.url = (pick(url, "url") or "").rstrip("/")
        self.key = pick(key, "service_key") or ""
        self.bench_id = pick(bench_id, "bench_id") or ""
        self.enabled = bool(self.url and self.key and self.bench_id)
        self.data, self.sets, self.runners, self.log, self._clock = Path(data), sets, runners, log, clock
        self._lock = threading.Lock()
        self._counts: Counter | None = None              # (세트, 사이클) → 클라우드에 있는 행 수
        self.last_ok: float | None = None                # 마지막으로 클라우드 올리기가 끝난 시각
        self.last_run: float | None = None               # 마지막으로 한 회차가 끝난 시각
        self.error: str | None = None                    # 클라우드 실패 이유
        self.local_error: str | None = None              # 로컬 집계 실패 이유(가장 최근 것)

    def _req(self, method: str, path: str, body=None, prefer: str | None = None, timeout: float = 15.0):
        req = urllib.request.Request(f"{self.url}/rest/v1/{path}", method=method,
                                     data=json.dumps(body, allow_nan=False).encode("utf-8") if body is not None else None)
        req.add_header("apikey", self.key); req.add_header("Authorization", f"Bearer {self.key}")
        req.add_header("Content-Type", "application/json")
        if prefer:
            req.add_header("Prefer", prefer)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    def _cloud_counts(self) -> Counter:
        out: Counter = Counter()
        for off in range(0, 10_000_000, 1000):
            rows = self._req("GET", f"{TABLE}?select=set_id,cycle&bench_id=eq.{self.bench_id}&order=set_id,cycle,serial"
                                    f"&limit=1000&offset={off}") or []
            out.update((int(r["set_id"]), int(r["cycle"])) for r in rows)
            if len(rows) < 1000:
                return out
        return out

    def push(self, set_id: int) -> int:
        """그 세트의 fg_cycles.csv 중 클라우드 행 수가 로컬보다 적은 사이클을 통째로 올린다 → 올린 행 수."""
        if self._counts is None:
            self._counts = self._cloud_counts()
        rows = _rows(ftglog.set_dir(self.data, set_id) / "fg_cycles.csv")
        local = Counter(int(r["cycle"]) for r in rows)
        due = {c for c, n in local.items() if self._counts[(set_id, c)] < n}
        n = 0
        for part in batches([r for r in rows if int(r["cycle"]) in due], BATCH):
            self._req("POST", f"{TABLE}?on_conflict=bench_id,set_id,cycle,serial", [cloud_row(self.bench_id, set_id, r) for r in part],
                      prefer="resolution=merge-duplicates,return=minimal")
            for c, k in Counter(int(r["cycle"]) for r in part).items():
                self._counts[(set_id, c)] = k
            n += len(part)
        return n

    def step(self) -> dict:
        """한 회차 — ① 모든 세트의 증분 계산 ② 클라우드 올리기. 실패는 상태에 남기고 던지지 않는다."""
        with self._lock:
            try:
                ks = [k for k in self.sets() if (ftglog.set_dir(self.data, k) / "ftg").is_dir()]
            except Exception as e:
                self.local_error = f"세트 목록 — {_why(e)}"
                self.log(f"IC 집계: {self.local_error}")
                return {}
            for k in ks:
                for run in self.runners:
                    try:
                        run(self.data, k, log=lambda m, k=k: self.log(f"[세트 {k}] {m}"))
                    except Exception as e:                   # 한 도구가 실패해도 다른 도구 · 세트는 계속
                        self.local_error = f"세트 {k} — {_why(e)}"
                        self.log(f"[세트 {k}] IC 집계 실패 — {_why(e)} (10분 뒤 다시)")
            done = {}
            if self.enabled:
                fails = []
                for k in ks:
                    try:
                        done[k] = self.push(k)
                    except Exception as e:                   # 이 세트만 실패 — 다음 세트는 계속, 다음 회차에 클라우드 목록부터 다시 읽는다
                        fails.append(f"세트 {k} {_why(e)}")
                        self._counts = None
                if fails:
                    self.error = " · ".join(fails)
                    self.log(f"방전 용량 클라우드 올리기 실패 — {self.error} (10분 뒤 다시)")
                else:
                    self.last_ok, self.error = self._clock(), None
                if any(done.values()):
                    self.log("방전 용량 클라우드: " + " · ".join(f"세트 {k} {n}행" for k, n in done.items() if n))
            self.last_run = self._clock()
            return done

    def status(self) -> dict:
        """/api/fg/status — state: off(키 없음) · fail(마지막 클라우드 시도 실패) · ok. local_error 는 로컬 집계의 최근 실패."""
        return {"state": "off" if not self.enabled else "fail" if self.error else "ok", "last_ok": self.last_ok,
                "last_run": self.last_run, "error": self.error, "local_error": self.local_error}

    def start(self, every_s: float = EVERY_S, first_delay_s: float = FIRST_DELAY_S) -> threading.Thread:
        def loop():
            time.sleep(first_delay_s)
            while True:
                try:
                    self.step()
                except Exception as e:                       # 무엇이 나도 스레드가 소리 없이 멈추지 않게 (QA #67-10)
                    self.local_error = _why(e)
                    self.log(f"IC 집계 회차 실패 — {self.local_error}")
                time.sleep(every_s)
        th = threading.Thread(target=loop, daemon=True, name="fg-follow")
        th.start()
        self.log(f"IC 집계 따라가기 켜짐 · {every_s / 60:.0f}분마다 · 클라우드 {'켜짐' if self.enabled else '꺼짐 (keyring 키 없음)'}")
        return th
