"""모터 M2(입회 10 mm 왕복) — 장비 없이 돈다. pysoem 을 가져오지 않는다.

승인 파일 · 입회 표지 판정, 창 밖 목표의 전송 없는 거부, 자동 Quick stop, 그리고 CiA402 를 흉내 낸 가짜 드라이브 위에서
서보 온 → PP 핸드셰이크 → Quick stop 시험 → 복귀 → 종료 순서 전체를 돌린다. 실제 승인 파일 · 표지는 건드리지 않는다(tmp_path 만).
"""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motionbench import m2, safety
from motionbench.cia402 import OUT, Inputs, state

GOOD = {"approved_by": "김건우", "approved_at": "2026-10-26T18:03:46+09:00", "first_move_dir": 1, "distance_mm": 10,
        "window_mm": 15, "vmax_mm_s": 10, "amax_mm_s2": 50, "cycles": 3}


def _write(tmp_path, name, doc):
    p = tmp_path / name
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return p


def _marker(tmp_path, age_s=10, life_s=3600):
    now = time.time()
    return _write(tmp_path, "motion_attended", {"by": "김건우", "reason": "시험", "created": now - age_s, "until": now - age_s + life_s})


def test_approval_accepts_only_complete_and_within_caps(tmp_path):
    assert safety.load_approval(_write(tmp_path, "a.json", GOOD)) == (GOOD, None)
    assert safety.load_approval(tmp_path / "없음.json")[0] is None
    assert safety.load_approval(_write(tmp_path, "b.json", "{깨짐"))[0] is None
    bad = [{k: v for k, v in GOOD.items() if k != "cycles"},
           {**GOOD, "distance_mm": 10.5}, {**GOOD, "window_mm": 16}, {**GOOD, "vmax_mm_s": 21}, {**GOOD, "amax_mm_s2": 101},
           {**GOOD, "cycles": 6}, {**GOOD, "cycles": 2.5}, {**GOOD, "first_move_dir": True}, {**GOOD, "first_move_dir": 0},
           {**GOOD, "vmax_mm_s": "10"}, {**GOOD, "amax_mm_s2": 0}, {**GOOD, "approved_by": " "},
           {**GOOD, "approved_at": "2026-10-26T18:03:46"}, {**GOOD, "distance_mm": 10, "window_mm": 10}]
    for i, doc in enumerate(bad):
        a, why = safety.load_approval(_write(tmp_path, f"bad{i}.json", doc))
        assert a is None and why.startswith("승인 파일 거부"), doc


def test_marker_validity_and_deadline(tmp_path):
    now = 1_000_000.0
    ok = {"by": "김건우", "reason": "r", "created": now - 600, "until": now + 3000}
    assert safety.marker_problem(ok, now) is None
    assert safety.marker_problem({**ok, "until": now - 1}, now)                       # 지났다
    assert safety.marker_problem({**ok, "created": now - 3601, "until": now + 60}, now)   # 신선하지 않다
    assert safety.marker_problem({**ok, "until": ok["created"] + 4 * 3600 + 1}, now)  # 4시간 넘는 표지
    assert safety.marker_problem({**ok, "by": ""}, now)
    assert safety.marker_problem({**ok, "created": "x"}, now)
    assert safety.check_marker(tmp_path / "없음", now)[0]                             # 못 읽으면 무효
    assert safety.check_marker(_write(tmp_path, "깨짐", "{"), now)[0]
    why, deadline = safety.check_marker(_marker(tmp_path, age_s=600, life_s=7200), time.time())
    assert why is None and abs(deadline - (time.time() - 600 + 3600)) < 2         # 마감 = min(until, created + 60분)


def test_servo_on_refused_without_marker(tmp_path):
    """v2 M2 ⑧ — 표지가 없거나 무효면 서보 온 시도가 거부되고 Controlword 는 0 그대로다."""
    for marker in (tmp_path / "motion_attended", _marker(tmp_path, age_s=3700, life_s=7200)):
        ax = m2.Axis(0, 15000, time.time() + 60)
        ax.last = Inputs(0, 0x0250, 1, 0, 0, 0, 0, 0)
        with pytest.raises(m2.Abort, match="서보 온 거부"):
            m2.servo_on(ax, marker)
        assert ax.cw == 0 and OUT.unpack(ax.output())[0] == 0


def test_target_outside_window_refused_without_sending(tmp_path):
    ax = m2.Axis(0, 15000, time.time() + 60)
    ax.follow, ax.cw = False, m2.PP_RUN
    for target in (15001, -15001, 14999):            # 14999 는 정지 거리(v²/2a)를 더하면 창 밖
        with pytest.raises(m2.Abort, match="전송 없이 거부"):
            m2.move(ax, target, _marker(tmp_path), 10000, 50000, 50000)
        assert (ax.cw, ax.target) == (m2.PP_RUN, 0)
    assert safety.target_problem(0, 10000, 15000, 10000, 1_000_000) is None


