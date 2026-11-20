"""만충 판정 — 모든 셀이 한 번 100% 를 찍은 뒤 셀 하나가 99% 로 내려가도 만충으로 잡는다.

2026-11-18 · 19 세트 1(Y) 140 · 141회: 충전 2시간 만에 24대가 모두 100% 였는데, Y Dock 은 만충 뒤 전력이 수십 분에 걸쳐
천천히 내려가고 그 사이 셀 하나가 99% 로 떨어져 '모두 100% + 10분 평탄'이 끝내 겹치지 않았다. 두 번 모두 6시간 한도
(charge_timeout)로 끝났고, 세 번 연속이면 엔진이 failsafe 로 멈춘다.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sim_world as sw  # noqa: E402
from cellbench.cycle import is_full  # noqa: E402


def _flat(start, minutes, w=34.0, step=20):
    return [(start + i * step, w) for i in range(int(minutes * 60 / step) + 1)]


def test_is_full_accepts_99_only_after_all_cells_were_100():
    t0 = 1000.0
    samples, now = _flat(t0, 12), t0 + 12 * 60
    one_99 = [100] * 23 + [99]
    assert is_full(samples, one_99, now, 10, 1.0, True, 24) is False                        # 아직 모두 100% 를 본 적 없다 — 지금과 같다
    assert is_full(samples, one_99, now, 10, 1.0, True, 24, seen_all_100=True) is True      # 한 번 모두 100% 였다 — 99% 는 받아 준다
    assert is_full(samples, [100] * 23 + [98], now, 10, 1.0, True, 24, seen_all_100=True) is False   # 98% 는 받지 않는다
    assert is_full(samples, [100] * 23, now, 10, 1.0, True, 24, seen_all_100=True) is False          # 셀 하나가 안 들리면 그대로 보류


def test_is_full_still_needs_flat_power_after_100():
    t0 = 1000.0
    falling = [(t0 + i * 20, 40 - i * 0.05) for i in range(37)]                               # 12분 동안 1.8 W 하락
    assert is_full(falling, [100] * 23 + [99], t0 + 12 * 60, 10, 1.0, True, 24, seen_all_100=True) is False


class TaperWorld(sw.World):
    """Y Dock 처럼: 모든 셀이 100% 가 된 뒤 전력이 40 W 에서 10분에 1.5 W 씩 천천히 내려가 31 W 에서 멈추고,
    100% 가 된 지 30분 뒤 셀 하나가 99% 로 내려가 다시 차지 않는다."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.t100 = None

    def integrate(self, dt):
        super().integrate(dt)
        d = self.docks[0]
        if not self.plugs.get(d.feeder):
            self.t100 = None
            return
        if self.t100 is None and all(round(b) >= 100 for b in d.batt.values()):    # 셀이 보고하는 값(반올림)이 모두 100%
            self.t100 = self.clock.t
        if self.t100 is not None and self.clock.t - self.t100 >= 30 * 60:
            d.batt[d.serials[0]] = 99.0

    def watts(self, mac):
        if self.t100 is not None and self.plugs.get(mac):
            return max(31.0, 40.0 - 0.15 * (self.clock.t - self.t100) / 60)
        return super().watts(mac)


def test_world_slow_taper_with_one_cell_at_99_ends_as_full(tmp_path, monkeypatch):
    w = TaperWorld([sw.Dock(sw.SET1_MAC, sw.SET1)], rate_pct_min=0.6, discharge_rate=2.0)
    assert sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"]) == 0
    data = tmp_path / "data"
    cyc = list(csv.DictReader(open(data / "cycles.csv", encoding="utf-8-sig", newline="")))
    kinds = [r["kind"] for r in csv.DictReader(open(data / "events.csv", encoding="utf-8-sig", newline=""))]
    assert "charge_timeout" not in kinds and cyc[-1]["note"] == ""
    assert float(cyc[-1]["charge_min"]) < 4 * 60                                             # 한도(기본 4시간) 전에 만충
    assert "99% 로 내려간 채" in (data / "run.log").read_text(encoding="utf-8")
