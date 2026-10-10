import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench.cloud import Cloud, NoCloud
from cellbench.record import Recorder


class FakeCloud(Cloud):
    """HTTP 대신 호출을 기록한다. 큐·스레드는 그대로 쓴다."""
    def __init__(self, **kw):
        self.calls = []
        super().__init__(log=lambda m: None, url="https://x.supabase.co", key="k" * 50, bench_id="t1", **kw)
    def _req(self, method, table, body=None, params=None, prefer=None, timeout=8.0):
        self.calls.append((method, table, body, params))
        if method == "GET" and table == "bench_command":
            return [{"id": 7, "cmd": "plug_on", "requested_by": "a@b.c"}]
        return None
    def drain(self):
        t0 = time.time()
        while self._q.unfinished_tasks and time.time() - t0 < 3:
            time.sleep(0.02)


def test_disabled_without_keys():
    c = Cloud(log=lambda m: None, url="", key="", bench_id="")
    assert c.enabled is False
    c.state({"phase": "X"}); c.event(time.time(), 1, "X", "k", "-", "d")      # 무음
    assert c.poll_command() is None


def test_sample_is_throttled_to_once_per_minute():
    c = FakeCloud(sample_every_s=60)
    t = 1000.0
    c.sample(t, "CHARGE", 1, True, 68.0, 1.0, [90, 92], 2)
    c.sample(t + 20, "CHARGE", 1, True, 68.0, 1.4, [90, 92], 2)
    c.sample(t + 61, "CHARGE", 1, True, 67.0, 2.0, [91, 93], 2)
    c.drain()
    posts = [x for x in c.calls if x[1] == "bench_sample"]
    assert len(posts) == 2 and posts[0][2][0]["batt_avg"] == 91.0


def test_poll_marks_taken_and_ack_patches():
    c = FakeCloud()
    cmd = c.poll_command()
    assert cmd["cmd"] == "plug_on"
    c.ack(7, "켜짐"); c.drain()
    patches = [x for x in c.calls if x[0] == "PATCH"]
    assert patches[0][2] .get("taken_at") and patches[1][2] == {"result": "켜짐"} and patches[1][3] == {"id": "eq.7"}


def test_recorder_forwards_to_cloud(tmp_path):
    c = FakeCloud()
    rec = Recorder(tmp_path, cloud=c)
    rec.begin_cycle(3)
    rec.sample("DISCHARGE", False, 0.0, 0.0, [70, 72], 0)
    rec.event(3, "DISCHARGE", "blind", "-", "x")
    rec.cycle({"cycle": 3, "note": "ok"})
    rec.discharge(3, {11733: 100}, {11733: 30}, 3600 * 4, 30)
    rec.now({"phase": "DISCHARGE", "cycle": 3})
    c.drain()
    tables = [x[1] for x in c.calls]
    assert {"bench_sample", "bench_event", "bench_cycle", "bench_discharge", "bench_state"} <= set(tables)
    cyc = next(x for x in c.calls if x[1] == "bench_cycle")
    assert cyc[2][0]["cycle"] == 3 and cyc[3] == {"on_conflict": "bench_id,cycle"}


def test_recorder_default_is_nocloud(tmp_path):
    assert isinstance(Recorder(tmp_path).cloud, NoCloud)


# ---------- 실패를 버리지 않는다: 재시도 · 상한 · 키 거부 · 실패 기록 · 통계 (FMEA 7.4 · 7.5 · 3.7) ----------
import csv
import urllib.error

from cellbench import cloud as cloudmod

T = 1_800_000_000.0


def http(code):
    return urllib.error.HTTPError("https://x.supabase.co/rest/v1/t", code, "err", hdrs=None, fp=None)


class PumpCloud(Cloud):
    """작업 스레드 없이 가짜 시계로 — 검사가 pump() 로 '지금 보낼 것'을 직접 꺼내 보낸다. 네트워크에는 닿지 않는다."""
    def __init__(self, fail=None, **kw):
        self.calls, self.logs, self.now = [], [], T
        self.fail = fail or (lambda method, table, n: None)     # 예외를 돌려주면 그 호출이 실패한다
        super().__init__(log=self.logs.append, url="https://x.supabase.co", key="k" * 50, bench_id="t1",
                         clock=lambda: self.now, **kw)
    def _worker(self):
        pass
    def _req(self, method, table, body=None, params=None, prefer=None, timeout=8.0):
        self.calls.append((self.now, method, table, body))
        e = self.fail(method, table, len(self.calls))
        if e:
            raise e
        if method == "GET":
            return []
        return None
    def pump(self, now=None):
        if now is not None:
            self.now = now
        while (job := self._q.take_due(self.now)) is not None:
            self._run(job)
    def times(self, table):
        return [c[0] - T for c in self.calls if c[2] == table]


