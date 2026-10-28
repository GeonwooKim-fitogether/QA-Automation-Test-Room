"""리니어 모터 모션 엔진 — M1 읽기 전용 · M2 입회 왕복.

사용 (작업 트리 루트에서):
  python cell-bench/run_motion.py scan                   # 드라이브를 찾아 기록과 대조 · SDO 읽기 · PDO 1초 → data/drives.json
  python cell-bench/run_motion.py monitor --minutes 10   # 위 + 2 ms 주기 PDO 읽기 → data/motion.json(1초 심박) · data/motion_<시각>.csv
  python cell-bench/run_motion.py move                   # M2 — 승인 파일 + 유효 입회 표지가 있을 때만 PP 상대 왕복 → data/motion_m2.json
  python cell-bench/run_motion.py hold --hold-s 120      # M2 재기 — 10 mm 가서 Enter 까지 멈춤 → 돌아와 한 번 더 멈춤 → data/motion_m2_hold_<시각>.json
  python cell-bench/run_motion.py slow --mm-s 10         # M2 Ctrl+C 시험 — Enter 뒤 10 mm/s 왕복 1번, 움직이는 동안 Ctrl+C → data/motion_m2_slow_<시각>.json
  python cell-bench/run_motion.py slow                   # M2 랜선 시험 — Enter 뒤 2 mm/s(기본) 왕복 1번, 움직이는 동안 PC 쪽 랜선을 뽑는다
hold · slow 는 콘솔 창에서만 돈다(Enter 를 콘솔 키로 읽는다). 수치는 move 와 같은 승인 파일 · 입회 표지.

scan · monitor 절대 규칙: 서보 온 없음 · SAFE-OP 까지만 · 출력 Controlword 는 늘 0 · SDO 쓰기 없음.
예상 밖 Statusword · 상태 전이 · 0x603F · 슬레이브 수 · PDO 매핑 · WKC 3주기 연속 불일치면 즉시 INIT 로 내리고 어댑터를 닫는다.
move · hold · slow 의 규칙 — 수치는 승인 파일에서만, 이상이면 Quick stop, 끝은 늘 Quick stop → 0x06 → 0 → SAFE-OP → INIT.
방향은 축 정의 파일(dynamic-bench/config/axis-motor1.json)에서만. 위치는 전원을 넣은 자리를 믿지 않고 레일 파일(data/rail-motor1.json)만
믿는다 — 같은 전원이 아니면 먼저 home(양 끝 잡기), 모든 목표는 양 끝에서 20 mm 안쪽(motionbench/rail.py, 기획 dynamic-bench/design/
rail-recovery.md). home · goto 는 python cell-bench/run_motion.py home | goto. scan · monitor 는 이것들을 보지 않는다.
pysoem 과 Npcap 이 필요하다(cell-bench requirements.txt 에는 아직 없다).
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from cellbench import proc
from cellbench.control import write_json_atomic
from motionbench import axis, ecat, m2, rail, safety
from motionbench import motion_record as rec

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"          # ponytail: cell-bench 의 data 폴더에 고정. 감시자가 읽게 될 때(M3) Config.data_dir 로 바꾼다
APPROVAL = ROOT.parent / "dynamic-bench" / "dev" / "m2-approval.json"   # 읽기만 한다 — 다른 쪽이 사용자 승인으로 만든다
APPROVAL_M3 = ROOT.parent / "dynamic-bench" / "dev" / "m3-approval.json"  # 읽기만 한다 — M3 단계마다 사용자 승인으로 만든다
APPROVAL_TOUCH = ROOT.parent / "dynamic-bench" / "dev" / "touch-approval.json"  # 읽기만 한다 — 끝 터치 사용자 승인
APPROVAL_HOME = ROOT.parent / "dynamic-bench" / "dev" / "home-approval.json"    # 읽기만 한다 — 레일 양 끝 잡기 사용자 승인
APPROVAL_GOTO = ROOT.parent / "dynamic-bench" / "dev" / "goto-approval.json"    # 읽기만 한다 — 절대 위치 이동 사용자 승인
RAIL = DATA / "rail-motor1.json"     # 이번 전원의 레일 양 끝 · 표시 · 마지막 위치 — home 이 쓰고 모든 이동이 읽는다
MARKER = DATA / "motion_attended"                                         # 읽기만 한다 — 다른 쪽이 사용자 승인으로 만든다
AXIS = ROOT.parent / "dynamic-bench" / "config" / "axis-motor1.json"      # 읽기만 한다 — Home 쪽은 사람이 현장에서 확인해 채운다
DONE_M2 = "왕복 다 됨"
DONE_TOUCH = "끝 터치 다 됨"
DONE_HOME = "home 다 됨"
DONE_GOTO = "이동 다 됨"
MODES = ("move", "hold", "slow", "m3", "touch", "home", "goto")
TOUCH_A_MM_S2 = 100          # 끝 터치 가감속 — 5 mm/s 까지 0.05초
TOUCH_MARGIN_MM = 5          # 끝 터치의 창 = 승인 max_mm + 이만큼
GEAR_SDO = [(0x608F, 1), (0x608F, 2), (0x6091, 1), (0x6091, 2), (0x6092, 1), (0x6092, 2)]


def _load(path, problem):
    """승인 파일 하나 → (dict, None) 또는 (None, '승인 파일 거부 — …')."""
    a, why = safety.read_json(path)
    why = why or problem(a)
    return (None, f"승인 파일 거부 — {why} ({path})") if why else (a, None)


def _rail_load():
    """레일 파일 → dict 또는 None (없거나 깨졌으면 None — 그때는 home 이 새로 만든다)."""
    doc, why = safety.read_json(RAIL)
    return None if why or rail.problem(doc) else doc


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def move(m, info: dict, mode: str = "move", hold_s: float = 120.0, mm_s: float = 2.0) -> str:
    """입회 하 Profile Position 이동 — 끝난 이유를 돌려준다.
    move · m3: 시작점 기준 왕복(move 는 마지막 왕복 도중 Quick stop 시험). hold · slow: 왕복 1번에 Enter 기다림(_attended).
    touch: 끝 터치(진단). home: 레일 양 끝 잡기. goto: 왼쪽 끝에서 잰 절대 위치로.
    touch 말고는 전부 레일 기준(motionbench/rail.py): 같은 전원이 아니면(0x607D ≠ 레일 limits, 또는 첫 서보 온 > 0.5초) 먼저 home."""
    if mode not in MODES:                # 어댑터를 열기 전에 거부한다. NaN · inf 도 아래 비교에서 걸린다
        return f"거부 — 모르는 모드 {mode!r}"
    if mode == "touch":
        a, why = safety.load_touch_approval(APPROVAL_TOUCH)
    elif mode == "home":
        a, why = _load(APPROVAL_HOME, rail.home_approval_problem)
    elif mode == "goto":
        a, why = _load(APPROVAL_GOTO, rail.goto_approval_problem)
    else:
        a, why = safety.load_m3_approval(APPROVAL_M3, _m3_passed()) if mode == "m3" else safety.load_approval(APPROVAL)
    info["approval"] = a or why
    if why:
        return why
    if "start_from_left_mm" in a:   # 시작 위치는 M3 · M4 승인에서만, 0 이상의 숫자로 (기획 2-5)
        x = a["start_from_left_mm"]
        if mode != "m3":
            return f"승인 파일 거부 — start_from_left_mm 는 m3 승인에서만 쓴다 ({mode})"
        if not isinstance(x, (int, float)) or isinstance(x, bool) or x != x or x < 0:
            return f"승인 파일 거부 — start_from_left_mm = {x!r} — 0 이상의 숫자여야 한다"
    touch = a if mode == "touch" else None
    ha = a if mode == "home" else None if touch else _load(APPROVAL_HOME, rail.home_approval_problem)[0]
    rdoc = None if touch else _rail_load()
    if not touch and rdoc is None and ha is None:
        return f"거부 — 레일 파일이 없고 home 승인도 없다 — 레일을 잡을 수 없다 ({RAIL} · {APPROVAL_HOME})"
    k = safety.PULSE_PER_MM
    big = (rail.LENGTH + 2 * rail.SEARCH_EXTRA) / k                       # home · goto · 시작점 이동의 창 (mm) — 레일 한계가 따로 막는다
    if touch:                        # 끝 터치를 왕복과 같은 칸으로 — 방향은 축 정의의 plus_direction 과 side 로 정한다
        doc, why = axis.load(AXIS)
        if why:
            return f"거부 — 축 정의: {why} ({AXIS})"
        d = 1 if touch["side"] == doc.get("plus_direction") else -1
        a = {**touch, "first_move_dir": d, "distance_mm": touch["max_mm"], "window_mm": touch["max_mm"] + TOUCH_MARGIN_MM,
             "vmax_mm_s": touch["creep_mm_s"], "amax_mm_s2": TOUCH_A_MM_S2, "cycles": touch["touches"]}
    elif mode == "home":
        a = {**a, "first_move_dir": 1, "distance_mm": 0, "window_mm": big, "vmax_mm_s": a["creep_mm_s"],
             "amax_mm_s2": TOUCH_A_MM_S2, "cycles": 0}
    elif mode == "goto":
        a = {**a, "first_move_dir": 1, "distance_mm": 0, "window_mm": big, "cycles": 0}
    if mode in ("hold", "slow") and not 0 < hold_s <= m2.HOLD_MAX_S:
        return f"거부 — --hold-s {hold_s} 는 0 보다 크고 {m2.HOLD_MAX_S} 이하여야 한다"
    if mode == "slow" and not 1 <= mm_s <= a["vmax_mm_s"]:
        return f"거부 — --mm-s {mm_s} 는 1 이상, 승인 vmax {a['vmax_mm_s']} 이하여야 한다"
    if mode in ("hold", "slow"):
        if not _console_ok():
            return "거부 — hold · slow 는 콘솔 창에서만 돈다 (입력 · 출력이 콘솔이 아니다)"
        proc.console("[STOP] 창 닫기 · Ctrl+Break 는 멈춤 순서를 건너뜁니다 — 비상 정지는 전원 스위치. "
                     "도는 동안 창 안을 클릭하지 마세요 (선택 중에는 Ctrl+C 가 복사가 됩니다)")
    doc, why = axis.load(AXIS)           # 방향 — 확인되지 않았으면 어댑터를 열기 전에 거부한다. 첫 이동 방향은 레일 한계가 판정한다
    why = why or axis.problem(doc, a["first_move_dir"], away=False)
    if why:
        return f"거부 — 축 정의: {why} ({AXIS})"
    plus_is_left = doc["plus_direction"] == "왼쪽"
    if touch:
        words = axis.touch_words(touch["side"], a["first_move_dir"], touch["max_mm"], touch["creep_mm_s"], touch["torque_permil"])
    elif mode == "home":
        words = f"왼쪽 · 오른쪽 끝 찾기 · {a['creep_mm_s']:g} mm/s · 힘 상한 {a['torque_permil'] / 10:g} %"
    elif mode == "goto":
        words = f"왼쪽 끝에서 {a['to_from_left_mm']:g} mm 자리로 · {a['vmax_mm_s']:g} mm/s"
    else:
        words = axis.first_move_words(doc, a["first_move_dir"], a["distance_mm"])
    info["axis"] = {"path": str(AXIS), **{f: doc[f] for f in ("viewpoint", "plus_direction", "home_side", "confirmed_by", "confirmed_at")},
                    "words": words}
    proc.console(f"[PLAN] 방향: {words} — 반대로 가면 바로 Ctrl+C")
    mwhy, deadline = safety.check_marker(MARKER, time.time())
    info["marker_start"] = {"valid": not mwhy, "why": mwhy, "deadline": rec.ts(deadline) if deadline else None}
    proc.console(f"{rec.ts()} 입회 표지 검사 — " + (f"무효: {mwhy}" if mwhy else f"유효 (마감 {rec.ts(deadline)})"))
    if mwhy:
        return f"입회 표지 무효 — 서보 온 거부: {mwhy}"
    D, W = round(a["distance_mm"] * k) * a["first_move_dir"], round(a["window_mm"] * k)
    # hold · slow 도 이 승인 파일 그대로 — 거리 · 방향 · 창 · amax 같고 왕복 1번(≤ 승인 cycles), 속도는 승인 vmax(hold) 또는
    # 그 이하의 --mm-s(slow). 그래서 V 만 바뀌고, 0x6081 쓰기 · 정지 거리 검사 · 이동 시간 한도가 그 V 를 그대로 쓴다.
    V, A = round((mm_s if mode == "slow" else a["vmax_mm_s"]) * k), round(a["amax_mm_s2"] * k)
    s = ax = cyc = log = limit0 = torque0 = None
    opened, reason, rail_ok, need_home = False, None, False, False
    try:
        m.open(ecat.IFNAME)          # Npcap 권한 등으로 실패하면 우회하지 않고 그대로 기록하고 끝낸다
        opened = True
        s, p0 = ecat.bring_up(m, info)                       # 슬레이브 1대 · 정체 · PDO 매핑 대조 · SAFE-OP (M1 그대로)
        sdo = {r["obj"]: r["value"] for r in info["sdo"]}
        gear = {f"0x{i:04X}:{j:02X}": ecat.sdo(s, i, j, "<I")[0] for i, j in GEAR_SDO}
        over, over_err = ecat.sdo(s, 0x3513, 0, "<h")        # Pr5.13 과속도 레벨 (PANATERM 객체 덤프: 3513h "Over-speed level setup")
        err, sw = ecat.sdo(s, 0x603F, 0, "<H")[0], ecat.sdo(s, 0x6041, 0, "<H")[0]
        a_qs = sdo["0x6085:00"]
        limit0 = (sdo["0x607D:01"], sdo["0x607D:02"])
        if not touch:                # 같은 전원인가 — 드라이브 0x607D 가 레일 파일의 limits 와 같을 때만
            same = rail.same_power(rdoc, limit0)
            need_home = mode == "home" or not same
            info["power"] = {"same": same, "drive_607D": list(limit0), "rail_limits": rdoc["limits"] if rdoc else None}
            if need_home and ha is None:
                raise m2.Abort(f"움직이지 않음 — 레일을 다시 잡아야 하는데(0x607D {list(limit0)}) home 승인이 없다 ({APPROVAL_HOME})")
        start, targets = _plan(mode, a, rdoc, p0, D, plus_is_left) if not touch and not need_home else (p0, [p0 + D])
        stop = round(V * V / (2 * a_qs)) if a_qs else 0     # 급정지 거리 — 레일 한계 검사에 더한다
        info["checks"] = {"p0": p0, "p0_from_origin_mm": round(p0 / k, 3), "gear": gear, "pr5_13_mm_s": over, "pr5_13_error": over_err, "err": err,
                          "statusword": None if sw is None else f"0x{sw:04X}", "quick_stop_decel": a_qs,
                          "stop_distance_pulse": round(V * V / (2 * a_qs), 1) if a_qs else None,
                          "profile_before": {f"0x{i:04X}": ecat.sdo(s, i, 0, "<I")[0] for i in (0x6081, 0x6083, 0x6084)}}
        why = (safety.start_problem(info["slaves"], sdo["0x1018:04"], err or 0, sw or 0, sdo["0x605A:00"])
               or (err is None or sw is None) and "0x603F · 0x6041 을 읽지 못했다"
               or safety.units_problem(gear) or safety.speed_problem(a["vmax_mm_s"], over)
               or (not touch and not need_home and next(filter(None, (rail.target_problem(t, rdoc, stop) for t in targets)), None))
               or (mode in ("move", "hold", "slow", "m3") and safety.target_problem(start, start + D, W, V, a_qs))
               or safety.target_problem(start, start, W, V, a_qs))
        info["checks"]["result"] = why or "통과"
        if why:
            raise m2.Abort(f"움직이지 않음 — {why}")
        if touch or need_home:       # 미는 힘 상한 — 처음 값을 읽어 둔 뒤에만, 서보 온 전에 낮춘다 (끝에 닿을 수 있는 동작)
            torque0 = _torque_low(s, info, touch["torque_permil"] if touch else ha["torque_permil"])
            info["torque_low_before_servo_on"] = True
        wr = {"0x6081:00": ecat.sdo_put(s, 0x6081, 0, "<I", V), "0x6083:00": ecat.sdo_put(s, 0x6083, 0, "<I", A),
              "0x6084:00": ecat.sdo_put(s, 0x6084, 0, "<I", A)}
        if touch:                    # 끝 터치는 옛 방식 그대로 — 창을 쓰고 끝에 처음 값으로 되돌린다
            wr.update({"0x607D:01": ecat.sdo_put(s, 0x607D, 1, "<i", p0 - W), "0x607D:02": ecat.sdo_put(s, 0x607D, 2, "<i", p0 + W)})
        info["sdo_written"] = wr
        stamp = info.setdefault("stamp", time.strftime("%Y%m%d-%H%M%S"))   # main 이 시작에 정한 것 — 결과 JSON 과 같은 시각
        info["csv"] = str(DATA / (f"motion_m2_{stamp}.csv" if mode == "move" else f"motion_m3_s{a['stage']}_{stamp}.csv"
                                  if mode == "m3" else f"motion_{mode}_{stamp}.csv" if mode in ("touch", "home", "goto")
                                  else f"motion_m2_{mode}_{stamp}.csv"))
        ax = m2.Axis(p0, W, deadline)
        cyc = ecat.Cycler(m, s, p0, 3600, ctl=ax)
        log = rec.Log(Path(info["csv"]), m.expected_wkc)
        ax.idle = lambda: log.take(cyc.rows)
        ax.dead = lambda: cyc.stop or (None if cyc.is_alive() else "주기 스레드가 끝났다")
        cyc.start()
        v = ax.pause(0.3)                                    # SAFE-OP 에서 입력 PDO 로 한 번 더
        why = safety.start_problem(1, sdo["0x1018:04"], v.err, v.sw, sdo["0x605A:00"]) if v else "입력 PDO 가 안 온다"
        if why:
            raise m2.Abort(f"움직이지 않음 — {why}")
        info["op"] = ecat.go(m, s, ecat.pysoem.OP_STATE)
        proc.console(f"{rec.ts()} OP 요청 — {info['op']}")
        if s.state != ecat.pysoem.OP_STATE:
            raise m2.Abort(f"OP 에 못 감 — {info['op']}")
        info["servo_on"] = m2.servo_on(ax, MARKER)
        proc.console(f"{rec.ts()} 서보 온 (Operation enabled) · p0 {p0} · 창 ±{W}")
        info["legs"] = []
        home_why = (f"전원 재투입 — 0x607D {list(limit0)} 가 레일 표시와 다르다" if need_home and mode != "home"
                    else "home 모드" if need_home else None)
        if not touch and not need_home and info["servo_on"]["enable_ms"] >= rail.FRESH_MS:
            home_why = f"첫 서보 온 {info['servo_on']['enable_ms']} ms ≥ {rail.FRESH_MS} ms — 전원 재투입으로 본다"
            rdoc = {**rdoc, "limits": None}                  # 레일 파일을 무효로 — 여기서 멈춰도 다음 실행이 home 부터 한다
            write_json_atomic(RAIL, rdoc)
            if ha is None:
                raise m2.Abort(f"움직이지 않음 — {home_why}. home 승인이 없다 ({APPROVAL_HOME})")
            need_home = True
            torque0 = _torque_low(s, info, ha["torque_permil"], release_gil=True)   # 서보가 켜진 뒤라 GIL 을 놓고
            ax.pause(0.05)                                   # SDO 동안 걸린 Quick stop 이 있으면 여기서 드러난다
        elif not touch and not need_home:                    # 같은 전원 확정 — 기억은 지우고(끝에 다시 쓴다) 끝에 표시를 남긴다
            rail_ok = True
            rdoc = {**rdoc, "last_pos": None}
            write_json_atomic(RAIL, rdoc)
        if need_home:
            info["home"] = {"why": home_why}
            proc.console(f"{rec.ts()} [HOME] {home_why} — 레일 양 끝을 잡습니다")
            rdoc = _home(ax, info["home"], ha, rdoc, round(ha["creep_mm_s"] * k), round(TOUCH_A_MM_S2 * k), a_qs)
            ax.p0, ax.window = ax.last.pos, round(big * k)  # 창을 지금 자리 가운데로 — 레일 한계가 따로 막는다
            rdoc = {**rdoc, "enable_ms": info["servo_on"]["enable_ms"], "last_pos": ax.last.pos}
            write_json_atomic(RAIL, rdoc)                    # 레일 파일 먼저, 표시는 나중 — 중간에 죽어도 다음 실행이 home 으로 간다
            rail_ok = True                                   # 표시(0x607D)는 끝 순서에서 — 서보가 꺼진 뒤에 쓴다
            ecat.sdo_put(s, 0x6072, 0, "<H", torque0, release_gil=True)   # 힘 상한을 처음 값으로 — 이제부터는 보통 동작
            info["torque_restored"], torque0 = torque0, None
            ax.pause(0.05)                                   # SDO 동안 걸린 Quick stop 이 있으면 여기서 드러난다
            info["home"]["done"] = True
            proc.console(f"{rec.ts()} [HOME] 끝 — 왼쪽(+) {rdoc['plus_end']} · 오른쪽(−) {rdoc['minus_end']} · 길이 "
                         f"{rdoc['length'] / k:.3f} mm ({rdoc['method']})")
            if mode == "home":
                raise _Done(DONE_HOME)
            if mode in ("move", "hold", "slow", "m3") and a.get("start_from_left_mm") is None:
                raise _Done("home 다 됨 — 승인에 start_from_left_mm 가 없어 왕복은 하지 않는다 "
                            "(home 뒤에는 끝 근처라 원래 출발점이 없다)")
            start, targets = _plan(mode, a, rdoc, ax.last.pos, D, plus_is_left)
            why = next(filter(None, (rail.target_problem(t, rdoc, stop) for t in targets)), None)
            if why:
                raise m2.Abort(f"움직이지 않음 — {why}")
        info["rail"] = rdoc if not touch else None
        if mode == "goto":
            leg = _long_move(ax, info, targets[0], V, A, a_qs)
            info["legs"].append(leg)
            proc.console(f"{rec.ts()} {json.dumps(leg, ensure_ascii=False)}")
            raise _Done(DONE_GOTO)
        if not touch and start != ax.last.pos and abs(start - ax.last.pos) > 1000:   # 승인의 시작 위치로 먼저 간다
            info["start"] = _long_move(ax, info, start, V, A, a_qs)
            proc.console(f"{rec.ts()} [START] {json.dumps(info['start'], ensure_ascii=False)}")
            ax.p0, ax.window = start, W                      # 창을 새 시작점 가운데로 (위치가 시작점에 있으니 바로 걸리지 않는다)
        p0 = start
        if mode in ("move", "m3", "touch"):                  # hold · slow 는 _attended 의 [PLAN] 줄이 같은 말을 한다
            proc.console(f"[PLAN] 방향: {info['axis']['words']} — 반대로 가면 바로 Ctrl+C")
        n = a["cycles"] if mode in ("move", "m3") else 0     # hold · slow 는 이 왕복 대신 아래 _attended 의 왕복 1번
        for c in range(1, n + 1):                            # Quick stop 시험은 M2(move) 마지막 왕복에만 — M3 는 왕복만
            for target, qs in ((p0 + D, abs(D) // 2 if c == n and mode == "move" else None), (p0, None)):
                leg = {"cycle": c, **m2.move(ax, target, MARKER, V, A, a_qs, qs)}
                info["legs"].append(leg)
                proc.console(f"{rec.ts()} {json.dumps(leg, ensure_ascii=False)}")
                if qs is not None:
                    info["resume"] = m2.resume(ax, MARKER)     # Quick stop 시험 뒤 정상 복귀 — 창 안의 p0 로만
        if touch:
            reason = _touches(ax, info, touch, p0, D, V, A, a_qs)
        else:
            reason = DONE_M2 if mode in ("move", "m3") else _attended(mode, ax, info, hold_s, D, V, A, a_qs)
    except _Done as e:
        reason = str(e)
    except (ecat.Stop, m2.Abort) as e:
        reason = str(e)
    except KeyboardInterrupt:
        reason = "Ctrl+C"
        v = ax and ax.last           # ④ 판정 재료 — 정지 거리 · 0x02 가 나간 때는 _after 가 센다
        info["ctrl_c"] = {"ns": time.perf_counter_ns(), "t": time.time(), "at": rec.ts(),
                          "pos": v.pos if v else None, "target": ax.target if ax else None}
    except Exception as e:           # 모르는 예외 — 이유를 남기고 내린다
        reason = f"예외 {type(e).__name__}: {e}"
    finally:                         # 늘: Quick stop → Shutdown 0x06 → Controlword 0 → SAFE-OP → INIT → 닫기. 한 단계가 실패해도 다음으로
        old = _sigint(signal.SIG_IGN)    # 두 번째 Ctrl+C 가 이 순서를 끊지 못하게 — 끝에서 되돌린다
        if ax:
            _safely(info, "finish", lambda: m2.finish(ax))
            proc.console("[STOP] 종료 중 — 최대 약 15초, 급하면 전원 스위치")   # finish 뒤에 — 화면이 막혀도 Quick stop 은 늦지 않게
            _safely(info, "safe_op", lambda: ecat.go(m, s, ecat.pysoem.SAFEOP_STATE))
            v = ax.last                                      # 서보가 꺼졌는지 — SAFE-OP 에서도 입력은 계속 온다
            info["final"] = None if v is None else {"state": rec.state(v.sw), "statusword": f"0x{v.sw:04X}", "err": f"0x{v.err:04X}", "pos": v.pos,
                                                    "input_at": rec.ts(ax.last_t) if ax.last_t else None,   # 랜선을 뽑았으면 묵은 입력이다
                                                    "stale": bool(ax.lost_at) or not (cyc and cyc.is_alive())}
            cyc.stop = cyc.stop or "끝"
            cyc.join(5)
            _safely(info, "pdo", lambda: (log.take(cyc.rows), log.close(), log.summary())[-1])
            info["alarms"] = [(rec.ts(t), w) for t, w in ax.alarms]
            info["qs_latched"] = ax.qs
            _safely(info, "trace_csv", lambda: _trace(ax.trace, info["csv"].replace(".csv", "_trace.csv")))
            if rail_ok and ax.last is not None:              # 기억 — 다음 전원의 빠른 확인이 여기서 출발한다 (두절이면 마지막으로 본 자리)
                _safely(info, "rail_saved", lambda: (write_json_atomic(RAIL, {**rdoc, "last_pos": ax.last.pos}), ax.last.pos)[1])
        if torque0 is None and "sdo_written_torque" not in info and "torque_before" in info and "torque_restored" not in info:
            torque0 = info["torque_before"]                  # 쓰기 도중 예외 — 낮아졌을 수 있으니 처음 값으로
        if torque0 is not None:      # 낮춘 힘 상한을 처음 값으로 — 어떻게 끝났든
            _safely(info, "torque_restored", lambda: ecat.sdo_put(s, 0x6072, 0, "<H", torque0))
        if rail_ok:                  # 표시를 레일 limits 로 남긴다 — 되돌리면 다음 실행이 전원 재투입으로 오판한다
            _safely(info, "sdo_restored", lambda: _mark(s, rdoc))
        elif touch and limit0:       # 끝 터치는 창으로 좁힌 0x607D 를 처음 값으로 되돌린다
            _safely(info, "sdo_restored", lambda: {"0x607D:01": ecat.sdo_put(s, 0x607D, 1, "<i", limit0[0]),
                                                   "0x607D:02": ecat.sdo_put(s, 0x607D, 2, "<i", limit0[1])})
        if opened:
            info["closed"] = ecat.shut(m)
        if old is not None:
            _sigint(old)
    _after(info, ax, cyc)
    return reason


class _Done(Exception):
    """정상 끝 — 나머지 순서를 건너뛰고 끝 순서(finally)로 간다. 이유 문자열을 그대로 돌려준다."""


def _plan(mode: str, a: dict, rdoc: dict, here: int, D: int, plus_is_left: bool):
    """레일을 알 때의 시작점과 확인할 목표들 → (start, [목표 …]). goto 는 목표 하나, 왕복은 시작점과 시작점 + D."""
    if mode == "goto":
        t = rail.from_left(rdoc, a["to_from_left_mm"], plus_is_left)
        return t, [t]
    start = rail.from_left(rdoc, a["start_from_left_mm"], plus_is_left) if a.get("start_from_left_mm") is not None else here
    return start, [start, start + D]


def _mark(s, rdoc: dict) -> dict:
    """드라이브 0x607D 에 레일 limits 를 쓴다 — 보호가 아니라 '같은 전원' 표시 (rail.py 머리말)."""
    lo, hi = rdoc["limits"]
    return {"0x607D:01": ecat.sdo_put(s, 0x607D, 1, "<i", lo), "0x607D:02": ecat.sdo_put(s, 0x607D, 2, "<i", hi)}


def _torque_low(s, info: dict, permil, release_gil: bool = False) -> int:
    """미는 힘 상한 0x6072 를 낮춘다 → 처음 값 (끝에 되돌린다). 처음 값을 못 읽으면 낮추지 않고 멈춘다.
    쓰기가 예외로 끝나도 부르는 쪽이 되돌릴 수 있게, 처음 값은 info["torque_before"] 에 먼저 남긴다."""
    t0 = ecat.sdo(s, 0x6072, 0, "<H", release_gil)[0]
    if t0 is None:
        raise m2.Abort("움직이지 않음 — 0x6072 Max torque 를 읽지 못했다")
    info["torque_before"] = t0
    info["sdo_written_torque"] = ecat.sdo_put(s, 0x6072, 0, "<H", permil, release_gil)
    return t0


def _long_move(ax, info: dict, target: int, V: int, A: int, a_qs: int) -> dict:
    """먼 이동(goto · 시작점) — 창을 이 이동 구간 + 5 mm 로 좁힌 뒤 간다. 넓히고 → 가운데 옮기고 → 좁히는 순서라
    주기 스레드가 어느 순간을 봐도 지금 위치는 창 안이다."""
    here = ax.last.pos
    mid, half = (here + target) // 2, abs(target - here) // 2 + 5_000
    ax.window = max(ax.window, abs(mid - here) + half)
    ax.p0 = mid
    ax.window = half
    info.setdefault("windows", []).append({"p0": mid, "window": half})
    return m2.move(ax, target, MARKER, V, A, a_qs)


def _reach(ax, side: int, reach: int, V: int, A: int, a_qs: int, moves: list):
    """side 쪽으로 reach 만큼 느리게 가며 끝을 찾는다 → 닿은 위치(닿으면 다시 켜 둔다) 또는 None(안 닿고 목표에 섬)."""
    try:
        t = m2.touch(ax, ax.last.pos + side * reach, MARKER, V, A, a_qs)
    except m2.Abort as e:
        if ax.qs is None and str(e).startswith("끝에 닿지 않았다"):   # 닿지 않고 목표까지 갔다 — 서보는 켜진 채다
            return None
        raise                        # 창 밖 거부 · 핸드셰이크 · 주기 스레드 — 닿지 않음이 아니다
    moves.append({"touch": t})
    moves.append({"resume": m2.resume(ax, MARKER)})
    return t["touched_at"]


def _home(ax, h: dict, ha: dict, old, V: int, A: int, a_qs: int) -> dict:
    """레일 양 끝을 잡는다 → 새 레일 파일 내용 (쓰기는 부르는 쪽). ② 빠른 확인 → 안 되면 ③ 전체.
    끝에 닿을 때마다 back_mm 물러난다. 같은 적재물의 길이와 2 mm 넘게 다르면 Abort — 레일 파일을 쓰지 않는다."""
    k, payload, back = safety.PULSE_PER_MM, ha["payload"], round(ha["back_mm"] * safety.PULSE_PER_MM)
    ax.window, ax.touch_ferr = rail.LENGTH + 2 * rail.SEARCH_EXTRA, round(ha["touch_ferr_um"] * k / 1000)
    lengths = dict(old["lengths"]) if old else {}
    h["moves"] = moves = []

    def back_off(side: int, at: int) -> None:
        moves.append({"back": m2.move(ax, at - side * back, MARKER, V, A, a_qs)})

    try:
        q = rail.quick_plan(old, ax.last.pos, payload)
        h["quick"] = q
        if q:                        # ② 기억한 자리에 있다고 보고 가까운 끝으로
            t = _reach(ax, q["side"], q["reach"], V, A, a_qs, moves)
            ok = t is not None and abs(t - q["expect"]) <= rail.TOL
            h["quick"] = {**q, "touched": t, "ok": ok}
            if t is not None:
                back_off(q["side"], t)
            if ok:
                n = lengths[payload]
                plus, minus = (t, t - n) if q["side"] > 0 else (t + n, t)
                return rail.make(plus, minus, payload, ["plus" if q["side"] > 0 else "minus"], lengths, None, _now_iso(), method="quick")
        # ③ 전체 — + 쪽 끝, 그다음 − 쪽 끝. 창 가운데를 지금 자리로 (②에서 멀리 갔어도 목표가 창 안)
        ax.p0 = ax.last.pos
        plus = _reach(ax, 1, rail.LENGTH + rail.SEARCH_EXTRA, V, A, a_qs, moves)
        if plus is None:
            raise m2.Abort(f"+ 쪽 끝에 닿지 않았다 — 레일 길이 + {rail.SEARCH_EXTRA // k} mm 를 갔다")
        back_off(1, plus)
        n = lengths.get(payload, rail.LENGTH)
        ax.p0, ax.touched = ax.last.pos, None             # 횡단도 닿음 걸쇠를 켠 채 — 튀어나온 적재물 · 장애물에 닿으면 선다
        moves.append({"cross": m2.move(ax, plus - n + rail.SEARCH_EXTRA, MARKER, V, A, a_qs)})
        minus = _reach(ax, -1, 2 * rail.SEARCH_EXTRA, V, A, a_qs, moves)
        if minus is None:
            raise m2.Abort(f"− 쪽 끝에 닿지 않았다 — 예상 자리에서 {rail.SEARCH_EXTRA // k} mm 더 갔다")
        back_off(-1, minus)
        meas = plus - minus
        h["length_vs"] = {p: meas - n0 for p, n0 in lengths.items() if p != payload}
        if payload in lengths:
            why = rail.length_problem(meas, lengths[payload])
            if why:
                raise m2.Abort(f"움직이지 않음 — {why}")
        else:
            lengths[payload] = meas
        return rail.make(plus, minus, payload, ["plus", "minus"], lengths, None, _now_iso(), method="full")
    finally:
        ax.touch_ferr = None         # 보통 동작에서는 닿음 걸쇠를 쓰지 않는다 — 100 mm/s 의 추종 오차 1,478 이 문턱을 넘는다
        # 창은 되돌리지 않는다 — 끝 근처에 있으니 옛 창이면 바로 걸린다. 부르는 쪽이 지금 자리를 가운데로 다시 잡는다


def _touches(ax, info: dict, touch: dict, p0: int, D: int, V: int, A: int, a_qs: int) -> str:
    """끝 터치 touches 번 — 닿으면 서고(걸쇠), 다시 켜서 back_mm 물러난다. 끝에 붙인 채 끝내지 않는다. → 끝난 이유."""
    k, d = safety.PULSE_PER_MM, 1 if D > 0 else -1
    ax.touch_ferr = round(touch["touch_ferr_um"] * k / 1000)   # µm → pulse (1 pulse = 1 µm)
    back = round(touch["back_mm"] * k)
    info["touches"], info["resumes"] = [], []
    for i in range(1, round(touch["touches"]) + 1):
        t = {"n": i, **m2.touch(ax, p0 + D, MARKER, V, A, a_qs)}
        info["touches"].append(t)
        proc.console(f"{rec.ts()} [TOUCH] {i}번째 닿은 위치 {t['touched_at']} (시작점에서 {(t['touched_at'] - p0) / k:+.3f} mm)")
        info["resumes"].append(m2.resume(ax, MARKER))
        leg = {"cycle": i, **m2.move(ax, t["stopped_at"] - d * back, MARKER, V, A, a_qs)}
        info["legs"].append(leg)
        proc.console(f"{rec.ts()} {json.dumps(leg, ensure_ascii=False)}")
    at = [t["touched_at"] for t in info["touches"]]
    info["touch_summary"] = {"n": len(at), "min": min(at), "max": max(at), "spread_um": max(at) - min(at),
                             "from_start_mm": round((sum(at) / len(at) - p0) / k, 3)}
    return DONE_TOUCH


def _m3_passed() -> set:
    """data 폴더의 M3 결과 기록(motion_m3_<시각>.json)에서 깨끗이 끝난 단계 번호들. 깨진 파일은 없는 것으로 본다."""
    docs = []
    for f in sorted(DATA.glob("motion_m3_*.json")):
        doc, why = safety.read_json(f)
        if not why:
            docs.append(doc)
    return safety.m3_passed(docs, DONE_M2)


def _attended(mode: str, ax, info: dict, hold_s: float, D: int, V: int, A: int, a_qs: int) -> str:
    """hold · slow — 왕복 1번에 Enter 기다림(m2.hold: 서보 온 · 새 명령 없음 · 매 주기 검사 그대로)을 끼운다. → 끝난 이유.
    hold: 가서 멈춤(재기) → Enter → 3초 뒤 돌아와 멈춤(다시 재기). slow: Enter → 3초 뒤 느리게 가고 온다(Ctrl+C · 랜선 시험).
    Enter 앞의 기다림이 시간으로 끝나면 움직이지 않고 끝낸다. 화면 안내마다 앞에 ASCII 태그 — 한글이 깨져도 읽힌다."""
    k, p0 = safety.PULSE_PER_MM, ax.p0
    far, dist, words = f"{D / k:+g} mm", f"{abs(D) / k:g} mm", info["axis"]["words"]   # words — 축 정의 파일에서 (move 가 확인)
    info["holds"] = []
    proc.console(f"[PLAN] p0 {p0} · 목표 {p0 + D} · 방향: {words} · 창 ±{ax.window / k:g} mm · 속도 {V / k:g} mm/s · "
                 f"기다림 최대 {hold_s:g}초 — 반대로 가면 바로 Ctrl+C")

    def leg(name: str, target: int) -> None:
        info["leg_now"] = {"name": name, "start": ax.last.pos if ax.last else None, "target": target}   # 도중에 멈추면 _after 가 interrupted_leg 로
        out = {"cycle": 1, **m2.move(ax, target, MARKER, V, A, a_qs)}
        del info["leg_now"]
        info["legs"].append(out)
        proc.console(f"{rec.ts()} {json.dumps(out, ensure_ascii=False)}")

    def enter(stay: str, *say: str) -> bool:
        done = _enter_key()          # 키 버퍼를 먼저 비운다 — 이 안내 뒤에 누른 Enter 만 센다
        for line in say:
            proc.console(line)
        h = m2.hold(ax, hold_s, done)
        info["holds"].append(h)
        proc.console(f"{rec.ts()} {json.dumps(h, ensure_ascii=False)}")
        if h["ended_by"] != "Enter":
            proc.console(f"[HOLD] 시간이 다 됨 — 움직이지 않고 서보를 끕니다. 슬라이더는 {stay} 자리에 남습니다")
        return h["ended_by"] == "Enter"

    def countdown(what: str) -> None:
        proc.console(f"[MOVE 3s] 3초 뒤 {what} — 손·자를 치우세요, 멈추려면 Ctrl+C")
        ax.pause(3.0)                # 그동안에도 매 주기 검사 · 걸쇠는 그대로 — 걸리면 Abort

    if mode == "hold":
        leg("가기", p0 + D)
        if not enter(far, f"[MEASURE] ① 슬라이더가 {words} 갔나요? ② 지금 {far} 위치 — 재세요. 다 재고 손과 자를 치운 뒤 Enter "
                          f"(최대 {hold_s:g}초 — 넘으면 움직이지 않고 서보를 끕니다)"):
            return f"유지 시간 다 됨 — {far} 자리에서 움직이지 않고 끝냄"
        countdown(f"시작 위치 쪽으로 {dist} 돌아갑니다")
        leg("오기", p0)
        enter("시작", f"[MEASURE] 시작 위치로 돌아옴 — 다시 재세요, 끝나면 Enter (최대 {hold_s:g}초)")
        return DONE_M2
    trip = f"{V / k:g} mm/s 로 시작 위치에서 멀어지는 쪽으로 {dist} 갔다가 돌아옵니다 (한 쪽 약 {abs(D) / V:.0f}초)"
    if not enter("시작", f"[HOLD] 준비되면 Enter — 누르면 3초 뒤 {trip}. 최대 {hold_s:g}초 기다림",
                 "[CTRL+C] Ctrl+C 시험이면 움직이는 동안 Ctrl+C 한 번",
                 "[CABLE] 랜선 시험이면 움직이는 동안 PC 쪽 랜선을 뽑으세요"):
        return "시작 대기 시간 다 됨 — 움직이지 않고 끝냄"
    countdown(trip)
    leg("가기", p0 + D)
    leg("오기", p0)
    return DONE_M2


def _enter_key():
    """키 버퍼를 비우고, 그 뒤 Enter('\\r')를 눌렀는지 보는 done() 을 돌려준다 — hold 가 주 스레드에서 0.05초마다 부른다.
    stdin 을 읽는 스레드를 두지 않는다(종료 때 stdin 잠금 문제 · 묵은 Enter). 콘솔 전용 — _console_ok 가 먼저 거른다. 시험은 바꿔 끼운다."""
    import msvcrt

    while msvcrt.kbhit():
        msvcrt.getwch()

    def done() -> bool:
        while msvcrt.kbhit():
            if msvcrt.getwch() == "\r":
                return True
        return False
    return done


def _console_ok() -> bool:
    """hold · slow 는 콘솔 창에서만 — Enter 를 콘솔 키로 읽고, Ctrl+C 가 이 창으로 와야 한다. 시험은 바꿔 끼운다."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:                # pythonw 처럼 stdin 이 없으면
        return False


