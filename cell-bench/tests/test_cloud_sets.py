"""다중 세트 5단계 — 클라우드 스키마 판 · 세트 칸 · 세트 k 의 NoCloud · Recorder 의 세트 표기 (명세 5-1 · 5-2 · 7-2)."""
import json
import sys
import time
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sim_world as sw  # noqa: E402
from cellbench import cloud as cloudmod  # noqa: E402
from cellbench.cloud import Cloud, SetCloud  # noqa: E402
from cellbench.record import Recorder  # noqa: E402

T = 1_800_000_000.0


class Pump(Cloud):
    """작업 스레드 없이 — pump() 가 탐침과 보낼 일을 직접 돌린다."""
    def __init__(self, schema=None, fail=None, **kw):
        self.calls, self.now, self.schema_answer, self.fail = [], T, schema, fail or (lambda *a: None)
        self.fetches = 0
        super().__init__(log=lambda m: None, url="https://x.supabase.co", key="k" * 50, bench_id="b1", clock=lambda: self.now, **kw)

    def _worker(self):
        pass

    def _fetch_schema(self):
        self.fetches += 1
        if isinstance(self.schema_answer, Exception):
            raise self.schema_answer
        return self.schema_answer

    def _req(self, method, table, body=None, params=None, prefer=None, timeout=8.0):
        self.calls.append((method, table, body, params))
        e = self.fail(method, table, len(self.calls))
        if e:
            raise e
        return [] if method == "GET" else None

    def pump(self, now=None):
        if now is not None:
            self.now = now
        if self._want_probe:
            self._probe()
        while (job := self._q.take_due(self.now)) is not None:
            self._run(job)
            if self._want_probe:
                self._probe()


def http400(code):
    e = urllib.error.HTTPError("https://x", 400, "bad", hdrs=None, fp=None)
    e.body = json.dumps({"code": code})
    return e


def test_no_probe_unless_started_and_v1_shape_unchanged():
    c = Pump(schema=2)
    c.state({"cycle": 1, "phase": "X"}); c.pump()
    assert c.fetches == 0 and c.schema is None                    # 검사 · 옛 배선은 탐침하지 않는다
    m, table, body, params = c.calls[0]
    assert params == {"on_conflict": "bench_id"} and "set_id" not in body[0]


def test_v2_shape_after_probe_same_values_plus_set():
    c = Pump(schema=2); c.start_probe()
    c.state({"cycle": 1, "phase": "X"}); c.cycle({"cycle": 3}); c.sample(T, "C", 1, True, 60.0, 1.0, [50], 1)
    c.discharge([{"cycle": 1, "serial": 5}]); c.event(T, 1, "C", "k", "-", "d")
    c.pump()
    by = {t: (b, p) for _, t, b, p in c.calls}
    assert by["bench_state"][1] == {"on_conflict": "bench_id,set_id"} and by["bench_state"][0][0]["set_id"] == 1
    assert by["bench_sample"][1] == {"on_conflict": "bench_id,set_id,t"}
    assert by["bench_cycle"][1] == {"on_conflict": "bench_id,set_id,cycle"}
    assert by["bench_discharge"][1] == {"on_conflict": "bench_id,set_id,cycle,serial"}
    assert by["bench_event"][0][0]["set_id"] == 1


@pytest.mark.parametrize("code", ["42P10", "PGRST204", "42703"])
def test_400_schema_errors_reprobe_and_retry_uses_new_shape(code):
    """운전 중 마이그레이션 적용: 옛 모양 upsert 가 42P10 으로 실패 → 재탐침 → 30초 뒤 재시도는 새 모양으로 (보낼 때 짓기)."""
    c = Pump(schema=None); c.start_probe(); c.pump()
    assert c.schema == 1

    def migrated(m, t, n):                # 이 순간 마이그레이션이 적용됐다 — 옛 모양은 거부되고 RPC 는 2 를 돌려준다
        if n == 1:
            c.schema_answer = 2
            return http400(code)
    c.fail = migrated
    c.state({"cycle": 1}); c.pump()
    assert c.schema == 2 and len(c.calls) == 1 and c.calls[0][3] == {"on_conflict": "bench_id"}
    c.pump(T + 31)
    assert c.schema == 2 and c.calls[1][3] == {"on_conflict": "bench_id,set_id"} and c.calls[1][2][0]["set_id"] == 1


