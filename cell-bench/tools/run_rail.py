"""레일 운전 반복기 — 시험 하나의 레일 운전을 승인 프리셋으로 n 번 돈다. 시험 ID 는 dynamic-bench/design/qc-tests.md.

  python cell-bench/tools/run_rail.py IMU-FW --n 10 --out cell-bench/data/IMU-FW_20261112/a50 --preset IMU-FW_a50
  python cell-bench/tools/run_rail.py IMU-MOVE      --n 10 --out cell-bench/data/IMU-MOVE_20261112/long    --preset IMU-MOVE_long
  python cell-bench/tools/run_rail.py GNSS-MOVE     --n 10 --out cell-bench/data/GNSS-MOVE_20261113/v300     --scene S07_Move300 --preset RUN-CONST_v300a8000
  python cell-bench/tools/run_rail.py cap        --n 1  --out ... --preset cap-d1400a1000      (상한 시험 — QC 시험이 아니다)

보통은 직접 부르지 않고 시험 실행기(tools/qc_tests/*_run.py)나 묶음 실행기(tools/qc_tests/qc_bundle.py)가 부른다.
하기 전에(담당자 절차서 dynamic-bench/design/qc-operator-procedure.md):
  · live_tap 이 돌고 있어야 한다(셀 배터리 · 위치를 이 파일로 읽는다): python cell-bench/motionbench/live_tap.py --minutes 60 --qc-now --test IMU-FW
  · 입회 표지(data/motion_attended)가 남은 시간 안이어야 한다 — 운전자가 현장에서 확인한 뒤에만 만든다.
  · 모터 값은 사용자가 승인한 프리셋(dynamic-bench/dev/presets/<프리셋>.json)만 쓴다. 실행기는 그 파일을
    dev/long-approval.json 으로 옮겨 놓을 뿐 값을 만들지 않는다.
GNSS-MOVE 는 LabSat 에 레일과 같은 속도로 움직이는 장면(기본 S07_Move300 = 300 mm/s, 위성-12)을 틀고, 재생 명령 뒤 2.75초(2026-10-30 실측)
+ 정지 20초에 레일이 출발하도록 모터를 띄운다. 나머지(IMU-MOVE · IMU-FW · cap)는 장면을 건드리지 않고 모터만 띄운다.
끝나면 out/runs.json 에 실행마다 모터 기록 경로와 시각을 남긴다. 판정은 tools/protocol_v4.py 와 tools/qc_tests/*_judge.py 가 한다.
도는 내내 PC 전역 레일 라운드 잠금(motionbench/drive_lock.py)을 쥔다 — 운전 사이 틈(드라이브 점검 · 장면 다시 틀기)에도 다른 체크아웃의
프로그램이 드라이브에 붙지 못한다. 띄운 run_motion.py 는 표를 물려받아 그 라운드 안에서 돈다. 이미 누가 쥐고 있으면 시작하지 않는다.
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
from motionbench import charge, drive_lock, qc_ids, wifi  # noqa: E402
from cellbench.control import live_dir  # noqa: E402

REPO = ROOT.parent
DATA = ROOT / "data"
DEV = REPO / "dynamic-bench" / "dev"
PRESETS = DEV / "presets"
APPROVAL = DEV / "long-approval.json"
MARKER = DATA / "motion_attended"
PY = sys.executable
SCENE_SYNC, SKY_DELAY_S, STILL_S = "S07_Move300", 2.75, 20.0      # 장면 이름은 LabSat 안의 파일 이름이라 바꾸지 않는다(위성-6 시절 기본은 S03_RailB120)
CAP = "cap"          # 상한 시험(tools/qc_tests/accel_step_run.py) — QC 시험이 아니다. 모터만 띄운다
TESTS = (*qc_ids.RAIL, CAP)
DEFAULT_PRESET = {"IMU-MOVE": "IMU-MOVE_long", "IMU-FW": "IMU-FW_a50", "GNSS-MOVE": "RUN-CONST_v300a8000"}


def say(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


LINK_WAIT_S = 300       # 셀이 하나도 안 들릴 때 와이파이를 다시 붙이며 기다리는 시간
STUCK = 3               # run_scene_sync 가 위치를 못 잡고 남은 셀을 보고 돌려주는 코드 — 실행기(gnss_move_run.py)가 재부팅하고 이어 간다


def live_cells() -> dict[int, dict] | None:
    """실시간 화면 요약(qc_now.json)에서 지금(10초 안) 들리는 셀 {시리얼: 칸}.
    실시간 기록이 꺼져 있으면(파일 없음 · 셀 칸 없음) None — 들리는지 모르는 것과 하나도 안 들리는 것을 가른다."""
    try:
        c = json.loads((live_dir(DATA) / "qc_now.json").read_text(encoding="utf-8")).get("cells")
    except (OSError, ValueError):
        return None
    if c is None:
        return None
    return {int(k): x for k, x in c.items() if x.get("age_s") is not None and x["age_s"] < 10}


def unlocked_now() -> set[int]:
    """들리는데 위치를 못 잡은 셀."""
    return {s for s, x in (live_cells() or {}).items() if x.get("fix") not in ("A", "D", "F", "R")}


def ensure_link() -> bool:
    """셀이 하나도 안 들리면 와이파이를 다시 붙이며 LINK_WAIT_S 까지 기다린다 — 다시 들리면 True.
    2026-11-19 12:40 PC 와이파이가 드라이버에 의해 끊겼는데 실행기가 '셀 잔량을 못 읽음'으로 멈췄고, 사람이 13:13 에 다시 붙였다.
    붙일 네트워크는 data/livehub.json 의 ssid 다(motionbench/wifi.py — 끊긴 연결을 같은 프로필로 다시 붙는 것만 한다)."""
    if live_cells() is None or live_cells():      # 실시간 기록이 꺼져 있으면 알 수 없으니 막지 않는다(셀 잔량 확인이 따로 막는다)
        return True
    t0 = time.time()
    say("셀이 하나도 안 들린다 — 와이파이를 확인하고 다시 붙인다")
    while time.time() - t0 < LINK_WAIT_S:
        r = wifi.ensure(say)
        if r.get("why"):
            say(r["why"])
        time.sleep(10)
        if live_cells():
            say(f"셀이 다시 들린다 ({time.time() - t0:.0f}초) — 이어 간다")
            return True
    say(f"{LINK_WAIT_S / 60:.0f}분 지나도 셀이 안 들린다 — 멈춘다")
    return False


def marker_until() -> float:
    try:
        return float(json.loads(MARKER.read_text(encoding="utf-8"))["until"])
    except (OSError, ValueError, KeyError):
        return 0.0


FRESH_S = 10 * 3600     # 모터 안전 장치(safety.py): 서보를 켤 때 입회 표지의 created 가 10시간 안이어야 한다
NEED_FILE = DATA / "attend_needed.json"


def marker_fresh_left() -> float:
    """지금부터 서보를 켤 수 있는 남은 초 — created + 10시간과 until 중 이른 쪽까지."""
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
    say("입회 표지 갱신 필요 — 레일 옆에서 tools/attend.py 로 현장 확인을 다시 한다(10시간 규칙). 갱신되면 바로 이어 간다")
    NEED_FILE.write_text(json.dumps({"t": time.time(), "why": "입회 표지 10시간 지남"}, ensure_ascii=False), encoding="utf-8")
    while marker_fresh_left() < need_s:
        time.sleep(poll_s)
    try:
        NEED_FILE.unlink()
    except OSError:
        pass
    say(f"입회 표지 갱신 확인 — {marker_fresh_left() / 60:.0f}분 남음, 이어 간다")


def install_preset(test: str, preset: str | None = None) -> dict:
    """사용자가 승인한 프리셋(presets/<이름>.json)을 승인 파일 자리에 둔다. 프리셋이 없으면 멈춘다 — 값을 지어내지 않는다.
    이름을 주지 않으면 시험의 기본 프리셋(DEFAULT_PRESET)을 쓴다. 상한 시험(cap)은 기본이 없어 이름을 꼭 준다."""
    name = preset or DEFAULT_PRESET.get(test)
    if not name:
        raise SystemExit(f"{test} 는 기본 프리셋이 없다 — --preset 으로 이름을 준다")
    src = PRESETS / f"{name}.json"
    if not src.exists():
        raise SystemExit(f"승인된 프리셋 {src} 이 없다 — 사용자 승인 뒤에 만든다")
    shutil.copyfile(src, APPROVAL)
    return json.loads(src.read_text(encoding="utf-8"))


def preflight(test: str, n: int, per_run_s: float) -> None:
    wait_drive()
    wait_marker(per_run_s)
    left = marker_fresh_left()
    low, nc, why = charge.rail_low()
    prob = charge.start_problem(low, why)
    if prob:
        raise SystemExit(prob)
    say(f"{test} {n}번 시작 — 가장 낮은 셀 {low}% ({nc}대) · 입회 표지 {left / 60:.0f}분 남음")


LOCK_BUSY = drive_lock.BUSY     # "다른 모션 엔진이 이미 돈다"


def motor_once(log: Path, lock_tries: int = 24) -> tuple[int, str, str | None, float]:
    """run_motion.py long 을 한 번 띄운다. 모션 잠금을 다른 프로그램이 잠깐 쥐고 있으면(충전 유지의 플러그 호출 — 2026-11-02 20:29 실측)
    5초 간격으로 lock_tries 번 다시 띄운다. 시험 중(RAIL_NO_HARDWARE)이면 띄우지 않는다."""
    why = drive_lock.blocked_reason()
    if why:
        return 1, why, None, time.time()
    for k in range(lock_tries):
        t_cmd = time.time()
        with open(log, "w", encoding="utf-8") as lf:
            rc = subprocess.call([PY, "-X", "utf8", str(ROOT / "run_motion.py"), "long"], cwd=REPO, stdout=lf, stderr=subprocess.STDOUT)
        txt = log.read_text(encoding="utf-8", errors="replace")
        if LOCK_BUSY in txt and k + 1 < lock_tries:
            say(f"모션 잠금이 잡혀 있다 — 5초 뒤 다시 띄운다({k + 1}/{lock_tries})")
            time.sleep(5)
            continue
        break
    why = next((l.split("끝난 이유: ")[-1] for l in txt.splitlines() if "끝난 이유" in l), LOCK_BUSY if LOCK_BUSY in txt else "?")
    csvs = [c for c in glob.glob(str(DATA / "motion_long_*.csv")) if not c.endswith("_trace.csv") and os.path.getmtime(c) >= t_cmd - 1]
    return rc, why, max(csvs, key=os.path.getmtime) if csvs else None, t_cmd


COMM_ERR = ("SDO", "PacketError", "슬레이브 수", "WKC", "어댑터를 열지 못함", "PDO", "SAFE-OP", "OP 전환", "응답 없음")
RETRY_WAIT_S = (10, 20, 30)     # 드라이브 통신 오류 재시도 간격 — 2026-11-02 17:55 SDO 실패 뒤 드라이브가 다시 뜨는 데 1분 남짓 걸렸다


def comm_error(why: str | None) -> bool:
    """다시 띄우면 되는 실패인가 — 드라이브 통신 오류, 또는 PC 주기 지연으로 건 Quick stop(기계는 멀쩡, 2026-11-02 21:33 실측).
    그 밖의 안전 정지(입회 표지 · 추종 오차 · 끝 터치 · 경보)는 아니다."""
    return bool(why) and (any(k in why for k in COMM_ERR) or "주기 지연" in why) and "입회" not in why


def drive_ok(timeout_s: float = 60.0) -> tuple[bool, str]:
    """읽기 전용 점검(run_motion.py scan) — 드라이브 1대가 SAFE-OP 까지 올라오면 True.
    scan 이 잠금에 막혀 drives.json 을 쓰지 않았으면 예전 파일을 믿지 않고, scan 이 남긴 마지막 줄을 이유로 돌려준다.
    시험 중(RAIL_NO_HARDWARE)이면 scan 을 띄우지 않는다 — 2026-11-12 16:43 사고가 이 길(시험 → preflight → 여기)이었다."""
    why = drive_lock.blocked_reason()
    if why:
        return False, why
    t0 = time.time()
    try:
        r = subprocess.run([PY, "-X", "utf8", str(ROOT / "run_motion.py"), "scan"], cwd=REPO, capture_output=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False, f"점검이 {timeout_s:.0f}초 안에 끝나지 않음"
    try:
        d = json.loads((DATA / "drives.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if not d or d.get("t", 0) < t0 - 1:
        out = (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        return False, out[-1] if out else "drives.json 을 읽지 못함"
    ok = d.get("slaves") == 1 and d.get("exit") == "시간 다 됨" and not d.get("emergencies")
    return ok, d.get("exit") or "?"


def wait_drive(poll_s: float = 30.0) -> None:
    """드라이브가 응답할 때까지 기다린다 — 라운드를 끝내지 않는다. 기다리는 동안 data/drive_needed.json 을 남긴다.
    시험 중(RAIL_NO_HARDWARE)이면 기다리지 않고 멈춘다 — 영영 돌아오지 않는 점검을 끝없이 기다리지 않게."""
    why = drive_lock.blocked_reason()
    if why:
        raise SystemExit(f"{why} — 드라이브 점검을 띄우지 않는다")
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


def first_move(csv_path: str | None, thr_um: int = 500) -> tuple[float | None, bool]:
    """(왕복이 출발한 시각, 시작점까지 자리를 먼저 옮겼나). 출발은 왕복 시작점을 처음 벗어난 때다.
    시작점이 다른 프리셋(2.0 m/s 는 250 mm, 나머지는 150 mm)으로 바뀐 첫 운전은 시작점까지 자리를 먼저 옮긴다.
    처음 위치를 벗어난 때를 출발로 읽으면 그 자리 옮김이 출발이 되고, 그 지연으로 맞춘 다음 운전이 0.27초 어긋났다(2026-11-16).
    시작점은 기록 뒤쪽 절반의 양 끝 가운데 처음 위치에 가까운 쪽이다 — 자리 옮김은 앞쪽에서 끝난다."""
    if not csv_path:
        return None, False
    # 드라이브 통신이 잠깐 끊긴 주기(wkc -1)는 위치 칸 없이 기록된다 — 건너뛴다(2026-11-16 20:28, 이 줄에서 실행기가 멈췄다)
    rows = [r for r in csv.DictReader(open(csv_path, encoding="utf-8")) if r.get("pos") not in (None, "")]
    pos = [int(r["pos"]) for r in rows]
    if not pos:
        return None, False
    tail = pos[len(pos) // 2:]
    start = min((min(tail), max(tail)), key=lambda e: abs(e - pos[0]))
    moved = abs(pos[0] - start) > thr_um
    at = next((k for k, q in enumerate(pos) if abs(q - start) <= thr_um), None)
    if at is None:
        return None, moved
    return next((float(rows[k]["t_wall"]) for k in range(at, len(pos)) if abs(pos[k] - start) > thr_um), None), moved


def run_plain(n: int, out: Path, runs: list, test: str = "IMU-FW") -> int:
    """모터만 n 번 — IMU-MOVE · IMU-FW · 상한 시험. 장면은 건드리지 않는다(정지 장면이 돌고 있어야 한다)."""
    done, retries = 0, 0
    while done < n:
        i = len(runs) + 1
        if not ensure_link():
            return 1
        if charge.should_stop(charge.rail_low()[0]):
            say("가장 낮은 셀이 멈춤 기준 이하 — 충전부터"); return 2
        wait_marker(30)
        rc, why, csvp, t_cmd = motor_once(out / f"run{i}_motor.log")
        runs.append({"i": i, "t_motor_cmd": t_cmd, "rc": rc, "why": why, "motor_csv": csvp})
        (out / "runs.json").write_text(json.dumps(runs, ensure_ascii=False, indent=1), encoding="utf-8")
        say(f"{test} {i}번째 끝 · 코드 {rc} · {why}")
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


def scene_still_s(scene: str) -> float:
    """장면이 움직이기 시작하는 초 — 장면 정답 파일(t_s · pos_mm · v_mm_s)에서 속도가 처음 0 보다 큰 줄의 앞 줄.
    S06 ~ S11 은 20초, S12_Move1000s45 는 45초(2026-11-17 정지 45초 확인 시험). 정답 파일을 못 찾으면 STILL_S."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import qc_round as Q
    t = Q.scene_truth(scene)
    if t is None or not t.exists():
        return STILL_S
    prev = 0.0
    for r in csv.DictReader(open(t, encoding="utf-8")):
        if float(r["v_mm_s"]) > 0:
            return round(prev, 2)
        prev = float(r["t_s"])
    return STILL_S


