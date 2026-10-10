"""무인 충방전 사이클 실행.

사용:
  python run_cycle.py                      # 1사이클
  python run_cycle.py --cycles 3
  python run_cycle.py --dry-run            # 플러그·셀을 건드리지 않고 흐름만 (라이브 수신은 함)
  python run_cycle.py --config my.json     # Config 필드를 JSON 으로 덮어씀 (bench.json 위에 한 번 더)

멈추려면 Ctrl+C. 플러그는 마지막 상태 그대로 남으니 멈춘 뒤 tools/plug_cli.py 로 확인한다.
다 돌았거나, 원격 안전 정지·연속 실패로 멈출 때는 플러그를 켜 두고 끝낸다(충전 쪽이 안전).

시작할 때 설정 검사(config.validate)와 이 PC 의 시험망 주소 중복(net.ip_state)을 보고, 문제가 있으면 시작하지 않는다
(engine.json exit=config_error — 감시자는 되살리지 않고 사람을 부른다).

감시자(supervise.py)가 읽도록 data/engine.json 에 시작 정보와 끝난 이유(exit)를 남긴다. 이유 없이 사라지면
감시자가 플러그를 켜고 남은 사이클로 다시 띄운다. Ctrl+C 로 멈추면 exit=interrupted 라 되살리지 않는다.
감시자가 띄운 엔진은 창이 없으므로, 일부러 멈추려면 결과판의 '안전 정지'를 쓰거나 data/supervisor_pause 를 먼저 둔다
(작업 관리자에서 끝내면 감시자는 비정상 종료로 보고 되살린다).
"""
from __future__ import annotations

import argparse
import atexit
import os
import sys
import time
from pathlib import Path

from cellbench import net, proc
from cellbench.alert import make_notifier
from cellbench.cells import CellLink, LiveListener, PortBusy
from cellbench.cloud import Cloud
from cellbench.config import Config, validate
from cellbench.control import read_json, write_json_atomic
from cellbench.cycle import CycleRunner
from cellbench.plug import Plug
from cellbench.record import ENGINE_FILE, Recorder
from cellbench.supervisor import pid_matches


