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
