"""레일 시험 반복 실행기 — 파트 C(레일 가속도 + 정지 장면) · 파트 B(레일 왕복 + 같은 경로 장면)를 n 번 돈다.

  python cell-bench/tools/run_rail.py C --n 10 --out cell-bench/data/collect_20261102/C1
  python cell-bench/tools/run_rail.py B --n 10 --out cell-bench/data/collect_20261102/B1

하기 전에(담당자 절차서 dynamic-bench/design/qc-operator-procedure.md):
  · live_tap 이 돌고 있어야 한다(셀 배터리 · 위치를 이 파일로 읽는다): python cell-bench/motionbench/live_tap.py --minutes 60 --qc-now --part C
  · 입회 표지(data/motion_attended)가 남은 시간 안이어야 한다 — 운전자가 현장에서 확인한 뒤에만 만든다.
  · 모터 값은 사용자가 승인한 프리셋(dynamic-bench/dev/presets/long-<파트>.json)만 쓴다. 실행기는 그 파일을
    dev/long-approval.json 으로 옮겨 놓을 뿐 값을 만들지 않는다.
파트 B 는 LabSat 에 장면 S03_RailB120 을 틀고, 재생 명령 뒤 2.75초(2026-10-30 실측) + 정지 20초에 레일이 출발하도록 모터를 띄운다.
끝나면 out/runs.json 에 실행마다 모터 기록 경로와 시각을 남긴다. 판정은 tools/protocol_v4.py 가 한다.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motionbench import charge  # noqa: E402

REPO = ROOT.parent
DATA = ROOT / "data"
DEV = REPO / "dynamic-bench" / "dev"
PRESETS = DEV / "presets"
APPROVAL = DEV / "long-approval.json"
MARKER = DATA / "motion_attended"
PY = sys.executable
SCENE_B, SKY_DELAY_S, STILL_S = "S03_RailB120", 2.75, 20.0


def say(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def marker_until() -> float:
    try:
        return float(json.loads(MARKER.read_text(encoding="utf-8"))["until"])
    except (OSError, ValueError, KeyError):
        return 0.0


FRESH_S = 3600          # 모터 안전 장치(safety.py): 서보를 켤 때 입회 표지의 created 가 60분 안이어야 한다
NEED_FILE = DATA / "attend_needed.json"


def marker_fresh_left() -> float:
    """지금부터 서보를 켤 수 있는 남은 초 — created + 60분과 until 중 이른 쪽까지."""
    try:
        m = json.loads(MARKER.read_text(encoding="utf-8"))
        return min(float(m["created"]) + FRESH_S, float(m["until"])) - time.time()
    except (OSError, ValueError, KeyError):
        return 0.0


def wait_marker(need_s: float = 30.0, poll_s: float = 5.0) -> None:
    """입회 표지가 need_s 초 이상 남아 있을 때까지 기다린다 — 라운드를 멈추지 않고, 운전자가 tools/attend.py 로 갱신하면 바로 이어 간다.
    기다리는 동안 data/attend_needed.json 을 남긴다(알림 · 결과판이 읽는다)."""
    if marker_fresh_left() >= need_s:
        return
    say("입회 표지 갱신 필요 — 레일 옆에서 tools/attend.py 로 현장 확인을 다시 한다(60분 규칙). 갱신되면 바로 이어 간다")
    NEED_FILE.write_text(json.dumps({"t": time.time(), "why": "입회 표지 60분 지남"}, ensure_ascii=False), encoding="utf-8")
    while marker_fresh_left() < need_s:
        time.sleep(poll_s)
    try:
        NEED_FILE.unlink()
    except OSError:
        pass
    say(f"입회 표지 갱신 확인 — {marker_fresh_left() / 60:.0f}분 남음, 이어 간다")


def install_preset(part: str, preset: str | None = None) -> dict:
    """사용자가 승인한 프리셋을 승인 파일 자리에 둔다. 프리셋이 없으면 멈춘다 — 값을 지어내지 않는다."""
    src = PRESETS / f"long-{preset or part}.json"
    if not src.exists():
        raise SystemExit(f"승인된 프리셋 {src} 이 없다 — 사용자 승인 뒤에 만든다")
    shutil.copyfile(src, APPROVAL)
    return json.loads(src.read_text(encoding="utf-8"))


def preflight(part: str, n: int, per_run_s: float) -> None:
    wait_drive()
    wait_marker(per_run_s)
    left = marker_fresh_left()
    low, nc, why = charge.rail_low()
    prob = charge.start_problem(low, why)
    if prob:
        raise SystemExit(prob)
    say(f"파트 {part} {n}번 시작 — 가장 낮은 셀 {low}% ({nc}대) · 입회 표지 {left / 60:.0f}분 남음")


def motor_once(log: Path) -> tuple[int, str, str | None, float]:
    t_cmd = time.time()
    with open(log, "w", encoding="utf-8") as lf:
        rc = subprocess.call([PY, "-X", "utf8", str(ROOT / "run_motion.py"), "long"], cwd=REPO, stdout=lf, stderr=subprocess.STDOUT)
    txt = log.read_text(encoding="utf-8", errors="replace")
    why = next((l.split("끝난 이유: ")[-1] for l in txt.splitlines() if "끝난 이유" in l), "?")
    csvs = [c for c in glob.glob(str(DATA / "motion_long_*.csv")) if not c.endswith("_trace.csv") and os.path.getmtime(c) >= t_cmd - 1]
    return rc, why, max(csvs, key=os.path.getmtime) if csvs else None, t_cmd


COMM_ERR = ("SDO", "PacketError", "슬레이브 수", "WKC", "어댑터를 열지 못함", "PDO", "SAFE-OP", "OP 전환", "응답 없음")
RETRY_WAIT_S = (10, 20, 30)     # 드라이브 통신 오류 재시도 간격 — 2026-11-02 17:55 SDO 실패 뒤 드라이브가 다시 뜨는 데 1분 남짓 걸렸다


def comm_error(why: str | None) -> bool:
    """드라이브 통신 오류인가 — 다시 붙으면 되는 실패. 안전 장치가 멈춘 것(입회 표지 · 추종 오차 · Quick stop · 경보)은 아니다."""
    return bool(why) and any(k in why for k in COMM_ERR) and "입회" not in why


def drive_ok(timeout_s: float = 60.0) -> tuple[bool, str]:
    """읽기 전용 점검(run_motion.py scan) — 드라이브 1대가 SAFE-OP 까지 올라오면 True."""
    try:
        subprocess.run([PY, "-X", "utf8", str(ROOT / "run_motion.py"), "scan"], cwd=REPO, capture_output=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False, f"점검이 {timeout_s:.0f}초 안에 끝나지 않음"
    try:
        d = json.loads((DATA / "drives.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "drives.json 을 읽지 못함"
    ok = d.get("slaves") == 1 and d.get("exit") == "시간 다 됨" and not d.get("emergencies")
    return ok, d.get("exit") or "?"


def wait_drive(poll_s: float = 30.0) -> None:
    """드라이브가 응답할 때까지 기다린다 — 라운드를 끝내지 않는다. 기다리는 동안 data/drive_needed.json 을 남긴다."""
    ok, why = drive_ok()
    if ok:
        return
    need = DATA / "drive_needed.json"
    say(f"모터 드라이브 응답 없음({why}) — 드라이브 표시등 · 전원 · 모터 쪽 USB 랜을 본다. 돌아오면 바로 이어 간다")
    need.write_text(json.dumps({"t": time.time(), "why": why}, ensure_ascii=False), encoding="utf-8")
    while not ok:
        time.sleep(poll_s)
        ok, why = drive_ok()
    try:
        need.unlink()
    except OSError:
        pass
    say("모터 드라이브 응답 확인 — 이어 간다")


def first_move(csv_path: str | None, thr_um: int = 500) -> float | None:
    if not csv_path:
        return None
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    p0 = int(rows[0]["pos"])
    return next((float(r["t_wall"]) for r in rows if abs(int(r["pos"]) - p0) > thr_um), None)


def run_c(n: int, out: Path, runs: list) -> int:
    done, retries = 0, 0
    while done < n:
        i = len(runs) + 1
        if charge.should_stop(charge.rail_low()[0]):
            say("가장 낮은 셀이 멈춤 기준 이하 — 충전부터"); return 2
        wait_marker(30)
        rc, why, csvp, t_cmd = motor_once(out / f"run{i}_motor.log")
        runs.append({"i": i, "t_motor_cmd": t_cmd, "rc": rc, "why": why, "motor_csv": csvp})
        (out / "runs.json").write_text(json.dumps(runs, ensure_ascii=False, indent=1), encoding="utf-8")
        say(f"C {i}번째 끝 · 코드 {rc} · {why}")
        if rc == 0:
            done, retries = done + 1, 0
        elif comm_error(why) and retries < len(RETRY_WAIT_S):
            say(f"드라이브 통신 오류 — {RETRY_WAIT_S[retries]}초 뒤 다시 붙는다({retries + 1}/{len(RETRY_WAIT_S)})")
            time.sleep(RETRY_WAIT_S[retries]); retries += 1
            wait_drive()
            continue
        else:
            say("정상으로 끝나지 않아 멈춘다"); return 1
        time.sleep(5)
    return 0


def run_b(n: int, out: Path, runs: list, lead: float, scene: str = SCENE_B) -> int:
    from motionbench.labsat import LabSat
    ls = LabSat()
    done, retries = 0, 0
    while done < n:
        i = len(runs) + 1
        if charge.should_stop(charge.rail_low()[0]):
            say("가장 낮은 셀이 멈춤 기준 이하 — 충전부터"); return 2
        wait_marker(60)
        ls.stop(); time.sleep(2)
        t_play = ls.play(scene)
        t_go = t_play + SKY_DELAY_S + STILL_S - lead
        say(f"B {i}번째 — 재생 시작 · 모터는 {t_go - time.time():.1f}초 뒤 (lead {lead:.2f})")
        while time.time() < t_go:
            time.sleep(0.005)
        rc, why, csvp, t_cmd = motor_once(out / f"run{i}_motor.log")
        try:
            ls.stop()
        except Exception as e:  # 재생 멈춤이 실패해도 다음 재생 명령이 처음부터 다시 튼다
            say(f"PLAY:STOP 실패 {e}")
        fm = first_move(csvp)
        off = None if fm is None else round(fm - (t_play + SKY_DELAY_S + STILL_S), 3)
        runs.append({"i": i, "scene": scene, "t_play": t_play, "t_motor_cmd": t_cmd, "t_first_move": fm, "rail_minus_sky_s": off,
                     "lead_used": lead, "sky_delay_used": SKY_DELAY_S, "rc": rc, "why": why, "motor_csv": csvp})
        (out / "runs.json").write_text(json.dumps(runs, ensure_ascii=False, indent=1), encoding="utf-8")
        say(f"B {i}번째 끝 · 코드 {rc} · {why} · 레일 출발 − 하늘 출발 {off}초")
        if rc != 0:
            if comm_error(why) and retries < len(RETRY_WAIT_S):
                say(f"드라이브 통신 오류 — {RETRY_WAIT_S[retries]}초 뒤 다시 붙는다({retries + 1}/{len(RETRY_WAIT_S)})")
                time.sleep(RETRY_WAIT_S[retries]); retries += 1
                wait_drive()
                continue
            say("정상으로 끝나지 않아 멈춘다"); return 1
        done, retries = done + 1, 0
        if fm:
            lead = fm - t_cmd
        time.sleep(5)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("part", choices=["C", "B"])
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lead", type=float, default=5.3, help="파트 B — 모터 명령에서 첫 움직임까지 첫 추정(초). 둘째 번부터는 앞 번 실측")
    ap.add_argument("--scene", default=SCENE_B, help="파트 B 의 LabSat 장면 이름 (기본 S03_RailB120 = 300 mm/s)")
    ap.add_argument("--preset", help="승인 프리셋 이름(long-<이름>.json). 기본은 파트 이름 — B100 · B500 처럼 속도별 프리셋")
    a = ap.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    preset = install_preset(a.part, a.preset)
    per = 110.0 if a.part == "B" else 95.0
    preflight(a.part, a.n, per)
    (out / "preset.json").write_text(json.dumps(preset, ensure_ascii=False, indent=1), encoding="utf-8")
    rp = out / "runs.json"
    runs = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else []
    rc = run_c(a.n, out, runs) if a.part == "C" else run_b(a.n, out, runs, a.lead, a.scene)
    say(f"끝 → {rp}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
