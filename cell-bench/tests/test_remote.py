import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench.alert import make_notifier
from cellbench.control import ControlInbox, PinGuard, StopRequested
from cellbench.cycle import CycleRunner, CycleState
from cellbench.config import Config
from cellbench.record import Recorder


def test_inbox_post_take_ack(tmp_path):
    ib = ControlInbox(tmp_path)
    ib.post("plug_on", "phone")
    assert ib.pending()["cmd"] == "plug_on"
    cmd = ib.take()
    assert cmd["cmd"] == "plug_on" and cmd["source"] == "phone"
    assert ib.pending() is None and ib.take() is None       # 한 번만 집어 간다
    ib.ack("plug_on", "켜짐")
    assert ib.last_ack()["result"] == "켜짐"


def test_inbox_rejects_unknown_command(tmp_path):
    with pytest.raises(ValueError):
        ControlInbox(tmp_path).post("format_disk", "x")


def test_pin_guard_locks_after_failures():
    g = PinGuard("1234")
    t = 1000.0
    for i in range(5):
        assert g.check("phone", "0000", now=t + i) is False
    assert g.locked("phone", now=t + 10) is True
    assert g.check("phone", "1234", now=t + 10) is False          # 잠긴 동안은 맞아도 거부
    assert g.check("phone", "1234", now=t + 700) is True          # 10분 뒤 풀림
    assert g.check("laptop", "1234", now=t + 10) is True          # 다른 주소는 영향 없음


def test_pin_guard_disabled_without_pin():
    g = PinGuard(None)
    assert g.enabled is False and g.check("x", "") is False


def test_notifier_is_silent_without_webhook():
    send = make_notifier(hook="")
    send("아무 일 없음")                                           # 예외 없이 지나가야 한다


class _Plug:
    def __init__(self): self.calls = []
    def on(self):
        self.calls.append("on"); from cellbench.plug import PlugReading; return PlugReading(True, 70.0, time.time())
    def off(self):
        self.calls.append("off"); from cellbench.plug import PlugReading; return PlugReading(False, 0.0, time.time())
    def recharge(self):
        self.calls.append("recharge"); from cellbench.plug import PlugReading; return PlugReading(True, 70.0, time.time())
    def read(self):
        from cellbench.plug import PlugReading; return PlugReading(True, 70.0, time.time())


class _Live:
    cells = {}
    def snapshot(self): return self.cells


def _runner(tmp_path):
    cfg = Config(data_dir=str(tmp_path), wifi_reconnect=False)
    return CycleRunner(cfg, _Live(), _Plug(), None, Recorder(tmp_path))


def test_remote_plug_on_forces_discharge_to_end(tmp_path):
    r = _runner(tmp_path); st = CycleState(cycle=1); st.phase = "DISCHARGE"
    r.ctrl.post("plug_on", "phone")
    r._handle_control(st)
    assert r.plug.calls == ["on"] and r._force == "charge"
    assert r.ctrl.last_ack()["cmd"] == "plug_on"
    assert "manual" in (tmp_path / "events.csv").read_text(encoding="utf-8-sig")


def test_remote_stop_raises_and_supervisor_leaves_plug_on(tmp_path):
    r = _runner(tmp_path); st = CycleState(cycle=1)
    r.ctrl.post("stop_safe", "phone")
    with pytest.raises(StopRequested):
        r._handle_control(st)

    def cycle_that_gets_stopped():
        r.current = CycleState(cycle=r.rec.next_cycle_no())
        raise StopRequested()
    r.run_cycle = cycle_that_gets_stopped
    r.plug.calls.clear()
    r.run(3)
    assert r.plug.calls == ["recharge"]
    assert "stopped_by_user" in (tmp_path / "cycles.csv").read_text(encoding="utf-8-sig")
