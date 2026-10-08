"""시험대 설정 — 한 곳에서만 바꾼다.

값의 근거는 2026-10-07 실측이다(README '실측값' 절). 기준선·만충 조건은 가안이며,
몇 사이클 돌려 본 뒤 조정한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class Config:
    # --- 시험망 (LiveHub FTG-3D93, 인터넷 없음) ---
    pc_ip: str = "192.168.1.100"          # 노트북 Wi-Fi 고정 주소. 셀은 이 주소(ap_ip)로만 라이브를 보낸다
    port: int = 60222                      # 셀 → PC 라이브(UDP) · PC ← 셀 명령(TCP) 공용 포트
    wake_port: int = 9999                  # PC → 셀 깨우기(UDP)
    broadcast: str = "192.168.1.255"

    # --- 셀 ---
    serials: list[int] = field(default_factory=lambda: list(range(11733, 11757)))  # 24대
    live_gap_alarm_s: float = 15.0         # 라이브 신호가 이만큼 끊기면 이상 기록 (평소 0.5초 간격)
    resume_timeout_s: float = 90.0         # 0x26 뒤 측정 복귀를 기다리는 한도
    extract_batch: int = 6                 # 동시 추출 대수 (5GHz 에서 6대 2.88 MB/s 가 가장 효율적)
    delete_after_extract: bool = False     # 0x13 삭제. 사람이 승인한 사이클부터만 켠다

    # --- 스마트 플러그 (Tapo P110M, KLAP) ---
    plug_mac: str = "20:E1:5D:E6:9C:77"    # IP 는 DHCP 라 바뀔 수 있어 MAC 으로 찾는다
    plug_ip_hint: str = "192.168.1.103"    # 마지막으로 본 주소. 먼저 시도하고 안 되면 MAC 으로 재탐색
    keyring_service: str = "cell-bench-tapo"  # Windows 자격 증명 관리자에 저장된 TP-Link 계정 (username/password)
    plug_retries: int = 3
    plug_on_min_w: float = 40.0            # 켠 뒤 이 이상이면 "충전 시작" (24셀 충전 68 W, 바닥 31 W)

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

    # --- 기록 ---
    data_dir: str = "data"                 # 사이클 CSV · 표본 CSV · 이상 로그 · 추출 파일

    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        cfg = cls()
        if path:
            for k, v in json.loads(Path(path).read_text(encoding="utf-8")).items():
                if not hasattr(cfg, k):
                    raise KeyError(f"알 수 없는 설정: {k}")
                setattr(cfg, k, v)
        return cfg

    def dump(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)
