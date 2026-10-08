"""2026-10-07 밤 사고를 재현하는 검사.

1) 결과판이 now.json 을 열고 있는 순간 바꿔치기가 '액세스 거부'로 실패해 시험 전체가 멈췄다.
2) PC Wi-Fi 가 끊기면 셀이 하나도 안 보이는데, 방전 중이었다면 플러그 OFF 로 영원히 기다렸을 것이다.
3) 사이클 하나의 예외가 프로그램 전체를 끝냈다.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench.config import Config
from cellbench.cycle import CycleAborted, CycleRunner, CycleState
from cellbench.record import Recorder


class FakeLive:
    def __init__(self, cells=None):
        self.cells = cells or {}

    def snapshot(self):
        return self.cells


class FakePlug:
    def __init__(self):
        self.calls = []

    def on(self):
        self.calls.append("on")
        from cellbench.plug import PlugReading
        return PlugReading(True, 60.0, time.time())


def runner(tmp_path, **cfg_over):
    cfg = Config(data_dir=str(tmp_path), wifi_reconnect=False, **cfg_over)
    return CycleRunner(cfg, FakeLive(), FakePlug(), None, Recorder(tmp_path)), cfg


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 의 파일 잠금 동작")
def test_now_json_survives_reader_holding_file(tmp_path):
    rec = Recorder(tmp_path)
    rec.now({"phase": "A"})
    with open(tmp_path / "now.json", "rb"):           # 결과판 서버가 읽는 중
        ok = rec.now({"phase": "B"})                   # 예전엔 여기서 PermissionError 로 시험이 죽었다
    assert ok is False
    assert rec.now({"phase": "C"}) is True             # 놓은 뒤에는 다시 된다


def test_publish_never_raises(tmp_path):
    r, _ = runner(tmp_path)
    r.rec.now = lambda payload: (_ for _ in ()).throw(PermissionError("locked"))
    r._publish(CycleState(cycle=1))                    # 예외가 밖으로 나오면 안 된다


def test_blind_aborts_cycle_after_failsafe_time(tmp_path):
    r, cfg = runner(tmp_path, blind_failsafe_min=5.0)
    st = CycleState(cycle=1)
    r._watch_link(st)                                  # 처음 안 보임 → 기록만
    assert r._blind_since is not None and st.events == 1
    r._blind_since = time.time() - 6 * 60              # 6분째 안 보임
    with pytest.raises(CycleAborted):
        r._watch_link(st)


def test_blind_clears_when_cells_return(tmp_path):
    from cellbench.cells import CellLive
    r, _ = runner(tmp_path)
    st = CycleState(cycle=1)
    r._watch_link(st)
    r.live.cells = {11733: CellLive("1.1.1.1", 50, 0, 4, -10, t=time.time())}
    r._watch_link(st)
    assert r._blind_since is None


def test_supervisor_records_abort_and_continues(tmp_path):
    r, _ = runner(tmp_path)
    calls = {"n": 0, "recover": 0}

    def flaky_cycle():
        calls["n"] += 1
        r.current = CycleState(cycle=r.rec.next_cycle_no())
        if calls["n"] == 1:
            raise PermissionError("now.json locked")   # 어젯밤과 같은 종류의 예외
        r.rec.cycle({"cycle": r.current.cycle, "note": "ok"})

    r.run_cycle = flaky_cycle
    r._recover = lambda st: calls.__setitem__("recover", calls["recover"] + 1)
    r.run(1)
    rows = (tmp_path / "cycles.csv").read_text(encoding="utf-8-sig").splitlines()
    assert calls["n"] == 2 and calls["recover"] == 1
    assert "crash: PermissionError" in rows[1] and rows[2].endswith("ok")


def test_supervisor_gives_up_with_plug_on(tmp_path):
    r, _ = runner(tmp_path, max_consecutive_failures=2)

    def always_fail():
        r.current = CycleState(cycle=r.rec.next_cycle_no())
        raise RuntimeError("boom")

    r.run_cycle = always_fail
    r._recover = lambda st: None
    r.run(3)
    assert r.plug.calls == ["on"]                      # 멈출 때 충전 쪽으로 둔다