def _sigint(handler):
    """SIGINT 처리기를 바꾸고 앞의 것을 돌려준다. 주 스레드가 아니라서 못 바꾸면 None — 그때는 그대로 둔다."""
    try:
        return signal.signal(signal.SIGINT, handler)
    except Exception:
        return None


def _after(info: dict, ax, cyc) -> None:
    """장비를 다 내린 뒤 기록만 더한다 — finally 밖이라 여기서 무엇이 어긋나도 종료 순서를 건너뛰지 않는다."""
    f = info.get("final")
    if "leg_now" in info:            # 이 이동이 끝나기 전에 멈췄다 — m2.move 가 돌려주지 못해 legs 에 없다
        info["interrupted_leg"] = {**info.pop("leg_now"), "last_pos": ax.last.pos if ax and ax.last else None}
    c = info.get("ctrl_c")
    if c:                            # ④ 는 끝난 이유 Ctrl+C · 0x02 가 실제로 나감 · 정지 거리 ≤ 5 mm 셋 다여야 통과
        rows = ax.trace if ax else []
        c["qs_sent_ms"] = next((round((r[0] - c["ns"]) / 1e6, 1) for r in rows if r[0] >= c["ns"] and r[1] == m2.QUICK_STOP), None)
        ok = bool(f) and not f["stale"] and c["pos"] is not None
        c["stop_pos"] = f["pos"] if ok else None
        c["stop_distance_mm"] = round(abs(f["pos"] - c["pos"]) / safety.PULSE_PER_MM, 3) if ok else None
        g, p = info.get("interrupted_leg"), c["pos"]   # 움직이는 중이었나 — 이동 도중 · 출발점과 목표에서 1 mm 넘게 떨어진 곳
        c["moving"] = bool(g) and None not in (p, g["start"], g["target"]) and \
            min(abs(p - g["start"]), abs(p - g["target"])) >= safety.PULSE_PER_MM
    died = cyc is not None and str(cyc.stop).startswith("주기 스레드 예외")
    if ax and (ax.lost_at or died):  # 랜선 시험 — WKC 로 알았나, 주기 스레드가 예외로 죽었나
        v = ax.last
        x = ax.lost_at or {"at": None, "why": cyc.stop, "last_t": ax.last_t, "target": ax.target,
                           "last_pos": None if v is None else v.pos, "last_sw": None if v is None else v.sw}
        info["comms_lost"] = {"source": "WKC" if ax.lost_at else "주기 스레드 예외", **x,
                              "at_ts": rec.ts(x["at"]) if x["at"] else None, "last_ts": rec.ts(x["last_t"]) if x["last_t"] else None}