def test_probe_failure_is_v1_and_not_a_send_failure():
    c = Pump(schema=OSError("dns")); c.start_probe(); c.pump()
    st = c.stats()
    assert c.schema == 1 and st["fail_1h"] == 0 and st["fail_t"] is None


def test_set_cloud_silent_before_v2_then_tags_set():
    c = Pump(schema=None); c.start_probe(); c.pump()
    s2 = c.for_set(2)
    s2.state({"cycle": 1}); s2.event(T, 1, "P", "pair_check", "-", "d"); c.pump()
    assert c.calls == [] and not s2.enabled and s2.poll_command() is None   # 마이그레이션 전 — 세트 1 의 행을 덮지 않는다(B-H2)
    c.schema_answer = 2; c._want_probe = True; c.pump()
    s2.state({"cycle": 1}); c.pump()
    assert c.calls[-1][2][0]["set_id"] == 2


def test_latest_only_and_sample_throttle_are_per_set():
    c = Pump(schema=2); c.start_probe(); c.pump()
    c.state({"cycle": 1}); c.for_set(2).state({"cycle": 9}); c.pump()
    assert sorted(b[0]["set_id"] for _, t, b, _ in c.calls if t == "bench_state") == [1, 2]   # 세트 2 가 세트 1 을 밀어내지 않는다
    c.calls.clear()
    c.sample(T, "C", 1, True, 1.0, 0.0, [1], 1); c.for_set(2).sample(T + 1, "C", 1, True, 1.0, 0.0, [1], 1); c.pump()
    assert [b[0]["set_id"] for _, t, b, _ in c.calls if t == "bench_sample"] == [1, 2]


def test_poll_commands_takes_up_to_limit():
    class P(Pump):
        rows = [{"id": 1, "cmd": "stop_safe"}, {"id": 2, "cmd": "plug_on", "set_id": 2}]
        def _req(self, method, table, body=None, params=None, prefer=None, timeout=8.0):
            if method == "GET":
                return [self.rows.pop(0)] if self.rows else []
    assert [c["id"] for c in P().poll_commands(5)] == [1, 2]


def test_recorder_tags_only_sets_other_than_1(tmp_path):
    for sid, want in ((None, "[셀 시험대] plug · 사이클"), (1, "[셀 시험대] plug · 사이클"), (2, "[셀 시험대] plug · 세트 2 · 사이클")):
        got = []
        r = Recorder(tmp_path / str(sid), alert=got.append, set_id=sid)
        r.event(1, "C", "plug", "-", "x")
        assert got[0].startswith(want)


def test_golden_run_on_v2_has_same_values_plus_set_id(tmp_path, monkeypatch):
    """마이그레이션 뒤(판 2) 세트 1 만 돌면 — 요청에 set_id = 1 · on_conflict 에 set_id 가 붙을 뿐 값은 같다."""
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1)]); w.schema = 2

    class Sync(sw.capture_cloud(w)):          # 탐침을 시작 순간 바로 끝내 판이 결정적이게
        def start_probe(self):
            super().start_probe(); self._probe()
    sw.patch_world(monkeypatch, w)
    monkeypatch.setattr(sw.run_cycle, "Cloud", Sync)
    monkeypatch.chdir(tmp_path); monkeypatch.setattr(sys, "argv", ["run_cycle.py", "--cycles", "1"])
    assert sw.run_cycle.main() == 0
    reqs = [json.loads(r) for r in w.requests if json.loads(r)[0] == "POST"]
    states = [r for r in reqs if r[1] == "bench_state"]
    assert states and all(r[2] == {"on_conflict": "bench_id,set_id"} and r[4][0]["set_id"] == 1 for r in states)
    assert all(b.get("set_id") == 1 for r in reqs for b in (r[4] if isinstance(r[4], list) else []))


def test_supervisor_cloud_starts_probe(monkeypatch):
    import supervise
    made = []

    class C:
        def __init__(self, **kw): made.append(self); self.probed = False
        def start_probe(self): self.probed = True
    monkeypatch.setattr(cloudmod, "Cloud", C)
    make = supervise.make_deps_factory(True, lambda d, m: None)
    cfg = supervise.Config()
    make.cloud_for(cfg, Path("."))
    assert made and made[0].probed
