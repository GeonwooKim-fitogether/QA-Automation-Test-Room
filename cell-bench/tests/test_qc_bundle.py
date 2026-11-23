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
import imu_move_run  # noqa: E402
import gnss_move_run  # noqa: E402
import gnss_static_run  # noqa: E402
from motionbench import qc_gate  # noqa: E402

Q = RC.Q


@pytest.fixture(autouse=True)
def no_loop_lock(monkeypatch):
    """레일 실행기가 쥐는 수집 실행기 잠금(실제 파일) 대신 가짜 — 시험이 잠금 파일을 남기거나 서로 막지 않게. 테스트가 다시 바꿔 끼울 수 있다."""
    monkeypatch.setattr(Q, "hold_loop_lock", lambda: object())


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """장비 대신 호출을 적어 두는 가짜. calls 에 차례대로 쌓인다."""
    calls = []
    pre = tmp_path / "presets"; pre.mkdir()
    for name in ("IMU-FW_a50", "IMU-FW_a500", "IMU-MOVE_long", *(f"RUN-CONST_v{v}a8000" for v in Q.CONST_SPEEDS),
                 *(f"RUN-ACCEL_a{a}" for a in (500, 1000, 2000, 4000, 8000))):
        (pre / f"{name}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Q.run_rail, "PRESETS", pre)
    monkeypatch.setattr(Q, "DATA", tmp_path / "data")
    truth = tmp_path / "truth.csv"; truth.write_text("t_s,v_mm_s\n", encoding="utf-8")
    monkeypatch.setattr(Q, "CONST_SCENES", {v: (f"S_{v}", truth, f"RUN-CONST_v{v}a8000") for v in Q.CONST_SPEEDS})
    monkeypatch.setattr(imu_fw_run, "motor_result",
                        lambda sub: {"exit": "ok", "alarms": [], "ferr_max_um": 100, "speed_max_mm_s": 900, "motor_json": None})
    monkeypatch.setattr(RC.time, "sleep", lambda s: None)
    monkeypatch.setattr(imu_move_run, "stage_result",
                        lambda sub: {"runs": 1, "ok": 1, "ferr_max_um": 100, "speed_max_mm_s": 900, "alarms": [], "exits": ["ok"]})
    monkeypatch.setattr(Q, "rail_prep", lambda tests, minutes, out, say, **kw: (calls.append(
        ("prep", tuple(tests)) + ((kw["expect"],) if "expect" in kw else ()) + ((kw["labsat"],) if "labsat" in kw else ())),
        ("ls", "tap", {"ok": True}))[1])
    monkeypatch.setattr(Q, "alive_cells", lambda: [])
    # 저장량 확인(셀에 상태를 묻는다)은 calls 에 적지 않는다 — 차례는 아래 test_storage_check_runs_first_* 가 따로 본다
    monkeypatch.setattr(Q, "storage_ready", lambda layout, say: {"warn_mb": 120.0, "mb": {"10680": 12.0}, "no_live": [], "cleared": [], "ftg": None})
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
    assert [c[0] for c in calls] == ["prep", *["rail"] * 7, "finish", "extract"]
    assert calls[0] == ("prep", ("IMU-MOVE", "IMU-FW"), 12)                   # 주는 순서와 상관없이 운영 순서 — 센서 시험 IMU-MOVE 먼저, 배치-12
    day = Q.time.strftime('%Y%m%d')
    assert [c[2:] for c in calls if c[0] == "rail"] == [
        *[(f"IMU-MOVE_{day}", f"{i:02d}_a{a}", f"RUN-ACCEL_a{a}") for i, a in enumerate((500, 1000, 2000, 4000, 8000), 1)],
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
    rcs["RUN-ACCEL_a1000"] = 1
    assert qc_bundle.main(["--tests", "IMU-FW,IMU-MOVE,GNSS-MOVE"]) == 1
    assert [c[4] for c in calls if c[0] == "rail"] == ["RUN-ACCEL_a500", "RUN-ACCEL_a1000"]   # IMU-MOVE 다음 단계 · IMU-FW · GNSS-MOVE 는 돌지 않는다
    assert ("finish", "tap") in calls and calls[-1][0] == "extract"


def test_gnss_move_goes_last_and_changes_scene_per_speed(rig):
    calls, _, data = rig
    assert qc_bundle.main(["--tests", "GNSS-MOVE,IMU-MOVE", "--speeds", "100,500"]) == 0
    assert [c[1] for c in calls if c[0] == "rail"] == ["IMU-MOVE"] * 5 + ["GNSS-MOVE", "GNSS-MOVE"]
    assert [c[1:] for c in calls if c[0] == "gps"] == [("S_100", "IMU-MOVE,GNSS-MOVE"), ("S_500", "IMU-MOVE,GNSS-MOVE")]
    assert ("finish", "tap2") in calls                                          # 장면을 바꾸며 다시 켠 실시간 기록을 끈다
    s = _summary(data, "GNSS-MOVE_")
    assert s["run"] == "RUN-CONST" and s["gps"] == "GPS-SYNC" and s["sat"] == "SAT-12" and s["dock"] == "DOCK-24"   # --rows 없으면 독 전체
    assert s["v100"]["scene"] == "S_100" and s["v500"]["scene"] == "S_500"                                     # 속도마다 쓴 장면은 따로 남는다


def test_gnss_move_first_check_one_speed_one_run(rig, monkeypatch):
    """첫 운전 확인 — 속도 하나 · 1번만. 새 프리셋 · 장면으로, 배치는 --rows 1,3 이면 DOCK-12 로 적는다."""
    import gnss_move_run
    calls, _, data = rig
    seen = []
    monkeypatch.setattr(Q.run_rail, "main", lambda argv: seen.append(argv) or 0)
    assert gnss_move_run.main(["--speeds", "300", "--n", "1", "--rows", "1,3", "--no-extract"]) == 0
    (argv,) = seen
    assert argv[argv.index("--n") + 1] == "1" and argv[argv.index("--preset") + 1] == "RUN-CONST_v300a8000"
    assert argv[argv.index("--scene") + 1] == "S_300" and [c[1] for c in calls if c[0] == "gps"] == ["S_300"]
    assert ("prep", ("GNSS-MOVE",), 12) in calls                           # 배치-12 — 레일 준비가 12대를 확인한다
    s = _summary(data, "GNSS-MOVE_")
    assert (s["sat"], s["dock"], s["gps"], s["att"]) == ("SAT-12", "DOCK-12", "GPS-SYNC", "ATT-0")
    assert not any(c[0] == "extract" for c in calls)


def test_imu_static_runs_first_before_rail_prep(rig, monkeypatch):
    import imu_static_run
    calls, _, data = rig
    monkeypatch.setattr(imu_static_run, "drive", lambda rec, say, a: calls.append(("imu1", a.boots)) or 0)
    assert qc_bundle.main(["--tests", "IMU-FW,IMU-STATIC", "--boots", "2", "--steps", "500"]) == 0
    assert [c[0] for c in calls] == ["imu1", "prep", "rail", "finish", "extract"] and calls[0] == ("imu1", 2)
    assert calls[1] == ("prep", ("IMU-FW",))                               # 레일 준비에는 레일 시험만
    assert _summary(data, "IMU-STATIC_")["ftg"] == _summary(data, "IMU-FW_")["ftg"]


def test_storage_check_runs_first_even_with_no_extract(rig, monkeypatch):
    """저장량 확인은 첫 시험(IMU-STATIC)과 레일 준비보다 먼저 한 번 — --no-extract 여도 한다. 결과는 모든 폴더의 summary.json 에(2026-11-23)."""
    import imu_static_run
    calls, _, data = rig
    monkeypatch.setattr(Q, "storage_ready", lambda layout, say: calls.append(("storage", layout)) or {
        "warn_mb": 120.0, "mb": {"10680": 130.0}, "no_live": [], "cleared": [10680], "ftg": "ftg_y_pre"})
    monkeypatch.setattr(imu_static_run, "drive", lambda rec, say, a: calls.append(("imu1", a.boots)) or 0)
    assert qc_bundle.main(["--tests", "IMU-FW,IMU-STATIC", "--boots", "1", "--steps", "500", "--no-extract"]) == 0
    assert [c[0] for c in calls] == ["storage", "imu1", "prep", "rail", "finish"] and calls[0] == ("storage", "Y-shift")
    assert _summary(data, "IMU-STATIC_")["storage"]["cleared"] == [10680] == _summary(data, "IMU-FW_")["storage"]["cleared"]


def test_storage_check_that_stops_leaves_the_rig_untouched(rig, monkeypatch):
    """받고 비운 뒤에도 남은 셀이 있으면 레일 준비(플러그 · LabSat) · 운전 · 받기 없이 멈추고, 이유가 실행 기록에 남는다."""
    calls, _, data = rig

    def left(layout, say):
        say("저장량 확인 — 받고 비운 뒤에도 기록이 남은 셀 [10692] — 시험을 시작하지 않는다")
        raise SystemExit("남음")

    monkeypatch.setattr(Q, "storage_ready", left)
    with pytest.raises(SystemExit):
        qc_bundle.main(["--tests", "IMU-MOVE,IMU-FW", "--n", "1", "--steps", "500"])
    assert calls == []
    log = next(d for d in data.iterdir() if d.name.startswith("IMU-MOVE_")) / "run.log"
    assert "기록이 남은 셀 [10692]" in log.read_text(encoding="utf-8")


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


def test_imu_fw_steps_stop_when_follow_error_is_near_the_limit(rig, monkeypatch):
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


def test_old_imu_fw_runner_still_drives_old_presets(rig):
    calls, _, _ = rig
    assert imu_fw_old_run.main(["--n", "1", "--accels", "50", "--no-extract"]) == 0
    assert [c[4] for c in calls if c[0] == "rail"] == ["IMU-FW_a50"]
    assert RC.MODULE["IMU-FW"] == "imu_fw_run"                     # 옛 실행기는 그 실행에서만 바꾼다


def test_gnss_static_records_scene_pinned_window_and_holds_loop_lock(tmp_path, monkeypatch):
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
    monkeypatch.setattr(Q, "storage_ready", lambda layout, say: calls.append("storage") or {"mb": {}, "cleared": []})
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
    # 저장량 확인은 잠금 뒤 · 플러그 끄기와 장면 재생 전 — 받기에 걸린 시간이 판정 구간까지의 시간을 먹지 않게(2026-11-23)
    assert calls[:5] == ["lock", "storage", ("plug", False), "stop", ("play", "S01-Static-GLE-8h", 300 * 60)]
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
    assert (s["run"], s["src"], s["gps"], s["att"], s["dock"]) == ("RUN-STILL", "SRC-LABSAT", "GPS-STILL", "ATT-18", "DOCK-12")
    assert s["sat"].startswith("S01-Static-GLE-8h 300분부터 틀고 321 ~ 346분 기록") and s["sat"].endswith("SAT-12)")   # 목표값이 아니라 실제 장면 · 구간
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


# ---------------------------------------------------------------- IMU-MOVE 운전-가속 5단계 (2026-11-20 합의 절차)

def test_imu_move_drives_five_accel_steps_on_the_repeater(rig):
    """올리기만 0.5 → 8 m/s² · 단계마다 n 번 · 배치-12 · --src repeater 면 레일 준비가 LabSat 을 건드리지 않는다."""
    calls, _, data = rig
    assert imu_move_run.main(["--src", "repeater", "--n", "1", "--no-extract"]) == 0
    assert calls[0] == ("prep", ("IMU-MOVE",), 12, False)
    assert [c[3:] for c in calls if c[0] == "rail"] == [(f"{i:02d}_a{a}", f"RUN-ACCEL_a{a}") for i, a in enumerate((500, 1000, 2000, 4000, 8000), 1)]
    s = _summary(data, "IMU-MOVE_")
    assert (s["run"], s["src"], s["gps"], s["att"], s["dock"]) == ("RUN-ACCEL", "SRC-REPEATER", "GPS-STILL", "ATT-NONE", "DOCK-12")
    assert s["steps_mm_s2"] == [500, 1000, 2000, 4000, 8000] and s["n_per_step"] == 1 and len(s["results"]) == 5


def test_imu_move_labsat_is_the_default(rig):
    calls, _, data = rig
    assert imu_move_run.main(["--n", "1", "--move-steps", "500", "--no-extract"]) == 0
    assert calls[0] == ("prep", ("IMU-MOVE",), 12)                          # labsat 을 넘기지 않는다 — 레일 준비 기본(정지 장면 · 감쇠 0)
    assert _summary(data, "IMU-MOVE_")["src"] == "SRC-LABSAT"


def test_imu_move_stops_when_follow_error_is_near_the_limit(rig, monkeypatch):
    calls, _, data = rig
    lim = imu_move_run.safety.ferr_limit(imu_move_run.FERR_V_MM_S)
    ferr = {"01_a500": 100, "02_a1000": lim}
    monkeypatch.setattr(imu_move_run, "stage_result",
                        lambda sub: {"runs": 1, "ok": 1, "ferr_max_um": ferr[sub.name], "speed_max_mm_s": 900, "alarms": [], "exits": ["ok"]})
    assert imu_move_run.main(["--n", "1", "--no-extract"]) == 1
    assert [c[4] for c in calls if c[0] == "rail"] == ["RUN-ACCEL_a500", "RUN-ACCEL_a1000"]


def test_imu_move_stage_result_reads_every_run(tmp_path):
    """단계 요약은 첫 실행만이 아니라 모든 실행의 모터 기록에서 — 추종 오차 · 최고 속도는 최댓값, 정상 횟수는 rc 0 인 실행 수."""
    runs = []
    for i, (f, v, rc) in enumerate(((100, 900, 0), (300, 950, 0), (200, 1000, 1)), 1):
        m = tmp_path / f"m{i}.json"
        m.write_text(json.dumps({"legs": [{"ferr_max": -f, "speed_max_mm_s": v}], "exit": "ok",
                                 "alarms": [] if rc == 0 else ["Err80.4"]}), encoding="utf-8")
        runs.append({"i": i, "rc": rc, "motor_csv": str(m.with_suffix(".csv"))})
    (tmp_path / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
    r = imu_move_run.stage_result(tmp_path)
    assert (r["runs"], r["ok"], r["ferr_max_um"], r["speed_max_mm_s"], r["alarms"]) == (3, 2, 300, 1000, [["Err80.4"]])


def test_repeater_refuses_rail_tests_that_need_labsat(monkeypatch, tmp_path):
    monkeypatch.setattr(Q.drive_lock, "take_round", lambda who: pytest.fail("거절할 것인데 레일 잠금을 쥐었다"))
    with pytest.raises(SystemExit, match="IMU-MOVE 만"):
        Q.rail_prep(["IMU-MOVE", "IMU-FW"], 10, tmp_path, print, labsat=False)


def test_rail_prep_on_the_repeater_never_touches_labsat(monkeypatch, tmp_path):
    seen = []

    class Held:
        def release(self): seen.append("release")

    monkeypatch.setattr(Q.drive_lock, "take_round", lambda who: Held())
    monkeypatch.setattr(Q.charge, "plug_set", lambda on, say: seen.append(("plug", on)) or {"ok": True})
    monkeypatch.setattr(Q, "LabSat", lambda *a, **k: pytest.fail("중계기 날인데 LabSat 에 붙었다"))
    monkeypatch.setattr(Q, "lock_ready", lambda test, minutes, out, say, need: seen.append(("lock", need)) or "tap")
    monkeypatch.setattr(Q, "dock_count_ok", lambda expect, say: seen.append(("dock", expect)))
    monkeypatch.setattr(Q, "battery_ok", lambda say: seen.append("batt"))
    monkeypatch.setattr(Q, "stop_tap", lambda tap: seen.append(("stop", tap)))
    ls, tap, plug = Q.rail_prep(["IMU-MOVE"], 10, tmp_path, print, expect=12, labsat=False)
    assert ls is None and tap == "tap"
    Q.rail_finish(ls, tap, print)
    assert seen == [("plug", False), ("lock", 12), ("dock", 12), "batt", ("stop", "tap"), "release"]
