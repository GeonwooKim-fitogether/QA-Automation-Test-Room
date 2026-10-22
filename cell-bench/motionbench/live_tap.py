"""셀 라이브 원시 기록 — 받은 데이터그램을 PC 수신 시각과 함께 그대로 파일에 덧붙인다 (dynamic-bench plan-v3 1.3 · 3.4).

cell-bench 의 LiveListener 를 상속만 한다 — cell-bench 파일은 고치지 않는다(cells.py 의 sink 는 뒤의 D1). 라이브 포트(UDP 60222)는
한 프로그램만 열 수 있어, 이것이 도는 동안 run_cycle.py 는 돌리지 않는다(돌고 있으면 PortBusy 로 시작하지 않는다).
파싱은 하지 않는다 — speed_history 의 순서가 아직 확인되지 않아 원본을 남기는 쪽이 안전하다. 읽기는 read_raw → protocol.parse_gps.

파일: data/live_raw/<시각>.bin — 레코드마다 [t_wall µs u64 LE][길이 u16 LE][데이터그램 그대로].
t_wall = time.time() 으로 cells.py:107 이 넘겨주는 수신 시각 그대로이고, 모터 기록(motion_record.py)의 t_wall 과 같은 PC 시계다.

사용 (작업 트리 루트에서):  .venv/Scripts/python.exe cell-bench/motionbench/live_tap.py --minutes 15     (Ctrl+C 로 먼저 끝내도 된다)
"""
from __future__ import annotations

import argparse
import struct
import sys
import threading
import time
from pathlib import Path

if __name__ == "__main__":       # 스크립트로 띄울 때만 cell-bench 를 경로에 넣는다
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cellbench import proc  # noqa: E402
from cellbench.cells import LiveListener, PortBusy  # noqa: E402
from cellbench.config import Config  # noqa: E402

REC = struct.Struct("<QH")
DATA = Path(__file__).resolve().parents[1] / "data" / "live_raw"


class LiveTap(LiveListener):
    """LiveListener 그대로 + 받은 것을 파일에. 셀 표(now 판정)는 부모가 그대로 갱신한다.
    끝을 기다릴 때는 join() · is_alive() 대신 done 을 본다 — 부모의 self._stop(Event)이 Thread._stop() 을 가려서,
    Python 3.11 에서는 끝난 이 스레드에 join() · is_alive() 를 부르면 TypeError 가 난다. 파일은 이 스레드가 끝나며 닫는다."""

    def __init__(self, cfg: Config, path: Path):
        super().__init__(cfg)
        self.out = open(path, "ab", buffering=0)    # 버퍼 없이 — 프로그램이 죽어도 받은 데까지는 남는다
        self.n = 0
        self.done = threading.Event()

    def run(self) -> None:
        try:
            super().run()
        finally:
            self.out.close()
            self.done.set()

    def _handle(self, d: bytes, ip: str, now: float) -> None:
        self.out.write(REC.pack(round(now * 1e6), len(d)) + d)
        self.n += 1
        super()._handle(d, ip, now)


def read_raw(path) -> list[tuple[float, bytes]]:
    """live_raw 파일 → [(수신 시각 t_wall, 데이터그램)]. 쓰다 끊긴 마지막 레코드는 버린다."""
    b, out, i = Path(path).read_bytes(), [], 0
    while i + REC.size <= len(b):
        t, n = REC.unpack_from(b, i)
        i += REC.size
        if i + n > len(b):
            break
        out.append((t / 1e6, b[i:i + n]))
        i += n
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="셀 라이브 원시 기록 (슬라이스) — data/live_raw/<시각>.bin")
    ap.add_argument("--minutes", type=float, default=30.0, help="기록 시간 (기본 30분, Ctrl+C 로 먼저 끝낼 수 있다)")
    args = ap.parse_args()
    proc.safe_stdio()
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"{time.strftime('%Y%m%d-%H%M%S')}.bin"
    try:
        tap = LiveTap(Config(), path)
    except PortBusy as e:
        proc.console(str(e))
        return 1
    tap.start()
    proc.console(f"{time.strftime('%H:%M:%S')} 기록 시작 → {path}")
    end = time.time() + args.minutes * 60
    say = time.time() + 10
    try:
        while time.time() < end and not tap.done.is_set():     # 수신 스레드가 죽으면(쓰기 실패 등) 곧바로 빠진다
            time.sleep(0.5)                                     # Windows 에서 Ctrl+C 를 바로 받는 대기
            if time.time() >= say:
                proc.console(f"{time.strftime('%H:%M:%S')} 받은 것 {tap.n}개 · 지금 들리는 셀 {len(tap.alive())}대")
                say += 10
    except KeyboardInterrupt:
        pass
    finally:
        tap.stop()
        tap.done.wait(2)                            # 소켓 대기 0.5초 안에 끝나고 파일을 닫는다
    proc.console(f"끝 — {tap.n}개 → {path}")
    return 0 if tap.n else 1


if __name__ == "__main__":
    sys.exit(main())