def _summary(info: dict, path) -> list[str]:
    """화면 맨 끝 몇 줄 — 현장에서 바로 적을 것. 한글이 깨져도 앞의 [태그] 는 읽힌다."""
    k, p0 = safety.PULSE_PER_MM, (info.get("checks") or {}).get("p0")

    def at(p) -> str:
        if p is None:
            return "-"
        return str(p) if p0 is None else f"{p} (시작점에서 {(p - p0) / k:+.3f} mm)"

    out = ["", f"[DONE] 끝난 이유: {info.get('exit')}", f"[DONE] 결과 파일 {path}"]
    if info.get("legs"):
        out.append("[DONE] 이동 " + " · ".join(f"{g['target']} 까지 오차 {g.get('err_um', '-')} um" for g in info["legs"]))
    g = info.get("interrupted_leg")
    if g:
        out.append(f"[DONE] 끝나지 못한 이동 {g['name']} {g['start']} → {g['target']} · 마지막으로 본 위치 {at(g['last_pos'])}")
    holds = info.get("holds") or []
    if info.get("mode") == "hold" and holds:
        out.append("[MEASURE] 멈춰 있던 위치 " + " · ".join(at(h["pos"]) for h in holds)
                   + (f" — 두 번 잰 값의 차가 {info['approval']['distance_mm']} ± 0.5 mm 면 ② 통과" if len(holds) == 2 else ""))
    c = info.get("ctrl_c")
    if c:
        q, d = c.get("qs_sent_ms"), c.get("stop_distance_mm")
        ok = info.get("exit") == "Ctrl+C" and q is not None and d is not None and d <= 5 and c.get("moving")
        sent = "안 나감" if q is None else f"누른 뒤 {q} ms 에 나감"
        verdict = "통과" if ok else "통과 아님" if c.get("moving") else "판정 불가"
        out.append(f"[CTRL+C] 누른 때 위치 {at(c['pos'])} · Quick stop(0x02) {sent} · 멈춘 위치 {at(c.get('stop_pos'))} · "
                   f"정지 거리 {'-' if d is None else d} mm → ④ {verdict} (조건: 움직이는 중 · 0x02 나감 · 5 mm 이하)")
        if not c.get("moving"):
            out.append("[CTRL+C] 움직이는 중이 아니었다 — ④ 판정 불가 (출발점 · 목표에서 1 mm 넘게 떨어진 곳에서 누른다)")
    x = info.get("comms_lost")
    if x:
        out.append(f"[CABLE] 통신 두절 감지({x['source']}) {x['at_ts'] or '-'} · 마지막으로 본 위치 {at(x['last_pos'])}"
                   f" ({x['last_ts'] or '-'}) · 목표 {x['target']}")
        out.append("[CABLE] 적을 것: 1) 드라이브 앞면 알람 번호 2) 슬라이더가 선 자리 — 랜선을 다시 꽂고 전원을 끄기 전에 scan 을 돌려 "
                   "pdo.now.pos 를 읽는다 (SAFE-OP 가 거부되면 sdo 의 0x6064:00) 3) 어떻게 풀었나(전원 재투입 · Home 재배치)")
    return out