def test_retry_after_30s_2min_10min_then_give_up():
    c = PumpCloud(fail=lambda m, t, n: OSError("인터넷 없음"))
    c.event(T, 1, "CHARGE", "plug", "-", "x")
    for dt in (0, 29, 30, 149, 150, 749, 750):
        c.pump(T + dt)
    assert c.times("bench_event") == [0, 30, 150, 750]           # 처음 1번 + 다시 3번
    s = c.stats()
    assert s["dropped"] == 1 and s["queue"] == 0 and s["fail_1h"] == 4 and s["fail_t"] == T + 750 and s["ok_t"] is None
    c.pump(T + 5000)
    assert len(c.times("bench_event")) == 4 and c.stats()["fail_1h"] == 0     # 더 하지 않고, 1시간 지난 실패는 세지 않는다


def test_retry_succeeds_and_records_ok():
    c = PumpCloud(fail=lambda m, t, n: OSError("잠깐 끊김") if n == 1 else None)
    c.cycle({"cycle": 3})
    c.pump(T); c.pump(T + 30)
    assert c.times("bench_cycle") == [0, 30]
    s = c.stats()
    assert s["ok_t"] == T + 30 and s["dropped"] == 0 and s["fail_1h"] == 1 and s["queue"] == 0


def test_old_state_is_never_resent_over_a_newer_one():
    c = PumpCloud(fail=lambda m, t, n: OSError("x") if n == 1 else None)
    c.state({"phase": "CHARGE", "cycle": 1})
    c.pump(T)                                                     # 첫 상태 실패 → 30초 뒤 재시도 예약
    c.state({"phase": "FULL", "cycle": 1})
    c.pump(T + 10)                                                # 새 상태 성공
    c.pump(T + 30)                                                # 옛 상태 재시도는 건너뛴다
    sent = [x[3][0]["phase"] for x in c.calls if x[2] == "bench_state"]
    assert sent == ["CHARGE", "FULL"] and c.stats()["queue"] == 0
    c.state({"phase": "A"}); c.state({"phase": "B"}); c.pump(T + 40)   # 쌓인 상태는 가장 새것만
    assert [x[3][0]["phase"] for x in c.calls if x[2] == "bench_state"][-1:] == ["B"]
    assert len([x for x in c.calls if x[2] == "bench_state"]) == 3


def test_full_queue_drops_samples_first_then_states():
    c = PumpCloud()
    c._q.cap = 4
    c.event(T, 1, "X", "plug", "-", "e1")
    c.sample(T, "X", 1, True, 1.0, 0.1, [50], 1)
    c.sample(T + 61, "X", 1, True, 1.0, 0.1, [50], 1)
    c.state({"phase": "X"})                                        # 4건 — 가득
    c.cycle({"cycle": 1})                                          # 가장 오래된 표본을 버린다
    c.cycle({"cycle": 2})                                          # 남은 표본을 버린다
    c.cycle({"cycle": 3})                                          # 표본이 없으니 상태를 버린다
    c.sample(T + 122, "X", 1, True, 1.0, 0.1, [50], 1)             # 새 표본이 버려진다
    assert c.stats()["dropped"] == 4
    c.pump(T + 200)
    assert [x[2] for x in c.calls] == ["bench_event", "bench_cycle", "bench_cycle", "bench_cycle"]


