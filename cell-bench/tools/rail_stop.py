"""레일 멈춤 — 총괄(채팅) · 사람이 부르는 정지 명령. 레일 옆 화면(tools/rail_stop_gui.py)과 같은 함수를 부른다.

  python cell-bench/tools/rail_stop.py                 # 멈춤 — 정지 표지를 만들고 모터 기록으로 확인될 때까지 기다린다(최대 3초)
  python cell-bench/tools/rail_stop.py --why "독 옮김"   # 까닭을 함께 남긴다
  python cell-bench/tools/rail_stop.py --status        # 지금 상태만 본다(아무것도 바꾸지 않는다)

끝 코드: 0 = 멈춤 확인(또는 운전 중인 모터 프로그램이 없어 다음 운전만 막음) · 2 = 정지 요청 실패(표지를 못 만듦) · 3 = 확인 못 함.
**마지막 줄을 그대로 사용자에게 옮긴다. 이 줄이 나오기 전에는 '멈췄다'고 말하지 않는다.**
정지 풀기는 여기 없다 — 다시 움직이게 하는 일이라 레일 옆 '레일 멈춤' 창에서만 한다(#17 사용자 확정 D2).
프로세스를 강제로 끄는 것은 정지 수단이 아니다(venv 런처만 끄면 실행기가 산다) — 정지는 늘 이 명령이나 레일 옆 창이다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cellbench import proc  # noqa: E402
from motionbench import drive_lock, rail_stop  # noqa: E402

OK, FAILED, UNCONFIRMED = 0, 2, 3


def motor_running() -> tuple[bool, str]:
    """지금 모터 프로그램이 드라이브를 쥐고 있나 → (쥐고 있나, 쥔 쪽 한 줄). 잠금을 잠깐 쥐어 보고 되면 바로 놓는다."""
    try:
        held = drive_lock._locked_by_someone(drive_lock.DRIVE)
    except drive_lock.Busy as e:          # 잠금 자리를 못 연다 — 모른다. 돌고 있는 것으로 보고 확인 파일을 기다린다
        return True, f"드라이브 잠금을 못 읽음 — {e}"
    return held, (drive_lock.holder(drive_lock.DRIVE) if held else "드라이브 잠금 비어 있음")


def holder_pid() -> int | None:
    if not motor_running()[0]:
        return None
    try:
        return int(drive_lock._read(drive_lock.DRIVE).get("pid"))
    except (TypeError, ValueError):
        return None


def status_lines() -> list[str]:
    run, who = motor_running()
    st = rail_stop.active()
    out = [f"지금: {'운전 중 — ' + who if run else '운전 없음'}",
           "정지 표지 없음" if st is None else f"정지 표지 있음 — {st.get('at', '?')} {st.get('by', '?')} · {st.get('why', '?')}",
           rail_stop.watch_matches(holder_pid())[1]]
    ign = rail_stop.env_ignored()
    return out + ([ign] if ign else [])


def stop(by: str, why: str, say=print) -> int:
    """정지 요청 → 확인. 마지막으로 찍는 줄이 사용자에게 옮길 줄이다."""
    try:
        req = rail_stop.request(by, why)
    except OSError as e:
        say(f"정지 요청 실패 — 드라이브 전원 스위치를 끄세요 ({rail_stop.stop_dir()} · {type(e).__name__}: {e})")
        return FAILED
    if req.get("existing"):
        say(f"정지 표지가 이미 있다 — {req.get('at', '?')} {req.get('by', '?')} · {req.get('why', '?')}")
    say(f"멈추는 중… (요청 {req.get('at', '?')} · 표지 {rail_stop.stop_dir() / rail_stop.STOP})")
    code, line = rail_stop.wait_confirm(req, motor_running)
    say(line)
    return OK if code == 0 else UNCONFIRMED


def main(argv=None) -> int:
    proc.safe_stdio()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--why", default="사람 요청", help="까닭 한 줄")
    ap.add_argument("--by", default="총괄(rail_stop.py)", help="누가")
    ap.add_argument("--status", action="store_true", help="상태만 본다")
    a = ap.parse_args(argv)
    if a.status:
        for line in status_lines():
            print(line)
        return OK
    return stop(a.by, a.why)


if __name__ == "__main__":
    raise SystemExit(main())