def _safely(info: dict, key: str, fn) -> None:
    try:
        info[key] = fn()
    except Exception as e:
        info[key] = f"실패 {type(e).__name__}: {e}"


def _trace(trace, path: str) -> str:
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_mono_ns", "cw_sent", "target_sent", "statusword", "err_code", "mode", "pos", "ferr"])
        w.writerows(trace)
    return path


def cycle(m, s, pos: int, seconds: float, csv_path: Path | None = None, beat_path: Path | None = None):
    """주기 스레드를 돌리고, 이 (주) 스레드는 0.1초마다 행을 꺼내 CSV 에 쓰고 1초마다 심박을 쓴다 → (멈춘 이유, Log)."""
    cyc = ecat.Cycler(m, s, pos, seconds)
    log = rec.Log(csv_path, m.expected_wkc)
    started = say = beat = time.time()
    cyc.start()
    try:
        while cyc.is_alive():
            time.sleep(0.1)
            log.take(cyc.rows)
            now = time.time()
            if beat_path and now - beat >= 1.0:
                rec.write_beat(beat_path, started, log, None)
                beat = now
            if now - say >= 60:
                c, v = log.summary(500), log.last
                proc.console(f"{rec.ts()} 주기 {log.n} · WKC 오류 {log.wkc_err} · 최근 1초 p99 {c['cycle_ms'].get('p99')} ms"
                             f" · {c['now']['state'] if v else '-'} · pos {v.pos if v else '-'}")
                say = now
    except KeyboardInterrupt:
        cyc.stop = cyc.stop or "Ctrl+C"
    finally:
        cyc.stop = cyc.stop or "기록 쪽 예외"   # 이 스레드가 예외로 빠져나가도 주기 스레드는 세운다
        cyc.join(5)
        log.take(cyc.rows)
        log.close()
    if beat_path:
        rec.write_beat(beat_path, started, log, cyc.stop, final=True)
    return cyc.stop, log


