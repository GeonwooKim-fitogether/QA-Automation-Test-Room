"""다중 세트 2단계 — 방향 대조 (a) ~ (d) · 방전 7시간 한도 · 짝 이상 때 모든 등록 플러그 켬 (명세 3-6 · D2 · D3).

단일 모드에도 들어가는 안전 장치라 두 가지를 본다: 고장이면 반드시 발동하고, 정상 운전(실측과 비슷한 합성 기록)에서는 발동하지 않는다.
TestPC 의 실제 기록을 흘리는 G12 는 사본이 없어 '보류'다 — 사본이 생기면 TESTPC_SAMPLES 에 폴더를 주고 돌린다.
"""
import json
import math
import os
import random
from pathlib import Path

import pytest

import sim_world as sw
from cellbench import guards
from cellbench.config import SAFETY

S2 = list(range(11594, 11618))
P2 = "AA:BB:CC:DD:EE:02"
BENCH2 = {"bench_id": "hq-bench-1", "sets": [dict(sw.configmod.DEFAULT_SET),
                                              {"id": 2, "plug_mac": P2, "serials": "11594-11617"}]}


def _b(n: int, v: float) -> dict[int, int]:
    return {s: int(round(v)) for s in range(1000, 1000 + n)}


# ---------- 순수 판정 ----------

def test_median_change_needs_min_cells():
    assert guards.median_change(_b(24, 50), _b(24, 47), 12) == -3.0
    assert guards.median_change(_b(11, 50), _b(11, 40), 12) is None        # 양 끝 모두 들린 셀이 모자라면 판정 보류
    half = {s: v for s, v in _b(24, 40).items() if s < 1012}
    assert guards.median_change(_b(24, 50), half, 12) == -10.0


def _dw():
    return guards.DirectionWatch(SAFETY, stuck_w=10.0)


def test_charge_side_drop_fires_only_when_plug_on_and_at_least_stuck_w():
    w = _dw(); w.start_charge(0)
    fired = None
    for i in range(0, 50 * 60, 20):                       # 켜짐 · 31 W(만충 근처 Dock 도 10 W 이상) · 셀은 분당 0.34 %p 하락
        fired = fired or w.feed(i, _b(24, 80 - 0.34 * i / 60), True, 31.0)
    assert fired and "먹이지 않는다" in fired
    w = _dw(); w.start_charge(0)                           # Dock 1.5 W 멈춤 — 저전력 복구 몫이라 짝 이상이 아니다
    assert not any(w.feed(i, _b(24, 80 - 0.34 * i / 60), True, 1.5) for i in range(0, 60 * 60, 20))
    w = _dw(); w.start_charge(0)                           # 플러그를 못 읽은 표본이 창에 있으면 판정하지 않는다
    assert not any(w.feed(i, _b(24, 80 - 0.34 * i / 60), None if i % 300 == 0 else True, 68.0) for i in range(0, 60 * 60, 20))


def test_charge_side_waits_after_plug_on():
    w = _dw(); w.start_charge(0)                           # 켠 뒤 20분은 보지 않는다 (추출 · 충전 시작의 흔들림)
    assert not any(w.feed(i, _b(24, 80 - 0.34 * i / 60), True, 68.0) for i in range(0, 39 * 60, 20))


def test_discharge_rise_and_start_check():
    w = _dw(); w.start_discharge(0, _b(24, 60))
    got = [w.feed(i, _b(24, 60 + 0.4 * i / 60), False, 0.0) for i in range(20, 25 * 60, 20)]
    assert any(g and "다른 플러그" in g for g in got)
    w = _dw(); w.start_discharge(0, _b(24, 100))             # 100 % 에서 꺼졌는데 30분 동안 그대로 → (c)
    got = [w.feed(i, _b(24, 100), False, 0.0) for i in range(20, 31 * 60, 20)]
    assert any(g and "방전 시작" in g for g in got)
    w = _dw(); w.start_discharge(0, _b(24, 100))             # 정상 방전 — 표시가 15분 머물다 내려가도 30분 안에 3 %p
    seq = [w.feed(i, _b(24, 100 if i < 900 else 100 - 0.34 * (i - 900) / 60), False, 0.0) for i in range(20, 60 * 60, 20)]
    assert not any(seq)
    w = _dw(); w.start_discharge(0, _b(24, 100))             # 플러그가 켜짐으로 읽혔다 → 이 방전은 대조하지 않는다 (사람 · 원격 켜기 몫)
    assert not any(w.feed(i, _b(24, 100), True if i == 40 else False, 0.0) for i in range(20, 40 * 60, 20))


