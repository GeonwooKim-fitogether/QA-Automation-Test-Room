"""GNSS-1 · GNSS 수신 시험 실행기 — 2026-11-11 확정 방식 한 회차. 모터 없음. 다른 시험과 함께 돌리지 않는다.
독은 1행 · 3행에만 셀을 꽂는다(배치 파일 columns 의 1 · 3번째 줄, 한 행 6칸). 꽉 찬 독보다 1행 +5 dB · 3행 +3.5 dB 로 신호가 세다(11-11 행 간격 시험).
정지 장면을 장면 313분부터 다시 틀고, 판정 구간은 장면 321 ~ 346분(25분)이다 — 회차마다 같은 자리부터 틀어 모든 회차가 같은 위성 구성을 본다
(장면 313 ~ 357분은 위성 12개로 일정, 11-11 실측). 판정 구간은 PC 시각이 아니라 장면 시각으로 정한다.
순서: 수집 실행기 잠금 쥠(충전 유지가 플러그를 다시 켜지 않게) → 플러그 끔 → 장면 313분부터 재생 → 감쇠 18 dB → 실시간 기록 50초(들리는 셀 · 배터리)
     → 들리는 셀 모두 다시 켜기 → 위치 잡기 → 장면 321분까지 기다림 → 346분까지 기록 → 감쇠 0
     → 1차 거르기(회차 신호 세기로 바로) → 셀 기록 받기(받으면서 비우기).
1차 거르기(2026-11-12 사용자 결정 — 단계별 거르기): 판정 구간의 실시간 패킷으로 셀마다 신호 세기(C/N0) 중앙값과 위치 잡은 비율을 내 summary.json 의 cn0 에
적고, 꽂은 행의 셀마다 위치를 못 잡았거나 칸 기준값보다 3 dB 넘게 낮으면 '재측정', 두 번 걸리면 '불합격'으로 data/qc_gate_<세트>.json 에 누적한다.
규칙과 숫자는 motionbench/qc_gate.py · criteria/qc_tests.json. 재측정 셀은 다음 회차에 다른 칸에 꽂는다.
결과: data/GNSS-1_<날짜>[꼬리표]/r<회차>/{summary.json(window = 판정 구간 PC 시각, cn0 · gate = 1차 거르기), run.log, tap.log}, 받은 셀 기록 data/ftg_y_<시각>
판정: gnss1_judge.py GNSS-1_<날짜>[꼬리표] (회차마다 셀 · 칸 · 회차 효과로 나눈 신호 세기)
사용: python -X utf8 gnss1_run.py <회차> [--layout Y] [--rows 1,3] [--from-min 313] [--rec-from 321] [--rec-to 346] [--attn 18] [--min-cells 10] [--tag 꼬리표] [--no-extract]"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_common as RC  # noqa: E402
from motionbench import qc_gate  # noqa: E402

TEST = "GNSS-1"


def round_items(t0: float, t1: float) -> dict[int, dict]:
    """판정 구간의 실시간 패킷(data/live_raw) → 셀마다 {cn0_sim: 위치 잡은 패킷의 신호 세기 중앙값, gps_rx: 위치 잡은 패킷 %, n: 패킷 수}."""
    import protocol_v4 as P
    live = P.live(t0, t1, RC.Q.DATA)
    return {s: P.base_items(pk, t0, t1) | {"n": len(pk)} for s, pk in live.items()}


def first_gate(s: dict, out: Path, say, a) -> None:
    """GNSS-1 1차 거르기 — 회차 신호 세기를 summary(cn0)에 적고, 꽂은 행의 셀을 거르기 파일에 누적한다(qc_gate.gnss1_round)."""
    t0 = time.mktime(time.strptime(s["window"]["from"], "%Y-%m-%d %H:%M:%S"))
    t1 = time.mktime(time.strptime(s["window"]["to"], "%Y-%m-%d %H:%M:%S"))
    cols = s["columns"]
    slot = {x: f"{c + 1}-{r + 1}" for c, col in enumerate(cols) for r, x in enumerate(col)}
    tested = [x for row in s["rows"] if 1 <= row <= len(cols) for x in cols[row - 1]]
    items = round_items(t0, t1)
    rnd = lambda v: None if v is None else round(v, 1)
    s["cn0"] = {str(x): {"slot": slot.get(x), "cn0": rnd(v.get("cn0_sim")), "gps_rx": rnd(v.get("gps_rx")), "packets": v.get("n"),
                         "tested": x in tested} for x, v in sorted(items.items())}
    cells = {x: {"slot": slot[x], "cn0": (items.get(x) or {}).get("cn0_sim"), "gps_rx": (items.get(x) or {}).get("gps_rx")} for x in tested}
    gp = qc_gate.path(a.layout.split("-")[0], RC.Q.DATA)
    doc = qc_gate.load(gp)
    cond = f"감쇠{a.attn}_행{','.join(map(str, s['rows']))}"
    res = qc_gate.gnss1_round(doc, cells, cond, f"{out.parent.name}/{out.name}")
    qc_gate.save(gp, doc)
    s["gate"] = {str(x): r for x, r in res.items()}
    s["gate_file"] = gp.name
    say(f"1차 거르기(조건 {cond}) — 꽂은 행 셀 {len(cells)}대:")
    for x, r in sorted(res.items()):
        say(f"  {x} 칸 {cells[x]['slot']}: {r['status']}{' · 이번 회차 걸림' if r['hit'] else ''} — {r['why']}")
    n = {k: sum(1 for r in res.values() if r["status"] == k) for k in (qc_gate.PASS, qc_gate.RETEST, qc_gate.FAIL)}
    say(f"1차 거르기 끝 — 합격 {n[qc_gate.PASS]} · 재측정 {n[qc_gate.RETEST]}(다음 회차에 다른 칸) · 불합격 {n[qc_gate.FAIL]} → {gp.name}")


def main(argv=None) -> int:
    Q = RC.Q
    from motionbench.labsat import clean
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("round", type=int, help="회차 번호 — 폴더 r<회차>")
    ap.add_argument("--layout", default="Y", help="독 배치 이름(기본 Y). 꽂은 셀이 그 배치의 1 · 3행 자리에 있어야 한다")
    ap.add_argument("--rows", default="1,3", help="셀을 꽂은 행(배치 파일 columns 의 몇 번째 줄), 기록에만 남긴다(기본 1,3)")
    ap.add_argument("--from-min", type=float, default=313.0, help="정지 장면을 다시 트는 자리, 장면 분(기본 313)")
    ap.add_argument("--rec-from", type=float, default=321.0, help="판정 구간 시작, 장면 분(기본 321)")
    ap.add_argument("--rec-to", type=float, default=346.0, help="판정 구간 끝, 장면 분(기본 346)")
    ap.add_argument("--attn", type=int, default=18, help="LabSat 출력 감쇠 dB(기본 18)")
    ap.add_argument("--min-cells", type=int, default=10, help="들려야 하고 위치를 잡아야 하는 셀 수(기본 10 — 1 · 3행은 12칸)")
    ap.add_argument("--tag", default="", help="기록 폴더 이름 꼬리")
    ap.add_argument("--no-extract", action="store_true", help="셀 기록을 받지 않고 끝낸다")
    a = ap.parse_args(argv)
    rows = [int(x) for x in a.rows.split(",") if x.strip()]
    out = Q.record_dir(TEST, a.tag) / f"r{a.round:02d}"
    out.mkdir(parents=True, exist_ok=True)
    say = Q.Log(out / "run.log")
    s = {"test": TEST, "round": a.round, "layout": a.layout, "rows": rows, "columns": Q.layout_columns(a.layout), "scene": Q.STATIC,
         "scene_from_min": a.from_min, "rec_scene_min": [a.rec_from, a.rec_to], "attn_db": a.attn, "started": RC.now()}

    def save():
        (out / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")

    say(f"── {TEST} {a.round}회차 · 배치 {a.layout} · {' · '.join(map(str, rows))}행 · 장면 {a.from_min:g}분부터 · 판정 구간 장면 {a.rec_from:g} ~ {a.rec_to:g}분 · 감쇠 {a.attn} dB")
    Q.TAP_LAYOUT = RC.tap_layout(a.layout)
    _lock = Q.hold_loop_lock()          # 회차가 끝나면(프로그램이 끝나면) 저절로 풀린다
    s["plug_off"] = Q.charge.plug_set(False, say)     # 배터리로 — 레일 운전이 끝나면 플러그가 켜져 있다(11-11 충전 중 회차 사고)
    ls = Q.LabSat()
    ls.stop()
    time.sleep(2)
    t_play = ls.play(Q.STATIC, from_s=int(a.from_min * 60))
    s["t_play"] = t_play
    say(f"장면 {Q.STATIC} {a.from_min:g}분부터 재생 · {ls.status()}")
    r = clean(ls._one(f"ATTN:{a.attn:02d}"))
    say(f"감쇠 {a.attn} dB → {r}")
    if "ERR" in r.upper():
        raise SystemExit("감쇠 설정 실패")
    scene_at = lambda m: t_play + (m - a.from_min) * 60          # 장면 m 분이 되는 PC 시각

    try:
        tap = Q.start_tap(TEST, 2, out / "tap.log")
        time.sleep(50)
        cells = Q.qc_now().get("cells") or {}
        heard = sorted(int(k) for k, x in cells.items() if x.get("age_s") is not None and x["age_s"] < 10)
        s["heard"] = heard
        s["battery"] = {int(k): x.get("battery") for k, x in cells.items() if int(k) in heard}
        say(f"들리는 셀 {len(heard)}대 · 가장 낮은 배터리 {min((b for b in s['battery'].values() if b is not None), default=None)}%")
        Q.stop_tap(tap)
        save()
        if len(heard) < a.min_cells:
            raise SystemExit(f"들리는 셀이 {len(heard)}대뿐(필요 {a.min_cells})")
        Q.reboot(heard, say)
        say("다시 켬")
        tap = Q.start_tap(TEST, (a.rec_to - a.from_min) + 15, out / "tap.log")
        time.sleep(8)
        if not Q.wait_lock(say, need=a.min_cells, max_s=Q.LOCK_WAIT_S):
            Q.stop_tap(tap)
            raise SystemExit(f"위치를 못 잡는 셀이 많다 {Q.unlocked()} — 멈춘다")
        wait = scene_at(a.rec_from) - time.time()
        if wait < 0:
            Q.stop_tap(tap)
            raise SystemExit(f"위치 잡기가 늦어 장면 {a.rec_from:g}분을 {-wait:.0f}초 넘겼다 — --rec-from 을 늦춰 다시")
        say(f"장면 {a.rec_from:g}분까지 {wait:.0f}초 기다림")
        time.sleep(wait)
        t0 = time.time()
        say(f"기록 시작(장면 {a.rec_from:g}분)")
        time.sleep(max(0.0, scene_at(a.rec_to) - time.time()))
        s["window"] = {"from": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)), "to": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))}
        say(f"기록 끝(장면 {a.rec_to:g}분)")
        Q.stop_tap(tap)
    finally:
        say(f"감쇠 0 → {clean(ls._one('ATTN:00'))}")
        save()
    try:
        first_gate(s, out, say, a)
    except Exception as e:                       # 거르기가 실패해도 회차 기록과 셀 기록 받기는 잃지 않는다
        s["gate_error"] = repr(e)
        say(f"1차 거르기 실패 — {e!r}. 셀 기록은 받는다. 거르기는 이 회차 폴더로 다시 낼 수 있다")
    save()
    if not a.no_extract:
        time.sleep(5)
        ftg = Q.extract(a.layout, say)
        s["ftg"] = str(ftg)
    s["ended"] = RC.now()
    save()
    say(f"── {TEST} {a.round}회차 끝 · 셀 기록 {s.get('ftg') or '받지 않음'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
