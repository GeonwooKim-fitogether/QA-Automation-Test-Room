"""감시자 — 시험 프로그램(엔진) 밖에서 엔진을 되살리는 장치.

지금까지의 대책(감독 루프 · Wi-Fi 재연결 · 안전 상태)은 전부 '프로그램 안에서 버티는' 장치라, 프로그램 자체가
사라지면 아무도 모른다. 2026-10-08 21:15 Windows 업데이트 자동 재시작으로 프로그램이 사라지고 플러그가 OFF 로
남아 셀 24대가 방전된 채 33시간 뒤에 발견됐다. 감시자는 작업 스케줄러가 로그온 때 띄우고(tools/install_supervisor.ps1),
supervisor_tick_s 마다 한 번 아래 순서로 점검한다.

  ① 시험망(LiveHub) — 이번 점검에서 플러그·엔진에 손대야 하는데 안 닿으면 저장된 Wi-Fi 프로필로 재연결
  ② 멈춘(행) 엔진이면 그 프로세스를 끝낸다
  ③ 플러그 ON — 엔진을 다시 띄우기보다 먼저 (모를 때는 충전 쪽이 안전. 부팅 직후 첫 점검도 이것이 첫 조치다)
  ④ 결과판 서버 — 응답이 없으면 창 없이 다시 띄운다
  ⑤ 엔진을 남은 사이클 수로 다시 띄운다 — 플러그 ON 이 된 뒤에만, 시간당 한도 안에서, 설정에 문제가 없을 때만
  ⑤-2 엔진이 돌지 않는 동안(다 돎 · 정지 · 연속 실패 · 설정 오류 · Ctrl+C · 기록 없음) 플러그를 지킨다(plug_guard) — 10분마다 읽어
     꺼짐이면 켜고, 켜짐인데 Dock 이 끌어 쓰지 않으면 끊었다 켠다(시간당 한도). 일시 중지 표지가 있으면 하지 않는다
  ⑥ 감시자가 직접 본 것(엔진 · 플러그 ON · 결과판)을 signals 로 남기고, 신호등(cellbench/health.py)이 그것과 엔진 지표 ·
     운영체제 정보(osinfo.json, 10분마다 모음)로 9개 차선을 판정한다. 차선마다 (불, 이유)를 신호등 알림기(alert.Alerter)에 넘긴다 —
     무엇을 언제 Slack 으로 보낼지(노랑 1건/1시간, 빨강 즉시 + 확인까지 15분마다, 복구 1건)는 알림기가 정한다
  ⑦ data/supervisor.json 에 결과와 불(alerts) · Slack 연결 여부(slack), data/health.json 에 신호등 판정 (run_once 가 쓰고 클라우드에도 올린다)
  ⑧ plug_scan_every_s(10분)마다 시험망의 플러그를 찾아 data/plugs.json 에 둔다 — 결과판의 '미등록 감지'·세트 등록 화면의 재료.
     등록된 플러그에는 접속하지 않고(plug.discover_plugs), 실패해도 점검은 계속한다

되살리지 않는 것 — 사람이 일부러 멈춘 엔진(done · stopped · interrupted · config_error — 설정 오류도 플러그는 켠다), 연속 실패로 스스로 멈춘
엔진(failsafe — 같은 결함을 되풀이하므로 사람이 본다), data/supervisor_pause 표지가 있는 동안(코드 교체·이관 — until 이 없으면
supervisor_pause_max_h 뒤 무시한다. 일시 중지 중에도 빨강·미확인은 알린다).
무엇을 볼지(설정)는 엔진과 같은 순서로 읽는다 — 기본값 ← bench.json ← 엔진의 --config (with_engine_config).
옛 임시 감시자(tools/watchdog.ps1)가 살아 있는 동안에도 점검만 한다 — 둘이 동시에 엔진을 띄우지 않게
(tools/install_supervisor.ps1 이 옛 감시자를 끈다).

engine.json 이 없는 엔진(감시자 이전 코드 — 옛 감시자가 띄운 것 등)도 지켜본다: now.json 이 멈췄고 엔진 프로세스도
없으면 플러그 ON 뒤 옛 감시자와 같은 규칙(Config.engine_cycles_default · engine_config_file)으로 다시 띄운다.
기록에 없는 run_cycle.py 프로세스가 남아 있으면 끝내지 않는다 — 다른 data 폴더로 도는 엔진일 수 있어서, 플러그 ON 만 하고
사람을 부른다. 그 엔진들은 추출 중에 심박을 찍지 않으므로 추출 중에는 충전 한도까지 멈춤으로 보지 않는다.

판정(engine_verdict · restart_plan · pause_state 등)은 순수 함수라 장비·프로세스 없이 검사한다. 바깥 세상에 닿는
일은 전부 Deps 를 거치고, 실물 Deps 는 supervise.py 가 만든다 — 플러그는 plug.py(Plug), 재연결은 net.py 만 쓴다.
감시자는 셀 라이브 포트(UDP 60222)를 열지 않는다.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .alert import URGENT, Alerter
from .config import Config, validate
from .control import read_json, write_json_atomic
from .record import CYCLES_FILE, ENGINE_FILE, NOW_FILE, cycles_done, read_rows

STATE_FILE = "supervisor.json"      # 점검 결과 — 신호등이 읽는다
PAUSE_FILE = "supervisor_pause"     # 있으면 점검만 하고 조치하지 않는다
LOG_FILE = "supervisor.log"
LOCK_FILE = "supervisor.lock"
HEALTH_FILE = "health.json"         # 신호등 판정 (cellbench/health.py) — 감시자가 1분마다 쓴다
OSINFO_FILE = "osinfo.json"         # 운영체제 정보 (cellbench/osinfo.py) — 감시자가 osinfo_every_s 마다 모은다
PLUGS_FILE = "plugs.json"           # 시험망의 플러그 탐색 (plug.discover_plugs) — 감시자가 plug_scan_every_s 마다, 결과판 '다시 찾기'가 즉석에서
# 신호등 이전의 알림 키 — 이제 차선 키로 접어 넣는다: engine·board → program, plug_on → plug, net → wireless.
# 옛 alert_state.json 에 남은 이 키들은 첫 점검에서 조용히 지운다(Alerter.forget) — 옛 빨강이 영영 켜져 보이지 않게.
RETIRED_KEYS = ("engine", "plug_on", "board", "net")

# 엔진이 남긴 끝난 이유(exit) → 판정. 여기 없는 값(no_cells 등)과 null(예외 · 강제 종료 · 전원 차단)은 '비정상'이다.
EXIT_VERDICT = {"done": "finished", "stopped": "stopped", "interrupted": "stopped",
                "failsafe": "paused", "config_error": "config_error"}
PLUG_SAFE_EXITS = {"done", "stopped", "failsafe"}   # 엔진이 플러그를 켜고 끝냈어야 하는 끝 (Ctrl+C 는 사람 뜻대로 그대로 둔다)

# 감시자 내부의 동작 간격 — 운영자가 바꿀 값이 아니라 Config 에 두지 않았다
SPAWN_GRACE_S = 600.0          # 다시 띄운 엔진이 아직 살아 있으면 engine.json 을 쓸 때까지 기다려 주는 시간
RECONNECT_GAP_S = 120.0        # Wi-Fi 재연결 최소 간격 (엔진의 _watch_link 와 같은 2분)
PLUG_FAIL_ALERT_AFTER = 3      # 플러그 ON 이 이만큼 연속 실패하면 사람을 부른다 (부팅 직후 Wi-Fi 가 늦게 붙는 것은 넘긴다)
PID_TOLERANCE_S = 600.0        # 프로세스가 태어난 시각과 engine.json 의 started 가 이만큼 안이면 같은 엔진
HUNG_CONFIRM_TICKS = 2         # '멈춤'이 이만큼 연속 점검에서 보여야 끝낸다 — 시계가 한 번 튀거나 now.json 을 한 번 못 읽은 것으로 산 엔진을 죽이지 않게
PLUG_GUARD_EVERY_S = 600.0     # 엔진이 돌지 않는 동안 플러그를 읽어 보는 간격 (검토 F3 — 끝난 뒤 '안전 상태'를 확인한다)
IDLE_VERDICTS = {"finished", "stopped", "paused", "config_error"}   # 엔진이 일부러·스스로 끝나 아무도 플러그를 보지 않는 판정


# ---------- 순수 판정 ----------

def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def beat_of(now: dict | None) -> float:
    """now.json 의 마지막 진척 시각. 감시자 이전의 엔진은 beat 가 없어 t(마지막 갱신)를 쓴다."""
    return _f((now or {}).get("beat") or (now or {}).get("t"))


def has_beat(now: dict | None) -> bool:
    """now.json 에 심박(beat)이 있나 — 감시자 이전 코드의 엔진은 없다(추출 중에 진척을 알리지 않는다)."""
    return bool((now or {}).get("beat"))


def stale_limit(phase: str | None, cfg: Config, beats: bool = True) -> float:
    """심박이 이만큼 멈춰야 '멈춤'. 추출 중에는 표본이 멈추고 셀 데이터를 받을 때만 심박이 뛰므로 길게 본다.

    심박이 없는 옛 엔진은 추출 내내 now.json 이 멈춰 있다. 셀 파일을 지우지 않는 동안 추출은 사이클마다 길어지므로
    (파일 전체를 매번 받는다) 그 엔진의 추출은 충전 한도(charge_timeout_h)까지 멈춤으로 보지 않는다.
    """
    if phase != "EXTRACT":
        return cfg.heartbeat_stale_s
    return cfg.heartbeat_stale_extract_s if beats else max(cfg.heartbeat_stale_extract_s, cfg.charge_timeout_h * 3600)


def warn_limit(phase: str | None, cfg: Config) -> float:
    """이만큼 멈추면 '주의'(노란불). 추출 중에는 셀 복귀를 기다리는 동안(최대 90초) 심박이 비는 것이 정상이라
    멈춤 문턱(heartbeat_stale_s)을 주의 문턱으로 쓴다."""
    return cfg.heartbeat_stale_s if phase == "EXTRACT" else cfg.heartbeat_warn_s


def now_fresh(now: dict | None, now_t: float, cfg: Config, age: float | None = None) -> bool:
    """누군가 지금 now.json 을 쓰고 있나 — 엔진 기록과 무관하게 '도는 엔진이 있다'는 증거.
    다시 띄우기 전에 이것을 보면, engine.json 을 남기지 않는 옛 엔진이나 사람이 띄운 엔진과 겹쳐 띄우지 않는다.
    age 를 주면(감시자가 단조 시계로 잰 심박 나이 — tick 의 beat_age_mono) 벽시계 대신 그것을 쓴다."""
    a = now_t - beat_of(now) if age is None else age
    return bool(now) and a <= stale_limit((now or {}).get("phase"), cfg, has_beat(now))


def heartbeat(engine: dict | None, now: dict | None, now_t: float, cfg: Config,
              age: float | None = None) -> tuple[float, str | None, str]:
    """돌고 있는 엔진의 심박 → (멈춘 초, 단계, ok · warn · stale).

    now.json 이 이번 엔진이 쓴 것이 아니면(시작 직후 아직 안 썼다) 시작 시각부터 센다 — 이전 실행의 단계도 믿지 않는다.
    age 를 주면 이번 엔진의 심박 나이로 그것을 쓴다(감시자의 단조 시계 — 벽시계가 튀어도 건강한 엔진을 끝내지 않게, 검토 F9).
    """
    started = _f((engine or {}).get("started"))
    beat = beat_of(now)
    mine = beat >= started
    age = age if (mine and age is not None) else now_t - (beat if mine else started)
    phase = (now or {}).get("phase") if mine else None
    if age > stale_limit(phase, cfg, has_beat(now) or not mine):
        return age, phase, "stale"
    return age, phase, "warn" if age > warn_limit(phase, cfg) else "ok"


def pid_matches(created: float | None, started: float, boot_t: float) -> bool:
    """engine.json 의 pid 가 지금도 '그 엔진'인가.

    created: 그 pid 의 프로세스가 태어난 시각 (None = 없음, 0.0 = 있는데 권한 때문에 모름).
    지난 부팅 때 시작한 엔진은 살아 있을 수 없고(재부팅 뒤 같은 pid 를 다른 프로그램이 받았을 수 있다),
    태어난 시각이 시작 기록보다 늦으면 pid 가 재사용된 것이다.
    """
    if created is None or (boot_t and started < boot_t):
        return False
    if created == 0.0:
        return True
    return started - PID_TOLERANCE_S <= created <= started + 5


def engine_verdict(engine: dict | None, now: dict | None, pid_alive: bool, now_t: float, cfg: Config,
                   age: float | None = None) -> str:
    """엔진 판정: ok · hung · dead · finished · stopped · paused · config_error · absent.

      absent        engine.json 이 없다 (감시자 이전 코드로 도는 중이거나, 한 번도 돈 적이 없다)
      finished      목표 사이클을 다 돌았다 (exit=done)
      stopped       사람이 멈췄다 (exit=stopped 원격 안전 정지 · interrupted Ctrl+C)
      paused        연속 실패로 엔진이 스스로 멈췄다 (exit=failsafe) — 사람이 본다
      config_error  설정 오류로 시작하지 못했다
      dead          끝난 이유 없이 사라졌다 (예외 · 강제 종료 · 재부팅 · 정전), 또는 no_cells 같은 비정상 끝
      hung          살아 있는데 심박이 멈췄다 — 추출 중이면 heartbeat_stale_extract_s, 아니면 heartbeat_stale_s
      ok            살아 있고 심박이 뛴다 (heartbeat_warn_s 를 넘었으면 '주의'지만 판정은 ok — heartbeat() 로 따로 본다)
    """
    if not engine:
        return "absent"
    ex = engine.get("exit")
    if ex is not None:
        return EXIT_VERDICT.get(ex, "dead")
    if not pid_alive:
        return "dead"
    return "hung" if heartbeat(engine, now, now_t, cfg, age)[2] == "stale" else "ok"


def remaining_cycles(engine: dict | None, done: int, default: int) -> int:
    """다시 시작할 사이클 수.

    engine.json 에 목표가 있으면 = 목표 마지막 사이클 번호 − 기록된 사이클 수 (0 이하면 다시 시작하지 않는다).
    목표를 모르면(engine.json 이 없는 옛 엔진) 옛 감시자와 같은 규칙 = max(1, default − 기록된 사이클 수).
    """
    target = (engine or {}).get("target_last_cycle")
    try:
        return int(target) - done
    except (TypeError, ValueError):
        return max(1, default - done)


def restarts_in_hour(times: list, now_t: float) -> list[float]:
    return [t for t in (times or []) if isinstance(t, (int, float)) and now_t - t < 3600]


def pause_state(text: str | None, now_t: float, mtime: float | None = None,
                max_h: float | None = None) -> tuple[bool, str]:
    """data/supervisor_pause 표지를 읽는다 → (일시 중지인가, 사유).

    내용은 JSON {"reason": "...", "until": <epoch 초>, "by": "..."}. until 이 지나면 표지를 무시한다 — 교체
    스크립트가 중간에 죽어 표지가 남아도 감시자가 영원히 멈추지 않게.
    until 이 없거나 읽을 수 없으면(내용이 비었거나 깨진 표지 포함) 표지 파일을 쓴 시각(mtime)부터 max_h 시간(설정
    supervisor_pause_max_h, 2시간)이 지나면 무시한다 — 잊고 남긴 표지 하나로 감시자가 영영 조치하지 않는 일을 막는다(검토 F5).
    그 안에서는 내용을 못 읽어도 일시 중지로 본다 — 누군가 일부러 둔 표지일 테니. mtime · max_h 를 주지 않으면 수명을 보지 않는다.
    """
    if text is None:
        return False, ""

    def by_age(reason: str) -> tuple[bool, str]:
        if mtime is not None and max_h is not None and now_t - mtime > max_h * 3600:
            return False, f"오래된 표지 무시 — {reason} ({max_h:g}시간 넘음 · until 없음)"
        return True, reason

    try:
        d = json.loads(text) if text.strip() else {}
    except ValueError:
        return by_age("표지 내용을 읽지 못함")
    if not isinstance(d, dict):
        return by_age("표지 내용을 읽지 못함")
    reason = str(d.get("reason") or "사유 없음")
    if d.get("until") is not None:
        try:
            until = float(d["until"])
        except (TypeError, ValueError):
            return by_age(f"{reason} (만료 시각을 읽지 못함)")
        if now_t > until:
            return False, f"만료된 표지 무시 — {reason}"
        return True, reason
    return by_age(reason)


@dataclass(frozen=True)
class Plan:
    """한 번의 점검에서 할 일. why 는 로그·알림·supervisor.json 에 그대로 쓰는 한 줄이다."""
    kill: bool = False           # 멈춘 엔진 프로세스를 끝낸다
    plug_on: bool = False        # 플러그를 충전 쪽으로 (재시작보다 먼저)
    restart: int = 0             # 다시 시작할 사이클 수 (0 = 다시 시작하지 않음)
    call_human: bool = False     # 조치로 끝나지 않아 사람이 봐야 한다
    why: str = ""


def restart_plan(verdict: str, engine: dict | None, now: dict | None, done: int, restart_times: list,
                 now_t: float, cfg: Config, problems: list[str] | tuple = (), age: float | None = None) -> Plan:
    """판정에서 할 일을 정한다. 순수 함수 — 같은 사건에서 플러그를 두 번 켜지 않는 것은 tick() 이 맡는다.
    age 는 감시자가 단조 시계로 잰 심박 나이(now_fresh 에 넘긴다)."""
    eng = engine or {}
    left_off = eng.get("exit") in PLUG_SAFE_EXITS and eng.get("plug_on") is False
    fix = " — 엔진이 플러그를 켜지 못하고 끝나 감시자가 켠다" if left_off else ""
    if verdict == "ok":
        return Plan(why="엔진 정상")
    if verdict == "finished":
        return Plan(plug_on=left_off, why="목표 사이클을 다 돌고 끝남" + fix)
    if verdict == "stopped":
        who = "원격 안전 정지" if eng.get("exit") == "stopped" else "사람이 Ctrl+C 로 멈춤"
        return Plan(plug_on=left_off, why=f"{who} — 되살리지 않는다" + fix)
    if verdict == "config_error":
        # 플러그는 켠다(같은 사건에 한 번 — tick 이 센다). 설정 오류로 거부된 엔진은 플러그를 만지지 않았으므로, 경계에서 바꾼 새 엔진이
        # 거부되면 플러그는 방전 시작(꺼짐) 그대로다(검토 F2). 켜는 것은 안전 쪽이라 감시자 설정의 검증 결과와 무관하게 한다.
        return Plan(plug_on=True, call_human=True,
                    why=f"엔진이 설정 오류로 시작하지 못함 — 되살리지 않고 플러그 ON 으로 둔다 ({eng.get('error', '')})")
    if verdict == "paused":
        return Plan(plug_on=left_off, call_human=True,
                    why="엔진이 연속 실패로 스스로 멈춤 — 같은 결함을 되풀이할 수 있어 되살리지 않는다. 사람 확인 필요" + fix)
    fresh = now_fresh(now, now_t, cfg, age)
    if verdict == "absent":
        if not now:
            return Plan(why="엔진 기록 없음 — 이 시험대는 아직 돈 적이 없다")
        if fresh:
            return Plan(why="engine.json 없이 도는 엔진이 있다(감시자 이전 코드) — 지켜보기만 한다")

    # dead · hung · (기록 없이 멈춘) absent
    hung = verdict == "hung"
    if hung:
        what = "엔진이 멈춤(심박 없음)" if engine else "기록(engine.json) 없이 돌던 엔진이 멈춤(결과판 기록 없음)"
    elif not engine:
        what = "기록(engine.json) 없이 돌던 엔진이 사라짐"
    elif eng.get("exit") == "no_cells":
        what = "엔진이 셀을 하나도 못 들어 끝남(셀이 꺼졌으면 Dock 버튼을 2초 이상 눌러야 한다)"
    else:
        what = "엔진이 비정상 종료됨"
    if (eng.get("args") or {}).get("dry_run"):
        return Plan(why=f"{what} — 모의 실행이라 되살리지 않는다")
    if not hung and fresh:
        return Plan(why=f"{what} — 그런데 결과판 기록이 살아 있어 다른 엔진이 도는 것으로 보고 기다린다")
    if problems:
        return Plan(kill=hung, plug_on=True, call_human=True,
                    why=f"{what} — 설정에 문제가 있어 되살리지 않는다: " + "; ".join(list(problems)[:3]))
    left = remaining_cycles(eng, done, cfg.engine_cycles_default)
    if left <= 0:
        return Plan(kill=hung, plug_on=True, why=f"{what} — 목표 사이클을 이미 다 돌아 다시 시작하지 않는다")
    recent = restarts_in_hour(restart_times, now_t)
    if len(recent) >= cfg.restart_max_per_h:
        return Plan(kill=hung, plug_on=True, call_human=True,
                    why=f"{what} — 1시간에 {len(recent)}번 되살렸는데 또 멈췄다. 더 하지 않고 플러그 ON 으로 둔다. 사람 확인 필요")
    return Plan(kill=hung, plug_on=True, restart=left, why=f"{what} — 플러그 ON 뒤 남은 {left}사이클로 다시 시작")


CARRY_SKIP = {"cycles", "precharge", "dry_run"}   # 다시 띄울 때 감시자가 정하는 인자 — 나머지는 처음 엔진의 것을 그대로


def engine_argv(python: str, root: Path, cycles: int, args: dict | None, fallback_config: str | None = None) -> list[str]:
    """다시 띄울 엔진 명령. 예비 충전부터 — 엔진이 언제 멈췄든 셀을 만충에서 시작하게 한다.

    args 는 engine.json 의 args (run_cycle.py 가 해석한 인자 전부, --config 는 절대 경로). 사이클 수·예비 충전·모의 외의
    인자는 이름 그대로 되살린다 — 나중에 run_cycle.py 에 인자가 늘어도 여기를 고치지 않아도 된다.
    args 가 None 이면(engine.json 이 없는 옛 엔진) 옛 감시자처럼 fallback_config(Config.engine_config_file, 있을 때만)를 붙인다.
    """
    argv = [python, str(root / "run_cycle.py"), "--precharge", "--cycles", str(int(cycles))]
    if args is None:
        return argv + (["--config", str(fallback_config)] if fallback_config else [])
    for k in sorted(args):
        v = args[k]
        if k in CARRY_SKIP or v is None or v is False:
            continue
        flag = "--" + k.replace("_", "-")
        argv += [flag] if v is True else [flag, str(v)]
    return argv


def incident_key(engine: dict | None, now: dict | None) -> str:
    """같은 사건인가를 가르는 열쇠. 엔진 기록이 바뀌면(다시 띄우면) 새 사건이다."""
    if engine:
        return f"engine:{engine.get('pid')}@{engine.get('started')}"
    return f"no-engine:{(now or {}).get('t')}"


# ---------- 신호등 재료 (감시자가 직접 본 불 — signals) ----------
# 이유 문구는 같은 원인이면 점검마다 똑같아야 한다 — 알림기가 이유가 바뀐 것을 '새 원인'으로 보고 다시 보내기 때문이다.
# NET_DOWN · NET_FIXED 는 신호등(health.py)의 '무선' 차선이 checks.wifi 를 보고 그대로 쓴다.
HEART_WATCH = "엔진 심박이 멈춤 — 지켜보는 중"
PLUG_STUCK = f"플러그를 {PLUG_FAIL_ALERT_AFTER}번 연속 켜지 못함 — 시험망·플러그 확인 필요"
PLUG_FIXED = "엔진이 플러그를 켜지 못하고 끝나 감시자가 켰다"
BOARD_DOWN = "결과판 서버가 응답하지 않음 — 감시자가 다시 띄운다"
BOARD_FAIL = "결과판 서버를 띄우지 못함"
NET_FIXED = "시험망(LiveHub)이 끊겨 감시자가 다시 붙였다"
NET_DOWN = "시험망(LiveHub)이 이 PC 에서 닿지 않음"
GUARD_ON = "엔진이 돌지 않는 동안 플러그가 꺼져 있어 감시자가 켰다"
GUARD_KICK = "엔진이 돌지 않는 동안 Dock 이 전력을 끌어 쓰지 않아 감시자가 끊었다 켰다"
GUARD_CAPPED = "엔진이 돌지 않는 동안 Dock 이 전력을 끌어 쓰지 않음 — 감시자가 1시간 한도까지 끊었다 켰는데도 안 됨. Dock 전원·어댑터 확인"
GUARD_STUCK = f"엔진이 돌지 않는 동안 꺼진 플러그를 {PLUG_FAIL_ALERT_AFTER}번 연속 켜지 못함 — 셀이 방전 중. 시험망·플러그 확인"


def engine_light(verdict: str, plan: Plan, beat_level: str | None, quiet: bool, *, revived: str | None = None,
                 start_err: str | None = None, deferred: bool = False, waiting: bool = False,
                 last_revived: str | None = None) -> tuple[str, str]:
    """엔진 불 — (불, 이유). 순수 함수.

      조치(red)     사람이 와야 한다: 되살리지 않고 사람을 부르는 판정(연속 실패 · 설정 오류 · 시간당 한도 · 끝내지 못함 ·
                    기록에 없는 엔진), 또는 다시 띄우려다 실패
      준비(yellow)  아직 잃은 것은 없다: 심박이 1분 넘게 멈춤, 되살렸음(그 사건에 1건), 되살리는 중(새 엔진이 뜨는 중 ·
                    플러그 ON 을 기다림), 비정상 종료지만 사이클을 다 돎, 기록과 다른 엔진이 도는 중
      정상(green)   엔진 정상, 또는 일부러 멈춤 · 다 돎 · 아직 돈 적 없음 · 모의 실행 (quiet)
    """
    if start_err:
        return "red", f"엔진을 다시 띄우지 못함 — {start_err}"
    if plan.call_human:
        return "red", plan.why
    if revived:
        return "yellow", revived
    if waiting:
        return "yellow", last_revived or plan.why
    if deferred:
        return "yellow", "엔진이 멈춰 되살리려는 중 — 플러그 ON 을 기다린다"
    if verdict == "ok":
        return ("yellow", HEART_WATCH) if beat_level == "warn" else ("green", "엔진 정상")
    if quiet:
        return "green", plan.why
    return "yellow", plan.why


# ---------- 한 번의 점검 ----------

@dataclass
class Deps:
    """감시자가 바깥 세상에 닿는 통로. 검사에서는 가짜로 바꾼다 (실물은 supervise.py 의 make_deps)."""
    hub_reachable: Callable[[], bool]
    reconnect: Callable[[], bool]
    board_ok: Callable[[], bool]
    start_board: Callable[[], int]
    plug_on: Callable[[], str]                  # 켜고 결과 한 줄 (실패하면 예외)
    proc_created: Callable[[int], float | None]
    kill: Callable[[int], bool]
    start_engine: Callable[[int, dict | None], int]   # (사이클 수, 처음 엔진의 args · 기록이 없으면 None) → pid
    alerter: Alerter                            # 신호등 알림기 — 불을 받아 보낼 것만 Slack 으로 (감시자 프로세스 안에서 data 폴더마다 하나)
    scan: Callable[[], dict | None] = lambda: None    # {"watchdog": [pid], "engine": [pid]} — 옛 감시자·엔진 프로세스 (모르면 None)
    boot_t: float = 0.0
    osinfo: Callable[[], dict | None] = lambda: None          # 운영체제 정보 모으기 (osinfo.collect, 읽기만) — 없으면 '전원 · OS' 미확인
    disk_free_gb: Callable[[], float | None] = lambda: None   # data 드라이브 여유 (GB) — 엔진이 없어도 '기록 · 디스크' 를 판정하게
    publish_health: Callable[[dict], None] = lambda h: None   # 신호등 판정을 클라우드(bench_health)로 — run_once 가 부른다(모의면 안 부름)
    scan_plugs: Callable[[], list | None] = lambda: None      # 시험망의 플러그 찾기 (plug.discover_plugs — 등록된 플러그에는 접속하지 않는다). None = 찾지 않음
    # 엔진이 돌지 않는 동안의 플러그 지키기(plug_guard) — 운전 중인 플러그를 읽어 (켜짐, W) 를 돌려준다(실패하면 예외). None = 읽지 않음(모의 · 검사)
    plug_read: Callable[[], tuple[bool, float] | None] = lambda: None
    # 심박 단조 시계 (검토 F9) — now.json 의 beat 값이 바뀐 순간을 이 프로세스의 단조 시계로 기억한다(beat_seen 은 점검 사이에 이어 쓰는 dict).
    # 감시자 프로세스 안에서만 이어진다 — 재부팅하면 단조 시계가 처음부터 시작하므로 파일에 남기지 않는다
    mono: Callable[[], float] = time.monotonic
    beat_seen: dict | None = None


def tick(cfg: Config, data: Path, prev: dict | None, deps: Deps, now_t: float) -> tuple[dict, list[str]]:
    """한 번 점검하고 조치한다. (supervisor.json 에 쓸 내용, 로그에 남길 조치 줄들) 을 돌려준다.

    prev 는 지난 supervisor.json — 그 안의 memo 로 사건별로 이미 한 일(플러그 ON)과 재시작 시각을 이어 받는다.
    무엇을 언제 알렸는지는 알림기(deps.alerter)가 data/alert_state.json 에 따로 이어 받는다.
    감시자 프로세스가 다시 떠도 시간당 한도와 '한 번만'·중복 없는 알림이 지켜진다.
    """
    prev = prev or {}
    memo = dict(prev.get("memo") or {})
    engine = read_json(data / ENGINE_FILE)
    now = _read_json_retry(data / NOW_FILE)
    done = cycles_done(data / CYCLES_FILE)
    eng = engine or {}

    alive = (bool(engine) and eng.get("exit") is None
             and pid_matches(deps.proc_created(int(_f(eng.get("pid")))), _f(eng.get("started")), deps.boot_t))
    age = beat_age_mono(now, now_t, deps)        # 심박 나이 — beat 값이 그대로인 동안은 단조 시계로 잰다(검토 F9)
    verdict = engine_verdict(engine, now, alive, now_t, cfg, age)
    paused, pause_why = pause_state(_read_text(data / PAUSE_FILE), now_t, _mtime(data / PAUSE_FILE), cfg.supervisor_pause_max_h)
    problems = validate(cfg)
    restart_times = restarts_in_hour(memo.get("restart_times", []), now_t)
    sp = memo.get("spawned") or {}

    # 이 PC 의 다른 감시자·엔진 프로세스. engine.json 에 없는 엔진이 남아 있는데 now.json 이 멈췄으면 '멈춤'으로 보되 끝내지는 않는다(아래)
    scan = deps.scan() or {}
    watchdogs = list(scan.get("watchdog") or [])
    mine = ({int(_f(eng.get("pid")))} if alive else set()) | ({int(_f(sp.get("pid")))} if sp else set())
    strays = [p for p in (scan.get("engine") or []) if p not in mine]
    kill_pids = [int(_f(eng.get("pid")))] if verdict == "hung" else []
    starting = held = None
    fresh_now = now_fresh(now, now_t, cfg, age)
    if verdict in ("dead", "absent") and strays and now and not fresh_now:
        born = {p: deps.proc_created(p) for p in strays}
        young = [p for p, c in born.items() if c and now_t - c < cfg.heartbeat_stale_s]
        if young:
            starting = young                     # 막 뜬 엔진 — 첫 기록을 쓸 때까지 기다린다
        else:
            verdict, held = "hung", strays
    plan = restart_plan(verdict, engine, now, done, restart_times, now_t, cfg, problems, age)
    if starting:
        plan = Plan(why=f"기록 없이 막 뜬 엔진(pid {starting})이 시작하는 중 — 기다린다")
    if held:
        # engine.json 에 없는 엔진은 끝내지 않는다. 프로세스 훑기는 PC 전체를 보지만 now.json 은 이 시험대의 data 폴더 것이라,
        # 다른 data 폴더로 도는 엔진(다른 세트·사람이 띄운 시험)을 '멈춤'으로 오판할 수 있다. 건강한 엔진을 죽이는 것이 더 나쁘다.
        # 다시 띄우지도 않는다 — 그 엔진이 셀 포트를 쥐고 있으면 새 엔진은 어차피 시작하지 못한다.
        plan = Plan(plug_on=True, call_human=True,
                    why=f"기록(engine.json)에 없는 엔진 프로세스(pid {held})가 남아 있는데 결과판 기록이 멈춤 — "
                        f"다른 시험·세트의 엔진일 수 있어 끝내지 않는다. 플러그 ON 으로 두었다. 사람 확인 필요")

    key = incident_key(engine, now)
    inc = memo.get("incident") or {}
    if inc.get("key") != key:
        inc = {"key": key, "plug_on": False, "plug_fail": 0}
    inc = dict(inc)
    inc.pop("alerts", None)    # 이전 판의 '사건마다 한 번 알림' 목록 — 이제 신호등 알림기(alert_state.json)가 맡는다
    # '멈춤'은 연속으로 보여야 믿는다 — 처음 보인 점검에서는 기다린다 (엔진 불도 바꾸지 않는다 — 판단 보류)
    inc["hung_ticks"] = int(_f(inc.get("hung_ticks"))) + 1 if verdict == "hung" else 0
    hung_wait = verdict == "hung" and inc["hung_ticks"] < HUNG_CONFIRM_TICKS
    if hung_wait:
        plan = Plan(why=f"엔진 심박이 멈춤 — 다음 점검에서도 멈춰 있으면 조치한다 ({inc['hung_ticks']}/{HUNG_CONFIRM_TICKS})")

    # 방금 다시 띄운 엔진이 아직 살아 있으면 engine.json 을 쓸 때까지 기다린다 (겹쳐 띄우지 않게)
    waiting = bool(starting)
    if (verdict in ("dead", "absent") and sp and now_t - _f(sp.get("t")) < SPAWN_GRACE_S
            and pid_matches(deps.proc_created(int(_f(sp.get("pid")))), _f(sp.get("t")), deps.boot_t)):
        plan = Plan(why=f"다시 띄운 엔진(pid {sp.get('pid')})이 시작하는 중 — 기다린다")
        waiting = True

    lines: list[str] = []      # supervisor.log 에 남길 조치
    revived = start_err = None  # 이번 점검에서 엔진을 되살렸나(그 이유) · 띄우려다 실패했나
    deferred = False            # 되살려야 하는데 플러그가 아직 안 켜져 미뤘나
    board_fail = False
    beat_age, _, beat_level = heartbeat(engine, now, now_t, cfg, age) if alive else (None, None, None)
    checks = {"wifi": "ok", "board": "ok", "engine": verdict, "heartbeat": beat_level or "-",
              "old_watchdog": watchdogs}
    hub = deps.hub_reachable()
    checks["wifi"] = "ok" if hub else "down"
    # 엔진이 돌지 않아 아무도 플러그를 보지 않는가 — 일부러·스스로 끝났거나(IDLE_VERDICTS), 사라졌는데 되살리지 않는다(한도 · 설정 문제 ·
    # 사이클을 다 돎 · 모의 · 기록 없음). 누가 now.json 을 쓰고 있거나 기록에 없는 엔진 프로세스가 있으면 지키지 않는다 —
    # 그 엔진이 같은 플러그로 방전 중일 수 있고, 그때 꺼진 플러그를 켜면 그 방전을 망친다.
    idle = ((verdict in IDLE_VERDICTS or (verdict in ("dead", "absent") and plan.restart == 0 and not waiting))
            and not fresh_now and not strays)
    guard_due = idle and bool(memo.get("guard")) and now_t >= _f((memo.get("guard") or {}).get("next"))
    guard: dict = {"active": False, "why": "엔진이 돌거나 감시자가 되살리는 중" if not idle else ""}

    if paused or watchdogs:
        checks["board"] = "ok" if deps.board_ok() else "down"
        if paused:
            why = f"일시 중지 — {pause_why} (점검만 하고 조치하지 않는다)"
            guard["why"] = "일시 중지 표지 — 사람이 일부러 손대는 중이라 플러그를 지키지 않는다"
        else:
            why = (f"옛 임시 감시자(watchdog.ps1, pid {watchdogs})가 돌고 있어 점검만 한다 — 둘이 엔진을 겹쳐 띄우지 않게. "
                   f"tools/install_supervisor.ps1 이 옛 감시자를 끈다 (지금 판정: {plan.why})")
            guard["why"] = "옛 임시 감시자가 돌아 점검만 한다"
    else:
        why = plan.why
        # ① 시험망 — 이번에 플러그·엔진에 손대야 할 때만. 엔진이 살아 있으면 엔진이 스스로 재연결한다(겹쳐 부르지 않게)
        need_net = plan.kill or (plan.plug_on and not inc["plug_on"]) or plan.restart > 0 or guard_due
        if (not hub and need_net and cfg.wifi_reconnect
                and now_t - _f(memo.get("last_reconnect")) >= RECONNECT_GAP_S):
            memo["last_reconnect"] = now_t
            ok = deps.reconnect()
            checks["wifi"] = "reconnected" if ok else "reconnect_failed"
            lines.append(f"시험망이 안 닿아 {cfg.wifi_profile} 재연결 {'성공' if ok else '실패'}")
        # ② 멈춘 엔진 끝내기 — 못 끝내면 다시 띄우지 않는다 (두 엔진이 한 플러그·한 포트를 두고 다투면 안 된다)
        if plan.kill:
            for pid in kill_pids:
                if deps.kill(pid):
                    lines.append(f"멈춘 엔진(pid {pid})을 끝냄")
                else:
                    lines.append(f"멈춘 엔진(pid {pid})을 끝내지 못함 — 다시 띄우지 않는다")
                    plan = replace(plan, restart=0, call_human=True,
                                   why=f"{plan.why.split(' — ')[0]} — 멈춘 엔진(pid {pid})을 끝내지 못해 다시 띄우지 않는다. 사람 확인 필요")
                    why = plan.why
        # ③ 플러그 ON — 재시작보다 먼저, 같은 사건에서 한 번만 (켤 때마다 10초 끊었다 켜므로 되풀이하지 않는다)
        if plan.plug_on and not inc["plug_on"]:
            try:
                res = deps.plug_on()
                inc["plug_on"], inc["plug_fail"] = True, 0
                lines.append(f"플러그 ON ({res})")
            except Exception as e:
                inc["plug_fail"] = int(inc.get("plug_fail") or 0) + 1
                lines.append(f"플러그 ON 실패 {inc['plug_fail']}번째: {e}")
        # ④ 결과판 서버
        if not deps.board_ok():
            bp = memo.get("board") or {}
            if bp and pid_matches(deps.proc_created(int(_f(bp.get("pid")))), _f(bp.get("t")), deps.boot_t):
                checks["board"] = "down"
                lines.append(f"결과판 서버(pid {bp.get('pid')})가 떠 있는데 응답하지 않음")
            else:
                try:
                    pid = deps.start_board()
                    memo["board"] = {"pid": pid, "t": now_t}
                    checks["board"] = "started"
                    lines.append(f"결과판 서버가 응답하지 않아 다시 띄움 (pid {pid}, 포트 {cfg.board_port})")
                except Exception as e:
                    checks["board"], board_fail = "down", True
                    lines.append(f"결과판 서버를 띄우지 못함: {e}")
        # ⑤ 엔진 다시 띄우기 — 플러그 ON 이 된 뒤에만
        if plan.restart > 0:
            if not inc["plug_on"]:
                deferred = True
                lines.append("플러그를 켜지 못해 엔진 재시작을 다음 점검으로 미룬다")
            else:
                try:
                    pid = deps.start_engine(plan.restart, (eng.get("args") or {}) if engine else None)
                    restart_times = restart_times + [now_t]
                    memo["spawned"] = {"pid": pid, "t": now_t}
                    lines.append(f"엔진 다시 시작 (pid {pid} · --precharge --cycles {plan.restart})")
                    revived = memo["revived"] = f"엔진을 되살림 (1시간에 {len(restart_times)}번째) — {plan.why}"
                except Exception as e:
                    start_err = f"{type(e).__name__}: {e}"
                    lines.append(f"엔진을 띄우지 못함: {e}")
        # ⑥ 엔진이 돌지 않는 동안 플러그 지키기 (검토 F3) — 사건마다 한 번 켜는 것(③)이 아직 되지 않았으면 그것이 먼저다
        if not idle:
            memo.pop("guard", None)                 # 엔진이 다시 돈다 — 다음에 멈추면 처음부터 센다
        elif plan.plug_on and not inc["plug_on"]:
            guard["why"] = "사건의 플러그 ON 을 먼저 한다"
        else:
            guard = plug_guard(memo, deps, cfg, now_t, lines)

    if verdict not in ("dead", "absent", "hung"):
        memo.pop("revived", None)           # 되살린 엔진이 제대로 돈다 — 그 사건은 끝났다
    memo["incident"] = inc
    memo["restart_times"] = restart_times
    if lines:
        memo["last_action"] = {"t": now_t, "what": " · ".join(lines)}

    # ⑥ 신호등. 감시자가 직접 본 불(signals)을 남긴다 — 일시 중지·옛 감시자가 도는 동안에는 비운다(사람이 일부러 손대는 중이거나
    # 옛 감시자 몫이라 감시자 판정이 없다. 그때 신호등은 같은 문턱으로 심박을 직접 본다).
    signals: dict = {}
    if not (paused or watchdogs):
        quiet = (verdict in ("finished", "stopped") or (verdict == "absent" and not plan.plug_on)
                 or (verdict in ("dead", "hung") and bool((eng.get("args") or {}).get("dry_run"))))
        left_off = eng.get("exit") in PLUG_SAFE_EXITS and eng.get("plug_on") is False
        g_light = guard_light(memo.get("guard"), now_t)
        signals = {
            "plug_on": (["red", PLUG_STUCK] if int(inc.get("plug_fail") or 0) >= PLUG_FAIL_ALERT_AFTER else
                        g_light if g_light and g_light[0] == "red" else
                        ["yellow", PLUG_FIXED] if left_off and inc.get("plug_on") else
                        g_light or ["green", "이상 없음"]),
            "board": (["green", "이상 없음"] if checks["board"] == "ok" else ["yellow", BOARD_FAIL if board_fail else BOARD_DOWN]),
            # '멈춤'을 처음 본 점검은 판단 보류(hold) — 신호등은 '프로그램' 차선을 알림기에 넘기지 않는다(불을 바꾸지 않는다)
            "engine": (["hold", HEART_WATCH] if hung_wait else
                       list(engine_light(verdict, plan, beat_level, quiet, revived=revived, start_err=start_err,
                                         deferred=deferred, waiting=waiting, last_revived=memo.get("revived")))),
        }
    report = {
        "bench_id": cfg.bench_id, "bench_name": cfg.bench_name, "t": now_t, "tick": int(_f(prev.get("tick"))) + 1,
        "tick_s": cfg.supervisor_tick_s, "checks": checks, "why": why,
        "paused": pause_why if paused else None, "config_problems": problems,
        "last_action": memo.get("last_action"), "restarts_1h": len(restart_times),
        "engine": {"pid": eng.get("pid"), "started": eng.get("started"), "exit": eng.get("exit"),
                   "target_last_cycle": eng.get("target_last_cycle"), "cycles_done": done,
                   "phase": (now or {}).get("phase"), "beat": beat_of(now) or None,
                   "beat_age": None if beat_age is None else round(beat_age, 1)},
        "strays": strays,
        "plug_guard": guard,                        # 엔진이 돌지 않는 동안의 플러그 지키기 (plug_guard — README 감시자 절의 표)
        "signals": signals,                         # {engine · plug_on · board: [불, 이유]} — 신호등의 차선 재료 (health.py)
        "disk_free_gb": _call(deps.disk_free_gb),   # 엔진이 없어도 '기록 · 디스크' 차선을 판정하게
        "slack": deps.alerter.has_webhook(),        # False 면 신호등이 'Slack 미연결'을 보여 준다
        "memo": memo,
    }
    # 9개 차선 판정 → 알림기 (키 = 차선 키). 일시 중지·옛 감시자가 도는 동안에는 알림기의 불을 바꾸지 않는다
    from .health import alert_lights, compute, mark_acks, thresholds     # health 가 이 모듈의 판정 함수를 쓰므로 여기서 불러온다
    osinfo, os_new = _osinfo(data, deps, now_t, thresholds(cfg))
    plugs, plugs_new = _plugs(data, deps, now_t, cfg)
    health = compute(now, report, osinfo, read_rows(data / CYCLES_FILE), cfg, now_t, plugs)
    sent: list[str] = []
    if not (paused or watchdogs):
        deps.alerter.forget(RETIRED_KEYS)
        sent = deps.alerter.update(alert_lights(health), now_t)
    elif paused:
        # 일시 중지 중에도 조치는 하지 않되 빨강·미확인은 알린다(검토 F5) — 사람이 손대는 동안 엔진이 죽거나 디스크가 차도 아무도 모르면 안 된다.
        # 노랑과 복구는 일시 중지가 끝난 뒤 알린다(교체 중에 잠깐 비는 것까지 보내지 않게).
        sent = deps.alerter.update({k: v for k, v in alert_lights(health).items() if v[0] in URGENT}, now_t)
    tag = "알림" if deps.alerter.has_webhook() else "알림(Slack 미연결 — 보내지 못하고 기록만)"
    lines += [f"{tag}: {m}" for m in sent]
    report["alerts"] = deps.alerter.snapshot()      # {차선 키: {light, since, reason, acked}}
    report["health"] = mark_acks(health, deps.alerter.acks(), report["alerts"])   # run_once 가 떼어 health.json 으로 쓴다
    if os_new:
        report["osinfo"] = osinfo                   # run_once 가 떼어 osinfo.json 으로 쓴다
    if plugs_new:
        report["plugs"] = plugs                     # run_once 가 떼어 plugs.json 으로 쓴다
        if plugs.get("error"):
            lines.append(f"플러그 탐색 실패 — {plugs['error']} (다음 탐색은 {cfg.plug_scan_every_s / 60:.0f}분 뒤)")
    return report, lines


def beat_age_mono(now: dict | None, now_t: float, deps: Deps) -> float | None:
    """심박 나이(초) — now.json 의 beat 값이 처음 보인 점검에서는 벽시계로(now_t − beat), 같은 값이 이어지는 동안은
    그때의 나이 + 이 감시자 프로세스의 단조 시계로 잰 경과 시간 (검토 F9).

    벽시계가 앞으로 튀면(시간 동기화 · 사람이 시각을 고침) now_t − beat 가 한꺼번에 커져 건강한 엔진을 '멈춤'으로 끝낼 수 있다.
    엔진은 심박을 쓸 때마다 새 값을 쓰므로, 값이 그대로인 동안의 경과만 단조 시계로 재면 튄 시계에 흔들리지 않는다.
    deps.beat_seen 이 없으면(검사 · 모의 일부) 언제나 벽시계로 잰다. beat 가 없으면 None.
    """
    b = beat_of(now)
    if not b:
        return None
    seen, m = deps.beat_seen, deps.mono()
    if seen is not None and seen.get("beat") == b and m >= _f(seen.get("mono")):
        return _f(seen.get("age0")) + (m - _f(seen.get("mono")))
    if seen is not None:
        seen.clear()
        seen.update(beat=b, mono=m, age0=now_t - b)
    return now_t - b


def plug_guard(memo: dict, deps: Deps, cfg: Config, now_t: float, lines: list[str]) -> dict:
    """엔진이 돌지 않는 동안 플러그를 지킨다 (검토 F3). memo["guard"] 를 고치고 supervisor.json 의 plug_guard 를 돌려준다.

    끝난 엔진은 플러그를 켜 두고 끝나지만, 그 뒤로는 아무도 보지 않았다 — 누가 끄거나 Dock 이 충전을 멈추면 셀이 방전된다.
      · PLUG_GUARD_EVERY_S(10분)마다 읽는다. 처음 읽는 것은 엔진이 멈춘 지 10분 뒤다(사건의 플러그 ON 이 막 끝났을 수 있다).
      · 꺼짐이면 켠다(stuck_gap_s 끊었다 켜기 — deps.plug_on). 한도와 무관하다 — 켜는 쪽이 안전하다.
      · 켜짐인데 stuck_w(10 W) 미만이면 stuck_s(60초) 뒤 다시 읽어 그래도 미만이면 끊었다 켠다. 1시간에 stuck_max(3)번까지 —
        그 뒤로는 빨강으로 사람을 부른다(릴레이를 계속 움직이지 않게).
      · 켜기에 실패하면 stuck_s 뒤 다시 하고, PLUG_FAIL_ALERT_AFTER(3)번 연속이면 빨강.
    읽지 못하면(시험망 끊김 등) 10분 뒤 다시 본다 — 시험망 상태는 '무선' 차선이 알린다.
    """
    stuck_w, stuck_s, _, cap = cfg.stuck_rule()
    g = dict(memo.get("guard") or {})
    if not g:
        g = {"since": now_t, "next": now_t + PLUG_GUARD_EVERY_S, "fail": 0, "recharges": [], "low_since": None}
    g["recharges"] = [t for t in (g.get("recharges") or []) if isinstance(t, (int, float)) and now_t - t < 3600]
    memo["guard"] = g                                       # 아래에서 고치는 것이 그대로 memo 에 남는다

    def turn_on(kind: str, why: str) -> None:
        try:
            res = deps.plug_on()
        except Exception as e:
            g["fail"] = int(_f(g.get("fail"))) + 1
            g["action"], g["next"] = "plug_on_failed", now_t + stuck_s
            lines.append(f"플러그 지키기: {why} — 켜기 실패 {g['fail']}번째: {e}")
            return
        g.update(fail=0, low_since=None, action=kind, acted=kind, acted_t=now_t, next=now_t + stuck_s)
        if kind == "recharge":
            g["recharges"].append(now_t)
        lines.append(f"플러그 지키기: {why} — 감시자가 {'켰다' if kind == 'plug_on' else '끊었다 켰다'} ({res})")

    if now_t >= _f(g.get("next")):
        try:
            rd = deps.plug_read()
        except Exception as e:
            rd = e
        if rd is None:
            g.update(action="no_read", next=now_t + PLUG_GUARD_EVERY_S)
        elif isinstance(rd, Exception):
            g.update(action="read_failed", error=f"{type(rd).__name__}: {rd}"[:200], next=now_t + PLUG_GUARD_EVERY_S)
        else:
            on, w = rd
            w = float("nan") if w is None else float(w)
            g.update(checked_t=now_t, on=on, watts=None if w != w else round(w, 1))
            g.pop("error", None)
            if on is False:
                turn_on("plug_on", "플러그 꺼짐")
                return _guard_report(g)
            g["fail"] = 0                                   # 켜져 있다 — 앞서 켜지 못한 것은 풀렸다
            if w == w and w < stuck_w:
                if g.get("low_since") is None:
                    g.update(low_since=now_t, action="low_seen", next=now_t + stuck_s)
                elif now_t - _f(g["low_since"]) < stuck_s:
                    g["next"] = _f(g["low_since"]) + stuck_s
                elif len(g["recharges"]) >= cap:
                    g.update(action="capped", capped=True, next=now_t + PLUG_GUARD_EVERY_S)
                else:
                    turn_on("recharge", f"켜짐인데 {w:.1f} W — Dock 이 전력을 끌어 쓰지 않음")
            else:
                g.update(low_since=None, capped=False, action="ok", next=now_t + PLUG_GUARD_EVERY_S)
    return _guard_report(g)


def _guard_report(g: dict) -> dict:
    """supervisor.json 의 plug_guard — 화면 쪽이 읽는다(README 감시자 절의 표)."""
    return {"active": True, "why": "엔진이 돌지 않아 감시자가 플러그를 지킨다", "since": g.get("since"),
            "checked_t": g.get("checked_t"), "on": g.get("on"), "watts": g.get("watts"), "action": g.get("action"),
            "action_t": g.get("acted_t"), "recharges_1h": len(g.get("recharges") or []), "fail": int(_f(g.get("fail"))),
            "next_t": g.get("next"), "error": g.get("error")}


def guard_light(g: dict | None, now_t: float) -> list | None:
    """플러그 지키기의 불 (플러그 차선) — 켜지 못함 · 한도까지 해도 안 됨은 빨강, 지난 1시간에 켰거나 끊었다 켰으면 노랑, 그 밖은 None."""
    if not g:
        return None
    if int(_f(g.get("fail"))) >= PLUG_FAIL_ALERT_AFTER:
        return ["red", GUARD_STUCK]
    if g.get("capped"):
        return ["red", GUARD_CAPPED]
    if g.get("acted_t") and now_t - _f(g["acted_t"]) < 3600:
        return ["yellow", GUARD_ON if g.get("acted") == "plug_on" else GUARD_KICK]
    return None


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def default_engine_config(root: Path, cfg: Config) -> str | None:
    """engine.json 이 없는 엔진을 다시 띄울 때 붙일 설정 파일 (Config.engine_config_file, cell-bench 기준) — 있을 때만."""
    p = Path(cfg.engine_config_file)
    p = p if p.is_absolute() else root / p
    return str(p) if cfg.engine_config_file and p.exists() else None


def engine_config_path(root: Path, cfg: Config, engine: dict | None) -> str | None:
    """지금 엔진이 쓰는 --config 파일 — 감시자가 그 엔진을 되살릴 때 붙이는 것과 같다(engine_argv).
    engine.json 에 인자 기록이 있으면 그 config(없으면 None — --config 없이 돈다), 기록이 없으면 engine_config_file(있을 때만).
    상대 경로는 cell-bench 기준이다(엔진은 cell-bench 에서 뜬다)."""
    args = (engine or {}).get("args")
    if isinstance(args, dict):
        c = args.get("config")
        if not c:
            return None
        p = Path(str(c))
        return str(p if p.is_absolute() else root / p)
    return default_engine_config(root, cfg)


def with_engine_config(root: Path, cfg: Config, config_path, engine: dict | None,
                       load: Callable[..., Config]) -> tuple[Config, str | None]:
    """감시자 설정에 엔진의 --config 를 덮는다 (검토 F8) → (쓸 설정, 경고 한 줄 또는 None).

    엔진은 기본값 ← bench.json ← --config 로 돈다. 감시자가 자기 설정(기본값 ← bench.json)만 보면, --config 로 바꾼 플러그 MAC ·
    문턱 · 한도를 모르고 엉뚱한 플러그를 켜거나 다른 문턱으로 판정한다. 그래서 엔진과 같은 순서로 다시 읽는다.
    data 폴더만은 감시자 것을 지킨다 — engine.json 을 찾은 곳이 그 폴더라서. 다르면 경고한다(플러그 MAC 이 달라도 경고).
    엔진 설정 파일을 읽지 못하면(그 파일이 설정 오류의 원인일 수 있다) 감시자 설정을 그대로 쓰고 경고한다 — 플러그 ON 은 그래도 한다.
    """
    ec = engine_config_path(root, cfg, engine)
    if not ec:
        return cfg, None
    try:
        if config_path and Path(ec).resolve() == Path(config_path).resolve():
            return cfg, None
    except OSError:
        pass
    try:
        ecfg = load(ec)
    except Exception as e:
        return cfg, f"엔진 설정 파일({ec})을 읽지 못해 감시자 설정만 쓴다: {type(e).__name__}: {e}"
    notes = []
    if str(ecfg.plug_mac).upper() != str(cfg.plug_mac).upper():
        notes.append(f"플러그 MAC — 감시자 {cfg.plug_mac} · 엔진 {ecfg.plug_mac} (엔진 것을 쓴다)")
    if data_path(root, ecfg) != data_path(root, cfg):
        notes.append(f"data 폴더 — 감시자 {data_path(root, cfg)} · 엔진 {data_path(root, ecfg)} (감시자 것을 쓴다)")
        ecfg = replace(ecfg, data_dir=cfg.data_dir)
    return ecfg, ("감시자와 엔진의 설정이 다름: " + " / ".join(notes)) if notes else None


def _call(fn):
    try:
        return fn()
    except Exception:
        return None


def _osinfo(data: Path, deps: Deps, now_t: float, th: dict) -> tuple[dict | None, bool]:
    """운영체제 정보 — data/osinfo.json 이 osinfo_every_s 보다 새것이면 그대로, 아니면 새로 모은다. (정보, 새로 모았나)."""
    old = read_json(data / OSINFO_FILE)
    if old and 0 <= now_t - _f(old.get("t")) < float(th.get("osinfo_every_s") or 600):
        return old, False
    new = _call(deps.osinfo)
    if not isinstance(new, dict):
        return old, False
    new = {**new, "t": now_t}
    return new, True


def _plugs(data: Path, deps: Deps, now_t: float, cfg: Config) -> tuple[dict | None, bool]:
    """플러그 탐색 — data/plugs.json 이 plug_scan_every_s 보다 새것이면 그대로, 아니면 새로 찾는다. (결과, 새로 찾았나).

    결과판의 '미등록 감지'와 등록 화면의 플러그 표가 읽는다. 찾다 실패해도 감시자는 계속하고, 실패도 시각·이유와 함께 남겨
    다음 탐색은 주기 뒤에 한다 — 실패할 때마다 1분 점검마다 5초씩 방송하지 않게. 탐색기가 없으면(None) 파일을 건드리지 않는다.
    """
    old = read_json(data / PLUGS_FILE)
    if old and 0 <= now_t - _f(old.get("t")) < cfg.plug_scan_every_s:
        return old, False
    try:
        found = deps.scan_plugs()
    except Exception as e:
        return {"t": now_t, "plugs": [], "error": f"{type(e).__name__}: {e}"[:200], "by": "supervisor"}, True
    if found is None:
        return old, False
    return {"t": now_t, "plugs": list(found), "by": "supervisor"}, True


def data_path(root: Path, cfg: Config) -> Path:
    d = Path(cfg.data_dir)
    return d if d.is_absolute() else root / d


def run_once(root: Path, config_path, make_deps: Callable[[Config, Path], Deps], dry: bool,
             log: Callable[[Path, str], None], now_t: float | None = None,
             load: Callable[..., Config] = Config.load) -> dict:
    """설정을 (매번 새로) 읽고 한 번 점검한 뒤 supervisor.json · health.json(· 새로 모았으면 osinfo.json)을 쓰고 신호등을 클라우드에 올린다.
    설정이 바뀌면 다음 점검부터 따른다.

    설정 파일이 깨졌으면 아무 조치도 하지 않고(무엇을 볼지조차 모르므로) 기본 data 폴더에 그 사실만 남긴다.
    엔진 불은 '미확인'(판정할 수 없음) — 빨강과 같이 바로 알리고 확인까지 15분마다 다시 알린다.
    dry 이면 파일을 하나도 쓰지 않고 클라우드에도 올리지 않는다 — 모의 점검이 진짜 감시자의 '이미 한 일' 기록을 바꾸지 않게.
    """
    now_t = time.time() if now_t is None else now_t
    try:
        cfg = load(config_path)
    except Exception as e:
        cfg0 = Config()
        data = data_path(root, cfg0)
        prev = read_json(data / STATE_FILE) or {}
        memo = dict(prev.get("memo") or {})
        why = f"설정을 읽지 못해 아무것도 하지 않는다: {type(e).__name__}: {e}"
        if memo.get("config_error") != str(e):
            memo["config_error"] = str(e)
            log(data, why)
        alerter = make_deps(cfg0, data).alerter
        for m in alerter.update({"program": ("unknown", f"감시자가 {why}")}, now_t):
            log(data, f"알림: {m}")
        report = {"bench_id": cfg0.bench_id, "bench_name": cfg0.bench_name, "t": now_t,
                  "tick": int(_f(prev.get("tick"))) + 1, "tick_s": cfg0.supervisor_tick_s,
                  "checks": {"config": "error"}, "why": why, "pid": os.getpid(),
                  "alerts": alerter.snapshot(), "slack": alerter.has_webhook(), "memo": memo}
        if not dry:
            write_json_atomic(data / STATE_FILE, report)
        return report
    data = data_path(root, cfg)
    prev = read_json(data / STATE_FILE) or {}
    # 엔진이 쓰는 --config 를 엔진과 같은 순서로 덮는다 (검토 F8). 다르면 감시자가 시작할 때와 달라질 때 경고를 한 줄 남긴다
    cfg, cfg_warn = with_engine_config(root, cfg, config_path, read_json(data / ENGINE_FILE), load)
    deps = make_deps(cfg, data)
    report, lines = tick(cfg, data, prev, deps, now_t)
    health = report.pop("health", None)
    osinfo = report.pop("osinfo", None)
    plugs = report.pop("plugs", None)
    report["pid"] = os.getpid()
    report["memo"].pop("config_error", None)
    report["config_warning"] = cfg_warn
    if cfg_warn and (cfg_warn != (prev.get("memo") or {}).get("cfg_warn") or prev.get("pid") != os.getpid()):
        log(data, f"경고: {cfg_warn}")
    if cfg_warn:
        report["memo"]["cfg_warn"] = cfg_warn
    else:
        report["memo"].pop("cfg_warn", None)
    for line in lines:
        log(data, line)
    summary = f"점검 — 엔진 {report['checks']['engine']} · 시험망 {report['checks']['wifi']} · 결과판 {report['checks']['board']} · {report['why']}"
    if summary != (prev.get("memo") or {}).get("summary"):
        log(data, summary)
    report["memo"]["summary"] = summary
    if not dry:
        write_json_atomic(data / STATE_FILE, report)
        if osinfo is not None:
            write_json_atomic(data / OSINFO_FILE, osinfo)
        if plugs is not None:
            write_json_atomic(data / PLUGS_FILE, plugs)
        if health is not None:
            write_json_atomic(data / HEALTH_FILE, health)
            try:
                deps.publish_health(health)         # 클라우드 결과판의 신호등 (bench_health) — 전송 실패는 Cloud 가 다시 해 본다
            except Exception as e:
                log(data, f"신호등 클라우드 전송을 넘기지 못함: {type(e).__name__}: {e}")
    return report


def _read_json_retry(path: Path, tries: int = 5) -> dict | None:
    """now.json 은 엔진이 20초마다 바꿔치기하므로 그 순간 읽기가 실패할 수 있다. 몇 번 다시 읽는다."""
    for i in range(tries):
        d = read_json(path)
        if d is not None or not path.exists():
            return d
        time.sleep(0.1)
    return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except OSError:
        return ""            # 있는데 못 읽음 → pause_state 가 일시 중지로 본다
