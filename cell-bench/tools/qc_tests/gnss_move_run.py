"""GNSS-MOVE · GNSS 레일 시험 실행기 — 레일과 같은 속도로 움직이는 LabSat 장면을 함께 틀고 100 · 300 · 500 mm/s 각 N번, 끝나면 정지 장면으로 되돌리고 셀 기록 받기.
속도마다 장면을 바꾸면 셀이 그 장면에서 위치를 잡을 때까지 기다린다(qc_round.gps_ready — 5분까지, 90초에 못 잡은 셀만 재부팅).
준비 · 마무리 · 받기는 run_common.run 이 tools/qc_round.py 로 한다. 운전 부분(drive)은 묶음 실행기(qc_bundle.py)도 부르고, 함께 돌리면 늘 맨 끝이다.
결과: data/GNSS-MOVE_<날짜>[꼬리표]/v<속도>/runs.json · run.log · summary.json · tap.log, 받은 셀 기록 data/ftg_y_<시각>
판정: pv4_shift.py GNSS-MOVE --runs <폴더>/v300/runs.json --truth ... --scene S03_RailB120 --ftg <셀 기록> (protocol_v4.py GNSS-MOVE 를 그대로 부른다).
  합불은 GPS 원값 속도 비(gps_move)와 신호 세기(cn0_sim)로 본다. IMU 판정을 거친 속도(ftg_move · ftg_on · ftg_dist)는 참고값이다.
  GPS 원값 위치를 레일 정답과 비교하는 항목은 판정 도구에 아직 없다.
2026-11-12 까지 FIRMWARE-2 로 부르던 운전이다 — 프리셋 이름만 RUN-CONST_v<속도> 로 바꿨고 값 · 장면은 그대로다.
속도마다 첫 운전은 판정에서 뺀다 — 첫 번에는 모터 지연을 추정값(5.3초)으로 써서 레일이 장면보다 먼저 출발한다.
사용: python -X utf8 gnss_move_run.py [--n 10] [--speeds 300,100,500] [--layout Y-shift] [--tag 꼬리표] [--no-extract]"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_common as RC  # noqa: E402

TEST = "GNSS-MOVE"


def add_options(ap) -> None:
    ap.add_argument("--speeds", default="300,100,500", help="GNSS-MOVE 속도 mm/s, 쉼표로 (기본 300,100,500)")


def speeds(a) -> list[int]:
    return [int(x) for x in a.speeds.split(",") if x.strip()]


def check(a) -> None:
    for v in speeds(a):
        if v not in RC.Q.CONST_SCENES:
            raise SystemExit(f"GNSS-MOVE {v} mm/s 의 장면이 없다 — 있는 것: {sorted(RC.Q.CONST_SCENES)}")
        scene, truth, preset = RC.Q.CONST_SCENES[v]
        if not truth.exists() or not (RC.Q.run_rail.PRESETS / f"{preset}.json").exists():
            raise SystemExit(f"GNSS-MOVE {v} mm/s 의 장면 정답 파일 · 승인 프리셋이 없다 ({truth.name} · {preset}.json)")


def tap_minutes(a) -> float:
    return 20.0 * a.n * len(speeds(a)) / 3     # 10번 · 세 속도에 200분 — 11-10 에 50분으로 걸어 500 mm/s 6번째에서 실시간 기록이 끊겼다


def conditions(a) -> dict:
    """이 실행기가 실제로 쓰는 조건 모듈 값 — summary.json 의 run · src · sat · gps · att · dock (qc-tests.md 1-1절).
    레일 준비(qc_round.rail_prep)가 정지 장면 · 감쇠 0 을 걸고, 독 배치는 꽉 찬 독(Y-shift 등)이다."""
    return RC.qc_ids.conditions(run="RUN-CONST", src="SRC-LABSAT", sat=" · ".join(f"{RC.Q.CONST_SCENES[v][0]} 처음부터" for v in speeds(a)) + " (움직이는 장면 — 위성 4 ~ 6개, SAT-6)", gps="GPS-SYNC", att="ATT-0", dock="DOCK-24")


def drive(rec, say, ls, tap, a):
    """운전만 — 속도마다 장면을 바꾸고 위치를 다시 잡은 뒤 n 번. 정지 장면으로 되돌리기는 마무리(rail_finish)가 한다.
    (코드, 실시간 기록)을 돌려준다 — 장면을 바꿀 때 실시간 기록을 다시 켤 수 있어서 돌려준다."""
    rc = 0
    for v in speeds(a):
        scene, truth, preset = RC.Q.CONST_SCENES[v]
        got, tap = RC.Q.gps_ready(ls, scene, say, tap, a.tap_test, rec.out, tap_minutes(a))
        rec.summary[f"v{v}_gps_fixed"] = got
        rec.save()
        if got is None:
            say(f"{scene} 에서 위치를 못 잡아 멈춘다")
            return 1, tap
        t0 = time.time()
        rc = RC.Q.run_rail.main([TEST, "--n", str(a.n), "--out", str(rec.out / f"v{v}"), "--scene", scene, "--preset", preset])
        rec.summary[f"v{v}"] = {"rc": rc, "scene": scene, "truth": str(truth), "preset": preset, "from": t0, "to": time.time()}
        rec.save()
        say(f"{TEST} 속도 {v} mm/s 끝 · 코드 {rc}")
        if rc != 0:
            break
    return rc, tap


def main(argv=None) -> int:
    return RC.run([TEST], argv, __doc__)


if __name__ == "__main__":
    raise SystemExit(main())