def main() -> int:
    ap = argparse.ArgumentParser(description="셀 내구 시험대 무인 충방전")
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--precharge", action="store_true", help="시작 전에 플러그를 껐다 켜 만충까지 충전 (첫 사이클을 100%% 에서)")
    args = ap.parse_args()
    proc.safe_stdio()          # 화면이 파일·DEVNULL 이어도 '—' 같은 글자로 죽지 않게 (검토 F1)

    try:
        cfg = Config.load(args.config)
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"설정을 읽지 못함 — 시작하지 않는다: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        config_error(Path(Config().data_dir), args, e)
        return 2
    why = start_problem(cfg)
    if why:
        print(f"시작하지 않는다: {why}", file=sys.stderr, flush=True)
        config_error(Path(cfg.data_dir), args, why, bench_id=cfg.bench_id)
        return 2
    notify = make_notifier()
    rec = Recorder(cfg.data_dir, alert=notify)
    # 클라우드 로그는 run.log 로 (print 로만 가면 감시자가 창 없이 띄운 엔진에서는 사라진다).
    # 키가 3번 연속 거부되면 Slack 으로, 전송 실패는 10분에 한 번 events 의 cloud_fail 로. 시험대 식별자는 설정 한 곳(cfg.bench_id)에서.
    rec.cloud = Cloud(log=rec.log, sample_every_s=cfg.cloud_sample_s, bench_id=cfg.bench_id,
                      on_alert=lambda m: notify(f"[셀 시험대 {cfg.bench_id}] 조치 · 클라우드 · {m}"),
                      on_fail=lambda kind, detail: rec.event(rec.next_cycle_no(), "-", "cloud_fail", "-", detail))
    rec.log("설정: " + cfg.dump().replace("\n", " "))

    try:
        live = LiveListener(cfg)
    except PortBusy as e:
        # 다른 엔진이 이미 돈다. 그 엔진의 engine.json 을 덮어쓰지 않도록 아무 기록도 남기지 않고 끝낸다.
        rec.log(f"시작하지 않음 — {e}")
        return 3
    rec.engine_start(engine_info(cfg, args, rec.next_cycle_no()))
    plugs: list[Plug] = []
    atexit.register(last_gasp, rec, plugs, cfg.stuck_rule()[2])
    runner = None
    try:
        live.start()
        time.sleep(3)
        heard = live.alive()
        rec.log(f"라이브 신호 {len(heard)}/{len(cfg.serials)}대")
        if not heard and not args.dry_run:
            rec.log("셀이 하나도 안 들린다 — Wi-Fi 가 LiveHub(FTG-3D93-5G)에 붙어 있는지, 셀이 켜져 있는지 확인")
            rec.engine_exit("no_cells")
            return 2

        plug = None
        if not args.dry_run:
            plug = Plug(cfg, stats_path=Path(cfg.data_dir) / "plug_stats.json"); plugs.append(plug)
            r = plug.read()
            rec.log(f"플러그 {plug.ip} · {'켜짐' if r.on else '꺼짐'} · {r.watts:.1f} W")

        def link_log(msg: str) -> None:
            # 추출 중에는 표본(_poll)이 돌지 않아 now.json 이 몇 분씩 멈춘다. 셀 하나하나의 진척 로그마다 심박을 찍어
            # 감시자가 추출을 '멈춤'으로 오판하지 않게 한다. 별도 스레드로 찍지 않는다 — 흐름이 멈추면 심박도 멈춰야 한다.
            rec.log(msg); rec.beat()

        link = CellLink(cfg, live, link_log, progress=rec.beat)
        runner = CycleRunner(cfg, live, plug, link, rec, dry_run=args.dry_run)
        kind = runner.run(args.cycles, precharge=args.precharge)
        rec.engine_exit(kind, plug_on=runner.plug_on_at_exit)
        return 0
    except KeyboardInterrupt:
        rec.log("사용자 중단 — 플러그 상태는 그대로다")
        note_interrupt(rec, runner)
        rec.engine_exit("interrupted")
        return 130
    finally:
        live.stop()


def note_interrupt(rec: Recorder, runner) -> None:
    """Ctrl+C 로 끝날 때 마지막으로 안 플러그 상태가 '꺼짐'이면 run.log 와 events 에 노랑 이상을 남긴다 (검토 F14).

    플러그는 사람 뜻대로 그대로 두지만(되살리지도 켜지도 않는다), 꺼진 채라면 셀이 방전 중이라는 것을 알아야 한다.
    플러그에 다시 묻지 않는다 — 끝내는 길에서 플러그 호출(최대 1분 남짓)로 붙잡히지 않게, 엔진이 마지막으로 받은 응답(plug_known)을 쓴다.
    모르면(아직 플러그를 읽지 않았거나 모의) 남기지 않는다. 일시 중지 표지가 없으면 감시자가 10분 안에 켠다(plug_guard).
    """
    if runner is None or getattr(runner, "plug_known", None) is not False:
        return
    st = getattr(runner, "current", None)
    rec.event(st.cycle if st else rec.next_cycle_no(), st.phase if st else "-", "interrupted_plug_off", "-",
              "사람이 멈춤(Ctrl+C) · 플러그 꺼짐 — 셀이 방전 중이다. 충전하려면 python tools/plug_cli.py on "
              "(감시자는 data/supervisor_pause 표지가 없으면 10분 안에 켠다)")