def test_cross_watch_needs_still_before_and_drop_after():
    c = guards.CrossWatch(SAFETY, {2: list(range(1000, 1024))})
    for i in range(0, 31 * 60, 20):
        assert c.feed(i, _b(24, 90)) is None
    c.plug_off(31 * 60)
    got = [c.feed(i, _b(24, 90 - 0.34 * (i - 31 * 60) / 60)) for i in range(31 * 60, 50 * 60, 20)]
    assert any(g and "세트 2" in g for g in got)
    c = guards.CrossWatch(SAFETY, {2: list(range(1000, 1024))})   # 사람이 일부러 꺼 둔 세트 — 끄기 전에도 내려가고 있었다
    for i in range(0, 31 * 60, 20):
        c.feed(i, _b(24, 90 - 0.34 * i / 60))
    c.plug_off(31 * 60)
    assert not any(c.feed(i, _b(24, 90 - 0.34 * i / 60)) for i in range(31 * 60, 70 * 60, 20))
    assert guards.CrossWatch(SAFETY, {}).feed(0, {}) is None       # 등록 세트가 하나면 아무 일도 하지 않는다


# ---------- 가짜 세상 — 진짜 run_cycle.main ----------

def _events(tmp):
    return (tmp / "data" / "events.csv").read_text(encoding="utf-8-sig")


def _engine(tmp):
    return json.loads((tmp / "data" / "engine.json").read_text(encoding="utf-8"))


def test_crossed_wiring_turns_on_every_registered_plug_and_stops(tmp_path, monkeypatch):
    """세트 1 의 셀은 P2 가, 세트 2 의 셀은 P1 이 먹인다(바꿔 꽂힘). 세트 1 이 방전을 시작하며 P1 을 끄면 → 짝 이상."""
    w = sw.World([sw.Dock(P2, sw.SET1), sw.Dock(sw.SET1_MAC, S2)])
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "2"], bench=BENCH2)
    ev = _events(tmp_path)
    assert code == 0 and "pair_mismatch" in ev
    eng = _engine(tmp_path)
    assert eng["exit"] == "failsafe" and eng["plug_on"] is True           # 감시자는 되살리지 않고 사람을 부른다
    assert w.plugs[sw.SET1_MAC] and w.plugs[P2]                          # 등록된 플러그 둘 다 켜졌다
    assert "recharge" in w.plug_calls[P2]                                # 다른 세트 플러그는 공장(_plug_factory)으로 켰다
    assert any("pair_mismatch" in a for a in w.alerts)                   # Slack
    rows = (tmp_path / "data" / "cycles.csv").read_text(encoding="utf-8-sig").splitlines()
    assert "pair_mismatch" in rows[-1] and len(rows) == 2                # 사이클 2 는 시작하지 않았다


def test_charge_side_drop_in_precharge(tmp_path, monkeypatch):
    """세트 1 의 Dock 은 꺼진 P2 가 먹이고, P1 은 세트 2 Dock 을 충전한다(68 W). 예비 충전 중 세트 1 셀이 내려간다 → (a)."""
    w = sw.World([sw.Dock(P2, sw.SET1), sw.Dock(sw.SET1_MAC, S2, batt={s: 40.0 for s in S2})],
                 discharge_rate=0.34, rate_pct_min=0.4, plugs={P2: False})
    code = sw.run_main(monkeypatch, tmp_path, w, ["--precharge", "--cycles", "1"], bench=BENCH2)
    assert code == 0 and "충전 쪽인데" in _events(tmp_path) and _engine(tmp_path)["exit"] == "failsafe"
    assert w.plugs[P2] is True                                           # 꺼져 있던 P2 도 켰다


def test_cross_watch_in_single_mode(tmp_path, monkeypatch):
    """세트 2 는 운전하지 않는다. 세트 1 의 P1 이 세트 2 Dock 까지 먹인다 — P1 을 끄자 멈춰 있던 세트 2 셀이 내려간다 → (d)."""
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1, batt={s: 40.0 for s in sw.SET1}),
                  sw.Dock(sw.SET1_MAC, S2, batt={s: 100.0 for s in S2})])
    code = sw.run_main(monkeypatch, tmp_path, w, ["--precharge", "--cycles", "1"], bench=BENCH2)
    assert code == 0 and "운전하지 않는 세트 2" in _events(tmp_path) and _engine(tmp_path)["exit"] == "failsafe"


def test_cross_watch_quiet_when_other_set_was_already_discharging(tmp_path, monkeypatch):
    """세트 2 는 사람이 꺼 둬서 처음부터 내려가고 있다 — 세트 1 의 정상 끄기에 짝 이상을 내면 안 된다."""
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1, batt={s: 40.0 for s in sw.SET1}),
                  sw.Dock(P2, S2, batt={s: 100.0 for s in S2})], plugs={P2: False}, discharge_rate=0.5)
    code = sw.run_main(monkeypatch, tmp_path, w, ["--precharge", "--cycles", "1"], bench=BENCH2)
    assert code == 0 and "pair_mismatch" not in _events(tmp_path) and _engine(tmp_path)["exit"] == "done"