def test_auto_quick_stop_rules_latch():
    v = Inputs(0, 0x0237, 1, 100, 0, 0, 10, 0)
    assert safety.auto_qs(v, 2.0, 0, 15000, 1e12, 0) is None
    trips = [(v, 51.0, 1e12), (v._replace(sw=0x0218), 2.0, 1e12), (v._replace(err=0x2600), 2.0, 1e12),
             (v._replace(ferr=-2001), 2.0, 1e12), (v._replace(pos=15001), 2.0, 1e12), (v, 2.0, 0)]
    for x, gap, deadline in trips:
        assert safety.auto_qs(x, gap, 0, 15000, deadline, 1.0), x
    ax = m2.Axis(0, 15000, time.time() + 60)
    ax.cw = m2.ENABLE
    ax.output()
    ax.step(None, v._replace(ferr=5000), 1)
    assert "추종 오차" in ax.qs and OUT.unpack(ax.output())[0] == m2.QUICK_STOP
    ax2 = m2.Axis(0, 15000, time.time() + 60)
    ax2.lost(-1, 3)
    assert "WKC" in ax2.qs


def test_unit_speed_start_checks():
    assert safety.units_problem(dict(safety.GEAR)) is None
    assert safety.units_problem({**safety.GEAR, "0x6091:01": 2000})
    assert safety.speed_problem(10, 7400) is None
    assert safety.speed_problem(10, 10) and safety.speed_problem(10, None) and safety.speed_problem(10, 0)
    assert safety.start_problem(1, safety.SERIAL, 0, 0x0250, 2) is None
    assert safety.start_problem(2, safety.SERIAL, 0, 0x0250, 2)
    assert safety.start_problem(1, 1, 0, 0x0250, 2)
    assert "Fault reset 하지 않는다" in safety.start_problem(1, safety.SERIAL, 0x2600, 0x0218, 2)
    assert safety.start_problem(1, safety.SERIAL, 0, 0x0250, 1)


class FakeDrive(threading.Thread):
    """CiA402 + PP 를 흉내 내는 1 ms 드라이브 — M2 가 쓰는 명령만. Fault reset(0x80)이 오면 실패로 남긴다."""
    SW = {"SOD": 0x0250, "RTSO": 0x0231, "SO": 0x0233, "OE": 0x0237, "QSA": 0x0217}

    def __init__(self, ax, pos, speed=40):
        super().__init__(daemon=True)
        self.ax, self.pos, self.speed, self.st = ax, pos, speed, "SOD"
        self.goal, self.ack, self.prev, self.go, self.cws, self.bad = pos, False, 0, True, [], []

    def run(self):
        while self.go:
            cw, mode, tgt, _ = OUT.unpack(self.ax.output())
            if not self.cws or self.cws[-1] != cw:
                self.cws.append(cw)
            if cw & 0x80:
                self.bad.append("fault reset")
            st, low = self.st, cw & 0x0F
            if not cw & 0x02:
                st = "SOD"
            elif cw & 0x06 == 0x02:
                st = "QSA" if st == "OE" else "SOD"
            elif low & 0x07 == 0x06 and st in ("SOD", "SO", "OE"):
                st = "RTSO"
            elif low == 0x07 and st in ("RTSO", "OE"):
                st = "SO"
            elif low == 0x0F and st == "SO":
                st = "OE"
            elif st == "QSA":
                st = "SOD"
            if st == "QSA":
                self.goal = self.pos                    # 경사로 생략 — 바로 선다
            if st == "OE" and cw & 0x10 and not self.prev & 0x10:
                self.goal, self.ack = tgt, True
                if abs(tgt - self.ax.p0) > self.ax.window:
                    self.bad.append(f"창 밖 목표 {tgt}")
            if not cw & 0x10:
                self.ack = False
            if st == "OE":
                self.pos += max(-self.speed, min(self.speed, self.goal - self.pos))
            self.prev, self.st = cw, st
            sw = self.SW[st] | (0x1000 if self.ack else 0) | (0x0400 if self.pos == self.goal else 0)
            self.ax.step(None, Inputs(0, sw, mode, self.pos, 0, 0, 0, 0), time.perf_counter_ns())
            time.sleep(0.001)


def test_whole_sequence_on_fake_drive(tmp_path):
    marker = _marker(tmp_path)
    p0, D = 1000, 10000
    ax = m2.Axis(p0, 15000, time.time() + 600)
    drive = FakeDrive(ax, p0)
    drive.start()
    try:
        m2.servo_on(ax, marker)
        assert state(ax.last.sw) == "OPERATION_ENABLED" and ax.target == p0
        out = m2.move(ax, p0 + D, marker, 10000, 50000, 1_000_000)
        back = m2.move(ax, p0, marker, 10000, 50000, 1_000_000)
        assert out["err_um"] == back["err_um"] == 0 and out["reached"] == p0 + D
        qs = m2.move(ax, p0 + D, marker, 10000, 50000, 1_000_000, qs_after=D // 2)
        assert D // 2 <= qs["qs_at"] - p0 < D and qs["qs_distance"] <= 5000
        m2.resume(ax, marker)
        assert m2.move(ax, p0, marker, 10000, 50000, 1_000_000)["reached"] == p0
        did = m2.finish(ax)
        assert state(ax.last.sw) == "SWITCH_ON_DISABLED", did
    finally:
        drive.go = False
        drive.join(1)
    assert not drive.bad
    assert 0x3F in drive.cws and 0x2F in drive.cws and drive.cws[-1] == 0
    assert drive.cws[-3:] == [m2.QUICK_STOP, m2.SHUTDOWN, 0]           # 종료 순서: Quick stop → 0x06 → 0
