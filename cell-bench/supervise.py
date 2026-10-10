"""감시자 진입점 — 작업 스케줄러가 로그온 때 창 없이(pythonw) 띄우고, 5분마다 살아 있는지 확인한다
(등록: tools/install_supervisor.ps1, 작업 이름 "CellBench Supervisor").

  python supervise.py              # supervisor_tick_s(60초)마다 점검 — 끝나지 않는다
  python supervise.py --once       # 한 번 점검하고 끝
  python supervise.py --dry-run    # 무엇을 할지 로그만 남긴다. 플러그·Wi-Fi·프로세스·Slack·클라우드에 손대지 않고,
                                   # 잠금·data 의 상태 파일(supervisor.json · health.json · osinfo.json)도 건드리지 않는다
                                   # (진짜 감시자와 함께 돌려도 된다)
  python supervise.py --config x.json

무엇을 볼지(경로·포트·주기·한도)는 엔진과 같은 설정(코드 기본값 ← bench.json ← --config)에서 매 점검마다 새로 읽는다.
판단과 순서는 cellbench/supervisor.py 에 있고, 이 파일은 잠금을 잡고 바깥 세상(플러그·Wi-Fi·프로세스·Slack)과 잇기만 한다.
로그는 data/supervisor.log — 조치했거나 상태가 바뀐 때, 알림을 보낸 때만 한 줄씩.

알림은 신호등 알림기(cellbench/alert.Alerter)가 보낸다 — 무엇을 언제 보냈는지는 data/alert_state.json, 사람이 누른 '확인'은
data/alert_ack.json. 계속 도는 모드로 시작하면 시험 알림을 1건 보낸다(웹훅이 아직 없으면 웹훅이 들어오는 대로 — 5분마다 다시 본다).
신호등 판정(cellbench/health.py)은 점검마다 data/health.json 에 쓰고 클라우드 bench_health 에도 올린다(키가 없으면 무음으로 건너뛴다).
운영체제 정보는 10분마다 읽기만 해서(cellbench/osinfo.py) data/osinfo.json 에 둔다.
시험망의 플러그도 10분마다 찾아(plug.discover_plugs — 등록된 플러그에는 접속하지 않는다) data/plugs.json 에 둔다 — 세트 등록 화면의 재료.
모의(--dry-run)는 플러그를 찾지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from cellbench import net, osinfo, proc  # noqa: E402
from cellbench import supervisor as sup  # noqa: E402
from cellbench.alert import ACK_FILE, STATE_FILE as ALERT_STATE_FILE, Alerter, SlackSender  # noqa: E402
from cellbench.config import Config  # noqa: E402
from cellbench.health import thresholds  # noqa: E402

START_TEST = "감시자 시작 · 알림 시험 — 이 메시지가 보이면 Slack 연결이 된 것"


def make_log(dry: bool):
    def log(data: Path, msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {'(모의) ' if dry else ''}{msg}"
        try:
            data.mkdir(parents=True, exist_ok=True)
            with open(data / sup.LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
        proc.console(line)                  # pythonw 로 돌면 화면이 없어 아무 일도 하지 않는다. 화면 출력 실패로 죽지 않는다(검토 F1)
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
    return sup.default_engine_config(ROOT, cfg)


def once_per_hour(log, data: Path):
    """같은 머리말의 줄은 1시간에 한 번만 로그에 남긴다 — 클라우드 표가 아직 없을 때(마이그레이션 적용 전) 1분마다 실패가 쌓이지 않게."""
    seen: dict[str, float] = {}

    def f(msg: str) -> None:
        head = msg.split(" — ")[0].split(" (")[0]
        now = time.time()
        if now - seen.get(head, 0.0) >= 3600:
            seen[head] = now
            log(data, msg)
    return f


def make_deps_factory(dry: bool, log, sender=None, cloud=None):
    """설정마다 바깥 세상과 잇는 통로를 만든다. dry 이면 조치는 로그만 남기고 성공한 것으로 친다(점검은 실제로 한다).

    알림기는 data 폴더마다 하나를 이 프로세스가 끝날 때까지 쓴다(시험 알림 '한 번'과 보내지 못한 메시지를 이어 가게).
    sender 는 Slack 한 건 전송(SlackSender) — 없으면 처음 쓸 때 웹훅을 찾아 만든다(자격 증명 관리자, 없으면 클라우드 Vault).
    dry 이면 알림을 로그에만 남기고, alert_state.json 도 쓰지 않는다(진짜 감시자의 '이미 보낸 것' 기록을 바꾸지 않게).
    cloud 는 신호등을 올릴 클라우드(Cloud) — 없으면 처음 올릴 때 자격 증명 관리자(cell-bench-cloud)의 url · service_key 로 만든다.
    시험대 이름표는 엔진과 같은 cfg.bench_id. 모의(dry)는 올리지 않는다(run_once 가 부르지 않는다).
    """
    py = proc.console_python()
    alerters: dict[str, Alerter] = {}
    slack = {"sender": sender}
    clouds: dict[str, object] = {}
    beats: dict[str, dict] = {}         # data 폴더마다 '마지막으로 본 심박 값과 그때의 단조 시계' — 점검 사이에 이어 쓴다(검토 F9)

    def cloud_for(cfg: Config, data: Path):
        if cloud is not None:
            return cloud
        if cfg.bench_id not in clouds:
            from cellbench.cloud import Cloud
            clouds[cfg.bench_id] = Cloud(log=once_per_hour(log, data), sample_every_s=cfg.cloud_sample_s, bench_id=cfg.bench_id)
        return clouds[cfg.bench_id]

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

        # 신호등 재료 — 운영체제 정보와 data 드라이브 여유는 읽기만 하므로 모의에서도 실제로 읽는다
        reads = dict(osinfo=lambda: osinfo.collect(warn_days=int(thresholds(cfg)["pause_warn_days"])),
                     disk_free_gb=lambda: round(shutil.disk_usage(data).free / 1024 ** 3, 1),
                     beat_seen=beats.setdefault(str(data), {}))

        # 점검(시험망 ping · 결과판 응답 · 프로세스 훑기)은 모의에서도 실제로 한다 — 읽기만 하므로
        if dry:
            return sup.Deps(
                hub_reachable=hub, board_ok=lambda: board_ok(cfg.board_port), proc_created=proc.created,
                reconnect=lambda: say(f"Wi-Fi {cfg.wifi_profile} 재연결", True),
                start_board=lambda: say(f"결과판 서버 시작 (포트 {cfg.board_port})", 0),
                plug_on=lambda: say("플러그 ON (껐다 켜기)", "모의"),
                kill=lambda pid: say(f"엔진 pid {pid} 끝내기", True),
                start_engine=lambda n, a: say("엔진 시작: " + " ".join(sup.engine_argv(py, ROOT, n, a, fallback_config(cfg))), 0),
                alerter=alerter_for(cfg, data), scan=proc.scan_others, boot_t=proc.boot_time(), **reads)

        def plug_on() -> str:
            from cellbench.plug import Plug            # 장비 호출은 plug.py 만 거친다
            # stuck_gap_s(30초) 끊었다 켠다 — 10초로는 Dock 이 충전을 다시 시작하지 않은 일이 있었다(검토 F3). 릴레이 누적 횟수(4.6)에 감시자 몫도 센다
            r = Plug(cfg, stats_path=data / "plug_stats.json").recharge(gap_s=cfg.stuck_rule()[2])
            if not r.on:
                raise RuntimeError(f"켜기 명령 뒤에도 꺼짐 · {r.watts:.1f} W")
            return f"켜짐 · {r.watts:.1f} W"

        def plug_read() -> tuple[bool, float]:
            from cellbench.plug import Plug            # 읽기만 — 엔진이 돌지 않을 때만 부른다(plug_guard)
            r = Plug(cfg).read()
            return r.on, r.watts

        def scan_plugs() -> list:
            from cellbench.plug import discover_plugs  # 읽기만 — 등록된 플러그(엔진이 쓰는 중)에는 접속하지 않는다
            return discover_plugs(cfg)

        return sup.Deps(
            hub_reachable=hub, board_ok=lambda: board_ok(cfg.board_port), proc_created=proc.created,
            reconnect=lambda: net.reconnect(cfg.wifi_profile, cfg.hub_ip),
            start_board=lambda: proc.spawn([py, str(ROOT / "serve_board.py"), "--port", str(cfg.board_port),
                                            "--data", str(data)], ROOT, data / "board_stderr.txt"),
            plug_on=plug_on, kill=proc.kill,
            start_engine=lambda n, a: proc.spawn(sup.engine_argv(py, ROOT, n, a, fallback_config(cfg)), ROOT,
                                                 data / "engine_stderr.txt"),
            alerter=alerter_for(cfg, data), scan=proc.scan_others, boot_t=proc.boot_time(),
            publish_health=lambda h: cloud_for(cfg, data).health(h), scan_plugs=scan_plugs, plug_read=plug_read, **reads)

    make.alerter_for = alerter_for          # main 이 시작 시험 알림에 쓴다
    make.cloud_for = cloud_for
    return make


def main() -> int:
    ap = argparse.ArgumentParser(description="셀 시험대 감시자 — 엔진 밖에서 엔진을 되살린다")
    ap.add_argument("--once", action="store_true", help="한 번 점검하고 끝낸다")
    ap.add_argument("--dry-run", action="store_true", help="조치하지 않고 무엇을 할지 로그만 남긴다")
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    proc.safe_stdio()                   # 화면이 파일·파이프여도 '—' 같은 글자로 죽지 않게 (검토 F1)

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
            src = {"keyring": "자격 증명 관리자", "cloud": "클라우드 Vault"}.get(alerter.webhook_source())
            log(data0, "Slack 연결됨 — 시험 알림을 보냈다" + (f" (웹훅: {src})" if src else ""))
        elif alerter.has_webhook():
            log(data0, "Slack 웹훅은 있는데 시험 알림을 보내지 못함 — 다음 점검에서 다시 보낸다 (인터넷·웹훅 주소 확인)")
        else:
            log(data0, "Slack 미연결 — 알림은 기록만 한다. 자격 증명 관리자에도 클라우드 Vault 에도 웹훅이 없다(또는 클라우드 키가 없다). "
                       "어느 쪽이든 생기면 5분 안에 이어 받아 시험 알림부터 보낸다")
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
