"""하루 수집 실행기 — python cell-bench/tools/qc_loop.py <배치 X · X-swap · Y …> <앞 바퀴 번호> [수집 폴더 이름]
한 바퀴 = FIRMWARE-1(50 mm/s² 10번) → FIRMWARE-2(300 · 100 · 500 mm/s 각 10번) → 셀 정지 30분(GNSS-1 로 판정).
처음 쓴 날: CLBX-6B(X 세트) 2026-11-05 — 이 순서를 멈추지 않고 반복한다(2026-11-05 사용자 지시).
정지 구간은 그때의 수집 조건 그대로다(꽉 찬 독 · 감쇠 0 · 장면 이어 틀기) — GNSS-1 의 2026-11-11 확정 방식(1 · 3행 · 장면 구간 고정 · 감쇠 18 dB)은
tools/qc_tests/gnss1_run.py 로 따로 돌린다. 기준 파일 rail_v1 의 GNSS-1 칸도 이 수집 조건의 기록으로 만들었다.
바퀴 폴더 r<번호>/ 안: FIRMWARE-1_a50/ · FIRMWARE-2_v300/ · _v100/ · _v500/ (운전 기록), summary.json 의 "GNSS-1"(정지 구간), judged/(판정 파일).
세션과 따로 돈다. 멈추려면 <수집 폴더>/STOP 파일을 만든다 — 지금 바퀴를 마치고 받기 · 판정 뒤 끝난다.
- 플러그: 지그비 플러그(아카라 + ESP32-C6, 2026-11-09)를 PC 가 다룬다. 시험 동안은 끄고, 잔량이 35 % 아래면 바퀴 앞에서
  플러그를 켜고 셀이 85 % 이상으로 다시 들릴 때까지 기다린다(charge.charge_until — 충전 중엔 셀이 라이브를 안 보내 20분마다 잠깐 끄고 본다).
  11-05 ~ 11-09 에는 Tapo 를 PC 가 못 다뤄(TPAP) 플러그 호출을 건너뛰고 사람이 앱으로 충전했다.
- FIRMWARE-2 는 속도마다 gps_ready(5분까지, 90초에 못 잡은 셀 재부팅). 회차 실패는 다음 회차로 넘어간다.
- 셀 기록은 두 바퀴마다 정지 구간 뒤에 받고(받으면서 비우기) 그 바퀴들을 판정한다(기준 없이 살펴봄 — judge_all).
- 시작 전에 남은 공간을 본다(archive_raw.ensure_space) — 50 GB 아래면 7일 지난 원값을 날짜별 zip 으로 구글 드라이브에 옮기고,
  그래도 20 GB 아래면 시작하지 않는다."""
import sys, time, json, traceback
from pathlib import Path
ROOT = Path(r"C:\Dev\dynamic-bench\cell-bench"); sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT))
import qc_round as Q
from motionbench import wifi
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
DOCK = ARGS[0] if len(ARGS) > 0 else "X"      # 자리 바꾼 뒤에는 X-swap
K0 = int(ARGS[1]) if len(ARGS) > 1 else 0     # 이어서 띄울 때 앞 바퀴 번호
OUT = Q.DATA / (ARGS[2] if len(ARGS) > 2 else time.strftime("collect_%Y%m%d"))   # 수집 폴더 — 기본은 오늘 날짜
# --no-reboot: FIRMWARE-2 장면을 바꿀 때 원격 재부팅하지 않고 10분까지 기다린다. CLBX-5E 는 재부팅 뒤 23대 중 6대만 측정으로 돌아왔다(2026-11-06 r01 300 mm/s).
NO_REBOOT = "--no-reboot" in sys.argv
GPS_KW = dict(reboot_after_s=1e9, max_s=600) if NO_REBOOT else {}
RAIL_TESTS = "FIRMWARE-1,FIRMWARE-2"           # 실시간 기록에 적을 시험 ID — 정지 구간은 still_window 가 GNSS-1 로 따로 켠다
STOP = OUT / "STOP"
say = Q.Log(OUT / "loop.log")
Q.TAP_LAYOUT = DOCK
from cellbench import proc  # noqa: E402
LOOP = proc.acquire_lock(Q.charge.LOOP_LOCK)   # 도는 내내 쥔다 — 그동안 충전 유지(charge.py keep)는 플러그에서 손을 뗀다
if LOOP is None:
    raise SystemExit(f"수집 실행기가 이미 돈다({Q.charge.LOOP_LOCK.name}) — 하나만 띄운다")
import archive_raw  # noqa: E402  — 남은 공간이 50 GB 아래면 오래된 원값을 구글 드라이브로 옮긴다(2026-11-10, C 드라이브 8.5 GB 까지 참)
if not archive_raw.ensure_space(say=say):
    raise SystemExit("남은 공간이 모자라 수집을 시작하지 않는다 — loop.log 를 본다")
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
    tap = Q.start_tap(RAIL_TESTS, 150, out / "tap.log"); time.sleep(10)
    try:
        wait_charged()
        Q.wait_lock(say, need=20, max_s=300)
        rc = Q.run_rail.main(["FIRMWARE-1", "--n", "10", "--out", str(out / "FIRMWARE-1_a50"), "--preset", "FIRMWARE-1_a50"])
        say(f"FIRMWARE-1 50 mm/s² 끝 · 코드 {rc}")
        for v in (300, 100, 500):
            scene, truth, preset = Q.FW2_SCENES[v]
            got, tap = Q.gps_ready(ls, scene, say, tap, RAIL_TESTS, out, 150, **GPS_KW)
            if got is None:
                say(f"FIRMWARE-2 {v} mm/s 건너뜀 — GPS 를 못 잡았다"); continue
            rc = Q.run_rail.main(["FIRMWARE-2", "--n", "10", "--out", str(out / f"FIRMWARE-2_v{v}"), "--scene", scene, "--preset", preset])
            say(f"FIRMWARE-2 {v} mm/s 끝 · 코드 {rc}")
    finally:
        Q.ensure_static(ls, say); Q.stop_tap(tap)
    summary = {}
    summary["GNSS-1"] = Q.still_window("GNSS-1", 30, out, ls, say, summary)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def extract_and_judge(rounds: list[Path]) -> None:
    ftg = Q.rail_cells.extract_all(DOCK.split("-")[0], say); say(f"받기 끝 → {ftg}")
    for out in rounds:
        span = json.loads((out / "summary.json").read_text(encoding="utf-8")).get("GNSS-1")
        rcs = Q.judge_all(out, DOCK, [ftg], [300, 100, 500], span, say); say(f"{out.name} 판정 끝 · {rcs}")


say(f"하루 수집 시작 — 배치 {DOCK} · FIRMWARE-1 → FIRMWARE-2 → 정지(GNSS-1) 반복 (멈추려면 STOP 파일)" + (" · 원격 재부팅 끔" if NO_REBOOT else ""))
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
say("하루 수집 끝")
