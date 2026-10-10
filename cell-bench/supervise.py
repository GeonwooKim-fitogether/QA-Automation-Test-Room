"""감시자 진입점 — 작업 스케줄러가 로그온 때 창 없이(pythonw) 띄우고, 5분마다 살아 있는지 확인한다
(등록: tools/install_supervisor.ps1, 작업 이름 "CellBench Supervisor").

  python supervise.py              # supervisor_tick_s(60초)마다 점검 — 끝나지 않는다
  python supervise.py --once       # 한 번 점검하고 끝
  python supervise.py --dry-run    # 무엇을 할지 로그만 남긴다. 플러그·Wi-Fi·프로세스·Slack 에 손대지 않고,
                                   # 잠금·data/supervisor.json 도 건드리지 않는다 (진짜 감시자와 함께 돌려도 된다)
  python supervise.py --config x.json

무엇을 볼지(경로·포트·주기·한도)는 엔진과 같은 설정(코드 기본값 ← bench.json ← --config)에서 매 점검마다 새로 읽는다.
판단과 순서는 cellbench/supervisor.py 에 있고, 이 파일은 잠금을 잡고 바깥 세상(플러그·Wi-Fi·프로세스·Slack)과 잇기만 한다.
로그는 data/supervisor.log — 조치했거나 상태가 바뀐 때, 알림을 보낸 때만 한 줄씩.

알림은 신호등 알림기(cellbench/alert.Alerter)가 보낸다 — 무엇을 언제 보냈는지는 data/alert_state.json, 사람이 누른 '확인'은
data/alert_ack.json. 계속 도는 모드로 시작하면 시험 알림을 1건 보낸다(웹훅이 아직 없으면 웹훅이 들어오는 대로 — 5분마다 다시 본다).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from cellbench import net, proc  # noqa: E402
from cellbench import supervisor as sup  # noqa: E402
from cellbench.alert import ACK_FILE, STATE_FILE as ALERT_STATE_FILE, Alerter, SlackSender  # noqa: E402
from cellbench.config import Config  # noqa: E402

START_TEST = "감시자 시작 · 알림 시험 — 이 메시지가 보이면 Slack 연결이 된 것"


def make_log(dry: bool):
    def log(data: Path, msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {'(모의) ' if dry else ''}{msg}"
        print(line, flush=True)             # pythonw 로 돌면 화면이 없어 아무 일도 하지 않는다
        try:
            data.mkdir(parents=True, exist_ok=True)
            with open(data / sup.LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    return log


def board_ok(port: int) -> bool:
    """결과판 서버가 /api/now 에 답하나. 이 PC 안의 주소라 프록시를 거치지 않는다."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/now", timeout=3) as r:
            json.loads(r.read() or b"{}")
            return r.status == 200
    except Exception:
        return False


def fallback_config(cfg: Config) -> str | None:
    """engine.json 이 없는 엔진을 다시 띄울 때 붙일 설정 파일 (Config.engine_config_file, cell-bench 기준) — 있을 때만."""
    p = Path(cfg.engine_config_file)
    p = p if p.is_absolute() else ROOT / p
    return str(p) if cfg.engine_config_file and p.exists() else None


