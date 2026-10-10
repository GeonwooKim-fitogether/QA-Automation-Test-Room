"""신호등 — 시험대의 지금 상태를 9개 차선의 불(정상 · 준비 · 조치 · 미확인)로 판정한다. 순수 함수(파일·장비에 닿지 않는다).

불 넷 (신호등 설계안, 2026-10-10 사용자 승인)
  정상(green)     지표가 모두 기대 범위 안
  준비(yellow)    추세·여유·횟수가 문턱을 넘었다. 아직 잃은 것은 없다 — AI 가 미리 조치하고, 사람은 준비한다
  조치(red)       이미 잃고 있다 (셀 방전 중 · 프로그램 없음 · 수신 0 ...)
  미확인(unknown) 지표를 읽을 수 없다. 빨강과 같은 무게로 다룬다
불마다 글자(alert.LIGHTS)와 이유 한 줄이 함께 간다 — 색만으로 뜻을 전하지 않는다.

입력은 전부 파일에서 읽은 그대로다 (없으면 None).
  now     data/now.json (엔진, 20초마다) — phase · beat · cells · metrics. metrics 의 뜻은 README '신호등 지표' 표가 계약이다
  sup     data/supervisor.json (감시자, 1분마다) — t · checks · signals(감시자가 직접 본 불) · slack · paused · disk_free_gb · alerts
  osinfo  data/osinfo.json (감시자, 10분마다 — cellbench/osinfo.py) — power · update · wifi
  cycles  cycles.csv 의 줄들 (dict 목록)
  cfg     Config — 새 문턱은 thresholds(cfg) (config.HEALTH 위에 cfg.health 를 덮은 것), 이미 있던 문턱은 Config 의 값 그대로
  now_t   지금 시각 (epoch 초)

누가 부르나
  감시자(supervisor.tick) 1분마다 → data/health.json · 클라우드 bench_health · 알림기 키 = 차선 키 (alert_lights)
  결과판 서버(serve_board GET /api/health) 요청마다 → 감시자가 없어도 화면은 판정된다

두 가지 약속
  1. 차선의 이유(reason)는 같은 원인이면 늘 같은 글이다 — 알림기가 이유가 바뀐 것을 새 사건으로 보고 다시 보내기 때문이다.
     바뀌는 숫자는 값(value)과 짧은 글(short)에만 쓴다.
  2. 엔진 기록이 멈추면(심박이 감시자의 멈춤 문턱을 넘음) 엔진만 아는 차선(ENGINE_ONLY)은 미확인이 되고 hold 가 붙는다.
     원인은 '프로그램' 차선 하나로 알리고(그 차선도 미확인으로 올라가 '확인' 단추가 거기에 선다), 알림기는 hold 차선을
     건드리지 않는다 — 같은 원인으로 알림이 여러 건 쏟아지지 않게.

불이 없는 차선 — '기록 없음'(none). now.json 이 아예 없으면(엔진이 아직 돈 적 없음) 엔진에서만 재는 차선(SET_LANES)은
정상도 미확인도 아니다. 잴 것이 없을 뿐이다. 그래서 회색 '기록 없음'으로 두고, 시험대 전체 불을 올리지도 내리지도 않으며,
알림기에도 넘기지 않는다(alert_lights). 초록 '정상'으로 두면 화면이 "기록 없음"과 "정상"을 동시에 말해 스스로 모순된다.

AI 칸에는 코드가 실제로 하는 조치만 적는다. 하지 않는 조치를 했다고 쓰면 거짓이다.
"""
from __future__ import annotations

import time

from .alert import KEY_LABELS, LIGHTS, URGENT
from .config import HEALTH, next_set_id, registered_macs, registered_serials
from .supervisor import NET_DOWN, NET_FIXED, beat_of, has_beat, stale_limit, warn_limit

LANE_KEYS = ("power", "program", "wireless", "plug", "dock", "live", "disk", "cloud", "human")
# 차선이 덮는 FMEA 항목 (코드 주석의 번호에서 모은 것 — 1.x PC·OS · 2.x 프로그램 · 3.x 무선 · 4.x 플러그 · 5.x Dock · 6.x 셀 · 7.x 클라우드 · 8.x 사람)
FMEA = {"power": "1.x", "program": "2.x", "wireless": "3.x", "plug": "4.x · 5.2 · 5.3", "dock": "5.x · 6.5",
        "live": "6.x", "disk": "1.5", "cloud": "7.x", "human": "8.x"}
SET_LANES = ("plug", "dock", "live", "human")    # 세트마다 따로 있는 것 — 세트의 불 = 이 차선들 중 가장 나쁜 불
ENGINE_ONLY = ("dock", "live", "human")          # 엔진만 아는 차선 — 엔진 기록이 멈추면 미확인 + hold
ENDED_PHASES = {"DONE", "STOPPED"}               # 엔진이 일부러 끝낸 단계 — 심박이 멈추는 것이 정상
ENDED_EXITS = {"done", "stopped", "interrupted"}
PHASE_KO = {"PRECHARGE": "예비 충전", "DISCHARGE": "방전", "THRESHOLD": "기준선 도달", "PLUG_ON": "충전 개시",
            "EXTRACT": "데이터 추출", "CHARGE": "충전", "FULL": "만충", "PLUG_OFF": "충전 종료", "RECOVER": "복구 대기",
            "STOPPED": "정지", "DONE": "완료"}
KST_OFFSET_S = 9 * 3600                          # 화면·알림의 시각은 한국 시각으로 적고 KST 를 붙인다 (FMEA 7.6)
NONE = "none"                                    # 불 없음 — 엔진 기록이 아예 없어 잴 것이 없는 차선 (모듈 설명 참고)
WORDS = {**LIGHTS, NONE: "기록 없음"}
NONE_REASON = "엔진 기록 없음 — 아직 돈 적 없음"
SHORT_MAX = 80                                   # 띠에 싣는 짧은 글 하나의 최대 글자 수 (긴 감시자 이유가 띠를 덮지 않게)
ACK_REASONS = "_reason"                          # alert_ack.json 안의 칸 — {차선 키: {reason, light, t}} 확인을 누른 그 원인 (mark_acks)
HELD_SHORT = "엔진 기록이 멈춰 셀 쪽 차선 미확인"
INTERRUPTED = "엔진이 사람 손으로 멈춤 — 플러그 꺼짐이면 셀이 방전 중"


# ---------- 작은 도우미 ----------

def thresholds(cfg) -> dict:
    """새 문턱 — config.HEALTH 위에 cfg.health 를 덮는다. bench.json 에 일부 키만 줘도 나머지는 기본값."""
    over = getattr(cfg, "health", None)
    return {**HEALTH, **(over if isinstance(over, dict) else {})}


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def kst(t, day: bool = False) -> str:
    """epoch 초 → 'HH:MM KST' (day 면 'MM-DD HH:MM KST'). 이 PC 의 시간대와 무관하게 한국 시각."""
    x = _num(t)
    if not x:
        return "-"
    return time.strftime("%m-%d %H:%M" if day else "%H:%M", time.gmtime(x + KST_OFFSET_S)) + " KST"


