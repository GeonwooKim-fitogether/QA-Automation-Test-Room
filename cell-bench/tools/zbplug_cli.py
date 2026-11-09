"""지그비 플러그(레일 독 충전) 손 조작 — 보드 ESP32-C6 + 아카라 SP-EUC01.

  python tools/zbplug_cli.py ping          보드가 살아 있나 · 망 · 짝지은 플러그
  python tools/zbplug_cli.py pair [초]     짝짓기 창을 열고(기본 120초) 붙는 것을 지켜본다. 그동안 플러그 버튼을 길게(약 5초) 누른다
  python tools/zbplug_cli.py status        켜짐 · 전력
  python tools/zbplug_cli.py on | off      켜기 · 끄기 — 독 충전이 바뀐다
  python tools/zbplug_cli.py info          플러그 제조사 · 모델 · 펌웨어
  python tools/zbplug_cli.py desc          플러그의 엔드포인트 · 클러스터(전력 측정 지원 여부)
  python tools/zbplug_cli.py setup         켜짐 · 전력 보고 묶기를 다시 한다
  python tools/zbplug_cli.py watch [초]    보드가 내는 줄을 그대로 본다(기본 30초)
  python tools/zbplug_cli.py raw <명령>    보드 명령을 그대로 보낸다(예: raw FORGET)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench.zbplug import ZbPlug, reading  # noqa: E402


def stamp(line: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {line}", flush=True)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "ping"
    p = ZbPlug()
    if cmd == "ping":
        _, kv, rest = p.ask("PING")
        print(rest)
    elif cmd == "pair":
        sec = int(argv[2]) if len(argv) > 2 else 120
        stamp(f"짝짓기 창 {sec}초 — 플러그 버튼을 파란 불이 깜빡일 때까지(약 5초) 누른다. 붙으면 바로 끝낸다")
        joined = []

        def see(line: str) -> None:
            stamp(line)
            if line.startswith("EVT JOIN"):
                joined.append(line)
                return True
            return None

        end =time.monotonic() + sec
        while time.monotonic() < end and not joined:      # 보드가 한 번에 여는 창은 254초까지라 나눠 연다
            win = int(min(240, end - time.monotonic()))
            if win < 5:
                break
            p.ask(f"JOIN {win}")
            p.listen(win, see)
        if joined:
            p.listen(8, stamp)                             # 붙은 직후 보드가 묻는 기본 정보 · 묶기 결과
        p.ask("CLOSE")
        stamp(f"끝 — 붙은 기기 {len(joined)}개")
    elif cmd == "status":
        _, kv, _ = p.ask("STATE")
        r = reading(kv)
        print(f"{'켜짐' if r.on else '꺼짐' if r.on is False else '모름'} · {r.watts:.1f} W (전력 출처 {r.src}) · {kv}")
    elif cmd in ("on", "off"):
        r = p.on() if cmd == "on" else p.off()
        print(f"{'켜짐' if r.on else '꺼짐'} · {r.watts:.1f} W · 명령 응답 {r.ack} · {p.last_call_s}초")
    elif cmd in ("info", "desc", "setup"):
        _, _, rest = p.ask(cmd.upper(), timeout_s=20, on_line=stamp)
        print(rest)
    elif cmd == "watch":
        p.listen(float(argv[2]) if len(argv) > 2 else 30, stamp)
    elif cmd == "raw":
        _, _, rest = p.ask(" ".join(argv[2:]), timeout_s=20, on_line=stamp)
        print(rest)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
