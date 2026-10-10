"""코드 검토 4 — 운전하지 않는 세트의 플러그 '켜기'는 끊었다 켜기(OFF→ON)가 아니라 켜짐으로 읽힐 때까지의 켜기다.

끊었다 켜기는 끄기가 되고 켜기가 실패하면 플러그를 꺼진 채 남긴다 — 켜려다 끈 셈이다. 그래서
· 짝 이상 때 다른 세트 플러그(cycle._pair_fault) · 멈춘 검증기의 플러그 · 감시자의 세트 k 켜기는 plug.ensure_on 을 쓰고
· 감시자는 켜짐으로 읽힐 때까지 점검마다 다시 하며(3번 연속 실패면 빨강)
· 엔진이 돌지 않는 동안의 플러그 지키기에 검증 중이던 세트와 bench.json 의 다른 등록 세트도 넣는다(운전하지 않던 세트는 켜기만).
"""
import pytest

import sim_world as sw
import test_supervisor as ts
from cellbench import config as configmod
from cellbench import supervisor as sup
from cellbench.plug import PlugReading, ensure_on
from cellbench.supervisor import tick

P2 = "AA:BB:CC:DD:EE:02"
S2 = list(range(11594, 11618))


class Stubborn:
    def __init__(self, ons_before_on: int):
        self.left, self.calls = ons_before_on, []

    def on(self):
        self.calls.append("on")
        self.left -= 1
        return PlugReading(self.left < 0, 0.0, 0.0)

    def off(self):                                         # 불리면 안 된다
        self.calls.append("off")
        return PlugReading(False, 0.0, 0.0)


def test_ensure_on_retries_without_turning_off():
    p = Stubborn(1)
    assert ensure_on(p, sleep=lambda s: None).on and p.calls == ["on", "on"]
    p = Stubborn(99)
    with pytest.raises(RuntimeError, match="3번 켜도"):
        ensure_on(p, sleep=lambda s: None)
    assert p.calls == ["on"] * 3                           # 끄기는 한 번도 보내지 않는다


def test_pair_fault_turns_other_plug_on_without_cutting_it(tmp_path, monkeypatch):
    """바꿔 꽂힘 → 짝 이상. 다른 세트 플러그(P2)는 끊지 않고 켠다 — 고치기 전에는 recharge(OFF → 30초 → ON)였다."""
    w = sw.World([sw.Dock(P2, sw.SET1), sw.Dock(sw.SET1_MAC, S2)])
    bench = {"bench_id": "hq-bench-1", "sets": [dict(configmod.DEFAULT_SET), {"id": 2, "plug_mac": P2, "serials": "11594-11617"}]}
    code = sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "2"], bench=bench)
    assert code == 0 and "pair_mismatch" in (tmp_path / "data" / "events.csv").read_text(encoding="utf-8-sig")
    assert w.plugs[P2] is True and "on" in w.plug_calls[P2]
    assert "off" not in w.plug_calls[P2] and "recharge" not in w.plug_calls[P2]


def test_pair_fault_alert_text_when_plug_stays_off(tmp_path, monkeypatch):
    """켜기 명령 뒤에도 꺼짐으로 읽히면 3번 해 보고 Slack 조치 알림."""
    from cellbench.cycle import CycleRunner, CycleState, PairMismatch
    cfg = configmod.Config(data_dir=str(tmp_path / "data"), sets=[dict(configmod.DEFAULT_SET),
                                                                  {"id": 2, "plug_mac": P2, "serials": "11594-11617"}])
    from cellbench.record import Recorder
    alerts = []
    rec = Recorder(cfg.data_dir, alert=alerts.append)
    r = CycleRunner(cfg, None, None, None, rec)
    p = Stubborn(99)
    r._plug_factory = lambda mac, ip: p
    monkeypatch.setattr("cellbench.cycle.time.sleep", lambda s: None)
    with pytest.raises(PairMismatch):
        r._pair_fault(CycleState(cycle=1), "시험")
    for th in r._fault_threads:
        th.join(5)
    assert p.calls == ["on"] * 3
    assert any("세트 2 플러그" in a and "켜지 못함" in a for a in alerts)