def test_auth_rejection_three_in_a_row_alerts_once():
    alerts = []
    codes = {"seq": [401, 403, OSError("망"), 401, 401, 401, None, 401, 401, 401]}

    def fail(m, t, n):
        v = codes["seq"][n - 1]
        return http(v) if isinstance(v, int) else v

    c = PumpCloud(fail=fail, on_alert=alerts.append)
    for i in range(10):
        c.event(T, 1, "X", "k", "-", str(i))
        c.pump(T + i)                                              # 새 건만 (재시도는 30초 뒤라 아직)
    # 401·403 이 셋째에서 알림(망 오류는 끊지도 세지도 않는다), 이어지는 401 은 다시 알리지 않고, 성공 뒤 다시 셋이면 또 알린다
    assert alerts == [cloudmod.KEY_REJECTED, cloudmod.KEY_REJECTED]
    assert c.stats()["consecutive_auth_fail"] == 3
    assert any("키가 거부됨" in l for l in c.logs)


def test_poll_failure_counts_for_stats_and_auth():
    c = PumpCloud(fail=lambda m, t, n: http(401))
    assert c.poll_command() is None
    s = c.stats()
    assert s["consecutive_auth_fail"] == 1 and s["fail_1h"] == 1


def test_on_fail_is_bundled_to_once_per_10_minutes():
    hooks = []
    c = PumpCloud(fail=lambda m, t, n: OSError("x"), on_fail=lambda kind, detail: hooks.append((c.now - T, kind, detail)))
    for dt in (0, 60, 599):
        c.event(T + dt, 1, "X", "k", "-", "d"); c.pump(T + dt)
    assert len(hooks) == 1 and hooks[0][1] == "event" and "실패 1건" in hooks[0][2]
    c.cycle({"cycle": 9}); c.pump(T + 600)                         # 그사이 재시도 실패들도 함께 센다
    assert len(hooks) == 2 and hooks[1][0] == 600 and "지난 10분 사이 실패" in hooks[1][2]


def test_hook_errors_do_not_stop_retries():
    def boom(*a):
        raise RuntimeError("훅 고장")
    c = PumpCloud(fail=lambda m, t, n: OSError("x") if n == 1 else None, on_fail=boom, on_alert=boom)
    c.event(T, 1, "X", "k", "-", "d")
    c.pump(T); c.pump(T + 30)
    assert c.times("bench_event") == [0, 30] and c.stats()["ok_t"] == T + 30


def test_stats_shape_and_nocloud():
    c = PumpCloud()
    assert set(c.stats()) == {"enabled", "ok_t", "fail_t", "fail_1h", "consecutive_auth_fail", "queue", "dropped"}
    assert c.stats()["enabled"] is True
    assert NoCloud().stats() == {"enabled": False}
    off = Cloud(log=lambda m: None, url="", key="", bench_id="")
    assert off.stats()["enabled"] is False and off.stats()["queue"] == 0


def test_engine_wiring_records_cloud_fail_event(tmp_path):
    """run_cycle.py 에 붙일 배선(보고서의 코드 조각)과 같은 모양 — 전송 실패가 events.csv 에 cloud_fail 로 남는다."""
    rec = Recorder(tmp_path)
    c = PumpCloud(fail=lambda m, t, n: OSError("인터넷 없음"),
                  on_fail=lambda kind, detail: rec.event(rec.next_cycle_no(), "-", "cloud_fail", "-", detail))
    rec.cloud = c
    rec.event(1, "CHARGE", "plug", "-", "플러그 무응답")
    c.pump(T)
    with open(tmp_path / "events.csv", encoding="utf-8-sig") as f:
        kinds = [r["kind"] for r in csv.DictReader(f)]
    assert kinds == ["plug", "cloud_fail"]


def test_real_worker_thread_wakes_up_for_retry(monkeypatch):
    """작업 스레드가 재시도 시각까지 기다렸다가 깨어나 다시 보내는지 — 간격만 짧게 바꿔 실제 스레드로 본다."""
    monkeypatch.setattr(cloudmod, "RETRY_DELAYS_S", (0.2, 0.2, 0.2))

    class Flaky(FakeCloud):
        def _req(self, method, table, body=None, params=None, prefer=None, timeout=8.0):
            self.calls.append((time.time(), table))
            if len(self.calls) == 1:
                raise OSError("잠깐 끊김")
    c = Flaky()
    c.event(time.time(), 1, "X", "k", "-", "d")
    t0 = time.time()
    while len(c.calls) < 2 and time.time() - t0 < 3:
        time.sleep(0.02)
    assert len(c.calls) == 2 and c.calls[1][0] - c.calls[0][0] >= 0.15
    c.drain()
    assert c.stats()["queue"] == 0 and c.stats()["ok_t"] is not None
