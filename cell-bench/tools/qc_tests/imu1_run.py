"""IMU-1 · IMU 정지 시험 실행기 — 셀을 독에 둔 채 원격 재부팅을 여러 번 하고, 부팅마다 정지 구간을 기록한다. 모터 없음.
부팅마다: 실시간 기록 끔 → 들리는 셀 전부 재부팅 → 실시간 기록 켬 → 안정 대기 → 기록 구간. 끝나면 셀 기록을 받는다(받으면서 비우기).
운전 부분(drive)은 묶음 실행기(qc_bundle.py)도 부르고, 함께 돌리면 늘 맨 앞이다(레일 준비 전에 재부팅을 끝낸다).
결과: data/IMU-1_<날짜>[꼬리표]/{summary.json(boots = 부팅마다 기록 구간), run.log, tap.log}, 받은 셀 기록 data/ftg_y_<시각>
판정: imu1_judge.py IMU-1_<날짜>[꼬리표] (부팅마다 중력 크기 · 자이로스코프 영점 오차)
사용: python -X utf8 imu1_run.py [--boots 6] [--settle-min 3] [--rec-min 5] [--min-batt 50] [--min-cells 20] [--layout Y-shift] [--tag 꼬리표] [--no-extract]"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_common as RC  # noqa: E402

TEST = "IMU-1"


def add_options(ap) -> None:
    ap.add_argument("--boots", type=int, default=6, help="IMU-1 원격 재부팅 횟수(기본 6)")
    ap.add_argument("--settle-min", type=float, default=3.0, help="IMU-1 부팅 뒤 안정 대기 분(기본 3)")
    ap.add_argument("--rec-min", type=float, default=5.0, help="IMU-1 부팅마다 기록 분(기본 5)")
    ap.add_argument("--min-batt", type=float, default=50.0, help="IMU-1 시작할 때 가장 낮은 배터리 %%(기본 50)")
    ap.add_argument("--min-cells", type=int, default=20, help="IMU-1 시작할 때 들려야 하는 셀 수(기본 20)")


def check(a) -> None:
    pass


def drive(rec, say, a) -> int:
    """플러그 끔 → 들리는 셀 · 배터리 보기 → 부팅 a.boots 번(재부팅 · 안정 · 기록). 셀 기록 받기는 하지 않는다. 코드를 돌려준다."""
    Q = RC.Q
    s = rec.summary
    s.update({"boots": [], "settle_min": a.settle_min, "rec_min": a.rec_min})
    say(f"── {TEST} · 부팅 {a.boots}번 · 안정 {a.settle_min:g}분 + 기록 {a.rec_min:g}분")
    s["plug_off"] = Q.charge.plug_set(False, say)
    tap = Q.start_tap(TEST, 2, rec.out / "tap.log")
    time.sleep(50)
    cells = Q.qc_now().get("cells") or {}
    heard = sorted(int(k) for k, x in cells.items() if x.get("age_s") is not None and x["age_s"] < 10)
    batt = {int(k): x.get("battery") for k, x in cells.items() if int(k) in heard}
    low = min((b for b in batt.values() if b is not None), default=None)
    Q.stop_tap(tap)
    say(f"들리는 셀 {len(heard)}대 {heard} · 가장 낮은 배터리 {low}%")
    s["heard"] = heard
    s["battery_start"] = batt
    rec.save()
    if len(heard) < a.min_cells:
        raise SystemExit(f"들리는 셀이 {len(heard)}대뿐(필요 {a.min_cells}) — 멈춘다")
    if low is not None and low < a.min_batt:
        raise SystemExit(f"가장 낮은 배터리 {low}% < {a.min_batt:g}% — 충전 뒤 다시")
    for k in range(1, a.boots + 1):
        t_rb = time.time()
        Q.reboot(heard, say)
        tap = Q.start_tap(TEST, a.settle_min + a.rec_min + 3, rec.out / "tap.log")
        say(f"부팅 {k}/{a.boots} · 재부팅 {time.time() - t_rb:.0f}초 · 안정 {a.settle_min:g}분")
        time.sleep(a.settle_min * 60)
        t0 = time.time()
        say(f"부팅 {k} 기록 {a.rec_min:g}분 시작")
        time.sleep(a.rec_min * 60)
        t1 = time.time()
        cells = Q.qc_now().get("cells") or {}
        alive = sorted(int(x) for x, c in cells.items() if c.get("age_s") is not None and c["age_s"] < 10)
        Q.stop_tap(tap)
        s["boots"].append({"k": k, "reboot_at": t_rb, "from": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
                           "to": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t1)), "alive": alive})
        rec.save()
        say(f"부팅 {k} 끝 · 기록 중 들린 셀 {len(alive)}대")
    say(f"{TEST} 끝 · 플러그는 꺼 둔 채로 둔다(다음 레일 시험도 배터리로)")
    return 0


def main(argv=None) -> int:
    return RC.run([TEST], argv, __doc__)


if __name__ == "__main__":
    raise SystemExit(main())
