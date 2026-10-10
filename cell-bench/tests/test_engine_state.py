"""엔진 쪽 손질 — 감시자가 읽는 engine.json · 심박 · 끝난 뒤 안전 상태(플러그 ON) · 죽기 직전 플러그 ON ·
플러그 호출 상한 시간 · 추출 중 심박. 장비 없이 가짜 플러그와 이 PC 안의 소켓 짝으로만 돈다.
"""
import asyncio
import json
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_cycle
from cellbench import proc
from cellbench import protocol as P
from cellbench import record
from cellbench.config import Config
from cellbench.control import StopRequested
from cellbench.cycle import CycleRunner, CycleState
from cellbench.plug import Plug, PlugReading
from cellbench.record import Recorder, cycles_done


class FakeLive:
    def snapshot(self):
        return {}


class FakePlug:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def recharge(self, gap_s=10.0):
        self.calls.append("recharge")
        if self.fail:
            raise RuntimeError("플러그 응답 없음")
        return PlugReading(True, 61.0, time.time())

    def read(self):                                          # 끝낸 뒤 확인(_safe_end)의 읽기 — calls 에는 남기지 않는다
        return PlugReading(True, 61.0, time.time())


class SpyCloud:
    enabled = False

    def __init__(self):
        self.states = []

    def state(self, payload):
        self.states.append(payload)

    def __getattr__(self, name):
        return lambda *a, **k: None


def runner(tmp_path, plug=None, dry=False, **cfg_over):
    cfg_over.setdefault("stuck_s", 0.0)                      # 끝낸 뒤 확인(_safe_end)이 60초 기다리지 않게
    cfg = Config(data_dir=str(tmp_path), wifi_reconnect=False, **cfg_over)
    return CycleRunner(cfg, FakeLive(), plug or FakePlug(), None, Recorder(tmp_path), dry_run=dry)


def now_of(tmp_path):
    return json.loads((tmp_path / "now.json").read_text(encoding="utf-8"))


# ---------- engine.json ----------

def test_engine_json_start_and_exit(tmp_path):
    rec = Recorder(tmp_path)
    args = SimpleNamespace(cycles=4, precharge=True, dry_run=False, config=None)
    info = run_cycle.engine_info(Config(), args, next_no=3)
    assert info["target_last_cycle"] == 6 and info["pid"] == os.getpid() and info["bench_id"] == "hq-bench-1"
    rec.engine_start(info)
    e = json.loads((tmp_path / "engine.json").read_text(encoding="utf-8"))
    assert e["exit"] is None and e["args"]["precharge"] is True and rec.engine_exit_kind is None
    rec.engine_exit("done", plug_on=True)
    e = json.loads((tmp_path / "engine.json").read_text(encoding="utf-8"))
    assert e["exit"] == "done" and e["plug_on"] is True and e["ended"] >= e["started"]


def test_cycles_done_matches_next_cycle_no(tmp_path):
    rec = Recorder(tmp_path)
    assert cycles_done(tmp_path / "cycles.csv") == 0 and rec.next_cycle_no() == 1
    rec.cycle({"cycle": 1}); rec.cycle({"cycle": 2})
    assert cycles_done(tmp_path / "cycles.csv") == 2 and rec.next_cycle_no() == 3
    assert cycles_done(tmp_path / "없음.csv") == 0


# ---------- 심박 ----------

def test_now_json_carries_beat_but_cloud_does_not(tmp_path):
    cloud = SpyCloud()
    rec = Recorder(tmp_path, cloud=cloud)
    t0 = time.time()
    rec.now({"phase": "DISCHARGE", "t": t0})
    assert now_of(tmp_path)["beat"] >= t0
    assert "beat" not in cloud.states[-1]                  # 클라우드에 올리는 것은 그대로


def test_beat_updates_only_beat_and_is_throttled(tmp_path, monkeypatch):
    rec = Recorder(tmp_path)
    rec.beat()
    assert not (tmp_path / "now.json").exists()            # 이번 실행에서 아직 화면을 안 썼으면 건드리지 않는다
    rec.now({"phase": "EXTRACT", "t": 1.0, "cells": [1, 2]})
    first = now_of(tmp_path)["beat"]
    rec.beat()
    assert now_of(tmp_path)["beat"] == first               # 5초 안에는 다시 쓰지 않는다
    monkeypatch.setattr(record, "BEAT_MIN_GAP_S", 0.0)
    time.sleep(0.01)
    rec.beat()
    n = now_of(tmp_path)
    assert n["beat"] > first and n["phase"] == "EXTRACT" and n["t"] == 1.0 and n["cells"] == [1, 2]


