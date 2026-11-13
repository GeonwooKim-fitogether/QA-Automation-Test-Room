"""시험 실행기(tools/qc_tests/*_run.py)와 묶음 실행기(qc_bundle.py) — 모터 · LabSat · 셀 · 플러그 없이 차례와 기록 폴더만 본다.
2026-11-12 사용자 결정: 시험마다 실행기를 따로 두되, 함께 돌릴 때는 준비와 셀 기록 받기를 한 번만 한다.
같은 날 결정으로 시험이 여섯이 됐다 — 장면 동기 운전은 GNSS-MOVE, IMU-FW 은 가속도 단계 운전, GNSS-FW 는 보류."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

QT = Path(__file__).resolve().parents[1] / "tools" / "qc_tests"
sys.path.insert(0, str(QT))
import run_common as RC  # noqa: E402
import qc_bundle  # noqa: E402
import imu_fw_old_run  # noqa: E402
import imu_fw_run  # noqa: E402
import gnss_move_run  # noqa: E402
import gnss_static_run  # noqa: E402
from motionbench import qc_gate  # noqa: E402

Q = RC.Q


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """장비 대신 호출을 적어 두는 가짜. calls 에 차례대로 쌓인다."""
    calls = []
    pre = tmp_path / "presets"; pre.mkdir()
    for name in ("IMU-FW_a50", "IMU-FW_a500", "IMU-MOVE_long", "RUN-CONST_v300", "RUN-CONST_v100", "RUN-CONST_v500",
                 *(f"RUN-ACCEL_a{a}" for a in (500, 1000, 2000, 4000, 8000))):
        (pre / f"{name}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Q.run_rail, "PRESETS", pre)
    monkeypatch.setattr(Q, "DATA", tmp_path / "data")
    truth = tmp_path / "truth.csv"; truth.write_text("t_s,v_mm_s\n", encoding="utf-8")
    monkeypatch.setattr(Q, "CONST_SCENES", {v: (f"S_{v}", truth, f"RUN-CONST_v{v}") for v in (300, 100, 500)})
    monkeypatch.setattr(imu_fw_run, "motor_result",
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
    assert qc_bundle.main(["--tests", "IMU-FW,IMU-MOVE", "--n", "2", "--steps", "500,1000"]) == 0
    assert [c[0] for c in calls] == ["prep", "rail", "rail", "rail", "finish", "extract"]
    assert calls[0] == ("prep", ("IMU-MOVE", "IMU-FW"))                       # 주는 순서와 상관없이 운영 순서 — 센서 시험 IMU-MOVE 먼저
    assert [c[2:] for c in calls if c[0] == "rail"] == [
        (f"IMU-MOVE_{Q.time.strftime('%Y%m%d')}", "long", "IMU-MOVE_long"),
        (f"IMU-FW_{Q.time.strftime('%Y%m%d')}", "01_a500", "RUN-ACCEL_a500"),
        (f"IMU-FW_{Q.time.strftime('%Y%m%d')}", "02_a1000", "RUN-ACCEL_a1000")]
    f1, i2 = _summary(data, "IMU-FW_"), _summary(data, "IMU-MOVE_")
    assert f1["test"] == "IMU-FW" and i2["test"] == "IMU-MOVE" and f1["together"] == i2["together"] == ["IMU-MOVE", "IMU-FW"]
    assert f1["ftg"] == i2["ftg"] and f1["rc"] == i2["rc"] == 0 and f1["columns"] and f1["layout"] == "Y-shift"
    assert f1["run"] == "RUN-ACCEL" and f1["att"] == "ATT-0" and f1["dock"] == "DOCK-24" and f1["gps"] == "GPS-STILL"   # 조건 모듈 값
    assert all(i2[k] for k in ("run", "src", "sat", "gps", "att", "dock"))
    for d in data.iterdir():                                                    # 실행 기록은 두 폴더에 같은 줄로
        if d.name.startswith(("IMU-FW_", "IMU-MOVE_")):
            assert "함께 돌림" in (d / "run.log").read_text(encoding="utf-8")


def test_failed_drive_stops_later_tests_but_still_finishes_and_extracts(rig):
    calls, rcs, data = rig
    rcs["RUN-ACCEL_a500"] = 1
    assert qc_bundle.main(["--tests", "IMU-FW,IMU-MOVE,GNSS-MOVE"]) == 1
    assert [c[4] for c in calls if c[0] == "rail"] == ["IMU-MOVE_long", "RUN-ACCEL_a500"]   # 다음 단계 · GNSS-MOVE 는 돌지 않는다
    assert ("finish", "tap") in calls and calls[-1][0] == "extract"


def test_gnss2_goes_last_and_changes_scene_per_speed(rig):
    calls, _, _ = rig
    assert qc_bundle.main(["--tests", "GNSS-MOVE,IMU-MOVE", "--speeds", "100,500"]) == 0
    assert [c[1] for c in calls if c[0] == "rail"] == ["IMU-MOVE", "GNSS-MOVE", "GNSS-MOVE"]
    assert [c[1:] for c in calls if c[0] == "gps"] == [("S_100", "IMU-MOVE,GNSS-MOVE"), ("S_500", "IMU-MOVE,GNSS-MOVE")]
    assert ("finish", "tap2") in calls                                          # 장면을 바꾸며 다시 켠 실시간 기록을 끈다


def test_imu1_runs_first_before_rail_prep(rig, monkeypatch):
    import imu_static_run
    calls, _, data = rig
    monkeypatch.setattr(imu_static_run, "drive", lambda rec, say, a: calls.append(("imu1", a.boots)) or 0)
    assert qc_bundle.main(["--tests", "IMU-FW,IMU-STATIC", "--boots", "2", "--steps", "500"]) == 0
    assert [c[0] for c in calls] == ["imu1", "prep", "rail", "finish", "extract"] and calls[0] == ("imu1", 2)
    assert calls[1] == ("prep", ("IMU-FW",))                               # 레일 준비에는 레일 시험만
    assert _summary(data, "IMU-STATIC_")["ftg"] == _summary(data, "IMU-FW_")["ftg"]


def test_runner_drops_cells_failed_in_earlier_tests(rig):
    from motionbench import qc_gate
    calls, _, data = rig
    data.mkdir(parents=True, exist_ok=True)
    doc = {"cells": {}}
    qc_gate.record(doc, 10683, "GNSS-STATIC", qc_gate.FAIL, "GNSS-STATIC_20261112/r02", "두 번 걸림")
    qc_gate.record(doc, 10690, "IMU-MOVE", qc_gate.FAIL, "IMU-MOVE_20261112", "비 0.70")
    qc_gate.record(doc, 10691, "GNSS-FW", qc_gate.FAIL, "x", "뒤 시험 — 앞 시험이 아니다")
    qc_gate.save(qc_gate.path("Y", data), doc)
    assert qc_bundle.main(["--tests", "IMU-MOVE,IMU-FW", "--n", "1", "--steps", "500"]) == 0
    i2, f1 = _summary(data, "IMU-MOVE_"), _summary(data, "IMU-FW_")
    assert list(i2["excluded"]) == ["10683"]                                    # IMU-MOVE 앞은 GNSS-STATIC · IMU-STATIC
    assert sorted(f1["excluded"]) == ["10683", "10690"] and "IMU-MOVE 불합격" in f1["excluded"]["10690"]
    assert f1["gate_file"] == "qc_gate_Y.json" and f1["include_failed"] is False
    log = next(d for d in data.iterdir() if d.name.startswith("IMU-FW_")) / "run.log"
    assert "앞 시험 불합격 2대를 판정 대상에서 뺀다" in log.read_text(encoding="utf-8")


def test_include_failed_turns_the_gate_off(rig):
    from motionbench import qc_gate
    calls, _, data = rig
    data.mkdir(parents=True, exist_ok=True)
    doc = {"cells": {}}
    qc_gate.record(doc, 10683, "GNSS-STATIC", qc_gate.FAIL, "GNSS-STATIC_20261112/r02", "두 번 걸림")
    qc_gate.save(qc_gate.path("Y", data), doc)
    assert imu_fw_old_run.main(["--n", "1", "--accels", "50", "--include-failed", "--no-extract"]) == 0
    s = _summary(data, "IMU-FW_")
    assert s["excluded"] == {} and s["include_failed"] is True


def test_single_runner_uses_the_same_path(rig):
    calls, _, data = rig
    assert imu_fw_old_run.main(["--n", "1", "--accels", "500", "--no-extract"]) == 0
    assert [c[0] for c in calls] == ["prep", "rail", "finish"]
    s = _summary(data, "IMU-FW_")
    assert s["together"] == ["IMU-FW"] and s["ftg"] is None and s["a500"]["preset"] == "IMU-FW_a500"


@pytest.mark.parametrize("tests", ["GNSS-STATIC,IMU-MOVE", "GNSS-STATIC", "C", "A,B", "IMU-MOVE,FIRMWARE-3", "GNSS-FW", "IMU-MOVE,GNSS-FW"])
def test_refuses_before_touching_hardware(rig, tests):
    calls, _, _ = rig
    with pytest.raises(SystemExit):
        qc_bundle.main(["--tests", tests])
    assert calls == []


def test_missing_preset_or_scene_stops_before_hardware(rig):
    calls, _, _ = rig
    for argv in (["--tests", "IMU-FW", "--steps", "700"], ["--tests", "GNSS-MOVE", "--speeds", "200"]):
        with pytest.raises(SystemExit):
            qc_bundle.main(argv)
    for run, argv in ((gnss_move_run, ["--speeds", "999"]), (imu_fw_old_run, ["--accels", "700"])):
        with pytest.raises(SystemExit):
            run.main(argv)
    assert calls == []


def test_firmware1_steps_stop_when_follow_error_is_near_the_limit(rig, monkeypatch):
    """단계마다 추종 오차가 문턱의 90 % 를 넘으면 다음 단계로 가지 않는다 — 2026-11-12 첫 운전의 멈추는 기준."""
    calls, _, data = rig
    lim = imu_fw_run.safety.ferr_limit(imu_fw_run.FERR_V_MM_S)
    ferr = {"01_a500": 100, "02_a1000": lim}
    monkeypatch.setattr(imu_fw_run, "motor_result",
                        lambda sub: {"exit": "ok", "alarms": [], "ferr_max_um": ferr[sub.name], "speed_max_mm_s": 900, "motor_json": None})
    assert imu_fw_run.main(["--steps", "500,1000,2000", "--no-extract"]) == 1
    assert [c[4] for c in calls if c[0] == "rail"] == ["RUN-ACCEL_a500", "RUN-ACCEL_a1000"]
    s = _summary(data, "IMU-FW_")
    assert [r["a_mm_s2"] for r in s["results"]] == [500, 1000] and s["ferr_limit_um"] == lim and s["steps_mm_s2"] == [500, 1000, 2000]


def test_old_firmware1_runner_still_drives_old_presets(rig):
    calls, _, _ = rig
    assert imu_fw_old_run.main(["--n", "1", "--accels", "50", "--no-extract"]) == 0
    assert [c[4] for c in calls if c[0] == "rail"] == ["IMU-FW_a50"]
    assert RC.MODULE["IMU-FW"] == "imu_fw_run"                     # 옛 실행기는 그 실행에서만 바꾼다


def test_gnss1_records_scene_pinned_window_and_holds_loop_lock(tmp_path, monkeypatch):
    """GNSS-STATIC 은 혼자 돈다 — 수집 실행기 잠금 · 플러그 끔 · 장면 300분부터 · 감쇠 0 에서 독 셀 위치 잡기 → 18 dB → 0 · 판정 구간 장면 321 ~ 346분."""
    calls, clock = [], {"t": 1000.0}
    monkeypatch.setattr(Q, "DATA", tmp_path / "data")
    monkeypatch.setattr(gnss_static_run.time, "time", lambda: clock["t"])
    monkeypatch.setattr(gnss_static_run.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))

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
    eff = qc_gate.slot_table(qc_gate.criteria("GNSS-STATIC"), "감쇠18_행1,3")
    sig = {x: {"cn0_sim": 39.4 + eff[f"{c + 1}-{r + 1}"], "gps_rx": 100.0, "n": 1500} for c in (0, 2) for r, x in enumerate(lay[c])}
    sig[lay[2][0]] = {"cn0_sim": 39.4 + eff["3-1"] - 5.0, "gps_rx": 100.0, "n": 1500}
    sig[lay[2][1]] = {"cn0_sim": None, "gps_rx": 0.0, "n": 1500}
    sig[lay[1][0]] = {"cn0_sim": 20.0, "gps_rx": 100.0, "n": 1500}            # 2행 — 꽂지 않은 행이라 거르지 않는다
    monkeypatch.setattr(gnss_static_run, "round_items", lambda t0, t1: sig)
    assert gnss_static_run.main(["2"]) == 0
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
    assert d.name.startswith("GNSS-STATIC_")
    s = json.loads((d / "r02" / "summary.json").read_text(encoding="utf-8"))
    assert (s["run"], s["src"], s["sat"], s["gps"], s["att"], s["dock"]) == ("RUN-STILL", "SRC-LABSAT", "SAT-12", "GPS-STILL", "ATT-18", "DOCK-12")
    assert s["test"] == "GNSS-STATIC" and s["rows"] == [1, 3] and s["rec_scene_min"] == [321.0, 346.0] and s["attn_db"] == 18
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
    r = gate["cells"][str(lay[2][0])]["GNSS-STATIC"]
    assert r["source"] == f"{d.name}/r02" and r["rounds"][0]["cond"] == "감쇠18_행1,3" and r["rounds"][0]["level"] == 39.4
