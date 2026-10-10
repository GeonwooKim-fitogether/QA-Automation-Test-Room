"""다중 세트 4단계 — 추출 문 · 재연결 관문 · 끊을 수 있는 기다림 · 명령 출처 · 세트 지정 명령(명세 2-4 · 2-6 · 2-7).
기본값(단일 모드)은 지금 동작이다 — 그것은 골든(test_golden_single)이 못 박는다. 여기서는 끼워 넣었을 때의 동작을 본다."""
import json
import threading

import pytest

import run_cycle
import sim_world as sw
from cellbench.control import write_json_atomic
from cellbench.cycle import CycleRunner, _target
from cellbench.shared import ExtractGate, LinkGuard


def test_gate_fifo_token_and_wrong_release():
    g = ExtractGate(clock=lambda: 1.0)
    g.queue("1"); g.queue("2")
    assert g.try_take("2") is None                      # 줄의 맨 앞이 아니다
    t1 = g.try_take("1")
    assert t1 and t1.owner == "1" and g.try_take("2") is None
    assert g.try_take("3", wait_turn=False) is None     # 깨우기는 기다리는 추출을 앞지르지 않는다
    assert g.release(None) is False and g.holder == t1
    assert g.release(t1) and g.release(t1) is False     # 낡은 토큰으로 두 번 풀지 못한다
    t2 = g.try_take("2")
    assert t2 and t2.n == 2
    g.release(t2); g.queue("4"); g.leave("4")
    assert g.try_take("5", wait_turn=False)             # 줄이 비면 깨우기도 들어간다


def test_link_guard_only_when_all_running_sets_blind():
    lg = LinkGuard(["1", "2"])
    lg.report("1", 0.0)
    assert not lg.may_reconnect(100.0, 60.0)            # 세트 2 는 들린다 — PC 전체를 끊지 않는다
    lg.report("2", 10.0)
    assert lg.may_reconnect(100.0, 60.0) and not lg.may_reconnect(65.0, 60.0)
    lg.did_reconnect(100.0)
    assert not lg.may_reconnect(150.0, 60.0) and lg.may_reconnect(221.0, 60.0)   # 2분 간격을 함께 센다
    lg.remove("2"); lg.report("1", None)
    assert not lg.may_reconnect(500.0, 60.0)


def test_target_reads_local_and_cloud_fields():
    assert _target({"cmd": "plug_on"}) is None and _target({"cmd": "x", "set": 2}) == 2
    assert _target({"cmd": "x", "set_id": None}) is None and _target({"cmd": "x", "set_id": 3}) == 3


def test_interruptible_sleep(tmp_path):
    r = CycleRunner(run_cycle.Config(data_dir=str(tmp_path)), sw.FakeLive(sw.World([])), None, None,
                    run_cycle.Recorder(tmp_path), dry_run=True)
    r._halt = threading.Event(); r._halt.set()
    with pytest.raises(KeyboardInterrupt):
        r._sleep(5)


def _with(monkeypatch, **attrs):
    """run_cycle.main 이 만드는 실행기에 조정자처럼 속성을 끼운다."""
    real = run_cycle.CycleRunner

    def make(*a, **k):
        r = real(*a, **k)
        for n, v in attrs.items():
            setattr(r, n, v)
        return r
    monkeypatch.setattr(run_cycle, "CycleRunner", make)


def test_extract_waits_for_gate_then_extracts(tmp_path, monkeypatch):
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1)])
    g = ExtractGate(clock=w.clock.time)
    other = g.try_take("2")                              # 세트 2 가 추출 중
    w.at(32 * 60, lambda: g.release(other))              # 세트 1 이 기준선(약 25분)에 닿은 뒤 몇 분 지나 놓는다
    _with(monkeypatch, gate=g)
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"])
    log = (tmp_path / "data" / "run.log").read_text(encoding="utf-8")
    rows = (tmp_path / "data" / "cycles.csv").read_text(encoding="utf-8-sig").splitlines()
    assert code == 0 and "추출 대기 — 세트 2 가 추출 중" in log and rows[1].split(",")[10] == "24"
    assert g.holder is None                              # 추출 뒤 놓았다
    samples = (tmp_path / "data" / "samples_0001.csv").read_text(encoding="utf-8-sig")
    assert "EXTRACT_WAIT" in samples                      # 기다리는 동안 표본이 돌았다


def test_extract_skipped_after_wait_limit(tmp_path, monkeypatch):
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1)])
    g = ExtractGate(clock=w.clock.time)
    g.try_take("2")                                      # 놓지 않는다
    _with(monkeypatch, gate=g)
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"])
    ev = (tmp_path / "data" / "events.csv").read_text(encoding="utf-8-sig")
    rows = (tmp_path / "data" / "cycles.csv").read_text(encoding="utf-8-sig").splitlines()
    assert code == 0 and "extract_skipped" in ev and rows[1].split(",")[10] == "0" and rows[1].split(",")[12]


def test_single_mode_drops_command_for_other_set(tmp_path, monkeypatch):
    """G6 — 세트 2 로 보낸 '플러그 끄기'가 세트 1 에서 실행되면 안 된다 (명세 2-7)."""
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1)])
    post = lambda: write_json_atomic(tmp_path / "data" / "control.json",
                                     {"cmd": "plug_off", "source": "test", "t": w.clock.t, "set": 2})
    w.at(35 * 60, post)                                  # 세트 1 충전 중
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"])
    d = tmp_path / "data"
    ev = (d / "events.csv").read_text(encoding="utf-8-sig")
    ack = json.loads((d / "control_ack.json").read_text(encoding="utf-8"))
    rows = (d / "cycles.csv").read_text(encoding="utf-8-sig").splitlines()
    assert code == 0 and "manual_rejected" in ev and "버림" in ack["result"]
    assert "manual_stop_charge" not in rows[1]           # 충전을 끝내지 않았다 = 끄지 않았다


def test_command_source_injection(tmp_path, monkeypatch):
    """다중 모드의 명령 출처 — 조정자 대기열에서 받은 명령을 같은 만료 규칙으로 실행한다."""
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1)])
    queue: list = []
    acks: list = []
    w.at(40 * 60, lambda: queue.append(({"cmd": "plug_on", "t": w.clock.t, "set": 1}, "조정자", acks.append)))
    _with(monkeypatch, commands=lambda: [queue.pop(0)] if queue else [])
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"])
    ev = (tmp_path / "data" / "events.csv").read_text(encoding="utf-8-sig")
    assert code == 0 and "원격 명령 plug_on (조정자)" in ev and acks and not (tmp_path / "data" / "control_ack.json").exists()
