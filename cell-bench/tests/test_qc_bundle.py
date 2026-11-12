"""시험 실행기(tools/qc_tests/*_run.py)와 묶음 실행기(qc_bundle.py) — 모터 · LabSat · 셀 · 플러그 없이 차례와 기록 폴더만 본다.
2026-11-12 사용자 결정: 시험마다 실행기를 따로 두되, 함께 돌릴 때는 준비와 셀 기록 받기를 한 번만 한다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

QT = Path(__file__).resolve().parents[1] / "tools" / "qc_tests"
sys.path.insert(0, str(QT))
import run_common as RC  # noqa: E402
import qc_bundle  # noqa: E402
import firmware1_run  # noqa: E402
import firmware2_run  # noqa: E402
import gnss1_run  # noqa: E402
from motionbench import qc_gate  # noqa: E402

Q = RC.Q


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """장비 대신 호출을 적어 두는 가짜. calls 에 차례대로 쌓인다."""
    calls = []
    pre = tmp_path / "presets"; pre.mkdir()
    for name in ("FIRMWARE-1_a50", "FIRMWARE-1_a500", "IMU-2_long", "FIRMWARE-2_v300", "FIRMWARE-2_v100", "FIRMWARE-2_v500"):
        (pre / f"{name}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Q.run_rail, "PRESETS", pre)
    monkeypatch.setattr(Q, "DATA", tmp_path / "data")
    truth = tmp_path / "truth.csv"; truth.write_text("t_s,v_mm_s\n", encoding="utf-8")
    monkeypatch.setattr(Q, "FW2_SCENES", {v: (f"S_{v}", truth, f"FIRMWARE-2_v{v}") for v in (300, 100, 500)})
    monkeypatch.setattr(RC.time, "sleep", lambda s: None)
    monkeypatch.setattr(Q, "rail_prep", lambda tests, minutes, out, say: (calls.append(("prep", tuple(tests))), ("ls", "tap", {"ok": True}))[1])
    monkeypatch.setattr(Q, "rail_finish", lambda ls, tap, say: calls.append(("finish", tap)))
    monkeypatch.setattr(Q, "extract", lambda layout, say: (calls.append(("extract", layout)), tmp_path / "data" / "ftg_y_1")[1])
    monkeypatch.setattr(Q, "gps_ready", lambda ls, scene, say, tap, test, out, minutes: (calls.append(("gps", scene, test)), (23, "tap2"))[1])
    rcs = {}

    def fake_run_rail(argv):
        test, out = argv[0], Path(argv[argv.index("--out") + 1])
        preset = argv[argv.index("--preset") + 1]
        calls.append(("rail", test, out.parent.name, out.name, preset))
        return rcs.get(preset, 0)

    monkeypatch.setattr(Q.run_rail, "main", fake_run_rail)
    return calls, rcs, tmp_path / "data"


def _summary(data: Path, folder_prefix: str) -> dict:
    (d,) = [p for p in data.iterdir() if p.name.startswith(folder_prefix)]
    return json.loads((d / "summary.json").read_text(encoding="utf-8"))


def test_bundle_prepares_once_drives_in_order_and_extracts_once(rig):
    calls, _, data = rig
    assert qc_bundle.main(["--tests", "FIRMWARE-1,IMU-2", "--n", "2"]) == 0
    assert [c[0] for c in calls] == ["prep", "rail", "rail", "rail", "finish", "extract"]
    assert calls[0] == ("prep", ("IMU-2", "FIRMWARE-1"))                       # 주는 순서와 상관없이 운영 순서 — 센서 시험 IMU-2 먼저
    assert [c[2:] for c in calls if c[0] == "rail"] == [
        (f"IMU-2_{Q.time.strftime('%Y%m%d')}", "long", "IMU-2_long"),
        (f"FIRMWARE-1_{Q.time.strftime('%Y%m%d')}", "a50", "FIRMWARE-1_a50"),
        (f"FIRMWARE-1_{Q.time.strftime('%Y%m%d')}", "a500", "FIRMWARE-1_a500")]
    f1, i2 = _summary(data, "FIRMWARE-1_"), _summary(data, "IMU-2_")
    assert f1["test"] == "FIRMWARE-1" and i2["test"] == "IMU-2" and f1["together"] == i2["together"] == ["IMU-2", "FIRMWARE-1"]
    assert f1["ftg"] == i2["ftg"] and f1["rc"] == i2["rc"] == 0 and f1["columns"] and f1["layout"] == "Y-shift"
    for d in data.iterdir():                                                    # 실행 기록은 두 폴더에 같은 줄로
        if d.name.startswith(("FIRMWARE-1_", "IMU-2_")):
            assert "함께 돌림" in (d / "run.log").read_text(encoding="utf-8")


def test_failed_drive_stops_later_tests_but_still_finishes_and_extracts(rig):
    calls, rcs, data = rig
    rcs["FIRMWARE-1_a50"] = 1
    assert qc_bundle.main(["--tests", "FIRMWARE-1,IMU-2,FIRMWARE-2"]) == 1
    assert [c[4] for c in calls if c[0] == "rail"] == ["IMU-2_long", "FIRMWARE-1_a50"]   # 500 · FIRMWARE-2 는 돌지 않는다
    assert ("finish", "tap") in calls and calls[-1][0] == "extract"


def test_firmware2_goes_last_and_changes_scene_per_speed(rig):
    calls, _, _ = rig
    assert qc_bundle.main(["--tests", "FIRMWARE-2,IMU-2", "--speeds", "100,500"]) == 0
    assert [c[1] for c in calls if c[0] == "rail"] == ["IMU-2", "FIRMWARE-2", "FIRMWARE-2"]
    assert [c[1:] for c in calls if c[0] == "gps"] == [("S_100", "IMU-2,FIRMWARE-2"), ("S_500", "IMU-2,FIRMWARE-2")]
    assert ("finish", "tap2") in calls                                          # 장면을 바꾸며 다시 켠 실시간 기록을 끈다


def test_imu1_runs_first_before_rail_prep(rig, monkeypatch):
    import imu1_run
    calls, _, data = rig
    monkeypatch.setattr(imu1_run, "drive", lambda rec, say, a: calls.append(("imu1", a.boots)) or 0)
    assert qc_bundle.main(["--tests", "FIRMWARE-1,IMU-1", "--boots", "2", "--accels", "50"]) == 0
    assert [c[0] for c in calls] == ["imu1", "prep", "rail", "finish", "extract"] and calls[0] == ("imu1", 2)
    assert calls[1] == ("prep", ("FIRMWARE-1",))                               # 레일 준비에는 레일 시험만
    assert _summary(data, "IMU-1_")["ftg"] == _summary(data, "FIRMWARE-1_")["ftg"]


def test_runner_drops_cells_failed_in_earlier_tests(rig):
    from motionbench import qc_gate
    calls, _, data = rig
    data.mkdir(parents=True, exist_ok=True)
    doc = {"cells": {}}
    qc_gate.record(doc, 10683, "GNSS-1", qc_gate.FAIL, "GNSS-1_20261112/r02", "두 번 걸림")
    qc_gate.record(doc, 10690, "IMU-2", qc_gate.FAIL, "IMU-2_20261112", "비 0.70")
    qc_gate.record(doc, 10691, "FIRMWARE-2", qc_gate.FAIL, "x", "뒤 시험 — 앞 시험이 아니다")
    qc_gate.save(qc_gate.path("Y", data), doc)
    assert qc_bundle.main(["--tests", "IMU-2,FIRMWARE-1", "--n", "1"]) == 0
    i2, f1 = _summary(data, "IMU-2_"), _summary(data, "FIRMWARE-1_")
    assert list(i2["excluded"]) == ["10683"]                                    # IMU-2 앞은 GNSS-1 · IMU-1
    assert sorted(f1["excluded"]) == ["10683", "10690"] and "IMU-2 불합격" in f1["excluded"]["10690"]
    assert f1["gate_file"] == "qc_gate_Y.json" and f1["include_failed"] is False
    log = next(d for d in data.iterdir() if d.name.startswith("FIRMWARE-1_")) / "run.log"
    assert "앞 시험 불합격 2대를 판정 대상에서 뺀다" in log.read_text(encoding="utf-8")


def test_include_failed_turns_the_gate_off(rig):
    from motionbench import qc_gate
    calls, _, data = rig
    data.mkdir(parents=True, exist_ok=True)
    doc = {"cells": {}}
    qc_gate.record(doc, 10683, "GNSS-1", qc_gate.FAIL, "GNSS-1_20261112/r02", "두 번 걸림")
    qc_gate.save(qc_gate.path("Y", data), doc)
    assert firmware1_run.main(["--n", "1", "--accels", "50", "--include-failed", "--no-extract"]) == 0
    s = _summary(data, "FIRMWARE-1_")
    assert s["excluded"] == {} and s["include_failed"] is True


def test_single_runner_uses_the_same_path(rig):
    calls, _, data = rig
    assert firmware1_run.main(["--n", "1", "--accels", "500", "--no-extract"]) == 0
    assert [c[0] for c in calls] == ["prep", "rail", "finish"]
    s = _summary(data, "FIRMWARE-1_")
    assert s["together"] == ["FIRMWARE-1"] and s["ftg"] is None and s["a500"]["preset"] == "FIRMWARE-1_a500"


@pytest.mark.parametrize("tests", ["GNSS-1,IMU-2", "GNSS-1", "C", "A,B", "IMU-2,FIRMWARE-3"])
def test_refuses_before_touching_hardware(rig, tests):
    calls, _, _ = rig
    with pytest.raises(SystemExit):
        qc_bundle.main(["--tests", tests])
    assert calls == []


def test_missing_preset_or_scene_stops_before_hardware(rig):
    calls, _, _ = rig
    for argv in (["--tests", "FIRMWARE-1", "--accels", "700"], ["--tests", "FIRMWARE-2", "--speeds", "200"]):
        with pytest.raises(SystemExit):
            qc_bundle.main(argv)
    with pytest.raises(SystemExit):
        firmware2_run.main(["--speeds", "999"])
    assert calls == []


def test_gnss1_records_scene_pinned_window_and_holds_loop_lock(tmp_path, monkeypatch):
    """GNSS-1 은 혼자 돈다 — 수집 실행기 잠금 · 플러그 끔 · 장면 300분부터 · 감쇠 0 에서 독 셀 위치 잡기 → 18 dB → 0 · 판정 구간 장면 321 ~ 346분."""
    calls, clock = [], {"t": 1000.0}
    monkeypatch.setattr(Q, "DATA", tmp_path / "data")
    monkeypatch.setattr(gnss1_run.time, "time", lambda: clock["t"])
    monkeypatch.setattr(gnss1_run.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))

    class Ls:
        def stop(self): calls.append("stop")
        def play(self, name, from_s=None): calls.append(("play", name, from_s)); return clock["t"]
        def status(self): return ("S01-Static-GLE-8h", 0)
        def _one(self, cmd): calls.append(cmd); return "OK"

    lay = Q.layout_columns("Y")
    # 들리는 셀을 모두 다시 켠 뒤에도 독 셀 2대(1행 · 3행)와 독 밖 셀 1대가 위치를 못 잡는다 — 독 셀만 재부팅하면 잡는다
    stuck = {lay[0][0], lay[2][3], 99999}
    monkeypatch.setattr(Q, "unlocked", lambda: sorted(stuck))

    def reboot(serials, say):
        calls.append(("reboot", sorted(serials), clock["t"]))
        if len([c for c in calls if isinstance(c, tuple) and c[0] == "reboot"]) > 1:
            stuck.difference_update(serials)

    monkeypatch.setattr(Q, "LabSat", Ls)
    monkeypatch.setattr(Q, "hold_loop_lock", lambda: calls.append("lock") or object())
    monkeypatch.setattr(Q.charge, "plug_set", lambda on, say: calls.append(("plug", on)) or {"ok": True})
    monkeypatch.setattr(Q, "start_tap", lambda test, minutes, log: calls.append(("tap", test, clock["t"])) or "tap")
    monkeypatch.setattr(Q, "stop_tap", lambda tap: None)
    monkeypatch.setattr(Q, "qc_now", lambda: {"cells": {str(s): {"age_s": 1.0, "battery": 80} for s in range(10677, 10689)}})
    monkeypatch.setattr(Q, "reboot", reboot)
    monkeypatch.setattr(Q, "extract", lambda layout, say: tmp_path / "ftg_y_1")
    # 판정 구간 실시간 패킷 — 꽂은 1 · 3행 셀은 '회차 39.4 + 칸 기준값' 세기. 3행 1칸 셀은 그보다 5 dB 낮고, 3행 2칸 셀은 위치를 못 잡았다
    eff = qc_gate.slot_table(qc_gate.criteria("GNSS-1"), "감쇠18_행1,3")
    sig = {x: {"cn0_sim": 39.4 + eff[f"{c + 1}-{r + 1}"], "gps_rx": 100.0, "n": 1500} for c in (0, 2) for r, x in enumerate(lay[c])}
    sig[lay[2][0]] = {"cn0_sim": 39.4 + eff["3-1"] - 5.0, "gps_rx": 100.0, "n": 1500}
    sig[lay[2][1]] = {"cn0_sim": None, "gps_rx": 0.0, "n": 1500}
    sig[lay[1][0]] = {"cn0_sim": 20.0, "gps_rx": 100.0, "n": 1500}            # 2행 — 꽂지 않은 행이라 거르지 않는다
    monkeypatch.setattr(gnss1_run, "round_items", lambda t0, t1: sig)
    assert gnss1_run.main(["2"]) == 0
    assert calls[:4] == ["lock", ("plug", False), "stop", ("play", "S01-Static-GLE-8h", 300 * 60)]
    # 위치 잡기: 감쇠 0 → 들리는 셀 모두 재부팅 → 90초 뒤 못 잡은 '독' 셀만 재부팅(독 밖 99999 는 빼고) → 다 잡은 뒤 감쇠 18 → 끝나고 0
    reboots = [c for c in calls if isinstance(c, tuple) and c[0] == "reboot"]
    assert len(reboots) == 2 and reboots[1][1] == sorted({lay[0][0], lay[2][3]})
    tap_after_heard = [c for c in calls if isinstance(c, tuple) and c[0] == "tap"][1][2]
    assert reboots[1][2] - tap_after_heard >= 90
    i18 = calls.index("ATTN:18")
    assert calls.index("ATTN:00") < calls.index(reboots[1]) < i18 < len(calls) - 1 - calls[::-1].index("ATTN:00")
    assert len([c for c in calls if isinstance(c, tuple) and c[0] == "tap"]) == 3          # 재부팅 때 실시간 기록을 멈췄다 다시 켠다
    (d,) = [p for p in (tmp_path / "data").iterdir() if p.is_dir()]
    assert d.name.startswith("GNSS-1_")
    s = json.loads((d / "r02" / "summary.json").read_text(encoding="utf-8"))
    assert s["test"] == "GNSS-1" and s["rows"] == [1, 3] and s["rec_scene_min"] == [321.0, 346.0] and s["attn_db"] == 18
    assert s["scene_from_min"] == 300.0 and s["dock_unlocked"] == [] and s["attn_after_lock"] is True
    t0 = Q.time.mktime(Q.time.strptime(s["window"]["from"], "%Y-%m-%d %H:%M:%S"))
    t1 = Q.time.mktime(Q.time.strptime(s["window"]["to"], "%Y-%m-%d %H:%M:%S"))
    assert abs((t0 - s["t_play"]) - (321 - 300) * 60) <= 1 and abs((t1 - t0) - 25 * 60) <= 1     # 장면 시각으로 정한 구간
    # 1차 거르기: 회차 신호 세기를 기록하고, 꽂은 행만 칸 기준값으로 거른다
    assert s["cn0"][str(lay[1][0])]["tested"] is False and str(lay[1][0]) not in s["gate"] and len(s["gate"]) == 12
    assert s["gate"][str(lay[2][0])]["status"] == "재측정" and s["gate"][str(lay[2][0])]["base_kind"] == "칸 기준"
    assert s["gate"][str(lay[2][1])]["status"] == "재측정" and "위치" in s["gate"][str(lay[2][1])]["why"]
    assert all(s["gate"][str(x)]["status"] == "합격" for x in lay[0])                     # 약한 칸(1행 3칸 −4.6 dB)의 정상 셀도 합격
    gate = json.loads((tmp_path / "data" / "qc_gate_Y.json").read_text(encoding="utf-8"))
    r = gate["cells"][str(lay[2][0])]["GNSS-1"]
    assert r["source"] == f"{d.name}/r02" and r["rounds"][0]["cond"] == "감쇠18_행1,3" and r["rounds"][0]["level"] == 39.4
