"""IMU-STATIC · IMU 정지 시험 실행기 — 셀을 독에 둔 채 원격 재부팅을 여러 번 하고, 부팅마다 정지 구간을 기록한다. 레일은 움직이지 않는다.
부팅마다: 실시간 기록 끔 → 실시간 데이터가 들어오는 셀 재부팅(판정 불가 셀은 뺀다) → 실시간 기록 켬 → 독 셀이 모두 위치를 잡을 때까지 기다림
→ 안정 대기 → 기록 구간. 끝나면 셀 기록을 받는다(받으면서 비우기).
셀 시각을 지키는 조건(시험 문서 1-2절 '지킬 것', 2026-11-25 실행기에 넣음): ① 기록 전에 독 셀이 모두 위치를 잡는다 — 90초 뒤 못 잡은 셀만
재부팅, 그 뒤 4분마다, 최대 12분(GNSS-STATIC 과 같은 lock_dock). 잡은 셀이 --min-cells 아래면 멈춘다 ③ 부팅마다 기록을 시작할 때 셀마다
위치 상태 · 위성 수를 summary.json 의 boots[].gps 에, 첫 부팅 값을 gps_start 에 남긴다. LabSat 은 여전히 건드리지 않는다.
운전 부분(drive)은 묶음 실행기(qc_bundle.py)도 부르고, 함께 돌리면 늘 맨 앞이다(레일 준비 전에 재부팅을 끝낸다).
결과: data/IMU-STATIC_<날짜>[꼬리표]/{summary.json(boots = 부팅마다 기록 구간), run.log, tap.log}, 받은 셀 기록 data/ftg_y_<시각>
판정: imu_static_judge.py IMU-STATIC_<날짜>[꼬리표] (부팅마다 중력 크기 · 자이로스코프 영점 오차)
사용: python -X utf8 imu_static_run.py [--boots 6] [--settle-min 3] [--rec-min 5] [--min-batt 50] [--min-cells 20] [--layout Y-shift] [--tag 꼬리표] [--no-extract]"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_common as RC  # noqa: E402
import gnss_static_run as G  # noqa: E402  (독 셀 위치 잡기 lock_dock — GNSS-STATIC 과 같은 기다림)
from motionbench import cell_models  # noqa: E402

TEST = "IMU-STATIC"


def add_options(ap) -> None:
    ap.add_argument("--boots", type=int, default=6, help="IMU-STATIC 원격 재부팅 횟수(기본 6)")
    ap.add_argument("--settle-min", type=float, default=3.0, help="IMU-STATIC 부팅 뒤 안정 대기 분(기본 3)")
    ap.add_argument("--rec-min", type=float, default=5.0, help="IMU-STATIC 부팅마다 기록 분(기본 5)")
    ap.add_argument("--min-batt", type=float, default=50.0, help="IMU-STATIC 시작할 때 가장 낮은 배터리 %%(기본 50)")
    ap.add_argument("--min-cells", type=int, default=12,
                    help="IMU-STATIC 시작할 때 실시간 데이터가 들어와야 하는 셀 수 · 부팅마다 위치를 잡아야 하는 독 셀 수(기본 12 — 배치-12)")


def check(a) -> None:
    pass


def conditions(a) -> dict:
    """이 실행기가 실제로 쓰는 조건 모듈 값(qc-tests.md 1-1절). 모터를 쓰지 않고, LabSat 은 건드리지 않는다 —
    신호원 · 위성 · GPS 움직임 · 감쇠는 그때 LabSat 에 걸려 있던 상태다(혼자 돌리면 앞 시험이 남긴 상태, 묶음에서는 레일 준비 전)."""
    left = "실행기가 바꾸지 않음 — 그때 LabSat 에 걸린 상태"
    return RC.qc_ids.conditions(run="RUN-STILL", src=left, sat=left, gps=left, att=left, dock="DOCK-12")


def gps_snapshot(serials: list[int]) -> dict[str, dict]:
    """지금 실시간 파일(qc_now)에서 셀마다 {fix: 위치 상태 byte 47, sats: 위성 수, age_s} — 조건이 아니라 실제 값(지킬 것 ③).
    실시간 데이터가 10초 넘게 끊긴 셀은 fix · sats 를 None 으로 둔다."""
    cells = RC.Q.qc_now().get("cells") or {}
    out = {}
    for x in serials:
        c = cells.get(str(x)) or {}
        fresh = c.get("age_s") is not None and c["age_s"] < 10
        out[str(x)] = {"fix": c.get("fix") if fresh else None, "sats": c.get("sats") if fresh else None, "age_s": c.get("age_s")}
    return out


def unjudgeable(rec, cells: list[int]) -> dict[int, str]:
    """판정 불가 셀 {셀: 이유} — 기종 등록부 · 형식 표에 없는 셀(셀 번호 1 등)과 저장량 확인 인사말이 등록부와 다른 셀.
    이 셀들은 원격 재부팅 · 위치 잡기 기다림에서 뺀다(시험 문서 2-1절 — 시료 CLBY-2A 는 재부팅 뒤 측정으로 돌아오지 않는다)."""
    unj = cell_models.unjudgeable(cells)
    for k, w in ((rec.summary.get("storage") or {}).get("model_mismatch") or {}).items():
        if int(k) in cells and int(k) not in unj:
            unj[int(k)] = f"{cell_models.NEED_MODEL} — {w}"
    return unj


def drive(rec, say, a) -> int:
    """플러그 끔 → 실시간 데이터가 들어오는 셀 · 배터리 보기 → 부팅 a.boots 번(재부팅 · 위치 잡기 · 안정 · 기록). 셀 기록 받기는 하지 않는다. 코드를 돌려준다."""
    Q = RC.Q
    s = rec.summary
    s.update({"boots": [], "settle_min": a.settle_min, "rec_min": a.rec_min})
    say(f"── {TEST} · 부팅 {a.boots}번 · 위치 잡기 + 안정 {a.settle_min:g}분 + 기록 {a.rec_min:g}분")
    s["plug_off"] = Q.charge.plug_set(False, say)
    tap = Q.start_tap(TEST, 2, rec.out / "tap.log")
    time.sleep(50)
    cells = Q.qc_now().get("cells") or {}
    heard = sorted(int(k) for k, x in cells.items() if x.get("age_s") is not None and x["age_s"] < 10)
    batt = {int(k): x.get("battery") for k, x in cells.items() if int(k) in heard}
    low = min((b for b in batt.values() if b is not None), default=None)
    Q.stop_tap(tap)
    say(f"실시간 데이터가 들어오는 셀 {len(heard)}대 {heard} · 가장 낮은 배터리 {low}%")
    s["heard"] = heard
    s["battery_start"] = batt
    dock = sorted({x for col in Q.layout_columns(a.layout) for x in col if x})
    unj = unjudgeable(rec, sorted(set(heard) | set(dock)))
    s["model_check"] = {str(x): w for x, w in sorted(unj.items())}
    if unj:
        say(f"판정 불가(기종 확인 필요) {len(unj)}대 — 기록은 남기되 원격 재부팅 · 위치 잡기 기다림에서 뺀다: "
            + " · ".join(f"{x}({w})" for x, w in sorted(unj.items())))
    boot = [x for x in heard if x not in unj]
    # 위치 잡기를 기다릴 셀 — 독 배치에 있고 실시간 데이터가 들어오는 셀. 독 배치 밖에서 들어오는 셀은 기다리지 않는다
    wait_for = [x for x in boot if x in dock] if dock else boot
    missing = [x for x in dock if x not in heard and x not in unj]
    if missing:
        say(f"독 배치({a.layout})에 있는데 실시간 데이터가 끊긴 셀 {missing} — 기다리지 않는다")
    s["gps_wait_for"] = wait_for
    rec.save()
    if len(heard) < a.min_cells:
        raise SystemExit(f"실시간 데이터가 들어오는 셀이 {len(heard)}대뿐(필요 {a.min_cells}) — 멈춘다")
    if low is not None and low < a.min_batt:
        raise SystemExit(f"가장 낮은 배터리 {low}% < {a.min_batt:g}% — 충전 뒤 다시")
    tap_min = a.settle_min + a.rec_min + G.LOCK_MAX_S / 60 + 3
    for k in range(1, a.boots + 1):
        t_rb = time.time()
        Q.reboot(boot, say)
        tap = Q.start_tap(TEST, tap_min, rec.out / "tap.log")
        time.sleep(8)
        last_rb = [time.time()]

        def restart_tap(bad, old):
            Q.stop_tap(old)
            Q.reboot(bad, say)
            last_rb[0] = time.time()
            new = Q.start_tap(TEST, tap_min, rec.out / "tap.log")
            time.sleep(8)
            return new

        bad, tap, took = G.lock_dock(wait_for, tap, restart_tap, say)
        locked = len(wait_for) - len(bad)
        say(f"부팅 {k}/{a.boots} · 독 셀 위치 잡기 {locked}/{len(wait_for)} ({took:.0f}초)" + (f" · 못 잡은 셀 {bad}" if bad else ""))
        if locked < a.min_cells:
            Q.stop_tap(tap)
            s["boots"].append({"k": k, "reboot_at": t_rb, "lock_s": round(took, 1), "unlocked": bad, "stopped": True})
            rec.save()
            raise SystemExit(f"부팅 {k} — 위치를 잡은 독 셀이 {locked}대(필요 {a.min_cells}) · 못 잡은 셀 {bad} — 셀 시각을 "
                             "믿을 수 없어 멈춘다(신호원이 켜져 있는지 본다)")
        rest = max(a.settle_min * 60 - (time.time() - last_rb[0]), 0)   # 안정 대기는 마지막 재부팅부터 센다
        say(f"부팅 {k} 안정 {rest / 60:.1f}분 더")
        time.sleep(rest)
        t0 = time.time()
        gps = gps_snapshot(sorted(set(heard) | set(dock)))
        if k == 1:
            s["gps_start"] = gps
        say(f"부팅 {k} 기록 {a.rec_min:g}분 시작 · 셀마다 위치 상태/위성 수: "
            + " · ".join(f"{x} {g['fix']}/{g['sats']}" for x, g in gps.items()))
        time.sleep(a.rec_min * 60)
        t1 = time.time()
        cells = Q.qc_now().get("cells") or {}
        alive = sorted(int(x) for x, c in cells.items() if c.get("age_s") is not None and c["age_s"] < 10)
        Q.stop_tap(tap)
        s["boots"].append({"k": k, "reboot_at": t_rb, "from": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
                           "to": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t1)), "alive": alive,
                           "lock_s": round(took, 1), "unlocked": bad, "gps": gps})
        rec.save()
        say(f"부팅 {k} 끝 · 기록 중 실시간 데이터가 들어온 셀 {len(alive)}대")
    say(f"{TEST} 끝 · 플러그는 꺼 둔 채로 둔다(다음 레일 시험도 배터리로)")
    return 0


def main(argv=None) -> int:
    return RC.run([TEST], argv, __doc__)


if __name__ == "__main__":
    raise SystemExit(main())
