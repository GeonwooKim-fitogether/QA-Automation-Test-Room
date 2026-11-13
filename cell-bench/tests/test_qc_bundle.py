"""시험 실행기(tools/qc_tests/*_run.py)와 묶음 실행기(qc_bundle.py) — 모터 · LabSat · 셀 · 플러그 없이 차례와 기록 폴더만 본다.
2026-11-12 사용자 결정: 시험마다 실행기를 따로 두되, 함께 돌릴 때는 준비와 셀 기록 받기를 한 번만 한다.
같은 날 결정으로 시험이 여섯이 됐다 — 장면 동기 운전은 GNSS-2, FIRMWARE-1 은 가속도 단계 운전, FIRMWARE-2 는 보류."""
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
import firmware1_step_run  # noqa: E402
import gnss2_run  # noqa: E402
import gnss1_run  # noqa: E402

Q = RC.Q


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """장비 대신 호출을 적어 두는 가짜. calls 에 차례대로 쌓인다."""
    calls = []
    pre = tmp_path / "presets"; pre.mkdir()
    for name in ("FIRMWARE-1_a50", "FIRMWARE-1_a500", "IMU-2_long", "GNSS-2_v300", "GNSS-2_v100", "GNSS-2_v500",
                 *(f"FIRMWARE-1_step-a{a}" for a in (500, 1000, 2000, 4000, 8000))):
        (pre / f"{name}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Q.run_rail, "PRESETS", pre)
    monkeypatch.setattr(Q, "DATA", tmp_path / "data")
    truth = tmp_path / "truth.csv"; truth.write_text("t_s,v_mm_s\n", encoding="utf-8")
    monkeypatch.setattr(Q, "GNSS2_SCENES", {v: (f"S_{v}", truth, f"GNSS-2_v{v}") for v in (300, 100, 500)})
    monkeypatch.setattr(firmware1_step_run, "motor_result",
                        lambda sub: {"exit": "ok", "alarms": [], "ferr_max_um": 100, "speed_max_mm_s": 900, "motor_json": None})
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
    assert qc_bundle.main(["--tests", "FIRMWARE-1,IMU-2", "--n", "2", "--steps", "500,1000"]) == 0
    assert [c[0] for c in calls] == ["prep", "rail", "rail", "rail", "finish", "extract"]
    assert calls[0] == ("prep", ("IMU-2", "FIRMWARE-1"))                       # 주는 순서와 상관없이 운영 순서 — 센서 시험 IMU-2 먼저
    assert [c[2:] for c in calls if c[0] == "rail"] == [
        (f"IMU-2_{Q.time.strftime('%Y%m%d')}", "long", "IMU-2_long"),
        (f"FIRMWARE-1_{Q.time.strftime('%Y%m%d')}", "01_a500", "FIRMWARE-1_step-a500"),
        (f"FIRMWARE-1_{Q.time.strftime('%Y%m%d')}", "02_a1000", "FIRMWARE-1_step-a1000")]
    f1, i2 = _summary(data, "FIRMWARE-1_"), _summary(data, "IMU-2_")
    assert f1["test"] == "FIRMWARE-1" and i2["test"] == "IMU-2" and f1["together"] == i2["together"] == ["IMU-2", "FIRMWARE-1"]
    assert f1["ftg"] == i2["ftg"] and f1["rc"] == i2["rc"] == 0 and f1["columns"] and f1["layout"] == "Y-shift"
    for d in data.iterdir():                                                    # 실행 기록은 두 폴더에 같은 줄로
        if d.name.startswith(("FIRMWARE-1_", "IMU-2_")):
            assert "함께 돌림" in (d / "run.log").read_text(encoding="utf-8")


def test_failed_drive_stops_later_tests_but_still_finishes_and_extracts(rig):
    calls, rcs, data = rig
    rcs["FIRMWARE-1_step-a500"] = 1
    assert qc_bundle.main(["--tests", "FIRMWARE-1,IMU-2,GNSS-2"]) == 1
    assert [c[4] for c in calls if c[0] == "rail"] == ["IMU-2_long", "FIRMWARE-1_step-a500"]   # 다음 단계 · GNSS-2 는 돌지 않는다
    assert ("finish", "tap") in calls and calls[-1][0] == "extract"


def test_gnss2_goes_last_and_changes_scene_per_speed(rig):
    calls, _, _ = rig
    assert qc_bundle.main(["--tests", "GNSS-2,IMU-2", "--speeds", "100,500"]) == 0
    assert [c[1] for c in calls if c[0] == "rail"] == ["IMU-2", "GNSS-2", "GNSS-2"]
    assert [c[1:] for c in calls if c[0] == "gps"] == [("S_100", "IMU-2,GNSS-2"), ("S_500", "IMU-2,GNSS-2")]
    assert ("finish", "tap2") in calls                                          # 장면을 바꾸며 다시 켠 실시간 기록을 끈다


def test_imu1_runs_first_before_rail_prep(rig, monkeypatch):
    import imu1_run
    calls, _, data = rig
    monkeypatch.setattr(imu1_run, "drive", lambda rec, say, a: calls.append(("imu1", a.boots)) or 0)
    assert qc_bundle.main(["--tests", "FIRMWARE-1,IMU-1", "--boots", "2", "--steps", "500"]) == 0
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
    assert qc_bundle.main(["--tests", "IMU-2,FIRMWARE-1", "--n", "1", "--steps", "500"]) == 0
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


@pytest.mark.parametrize("tests", ["GNSS-1,IMU-2", "GNSS-1", "C", "A,B", "IMU-2,FIRMWARE-3", "FIRMWARE-2", "IMU-2,FIRMWARE-2"])
def test_refuses_before_touching_hardware(rig, tests):
    calls, _, _ = rig
    with pytest.raises(SystemExit):
        qc_bundle.main(["--tests", tests])
    assert calls == []


def test_missing_preset_or_scene_stops_before_hardware(rig):
    calls, _, _ = rig
    for argv in (["--tests", "FIRMWARE-1", "--steps", "700"], ["--tests", "GNSS-2", "--speeds", "200"]):
        with pytest.raises(SystemExit):
            qc_bundle.main(argv)
    for run, argv in ((gnss2_run, ["--speeds", "999"]), (firmware1_run, ["--accels", "700"])):
        with pytest.raises(SystemExit):
            run.main(argv)
    assert calls == []


def test_firmware1_steps_stop_when_follow_error_is_near_the_limit(rig, monkeypatch):
    """단계마다 추종 오차가 문턱의 90 % 를 넘으면 다음 단계로 가지 않는다 — 2026-11-12 첫 운전의 멈추는 기준."""
    calls, _, data = rig
    lim = firmware1_step_run.safety.ferr_limit(firmware1_step_run.FERR_V_MM_S)
    ferr = {"01_a500": 100, "02_a1000": lim}
    monkeypatch.setattr(firmware1_step_run, "motor_result",
                        lambda sub: {"exit": "ok", "alarms": [], "ferr_max_um": ferr[sub.name], "speed_max_mm_s": 900, "motor_json": None})
    assert firmware1_step_run.main(["--steps", "500,1000,2000", "--no-extract"]) == 1
    assert [c[4] for c in calls if c[0] == "rail"] == ["FIRMWARE-1_step-a500", "FIRMWARE-1_step-a1000"]
    s = _summary(data, "FIRMWARE-1_")
    assert [r["a_mm_s2"] for r in s["results"]] == [500, 1000] and s["ferr_limit_um"] == lim and s["steps_mm_s2"] == [500, 1000, 2000]


def test_old_firmware1_runner_still_drives_old_presets(rig):
    calls, _, _ = rig
    assert firmware1_run.main(["--n", "1", "--accels", "50", "--no-extract"]) == 0
    assert [c[4] for c in calls if c[0] == "rail"] == ["FIRMWARE-1_a50"]
    assert RC.MODULE["FIRMWARE-1"] == "firmware1_step_run"                     # 옛 실행기는 그 실행에서만 바꾼다


def test_gnss1_records_scene_pinned_window_and_holds_loop_lock(tmp_path, monkeypatch):
    """GNSS-1 은 혼자 돈다 — 수집 실행기 잠금 · 플러그 끔 · 장면 313분부터 · 감쇠 18 → 0 · 판정 구간 장면 321 ~ 346분."""
    calls, clock = [], {"t": 1000.0}
    monkeypatch.setattr(Q, "DATA", tmp_path / "data")
    monkeypatch.setattr(gnss1_run.time, "time", lambda: clock["t"])
    monkeypatch.setattr(gnss1_run.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))

    class Ls:
        def stop(self): calls.append("stop")
        def play(self, name, from_s=None): calls.append(("play", name, from_s)); return clock["t"]
        def status(self): return ("S01-Static-GLE-8h", 0)
        def _one(self, cmd): calls.append(cmd); return "OK"

    monkeypatch.setattr(Q, "LabSat", Ls)
    monkeypatch.setattr(Q, "hold_loop_lock", lambda: calls.append("lock") or object())
    monkeypatch.setattr(Q.charge, "plug_set", lambda on, say: calls.append(("plug", on)) or {"ok": True})
    monkeypatch.setattr(Q, "start_tap", lambda test, minutes, log: calls.append(("tap", test)) or "tap")
    monkeypatch.setattr(Q, "stop_tap", lambda tap: None)
    monkeypatch.setattr(Q, "qc_now", lambda: {"cells": {str(s): {"age_s": 1.0, "battery": 80} for s in range(10677, 10689)}})
    monkeypatch.setattr(Q, "reboot", lambda serials, say: calls.append(("reboot", len(serials))))
    monkeypatch.setattr(Q, "wait_lock", lambda say, need, max_s: calls.append(("lock_wait", need)) or True)
    monkeypatch.setattr(Q, "extract", lambda layout, say: tmp_path / "ftg_y_1")
    # 판정 구간 실시간 패킷 — 1행(10677 ~ 11619 …)과 3행 셀. 10683(3행)은 같은 행보다 5 dB 낮고, 10689 는 위치를 못 잡았다
    lay = Q.layout_columns("Y")
    sig = {s: {"cn0_sim": 40.0, "gps_rx": 100.0, "n": 1500} for s in lay[0] + lay[2]}
    sig[lay[2][0]] = {"cn0_sim": 35.0, "gps_rx": 100.0, "n": 1500}
    sig[lay[2][1]] = {"cn0_sim": None, "gps_rx": 0.0, "n": 1500}
    sig[lay[1][0]] = {"cn0_sim": 20.0, "gps_rx": 100.0, "n": 1500}            # 2행 — 꽂지 않은 행이라 거르지 않는다
    monkeypatch.setattr(gnss1_run, "round_items", lambda t0, t1: sig)
    assert gnss1_run.main(["2"]) == 0
    assert calls[:4] == ["lock", ("plug", False), "stop", ("play", "S01-Static-GLE-8h", 313 * 60)]
    assert "ATTN:18" in calls and calls.index("ATTN:18") < calls.index("ATTN:00") and ("tap", "GNSS-1") in calls
    (d,) = [p for p in (tmp_path / "data").iterdir() if p.is_dir()]
    assert d.name.startswith("GNSS-1_")
    s = json.loads((d / "r02" / "summary.json").read_text(encoding="utf-8"))
    assert s["test"] == "GNSS-1" and s["rows"] == [1, 3] and s["rec_scene_min"] == [321.0, 346.0] and s["attn_db"] == 18
    t0 = Q.time.mktime(Q.time.strptime(s["window"]["from"], "%Y-%m-%d %H:%M:%S"))
    t1 = Q.time.mktime(Q.time.strptime(s["window"]["to"], "%Y-%m-%d %H:%M:%S"))
    assert abs((t0 - s["t_play"]) - (321 - 313) * 60) <= 1 and abs((t1 - t0) - 25 * 60) <= 1     # 장면 시각으로 정한 구간
    # 1차 거르기: 회차 신호 세기를 기록하고, 꽂은 행만 거른다
    assert s["cn0"][str(lay[1][0])]["tested"] is False and str(lay[1][0]) not in s["gate"] and len(s["gate"]) == 12
    assert s["gate"][str(lay[2][0])]["status"] == "재측정" and s["gate"][str(lay[2][0])]["base_kind"] == "임시 기준"
    assert s["gate"][str(lay[2][1])]["status"] == "재측정" and "위치" in s["gate"][str(lay[2][1])]["why"]
    assert s["gate"][str(lay[0][0])]["status"] == "합격"
    gate = json.loads((tmp_path / "data" / "qc_gate_Y.json").read_text(encoding="utf-8"))
    assert gate["cells"][str(lay[2][0])]["GNSS-1"]["source"] == f"{d.name}/r02"
    assert len(gate["slot_cn0"]["감쇠18_행1,3"]) == 10                           # 걸리지 않은 10대가 칸 기준값 기록에 들어간다