def run_scene_sync(n: int, out: Path, runs: list, lead: float, scene: str = SCENE_SYNC, test: str = "GNSS-MOVE", stuck_exit: bool = False) -> int:
    """장면과 함께 n 번 — GNSS-MOVE. 번마다 장면을 처음부터 다시 틀고, 하늘 출발(장면이 움직이기 시작하는 초)에 맞춰 모터를 띄운다.
    stuck_exit 이면 운전마다 모터 출발 직전과 운전 끝에 위치를 못 잡은 셀을 보고, 두 번 다 못 잡은 셀이 있으면 그 운전을 기록한 뒤 STUCK 을 돌려준다.
    장면을 다시 틀면(되감기) 셀이 위치를 잃고 재부팅 전까지 못 잡는 일이 있었다(2026-11-18 속도마다 1대, 10회 넘게 0 %) — 실시간 기록을 쥔
    실행기가 그 셀만 재부팅하고 남은 횟수를 이어 간다."""
    from motionbench.labsat import LabSat
    ls = LabSat()
    still = scene_still_s(scene)
    say(f"{scene} — 정지 {still:g}초 뒤 레일 출발")
    done, retries, extra = 0, 0, False
    while done < n:
        i = len(runs) + 1
        if not ensure_link():
            return 1
        if charge.should_stop(charge.rail_low()[0]):
            say("가장 낮은 셀이 멈춤 기준 이하 — 충전부터"); return 2
        wait_marker(60)
        ls.stop(); time.sleep(2)
        t_play = ls.play(scene)
        t_go = t_play + SKY_DELAY_S + still - lead
        say(f"{test} {i}번째 — 재생 시작 · 모터는 {t_go - time.time():.1f}초 뒤 (lead {lead:.2f})")
        while time.time() < t_go:
            time.sleep(0.005)
        pre = unlocked_now() if stuck_exit else set()
        rc, why, csvp, t_cmd = motor_once(out / f"run{i}_motor.log")
        stuck = sorted(pre & unlocked_now()) if stuck_exit else []
        try:
            ls.stop()
        except Exception as e:  # 재생 멈춤이 실패해도 다음 재생 명령이 처음부터 다시 튼다
            say(f"PLAY:STOP 실패 {e}")
        fm, moved = first_move(csvp)
        off = None if fm is None else round(fm - (t_play + SKY_DELAY_S + still), 3)
        runs.append({"i": i, "scene": scene, "still_s": still, "t_play": t_play,"t_motor_cmd": t_cmd, "t_first_move": fm, "rail_minus_sky_s": off,
                     "lead_used": lead, "sky_delay_used": SKY_DELAY_S, "rc": rc, "why": why, "motor_csv": csvp,
                     "moved_to_start": moved, "stuck": stuck})
        (out / "runs.json").write_text(json.dumps(runs, ensure_ascii=False, indent=1), encoding="utf-8")
        say(f"{test} {i}번째 끝 · 코드 {rc} · {why} · 레일 출발 − 하늘 출발 {off}초")
        if rc != 0:
            if comm_error(why) and retries < len(RETRY_WAIT_S):
                say(f"드라이브 통신 오류 — {RETRY_WAIT_S[retries]}초 뒤 다시 붙는다({retries + 1}/{len(RETRY_WAIT_S)})")
                time.sleep(RETRY_WAIT_S[retries]); retries += 1
                wait_drive()
                continue
            say("정상으로 끝나지 않아 멈춘다"); return 1
        retries = 0
        if stuck:
            say(f"{test} {i}번째 — 출발 전부터 끝까지 위치를 못 잡은 셀 {stuck} — 실행기가 재부팅하고 이어 간다")
        if moved and not extra:   # 자리 옮김이 낀 운전은 지연이 다르다 — 세지 않고, 지연도 배우지 않고, 시작점에서 한 번 더 돈다(한 번만)
            extra = True
            say(f"{test} {i}번째는 시작점까지 자리를 옮긴 운전 — 횟수에 넣지 않고 한 번 더 돈다")
        else:
            done += 1
            if moved:   # 두 번째 자리 옮김(멈춘 사이 레일이 밀림 등)은 세되, 그 지연은 배우지 않는다(2026-11-16 19:23 — 배워서 다음 운전이 0.6초 어긋났다)
                say(f"{test} {i}번째도 시작점까지 자리를 옮긴 운전 — 횟수에는 넣고 지연은 배우지 않는다")
            elif fm:
                lead = fm - t_cmd
        if stuck:
            return STUCK
        time.sleep(5)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("test", choices=TESTS, help="시험 ID(GNSS-MOVE · IMU-MOVE · IMU-FW) 또는 cap(상한 시험)")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lead", type=float, default=5.3, help="GNSS-MOVE — 모터 명령에서 첫 움직임까지 첫 추정(초). 둘째 번부터는 앞 번 실측")
    ap.add_argument("--scene", default=SCENE_SYNC, help=f"GNSS-MOVE 의 LabSat 장면 이름 (기본 {SCENE_SYNC} = 300 mm/s)")
    ap.add_argument("--stuck-exit", action="store_true", help="GNSS-MOVE — 위치를 못 잡고 남은 셀이 있으면 그 운전 뒤 코드 3 으로 돌아온다(실행기가 재부팅하고 이어 간다)")
    ap.add_argument("--preset", help="승인 프리셋 이름(presets/<이름>.json). 기본은 시험마다 DEFAULT_PRESET — RUN-CONST_v100a8000 처럼 속도별 프리셋을 준다")
    a = ap.parse_args(argv)
    with drive_lock.take_round(f"run_rail {a.test}"):     # 이미 누가 쥐고 있으면 Busy(SystemExit) — 장비를 건드리기 전에 멈춘다
        return _run(a)


def _run(a) -> int:
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    preset = install_preset(a.test, a.preset)
    sync = a.test in qc_ids.SCENE_SYNC
    per = 110.0 if sync else 95.0
    preflight(a.test, a.n, per)
    (out / "preset.json").write_text(json.dumps(preset, ensure_ascii=False, indent=1), encoding="utf-8")
    rp = out / "runs.json"
    runs = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else []
    rc = run_scene_sync(a.n, out, runs, a.lead, a.scene, a.test, a.stuck_exit) if sync else run_plain(a.n, out, runs, a.test)
    say(f"끝 → {rp}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