def make_deps_factory(dry: bool, log, sender=None):
    """설정마다 바깥 세상과 잇는 통로를 만든다. dry 이면 조치는 로그만 남기고 성공한 것으로 친다(점검은 실제로 한다).

    알림기는 data 폴더마다 하나를 이 프로세스가 끝날 때까지 쓴다(시험 알림 '한 번'과 보내지 못한 메시지를 이어 가게).
    sender 는 Slack 한 건 전송(SlackSender) — 없으면 처음 쓸 때 자격 증명 관리자에서 웹훅을 읽어 만든다.
    dry 이면 알림을 로그에만 남기고, alert_state.json 도 쓰지 않는다(진짜 감시자의 '이미 보낸 것' 기록을 바꾸지 않게).
    """
    py = proc.console_python()
    alerters: dict[str, Alerter] = {}
    slack = {"sender": sender}

    def alerter_for(cfg: Config, data: Path) -> Alerter:
        k = str(data)
        if k not in alerters:
            if dry:
                alerters[k] = Alerter(lambda text: log(data, f"알림: {text}"), None, data / ACK_FILE, bench=cfg.bench_id)
            else:
                slack["sender"] = slack["sender"] or SlackSender()
                alerters[k] = Alerter(slack["sender"], data / ALERT_STATE_FILE, data / ACK_FILE, bench=cfg.bench_id)
        return alerters[k]

    def make(cfg: Config, data: Path) -> sup.Deps:
        def say(msg, ret):
            log(data, msg)
            return ret

        def hub() -> bool:
            return any(net.hub_reachable(cfg.hub_ip) for _ in range(3))    # 한 번 놓친 것으로 재연결하지 않게

        # 점검(시험망 ping · 결과판 응답 · 프로세스 훑기)은 모의에서도 실제로 한다 — 읽기만 하므로
        if dry:
            return sup.Deps(
                hub_reachable=hub, board_ok=lambda: board_ok(cfg.board_port), proc_created=proc.created,
                reconnect=lambda: say(f"Wi-Fi {cfg.wifi_profile} 재연결", True),
                start_board=lambda: say(f"결과판 서버 시작 (포트 {cfg.board_port})", 0),
                plug_on=lambda: say("플러그 ON (껐다 켜기)", "모의"),
                kill=lambda pid: say(f"엔진 pid {pid} 끝내기", True),
                start_engine=lambda n, a: say("엔진 시작: " + " ".join(sup.engine_argv(py, ROOT, n, a, fallback_config(cfg))), 0),
                alerter=alerter_for(cfg, data), scan=proc.scan_others, boot_t=proc.boot_time())

        def plug_on() -> str:
            from cellbench.plug import Plug            # 장비 호출은 plug.py 만 거친다
            r = Plug(cfg).recharge()
            return f"{'켜짐' if r.on else '꺼짐'} · {r.watts:.1f} W"

        return sup.Deps(
            hub_reachable=hub, board_ok=lambda: board_ok(cfg.board_port), proc_created=proc.created,
            reconnect=lambda: net.reconnect(cfg.wifi_profile, cfg.hub_ip),
            start_board=lambda: proc.spawn([py, str(ROOT / "serve_board.py"), "--port", str(cfg.board_port),
                                            "--data", str(data)], ROOT, data / "board_stderr.txt"),
            plug_on=plug_on, kill=proc.kill,
            start_engine=lambda n, a: proc.spawn(sup.engine_argv(py, ROOT, n, a, fallback_config(cfg)), ROOT,
                                                 data / "engine_stderr.txt"),
            alerter=alerter_for(cfg, data), scan=proc.scan_others, boot_t=proc.boot_time())

    make.alerter_for = alerter_for          # main 이 시작 시험 알림에 쓴다
    return make


def main() -> int:
    ap = argparse.ArgumentParser(description="셀 시험대 감시자 — 엔진 밖에서 엔진을 되살린다")
    ap.add_argument("--once", action="store_true", help="한 번 점검하고 끝낸다")
    ap.add_argument("--dry-run", action="store_true", help="조치하지 않고 무엇을 할지 로그만 남긴다")
    ap.add_argument("--config", default=None)
    a = ap.parse_args()

    try:
        cfg0 = Config.load(a.config)
    except Exception:
        cfg0 = Config()
    data0 = sup.data_path(ROOT, cfg0)
    log = make_log(a.dry_run)
    lock = None
    if not a.dry_run:
        lock = proc.acquire_lock(data0 / sup.LOCK_FILE)
        if lock is None:
            return 0                     # 다른 감시자가 이미 돈다 — 조용히 끝낸다 (작업 스케줄러의 5분 반복이 여기로 온다)
    log(data0, f"감시자 시작 · pid {os.getpid()} · {'한 번' if a.once else '계속'}{' · 모의' if a.dry_run else ''}")
    make = make_deps_factory(a.dry_run, log)
    if not a.once and not a.dry_run:
        alerter = make.alerter_for(cfg0, data0)
        if alerter.test(START_TEST):     # 못 보냈으면 들고 있다가 웹훅이 들어오는(전송이 되는) 첫 점검에서 보낸다
            log(data0, "Slack 연결됨 — 시험 알림을 보냈다")
        elif alerter.has_webhook():
            log(data0, "Slack 웹훅은 있는데 시험 알림을 보내지 못함 — 다음 점검에서 다시 보낸다 (인터넷·웹훅 주소 확인)")
        else:
            log(data0, "Slack 미연결 — 알림은 기록만 한다. tools/remote_setup.py 로 웹훅을 넣으면 5분 안에 이어 받아 시험 알림부터 보낸다")
    while True:
        tick_s = 60.0
        try:
            report = sup.run_once(ROOT, a.config, make, a.dry_run, log)
            tick_s = float(report.get("tick_s") or tick_s)
        except Exception:
            log(data0, "점검 중 예외 — 다음 점검에서 다시 한다\n" + traceback.format_exc())
        if a.once:
            return 0
        time.sleep(max(5.0, tick_s))


if __name__ == "__main__":
    sys.exit(main())
