"""레일 QC 실행기들의 공통 준비 — 시험 실행기(tools/qc_tests/*_run.py) · 묶음 실행기(qc_tests/qc_bundle.py) · 하루 수집 실행기(qc_loop.py)가 부른다.

시험 이름은 시험 ID(dynamic-bench/design/qc-tests.md, motionbench/qc_ids.py)로 부른다. 이 모듈이 맡는 것:
  · 시험 전 저장량 확인(storage_ready: 셀마다 저장 MB → 120 MB 이상 · 앞에서 못 지운 셀만 받고 비움 → 남으면 멈춤)
  · 실시간 기록(live_tap) 켜고 끄기, 셀이 위치를 잡을 때까지 기다리기, 못 잡은 셀만 원격 재부팅
  · 레일 시험의 공통 준비(rail_prep: 플러그 끔 → 정지 장면 · 감쇠 0 → 위치 잡기 → 배터리 확인)와 마무리(rail_finish)
  · GNSS-MOVE 장면 표(CONST_SCENES), 셀 정지 기록 한 구간(still_window), 셀 기록 받기(extract), 하루 수집 판정(judge_all)
2026-11-02 사용자 결정 — 시험은 방전 상태(플러그 끔)에서, 같은 세트를 여러 번, 조건을 회차마다 기록한다.
2026-11-12 사용자 결정 — 옛 '파트' 이름을 버리고 시험 ID 로 부른다. 예전의 한 라운드 실행기(이 파일의 main)는 묶음 실행기로 바뀌었다.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from motionbench import cell_models, charge, drive_lock, qc_ids  # noqa: E402
from motionbench.labsat import LabSat, clean  # noqa: E402
import run_rail  # noqa: E402
import rail_cells  # noqa: E402

REPO = ROOT.parent
DATA = ROOT / "data"
from cellbench.control import live_dir  # noqa: E402  실시간 파일(qc_now.json)은 C:\Dev 밖
from cellbench.protocol import FIX_MODES  # noqa: E402  '위치 잡음' 정의 하나(2026-11-25)
PY = sys.executable
PY310 = r"C:\Users\HW team\AppData\Local\Programs\Python\Python310\python.exe"      # 판정(FTG 읽기)은 pandas 가 있는 3.10
STATIC = "S01-Static-GLE-8h"
# GNSS-MOVE 속도(mm/s) → (LabSat 장면, 장면 정답 파일, 승인 프리셋). 장면 이름 · 정답 파일 자리는 LabSat 과 지난 기록의 것이라 바꾸지 않는다.
# 위성-12 장면(2026-11-13 ~): 2026-10-30 16:00 UTC · 앙각 마스크 10°, 가속도 8 m/s², 끝 멈춤 3초. 2.0 m/s 만 구간 1,200 mm(레일 급정지 여유).
# 운전 폴더 이름은 프리셋 이름(RUN-CONST_v<속도>a8000) — 위성-6 시절 폴더(RUN-CONST_v<속도>)와 섞이지 않는다.
CONST_SCENES = {v: (n, DATA / "scenes" / n / f"{n}_truth.csv", f"RUN-CONST_v{v}a8000")
                for v, n in ((100, "S06_Move100"), (300, "S07_Move300"), (500, "S08_Move500"),
                             (1000, "S09_Move1000"), (1400, "S10_Move1400"), (2000, "S11_Move2000"))}
# 하루 수집 · GNSS-MOVE 실행기의 기본 속도 차례(느린 것부터) — 판정 속도는 0.1 ~ 1.4 m/s 다섯 단계(2026-11-17 사용자 결정).
# 2.0 m/s 는 등속 구간이 0.35초뿐이라 셀 기록(0.1초마다)으로도 운전마다 ±70 mm/s 로 흩어져 판정 속도에서 뺐다(1.4 m/s 는 ±7).
# 장면 · 프리셋은 남겨 둔다 — 따로 돌릴 때는 --speeds 2000.
CONST_SPEEDS = [v for v in CONST_SCENES if v <= 1400]
# 위성-6 장면(2026-10-30 ~ 11-13, 앙각 마스크 20° 로 위성 6개) — 그 기간 기록을 다시 판정할 때만 쓴다.
# (2026-11-12 까지 FIRMWARE-2 로 부르던 운전 — 프리셋 이름만 RUN-CONST_v<속도> 로 바꿨고 값은 그대로다)
CONST_SCENES_SAT6 = {300: ("S03_RailB120", DATA / "partB_20261030" / "rail_B_120s_truth.csv", "RUN-CONST_v300"),
                     100: ("S04_RailB100", DATA / "scenes" / "S04_RailB100" / "S04_RailB100_truth.csv", "RUN-CONST_v100"),
                     500: ("S05_RailB500", DATA / "scenes" / "S05_RailB500" / "S05_RailB500_truth.csv", "RUN-CONST_v500")}
# 정지 45초 확인 시험(2026-11-17 사용자 결정) — 레일 출발 전 정지를 20초에서 45초로 늘리면 위치를 잡은 비율이 오르는지 1.0 m/s 로만 본다.
# 장면 S12 는 정지 45초 뒤 왕복 7번(장면 길이 119.9초에 맞춤)이라 프리셋도 왕복 7번짜리를 쓴다. 실행기: gnss_move_run.py --still45
# 정지 45초 장면(2026-11-17 S12 · 11-18 S13 ~ S16). 정지가 길어 120초 장면에 들어가는 왕복 수가 줄어 프리셋도 c<왕복 수> 로 따로 둔다.
CONST_SCENES_S45 = {v: (n, DATA / "scenes" / n / f"{n}_truth.csv", f"RUN-CONST_v{v}a8000c{c}")
                    for v, n, c in ((100, "S13_Move100s45", 1), (300, "S14_Move300s45", 4), (500, "S15_Move500s45", 5),
                                    (1000, "S12_Move1000s45", 7), (1400, "S16_Move1400s45", 8))}


def scene_truth(scene: str) -> Path | None:
    """장면 이름 → 정답 파일. 운전 기록(runs.json)에 적힌 장면 이름으로 판정에 쓸 정답을 고른다."""
    return next((t for s, t, _ in (*CONST_SCENES.values(), *CONST_SCENES_SAT6.values(), *CONST_SCENES_S45.values()) if s == scene), None)
LOCK_MIN = 20          # 이만큼 위치를 잡아야 시험을 시작
LOCK_WAIT_S = 900      # 위치 잡기 최대 대기


class Log:
    """화면과 기록 파일에 같은 줄을 쓴다. 파일을 여럿 주면 모두에 쓴다(묶음 실행기 — 시험 폴더마다 같은 실행 기록)."""
    def __init__(self, *paths: Path):
        self.paths = paths
        for p in paths:
            p.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        for p in self.paths:
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")


def qc_now() -> dict:
    try:
        return json.loads((live_dir(DATA) / "qc_now.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def fixed_count() -> tuple[int, int]:
    c = qc_now().get("cells") or {}
    live = [x for x in c.values() if x.get("age_s") is not None and x["age_s"] < 10]
    return sum(1 for x in live if x.get("fix") in FIX_MODES), len(live)


TAP_LAYOUT = "Y"        # 실시간 화면이 그릴 배치 — 실행기가 --layout 으로 바꾼다(자리 바꾼 회차는 Y-swap)


def start_tap(test: str, minutes: float, log_path: Path) -> subprocess.Popen:
    """실시간 기록을 켠다. test 는 시험 ID — 함께 돌리면 'IMU-MOVE,IMU-FW' 처럼 쉼표로."""
    f = open(log_path, "a", encoding="utf-8")
    return subprocess.Popen([PY, "-X", "utf8", "-u", str(ROOT / "motionbench" / "live_tap.py"), "--minutes", f"{minutes:g}",
                             "--qc-now", "--test", test, "--sky", "simulator", "--layout", TAP_LAYOUT],
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


def gps_ready(ls: LabSat, scene: str, say, tap=None, test: str = "GNSS-MOVE", out: Path | None = None, minutes: float = 60,
              need: int = 12, max_s: float = 300, reboot_after_s: float = 90):
    """장면을 바꿀 때 셀이 그 장면에서 위치를 잡을 때까지 기다린다 — (잡은 수, tap) 을 돌려준다.
    들리는 셀이 모두 잡으면 바로, 아니면 max_s(5분)를 다 기다린 뒤 잡은 수로 시작한다(2026-11-03 사용자: 독에 셀이 겹쳐 있어 일찍 끊지 않는다).
    reboot_after_s 가 지나도 못 잡은 셀이 있으면 그 셀만 원격 재부팅한다 — 정지 장면(다른 날짜)에서 GNSS-MOVE 장면으로 바뀌면 셀이 오래 못 잡는데
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
            tap = start_tap(test, minutes, (out or DATA) / "tap.log"); time.sleep(8)
    fx, n = fixed_count()
    if fx >= need:
        say(f"장면 {scene} — {max_s / 60:.0f}분 기다려 위치 잡은 셀 {fx}/{n}, 시작한다")
        return fx, tap
    say(f"장면 {scene} — {max_s / 60:.0f}분 지나도 위치 잡은 셀 {fx}/{n} < {need}")
    return None, tap


