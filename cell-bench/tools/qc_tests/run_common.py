"""레일 QC 시험 실행기의 공통 뼈대 — 시험 실행기(imu1_run · imu2_run · firmware1_run · firmware2_run)와 묶음 실행기(qc_bundle.py)가 쓴다.

준비 · 마무리 · 셀 기록 받기는 tools/qc_round.py 의 함수(rail_prep · rail_finish · extract)를 부르고, 이 파일은 그것을 시험 차례대로 엮기만 한다.
시험 하나만 돌려도, 여럿을 함께 돌려도 같은 길을 지난다:
  0. 단계별 거르기 파일(data/qc_gate_<세트>.json, motionbench/qc_gate.py)을 읽어, 앞 시험에서 '불합격'한 셀을 시험마다 판정 대상에서 뺀다.
     독에는 있어도 된다 — 뺀 셀은 summary.json 의 excluded 에 이유와 함께 적고, 판정 스크립트가 그 셀을 건너뛴다. --include-failed 로 끈다.
     함께 돌리는 동안에는 앞 시험의 판정이 아직 없으므로, 시작할 때의 거르기 파일로만 뺀다.
  1. 시험마다 기록 폴더 data/<시험 ID>_<날짜><꼬리표>/ 를 만들고, 실행 기록(run.log)은 모든 폴더에 같은 줄로 쓴다.
  2. IMU-1 이 있으면 먼저 돈다 — 원격 재부팅을 여러 번 하며 실시간 기록을 스스로 켜고 끈다.
  3. 레일 시험(IMU-2 · FIRMWARE-1 · FIRMWARE-2)이 있으면 준비를 한 번만 하고(rail_prep), 운전(drive)만 차례로 부른 뒤 마무리한다(rail_finish).
     한 시험의 운전이 정상으로 끝나지 않으면 다음 시험으로 가지 않는다. 그래도 마무리와 셀 기록 받기는 한다.
  4. 셀 기록을 한 번 받아(받으면서 비우기) 모든 폴더의 summary.json 에 그 폴더를 적는다.
차례는 주는 순서와 상관없이 ORDER 다 — 운영 순서(qc_gate.ORDER)대로 센서 시험 먼저, 장면을 바꾸는 FIRMWARE-2 는 맨 끝(앞 시험이 정지 장면 아래에서 돌아야 한다).
GNSS-1 은 여기로 돌리지 않는다 — 독 1 · 3행에만 꽂고 장면을 정해진 자리부터 다시 트는 시험이라 다른 시험과 독 · 장면이 다르다(gnss1_run.py 혼자).

시험 실행기 파일마다 갖추는 것: TEST, add_options(ap), check(a) — 장비를 건드리기 전에 프리셋 · 장면 파일을 본다,
  레일 시험은 tap_minutes(a) 와 drive(rec, say, ls, tap, a) → (코드, 실시간 기록), IMU-1 은 drive(rec, say, a) → 코드.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]          # <작업 트리>/cell-bench — 제어 PC 에서는 C:\Dev\dynamic-bench\cell-bench
for _p in (HERE, ROOT, ROOT / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import qc_round as Q  # noqa: E402
from motionbench import qc_gate, qc_ids  # noqa: E402

ORDER = ("IMU-1", "IMU-2", "FIRMWARE-1", "FIRMWARE-2")      # qc_gate.ORDER 에서 GNSS-1 을 뺀 차례
MODULE = {"IMU-1": "imu1_run", "FIRMWARE-1": "firmware1_run", "IMU-2": "imu2_run", "FIRMWARE-2": "firmware2_run"}


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Rec:
    """시험 하나의 기록 폴더와 summary.json."""

    def __init__(self, test: str, a, together: list[str]):
        self.test = test
        self.out = Q.record_dir(test, a.tag)
        self.summary = {"test": test, "together": together, "layout": a.layout, "columns": Q.layout_columns(a.layout),
                        "n": a.n, "started": now()}

    def save(self) -> None:
        (self.out / "summary.json").write_text(json.dumps(self.summary, ensure_ascii=False, indent=1), encoding="utf-8")


def order(tests: list[str]) -> list[str]:
    """함께 돌릴 차례로 늘어놓는다. GNSS-1 이나 목록에 없는 ID 면 장비를 건드리기 전에 멈춘다."""
    tests = list(dict.fromkeys(tests))
    if "GNSS-1" in tests:
        raise SystemExit("GNSS-1 은 다른 시험과 함께 돌리지 않는다 — 독 1 · 3행 배치와 장면 구간이 따로라 gnss1_run.py 로 혼자 돌린다")
    if bad := [t for t in tests if t not in MODULE]:
        raise SystemExit(f"실행기가 없는 시험 {bad} — {', '.join(ORDER)} 중에서 고른다")
    return [t for t in ORDER if t in tests]


def tap_layout(name: str) -> str:
    """실시간 화면이 그릴 배치 — 실시간 기록(live_tap)은 motionbench/dock_layout.json 의 배치만 안다. 거기 없으면(Y-shift 등) Y."""
    lay = json.loads((ROOT / "motionbench" / "dock_layout.json").read_text(encoding="utf-8"))
    return name if name in lay else name.split("-")[0] if name.split("-")[0] in lay else "Y"


def run(tests: list[str], argv=None, doc: str = "", bundle: bool = False) -> int:
    tests = order(tests)
    mods = {t: importlib.import_module(MODULE[t]) for t in tests}
    ap = argparse.ArgumentParser(description=(doc or "").strip().splitlines()[0] if doc else None)
    if bundle:
        ap.add_argument("--tests", required=True, help="함께 돌릴 시험 ID, 쉼표로 — 예 IMU-2,FIRMWARE-1")
    ap.add_argument("--n", type=int, default=10, help="운전 횟수(가속도 · 속도마다, 기본 10)")
    ap.add_argument("--layout", default="Y-shift", help="독 배치 이름(motionbench/dock_layout.json · qc_tests/dock_layout_a.json, 기본 Y-shift)")
    ap.add_argument("--tag", default="", help="기록 폴더 이름 꼬리 — 같은 날 같은 시험을 또 돌릴 때(예 _swap)")
    ap.add_argument("--no-extract", action="store_true", help="셀 기록을 받지 않고 끝낸다(다음 시험 뒤에 한 번에 받는다)")
    ap.add_argument("--include-failed", action="store_true", help="앞 시험에서 불합격한 셀도 판정 대상에 둔다(단계별 거르기를 끈다)")
    for m in mods.values():
        m.add_options(ap)
    a = ap.parse_args(argv)
    for m in mods.values():
        m.check(a)                                  # 프리셋 · 장면 파일 — 장비를 건드리기 전에
    rail = [t for t in tests if t in qc_ids.RAIL]
    a.tap_test = ",".join(rail)                     # 실시간 기록에 적을 시험 ID(레일 시험들)
    Q.TAP_LAYOUT = tap_layout(a.layout)
    recs = {t: Rec(t, a, tests) for t in tests}
    say = Q.Log(*(r.out / "run.log" for r in recs.values()))
    say(f"── {qc_ids.join(tests)} · 배치 {a.layout} · 운전 {a.n}번" + (" · 함께 돌림(준비 · 받기 한 번)" if len(tests) > 1 else "")
        + " · 기록 " + " · ".join(r.out.name for r in recs.values()))
    gate_file = qc_gate.path(a.layout.split("-")[0], Q.DATA)
    gate = qc_gate.load(gate_file)
    for t, r in recs.items():
        ex = {} if a.include_failed else qc_gate.excluded(gate, t)
        r.summary.update({"gate_file": gate_file.name, "include_failed": a.include_failed, "excluded": {str(s): why for s, why in ex.items()}})
        if a.include_failed:
            say(f"{t} — 단계별 거르기 끔(--include-failed): 앞 시험 불합격 셀도 판정한다")
        elif ex:
            say(f"{t} — 앞 시험 불합격 {len(ex)}대를 판정 대상에서 뺀다(독에는 있어도 된다): "
                + " · ".join(f"{s}({why})" for s, why in sorted(ex.items())))
        else:
            say(f"{t} — 앞 시험 불합격 셀 없음({gate_file.name})")
        r.save()
    rc = 0
    if "IMU-1" in recs:
        rc = mods["IMU-1"].drive(recs["IMU-1"], say, a)
        recs["IMU-1"].summary["rc"] = rc
        recs["IMU-1"].save()
    if rc == 0 and rail:
        minutes = sum(mods[t].tap_minutes(a) for t in rail)
        ls, tap, plug = Q.rail_prep(rail, minutes, recs[rail[0]].out, say)
        try:
            for t in rail:
                r = recs[t]
                r.summary["plug_off"] = plug
                r.summary["drive_from"] = now()
                rc, tap = mods[t].drive(r, say, ls, tap, a)
                r.summary["rc"] = rc
                r.summary["drive_to"] = now()
                r.save()
                say(f"{t} 운전 끝 · 코드 {rc}")
                if rc != 0:
                    say("정상으로 끝나지 않아 다음 시험으로 가지 않는다 — 셀 기록은 받는다")
                    break
            say("모터 운전 끝 — 여기부터 현장 입회가 필요 없다")
        finally:
            Q.rail_finish(ls, tap, say)
    ftg = None
    if not a.no_extract:
        time.sleep(5)
        ftg = Q.extract(a.layout, say)
    for r in recs.values():
        r.summary["ftg"] = None if ftg is None else str(ftg)
        r.summary["ended"] = now()
        r.save()
    say(f"── 끝 · 코드 {rc} · 셀 기록 {ftg or '받지 않음'}")
    return rc
