"""가짜 세상 — 골든 기록 검사(G1 · G2)와 안전 장치 검사가 쓰는 결정적 시험대.

진짜 run_cycle.main 을 그대로 돌린다. 바꿔 끼우는 것은 바깥 세상뿐이다:
  · 가상 시계 — time.time / sleep / localtime 을 가상 시각으로(localtime 은 이 PC 시간대와 무관하게 KST)
  · 셀 — 배터리가 '자기 Dock 을 먹이는 플러그'를 따라 오르내린다(세트마다, 교차 연결도 만들 수 있다)
  · 플러그 · CellLink(추출) · 클라우드(요청을 보내지 않고 모양을 적는다) · Slack · Wi-Fi 재연결 · 디스크 여유
같은 시나리오는 언제 어디서 돌려도 바이트 단위로 같은 기록을 낸다. 그래서 골든 비교가 된다.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time as _real_time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_cycle  # noqa: E402
from cellbench import cells as cellsmod  # noqa: E402
from cellbench import cloud as cloudmod  # noqa: E402
from cellbench import config as configmod  # noqa: E402
from cellbench import control as controlmod  # noqa: E402
from cellbench import cycle as cyclemod  # noqa: E402
from cellbench import net as netmod  # noqa: E402
from cellbench import record as recordmod  # noqa: E402
from cellbench.cells import CellLive, CellResult  # noqa: E402
from cellbench.plug import PlugReading  # noqa: E402

T0 = 1791900000.0                 # 가상 시각의 시작 (2026-10-14 근처 — 값 자체는 뜻이 없다)
KST = 9 * 3600
SET1 = configmod.expand_serials(configmod.DEFAULT_SET["serials"])
SET1_MAC = configmod.DEFAULT_SET["plug_mac"]


class Stop(Exception):
    """시나리오가 정한 시간 한도에 닿았다 — 무한 고리를 막는 안전판."""


class Clock:
    def __init__(self, world: "World"):
        self.t = T0
        self.world = world

    def time(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        dt = max(0.0, float(dt))
        end = self.t + dt
        while self.t < end:                    # 1분 단위로 쪼개 배터리를 적분하고 예약된 일을 제때 한다
            step = min(60.0, end - self.t)
            self.world.integrate(step)
            self.t += step
            self.world.fire_due()
        if self.t - T0 > self.world.limit_s:
            raise Stop(f"가상 {self.world.limit_s / 3600:.1f}시간 한도")

    def localtime(self, t=None):
        return _real_time.gmtime((self.t if t is None else t) + KST)

    def module(self) -> SimpleNamespace:
        """모듈의 `time` 이름 자리에 끼우는 가짜 time 모듈."""
        rt = _real_time
        return SimpleNamespace(time=self.time, sleep=self.sleep, localtime=self.localtime,
                               strftime=lambda fmt, tt=None: rt.strftime(fmt, self.localtime() if tt is None else tt),
                               gmtime=rt.gmtime, mktime=lambda tt: rt.mktime(tt), strptime=rt.strptime,
                               monotonic=self.time, perf_counter=self.time)


@dataclass
class Dock:
    """Dock 하나 — 그 위의 셀들과, 그 Dock 을 실제로 먹이는 플러그 MAC."""
    feeder: str
    serials: list[int]
    batt: dict[int, float] = field(default_factory=dict)


class World:
    def __init__(self, docks: list[Dock], rate_pct_min: float = 2.0, limit_h: float = 30.0):
        self.clock = Clock(self)
        self.docks = docks
        self.plugs: dict[str, bool] = {d.feeder: True for d in docks}   # MAC → 켜짐
        self.rate = rate_pct_min
        self.limit_s = limit_h * 3600
        self.blind_until = -1.0                    # 이 시각까지 셀이 하나도 안 들린다
        self.blind_from = -1.0
        self.events: list[tuple[float, object]] = []    # (시각, 할 일)
        self.requests: list[str] = []              # 클라우드 요청 (정규화한 JSON 한 줄)
        self.alerts: list[str] = []
        self.reconnects = 0
        self.plug_calls: dict[str, list[str]] = {}
        for d in docks:
            for i, s in enumerate(d.serials):
                d.batt.setdefault(s, 80.0 + (i % 4) * 0.3)

    # --- 시간 ---
    def at(self, dt_s: float, fn) -> None:
        self.events.append((T0 + dt_s, fn))
        self.events.sort(key=lambda e: e[0])

    def fire_due(self) -> None:
        while self.events and self.events[0][0] <= self.clock.t:
            _, fn = self.events.pop(0)
            fn()

    def integrate(self, dt: float) -> None:
        for d in self.docks:
            sign = 1.0 if self.plugs.get(d.feeder) else -1.0
            for s in d.batt:
                d.batt[s] = min(100.0, max(0.0, d.batt[s] + sign * self.rate * dt / 60))

    def watts(self, mac: str) -> float:
        if not self.plugs.get(mac):
            return 0.0
        fed = [d for d in self.docks if d.feeder == mac]
        if not fed:
            return 1.4                             # 아무 Dock 도 안 먹이는 플러그
        return 68.0 if any(b < 100.0 for d in fed for b in d.batt.values()) else 31.0

    def blind(self) -> bool:
        return self.blind_from <= self.clock.t < self.blind_until

    def battery(self, serial: int) -> int | None:
        for d in self.docks:
            if serial in d.batt:
                return int(round(d.batt[serial]))
        return None


class FakeLive:
    def __init__(self, world: World):
        self.w = world
        self.ip_changes = 0

    def start(self):
        pass

    def stop(self):
        pass

    def snapshot(self) -> dict[int, CellLive]:
        if self.w.blind():
            return {}
        now = self.w.clock.t
        out = {}
        for d in self.w.docks:
            for i, s in enumerate(d.serials):
                out[s] = CellLive(f"192.168.1.{110 + (s % 120)}", self.w.battery(s), 0, 8, -55, now)
        return out

    def alive(self, within_s: float = 5.0) -> list[int]:
        return sorted(self.snapshot())

    def wait_for(self, serials, timeout_s):
        snap = self.snapshot()
        return [s for s in serials if s not in snap]


class FakePlug:
    """플러그 하나. MAC 으로 세상의 플러그를 움직인다. 호출은 world.plug_calls 에 남는다."""

    def __init__(self, world: World, cfg, stats_path=None):
        self.w, self.cfg = world, cfg
        self.mac = cfg.plug_mac.upper()
        self.ip = cfg.plug_ip_hint or "?"
        self.toggles = 0

    def _r(self) -> PlugReading:
        return PlugReading(bool(self.w.plugs.get(self.mac)), self.w.watts(self.mac), self.w.clock.t)

    def _log(self, what: str) -> None:
        self.w.plug_calls.setdefault(self.mac, []).append(what)

    def read(self):
        self._log("read")
        return self._r()

    def _set(self, on: bool):
        if self.w.plugs.get(self.mac) != on:
            self.toggles += 1
        self.w.plugs[self.mac] = on

    def on(self):
        self._log("on"); self._set(True)
        return self._r()

    def off(self):
        self._log("off"); self._set(False)
        return self._r()

    def recharge(self, gap_s: float = 10.0):
        self._log("recharge"); self._set(False)
        self.w.clock.sleep(gap_s)
        self._set(True)
        return self._r()

    def metrics(self, now=None):
        return {"retries_1h": 0, "last_call_s": 0.5, "toggles_total": self.toggles}


class FakeLink:
    def __init__(self, world: World, cfg, live, log=print, progress=None):
        self.w, self.cfg, self.log = world, cfg, log
        self.progress = progress or (lambda: None)

    def extract(self, serials, out_dir: Path):
        out_dir.mkdir(parents=True, exist_ok=True)
        res = {}
        for i in range(0, len(serials), self.cfg.extract_batch):
            group = serials[i:i + self.cfg.extract_batch]
            self.log(f"추출 {i // self.cfg.extract_batch + 1}번째 묶음: {group}")
            t0 = self.w.clock.t
            self.w.clock.sleep(75)
            self.progress()
            stamp = _real_time.strftime("%Y%m%d_%H%M%S", self.w.clock.localtime(t0))
            for s in group:
                size = 20 * 1048576
                res[s] = CellResult(s, "192.168.1.%d" % (110 + s % 120), battery=self.w.battery(s), size=size, got=size,
                                    ended=True, deleted=self.cfg.delete_after_extract,
                                    file=str(out_dir / f"ftg_{s}_{stamp}.bin"), t_start=t0, t_end=self.w.clock.t,
                                    resume_s=18.0)
        return res

    def resume(self, serials):
        return {}


def capture_cloud(world: World):
    """진짜 Cloud 의 요청 모양(방법 · 표 · 매개변수 · Prefer · 본문)을 보내지 않고 적는다. 작업 스레드를 거치지 않고 그 자리에서."""

    class CaptureCloud(cloudmod.Cloud):
        def __init__(self, log=print, sample_every_s=60.0, bench_id=None, on_alert=None, on_fail=None, **kw):
            super().__init__(log=log, sample_every_s=sample_every_s, url="https://golden.invalid", key="k",
                             bench_id=bench_id or "", on_alert=on_alert, on_fail=on_fail, clock=world.clock.time)

        def _submit(self, kind, fn):
            if self.enabled:
                fn(); self._ok()

        def _req(self, method, table, body=None, params=None, prefer=None, timeout=8.0):
            world.requests.append(json.dumps([method, table, params, prefer, body], ensure_ascii=False, sort_keys=True))
            return [] if method == "GET" else None

    return CaptureCloud


def patch_world(monkeypatch, world: World, bench_path: Path | None = None) -> None:
    """run_cycle.main 이 바깥 세상 대신 이 가짜 세상과 이야기하게 한다."""
    fake_time = world.clock.module()
    for mod in (run_cycle, cyclemod, recordmod, cloudmod, controlmod):
        monkeypatch.setattr(mod, "time", fake_time)
    monkeypatch.setattr(run_cycle, "LiveListener", lambda cfg: FakeLive(world))
    monkeypatch.setattr(run_cycle, "Plug", lambda cfg, stats_path=None: FakePlug(world, cfg, stats_path))
    monkeypatch.setattr(run_cycle, "CellLink", lambda cfg, live, log=print, progress=None: FakeLink(world, cfg, live, log, progress))
    monkeypatch.setattr(run_cycle, "Cloud", capture_cloud(world))
    monkeypatch.setattr(run_cycle, "make_notifier", lambda *a, **k: world.alerts.append)
    monkeypatch.setattr(run_cycle.atexit, "register", lambda *a, **k: None)
    monkeypatch.setattr(run_cycle.proc, "safe_stdio", lambda: None)
    monkeypatch.setattr(run_cycle.start_problem, "__defaults__", (lambda ip: None,))
    monkeypatch.setattr(netmod, "reconnect", lambda *a, **k: (setattr(world, "reconnects", world.reconnects + 1), True)[1])
    monkeypatch.setattr(netmod, "hub_reachable", lambda *a, **k: True)
    monkeypatch.setattr(cyclemod, "shutil", SimpleNamespace(disk_usage=lambda p: SimpleNamespace(free=200 * 1024 ** 3)))
    monkeypatch.setattr(cyclemod, "Plug", lambda cfg, stats_path=None: FakePlug(world, cfg, stats_path), raising=False)
    # CycleState 의 t_start · phase_since 기본값은 클래스를 만들 때 진짜 time.time 을 붙잡았다 — 가상 시각으로 채운다
    real_state = cyclemod.CycleState

    def state(*a, **k):
        st = real_state(*a, **k)
        for f in ("t_start", "phase_since"):
            if f not in k:
                setattr(st, f, world.clock.t)
        return st
    monkeypatch.setattr(cyclemod, "CycleState", state)
    orig = configmod.Config.load.__func__
    monkeypatch.setattr(configmod.Config, "load",
                        classmethod(lambda cls, path=None, bench_path_=None: orig(cls, path, bench_path)))


def run_main(monkeypatch, tmp_path: Path, world: World, argv: list[str], bench: dict | None = None) -> int:
    bench_path = None
    if bench is not None:
        bench_path = tmp_path / "bench.json"
        bench_path.write_text(json.dumps(bench, ensure_ascii=False), encoding="utf-8")
    patch_world(monkeypatch, world, bench_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["run_cycle.py", *argv])
    try:
        return run_cycle.main()
    except Stop as e:
        return f"stop: {e}"


# ---------- 기록을 골든으로 ----------
FULL_FILES = {"cycles.csv", "events.csv", "now.json", "engine.json", "control_ack.json"}


def _norm(text: str, tmp: Path) -> str:
    text = text.replace(str(tmp), "<tmp>").replace(str(tmp).replace("\\", "\\\\"), "<tmp>")
    return re.sub(r'"pid": \d+', '"pid": 0', text)


def snapshot(tmp: Path, world: World, code) -> dict:
    data = tmp / "data"
    files = {}
    for p in sorted(data.rglob("*")):
        rel = p.relative_to(data).as_posix()
        if p.is_dir():
            files[rel + "/"] = "dir"
            continue
        raw = _norm(p.read_text(encoding="utf-8-sig"), tmp)
        if p.name in FULL_FILES:
            files[rel] = raw.splitlines()
        else:
            files[rel] = {"sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(), "lines": raw.count("\n")}
    reqs = [_norm(r, tmp) for r in world.requests]
    by: dict[str, int] = {}
    first: dict[str, str] = {}
    for r in reqs:
        m, table = json.loads(r)[:2]
        k = f"{m} {table}"
        by[k] = by.get(k, 0) + 1
        first.setdefault(k, r)
    return {"exit": code, "files": files,
            "cloud": {"sha256": hashlib.sha256("\n".join(reqs).encode("utf-8")).hexdigest(), "count": by, "first": first},
            "alerts": [_norm(a, tmp) for a in world.alerts], "reconnects": world.reconnects,
            "plug_calls": {k: len(v) for k, v in sorted(world.plug_calls.items())}}