def unlocked() -> list[int]:
    """실시간 데이터가 들어오는데(10초 안) 위치를 못 잡은 셀. 실시간 파일에 없는 셀은 여기 들지 않는다 — 독 셀을 셀 때는 locked() 를 쓴다."""
    c = qc_now().get("cells") or {}
    return sorted(int(k) for k, x in c.items() if x.get("age_s") is not None and x["age_s"] < 10 and x.get("fix") not in FIX_MODES)


def locked() -> list[int]:
    """실시간 데이터가 들어오고(10초 안) 위치를 잡은 셀(byte 47 이 FIX_MODES). 이 목록에 없는 독 셀은 못 잡은 것이다(2026-11-25)."""
    c = qc_now().get("cells") or {}
    return sorted(int(k) for k, x in c.items() if x.get("age_s") is not None and x["age_s"] < 10 and x.get("fix") in FIX_MODES)


def reboot(serials: list[int], say) -> dict:
    """측정으로 돌아가는 재부팅(0x26) — 상태 묻기 뒤에 보낸다. 장면이 2026 → 2023 으로 뒤로 가면 셀이 위치를 오래 못 잡는데
    (2026-10-30 · 11-02 실측), 원격 재부팅으로 바로 다시 잡았다(11-02 16:55, 19대). 라이브 포트를 쓰므로 tap 을 멈춘 뒤 부른다.
    물은 셀마다의 상태 응답 {셀: CellResult}(배터리 · 저장 크기 size)를 돌려준다 — 시험 전 저장량 확인(storage_ready)이 쓴다."""
    from dataclasses import replace
    from cellbench.cells import CellLink, LiveListener
    from cellbench.config import Config
    cfg = replace(Config(), serials=serials)
    lv = LiveListener(cfg); lv.start(); time.sleep(5)
    try:
        link = CellLink(cfg, lv, log=lambda m: None)
        res = link.status([s for s in serials if s in lv.alive()])
        # 측정으로 돌아오지 않은 셀은 '연결 기다림(0x16)'만 보내며 멈춘다 — 30초 뒤 한 번 더 깨운다(2026-11-19 10692: 16:11 재부팅 뒤
        # 1시간 넘게 멈춰 있다가 상태 묻기 한 번으로 19초 만에 돌아왔다). 0x16 에 든 셀 번호로 주소를 배우므로 라이브가 끊긴 셀도 닿는다.
        stuck = sorted(s for s, r in res.items() if r.resume_s is None)
        if stuck:
            time.sleep(30)
            again = link.status([s for s in stuck if s in lv.snapshot()])
            res.update({s: r for s, r in again.items() if r.resume_s is not None})
            say(f"측정으로 돌아오지 않은 셀 {stuck} — 한 번 더 깨움 · 돌아옴 {sorted(s for s, r in again.items() if r.resume_s is not None)}")
    finally:
        lv.stop()
    say(f"원격 재부팅 {len(res)}대 — 측정 복귀 {sum(1 for r in res.values() if r.resume_s is not None)}대")
    return res


