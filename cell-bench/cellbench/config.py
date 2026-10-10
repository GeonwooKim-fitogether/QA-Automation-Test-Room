"""시험대 설정 — 한 곳에서만 바꾼다.

값의 근거는 2026-10-07 실측이다(README '실측값' 절). 기준선·만충 조건은 가안이며,
몇 사이클 돌려 본 뒤 조정한다.

불러오는 순서 (뒤가 앞을 덮는다) — Config.load():
  1. 이 파일의 코드 기본값 (표준 시험)
  2. cell-bench/bench.json — 이 시험대만의 값 (시험대 이름·세트·시리얼·플러그).
     git 에 올리지 않는다: 등록 화면이 운전 중에 이 파일을 고치므로, 저장소에 두면 TestPC 의
     git pull 이 충돌한다. 형식은 저장소의 bench.example.json 을 본다.
  3. --config 로 준 파일 — 한 번의 실행만 바꿀 때
시험 프로그램(run_cycle.py)과 감시자(supervise.py)가 같은 순서로 같은 값을 읽는다 — 값을 복사해 두지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

BENCH_FILE = Path(__file__).resolve().parents[1] / "bench.json"

# 세트 1 — 세트 = 스마트 플러그 1 · Dock 1 · 셀 24. 운전 중인 세트의 serials·plug_mac 기본값도 여기서 나온다.
DEFAULT_SET = {"id": 1, "plug_mac": "20:E1:5D:E6:9C:77", "serials": "11733-11756", "label": "CLBY4B"}

# 신호등 문턱 (cellbench/health.py — 9개 차선의 노랑·빨강). 2026-10-10 사용자가 "설계안 초안을 시작값으로" 쓰기로 했다.
# 이미 Config 에 있는 문턱(심박 heartbeat_* · 라이브 끊김 live_gap_alarm_s · 충전 한도 charge_timeout_h · 셀 저장량 cell_storage_*_pct ·
# 디스크 disk_*_gb)은 여기 다시 두지 않고 그 값을 그대로 쓴다 — 같은 문턱이 두 곳에 있으면 어긋난다.
# bench.json 에 "health": {"wifi_warn_dbm": -75} 처럼 일부만 줘도 된다 — 주지 않은 키는 이 값 (health.thresholds).
HEALTH = {
    # 1 전원 · OS
    "pause_warn_days": 7,          # 업데이트 일시 중지 만료까지 이 일수 이하면 노랑, 지났으면 빨강 (tools/check_env.pause_check 의 warn_days)
    "battery_alarm_pct": 20,       # 충전기가 빠져 배터리로 돌면 노랑, 배터리가 이 % 미만이면 빨강
    "osinfo_every_s": 600,         # 감시자가 운영체제 정보를 모으는 주기 (data/osinfo.json)
    "osinfo_stale_s": 1800,        # osinfo.json 이 이보다 오래되면 '전원 · OS' 를 판정할 수 없다 (신호 없음)
    # 2 프로그램 — 심박 문턱은 감시자 판정과 같은 heartbeat_warn_s · heartbeat_stale_s · heartbeat_stale_extract_s
    "supervisor_stale_s": 180,     # supervisor.json 이 이보다 묵으면 '감시자 없음' 노랑 (감시자는 1분마다 쓴다)
    "dwell_factor": 1.5,           # 단계 체류가 기대 시간의 이 배를 넘으면 노랑
    "dwell_expect_h": {"DISCHARGE": 5.0, "PRECHARGE": None, "CHARGE": None},   # 단계별 기대 시간. None = charge_timeout_h
    "events_1h_warn": 3,           # 지난 1시간 이상이 이 수 이상이면 노랑
    # 3 무선 · LiveHub
    "wifi_warn_dbm": -70,          # PC Wi-Fi 신호가 이 아래면 노랑. netsh 는 % 만 주므로 dBm 으로 근사한다 (health.wifi_dbm 의 근거)
    "reconnects_24h_warn": 2,      # 지난 24시간 Wi-Fi 재연결이 이 수 이상이면 노랑
    "ip_changes_warn": 3,          # 이번 사이클 셀 주소(DHCP) 변경이 이 수 이상이면 노랑
    # 4 플러그
    "plug_retries_1h_warn": 1,     # 지난 1시간 플러그 호출 실패(재시도)가 이 수 이상이면 노랑
    "plug_call_warn_s": 3.0,       # 마지막 플러그 호출이 이보다 오래 걸렸으면 노랑
    "relay_life": 30000,           # 가안 — 릴레이 수명(켜짐↔꺼짐 횟수). P110M 자료에 수명이 없어(2026-10-10 확인) 보수적으로 둔다
    "relay_warn_pct": 70,          # 릴레이 누적이 수명의 이 % 이상이면 노랑 (예비 플러그 준비)
    "relay_alarm_pct": 90,         # 이 % 이상이면 빨강 (교체)
    # 5 Dock · 셀 — 셀 저장량 문턱은 cell_storage_warn_pct · cell_storage_alarm_pct
    "charge_slow_pct": 20,         # 만충 시간(cycles.csv charge_min)이 직전 사이클 평균보다 이 % 이상 늘면 노랑
    "charge_slow_window": 3,       # 직전 몇 사이클의 평균과 비교하나
    # 6 셀 수신 — 끊김 문턱은 live_gap_alarm_s
    "live_gaps_1h_warn": 3,        # 지난 1시간 라이브 끊김이 이 수 이상이면 노랑
    # 7 기록 · 디스크 — disk_warn_gb · disk_alarm_gb
    # 8 클라우드 · 알림
    "cloud_fail_1h_warn": 3,       # 지난 1시간 클라우드 전송 실패가 이 수 이상이면 노랑
    "cloud_auth_fail_alarm": 3,    # 키 거부(401·403)가 이만큼 연속이면 빨강 (cloud.AUTH_FAIL_ALERT_AFTER 와 같은 값)
    "cloud_board_stale_s": 300,    # 클라우드 결과판: bench_health 가 이보다 묵으면 화면이 스스로 전체를 '신호 없음'으로 (클라우드 심박 감시와 같은 5분)
    # 9 사람 조작
    "manual_plug_1h_warn": 1,      # 지난 1시간 수동 플러그 조작(manual_plug)이 이 수 이상이면 노랑
}

_MAC = re.compile(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}")
_SPAN = re.compile(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?")
_MAX_SPAN = 1000          # 범위 하나가 이보다 넓으면 오타로 본다 (Dock 하나에 셀 24대)


def expand_serials(spec) -> list[int]:
    """시리얼 표기를 목록으로 펼친다.

    "11733-11756" · "11733-11740, 11750" · [11733, "11740-11742"] · 11733 을 모두 받는다.
    순서를 지키고 중복은 그대로 둔다 — 중복은 validate() 가 문제로 알린다. 형식이 틀리면 ValueError.
    """
    if isinstance(spec, bool):
        raise ValueError(f"시리얼 표기를 읽을 수 없다: {spec!r}")
    if isinstance(spec, int):
        return [spec]
    if isinstance(spec, str):
        parts = [p for p in spec.split(",") if p.strip()]
    elif isinstance(spec, (list, tuple)):
        parts = list(spec)
    else:
        raise ValueError(f"시리얼 표기를 읽을 수 없다: {spec!r}")
    out: list[int] = []
    for p in parts:
        if isinstance(p, int) and not isinstance(p, bool):
            out.append(p)
            continue
        m = _SPAN.fullmatch(p) if isinstance(p, str) else None
        if not m:
            raise ValueError(f"시리얼 표기를 읽을 수 없다: {p!r}")
        a = int(m.group(1)); b = int(m.group(2) or a)
        if b < a:
            raise ValueError(f"시리얼 범위가 거꾸로다: {p!r}")
        if b - a >= _MAX_SPAN:
            raise ValueError(f"시리얼 범위가 너무 넓다(오타?): {p!r}")
        out.extend(range(a, b + 1))
    return out


@dataclass
class Config:
    # --- 시험대 (bench.json 으로 이 시험대만의 값을 준다) ---
    bench_id: str = "hq-bench-1"           # 기록·클라우드 행에 붙는 시험대 이름표
    bench_name: str = "본사 셀 시험대 1호"   # 알림·결과판에 보이는 이름
    # 세트 목록. 지금 엔진은 sets[0](운전 중인 세트) 하나만 돈다 — 다중 세트 운전은 feat/multi-set-bench.
    sets: list[dict] = field(default_factory=lambda: [dict(DEFAULT_SET)])

    # --- 시험망 (LiveHub FTG-3D93, 인터넷 없음) ---
    pc_ip: str = "192.168.1.100"          # 노트북 Wi-Fi 고정 주소. 셀은 이 주소(ap_ip)로만 라이브를 보낸다
    port: int = 60222                      # 셀 → PC 라이브(UDP) · PC ← 셀 명령(TCP) 공용 포트
    wake_port: int = 9999                  # PC → 셀 깨우기(UDP)
    broadcast: str = "192.168.1.255"

    # --- 셀 (운전 중인 세트의 값 — bench.json 에 sets 만 있으면 sets[0] 에서 채운다, Config.load 참고) ---
    serials: list[int] = field(default_factory=lambda: expand_serials(DEFAULT_SET["serials"]))  # 24대
    live_gap_alarm_s: float = 15.0         # 라이브 신호가 이만큼 끊기면 이상 기록 (평소 0.5초 간격)
    resume_timeout_s: float = 90.0         # 0x26 뒤 측정 복귀를 기다리는 한도
    extract_batch: int = 6                 # 동시 추출 대수 (5GHz 에서 6대 2.88 MB/s 가 가장 효율적)
    # 0x13 삭제 — 2026-10-10 셀 저장 상한 실측으로 켠다. 23대가 같은 크기(≈160 MB)에서 측정·라이브 송신을 멈췄고,
    # LiveHub 연결·명령 응답은 되지만 0x26 복귀 뒤에도 측정으로 돌아오지 않았다. 3.45 MB/h 라 지우지 않으면 약 46시간마다 다시 찬다.
    # 지운 데이터는 되살릴 수 없으므로 조건은 엄격하다(cells.extract_problem): 끝 표지까지 받았고(ended) · 오류 블록 0 ·
    # 받은 바이트 ≥ 상태 응답의 크기. 하나라도 어긋나면 지우지 않고 이상(extract)으로 남긴다. 받은 파일은 data/ftg/<사이클>/ 에,
    # 지웠는지는 cells_<사이클>.csv 의 deleted 열에 남는다.
    delete_after_extract: bool = True
    # 셀 저장량 추정 (2026-10-10 실측). 셀에 크기를 따로 묻지 않는다 — 묻는 동안 측정이 20초 멈추기 때문이다.
    # 추출 때 받은 상태 응답의 크기와 삭제 여부를 '마지막으로 아는 크기'로 두고, 그 뒤 경과 시간 × 증가율로 지금 크기를 추정한다
    # (guards.storage_now). MB = 1,048,576 바이트 (이 코드 전체의 관례).
    cell_storage_cap_mb: float = 160.0     # 이 크기에서 측정·라이브가 멈춘다 (23대 실측)
    cell_storage_rate_mb_h: float = 3.45   # 증가율 (10-07 실측 3.44~3.54)
    cell_storage_warn_pct: float = 80      # 이 이상이면 노란불 — 이상 cell_storage (셀당 한 번)
    cell_storage_alarm_pct: float = 95     # 이 이상이면 빨간불 — 이상 cell_storage_critical (셀당 한 번)
    # 추출 때 잰 크기를 이만큼까지만 믿는다. 더 오래된 값, 그리고 엔진이 시작할 때 이어받는 기록 중 '삭제를 켜기 전' 추출의 값은
    # '모름'으로 뺀다 — 엔진 밖에서(사람이 손으로) 지웠으면 그 기록은 낡았고, 낡은 추정으로 빨강을 내거나 대기 셀 복귀를 막으면 안 된다(검토 F7).
    # 모르는 셀은 다음 추출에서 다시 잰다.
    cell_storage_known_max_h: float = 12.0

    # --- 스마트 플러그 (Tapo P110M, KLAP) ---
    plug_mac: str = DEFAULT_SET["plug_mac"]  # IP 는 DHCP 라 바뀔 수 있어 MAC 으로 찾는다
    plug_ip_hint: str = "192.168.1.103"    # 마지막으로 본 주소. 먼저 시도하고 안 되면 MAC 으로 재탐색
    keyring_service: str = "cell-bench-tapo"  # Windows 자격 증명 관리자에 저장된 TP-Link 계정 (username/password)
    plug_retries: int = 3
    plug_call_timeout_s: float = 20.0      # 플러그 호출 한 번의 상한. 무선이 반쯤 끊긴 채 응답을 영영 안 주면 흐름 전체가 멈춘다
    plug_on_min_w: float = 40.0            # 켠 뒤 이 이상이면 "충전 시작" (24셀 충전 68 W, 바닥 31 W)
    # Dock 저전력 자동 복구 — 플러그가 켜져 있어야 하는 모든 단계(guards.PLUG_EXPECT)에서 쓴다 (guards.PowerWatch).
    # Dock 이 켠 지 20초 만에 충전을 멈춰 플러그가 켜진 채 1.5 W 로 남는 일이 있었고(2026-10-08 19:44, 세트 1),
    # 30초 끊었다 켜니 68 W 로 돌아왔다(19:53). 처음엔 예비 충전에만 두었는데 10-10 세트 2 에서도 같은 일(켜짐 · 1.4 W)이 나서
    # 모든 ON 단계로 넓혔다. 바닥 전력(만충 뒤 31 W)과 구분되게 10 W 아래(또는 꺼짐)가 1분 이어지면 끊었다 켠다.
    stuck_w: float = 10.0                  # 켜져 있는데 이 아래면 Dock 이 끌어 쓰지 않는 것
    stuck_s: float = 60.0                  # 이만큼 이어지면 복구
    stuck_gap_s: float = 30.0              # 끊어 두는 시간 (10초로는 안 됐다)
    stuck_max: int = 3                     # 단계 한 번에 복구 시도 한도. 다 써도 안 되면 이상 dock_power
    # 옛 이름 (예비 충전에만 있던 때) — 옛 --config 파일이 그대로 돌게 남겨 둔다. 주면 위의 새 이름보다 이긴다 (stuck_rule)
    precharge_stuck_w: float | None = None
    precharge_stuck_s: float | None = None
    precharge_recover_gap_s: float | None = None
    precharge_recover_max: int | None = None

    # --- 사이클 판정 ---
    discharge_stop_pct: int = 30           # 기준선. 24셀 중 하나라도 이 이하면 방전 끝
    full_flat_min: float = 10.0            # 만충: 전력이 이 시간 동안 평평하고
    full_flat_tol_w: float = 1.0           #       변동폭이 이 안이며
    full_requires_all_100: bool = True     #       24셀이 모두 100% 를 보고할 때
    charge_timeout_h: float = 4.0          # 이 시간 안에 만충이 안 되면 이상 기록 후 다음 단계로
    poll_s: float = 20.0                   # 플러그 전력·배터리 표본 주기

    # --- 끊김 대비 (2026-10-07 밤 사고에서 나옴) ---
    hub_ip: str = "192.168.1.1"
    wifi_profile: str = "FTG-3D93-5G"      # 셀이 하나도 안 들리면 이 저장된 프로필로 다시 연결 (설정은 안 바꿈)
    wifi_reconnect: bool = True
    blind_reconnect_s: float = 60.0        # 셀이 하나도 안 들린 지 이만큼 지나면 재연결 시도 (2분마다)
    blind_failsafe_min: float = 5.0        # 이만큼 계속 안 보이면 사이클을 중단하고 플러그 ON(충전 쪽이 안전)
    max_consecutive_failures: int = 5      # 연속으로 이만큼 사이클이 깨지면 플러그 ON 으로 두고 멈춤
    command_max_age_s: float = 120.0       # 원격 명령은 보낸 뒤 이 시간 안에만 실행한다. 넘으면 실행하지 않고 버린다(QA C-1, 10-10)

    # --- 안전망 (FMEA P4 — 엔진이 스스로 잡는 고장. 판정은 cellbench/guards.py) ---
    manual_plug_margin_pct: int = 10       # 방전 중 원격 명령 없이 켜진 플러그: 최저 배터리가 기준선 + 이것 이하면 끄지 않고 충전으로 넘긴다
    not_charging_median_pct: int = 10      # 충전 중 셀들의 배터리 상승 중앙값이 이만큼 넘었는데
    not_charging_min_pct: int = 2          #   어떤 셀이 이만큼도 못 올랐고
    not_charging_full_pct: int = 95        #   아직 이 아래면 그 셀의 Dock 접촉 불량으로 본다 (이상 cell_not_charging)
    waiting_resume_s: float = 60.0         # 방전 중 대기 모드(0x16 만 오고 0x09 가 끊김)가 이만큼 이어지면 그 셀만 깨워 0x26 복귀
    waiting_resume_every_s: float = 600.0  # 같은 셀은 이 간격 안에 다시 깨우지 않는다
    disk_check_s: float = 600.0            # data 폴더 드라이브 여유를 보는 주기
    disk_warn_gb: float = 20.0             # 여유가 이 아래면 노란불 (이상 disk)
    disk_alarm_gb: float = 5.0             # 이 아래면 빨간불 (이상 disk_critical)

    # --- 감시자 (supervise.py — 엔진 밖에서 되살린다. 2026-10-08 업데이트 재시작 사고에서 나옴) ---
    supervisor_tick_s: float = 60.0        # 점검 주기
    # 심박(now.json 의 beat)이 멈춘 시간으로 엔진의 '멈춤(행)'을 가른다. 평소엔 20초마다 뛰지만, 정상 엔진도
    # 플러그 재시도(최대 3×(20+2)초) + Wi-Fi 재연결(~25초) + 클라우드 명령 확인(~10초) + 표본 간격(20초)이 겹치면
    # 한 번에 2분 넘게 빌 수 있다. 건강한 엔진을 죽이는 오탐이 더 나쁘므로 재시작 문턱을 넉넉히 둔다
    # (옛 임시 감시자 watchdog.ps1 의 600/1800초보다는 빠르다). 감시자는 2번 연속 점검에서 멈춰 있어야 끝낸다.
    heartbeat_warn_s: float = 60.0         # 이만큼 멈추면 '주의'(노란불) — 감시자는 기록만 한다
    heartbeat_stale_s: float = 300.0       # 이만큼 멈추면 '멈춤' — 끝내고 플러그 ON 뒤 다시 띄운다
    heartbeat_stale_extract_s: float = 1200.0  # 추출 중 (표본이 멈추고 셀 데이터를 받을 때마다만 심박이 뛴다)
    board_port: int = 8765                 # 결과판 서버 (serve_board.py) 포트
    restart_max_per_h: int = 3             # 1시간에 이만큼 되살렸는데 또 멈추면 더 하지 않고 플러그 ON + 사람 호출
    # 일시 중지 표지(data/supervisor_pause)의 최대 수명. 표지에 until 이 있으면 그것을 따르고, 없거나 읽을 수 없으면 파일을 쓴 시각부터
    # 이만큼 지나면 표지를 무시한다 — 잊고 남긴 표지 하나로 감시자가 영영 조치하지 않는 일을 막는다(검토 F5).
    supervisor_pause_max_h: float = 2.0
    # 엔진 기록(engine.json)이 없을 때 — 감시자 이전 코드로 돌던 엔진(옛 watchdog.ps1 이 띄운 것 등)이 멈췄을 때 —
    # 다시 시작하는 규칙. 옛 감시자와 같다: max(1, engine_cycles_default − cycles.csv 줄 수) 사이클,
    # engine_config_file 이 있으면 --config 로 붙인다(cell-bench 폴더 기준 경로). engine.json 이 있으면 그 기록의 인자를 그대로 쓴다.
    engine_cycles_default: int = 4
    engine_config_file: str = "data/run_config.json"

    # --- 신호등 (cellbench/health.py) — 기본값은 위의 HEALTH. bench.json 에 일부 키만 줘도 나머지는 기본값 ---
    health: dict = field(default_factory=lambda: dict(HEALTH))

    # --- 세트 등록 (결과판 '세트 k 등록' 화면 · serve_board /api/register — 판정은 cellbench/register.py) ---
    # 등록은 bench.json 의 sets 끝에 세트 하나를 붙이는 일이다(add_set). 잘못 묶이면 세트 k 를 끄려다 다른 세트의 전원을 끊으므로
    # (FMEA 8.5) 저장 전에 10초 검사 넷을 본다 — ① 플러그 응답 ② 셀 수신 ③ 다른 세트와 겹침 ④ 플러그 전력(가안, 주의만).
    cells_per_set: int = 24                # 세트 하나의 셀 수. 등록 화면은 정확히 이만큼 골랐을 때만 저장 단추를 켠다
    heard_window_s: float = 30.0           # now.json 의 heard = 어느 세트에도 속하지 않는데 이 안에 들린 셀 (감지 재료)
    register_listen_s: float = 10.0        # 검사 ②: 엔진이 없을 때 셀 라이브를 직접 듣는 시간. 수신으로 치는 나이는 live_gap_alarm_s(15초)
    register_plug_timeout_s: float = 8.0   # 검사 ①: 플러그 읽기 한 번의 상한 (재시도 없이 — 10초 안에 끝나게)
    register_power_min_w: float = 5.0      # 가안 — 검사 ④: 켜짐이고 이 범위면 '24대 Dock 규모'. 벗어나도 주의만 하고 저장은 막지 않는다
    register_power_max_w: float = 120.0    # (Dock 이 1.4 W 로 멈춰 있는 일은 10-10 실측처럼 있을 수 있고, 그건 운전 안전망 P4 의 일이다)
    plug_scan_every_s: float = 600.0       # 감시자가 시험망의 플러그를 찾아 data/plugs.json 에 쓰는 주기 (등록된 플러그에는 접속하지 않는다)

    # --- 클라우드 (Supabase cell-bench · keyring cell-bench-cloud) ---
    cloud_sample_s: float = 60.0           # 표본을 클라우드에 올리는 주기 (로컬 CSV 는 20초 그대로)

    # --- 기록 ---
    data_dir: str = "data"                 # 사이클 CSV · 표본 CSV · 이상 로그 · 추출 파일

    @classmethod
    def load(cls, path: str | Path | None = None, bench_path: str | Path | None = BENCH_FILE) -> "Config":
        """코드 기본값 ← bench.json(있으면) ← path(있으면) 순으로 덮어 Config 를 만든다.

        운전 중인 세트의 단일 원천 규칙: 어느 파일에든 sets 가 있고 serials / plug_mac 이 어느 파일에도
        명시되지 않았으면, 둘을 sets[0](운전 중인 세트)에서 채운다. 그래서 등록 화면은 sets 만 고치면 되고
        serials·plug_mac 을 따로 맞출 필요가 없다. 둘을 명시하면 그 값이 이긴다(한 번의 실행만 다른 셀로 돌릴 때).
        시리얼은 범위 문자열("11733-11756")과 목록 둘 다 받는다(expand_serials).

        알 수 없는 키는 거부한다(KeyError) — 오타 난 설정이 조용히 무시되면 엉뚱한 시험이 돈다.
        bench_path 가 없으면 건너뛰고, path 를 줬는데 없으면 그대로 오류다.
        """
        cfg = cls()
        known = {f.name for f in fields(cls)}
        given: set[str] = set()
        layers = [p for p in (bench_path,) if p and Path(p).exists()] + ([path] if path else [])
        for p in layers:
            data = json.loads(Path(p).read_text(encoding="utf-8-sig"))   # 메모장·PowerShell 이 붙이는 BOM 도 받는다
            if not isinstance(data, dict):
                raise ValueError(f"설정 파일은 JSON 객체여야 한다: {p}")
            for k, v in data.items():
                if k not in known:
                    raise KeyError(f"알 수 없는 설정: {k} ({p})")
                setattr(cfg, k, v)
                given.add(k)
        first = cfg.sets[0] if "sets" in given and isinstance(cfg.sets, list) and cfg.sets else None
        if isinstance(first, dict):
            if "serials" not in given and "serials" in first:
                cfg.serials = first["serials"]
            if "plug_mac" not in given and "plug_mac" in first:
                cfg.plug_mac = first["plug_mac"]
        cfg.serials = expand_serials(cfg.serials)
        return cfg

    def dump(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)

    def stuck_rule(self) -> tuple[float, float, float, int]:
        """Dock 저전력 복구 규칙 (문턱 W, 이어지는 초, 끊어 두는 초, 시도 한도). 옛 이름(precharge_*)을 줬으면 그것이 이긴다."""
        def pick(old, new):
            return new if old is None else old
        return (pick(self.precharge_stuck_w, self.stuck_w), pick(self.precharge_stuck_s, self.stuck_s),
                pick(self.precharge_recover_gap_s, self.stuck_gap_s), int(pick(self.precharge_recover_max, self.stuck_max)))


def validate(cfg: Config) -> list[str]:
    """설정의 문제 목록 (빈 목록 = 문제 없음). 순수 함수 — 장비에 닿지 않는다.

    보는 것: 시리얼 중복 · 세트 사이 시리얼 겹침 · MAC 형식(AA:BB:CC:DD:EE:FF) · 세트 사이 플러그 MAC 겹침 · 세트 id 중복 · 빈 시리얼.
    플러그 MAC 이 두 세트에 있으면 한 세트를 끄려다 다른 세트의 Dock 전원도 끊는다(FMEA 8.5) — 그래서 겹침으로 거부한다.
    문제가 있으면 run_cycle.py 가 시작을 거부하고(engine.json exit=config_error), 감시자는 엔진을 되살리지 않고 알린다.
    """
    problems: list[str] = []
    try:
        running = expand_serials(cfg.serials)
    except ValueError as e:
        problems.append(f"운전 중인 시리얼: {e}")
        running = None
    if running is not None:
        if not running:
            problems.append("운전 중인 시리얼이 비어 있다")
        if _dups(running):
            problems.append(f"운전 중인 시리얼이 중복된다: {_short(_dups(running))}")
    if not _mac_ok(cfg.plug_mac):
        problems.append(f"플러그 MAC 형식이 아니다(AA:BB:CC:DD:EE:FF): {cfg.plug_mac!r}")

    if not isinstance(cfg.sets, list) or not cfg.sets:
        problems.append("세트가 하나도 없다")
        return problems
    ids: list = []
    names: list[str] = []
    owner: dict[int, int] = {}          # 시리얼 → 세트 순번 (id 가 중복돼도 겹침을 놓치지 않게 순번으로 가른다)
    overlap: dict[tuple[int, int], list[int]] = {}
    mac_owner: dict[str, int] = {}      # 플러그 MAC(대문자) → 세트 순번
    for i, s in enumerate(cfg.sets):
        names.append(f"세트 {s.get('id', f'#{i + 1}')}" if isinstance(s, dict) else f"세트 #{i + 1}")
        if not isinstance(s, dict):
            problems.append(f"세트 {i + 1}번째 항목이 객체가 아니다")
            continue
        name = names[i]
        if "id" not in s:
            problems.append(f"{name}: id 가 없다")
        elif s["id"] in ids:
            problems.append(f"세트 id {s['id']} 가 중복된다")
        else:
            ids.append(s["id"])
        if not _mac_ok(s.get("plug_mac")):
            problems.append(f"{name}: 플러그 MAC 형식이 아니다(AA:BB:CC:DD:EE:FF): {s.get('plug_mac')!r}")
        else:
            mac = s["plug_mac"].upper()
            if mac in mac_owner:
                a = mac_owner[mac]
                problems.append(f"{names[a]}(순번 {a + 1}) 와 {name}(순번 {i + 1}) 의 플러그 MAC 이 같다: {mac}")
            else:
                mac_owner[mac] = i
        try:
            ser = expand_serials(s.get("serials", []))
        except ValueError as e:
            problems.append(f"{name}: {e}")
            continue
        if not ser:
            problems.append(f"{name}: 시리얼이 비어 있다")
            continue
        if _dups(ser):
            problems.append(f"{name}: 시리얼이 중복된다: {_short(_dups(ser))}")
        for x in dict.fromkeys(ser):
            if x in owner:
                overlap.setdefault((owner[x], i), []).append(x)
            else:
                owner[x] = i
    for (a, b), xs in overlap.items():
        problems.append(f"{names[a]}(순번 {a + 1}) 와 {names[b]}(순번 {b + 1}) 의 시리얼이 겹친다: {_short(xs)}")
    return problems


def compact_serials(xs) -> str:
    """시리얼 목록을 범위 표기로 줄인다 — [11594 … 11617] → "11594-11617", 끊기면 "1-3, 7". expand_serials 의 반대."""
    xs = sorted(dict.fromkeys(int(x) for x in xs))
    out: list[str] = []
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[j] + 1:
            j += 1
        out.append(str(xs[i]) if i == j else f"{xs[i]}-{xs[j]}")
        i = j + 1
    return ", ".join(out)


def registered_serials(cfg: Config) -> dict[int, object]:
    """등록된 시리얼 → 세트 id. 운전 중인 시리얼(cfg.serials — sets[0] 을 명시로 덮었을 수 있다)도 sets[0] 의 것으로 친다.
    읽을 수 없는 세트 항목은 건너뛴다 — 이 함수는 엔진의 now.json 쓰기에서도 불리므로 예외를 내지 않는다(문제는 validate 가 알린다)."""
    out: dict[int, object] = {}
    sets = cfg.sets if isinstance(cfg.sets, list) else []
    first = sets[0].get("id", 1) if sets and isinstance(sets[0], dict) else 1
    for s in sets:
        if not isinstance(s, dict):
            continue
        try:
            for x in expand_serials(s.get("serials", [])):
                out.setdefault(x, s.get("id"))
        except ValueError:
            continue
    try:
        for x in expand_serials(cfg.serials):
            out.setdefault(x, first)
    except ValueError:
        pass
    return out


def registered_macs(cfg: Config) -> dict[str, object]:
    """등록된 플러그 MAC(대문자) → 세트 id. 운전 중인 플러그(cfg.plug_mac)도 sets[0] 의 것으로 친다. 예외를 내지 않는다."""
    out: dict[str, object] = {}
    sets = cfg.sets if isinstance(cfg.sets, list) else []
    for s in sets:
        if isinstance(s, dict) and isinstance(s.get("plug_mac"), str):
            out.setdefault(s["plug_mac"].upper(), s.get("id"))
    if isinstance(cfg.plug_mac, str):
        out.setdefault(cfg.plug_mac.upper(), sets[0].get("id", 1) if sets and isinstance(sets[0], dict) else 1)
    return out


def next_set_id(sets) -> int:
    """새로 등록할 세트의 id — 지금 있는 정수 id 중 가장 큰 것 + 1 (없으면 1)."""
    ids = [s["id"] for s in (sets or []) if isinstance(s, dict) and isinstance(s.get("id"), int) and not isinstance(s.get("id"), bool)]
    return max(ids, default=0) + 1


def add_set(new: dict, bench_path: str | Path = BENCH_FILE) -> list[dict]:
    """bench.json 의 sets 끝에 세트 하나를 붙이고 원자적으로 쓴다. 돌려주는 것 = 쓴 뒤의 sets.

    지키는 것
      · bench.json 의 다른 키는 그대로 둔다.
      · sets[0](운전 중인 세트)은 바꾸지 않는다 — 언제나 끝에 붙인다. bench.json 에 sets 가 아직 없으면 지금 실제로 쓰는
        세트 목록(코드 기본값 세트 1)을 먼저 적고 그 뒤에 붙인다. 그렇지 않으면 새 세트가 sets[0] 이 되어 엔진이 그 세트로 바뀐다.
      · 쓰기 전에 엔진과 같은 방법(Config.load)으로 다시 읽어 validate 한다 — 문제가 있으면 쓰지 않고 ValueError.
      · 임시 파일에 쓰고 바꿔치기한다 — 엔진·감시자가 읽는 그 순간에도 반쯤 쓴 파일을 보지 않는다.
    """
    p = Path(bench_path)
    data = json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else {}
    if not isinstance(data, dict):
        raise ValueError(f"설정 파일은 JSON 객체여야 한다: {p}")
    base = data.get("sets") if isinstance(data.get("sets"), list) and data.get("sets") else Config.load(None, bench_path=None).sets
    sets = [dict(s) if isinstance(s, dict) else s for s in base] + [dict(new)]
    out = {**data, "sets": sets}
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    try:
        problems = validate(Config.load(None, bench_path=tmp))
        if problems:
            raise ValueError("; ".join(problems))
        for _ in range(10):
            try:
                os.replace(tmp, p)
                break
            except PermissionError:          # Windows — 누가 그 순간 열고 있으면 잠깐 뒤 다시
                time.sleep(0.05)
        else:
            raise OSError(f"{p} 를 바꿔 쓰지 못함 (다른 프로그램이 열고 있다)")
    finally:
        tmp.unlink(missing_ok=True)
    return sets


def _mac_ok(mac) -> bool:
    return isinstance(mac, str) and bool(_MAC.fullmatch(mac))


def _dups(xs: list[int]) -> list[int]:
    seen: set[int] = set()
    return sorted({x for x in xs if x in seen or seen.add(x)})


def _short(xs: list[int], n: int = 8) -> str:
    xs = sorted(xs)
    return ", ".join(map(str, xs[:n])) + (f" 외 {len(xs) - n}개" if len(xs) > n else "")