def engine_info(cfg: Config, args, next_no: int) -> dict:
    """engine.json 의 시작 기록. target_last_cycle = 시작할 때의 다음 사이클 번호 + 돌 사이클 수 − 1 —
    감시자는 이것과 cycles.csv 의 줄 수로 남은 사이클을 센다. 예비 충전은 사이클로 세지 않는다.
    args 에는 해석한 인자를 전부 남긴다 — 감시자가 다시 띄울 때 사이클 수 외의 인자(--config 등)를 그대로 되살린다.
    --config 는 절대 경로로 바꿔 둔다(어느 폴더에서 띄웠든 같은 파일을 가리키게)."""
    return {"bench_id": cfg.bench_id, "pid": os.getpid(), "started": time.time(),
            "args": {**vars(args), "config": os.path.abspath(args.config) if args.config else None},
            "target_last_cycle": next_no + args.cycles - 1}


def last_gasp(rec: Recorder, plugs: list, gap_s: float = Config.stuck_gap_s) -> None:
    """처리되지 않은 예외로 끝나는 길(atexit) — 끝난 이유가 아직 비어 있으면 플러그를 한 번 켜 둔다(충전 쪽이 안전).

    다 돎·원격 정지·연속 실패는 run() 이 이미 켰고, Ctrl+C 는 사람이 일부러 멈춘 것이라 손대지 않는다(exit 가 채워져 있다).
    gap_s(설정 stuck_gap_s, 30초) 끊었다 켠다 — 10초로는 Dock 이 충전을 다시 시작하지 않은 일이 있었다(검토 F3).
    실패해도 조용히 지나간다 — 감시자가 이어서 켠다. 작업 관리자에서 강제로 끝내거나 창을 닫으면 여기까지 오지 않는다.
    """
    if rec.engine_exit_kind is not None or not plugs:
        return
    try:
        r = plugs[0].recharge(gap_s=gap_s)
        rec.log(f"비정상 종료 — 마지막으로 플러그 ON · {r.watts:.1f} W")
    except Exception:
        pass


def start_problem(cfg: Config, ip_state=net.ip_state) -> str | None:
    """시작을 거부할 이유 (없으면 None) — 설정 검사(validate)와 이 PC 의 시험망 주소 중복 (FMEA 2.4 · 3.2 · 8.4).

    시리얼이 겹치거나 플러그 MAC 이 틀린 설정으로 돌면 엉뚱한 셀·플러그를 움직인다. 다른 PC 가 같은 주소(192.168.1.100)를
    쓰고 있으면 셀 라이브가 그쪽으로 가고 두 PC 가 셀·플러그를 두고 다툰다. 주소가 아예 없으면 막지 않는다(셀 0대 → no_cells 길).
    ip_state 는 검사에서 바꿔 끼운다 (실물은 PowerShell Get-NetIPAddress 를 읽기만 한다).
    """
    problems = validate(cfg)
    if problems:
        return "설정 문제 — " + "; ".join(problems)
    return net.ip_start_problem(ip_state(cfg.pc_ip), cfg.pc_ip)


def config_error(data: Path, args, err: Exception | str, bench_id: str | None = None) -> None:
    """설정을 못 읽거나 시작 검사(start_problem)에 걸리면 data 폴더의 engine.json 에 exit=config_error 를 남긴다 —
    감시자가 되살리지 않고 사람을 부른다. 지금 살아 있는 다른 엔진의 기록은 덮어쓰지 않는다(그 엔진을 감시자가 계속 지켜봐야 하므로)."""
    path = data / ENGINE_FILE
    old = read_json(path) or {}
    if old.get("exit") is None and old.get("pid") and pid_matches(proc.created(int(old["pid"])), float(old.get("started") or 0), proc.boot_time()):
        return
    data.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, {"bench_id": bench_id or Config().bench_id, "pid": os.getpid(), "started": time.time(),
                             "args": {"cycles": args.cycles, "config": args.config}, "target_last_cycle": None,
                             "exit": "config_error", "error": err if isinstance(err, str) else f"{type(err).__name__}: {err}"})


if __name__ == "__main__":
    sys.exit(main())
