"""충전 중 엔진 교체 — 방전은 끝났고 충전 중에 끊긴 사이클을 새 엔진이 복구한다 (record.recover_cut_charge · run_cycle).

가짜 세상으로 엔진을 한 번 끊고(Ctrl+C = 교체 때 옛 엔진이 끝나는 것과 같다) 같은 data 폴더로 다시 띄운다.
  충전 중 끊김  → cycles.csv 에 복구 행(note=restart_in_charge) · 새 사이클 번호는 +1 · discharge 파일에 같은 셀 줄 중복 없음 · 충전을 이어 간다
  방전 중 끊김  → 기존대로(행 없음 · 같은 번호 재사용 — 그 방전은 무효)
  추출 중 끊김  → 기존 동작(행 없음)
"""
import csv

import sim_world as sw


def _world():
    return sw.World([sw.Dock(sw.SET1_MAC, sw.SET1)])


def _cut_at(w, minutes):
    def boom():
        raise KeyboardInterrupt
    w.at(minutes * 60, boom)


def _rows(tmp, name):
    with open(tmp / "data" / name, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _first_run(tmp_path, monkeypatch, minutes):
    w = _world()
    _cut_at(w, minutes)
    assert sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"]) == 130
    return w


def _second_run(tmp_path, monkeypatch, w, *argv):
    w.events.clear()
    return sw.run_main(monkeypatch, tmp_path, w, list(argv) or ["--precharge", "--cycles", "1"])


def test_cut_in_charge_writes_recovery_row_and_next_cycle_is_plus_one(tmp_path, monkeypatch):
    w = _first_run(tmp_path, monkeypatch, 40)                          # 방전 25분 · 추출 5분 뒤 충전 중
    assert not _rows(tmp_path, "cycles.csv")                           # 끊긴 사이클은 아직 행이 없다
    assert (tmp_path / "data" / "discharge_0001.csv").exists() and (tmp_path / "data" / "cells_0001.csv").exists()
    n_dis = len(_rows(tmp_path, "discharge_0001.csv"))
    n_samples = len(_rows(tmp_path, "samples_0001.csv"))
    assert _second_run(tmp_path, monkeypatch, w) == 0
    rows = _rows(tmp_path, "cycles.csv")
    assert [r["cycle"] for r in rows] == ["1", "2"]
    rec = rows[0]
    assert rec["note"] == "restart_in_charge"
    assert rec["discharge_start"] and rec["discharge_end"] and rec["discharge_h"] and rec["min_batt_at_stop"] and rec["plug_on"]
    assert rec["extract_ok"] == "24" and float(rec["extract_mb"]) > 0
    assert not any(rec[c] for c in ("full_at", "charge_min", "charge_wh", "floor_w", "plug_off"))     # 충전 칸은 비어 있다
    assert rows[1]["note"] == "" and rows[1]["full_at"]                                              # 다음 사이클은 정상으로 끝났다
    # 파일이 섞이지 않았다 — 사이클 1 의 방전 · 표본은 그대로, 같은 셀 줄이 중복되지 않는다
    assert len(_rows(tmp_path, "discharge_0001.csv")) == n_dis == 24
    assert len(_rows(tmp_path, "discharge_0002.csv")) == 24
    assert len(_rows(tmp_path, "samples_0001.csv")) == n_samples
    assert len({r["serial"] for r in _rows(tmp_path, "discharge_0001.csv")}) == 24
    run_log = (tmp_path / "data" / "run.log").read_text(encoding="utf-8-sig")
    assert "restart_in_charge" in run_log and "새 사이클은 2 번" in run_log


def test_recovery_forces_precharge_even_without_flag(tmp_path, monkeypatch):
    """새 엔진이 --precharge 없이 떠도(셀이 덜 찼다) 방전부터 하지 않고 충전을 이어 간다."""
    w = _first_run(tmp_path, monkeypatch, 40)
    for s in sw.SET1:
        w.docks[0].batt[s] = 60.0                                      # 충전 도중 — 덜 찼다
    assert _second_run(tmp_path, monkeypatch, w, "--cycles", "1") == 0
    log = (tmp_path / "data" / "run.log").read_text(encoding="utf-8-sig")
    assert "예비 충전 시작" in log.split("restart_in_charge")[1]
    rows = _rows(tmp_path, "cycles.csv")
    assert [r["cycle"] for r in rows] == ["1", "2"] and float(rows[1]["min_batt_at_stop"]) >= 0
    assert rows[1]["discharge_start"] and rows[1]["full_at"]


def test_cut_in_discharge_keeps_old_behavior(tmp_path, monkeypatch):
    w = _first_run(tmp_path, monkeypatch, 15)                          # 방전 중
    assert not (tmp_path / "data" / "discharge_0001.csv").exists()
    for s in sw.SET1:
        w.docks[0].batt[s] = 80.0
    assert _second_run(tmp_path, monkeypatch, w, "--cycles", "1") == 0
    rows = _rows(tmp_path, "cycles.csv")
    assert [r["cycle"] for r in rows] == ["1"] and rows[0]["note"] == "" and "restart_in_charge" not in rows[0]["note"]   # 같은 번호 재사용
    assert len(_rows(tmp_path, "discharge_0001.csv")) == 24


def test_cut_in_extract_keeps_old_behavior(tmp_path, monkeypatch):
    w = _first_run(tmp_path, monkeypatch, 27)                          # 추출 중 (기준선 25분 → 추출 5분)
    assert not (tmp_path / "data" / "cells_0001.csv").exists()
    assert _second_run(tmp_path, monkeypatch, w) == 0
    rows = _rows(tmp_path, "cycles.csv")
    assert "restart_in_charge" not in "".join(r["note"] for r in rows)
    assert rows[0]["cycle"] == "1"                                     # 기존 동작 — 같은 번호를 다시 쓴다(행은 끝난 사이클 하나)


def test_clean_boundary_restart_writes_no_recovery_row(tmp_path, monkeypatch):
    w = _world()
    assert sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"]) == 0
    assert sw.run_main(monkeypatch, tmp_path, w, ["--cycles", "1"]) == 0
    rows = _rows(tmp_path, "cycles.csv")
    assert [r["cycle"] for r in rows] == ["1", "2"] and all(r["note"] == "" for r in rows)