def ago(s) -> str:
    x = _num(s)
    if x is None:
        return "-"
    x = max(0.0, x)
    if x < 120:
        return f"{x:.0f}초 전"
    m = int(x // 60)
    return f"{m // 60}시간 {m % 60}분 전" if m >= 120 else f"{m}분 전"


def wifi_dbm(wifi: dict | None) -> float | None:
    """PC Wi-Fi 신호 (dBm). netsh 가 Rssi 를 주면 그 값, 아니면 신호 % 에서 근사한다.

    근거: Windows WLAN API 문서(WLAN_ASSOCIATION_ATTRIBUTES 의 wlanSignalQuality)가 신호 품질 0 은 −100 dBm, 100 은 −50 dBm
    이고 그 사이는 선형이라고 적는다. netsh 의 '신호 %' 가 그 값이다. 그래서 dBm ≈ % ÷ 2 − 100 (−70 dBm ≈ 60 %).
    """
    if not isinstance(wifi, dict):
        return None
    rssi = _num(wifi.get("rssi_dbm"))
    if rssi is not None:
        return rssi
    pct = _num(wifi.get("signal_pct"))
    return None if pct is None else pct / 2 - 100


def _i(light: str, reason: str, ai: str = "", human: str = "", short: str | None = None, cells=None) -> dict:
    """차선 안의 한 가지 문제. reason 은 같은 원인이면 늘 같은 글, short 는 띠에 보일 짧은 글(숫자 포함),
    cells 는 그 문제가 걸린 셀 시리얼(화면의 차선 카드가 '어느 셀인지'를 보인다)."""
    return {"light": light, "reason": reason, "ai": ai, "human": human, "short": short or reason,
            "cells": [s for s in (cells or []) if s is not None]}


def _serial_key(s):
    """시리얼 정렬 — 숫자는 숫자 순서로, 숫자가 아닌 것은 뒤에 (now.json 이 이상한 값을 써도 예외 없이)."""
    return (0, s, "") if isinstance(s, (int, float)) and not isinstance(s, bool) else (1, 0, str(s))


def worst(lights) -> str:
    """가장 나쁜 불. 미확인은 빨강과 같은 무게 — 둘 다 있으면 조치(빨강)로 부른다. 불 없음(none)은 세지 않는다."""
    ls = set(lights)
    for x in ("red", "unknown", "yellow"):
        if x in ls:
            return x
    return "green"


def _lane(key: str, value: str, issues: list[dict], unknown: dict | None = None, hold: bool = False) -> dict:
    if unknown is not None:
        issues = [unknown]
    bad = [i for i in issues if i["light"] != "green"]
    light = worst(i["light"] for i in bad)
    top = [i for i in bad if i["light"] == light]

    def join(f: str) -> str:
        return " · ".join(dict.fromkeys(i[f] for i in top if i[f]))

    return {"key": key, "name": KEY_LABELS[key], "light": light, "word": LIGHTS[light], "value": value,
            "reason": join("reason") or "이상 없음", "ai": join("ai"), "human": join("human"),
            "cells": sorted({s for i in top for s in i.get("cells") or []}, key=_serial_key),
            "fmea": FMEA[key], "issues": bad, "hold": hold}


def _none(key: str, value: str = NONE_REASON) -> dict:
    """불 없음 — 엔진 기록이 아예 없어 잴 것이 없는 차선. 전체 불·알림에 들어가지 않는다."""
    return {"key": key, "name": KEY_LABELS[key], "light": NONE, "word": WORDS[NONE], "value": value,
            "reason": NONE_REASON, "ai": "", "human": "", "cells": [], "fmea": FMEA[key], "issues": [], "hold": False}


def engine_state(now: dict, sup_engine: dict | None, cfg, now_t: float) -> tuple[str, float | None]:
    """엔진 기록의 상태와 심박 나이(초).

      none   now.json 이 없다 (아직 돈 적 없음 — 감시자도 조용히 둔다)
      ended  일부러 끝났다 (단계 DONE · STOPPED, 또는 엔진 기록(engine.json)의 끝난 이유 done · stopped · interrupted)
      live   심박이 감시자의 멈춤 문턱(supervisor.stale_limit) 안
      stale  그 밖 — 엔진이 죽었거나 멈췄다

    sup_engine 은 engine.json 의 내용(감시자가 옮겨 적은 것이든 파일을 직접 읽은 것이든)이다. 끝난 시각(ended)이 있고
    그것이 마지막 심박보다 뒤면 심박이 아직 신선해도 끝난 것으로 본다 — Ctrl+C 직후 몇 분 동안 '살아 있음'으로 보이지 않게.
    ended 를 모르면(옛 기록) 예전처럼 심박이 멈춘 뒤에만 끝난 이유를 믿는다.
    """
    if not now:
        return "none", None
    age = now_t - beat_of(now)
    phase = now.get("phase")
    if phase in ENDED_PHASES:
        return "ended", age
    ed = sup_engine or {}
    ended_t = _num(ed.get("ended"))
    if ed.get("exit") in ENDED_EXITS and ended_t and ended_t >= beat_of(now) - 5:
        return "ended", age
    if age <= stale_limit(phase, cfg, has_beat(now)):
        return "live", age
    if ed.get("exit") in ENDED_EXITS:
        return "ended", age
    return "stale", age


def _quiet_value(eng: str, now: dict, exit_kind: str | None = None) -> str:
    if eng == "none":
        return NONE_REASON
    how = "사람이 멈춤 · Ctrl+C" if exit_kind == "interrupted" else PHASE_KO.get(now.get("phase"), now.get("phase") or "-")
    return f"엔진 끝남({how}) · 마지막 기록 {kst(beat_of(now), day=True)}"


def _held(key: str, what: str) -> dict:
    """엔진 기록이 멈춰 볼 수 없는 차선 — 미확인 + hold. 띠에는 여러 차선이 한 줄(short 가 같다)로 모인다."""
    return _lane(key, "엔진 기록 멈춤", [], hold=True, unknown=_i(
        "unknown", f"엔진 기록이 멈춰 {what} 볼 수 없음",
        "원인은 '프로그램' 차선 — 감시자가 엔진을 되살린다", "'프로그램' 차선을 본다",
        short=HELD_SHORT))


# ---------- 차선 9개 ----------

def _power(osinfo, now_t: float, th: dict) -> dict:
    """1 전원 · OS — 업데이트 재시작 예약 · 일시 중지 만료 · 배터리 전원."""
    if not isinstance(osinfo, dict) or not _num(osinfo.get("t")):
        return _lane("power", "OS 정보 없음", [], unknown=_i(
            "unknown", "OS 정보 없음 — 감시자가 10분마다 모은다", "없음 — 감시자가 모으는 정보다",
            "감시자가 도는지 확인 (tools\\install_supervisor.ps1)"))
    t = _num(osinfo["t"])
    if now_t - t > th["osinfo_stale_s"]:
        return _lane("power", f"마지막 수집 {kst(t, day=True)}", [], unknown=_i(
            "unknown", f"OS 정보가 {th['osinfo_stale_s'] / 60:.0f}분 넘게 묵음 — 감시자가 멈췄을 수 있다", "없음",
            "감시자가 도는지 확인 (data/supervisor.log)"))
    pw = osinfo.get("power") if isinstance(osinfo.get("power"), dict) else {}
    up = osinfo.get("update") if isinstance(osinfo.get("update"), dict) else {}
    pause = up.get("pause") if isinstance(up.get("pause"), dict) else {}
    if "error" in pw and "error" in up:            # 전원도 업데이트도 못 읽었다 — 하나라도 읽었으면 읽은 것만 판정한다
        return _lane("power", "전원·업데이트 정보를 읽지 못함", [], unknown=_i(
            "unknown", "전원·업데이트 정보를 읽지 못함", "없음", "python tools/check_env.py 로 같은 항목을 직접 확인",
            short=f"OS 정보 오류: {str(pw['error'])[:60]}"))
    install = "관리자 PowerShell(cell-bench): powershell -ExecutionPolicy Bypass -File tools\\install.ps1 -Only pause"
    issues: list[dict] = []
    if up.get("reboot_pending"):
        issues.append(_i("yellow", "업데이트 재시작이 예약됨", "알림만 — 재시작하지 않는다",
                         "사이클 경계(만충 직후)에 PC 를 재시작하고, 감시자가 엔진을 되살렸는지 확인"))
    state, text, days = pause.get("state"), str(pause.get("text") or ""), _num(pause.get("days_left"))
    if state == "bad":
        issues.append(_i("red", "업데이트 일시 중지가 없거나 만료됨", "알림만", install, short=f"일시 중지: {text}"))
    elif state == "warn":
        soon = days is not None and 0 < days <= th["pause_warn_days"]
        issues.append(_i("yellow", f"업데이트 일시 중지가 {th['pause_warn_days']}일 안에 만료" if soon else "업데이트 일시 중지 확인 필요",
                         "알림만", install, short=f"일시 중지: {text}"))
    ac, pct = pw.get("ac"), _num(pw.get("battery_pct"))
    if ac is False:
        if pct is not None and pct < th["battery_alarm_pct"]:
            issues.append(_i("red", f"배터리 {th['battery_alarm_pct']}% 미만 — 곧 꺼진다", "알림만",
                             "충전기 연결 — PC 가 꺼지면 감시자·엔진이 함께 멈춘다", short=f"배터리 {pct:.0f} %"))
        else:
            issues.append(_i("yellow", "충전기가 빠져 배터리로 동작", "알림만", "노트북 충전기 연결 확인",
                             short=f"배터리로 동작 {pct:.0f} %" if pct is not None else "배터리로 동작"))
    parts = ["전원 연결" if ac else "배터리" if ac is False else "전원 모름"]
    if pct is not None and pw.get("has_battery") is not False:
        parts[0] += f" {pct:.0f} %"
    parts.append(f"일시 중지 {text}" if text else "일시 중지 모름")
    parts.append("재시작 대기 있음" if up.get("reboot_pending") else "재시작 대기 없음")
    return _lane("power", " · ".join(parts), issues)


def _program(now: dict, m: dict, sup: dict, fresh: bool, sup_age, eng: str, beat_age, cfg, th: dict, now_t: float,
             ed: dict | None = None) -> dict:
    """2 프로그램 — 엔진(감시자 판정 그대로) · 결과판 서버 · 감시자 자신 · 단계 체류 · 이상 빈도.
    ed 는 engine.json 의 내용(끝난 이유 exit · 끝내며 켜 둔 플러그 plug_on) — 사람이 멈춘 엔진(F14)을 알아보는 데 쓴다."""
    ed = ed or {}
    checks = sup.get("checks") if isinstance(sup.get("checks"), dict) else {}
    if fresh and checks.get("config") == "error":
        return _lane("program", "감시자 설정 오류", [], unknown=_i(
            "unknown", "감시자가 설정 파일을 읽지 못함 — 판정할 수 없다", "아무 조치도 하지 않는다 (무엇을 볼지조차 모른다)",
            "bench.json · --config 파일 확인 (data/supervisor.log)", short=str(sup.get("why") or "")[:120]))
    phase = now.get("phase")
    issues: list[dict] = []
    sig = (sup.get("signals") or {}) if fresh else {}
    hold = False
    e = sig.get("engine")
    if e:
        light, why = e[0], str(e[1] or "")
        if light == "hold":
            hold = True
            issues.append(_i("yellow", "엔진 심박이 멈춤 — 다음 점검에서 다시 본다",
                             "감시자가 한 번 더 보고, 그래도 멈춰 있으면 끝내고 플러그 ON 뒤 다시 띄운다", "준비만",
                             short=f"엔진 심박 {ago(beat_age)}"))
        elif light == "yellow":
            issues.append(_i("yellow", why, "감시자 판정 그대로 — 지켜보거나, 플러그 ON 을 먼저 하고 되살릴 수 있으면 엔진을 다시 띄운다 (무엇을 했는지는 이유 줄)",
                             "준비만 — 1시간에 3번 넘게 되풀이되면 감시자가 멈추고 사람을 부른다"))
        elif light in URGENT:
            issues.append(_i(light, why, "감시자가 되살리기를 멈추고 사람을 부른다 (한 일은 data/supervisor.log)",
                             "제어 PC 에서 이유와 supervisor.log 를 보고 엔진을 다시 시작"))
    elif eng == "stale" and fresh and sup.get("paused"):
        # 일시 중지 표지가 있으면 감시자는 점검만 하고 되살리지 않는다(빨강·미확인 알림은 보낸다). 그 사실대로 적는다
        max_h = _num(getattr(cfg, "supervisor_pause_max_h", None)) or 2
        issues.append(_i("red", f"엔진 심박이 멈춤 — 감시자 일시 중지 중이라 되살리지 않음 (표지: data/supervisor_pause, 최대 {max_h:g}시간)",
                         "없음 — 일시 중지 중에는 감시자가 조치하지 않고 빨강·미확인만 알린다",
                         "손대는 일이 끝났으면 data/supervisor_pause 를 지우고, 제어 PC 에서 엔진이 도는지 확인",
                         short=f"엔진 심박 {ago(beat_age)} · 감시자 일시 중지"))
    elif eng == "stale":
        issues.append(_i("red", "엔진 심박이 멈춤 — 되살릴 감시자 판정이 없다", "없음 — 감시자가 없으면 아무도 되살리지 않는다",
                         "제어 PC 에서 엔진을 확인하고 다시 시작 · 감시자 등록(tools\\install_supervisor.ps1)",
                         short=f"엔진 심박 {ago(beat_age)}"))
    elif eng == "live" and beat_age is not None and beat_age > warn_limit(phase, cfg):
        issues.append(_i("yellow", "엔진 심박이 멈춤 — 지켜보는 중",
                         "없음 — 감시자 일시 중지 중" if fresh and sup.get("paused") else "없음 — 감시자 판정이 없다",
                         "계속 멈춰 있으면 제어 PC 확인",
                         short=f"엔진 심박 {ago(beat_age)}"))
    if eng == "stale" and not hold and (not e or e[0] == "green"):
        # 엔진만 아는 차선들이 미확인(hold)이 됐는데 감시자가 그 일을 맡고 있지 않다(판정이 없거나 '정상'이라고 본다) — 원인은
        # 이 차선 하나에서 빨강과 같은 무게로 알리고 '확인'을 받는다(QA 5). 감시자가 맡고 있으면 올리지 않는다:
        #   보류(hold) — 첫 점검. 다음 점검에서 되살리거나 사람을 부른다
        #   노랑      — 되살리는 중 · 되살림. 그 사건은 노랑 1건이 설계다(되살릴 때마다 '미확인'+'복구' 2건과 15분 반복이 생기지 않게)
        #   빨강      — 이미 사람을 부른다(이 차선이 빨강이고 '확인'이 있다)
        what = (f"감시자 판정은 '{e[1]}' — 다음 점검(1분)에서 다시 본다" if e and e[1]
                else "없음 — 감시자 일시 중지 중" if fresh and sup.get("paused") else "없음 — 감시자 판정이 없다")
        issues.append(_i("unknown", "엔진 기록이 멈춰 셀 쪽 차선을 볼 수 없음", what,
                         "제어 PC 에서 엔진이 도는지 확인 (data/run.log · data/supervisor.log)", short=HELD_SHORT))
    b = sig.get("board")
    if b and b[0] != "green":
        issues.append(_i(b[0], str(b[1] or ""), "감시자가 결과판 서버를 다시 띄운다", "되풀이되면 data/board_stderr.txt 확인"))
    if not fresh:
        issues.append(_i("yellow", "감시자 없음", "없음 — 엔진이 죽거나 멈춰도 되살릴 장치가 없다",
                         "감시자 등록(tools\\install_supervisor.ps1) 또는 작업 스케줄러에서 'CellBench Supervisor' 실행",
                         short="감시자 기록 없음" if sup_age is None else f"감시자 기록 {ago(sup_age)}"))
    elif sup.get("paused"):
        issues.append(_i("yellow", "감시자 일시 중지 — 점검만 하고 되살리지 않는다", "없음 (일시 중지 중)",
                         "교체가 끝나면 data/supervisor_pause 가 지워졌는지 확인", short=f"감시자 일시 중지: {sup['paused']}"))
    elif checks.get("old_watchdog"):
        issues.append(_i("yellow", "옛 임시 감시자가 돌아 새 감시자는 점검만 한다", "없음 (옛 감시자 몫)",
                         "tools\\install_supervisor.ps1 로 옛 감시자를 끈다"))
    warn = sup.get("config_warning") if fresh else None
    if warn:
        issues.append(_i("yellow", "감시자와 엔진의 설정이 다름",
                         "감시자가 엔진의 설정(--config)을 덮어 판정하고 플러그를 다룬다 — data 폴더만 감시자 것을 쓴다",
                         "bench.json 과 엔진 설정 파일(engine.json 의 args.config)을 맞춘다 (data/supervisor.log 의 경고 줄)",
                         short=str(warn)))
    if "error" in m:
        issues.append(_i("yellow", "엔진이 신호등 지표를 모으지 못함", "없음 — now.json 은 계속 쓴다", "data/run.log 확인",
                         short=f"지표 오류: {str(m['error'])[:80]}"))
    if eng == "live":
        exp = th.get("dwell_expect_h") or {}
        since = _num(now.get("phase_since"))
        if phase in exp and since:
            h = exp[phase] if exp[phase] is not None else cfg.charge_timeout_h
            dwell = now_t - since
            if h and dwell > th["dwell_factor"] * h * 3600:
                ko = PHASE_KO.get(phase, phase)
                issues.append(_i("yellow", f"{ko} 단계가 기대({h:g}시간)의 {th['dwell_factor']:g}배를 넘김", "알림만",
                                 "셀 배터리 추세와 플러그 상태 확인 (운영 탭 그래프)", short=f"{ko} {dwell / 3600:.1f}시간째"))
        n = _num(m.get("events_1h"))
        if n is not None and n >= th["events_1h_warn"]:
            issues.append(_i("yellow", f"지난 1시간 이상 {th['events_1h_warn']}건 이상",
                             "이상마다 엔진이 기록하고, 사람이 봐야 하는 종류는 Slack 으로 보낸다", "추이 탭의 이상 기록 확인",
                             short=f"이상 {n:.0f}건/1시간"))
    if eng in ("none", "ended"):
        value = _quiet_value(eng, now, ed.get("exit"))
    else:
        value = f"{PHASE_KO.get(phase, phase or '-')} · 사이클 {now.get('cycle', '-')} · 심박 {ago(beat_age)}"
    return _lane("program", value, issues, hold=hold)


def _wireless(m: dict, sup: dict, fresh: bool, osinfo, os_ok: bool, eng: str, th: dict) -> dict:
    """3 무선 · LiveHub — PC Wi-Fi 신호 · 재연결 · LiveHub 닿음 · 셀 주소 변경."""
    wifi = osinfo.get("wifi") if os_ok and isinstance(osinfo.get("wifi"), dict) else None
    hub = ((sup.get("checks") or {}).get("wifi")) if fresh else None
    if wifi is None and hub is None and eng != "live":
        return _lane("wireless", "무선 지표 없음", [], unknown=_i(
            "unknown", "무선 지표 없음 — 감시자·엔진 기록이 없다", "없음", "감시자가 도는지 확인"))
    issues: list[dict] = []
    if hub in ("down", "reconnect_failed"):
        issues.append(_i("red", NET_DOWN, "엔진(셀이 안 들릴 때)·감시자(조치할 때)가 저장된 Wi-Fi 프로필로 다시 붙는다",
                         "LiveHub 전원과 PC Wi-Fi 연결 확인"))
    elif hub == "reconnected":
        issues.append(_i("yellow", NET_FIXED, "감시자가 저장된 Wi-Fi 프로필로 다시 붙였다",
                         "되풀이되면 Wi-Fi 절전·블루투스 확인 (python tools/check_env.py)"))
    dbm = wifi_dbm(wifi)
    pct = _num((wifi or {}).get("signal_pct"))
    sig_txt = (f"{pct:.0f}% " if pct is not None else "") + (f"(≈{dbm:.0f} dBm)" if dbm is not None else "")
    if dbm is not None and dbm < th["wifi_warn_dbm"]:
        issues.append(_i("yellow", f"PC Wi-Fi 신호가 {th['wifi_warn_dbm']} dBm 아래", "알림만",
                         "노트북과 LiveHub 의 위치·방향 확인", short=f"Wi-Fi 신호 {sig_txt}"))
    rc = _num(m.get("reconnects_24h")) if eng == "live" else None
    if rc is not None and rc >= th["reconnects_24h_warn"]:
        issues.append(_i("yellow", f"지난 24시간 Wi-Fi 재연결 {th['reconnects_24h_warn']}회 이상",
                         "셀이 하나도 안 들리면 엔진이 저장된 프로필로 다시 붙는다",
                         "Wi-Fi 어댑터 절전·블루투스 꺼짐 확인 (python tools/check_env.py)", short=f"재연결 {rc:.0f}회/24시간"))
    ip = _num(m.get("ip_changes_cycle")) if eng == "live" else None
    if ip is not None and ip >= th["ip_changes_warn"]:
        issues.append(_i("yellow", f"이번 사이클 셀 주소 변경 {th['ip_changes_warn']}회 이상",
                         "엔진이 바뀐 주소를 따라 깨우고 추출한다", "LiveHub 의 주소 임대·무선 상태 확인",
                         short=f"셀 주소 변경 {ip:.0f}회"))
    parts = []
    if wifi is not None:
        parts.append(f"Wi-Fi {wifi.get('ssid') or '-'} {sig_txt}".strip() if wifi.get("connected") is not False else "Wi-Fi 연결 안 됨")
    if hub is not None:
        parts.append({"ok": "LiveHub 닿음", "reconnected": "LiveHub 다시 붙음"}.get(hub, "LiveHub 안 닿음"))
    if rc is not None:
        parts.append(f"재연결 {rc:.0f}회/24시간")
    return _lane("wireless", " · ".join(parts) or "-", issues)


def _plug(now: dict, m: dict, sup: dict, fresh: bool, eng: str, cfg, th: dict) -> dict:
    """4 플러그 — 호출 재시도·지연 · 감시자의 플러그 ON · Dock 저전력 복구 · 릴레이 수명."""
    sig = ((sup.get("signals") or {}).get("plug_on")) if fresh else None
    if eng == "stale" and not sig:
        return _held("plug", "플러그 상태를")
    p = m.get("plug") if isinstance(m.get("plug"), dict) else {}
    issues: list[dict] = []
    if sig and sig[0] in URGENT:
        issues.append(_i(sig[0], str(sig[1] or ""), "감시자가 점검마다 다시 켜 본다",
                         "플러그 전원·시험망(2.4 GHz) 확인 — 필요하면 손으로 켠다"))
    elif sig and sig[0] == "yellow":
        issues.append(_i("yellow", str(sig[1] or ""), "감시자가 플러그를 켰다", "준비만"))
    _, _, gap, tries = cfg.stuck_rule()
    if eng == "live":
        r = _num(p.get("retries_1h"))
        if r is not None and r >= th["plug_retries_1h_warn"]:
            issues.append(_i("yellow", "플러그 호출이 실패해 다시 시도함 (지난 1시간)", f"엔진이 다시 시도했다 (호출마다 {cfg.plug_retries}번까지)",
                             "플러그 위치·2.4 GHz 신호 확인", short=f"플러그 재시도 {r:.0f}회/1시간"))
        s = _num(p.get("last_call_s"))
        if s is not None and s > th["plug_call_warn_s"]:
            issues.append(_i("yellow", f"플러그 응답이 {th['plug_call_warn_s']:g}초 넘게 걸림", "알림만",
                             "플러그 위치·2.4 GHz 신호 확인", short=f"플러그 호출 {s:.1f}초"))
        rec = _num(m.get("recover_cycle"))
        if rec:
            issues.append(_i("yellow", "Dock 저전력 — 이번 사이클에 엔진이 끊었다 켜서 되살림",
                             f"엔진이 플러그를 {gap:.0f}초 끊었다 켰다 (단계마다 {tries}번까지)", "Dock 어댑터·USB-C 접촉 확인 준비",
                             short=f"Dock 저전력 복구 {rec:.0f}회"))
        fail = _num(m.get("dock_power_fail"))
        if fail:
            issues.append(_i("red", "Dock 이 전력을 끌어 쓰지 않음 — 복구 한도를 다 씀",
                             f"엔진이 {tries}번 끊었다 켰는데도 안 돼 멈췄다", "Dock 전원 어댑터·케이블 확인"))
    toggles = _num(p.get("toggles_total"))
    life = _num(th.get("relay_life"))
    used = toggles / life * 100 if toggles is not None and life else None
    if used is not None and used >= th["relay_alarm_pct"]:
        issues.append(_i("red", f"릴레이 누적이 수명(가안)의 {th['relay_alarm_pct']}% 이상", "알림만", "플러그 교체",
                         short=f"릴레이 {toggles:,.0f}회 ({used:.0f}%)"))
    elif used is not None and used >= th["relay_warn_pct"]:
        issues.append(_i("yellow", f"릴레이 누적이 수명(가안)의 {th['relay_warn_pct']}% 이상", "알림만", "예비 플러그 준비",
                         short=f"릴레이 {toggles:,.0f}회 ({used:.0f}%)"))
    if eng == "none" and not issues:
        return _none("plug")                         # 엔진이 아직 돈 적 없다 — 플러그 상태를 잴 곳이 없다
    if eng in ("none", "ended") and not p:
        value = _quiet_value(eng, now)
    else:
        st = now.get("plug") if isinstance(now.get("plug"), dict) else {}
        w = _num(st.get("w"))
        parts = [("켜짐" if st.get("on") else "꺼짐" if st.get("on") is False else "-") + (f" {w:.1f} W" if w is not None else "")]
        s = _num(p.get("last_call_s"))
        if s is not None:
            parts.append(f"호출 {s:.1f}초")
        if toggles is not None:
            parts.append(f"릴레이 {toggles:,.0f}회 (가안 수명 {life:,.0f}회의 {used:.0f}%)")
        value = " · ".join(parts)
    return _lane("plug", value, issues)


def charge_trend(cycles, window: int) -> tuple[float, float] | None:
    """(마지막 만충 시간, 직전 window 사이클 평균) 분. 사람이 손댄 사이클(note 에 manual)은 빼고 본다. 모자라면 None."""
    vals = []
    for r in cycles or []:
        if not isinstance(r, dict) or "manual" in str(r.get("note") or ""):
            continue
        x = _num(r.get("charge_min"))
        if x is not None and x > 0:
            vals.append(x)
    if window <= 0 or len(vals) < window + 1:
        return None
    return vals[-1], sum(vals[-window - 1:-1]) / window


def _dock(now: dict, m: dict, cycles, eng: str, cfg, th: dict, sid) -> dict:
    """5 Dock · 셀 — 충전 안 오르는 셀 · 잔량 0% · 셀 저장량 · 만충 시간 추세."""
    if eng == "stale":
        return _held("dock", "Dock·셀을")
    issues: list[dict] = []
    trend = charge_trend(cycles, int(th["charge_slow_window"]))
    if trend and trend[1] > 0 and trend[0] >= trend[1] * (1 + th["charge_slow_pct"] / 100):
        issues.append(_i("yellow", f"만충 시간이 직전 {int(th['charge_slow_window'])}사이클 평균보다 {th['charge_slow_pct']:g}% 이상 늘어남",
                         "알림만", "Dock 어댑터 출력·셀 노화 추세 확인 (추이 탭)",
                         short=f"세트 {sid} 만충 {trend[0]:.0f}분 (직전 평균 {trend[1]:.0f}분)"))
    low = None
    store_txt = None
    if eng == "live":
        nc = [s for s in (m.get("not_charging") or []) if s is not None]
        if nc:
            issues.append(_i("yellow", "충전 중 혼자 안 오르는 셀 (Dock 접촉 불량 의심)", "알림만 — 엔진이 그 셀을 사이클에 한 번 알린다",
                             "그 셀의 Dock 자리·접점 확인", short=f"세트 {sid} 셀 {', '.join(map(str, nc[:4]))} 충전 안 오름", cells=nc))
        cells = [c for c in (now.get("cells") or []) if isinstance(c, dict)]
        fresh = [c for c in cells if (_num(c.get("age")) or 0) <= cfg.live_gap_alarm_s and not c.get("waiting")
                 and _num(c.get("battery")) is not None]
        if fresh:
            low = min(_num(c["battery"]) for c in fresh)
        empty = [c.get("serial") for c in fresh if _num(c["battery"]) <= 0]
        if empty:
            issues.append(_i("red", "셀 잔량 0%", f"없음 — 엔진은 기준선({cfg.discharge_stop_pct}%)에서 방전을 끝낸다. 0%면 그 셀이 충전되지 않은 것",
                             "그 셀을 Dock 에 다시 꽂고 충전되는지 확인 (꺼졌으면 Dock 버튼 2초)",
                             short=f"세트 {sid} 셀 {', '.join(map(str, empty[:4]))} 잔량 0 %", cells=empty))
        cs = m.get("cell_storage") if isinstance(m.get("cell_storage"), dict) else {}
        pct = _num(cs.get("max_pct"))
        if pct is None and "cell_storage" in m:
            # 엔진은 '아는 셀'만 요약한다(12시간 넘은 기록·삭제를 켜기 전 기록은 뺀다). 모르면 null — 추정으로 빨강을 내지 않는다
            store_txt = "저장량 모름 (다음 추출에서 잰다)"
        if pct is not None:
            ser = cs.get("max_serial")
            store_txt = f"저장 최대 {pct:.0f}% (셀 {ser})"
            # F15 — 지우기 전에 엔진이 라이브로 그 셀의 신원을 확인한다(FMEA 6.8). 그 셀의 라이브가 끊겼으면 엔진은 지우지 않는다.
            # 그때 "다음 추출에서 지운다"라고 쓰면 거짓이다 — 사람이 현장에서 해야 한다고 쓴다.
            lost = ser is not None and ser not in {c.get("serial") for c in fresh}
            if not cfg.delete_after_extract:
                ai, human_warn, human_red = ("알림만 — 추출 뒤 삭제가 꺼져 있다 (delete_after_extract)",
                                             "그 셀을 손으로 추출·삭제할 시점을 정한다", "그 셀을 손으로 추출·삭제")
            elif lost:
                ai = "라이브가 끊겨 엔진이 자동으로 지울 수 없음 — 현장에서 추출·삭제"
                human_warn = human_red = "그 셀을 Dock 에 다시 꽂아 라이브가 돌아오는지 보고, 안 돌아오면 현장에서 손으로 추출·삭제"
            else:
                ai = "다음 추출에서 엔진이 받은 뒤 지운다 (끝 표지·오류 0·크기·라이브 신원 확인이 맞을 때만)"
                human_warn, human_red = "다음 추출 뒤 저장량이 줄었는지 확인", "extract·identity 이상이 있으면 그 셀을 손으로 추출·삭제"
            short = f"세트 {sid} 셀 {ser} 저장 {pct:.0f}% · 가득 예상 {kst(cs.get('est_full_at'), day=True)}"
            if pct >= cfg.cell_storage_alarm_pct:
                issues.append(_i("red", f"셀 저장량 {cfg.cell_storage_alarm_pct:g}% 이상 — 곧 측정이 멈춘다", ai, human_red,
                                 short=short, cells=[ser]))
            elif pct >= cfg.cell_storage_warn_pct:
                issues.append(_i("yellow", f"셀 저장량 {cfg.cell_storage_warn_pct:g}% 이상", ai, human_warn,
                                 short=short, cells=[ser]))
    if eng == "none" and not issues:
        return _none("dock")
    if eng in ("none", "ended"):
        parts = [_quiet_value(eng, now)]
    else:
        parts = [f"최저 {low:.0f}%" if low is not None else "잔량 -"]
        if store_txt:
            parts.append(store_txt)
    if trend:
        parts.append(f"만충 {trend[0]:.0f}분")
    return _lane("dock", " · ".join(parts), issues)


def _live(now: dict, m: dict, eng: str, cfg, th: dict, sid) -> dict:
    """6 셀 수신 — 끊긴 셀 · 라이브 끊김 빈도 · 전부 끊김 · 대기 모드 셀."""
    if eng == "stale":
        return _held("live", "셀 수신을")
    if eng == "none":
        return _none("live")
    if eng == "ended":
        return _lane("live", _quiet_value(eng, now), [])
    cells = now.get("cells")
    if not isinstance(cells, list):
        return _lane("live", "셀 정보 없음", [])
    expected = int(_num(now.get("expected")) or len(cfg.serials))
    gap = cfg.live_gap_alarm_s
    waiting = [s for s in (m.get("waiting") or []) if s is not None]
    rx = [c for c in cells if isinstance(c, dict) and (_num(c.get("age")) is not None) and _num(c["age"]) <= gap
          and not c.get("waiting")]
    lost = max(0, expected - len(rx) - len(waiting))
    # 끊긴 셀의 시리얼 — 엔진이 들은 적 있는 셀(now.json cells) 중 지금 안 들리는 것 + 설정의 셀 중 한 번도 안 들린 것.
    # 설정의 셀 수가 엔진의 기대 수와 다르면(run_config 로 다른 셀을 돌림) 설정 쪽은 믿지 않고 들은 적 있는 셀만 쓴다.
    seen = {c.get("serial") for c in cells if isinstance(c, dict)}
    plan = set(cfg.serials) if len(cfg.serials) == expected else set()
    ok = {c.get("serial") for c in rx} | set(waiting)
    lost_serials = sorted((seen | plan) - ok - {None}, key=_serial_key)
    issues: list[dict] = []
    if expected and not rx:
        issues.append(_i("red", "셀 수신이 전부 끊김",
                         f"엔진이 {cfg.blind_reconnect_s:.0f}초 뒤 Wi-Fi 를 다시 붙이고, {cfg.blind_failsafe_min:g}분 넘으면 사이클을 멈추고 플러그 ON",
                         "LiveHub 와 셀 전원 확인 (셀이 꺼졌으면 Dock 버튼 2초)", short=f"세트 {sid} 수신 0/{expected}"))
    elif lost:
        issues.append(_i("yellow", f"셀 수신이 {gap:g}초 넘게 끊김", "엔진이 끊김을 기록한다 (live_gap)", "끊긴 셀의 전원·위치 확인",
                         short=f"세트 {sid} 끊김 {lost}대" + (f" ({', '.join(map(str, lost_serials[:4]))}"
                                                            + (" …" if len(lost_serials) > 4 else "") + ")" if lost_serials else ""),
                         cells=lost_serials))
    g = _num(m.get("live_gaps_1h"))
    if g is not None and g >= th["live_gaps_1h_warn"]:
        issues.append(_i("yellow", f"지난 1시간 라이브 끊김 {th['live_gaps_1h_warn']}회 이상", "엔진이 끊김을 기록한다 (live_gap)",
                         "끊기는 셀의 위치·LiveHub 와의 거리 확인", short=f"라이브 끊김 {g:.0f}회/1시간"))
    if waiting:
        issues.append(_i("yellow", "대기 모드 셀 (측정 멈춤)",
                         f"방전 중이면 엔진이 그 셀만 깨워 측정으로 되돌린다 (셀당 {cfg.waiting_resume_every_s / 60:.0f}분에 한 번)",
                         "되풀이되면 그 셀 확인", short=f"세트 {sid} 대기 {', '.join(map(str, waiting[:4]))}", cells=waiting))
    value = f"수신 {len(rx)}/{expected}" + (f" · 대기 {len(waiting)}" if waiting else "") + (f" · 끊김 {g:.0f}회/1시간" if g is not None else "")
    return _lane("live", value, issues)


def _disk(m: dict, sup: dict, fresh: bool, cfg) -> dict:
    """7 기록 · 디스크 — data 드라이브 여유. 감시자가 잰 값을 먼저, 없으면 엔진의 값."""
    free = _num(sup.get("disk_free_gb")) if fresh else None
    if free is None:
        free = _num(m.get("disk_free_gb"))
    if free is None:
        return _lane("disk", "아직 재지 않음", [], unknown=_i(
            "unknown", "디스크 여유를 잴 수 없음 — 감시자·엔진 기록이 없다", "없음", "감시자가 도는지 확인"))
    issues: list[dict] = []
    human = "data 폴더의 오래된 추출 파일(data/ftg)을 다른 드라이브로 옮긴다"
    if free < cfg.disk_alarm_gb:
        issues.append(_i("red", f"디스크 여유 {cfg.disk_alarm_gb:g} GB 미만", "알림만", human, short=f"디스크 여유 {free:.1f} GB"))
    elif free < cfg.disk_warn_gb:
        issues.append(_i("yellow", f"디스크 여유 {cfg.disk_warn_gb:g} GB 미만", "알림만", human, short=f"디스크 여유 {free:.1f} GB"))
    return _lane("disk", f"여유 {free:.1f} GB", issues)


def _cloud(m: dict, sup: dict, fresh: bool, eng: str, th: dict) -> dict:
    """8 클라우드 · 알림 — 전송 실패 · 키 거부 · 꺼짐 · Slack 연결."""
    c = m.get("cloud") if eng == "live" and isinstance(m.get("cloud"), dict) and m.get("cloud") else None
    slack = sup.get("slack") if fresh else None
    if c is None and slack is None:
        return _lane("cloud", "-", [], unknown=_i(
            "unknown", "클라우드·알림 상태를 읽을 수 없음 — 감시자·엔진 기록이 없다", "없음", "감시자가 도는지 확인"))
    issues: list[dict] = []
    parts = []
    if c is not None:
        if c.get("enabled") is False:
            issues.append(_i("yellow", "클라우드 전송 꺼짐", "없음 — 로컬 기록만 남긴다", "python tools/cloud_setup.py 로 키를 넣는다"))
            parts.append("클라우드 꺼짐")
        else:
            auth, fails = _num(c.get("consecutive_auth_fail")), _num(c.get("fail_1h"))
            # 이유(고정 글)는 문턱을, 띠·값(숫자)은 지금 횟수를 같은 말('키 연속 거부')로 적는다 — "4회"와 "3회 연속"이 어긋나 보이지 않게
            if auth is not None and auth >= th["cloud_auth_fail_alarm"]:
                issues.append(_i("red", f"클라우드 키 연속 거부 {th['cloud_auth_fail_alarm']}회 이상", "엔진이 Slack 으로 한 번 알렸다",
                                 "python tools/cloud_setup.py 로 키 교체", short=f"클라우드 키 연속 거부 {auth:.0f}회"))
            if fails is not None and fails >= th["cloud_fail_1h_warn"]:
                issues.append(_i("yellow", f"클라우드 전송 실패 (지난 1시간 {th['cloud_fail_1h_warn']}회 이상)",
                                 "실패한 전송은 30초·2분·10분 뒤 다시 보낸다", "인터넷(이더넷) 연결 확인",
                                 short=f"클라우드 실패 {fails:.0f}회/1시간"))
            parts.append(f"클라우드 켜짐 · 실패 {fails or 0:.0f}회/1시간" + (f" · 키 연속 거부 {auth:.0f}회" if auth else "")
                         + f" · 마지막 성공 {kst(c.get('ok_t'))}")
    if slack is False:
        issues.append(_i("yellow", "Slack 미연결", "알림을 보내지 못하고 기록만 한다", "python tools/remote_setup.py 로 웹훅을 넣는다"))
    if slack is not None:
        parts.append("Slack 연결" if slack else "Slack 미연결")
    return _lane("cloud", " · ".join(parts), issues)


def _interrupted(now: dict, eng: str, ed: dict, sig_plug, sid, guard: dict | None = None) -> dict | None:
    """F14 — 사람이 Ctrl+C 로 멈춘 엔진(engine.json exit=interrupted)인데 플러그가 꺼져 있다 → 준비(노랑). 조용한 초록 금지.
    감시자는 사람이 멈춘 엔진을 되살리지 않는다. 꺼진 채면 셀이 계속 방전한다.
    플러그 상태는 감시자의 플러그 지키기(supervisor.json plug_guard — 엔진이 멈추고 10분 뒤부터 10분마다 실제로 읽는다)가 읽은 값이
    먼저다. 꺼짐을 읽고 감시자가 켰으면(action=plug_on) 켜진 것이다. 아직 읽기 전이면 engine.json 의 plug_on → 엔진이 마지막에 본 값
    (now.json plug.on — 엔진이 이상 interrupted_plug_off 로 남기는 것과 같은 사실). 감시자가 플러그를 켰다고 알리면(signals.plug_on 노랑)
    켜진 것으로 본다. 꺼진 것을 아는 때만 올린다 — 모르면(옛 기록) 값 줄에만 남는다."""
    if eng != "ended" or ed.get("exit") != "interrupted":
        return None
    st = now.get("plug") if isinstance(now.get("plug"), dict) else {}
    on = ed.get("plug_on") if ed.get("plug_on") is not None else st.get("on")
    g = guard if isinstance(guard, dict) else {}
    if g.get("checked_t") and g.get("on") is not None:
        on = True if (g.get("on") is False and g.get("action") == "plug_on") else g.get("on")
    if (sig_plug or [None])[0] == "yellow":
        on = True
    if on is not False:
        return None
    return _i("yellow", INTERRUPTED,
              "감시자는 사람이 멈춘 엔진을 되살리지 않는다 — 감시자가 플러그를 켰으면 '플러그' 차선에 보인다",
              "플러그를 켜(Tapo 앱 · 플러그 버튼) 충전 쪽으로 두고, 시험을 이어 가려면 제어 PC 에서 엔진을 다시 시작",
              short=f"세트 {sid} 엔진을 사람이 멈춤(Ctrl+C) · 플러그 꺼짐")


def _human(now: dict, m: dict, eng: str, cfg, th: dict, sid, exit_kind: str | None = None, stopped: dict | None = None) -> dict:
    """9 사람 조작 — 지난 1시간 수동 플러그 조작 · 마지막 수동 조작 시각 · 사람이 멈춘 엔진(F14, stopped)."""
    if eng == "stale":
        return _held("human", "사람 조작을")
    if eng == "none":
        return _none("human")
    if eng != "live":
        return _lane("human", _quiet_value(eng, now, exit_kind), [stopped] if stopped else [])
    n = _num(m.get("manual_plug_1h"))
    last = _num(m.get("manual_plug_last"))
    issues: list[dict] = []
    if n is not None and n >= th["manual_plug_1h_warn"]:
        issues.append(_i("yellow", "지난 1시간 안에 수동 플러그 조작",
                         f"방전 중이면 엔진이 다시 껐다 (최저 배터리가 기준선+{cfg.manual_plug_margin_pct}%p 이하면 끄지 않고 충전으로 넘겼다)",
                         "누가·왜 조작했는지 확인", short=f"세트 {sid} 수동 플러그 조작 {n:.0f}회 · 마지막 {kst(last)}"))
    value = f"수동 조작 {n or 0:.0f}회/1시간 · 마지막 {kst(last, day=True) if last else '없음(24시간)'}"
    return _lane("human", value, issues)


# ---------- 시험대 전체 ----------

def compute(now: dict | None, sup: dict | None, osinfo: dict | None, cycles: list | None, cfg, now_t: float,
            plugs: dict | None = None, engine: dict | None = None, busy: dict | None = None) -> dict:
    """9개 차선과 시험대 띠 한 줄을 판정한다 — data/health.json · 클라우드 bench_health 의 내용 그대로.
    plugs 는 data/plugs.json (감시자의 플러그 탐색 — 세트 목록의 '미등록 감지'에만 쓴다. 차선의 불에는 들어가지 않는다).
    engine 은 data/engine.json (결과판 서버가 넘긴다) — 감시자가 없어도 엔진이 끝난 이유(exit · ended · plug_on)를 본다.
    감시자는 넘기지 않아도 된다: 감시자 자신의 기록(sup['engine'])에 같은 내용이 있다.
    busy 는 운전 중인 엔진이 쥔 셀·플러그(register.engine_claims — {"serials", "macs"}) — 미등록 감지에서 뺀다(등록 화면과 같은 수가 되게)."""
    th = thresholds(cfg)
    now = now if isinstance(now, dict) else {}
    sup = sup if isinstance(sup, dict) else {}
    m = now.get("metrics") if isinstance(now.get("metrics"), dict) else {}
    st = _num(sup.get("t"))
    sup_age = now_t - st if st else None
    fresh = sup_age is not None and sup_age <= th["supervisor_stale_s"]
    ed = {**((sup.get("engine") or {}) if fresh and isinstance(sup.get("engine"), dict) else {}),
          **(engine if isinstance(engine, dict) else {})}
    eng, beat_age = engine_state(now, ed or None, cfg, now_t)
    os_ok = isinstance(osinfo, dict) and bool(_num(osinfo.get("t"))) and now_t - _num(osinfo.get("t")) <= th["osinfo_stale_s"]
    sets = cfg.sets if isinstance(getattr(cfg, "sets", None), list) and cfg.sets else [{"id": 1}]
    sid = sets[0].get("id", 1) if isinstance(sets[0], dict) else 1
    mm = {} if "error" in m else m              # 지표를 모으다 실패했으면 지표 차선은 '없음'으로 본다 (프로그램 차선이 알린다)
    lanes = [
        _power(osinfo, now_t, th),
        _program(now, m, sup, fresh, sup_age, eng, beat_age, cfg, th, now_t, ed),
        _wireless(mm, sup, fresh, osinfo, os_ok, eng, th),
        _plug(now, mm, sup, fresh, eng, cfg, th),
        _dock(now, mm, cycles, eng, cfg, th, sid),
        _live(now, mm, eng, cfg, th, sid),
        _disk(mm, sup, fresh, cfg),
        _cloud(mm, sup, fresh, eng, th),
        _human(now, mm, eng, cfg, th, sid, ed.get("exit"),
               _interrupted(now, eng, ed, ((sup.get("signals") or {}).get("plug_on")) if fresh else None, sid,
                            sup.get("plug_guard") if fresh else None)),
    ]
    light = worst(l["light"] for l in lanes)
    counts = {k: sum(1 for l in lanes if l["light"] == k) for k in ("yellow", "red", "unknown")}
    head, items = band_parts(lanes, counts)
    return {"bench_id": cfg.bench_id, "bench_name": cfg.bench_name, "t": now_t, "light": light, "word": LIGHTS[light],
            "reason": band_reason(lanes, counts), "reason_head": head, "reason_items": items,
            "todo": band_todo(lanes, light), "counts": counts, "lanes": lanes,
            "sets": [_set_light(s, i, lanes) for i, s in enumerate(sets)] + _detected(now, eng, plugs, cfg, now_t, busy),
            "engine": eng, "engine_exit": ed.get("exit"), "phase": now.get("phase"), "cycle": now.get("cycle"),
            "beat_age": None if beat_age is None else round(beat_age, 1),
            "supervisor_age": None if sup_age is None else round(sup_age, 1),
            "supervisor": ("감시자 없음" if sup_age is None else f"감시자 소식 {ago(sup_age)}" if not fresh
                           else "감시자 일시 중지" if sup.get("paused") else "감시자 정상"),
            "stale_after_s": th["cloud_board_stale_s"]}


def _clip(s: str, n: int = SHORT_MAX) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def band_parts(lanes: list[dict], counts: dict) -> tuple[str, list[str]]:
    """띠의 머리(건수)와 짧은 글 목록 — 나쁜 것부터, 같은 글은 한 번. 화면이 폭에 맞춰 몇 개를 보일지 정한다(폰은 하나 + '외 N건').
    짧은 글 하나는 SHORT_MAX 글자에서 자른다(감시자의 긴 이유가 띠를 덮지 않게 — 전문은 차선 카드에 있다)."""
    none = sum(1 for l in lanes if l["light"] == NONE)
    tail = f" · 기록 없음 {none}" if none else ""
    if not any(counts.values()):
        return ("모든 차선 정상" if not none else f"정상 · 기록 없음 {none}개 차선 — 엔진이 아직 돈 적 없음"), []
    head = f"준비 {counts['yellow']}건 · 조치 {counts['red']}건" + (f" · 미확인 {counts['unknown']}건" if counts["unknown"] else "") + tail
    order = {"red": 0, "unknown": 1, "yellow": 2}
    tops = sorted(((order[i["light"]], i["short"]) for l in lanes for i in l["issues"] if i["light"] == l["light"]),
                  key=lambda x: x[0])
    return head, list(dict.fromkeys(_clip(s) for _, s in tops))


def band_reason(lanes: list[dict], counts: dict) -> str:
    """띠 한 줄 — '준비 2건 · 조치 0건 — 디스크 여유 18.2 GB · 세트 1 셀 11740 잔량 0 %'. 나쁜 것부터 세 가지까지 (Slack · 클라우드 요약)."""
    head, shorts = band_parts(lanes, counts)
    if not shorts:
        return head
    more = f" 외 {len(shorts) - 3}건" if len(shorts) > 3 else ""
    return f"{head} — {' · '.join(shorts[:3])}{more}"


def band_todo(lanes: list[dict], light: str) -> dict | None:
    """띠 둘째 줄 '지금 할 일' — 가장 나쁜 차선(조치 → 미확인 → 준비, 같으면 차선 순서)의 '사람' 할 일 한 줄.
    hold 차선은 건너뛴다(그 할 일은 "'프로그램' 차선을 본다"뿐이고, 원인 차선이 따로 있다)."""
    if light not in ("red", "unknown", "yellow"):
        return None
    for want in ("red", "unknown", "yellow"):
        for l in lanes:
            if l["light"] == want and not l.get("hold") and l.get("human"):
                return {"key": l["key"], "name": l["name"], "light": want, "human": l["human"]}
    return None


def _set_light(s, i: int, lanes: list[dict]) -> dict:
    sid = s.get("id", i + 1) if isinstance(s, dict) else i + 1
    label = s.get("label", "") if isinstance(s, dict) else ""
    if i > 0:      # 지금 엔진은 sets[0] 하나만 돈다 — 나머지는 등록만 됐다 (다중 세트 운전은 feat/multi-set-bench). 불 없음 = 화면의 회색
        return {"id": sid, "label": label, "running": False, "light": None, "word": "대기",
                "reason": "등록됨 · 운전 대기 (다중 세트 기능 적용 뒤 운전)"}
    mine = [l for l in lanes if l["key"] in SET_LANES and l["light"] != NONE]
    if not mine:   # 엔진 기록이 아예 없다 — 세트 카드도 '정상'이 아니라 '기록 없음'(회색)
        return {"id": sid, "label": label, "running": True, "light": NONE, "word": WORDS[NONE], "reason": NONE_REASON}
    light = worst(l["light"] for l in mine)
    why = [f"{l['name']}: {l['reason']}" for l in mine if l["light"] == light and light != "green"]
    return {"id": sid, "label": label, "running": True, "light": light, "word": LIGHTS[light],
            "reason": " · ".join(why) or "이상 없음"}


def _detected(now: dict, eng: str, plugs: dict | None, cfg, now_t: float, busy: dict | None = None) -> list[dict]:
    """미등록 감지 — 어느 세트에도 속하지 않는데 들리는 셀(엔진의 now.json heard)과 탐색된 플러그(data/plugs.json).
    있으면 세트 목록 끝에 '등록 필요'(노랑) 한 칸을 붙인다 → 결과판 맵의 첫 빈 자리 · 세트 카드 · 머리글 칩의 재료.

    차선이 아니므로 시험대 전체 불과 Slack 알림을 올리지 않는다 — 운전 중인 세트와 무관한, 사람이 할 일(등록)의 안내라서다.
    엔진이 살아 있을 때의 heard 만 믿는다(멈춘 엔진의 목록은 옛것). 탐색 결과는 주기의 3배보다 묵으면 쓰지 않는다.
    지금 설정에 등록된 시리얼·MAC 은 뺀다 — 등록 직후 엔진이 옛 설정으로 그 셀을 아직 미등록으로 적기 때문이다.
    """
    reg = {**registered_serials(cfg), **((busy or {}).get("serials") or {})}
    heard = now.get("heard") if eng == "live" and isinstance(now.get("heard"), list) else []
    lag = max(0.0, now_t - (_num(now.get("t")) or now_t))
    cells = [c for c in heard if isinstance(c, dict) and c.get("serial") not in reg
             and (_num(c.get("age")) or 0) + lag <= cfg.heard_window_s]
    found = []
    if isinstance(plugs, dict) and _num(plugs.get("t")) and now_t - _num(plugs["t"]) <= 3 * cfg.plug_scan_every_s:
        macs = {**registered_macs(cfg), **((busy or {}).get("macs") or {})}
        found = [p for p in plugs.get("plugs") or [] if isinstance(p, dict) and str(p.get("mac") or "").upper() not in macs]
    if not cells and not found:
        return []
    sets = cfg.sets if isinstance(getattr(cfg, "sets", None), list) else []
    n, m = len(cells), len(found)
    return [{"id": next_set_id(sets), "label": "", "running": False, "light": "yellow", "word": "등록 필요",
             "reason": f"미등록 감지 · 셀 {n} · 플러그 {m}", "detect": {"cells": n, "plugs": m}}]


def alert_lights(health: dict) -> dict:
    """알림기(Alerter.update)에 넘길 {차선 키: (불, 이유)} — hold 차선과 불 없는 차선(기록 없음)은 빼서 그 불을 건드리지 않게 한다."""
    return {l["key"]: (l["light"], l["reason"]) for l in health.get("lanes", [])
            if not l.get("hold") and l.get("light") != NONE}


def ack_record(acks: dict | None, key: str, reason: str, light: str, t: float) -> dict:
    """'확인'을 누른 그 원인을 확인 시각과 함께 적은 alert_ack.json 의 새 내용 — {키: 시각, ACK_REASONS: {키: {reason, light, t}}}.
    키: 시각 칸은 알림기(alert.Alerter)가 읽는 옛 꼴 그대로 두고, 원인은 따로 한 칸에 둔다(알림기는 그 칸을 읽지 않는다)."""
    out = dict(acks or {})
    why = dict(out.get(ACK_REASONS) or {}) if isinstance(out.get(ACK_REASONS), dict) else {}
    out[str(key)] = float(t)
    why[str(key)] = {"reason": str(reason or ""), "light": str(light or ""), "t": float(t)}
    out[ACK_REASONS] = why
    return out


def mark_acks(health: dict, acks: dict | None, alerts: dict | None = None) -> dict:
    """빨강·미확인 차선에 사람이 누른 '확인' 시각(acked_at)을 붙인다. 확인은 '그 원인'에 묶인다.

      · 확인을 누를 때 적어 둔 원인(ACK_REASONS 의 reason · light)이 지금 차선의 이유·불과 다르면 친다 — 새 원인이라 다시 '확인'.
      · 알림기에서 그 차선의 불이 시작된 시각(since)을 알면 그 뒤에 누른 확인만 친다 — 같은 이유로 꺼졌다 다시 켜진 새 빨강도 다시 받는다.
      · 감시자가 없어 since 를 모르면 적어 둔 원인이 있어야 친다(원인 없이 남은 옛 확인은 언제의 것인지 알 수 없다).
    ack_old 는 "확인한 적은 있지만 그 뒤 원인이 바뀌었다" — 화면이 '다시 확인'으로 보인다.
    """
    acks = acks if isinstance(acks, dict) else {}
    why = acks.get(ACK_REASONS) if isinstance(acks.get(ACK_REASONS), dict) else {}
    for lane in health.get("lanes", []):
        key = lane["key"]
        t = _num(acks.get(key))
        since = _num(((alerts or {}).get(key) or {}).get("since"))
        rec = why.get(key) if isinstance(why.get(key), dict) else None
        same = rec is not None and rec.get("reason") == lane["reason"] and rec.get("light") in (lane["light"], "", None)
        if since is not None:
            ok = t is not None and t >= since and (rec is None or same)
        else:
            ok = t is not None and same
        urgent = lane["light"] in URGENT
        lane["acked_at"] = t if (t and urgent and ok) else None
        lane["ack_old"] = bool(t and urgent and not ok)
    return health