def test_beat_from_many_extract_threads_is_safe(tmp_path, monkeypatch):
    """추출 묶음의 작업 스레드들이 동시에 심박을 찍어도 now.json 이 깨지지 않는다."""
    monkeypatch.setattr(record, "BEAT_MIN_GAP_S", 0.0)
    rec = Recorder(tmp_path)
    rec.now({"phase": "EXTRACT"})
    errors = []

    def hammer():
        try:
            for _ in range(30):
                rec.beat()
        except Exception as e:                              # pragma: no cover - 실패 시 내용 보이기
            errors.append(e)

    ths = [threading.Thread(target=hammer) for _ in range(6)]
    [t.start() for t in ths]; [t.join() for t in ths]
    assert errors == [] and now_of(tmp_path)["phase"] == "EXTRACT"


def test_cell_link_progress_beats_during_extraction(tmp_path):
    """추출 데이터가 들어오는 동안 progress 가 불린다 — 로그 없이 수십 분 걸리는 추출도 심박이 뛴다."""
    from cellbench.cells import CellLink, CellResult
    calls = []
    live = SimpleNamespace(wait_resume=lambda s, since, timeout: 1.0)
    link = CellLink(Config(resume_timeout_s=1), live, log=lambda m: None, progress=lambda: calls.append(1))
    pc, cell = socket.socketpair()
    data = bytes(range(256)) * 16                           # 4096 바이트
    blocks = b"".join(struct.pack("<II", i * P.BLOCK_DATA, sum(data)) + data for i in range(3))
    status = P.HEADER + bytes([P.MSG_STATUS, 0, 0xF0, 88]) + struct.pack("<i", len(blocks)) + b"\x00"

    def wait_request():
        got = b""
        while len(got) < 5:                                  # PC 의 명령 프레임(머리 4 + 종류 1)이 올 때까지
            got += cell.recv(64)

    def fake_cell():
        cell.sendall(b"\x00" * 46)                          # 첫 인사
        wait_request(); cell.sendall(status)                # 0x11 상태 요청에 답
        wait_request()                                      # 0x12 업로드 요청
        for i in range(0, len(blocks), P.BLOCK_LEN):
            cell.sendall(blocks[i:i + P.BLOCK_LEN]); time.sleep(0.05)
        cell.sendall(P.MARK)
        wait_request()                                      # 0x26 복귀
        cell.close()

    th = threading.Thread(target=fake_cell); th.start()
    res = CellResult(11733, "127.0.0.1")
    link._serve(pc, res, extract=True, out_dir=tmp_path, verify=lambda: None)   # 신원 확인은 필수 인자다 — 검사는 허용을 명시로
    th.join()
    assert res.ended and res.bad_blocks == 0 and res.got == 3 * P.BLOCK_DATA
    assert len(calls) >= 1                                  # 받기 한 번마다 불린다 (몇 번으로 나뉘어 오는지는 OS 몫)


# ---------- 끝난 뒤 안전 상태 ----------

def _cycles_ok(r):
    def one():
        r.current = CycleState(cycle=r.rec.next_cycle_no())
        r.rec.cycle({"cycle": r.current.cycle, "note": "ok"})
    return one


def test_done_leaves_plug_on_and_phase_done(tmp_path):
    r = runner(tmp_path)
    r.run_cycle = _cycles_ok(r)
    assert r.run(2) == "done"
    assert r.plug.calls == ["recharge"] and r.plug_on_at_exit is True
    n = now_of(tmp_path)
    assert n["phase"] == "DONE" and n["plug"]["on"] is True


def test_done_reports_when_plug_on_failed(tmp_path):
    r = runner(tmp_path, plug=FakePlug(fail=True))
    r.run_cycle = _cycles_ok(r)
    assert r.run(1) == "done" and r.plug_on_at_exit is False      # 감시자가 이것을 보고 대신 켠다


