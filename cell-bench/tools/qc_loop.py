"""하루 수집 실행기 — python cell-bench/tools/qc_loop.py <배치 X · X-swap · Y …> <앞 바퀴 번호> [수집 폴더 이름]
처음 쓴 날: CLBX-6B(X 세트) 2026-11-05 — C 10번 → B(300 · 100 · 500 각 10번) → A 30분 을 한 바퀴로(Y 와 같은 순서, 1바퀴만 B → C → A) 멈추지 않고 반복한다(2026-11-05 사용자 지시).
세션과 따로 돈다. 멈추려면 <수집 폴더>/STOP 파일을 만든다 — 지금 바퀴를 마치고 받기 · 판정 뒤 끝난다.
- 플러그: 지그비 플러그(아카라 + ESP32-C6, 2026-11-09)를 PC 가 다룬다. 시험(C · B · A) 동안은 끄고, 잔량이 35 % 아래면 바퀴 앞에서
  플러그를 켜고 셀이 85 % 이상으로 다시 들릴 때까지 기다린다(charge.charge_until — 충전 중엔 셀이 라이브를 안 보내 20분마다 잠깐 끄고 본다).
  11-05 ~ 11-09 에는 Tapo 를 PC 가 못 다뤄(TPAP) 플러그 호출을 건너뛰고 사람이 앱으로 충전했다.
- B 는 속도마다 gps_ready(5분까지, 90초에 못 잡은 셀 재부팅). 회차 실패는 다음 회차로 넘어간다.
- 셀 기록은 두 바퀴마다 A 뒤에 받고(받으면서 비우기) 그 바퀴들을 판정한다(기준 rail_v1, 1차 제안값)."""
import sys, time, types, json, traceback
from pathlib import Path
ROOT = Path(r"C:\Dev\dynamic-bench\cell-bench"); sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT))
import qc_round as Q
from motionbench import wifi
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
DOCK = ARGS[0] if len(ARGS) > 0 else "X"      # 자리 바꾼 뒤에는 X-swap
K0 = int(ARGS[1]) if len(ARGS) > 1 else 0     # 이어서 띄울 때 앞 바퀴 번호
OUT = Q.DATA / (ARGS[2] if len(ARGS) > 2 else time.strftime("collect_%Y%m%d"))   # 수집 폴더 — 기본은 오늘 날짜
# --no-reboot: B 장면을 바꿀 때 원격 재부팅하지 않고 10분까지 기다린다. CLBX-5E 는 재부팅 뒤 23대 중 6대만 측정으로 돌아왔다(2026-11-06 r01 B300).
NO_REBOOT = "--no-reboot" in sys.argv
GPS_KW = dict(reboot_after_s=1e9, max_s=600) if NO_REBOOT else {}
STOP = OUT / "STOP"
say = Q.Log(OUT / "loop.log")
Q.TAP_LAYOUT = DOCK
from cellbench import proc  # noqa: E402
LOOP = proc.acquire_lock(Q.charge.LOOP_LOCK)   # 도는 내내 쥔다 — 그동안 충전 유지(charge.py keep)는 플러그에서 손을 뗀다
if LOOP is None:
    raise SystemExit(f"수집 실행기가 이미 돈다({Q.charge.LOOP_LOCK.name}) — 하나만 띄운다")
ls = Q.LabSat()


def wait_charged():
    low, n, why = Q.charge.rail_low()
    if low is not None and low >= 35:
        return
    if low is None:                       # 셀이 하나도 안 들리면 먼저 링크를 본다 — 11-05 에 와이파이 끊김을 충전 중으로 잘못 읽었다
        while not (w := wifi.ensure(say))["ok"]:
            say(f"셀이 안 들린다 — 충전이 아니라 링크 문제: {w['why']}. 1분 뒤 다시 본다"); time.sleep(60)
        time.sleep(15); low, n, why = Q.charge.rail_low()
        if low is not None and low >= 35:
            return
    say(f"충전 필요 — 가장 낮은 셀 {low}% ({why or ''}). 플러그를 켜고 85 % 이상으로 다시 들리면 이어 간다")
    while not Q.charge.charge_until(85, 20, say):
        say("8시간 안에 다 차지 않았다 — 독과 플러그를 직접 본다. 30분 뒤 다시 시도"); time.sleep(1800)
    say("충전 끝 — 이어 간다")


def one_round(k: int) -> Path:
    out = OUT / f"r{k:02d}"; out.mkdir(parents=True, exist_ok=True)
    say(f"── {k}바퀴 시작 → {out.name}")
    Q.ensure_static(ls, say)
    tap = Q.start_tap("C", 150, out / "tap_C.log"); time.sleep(10)
    try:
        wait_charged()
        Q.wait_lock(say, need=20, max_s=300)
        rc = Q.run_rail.main(["C", "--n", "10", "--out", str(out / "C")]); say(f"C 끝 · 코드 {rc}")   # Y 와 같은 순서: C → B → A (1바퀴만 B → C → A 였다)
        for v in (300, 100, 500):
            scene, truth, preset = Q.B_SCENES[v]
            got, tap = Q.gps_ready(ls, scene, say, tap, "C", out, 150, **GPS_KW)
            if got is None:
                say(f"B{v} 건너뜀 — GPS 를 못 잡았다"); continue
            rc = Q.run_rail.main(["B", "--n", "10", "--out", str(out / f"B{v}"), "--scene", scene, "--preset", preset])
            say(f"B{v} 끝 · 코드 {rc}")
    finally:
        Q.ensure_static(ls, say); Q.stop_tap(tap)
    summary = {}
    Q.run_a(types.SimpleNamespace(a_min=30), out, ls, say, summary)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def extract_and_judge(rounds: list[Path]) -> None:
    ftg = Q.rail_cells.extract_all(DOCK.split("-")[0], say); say(f"받기 끝 → {ftg}")
    for out in rounds:
        a = json.loads((out / "summary.json").read_text(encoding="utf-8")).get("A")
        rcs = Q.judge_all(out, DOCK, [ftg], [300, 100, 500], a, say); say(f"{out.name} 판정 끝 · {rcs}")


say(f"X 세트 하루 수집 시작 — 배치 {DOCK} · C → B → A 반복 (멈추려면 STOP 파일)" + (" · 원격 재부팅 끔" if NO_REBOOT else ""))
k, pending = K0, []
while not STOP.exists():
    k += 1
    try:
        pending.append(one_round(k))
    except BaseException as e:
        say(f"{k}바퀴 중 예외 — {e!r} · 30초 뒤 다음 바퀴"); say(traceback.format_exc()[-600:]); time.sleep(30)
    if len(pending) >= 2 or STOP.exists():
        try:
            extract_and_judge(pending); pending = []
        except BaseException as e:
            say(f"받기 · 판정 예외 — {e!r} · 다음 바퀴 뒤 다시"); say(traceback.format_exc()[-600:])
if pending:
    try: extract_and_judge(pending)
    except BaseException as e: say(f"받기 · 판정 예외 — {e!r}")
say("X 세트 하루 수집 끝")