def storage_ready(layout: str, say) -> dict:
    """저장량 확인 — 시험 실행기가 첫 시험 전에, 실시간 기록(live_tap)을 켜기 전에 부른다(같은 라이브 포트를 쓴다). 2026-11-23 추가.
    셀 저장이 가득 차면(약 243 MB, 추정 — rail_cells.FULL_MB) 측정과 실시간 데이터가 멈춘다. 실행기는 회차가 끝날 때만 셀 기록을 받으므로, 시험 사이에 며칠 쉬면 가득 찬 채로
    시작할 수 있다(11-20 19:45 뒤 70시간 비우지 않은 채 시작하려다 셀이 빨간불로 깜빡였다).
      1. 배치 세트에서 실시간 데이터가 들어오는 셀마다 상태(저장 크기)를 한 번 묻는다 — reboot 와 같은 길이다. 물으면 셀이 측정으로 돌아가는 재부팅을 해
         셀당 약 20초 측정이 멈추지만, 이 뒤에 위치 잡기 · 안정 대기가 오므로 시험에는 닿지 않는다. 안 돌아온 셀은 30초 뒤 한 번 더 깨운다.
      2. rail_cells.WARN_MB(120 MB) 이상인 셀, 앞에서 지우지 못한 셀(cells_not_cleared.json), 상태를 못 읽은 셀만 받으면서 비운다.
         지우기에 응답하지 않는 펌웨어(rail_cells.NO_DELETE)는 받아도 남으므로 받지 않고 경고만 적는다 — 시험마다 같은 기록을 다시 받지 않게.
      3. 받은 뒤에도 기록이 남은 셀이 있으면 시험을 시작하지 않고 멈춘다.
    실시간 데이터가 끊긴 셀(꺼짐 · 독 밖 · 가득 참)은 저장량을 물을 수도 받을 수도 없다. 가득 찬 셀도 실시간 데이터가 끊긴다(2026-11-23
    메인 세션 관찰: 70시간 비우지 않은 뒤 24대 중 7대가 실시간 데이터를 멈췄고 빨간불로 깜빡였다 — 상한 때문인지는 확정 아님).
    독 밖에 꺼 둔 셀과 구별할 수 없어 멈추지 않고, 가득 참 가능성과 할 일을 실행 기록에 적는다. 배치-12 는 뒤의 독 위 대수 확인
    (rail_prep 의 dock_count_ok · IMU-정지의 --min-cells)이 빠진 셀을 잡아 멈춘다.
    비울 셀이 없으면 셀 기록은 그대로 두고 재부팅만 된다. summary.json 에 적을 {warn_mb, mb, no_live, cleared, ftg} 를 돌려준다."""
    name = layout.split("-")[0]
    warn = rail_cells.WARN_MB
    want = rail_cells.serials(name)
    say(f"저장량 확인 — {name} 세트 셀마다 상태를 묻는다(물으면 셀이 측정으로 돌아가는 재부팅을 한다, 셀당 약 20초)")
    try:
        res = reboot(want, say)
    except rail_cells.PortBusy as e:
        msg = f"저장량 확인 — 라이브 포트를 다른 프로그램이 쓰고 있어 셀에 물을 수 없다. 다른 시험(실시간 기록)이 도는지 본다 ({e})"
        say(msg); raise SystemExit(msg)
    mb = {s: None if r.size is None else r.size / rail_cells.MB for s, r in sorted(res.items())}
    left = rail_cells.not_cleared()
    gone = sorted(set(want) - set(mb))
    say(f"저장량 확인 — 셀마다 저장 MB({warn:.0f} MB 이상은 시험 전에 비운다): "
        + (" · ".join(f"{s} {'?' if v is None else f'{v:.1f}'}" for s, v in mb.items()) or "실시간 데이터가 들어오는 셀 없음"))
    if gone:
        say(f"저장량 확인 — 실시간 데이터가 끊긴 셀 {gone}: 꺼졌거나 독 밖이거나, 저장이 가득 차(약 {rail_cells.FULL_MB:.0f} MB) 실시간 데이터를 멈췄다"
            + (f" · 그중 앞에서 지우지 못한 셀 {[s for s in gone if str(s) in left]}" if any(str(s) in left for s in gone) else "")
            + " — 이 셀들은 저장량을 물을 수도 받을 수도 없다. 독에 꽂힌 셀이 빨간불로 깜빡이면 가득 찬 것일 수 있다."
              " 껐다 켜 실시간 데이터가 들어오면 다시 띄운다(그때 이 단계가 받고 비운다)")
    doc = {"warn_mb": warn, "mb": {str(s): None if v is None else round(v, 1) for s, v in mb.items()}, "no_live": gone, "cleared": [], "ftg": None}
    # 기종 대조(2026-11-25) — 상태를 물을 때 받은 접속 인사말의 기종 · 펌웨어 앞자리를 기종 등록부와 견준다(새 접속은 하지 않는다).
    # 다르면 그 셀은 이번 시험에서 판정 불가(기종 확인 필요) — 실행기가 summary 의 storage.model_mismatch 를 읽는다
    models = cell_models.load()
    doc["hello"] = {str(s): r.hello for s, r in sorted(res.items()) if r.hello}
    doc["model_mismatch"] = {str(s): w for s, r in sorted(res.items()) if (w := cell_models.hello_mismatch(s, r.hello, models))}
    if doc["model_mismatch"]:
        say("저장량 확인 — 인사말의 기종 · 펌웨어가 기종 등록부와 다른 셀(이번 시험 판정 불가): "
            + " · ".join(f"{s}({w})" for s, w in doc["model_mismatch"].items())
            + " — 셀 기록을 받아 tools/qc_tests/cell_models_fill.py 로 등록부를 고친 뒤 판정한다")
    need = [s for s, v in mb.items() if s not in rail_cells.NO_DELETE and (v is None or v >= warn or str(s) in left)]
    if kept := [s for s, v in mb.items() if s in rail_cells.NO_DELETE and v is not None and v >= warn]:
        say(f"저장량 확인 — {kept} 는 지우기에 응답하지 않는 펌웨어라 비울 수 없다(받아도 남는다) — "
            f"{rail_cells.FULL_MB:.0f} MB 에서 그 셀만 측정이 멈춘다")
    if not need:
        say(f"저장량 확인 끝 — 실시간 데이터가 들어오는 셀 {len(mb)}대 모두 {warn:.0f} MB 아래, 비울 셀 없음")
        return doc
    why = lambda s: "상태 못 읽음" if mb[s] is None else f"{mb[s]:.0f} MB" if mb[s] >= warn else "앞에서 지우지 못함"
    say("저장량 확인 — 시험 전에 받고 비운다: " + " · ".join(f"{s}({why(s)})" for s in need))
    try:
        out, got = rail_cells.extract_cells(name, say, only=need)
    except rail_cells.PortBusy as e:
        msg = f"저장량 확인 — 라이브 포트를 다른 프로그램이 쓰고 있어 받지 못했다 ({e})"
        say(msg); raise SystemExit(msg)
    doc.update(cleared=need, ftg=str(out))
    still = [s for s in need if s not in got or rail_cells.unclear(got[s])]       # 받을 때 실시간 데이터가 끊겨 받지 못한 셀도 남은 셀이다
    if still:
        msg = (f"저장량 확인 — 받고 비운 뒤에도 기록이 남은 셀 {still} — 시험을 시작하지 않는다"
               f" ({out.name}/extract_result.json). 그 셀을 껐다 켜거나 독에서 빼고 끈 뒤 다시 띄운다")
        say(msg); raise SystemExit(msg)
    say(f"저장량 확인 끝 — {len(need)}대 받고 비움 → {out.name}")
    return doc