def test_discharge_time_limit_goes_to_charge_path(tmp_path, monkeypatch):
    """방전이 7시간 넘게 기준선에 닿지 않으면 RECOVER 가 아니라 충전 길로 넘긴다 (D3)."""
    w = sw.World([sw.Dock(sw.SET1_MAC, sw.SET1, batt={s: 100.0 for s in sw.SET1})], discharge_rate=0.12)
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"])
    ev = _events(tmp_path)
    assert code == 0 and "discharge_timeout" in ev and "pair_mismatch" not in ev
    rows = (tmp_path / "data" / "cycles.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(rows) == 2 and "discharge_timeout" in rows[1] and rows[1].split(",")[12]   # 만충까지 갔다
    h = float(rows[1].split(",")[4])
    assert 7.0 <= h < 7.1
    assert not (tmp_path / "data" / "holds.csv").exists()               # RECOVER 로 가지 않았다


# ---------- G12 — 정상 기록을 흘려도 발동하지 않는다 ----------

def _synthetic_cycles(seed: int, n: int = 6):
    """실측과 비슷한 합성 기록: 충전 0.35~0.47 %p/분 · 방전 0.34 %p/분 · 정수 % · 셀별 ±1 흔들림 · 끈 뒤 100 % 에 0~20분 머묾 ·
    만충 뒤 바닥 29~33 W · 충전 중 Dock 1.5 W 멈춤 한 번(저전력 복구가 끊었다 켬) · 추출 5분 공백 · 셀 몇 대 잠깐 안 들림."""
    rnd = random.Random(seed)
    t, out = 0.0, []
    for c in range(n):
        hold = rnd.uniform(0, 20 * 60)
        b0 = 100.0
        yield ("discharge", t, None)
        for i in range(0, int(3.6 * 3600), 20):
            v = b0 if i < hold else b0 - 0.34 * (i - hold) / 60
            if v <= 30:
                break
            yield ("s", t + i, (False, 0.0, v))
        t += i
        yield ("charge", t, None)
        rate = rnd.uniform(0.35, 0.47)
        stuck_at = rnd.uniform(40, 90) * 60
        for i in range(0, int(4 * 3600), 20):
            if 5 * 60 <= i < 10 * 60:
                continue                                  # 추출 — 표본이 없다
            stuck = stuck_at <= i < stuck_at + 120
            v = min(100.0, 30 + rate * i / 60 - (0.3 if stuck else 0))
            w = 1.5 if stuck else (68.0 if v < 100 else rnd.uniform(29, 33))
            yield ("s", t + i, (True, w, v))
        t += i


@pytest.mark.parametrize("seed", range(5))
def test_g12_synthetic_normal_history_never_fires(seed):
    rnd = random.Random(100 + seed)
    w = guards.DirectionWatch(SAFETY, stuck_w=10.0)
    for kind, t, x in _synthetic_cycles(seed):
        if kind == "discharge":
            w.start_discharge(t, {s: 100 for s in range(24)}) if x is None else None
        elif kind == "charge":
            w.start_charge(t)
        else:
            on, watts, v = x
            batts = {s: int(round(min(100, max(0, v + rnd.choice((-1, 0, 0, 1)))))) for s in range(24)
                     if rnd.random() > 0.05}
            assert w.feed(t, batts, on, watts) is None, (kind, t, v)


@pytest.mark.skipif(not os.environ.get("TESTPC_SAMPLES"), reason="G12 보류 — TestPC 의 samples_*.csv · cycles.csv 사본이 없다")
def test_g12_testpc_history_never_fires():
    """TestPC 사본이 생기면: TESTPC_SAMPLES=<사본 data 폴더>. 셀별 값이 없는 samples CSV 는 batt_min · avg · max 로만 흉내 낸다 —
    사본에 now.json 기록(셀별)이 있으면 그것을 쓰도록 이 검사를 넓힌다."""
    import csv
    d = Path(os.environ["TESTPC_SAMPLES"])
    w = guards.DirectionWatch(SAFETY, stuck_w=10.0)
    phase = None
    for f in sorted(d.glob("samples_*.csv")):
        for r in csv.DictReader(open(f, encoding="utf-8-sig")):
            t = sw._real_time.mktime(sw._real_time.strptime(r["time"], "%Y-%m-%d %H:%M:%S"))
            if not r.get("batt_avg"):
                continue
            avg = float(r["batt_avg"])
            if r["phase"] != phase:
                phase = r["phase"]
                if phase == "DISCHARGE":
                    w.start_discharge(t, {s: int(round(avg)) for s in range(24)})
                elif phase in ("PRECHARGE", "PLUG_ON"):
                    w.start_charge(t)
            on = {"on": True, "off": False}.get(r["plug"])
            watts = float(r["watts"]) if r["watts"] else math.nan
            assert w.feed(t, {s: int(round(avg)) for s in range(24)}, on, watts) is None, (f.name, r["time"])
