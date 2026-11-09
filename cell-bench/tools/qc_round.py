"""QC 한 라운드를 멈춤 없이 돈다 — 플러그 끄기 → A 정지 → C → B(속도마다) → 플러그 켜기 → 받으면서 비우기 → 판정.

  python cell-bench/tools/qc_round.py --round 1 --dock Y      --out cell-bench/data/collect_20261102/round1
  python cell-bench/tools/qc_round.py --round 2 --dock Y-swap --out cell-bench/data/collect_20261102/round2

사람이 하는 것은 둘뿐이다: 시작 전에 레일 옆에서 입회 표지를 만든다(tools/attend.py --minutes 240), 라운드 사이에 셀 자리를 바꾼다.
그 밖의 모든 것(플러그 · LabSat 장면 · 모터 · 기록 · 받기 · 판정)은 이 스크립트가 한다. 멈추면 마지막 줄이 이유다.
2026-11-02 사용자 결정 — 시험은 방전 상태(플러그 끔)에서, 같은 세트를 여러 번, 조건을 회차마다 기록한다.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from motionbench import charge  # noqa: E402
from motionbench.labsat import LabSat  # noqa: E402
import run_rail  # noqa: E402
import rail_cells  # noqa: E402

REPO = ROOT.parent
DATA = ROOT / "data"
from cellbench.control import live_dir  # noqa: E402  실시간 파일(qc_now.json)은 C:\Dev 밖
PY = sys.executable
PY310 = r"C:\Users\HW team\AppData\Local\Programs\Python\Python310\python.exe"      # 판정(FTG 읽기)은 pandas 가 있는 3.10
STATIC = "S01-Static-GLE-8h"
B_SCENES = {300: ("S03_RailB120", DATA / "partB_20261030" / "rail_B_120s_truth.csv", "B"),
            100: ("S04_RailB100", DATA / "scenes" / "S04_RailB100" / "S04_RailB100_truth.csv", "B100"),
            500: ("S05_RailB500", DATA / "scenes" / "S05_RailB500" / "S05_RailB500_truth.csv", "B500")}
LOCK_MIN = 20          # 이만큼 위치를 잡아야 A 를 시작
LOCK_WAIT_S = 900      # 위치 잡기 최대 대기


class Log:
    def __init__(self, path: Path):
        self.path = path; path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def qc_now() -> dict:
    try:
        return json.loads((live_dir(DATA) / "qc_now.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def fixed_count() -> tuple[int, int]:
    c = qc_now().get("cells") or {}
    live = [x for x in c.values() if x.get("age_s") is not None and x["age_s"] < 10]
    return sum(1 for x in live if x.get("fix") in ("A", "D", "F", "R")), len(live)


TAP_LAYOUT = "Y"        # 실시간 화면이 그릴 배치 — main 이 --dock 으로 바꾼다(자리 바꾼 라운드는 Y-swap)


def start_tap(part: str, minutes: float, log_path: Path) -> subprocess.Popen:
    f = open(log_path, "a", encoding="utf-8")
    return subprocess.Popen([PY, "-X", "utf8", "-u", str(ROOT / "motionbench" / "live_tap.py"), "--minutes", f"{minutes:g}",
                             "--qc-now", "--part", part, "--sky", "simulator", "--layout", TAP_LAYOUT],
                            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)


def stop_tap(p: subprocess.Popen) -> None:
    if p.poll() is None:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
    from cellbench.control import write_json_atomic
    write_json_atomic(live_dir(DATA) / "qc_now.json", {"t": time.time(), "step": {"key": "idle", "index": None}})


def ensure_static(ls: LabSat, say) -> None:
    st = ls.status()
    if st and st[0] == STATIC:
        return
    ls.stop(); time.sleep(2); ls.play(STATIC)
    say(f"LabSat 정지 장면 {STATIC} 재생 시작")


def wait_lock(say, need: int = LOCK_MIN, max_s: float = LOCK_WAIT_S) -> bool:
    t0 = time.time()
    while time.time() - t0 < max_s:
        fx, n = fixed_count()
        if fx >= need:
            say(f"위치 잡은 셀 {fx}/{n} — 시작한다")
            return True
        time.sleep(10)
    fx, n = fixed_count()
    say(f"{max_s / 60:.0f}분 지나도 위치 잡은 셀 {fx}/{n} < {need}")
    return False


def gps_ready(ls: LabSat, scene: str, say, tap=None, part: str = "C", out: Path | None = None, minutes: float = 60,
              need: int = 12, max_s: float = 300, reboot_after_s: float = 90):
    """장면을 바꿀 때 셀이 그 장면에서 위치를 잡을 때까지 기다린다 — (잡은 수, tap) 을 돌려준다.
    들리는 셀이 모두 잡으면 바로, 아니면 max_s(5분)를 다 기다린 뒤 잡은 수로 시작한다(2026-11-03 사용자: 독에 셀이 겹쳐 있어 일찍 끊지 않는다).
    reboot_after_s 가 지나도 못 잡은 셀이 있으면 그 셀만 원격 재부팅한다 — 정지 장면(다른 날짜)에서 B 장면으로 바뀌면 셀이 오래 못 잡는데
    재부팅으로 바로 잡았다(11-02 16:55). 재부팅은 라이브 포트를 쓰므로 tap 을 멈췄다 다시 켠다(tap 이 None 이면 재부팅하지 않는다).
    5분 뒤에도 need 미만이면 (None, tap)."""
    ls.stop(); time.sleep(2); ls.play(scene)
    t0 = time.time(); fx = n = 0; rebooted = False
    while time.time() - t0 < max_s:
        time.sleep(5)
        fx, n = fixed_count()
        if n and fx >= n:
            say(f"장면 {scene} — 위치 잡은 셀 {fx}/{n} 모두 ({time.time() - t0:.0f}초), 시작한다")
            return fx, tap
        if not rebooted and tap is not None and time.time() - t0 >= reboot_after_s:
            rebooted = True
            bad = unlocked()
            say(f"장면 {scene} — {reboot_after_s:.0f}초 지나도 못 잡은 셀 {len(bad)}대 {bad} — 원격 재부팅")
            stop_tap(tap); reboot(bad, say)
            tap = start_tap(part, minutes, (out or DATA) / f"tap_{part}.log"); time.sleep(8)
    fx, n = fixed_count()
    if fx >= need:
        say(f"장면 {scene} — {max_s / 60:.0f}분 기다려 위치 잡은 셀 {fx}/{n}, 시작한다")
        return fx, tap
    say(f"장면 {scene} — {max_s / 60:.0f}분 지나도 위치 잡은 셀 {fx}/{n} < {need}")
    return None, tap


def unlocked() -> list[int]:
    c = qc_now().get("cells") or {}
    return sorted(int(k) for k, x in c.items() if x.get("age_s") is not None and x["age_s"] < 10 and x.get("fix") not in ("A", "D", "F", "R"))


def reboot(serials: list[int], say) -> None:
    """측정으로 돌아가는 재부팅(0x26) — 상태 묻기 뒤에 보낸다. 장면이 2026 → 2023 으로 뒤로 가면 셀이 위치를 오래 못 잡는데
    (2026-10-30 · 11-02 실측), 원격 재부팅으로 바로 다시 잡았다(11-02 16:55, 19대). 라이브 포트를 쓰므로 tap 을 멈춘 뒤 부른다."""
    from dataclasses import replace
    from cellbench.cells import CellLink, LiveListener
    from cellbench.config import Config
    cfg = replace(Config(), serials=serials)
    lv = LiveListener(cfg); lv.start(); time.sleep(5)
    try:
        res = CellLink(cfg, lv, log=lambda m: None).status([s for s in serials if s in lv.alive()])
    finally:
        lv.stop()
    say(f"원격 재부팅 {len(res)}대 — 측정 복귀 {sum(1 for r in res.values() if r.resume_s is not None)}대")


def judge(cmd: list[str], say) -> int:
    say("판정: " + " ".join(cmd[2:5]))
    r = subprocess.run([PY310, "-X", "utf8", *cmd], cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in (r.stdout + r.stderr).splitlines()[-12:]:
        say("   " + line)
    return r.returncode


def lock_ready(part: str, minutes: float, out: Path, say):
    """실시간 기록(tap)을 켜고 셀이 위치를 잡을 때까지 기다린다. 5분 안에 안 되면 못 잡은 셀만 원격 재부팅하고 다시 기다린다."""
    log = out / f"tap_{part}.log"
    tap = start_tap(part, minutes + 2 * LOCK_WAIT_S / 60 + 5, log)
    time.sleep(8)
    if wait_lock(say, max_s=300):
        return tap
    bad = unlocked()
    stop_tap(tap)
    say(f"5분 지나도 못 잡은 셀 {bad} — 원격 재부팅")
    reboot(bad, say)
    tap = start_tap(part, minutes + LOCK_WAIT_S / 60 + 5, log)
    time.sleep(8)
    if not wait_lock(say):
        stop_tap(tap); charge.plug_set(True, say)
        raise SystemExit(f"재부팅 뒤에도 위치를 못 잡아 멈춘다 — 못 잡은 셀 {unlocked()} 를 껐다 켠다")
    return tap


def battery_ok(say) -> None:
    low, nc, why = charge.rail_low()
    if (prob := charge.start_problem(low, why)):
        charge.plug_set(True, say); raise SystemExit(prob)
    say(f"가장 낮은 셀 {low}% ({nc}대)")


def run_a(a, out: Path, ls, say, summary: dict) -> None:
    summary["plug_off_A"] = charge.plug_set(False, say)
    ensure_static(ls, say)
    tap = lock_ready("A", a.a_min, out, say)
    try:
        battery_ok(say)
        t_a0 = time.time()
        say(f"A 정지 {a.a_min:g}분 시작")
        time.sleep(a.a_min * 60)
        summary["A"] = {"from": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t_a0)), "to": time.strftime("%Y-%m-%d %H:%M:%S")}
        say("A 끝")
    finally:
        stop_tap(tap)


def run_motor(a, out: Path, ls, speeds: list[int], say, summary: dict) -> int:
    summary["plug_off_CB"] = charge.plug_set(False, say)
    ensure_static(ls, say)
    tap = lock_ready("C", 240, out, say)
    try:
        battery_ok(say)
        rc = run_rail.main(["C", "--n", str(a.n), "--out", str(out / "C")])
        summary["C"] = {"rc": rc}
        if rc == 0:
            for v in speeds:
                scene, truth, preset = B_SCENES[v]
                got, tap = gps_ready(ls, scene, say, tap, "C", out, a.n * 3 + 20)
                summary[f"B{v}_gps_fixed"] = got
                if got is None:
                    rc = 1
                    break
                rc = run_rail.main(["B", "--n", str(a.n), "--out", str(out / f"B{v}"), "--scene", scene, "--preset", preset])
                summary[f"B{v}"] = {"rc": rc, "scene": scene}
                if rc != 0:
                    break
        say("모터 시험 끝 — 여기부터 현장 입회가 필요 없다")
    finally:
        try:
            ensure_static(ls, say)           # 이미 정지 장면이면 건드리지 않는다 — 다시 틀면 셀이 위치를 잃는다(2026-11-02 20:29)
        except Exception as e:
            say(f"정지 장면 되돌리기 실패 {e}")
        stop_tap(tap)
    if rc != 0:
        say(f"레일 시험이 정상으로 끝나지 않았다(코드 {rc}) — 받기 · 판정은 한다")
    return rc


def judge_all(out: Path, dock: str, ftg: list[Path], speeds: list[int], a_span: dict | None, say) -> list[int]:
    """수집 라운드 판정 — 기준 없이(살펴봄만) out/judged/ 에 쓴다. 결과판 목록(data/ 바로 아래)을 수집 파일로 채우지 않는다.
    1차 기준은 이 파일들로 tools/qc_criteria.py 가 만든다."""
    rcs = []
    tail = ["--out", str(out / "judged"), "--criteria", "none", "--set", dock.split("-")[0]]   # --set 을 빼면 판정 도구 기본값 Y 가 적힌다(11-06 X 판정에서 발견)
    (out / "judged").mkdir(parents=True, exist_ok=True)
    _judge = judge
    judge_ = lambda cmd, say: _judge(cmd + tail, say)
    if a_span:
        rcs.append(judge_(["cell-bench/tools/protocol_v4.py", "a", "--from", a_span["from"], "--to", a_span["to"], "--window", "300",
                          "--ftg", *map(str, ftg), "--scene", STATIC, "--dock", dock], say))
    if (out / "C" / "runs.json").exists():
        rcs.append(judge_(["cell-bench/tools/protocol_v4.py", "c", "--runs", str(out / "C" / "runs.json"), "--ftg", *map(str, ftg), "--accel", "50", "--dock", dock], say))
    for v in speeds:
        rp = out / f"B{v}" / "runs.json"
        if rp.exists():
            scene, truth, _ = B_SCENES[v]
            rcs.append(judge_(["cell-bench/tools/protocol_v4.py", "b", "--runs", str(rp), "--truth", str(truth), "--scene", scene, "--dock", dock,
                               *(["--ftg", *map(str, ftg)] if ftg else [])], say))
    return rcs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--dock", default="Y", help="dock_layout.json 의 배치 이름 — Y(원래 자리) · Y-swap(자리 바꾼 뒤)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--a-min", type=float, default=30.0)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--b", default="300,100,500", help="파트 B 속도 목록 mm/s (빈 문자열이면 B 를 건너뛴다)")
    ap.add_argument("--skip-a", action="store_true")
    ap.add_argument("--skip-motor", action="store_true", help="C · B 를 하지 않고 A 만(입회 불필요 — 야간 반복용)")
    ap.add_argument("--a-last", action="store_true", help="모터 시험(C · B)을 먼저 하고 A 를 맨 끝에 — A 는 현장 입회가 필요 없어 사람이 떠난 뒤에 돈다")
    ap.add_argument("--judge-only", action="store_true", help="이미 돈 라운드를 판정만 — --ftg, A 가 있었으면 --a-from · --a-to")
    ap.add_argument("--ftg", nargs="+"); ap.add_argument("--a-from"); ap.add_argument("--a-to")
    ap.add_argument("--no-extract", action="store_true", help="받기 · 판정을 하지 않고 끝낸다(다음 라운드 뒤에 한 번에 받는다)")
    a = ap.parse_args(argv)
    global TAP_LAYOUT
    TAP_LAYOUT = a.dock
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    say = Log(out / "round.log")
    speeds = [int(x) for x in a.b.split(",") if x.strip()]
    if a.judge_only:
        span = {"from": a.a_from, "to": a.a_to} if a.a_from and a.a_to else None
        rcs = judge_all(out, a.dock, [Path(f) for f in a.ftg], speeds, span, say)
        say(f"라운드 {a.round} 판정만 끝 · {rcs}")
        return 0 if all(x == 0 for x in rcs) else 1
    for v in speeds:
        if v not in B_SCENES or not B_SCENES[v][1].exists():
            raise SystemExit(f"B {v} mm/s 의 장면 · 정답 파일이 없다")
    for part in ["C", *(B_SCENES[v][2] for v in speeds)]:
        if not (run_rail.PRESETS / f"long-{part}.json").exists():
            raise SystemExit(f"승인 프리셋 long-{part}.json 이 없다")
    need_s = 0 if a.skip_motor else a.n * (95 + 122 * len(speeds))
    left = run_rail.marker_fresh_left()
    ls = LabSat()
    order = "C · B → A" if a.a_last else "A → C · B"
    say(f"라운드 {a.round} 시작 · 배치 {a.dock} · 순서 {order} · A {a.a_min:g}분 · C {a.n}번 · B {speeds} 각 {a.n}번 · 모터 {need_s / 60:.0f}분 · 표지 {max(left, 0) / 60:.0f}분 남음(5시간마다 갱신)")
    summary = {"round": a.round, "dock": a.dock, "order": order, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "charging": "off(방전 상태)"}
    rc = 0
    if a.skip_motor:
        run_a(a, out, ls, say, summary)
    elif a.a_last:
        rc = run_motor(a, out, ls, speeds, say, summary)
        if not a.skip_a:
            run_a(a, out, ls, say, summary)
    else:
        if not a.skip_a:
            run_a(a, out, ls, say, summary)
        rc = run_motor(a, out, ls, speeds, say, summary)
    summary["plug_on"] = charge.plug_set(True, say)
    if a.no_extract:
        summary["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        say(f"라운드 {a.round} 끝(받기 · 판정은 뒤로) → {out / 'summary.json'}")
        return rc
    ftg = rail_cells.extract_all(a.dock.split("-")[0], say)
    summary["ftg"] = str(ftg)
    rcs = judge_all(out, a.dock, [ftg], speeds, summary.get("A"), say)
    summary["judge_rc"] = rcs
    summary["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"라운드 {a.round} 끝 → {out / 'summary.json'}")
    return 0 if rc == 0 and all(x == 0 for x in rcs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