def judge(cmd: list[str], say) -> int:
    say("판정: " + " ".join(cmd[2:5]))
    r = subprocess.run([PY310, "-X", "utf8", *cmd], cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in (r.stdout + r.stderr).splitlines()[-12:]:
        say("   " + line)
    return r.returncode


def lock_ready(test: str, minutes: float, out: Path, say, need: int = LOCK_MIN):
    """실시간 기록(tap)을 켜고 셀이 위치를 잡을 때까지 기다린다. 5분 안에 안 되면 못 잡은 셀만 원격 재부팅하고 다시 기다린다.
    need 는 위치를 잡아야 하는 대수 — 독 일부만 쓰는 배치(배치-12)는 그 대수로 낮춘다. 20대로 두면 12대 배치는
    20분을 기다린 뒤 멈춘다(2026-11-17 10:30)."""
    log = out / "tap.log"
    tap = start_tap(test, minutes + 2 * LOCK_WAIT_S / 60 + 5, log)
    time.sleep(8)
    if wait_lock(say, need=need, max_s=300):
        return tap
    bad = unlocked()
    stop_tap(tap)
    say(f"5분 지나도 못 잡은 셀 {bad} — 원격 재부팅")
    reboot(bad, say)
    tap = start_tap(test, minutes + LOCK_WAIT_S / 60 + 5, log)
    time.sleep(8)
    if not wait_lock(say, need=need):
        stop_tap(tap); charge.plug_set(True, say)
        raise SystemExit(f"재부팅 뒤에도 위치를 못 잡아 멈춘다 — 못 잡은 셀 {unlocked()} 를 껐다 켠다")
    return tap


def alive_cells() -> list[int]:
    """지금 들리는 셀(10초 안에 라이브를 보낸 셀) 번호."""
    c = qc_now().get("cells") or {}
    return sorted(int(k) for k, x in c.items() if x.get("age_s") is not None and x["age_s"] < 10)


def dock_count_ok(expect: int | None, say) -> list[int]:
    """독 위 셀 수 확인 — 플러그를 끈 뒤(배터리로) 들리는 셀이 배치의 대수와 같아야 시작한다. 다르면 모터 전에 멈추고 플러그를 켠다.
    2026-11-16 17:14 배치-12(--rows 1,3)로 띄웠는데 독 위에 23대가 있었고 실행기는 그대로 돌았다(GNSS-MOVE_20261116check5b).
    독 밖에 켜 둔 셀도 들리므로, 독 밖 셀을 꺼 두지 않으면 여기서 멈춘다. expect 가 None(독 전체)이면 세기만 하고 막지 않는다."""
    got = alive_cells()
    say(f"들리는 셀 {len(got)}대" + (f" — 배치는 {expect}대" if expect else "") + f" · {got}")
    if expect and len(got) != expect:
        charge.plug_set(True, say)
        raise SystemExit(f"들리는 셀이 {len(got)}대인데 배치는 {expect}대다 — 독 밖 셀을 끄거나 독에서 빼고, 빠진 셀은 꽂은 뒤 다시 띄운다")
    return got


def battery_ok(say) -> None:
    low, nc, why = charge.rail_low()
    if (prob := charge.start_problem(low, why)):
        charge.plug_set(True, say); raise SystemExit(prob)
    say(f"가장 낮은 셀 {low}% ({nc}대)")


def layout_columns(name: str) -> list[list[int]]:
    """독 배치 이름 → 열 목록. motionbench/dock_layout.json 과 qc_tests/dock_layout_a.json(Y-shift · Y-shift-b · Y-rot)을 함께 본다."""
    lay = json.loads((ROOT / "motionbench" / "dock_layout.json").read_text(encoding="utf-8"))
    extra = ROOT / "tools" / "qc_tests" / "dock_layout_a.json"
    if extra.exists():
        lay |= json.loads(extra.read_text(encoding="utf-8"))
    if name not in lay:
        raise SystemExit(f"독 배치 {name!r} 가 없다 — {', '.join(sorted(lay))}")
    return lay[name]["columns"]


def record_dir(test: str, tag: str = "", day: str | None = None) -> Path:
    """시험 기록 폴더 — data/<시험 ID>_<날짜><꼬리표>. 2026-11-12 전 폴더(collectA_ · railC_ · railB_ …)는 이름을 바꾸지 않고 그대로 둔다."""
    out = DATA / f"{test}_{day or time.strftime('%Y%m%d')}{tag}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def hold_loop_lock():
    """수집 실행기 잠금(charge.LOOP_LOCK)을 쥔다 — 쥐고 있는 동안 충전 유지(charge keep)가 플러그를 다시 켜지 않는다.
    프로그램이 끝나면 저절로 풀린다. 다른 실행기가 쥐고 있으면 멈춘다(두 시험이 함께 플러그를 다루지 않게)."""
    from cellbench import proc
    lock = proc.acquire_lock(charge.LOOP_LOCK)
    if lock is None:
        raise SystemExit(f"수집 실행기 잠금({charge.LOOP_LOCK.name})을 다른 프로그램이 쥐고 있다 — 다른 시험이 도는지 본다")
    return lock


_round: list = []      # rail_prep 이 쥔 레일 라운드 잠금 — rail_finish 가 놓는다


def rail_prep(tests: list[str], minutes: float, out: Path, say, expect: int | None = None,
              labsat: bool = True) -> tuple[LabSat | None, subprocess.Popen, dict]:
    """레일 시험(GNSS-MOVE · IMU-MOVE · IMU-FW)의 공통 준비 — 한 번만 한다.
    PC 전역 레일 라운드 잠금(motionbench/drive_lock.py)을 먼저 쥐고 → 플러그 끔(배터리로 시험) → 정지 장면 · 감쇠 0
    → 실시간 기록 켜고 위치 잡기(못 잡은 셀만 재부팅) → 독 위 셀 수 확인(expect 대) → 배터리 확인.
    expect 는 배치가 정한 독 위 대수(배치-12 면 12) — 위치 잡기 기준도 그 대수로 낮춘다. 잠금은 rail_finish 까지 쥔다 — 시험과 시험 사이
    (GNSS-MOVE 장면 바꾸기 · 위치 다시 잡기)에도 다른 체크아웃의 프로그램이 드라이브에 붙지 못한다. 이미 누가 쥐고 있으면
    플러그 · LabSat 을 건드리기 전에 멈춘다.
    labsat=False 면 LabSat 을 건드리지 않는다 — GPS 중계기 날(신호-중계기, IMU-MOVE 만, 2026-11-20). 그때 LabSat 자리는 None 이다.
    (LabSat, 실시간 기록, 플러그 응답)을 돌려준다. 끝나면 rail_finish 로 정지 장면으로 되돌리고 실시간 기록을 끈다."""
    if bad := [t for t in tests if t not in qc_ids.RAIL]:
        raise SystemExit(f"레일 시험이 아니다: {bad} — 레일 준비는 {', '.join(qc_ids.RAIL)} 만")
    if not labsat and (need := [t for t in tests if t != "IMU-MOVE"]):
        raise SystemExit(f"신호-중계기로는 IMU-MOVE 만 돈다 — {need} 는 LabSat 장면이 필요하다")
    held = drive_lock.take_round(f"레일 준비 {qc_ids.join(tests)}")
    try:
        plug = charge.plug_set(False, say)
        ls = None
        if labsat:
            ls = LabSat()
            ensure_static(ls, say)
            say(f"감쇠 0 으로 → {clean(ls._one('ATTN:00'))} · LabSat {ls.status()}")
        else:
            say("신호원 GPS 중계기 — LabSat 은 건드리지 않는다")
        tap = lock_ready(",".join(tests), minutes, out, say, need=min(LOCK_MIN, expect) if expect else LOCK_MIN)
        try:
            dock_count_ok(expect, say)
            battery_ok(say)
        except BaseException:
            stop_tap(tap)
            raise
    except BaseException:
        held.release()
        raise
    _round.append(held)
    return ls, tap, plug


def rail_finish(ls: LabSat | None, tap, say) -> None:
    """레일 시험 마무리 — 정지 장면으로 되돌리고(이미 정지 장면이면 건드리지 않는다) 실시간 기록을 끈다. 레일 라운드 잠금을 놓는다.
    ls 가 None 이면(신호-중계기) LabSat 은 건드리지 않는다."""
    try:
        try:
            if ls is not None:
                ensure_static(ls, say)       # 다시 틀면 셀이 위치를 잃는다(2026-11-02 20:29) — 정지 장면이면 그대로 둔다
        except Exception as e:
            say(f"정지 장면 되돌리기 실패 {e}")
        stop_tap(tap)
    finally:
        if _round:
            _round.pop().release()


def extract(layout: str, say) -> Path:
    """셀 기록 받기(받으면서 비우기) — 받은 폴더(data/ftg_y_<시각>)를 돌려준다. 실시간 기록을 끈 뒤에 부른다."""
    return rail_cells.extract_all(layout.split("-")[0], say)


def still_window(test: str, minutes: float, out: Path, ls: LabSat, say, summary: dict) -> dict:
    """셀 정지 기록 한 구간 — 플러그 끔 → 정지 장면 → 실시간 기록 켜고 위치 잡기 → 배터리 확인 → minutes 분 기록.
    기록 구간 {from, to}(PC 시각)를 돌려준다. 하루 수집 실행기(qc_loop)의 정지 구간이 쓴다 — 꽉 찬 독 · 감쇠 0 · 장면 이어 틀기.
    GNSS-STATIC 의 2026-11-11 확정 방식(1 · 3행 · 장면 구간 고정 · 감쇠 18 dB)은 tools/qc_tests/gnss_static_run.py 다."""
    summary[f"plug_off_{test}"] = charge.plug_set(False, say)
    ensure_static(ls, say)
    tap = lock_ready(test, minutes, out, say)
    try:
        battery_ok(say)
        t0 = time.time()
        say(f"{test} 정지 {minutes:g}분 시작")
        time.sleep(minutes * 60)
        say(f"{test} 정지 끝")
        return {"from": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)), "to": time.strftime("%Y-%m-%d %H:%M:%S")}
    finally:
        stop_tap(tap)


