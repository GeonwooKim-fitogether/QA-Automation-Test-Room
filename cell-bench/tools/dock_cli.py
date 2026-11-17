"""Dock 명령 수동 도구 (cellbench/dock.py) — 실물 확인(recovery-plan 4-4)과 수동 보내기.

  python -X utf8 tools/dock_cli.py probe --set 1                                  # 무해한 줄(PING)로 echo 만 확인 — 셀에 닿지 않는다
  python -X utf8 tools/dock_cli.py send  --set 1 --cmd CELL_AON                   # 보낼 것만 보여 준다 (포트를 열지 않는다)
  python -X utf8 tools/dock_cli.py send  --set 1 --cmd CELL_AON --all-off --yes   # 보낸다
  python -X utf8 tools/dock_cli.py wake  --set 1 --all-off --yes                  # 세트 줄 dock.wake 순서를 2초 간격으로 보낸다

--all-off 는 '이 세트의 셀 24대가 모두 꺼져 있음을 확인했다'는 사람의 확인이다. 켜진 셀이 있는 세트를 누르면 그 셀이 꺼질 수 있다
(CELL_EN_INIT 은 켜진 셀을 끄는 명령이고, CELL_AON 의 1.5초 누름도 켜진 셀의 끄기 문턱 2.0초와 여유가 0.5초뿐이다).
보낼 수 있는 명령은 CELL_AON · CELL_EN_INIT 뿐이다(허용 목록 config.DOCK). CELL_MES · CELL_OFF 등은 거부한다.
스위치(세트 줄 dock.power_on)는 엔진의 자동 사용만 막는다 — 이 도구는 스위치가 꺼져 있어도 --all-off --yes 로 보낸다(실물 확인은 스위치를 켜기 전에 한다).
설정은 엔진과 같은 bench.json 의 세트 줄 dock 블록에서 읽는다:
  "dock": {"port": "COM28", "usb_serial": "A50285BI", "power_on": false, "wake": ["CELL_EN_INIT", "CELL_AON"]}
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cellbench.config import BENCH_FILE, DOCK, Config  # noqa: E402
from cellbench.dock import Dock, DockResult  # noqa: E402


def say(*a) -> None:
    print(*a, flush=True)


def show(r: DockResult) -> None:
    where = f"포트 {r.port} ({r.found_by} 로 찾음 · {r.device})" if r.port else "포트 없음"
    say(f"{r.cmd}: {where}")
    if r.port:
        say(f"  쓴 바이트 {r.wrote} · echo {'일치' if r.echo_ok else '없음/다름'} · 응답 {r.reply!r} · {r.seconds:.2f}초")
        say(f"  받은 바이트 {r.raw!r}")
    say("  → 통과" if r.ok else f"  → 실패: {r.reason}")


def preview(dock: Dock, cmds: list[str]) -> int:
    """--yes 가 없을 때 — 무엇을 어디로 보낼지만 보여 준다. 포트 목록만 읽고 포트는 열지 않는다."""
    if dock.problems:
        say("설정 문제 — 보낼 수 없다: " + "; ".join(dock.problems))
        return 1
    if not cmds:
        say("dock.wake 순서가 비어 있다 — 실물 확인으로 정한 순서를 세트 줄에 적는다")
        return 1
    bad = [c for c in cmds if c not in DOCK["allowed"]]
    if bad:
        say(f"허용 목록 밖의 명령 — 보낼 수 없다: {bad} (보낼 수 있는 것: {' · '.join(DOCK['allowed'])})")
        return 1
    port, how, device, why = dock.locate()
    say(f"세트 {dock.set_id} Dock — " + (f"포트 {port} ({how} 로 찾음 · {device})" if port else f"포트를 못 찾음: {why}"))
    for c in cmds:
        say(f"  보낼 것: {c}  (바이트 {dock.plan(c)!r})")
    say(f"스위치 dock.power_on = {str(dock.settings.get('power_on')).lower()} — 엔진의 자동 사용 여부다. 이 도구는 스위치와 무관하게 보낸다")
    say("보내지 않았다 — 보내려면 --all-off --yes 를 붙인다 (--all-off = 이 세트의 셀 24대가 모두 꺼져 있음을 확인했다)")
    return 0


def main(argv: list[str] | None = None, make_dock=Dock) -> int:
    ap = argparse.ArgumentParser(description="Dock 명령 수동 도구 — 정체 확인 · 셀 전체 켜기 명령 보내기")
    ap.add_argument("action", choices=["probe", "send", "wake"])
    ap.add_argument("--set", type=int, required=True, help="세트 id (bench.json 세트 줄)")
    ap.add_argument("--cmd", default=None, help=f"send 의 명령 ({' · '.join(DOCK['allowed'])})")
    ap.add_argument("--all-off", action="store_true", help="이 세트의 셀이 모두 꺼져 있음을 확인했다")
    ap.add_argument("--yes", action="store_true", help="정말 보낸다 (없으면 보낼 것만 보여 준다)")
    ap.add_argument("--bench", default=str(BENCH_FILE))
    a = ap.parse_args(argv)
    if a.action == "send" and not a.cmd:
        ap.error("send 에는 --cmd 가 필요하다")
    cfg = Config.load(None, bench_path=a.bench)
    dock = make_dock(cfg, a.set)

    if a.action == "probe":
        r = dock.probe()
        show(r)
        if r.ok:
            say("정체 확인 통과 — 보낸 바이트가 그대로 돌아왔다(읽은 펌웨어와 같은 echo). 응답이 비어 있어야 정상이다(모르는 줄은 무시)")
        return 0 if r.ok else 1

    cmds = [a.cmd] if a.action == "send" else list((dock.settings or {}).get("wake") or [])
    if not a.yes:
        return preview(dock, cmds)
    if a.action == "send":
        r = dock.send(a.cmd, all_off_confirmed=a.all_off, manual=True)
        show(r)
        return 0 if r.ok else 1
    w = dock.wake(all_off_confirmed=a.all_off, manual=True)
    for r in w.steps:
        show(r)
    say("순서 통과" if w.ok else f"순서 실패: {w.reason}")
    return 0 if w.ok else 1


if __name__ == "__main__":
    sys.exit(main())