def main() -> int:
    ap = argparse.ArgumentParser(description="리니어 모터 모션 엔진 — M1 읽기 전용 · M2 입회 왕복")
    ap.add_argument("mode", choices=["scan", "monitor", *MODES])
    ap.add_argument("--minutes", type=float, default=10.0, help="monitor 시간 (기본 10분)")
    ap.add_argument("--hold-s", type=float, default=120.0, help=f"hold · slow — Enter 를 기다리는 최대 초 (기본 120, 상한 {m2.HOLD_MAX_S})")
    ap.add_argument("--mm-s", type=float, default=2.0, help="slow 속도 mm/s (기본 2 — 1 이상, 승인 vmax 이하)")
    args = ap.parse_args()
    proc.safe_stdio()
    DATA.mkdir(exist_ok=True)
    lock = proc.acquire_lock(DATA / "motion.lock")     # 장비는 한 프로세스만 만진다 — 두 번째 엔진은 시작하지 않는다
    if lock is None:
        proc.console("다른 모션 엔진이 이미 돈다 — 시작하지 않는다")
        return 1
    sys.setswitchinterval(0.0005)    # 주기 스레드가 GIL 을 기다리는 시간을 0.5 ms 안으로 (기본 5 ms)

    info = {"t": time.time(), "at": rec.ts(), "mode": args.mode, "ifname": ecat.IFNAME, "stamp": time.strftime("%Y%m%d-%H%M%S")}
    m = ecat.pysoem.Master()
    if args.mode in MODES:
        # hold · slow 는 실행마다 새 이름 — 앞의 실행 기록(motion_m2.json 포함)을 덮지 않는다
        path = DATA / ("motion_m2.json" if args.mode == "move" else f"motion_m3_{info['stamp']}.json" if args.mode == "m3"
                       else f"motion_{args.mode}_{info['stamp']}.json" if args.mode in ("touch", "home", "goto")
                       else f"motion_m2_{args.mode}_{info['stamp']}.json")
        try:
            info["exit"] = move(m, info, args.mode, args.hold_s, args.mm_s)
        except BaseException as e:   # move 밖으로 샌 예외도 결과 파일에 남긴다
            info["exit"] = f"예외 {type(e).__name__}: {e}"
            raise
        finally:
            info["emergencies"] = ecat.emergencies
            info["ended_at"] = rec.ts()
            old = _sigint(signal.SIG_IGN)    # 늦은 Ctrl+C 가 결과 파일 쓰기를 끊지 못하게
            write_json_atomic(path, info)
            if old is not None:
                _sigint(old)
        proc.console(json.dumps(info, ensure_ascii=False, indent=1, default=str))
        for line in _summary(info, path):
            proc.console(line)
        return 0 if info["exit"] in (DONE_M2, DONE_TOUCH, DONE_HOME, DONE_GOTO) else 1
    try:
        m.open(ecat.IFNAME)
    except Exception as e:           # Npcap 권한 등 — 우회(관리자 권한 상승)하지 않고 그대로 기록하고 끝낸다
        info["exit"] = f"어댑터를 열지 못함 — {type(e).__name__}: {e}"
        write_json_atomic(DATA / "drives.json", info)
        proc.console(info["exit"])
        return 1
    reason = None
    try:
        s, pos = ecat.bring_up(m, info)
        if args.mode == "monitor":
            stamp = time.strftime("%Y%m%d-%H%M%S")
            info["csv"] = str(DATA / f"motion_{stamp}.csv")
            proc.console(f"{rec.ts()} SAFE-OP · monitor {args.minutes}분 시작 — {info['csv']}")
            reason, log = cycle(m, s, pos, args.minutes * 60, Path(info["csv"]), DATA / "motion.json")
        else:
            reason, log = cycle(m, s, pos, 1.0)
        info["pdo"] = log.summary()
    except ecat.Stop as e:
        reason = str(e)
    except KeyboardInterrupt:
        reason = "Ctrl+C"
    except Exception as e:           # 모르는 예외 — 이유를 남기고 INIT 로 내린 뒤 그대로 올린다
        reason = f"예외 {type(e).__name__}: {e}"
        raise
    finally:
        info["closed"] = ecat.shut(m)
        info["exit"] = reason
        info["emergencies"] = ecat.emergencies
        info["ended_at"] = rec.ts()
        write_json_atomic(DATA / "drives.json", info)
    proc.console(json.dumps(info, ensure_ascii=False, indent=1))
    return 0 if reason == ecat.DONE else 1


if __name__ == "__main__":
    sys.exit(main())