def judge_all(out: Path, dock: str, ftg: list[Path], speeds: list[int], still_span: dict | None, say) -> list[int]:
    """하루 수집 회차 판정 — 기준 없이(살펴봄만) out/judged/ 에 쓴다. 결과판 목록(data/ 바로 아래)을 수집 파일로 채우지 않는다.
    회차 폴더 안의 운전 폴더: IMU-FW_a<가속도>/ · RUN-CONST_v<속도>a8000/ (qc_loop 가 만든다. 위성-6 회차는 RUN-CONST_v<속도>/,
    2026-11-12 전 회차는 FIRMWARE-2_v<속도>/). GNSS-MOVE 정답은 runs.json 에 적힌 장면 이름으로 고른다. 정지 구간은 still_span.
    1차 기준은 이 파일들로 tools/qc_criteria.py 가 만든다."""
    rcs = []
    tail = ["--out", str(out / "judged"), "--criteria", "none", "--set", dock.split("-")[0]]   # --set 을 빼면 판정 도구 기본값 Y 가 적힌다(11-06 X 판정에서 발견)
    (out / "judged").mkdir(parents=True, exist_ok=True)
    pv4 = "cell-bench/tools/protocol_v4.py"
    if still_span:
        rcs.append(judge([pv4, "GNSS-STATIC", "--from", still_span["from"], "--to", still_span["to"], "--window", "300",
                          "--ftg", *map(str, ftg), "--scene", STATIC, "--dock", dock, *tail], say))
    for sub in sorted([*out.glob("IMU-FW_a*"), *out.glob("FIRMWARE-1_a*")]):     # 2026-11-13 전 바퀴는 FIRMWARE-1_a<가속도>/
        if (sub / "runs.json").exists():
            rcs.append(judge([pv4, "IMU-FW", "--runs", str(sub / "runs.json"), "--ftg", *map(str, ftg),
                              "--accel", sub.name.split("_a")[-1], "--dock", dock, *tail], say))
    for v in speeds:
        for name in (f"RUN-CONST_v{v}a8000", f"RUN-CONST_v{v}", f"GNSS-2_v{v}", f"FIRMWARE-2_v{v}"):
            rp = out / name / "runs.json"
            if not rp.exists():
                continue
            runs = json.loads(rp.read_text(encoding="utf-8"))
            scene = next((r["scene"] for r in runs if r.get("scene")), None) or CONST_SCENES_SAT6.get(v, ("",))[0]
            truth = scene_truth(scene)
            if truth is None:
                say(f"{name}: 장면 {scene!r} 의 정답 파일을 모른다 — 판정 건너뜀"); continue
            rcs.append(judge([pv4, "GNSS-MOVE", "--runs", str(rp), "--truth", str(truth), "--scene", scene, "--dock", dock,
                              *(["--ftg", *map(str, ftg)] if ftg else []), *tail], say))
    return rcs
