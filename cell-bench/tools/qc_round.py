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
        return json.loads((DATA / "qc_now.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def fixed_count() -> tuple[int, int]:
    c = qc_now().get("cells") or {}
    live = [x for x in c.values() if x.get("age_s") is not None and x["age_s"] < 10]
    return sum(1 for x in live if x.get("fix") in ("A", "D", "F", "R")), len(live)


def start_tap(part: str, minutes: float, log_path: Path) -> subprocess.Popen:
    f = open(log_path, "a", encoding="utf-8")
    return subprocess.Popen([PY, "-X", "utf8", "-u", str(ROOT / "motionbench" / "live_tap.py"), "--minutes", f"{minutes:g}",
                             "--qc-now", "--part", part, "--sky", "simulator"], cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)


def stop_tap(p: subprocess.Popen) -> None:
    if p.poll() is None:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
    from cellbench.control import write_json_atomic
    write_json_atomic(DATA / "qc_now.json", {"t": time.time(), "step": {"key": "idle", "index": None}})


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


def judge(cmd: list[str], say) -> int:
    say("판정: " + " ".join(cmd[2:5]))
    r = subprocess.run([PY310, "-X", "utf8", *cmd], cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in (r.stdout + r.stderr).splitlines()[-12:]:
        say("   " + line)
    return r.returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--dock", default="Y", help="dock_layout.json 의 배치 이름 — Y(원래 자리) · Y-swap(자리 바꾼 뒤)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--a-min", type=float, default=30.0)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--b", default="300,100,500", help="파트 B 속도 목록 mm/s (빈 문자열이면 B 를 건너뛴다)")
    ap.add_argument("--skip-a", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    say = Log(out / "round.log")
    speeds = [int(x) for x in a.b.split(",") if x.strip()]
    for v in speeds:
        if v not in B_SCENES or not B_SCENES[v][1].exists():
            raise SystemExit(f"B {v} mm/s 의 장면 · 정답 파일이 없다")
    for part in ["C", *(B_SCENES[v][2] for v in speeds)]:
        if not (run_rail.PRESETS / f"long-{part}.json").exists():
            raise SystemExit(f"승인 프리셋 long-{part}.json 이 없다")
    need_s = a.n * (95 + 110 * len(speeds)) + 300
    left = run_rail.marker_until() - time.time()
    if left < need_s:
        raise SystemExit(f"입회 표지 남은 시간 {max(left, 0) / 60:.0f}분 < 필요 {need_s / 60:.0f}분 — 레일 옆에서 tools/attend.py --minutes 240")
    low, nc, why = charge.rail_low()
    if a.skip_a and (prob := charge.start_problem(low, why)):
        raise SystemExit(prob)
    ls = LabSat()
    say(f"라운드 {a.round} 시작 · 배치 {a.dock} · A {a.a_min:g}분 · C {a.n}번 · B {speeds} 각 {a.n}번 · 표지 {left / 60:.0f}분 남음")
    summary = {"round": a.round, "dock": a.dock, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "charging": "off(방전 상태)"}

    # 1. 방전 상태로 — 플러그 끄기
    r = charge.plug_set(False, say)
    summary["plug_off"] = r
    ensure_static(ls, say)

    # 2. 파트 A — 정지 (라이브 기록만)
    tap = start_tap("A", a.a_min + LOCK_WAIT_S / 60 + 2, out / "tap_A.log")
    time.sleep(8)
    if not wait_lock(say):
        stop_tap(tap); charge.plug_set(True, say); raise SystemExit("셀이 위치를 잡지 못해 멈춘다")
    low, nc, why = charge.rail_low()
    if (prob := charge.start_problem(low, why)):
        stop_tap(tap); charge.plug_set(True, say); raise SystemExit(prob)
    say(f"가장 낮은 셀 {low}% ({nc}대)")
    if not a.skip_a:
        t_a0 = time.time()
        say(f"A 정지 {a.a_min:g}분 시작")
        time.sleep(a.a_min * 60)
        t_a1 = time.time()
        summary["A"] = {"from": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t_a0)), "to": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t_a1))}
        say("A 끝")
    stop_tap(tap)

    # 3. 파트 C · B — 레일
    tap = start_tap("C", 240, out / "tap_CB.log")
    time.sleep(8)
    rc = run_rail.main(["C", "--n", str(a.n), "--out", str(out / "C")])
    summary["C"] = {"rc": rc}
    if rc == 0:
        for v in speeds:
            scene, truth, preset = B_SCENES[v]
            rc = run_rail.main(["B", "--n", str(a.n), "--out", str(out / f"B{v}"), "--scene", scene, "--preset", preset])
            summary[f"B{v}"] = {"rc": rc, "scene": scene}
            if rc != 0:
                break
    try:
        ls.stop(); time.sleep(2); ls.play(STATIC)
    except Exception as e:
        say(f"정지 장면 되돌리기 실패 {e}")
    stop_tap(tap)
    summary["plug_on"] = charge.plug_set(True, say)
    if rc != 0:
        say(f"레일 시험이 정상으로 끝나지 않았다(코드 {rc}) — 받기 · 판정은 한다")

    # 4. 받으면서 비우기
    ftg = rail_cells.extract_all(a.dock.split("-")[0], say)
    summary["ftg"] = str(ftg)

    # 5. 판정 — 결과판이 읽는 data/protocol_*.json 으로
    rcs = []
    if "A" in summary:
        rcs.append(judge(["cell-bench/tools/protocol_v4.py", "a", "--from", summary["A"]["from"], "--to", summary["A"]["to"], "--window", "300",
                          "--ftg", str(ftg), "--scene", STATIC, "--dock", a.dock], say))
    if (out / "C" / "runs.json").exists():
        rcs.append(judge(["cell-bench/tools/protocol_v4.py", "c", "--runs", str(out / "C" / "runs.json"), "--ftg", str(ftg), "--accel", "50", "--dock", a.dock], say))
    for v in speeds:
        rp = out / f"B{v}" / "runs.json"
        if rp.exists():
            scene, truth, _ = B_SCENES[v]
            rcs.append(judge(["cell-bench/tools/protocol_v4.py", "b", "--runs", str(rp), "--truth", str(truth), "--scene", scene, "--dock", a.dock], say))
    summary["judge_rc"] = rcs
    summary["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"라운드 {a.round} 끝 → {out / 'summary.json'}")
    return 0 if rc == 0 and all(x == 0 for x in rcs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