def test_supervisor_retries_verifying_plug_until_on_and_turns_red_after_three(tmp_path):
    ts.put(tmp_path, ts.eng(target_last_cycle=4, verifying=[{"id": 2, "mac": P2, "ip": ""}]), ts.now_json(4000), cycles=1)
    d = ts.World().deps()
    outcomes = [False, False, False, True]
    seen = []

    def on_mac(mac, ip):
        seen.append(mac)
        if not outcomes.pop(0):
            raise RuntimeError("3번 켜도 켜짐으로 읽히지 않음")
        return "켜짐"
    d.plug_on_mac = on_mac
    rep = None
    for i in range(3):
        rep, lines = tick(ts.cfg_for(tmp_path), tmp_path, rep, d, ts.T + 60 * i)
    assert len(seen) == 3                                   # 사건마다 한 번이 아니라 점검마다 다시
    assert rep["signals"]["plug_on"][0] == "red" and "세트 2" in rep["signals"]["plug_on"][1]
    rep, lines = tick(ts.cfg_for(tmp_path), tmp_path, rep, d, ts.T + 180)
    assert len(seen) == 4 and any("세트 2 플러그 ON (켜짐)" in l for l in lines)
    rep, _ = tick(ts.cfg_for(tmp_path), tmp_path, rep, d, ts.T + 240)
    assert len(seen) == 4                                   # 켜짐으로 읽힌 뒤에는 다시 하지 않는다


def _guard_setup(tmp_path, eng_kw, reads):
    ts.put(tmp_path, ts.eng(exit="done", plug_on=True, **eng_kw), ts.now_json(4000, "DONE"), cycles=4)
    cfg = ts.cfg_for(tmp_path, sets=[dict(configmod.DEFAULT_SET), {"id": 2, "plug_mac": P2, "serials": "11594-11617"},
                                     {"id": 3, "plug_mac": "AA:BB:CC:DD:EE:03", "serials": "11618-11641"}])
    d = ts.World().deps()
    acts = []
    d.plug_read_mac = lambda mac, ip: (acts.append(("read", mac)), reads[mac])[1]
    d.plug_on_mac = lambda mac, ip: (acts.append(("on", mac)), "켜짐")[1]
    d.plug_recharge_mac = lambda mac, ip: (acts.append(("recharge", mac)), "켜짐")[1]
    return cfg, d, acts


def test_guard_covers_registered_and_verifying_plugs_and_only_turns_them_on(tmp_path):
    """단일 모드로 끝난 뒤 — 세트 2(검증 중이던) · 세트 3(등록만)의 플러그도 지킨다. 꺼짐이면 켜고, 켜짐 · 낮은 전력이면 그대로 둔다."""
    reads = {P2: (False, 0.0), "AA:BB:CC:DD:EE:03": (True, 1.4)}
    cfg, d, acts = _guard_setup(tmp_path, {"verifying": [{"id": 2, "mac": P2, "ip": ""}]}, reads)
    rep = None
    for i in range(0, 13):
        rep, _ = tick(cfg, tmp_path, rep, d, ts.T + 60 * i)
    assert ("on", P2) in acts and ("read", "AA:BB:CC:DD:EE:03") in acts
    assert not any(a[0] == "recharge" for a in acts)                        # 운전하지 않던 세트는 끊지 않는다
    assert not any(a == ("on", "AA:BB:CC:DD:EE:03") for a in acts)
    assert set(rep["memo"]["guards"]) == {"2", "3"}


def test_guard_kicks_low_power_only_for_set_that_was_running(tmp_path):
    reads = {P2: (True, 1.4), "AA:BB:CC:DD:EE:03": (True, 1.4)}
    cfg, d, acts = _guard_setup(tmp_path, {"mode": "multi", "sets": [{"id": 1}, {"id": 2, "mac": P2, "ip": ""}]}, reads)
    rep = None
    for i in range(0, 13):
        rep, _ = tick(cfg, tmp_path, rep, d, ts.T + 60 * i)
    assert ("recharge", P2) in acts and ("recharge", "AA:BB:CC:DD:EE:03") not in acts


def test_guarded_plugs_list():
    cfg = configmod.Config(plug_mac=sw.SET1_MAC, sets=[dict(configmod.DEFAULT_SET), {"id": 2, "plug_mac": P2, "serials": "11594-11617"}])
    eng = {"sets": [{"id": 1, "mac": sw.SET1_MAC}, {"id": 2, "mac": P2.lower()}], "verifying": [{"id": 2, "mac": P2}]}
    assert sup.guarded_plugs(eng, cfg) == [{"id": 2, "mac": P2, "ip": "", "ran": True}]
    assert sup.guarded_plugs({}, cfg) == [{"id": 2, "mac": P2, "ip": "", "ran": False}]