def test_done_dry_run_does_not_touch_plug(tmp_path):
    r = runner(tmp_path, dry=True)
    r.run_cycle = _cycles_ok(r)
    assert r.run(1) == "done" and r.plug.calls == [] and r.plug_on_at_exit is None


def test_failsafe_returns_reason(tmp_path):
    r = runner(tmp_path, max_consecutive_failures=2)

    def boom():
        r.current = CycleState(cycle=r.rec.next_cycle_no())
        raise RuntimeError("boom")

    r.run_cycle = boom
    r._recover = lambda st: None
    assert r.run(5) == "failsafe" and r.plug_on_at_exit is True


def test_stop_during_precharge_is_a_clean_stop(tmp_path):
    """예전에는 예비 충전 중의 원격 정지가 처리되지 않은 예외로 프로그램을 끝냈다 → 감시자가 '비정상'으로 되살렸을 것."""
    r = runner(tmp_path)

    def stop():
        r.current = CycleState(cycle=r.rec.next_cycle_no(), phase="PRECHARGE")
        raise StopRequested()

    r.precharge = stop
    assert r.run(3, precharge=True) == "stopped"
    assert r.plug.calls == ["recharge"] and now_of(tmp_path)["phase"] == "STOPPED"


def test_stop_during_recover_is_a_clean_stop(tmp_path):
    r = runner(tmp_path)

    def boom():
        r.current = CycleState(cycle=r.rec.next_cycle_no())
        raise RuntimeError("boom")

    def recover(st):
        raise StopRequested()

    r.run_cycle, r._recover = boom, recover
    assert r.run(3) == "stopped"


# ---------- 죽기 직전 · 설정 오류 ----------

def test_last_gasp_only_when_exit_unknown(tmp_path):
    rec = Recorder(tmp_path)
    rec.engine_start({"pid": 1, "started": 1.0})
    plug = FakePlug()
    run_cycle.last_gasp(rec, [plug])                         # exit 가 비어 있음 = 처리되지 않은 예외
    assert plug.calls == ["recharge"]
    rec.engine_exit("interrupted")                           # Ctrl+C — 플러그는 사람 뜻대로 그대로
    run_cycle.last_gasp(rec, [plug])
    assert plug.calls == ["recharge"]
    run_cycle.last_gasp(Recorder(tmp_path / "b"), [])        # 플러그를 아직 만들지 않았으면 아무것도 안 함
    rec2 = Recorder(tmp_path / "c"); rec2.engine_start({"pid": 1, "started": 1.0})
    run_cycle.last_gasp(rec2, [FakePlug(fail=True)])         # 실패해도 조용히


def test_config_error_record(tmp_path):
    args = SimpleNamespace(cycles=4, config="x.json")
    run_cycle.config_error(tmp_path, args, KeyError("알 수 없는 설정: pol_s"))
    e = json.loads((tmp_path / "engine.json").read_text(encoding="utf-8"))
    assert e["exit"] == "config_error" and "pol_s" in e["error"]


def test_config_error_does_not_clobber_live_engine(tmp_path):
    born = proc.created(os.getpid())
    live = {"pid": os.getpid(), "started": born + 1, "exit": None}      # 이 검사 프로세스를 '돌고 있는 엔진'으로
    (tmp_path / "engine.json").write_text(json.dumps(live), encoding="utf-8")
    run_cycle.config_error(tmp_path, SimpleNamespace(cycles=1, config=None), ValueError("x"))
    assert json.loads((tmp_path / "engine.json").read_text(encoding="utf-8"))["exit"] is None


# ---------- 플러그 호출 상한 ----------

def test_plug_call_times_out_instead_of_hanging(monkeypatch):
    import cellbench.plug as plug_mod
    p = Plug.__new__(Plug)                                   # 자격 증명 관리자를 읽지 않게 생성자를 건너뛴다
    p.cfg = Config(plug_call_timeout_s=0.2, plug_retries=2)

    async def never(action):
        await asyncio.sleep(30)

    p._do = never
    monkeypatch.setattr(plug_mod.time, "sleep", lambda s: None)
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="응답 없음"):
        p._call("read")
    assert time.monotonic() - t0 < 3
