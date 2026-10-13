# 인수인계 — 다른 PC·다른 세션에서 이어 받기

> 한 줄 요지: **코드는 GitHub 에 있지만, "왜 이렇게 만들었고 무엇을 확인했고 무엇이 남았나"는 이 문서에만 있다.** 새 PC 에서 Claude Code 세션을 열면 이 문서를 먼저 읽히고 시작한다. 이전 세션의 대화 기록은 그 PC 에만 남고 옮겨지지 않는다.

구조(무엇이 무엇을 부르고 어떤 순서로 도나)는 [시스템 지도](system-map.md) 에 흐름도로 있다. 이 문서는 그 지도에 없는 결정 · 실측 · 사고를 맡는다.

기준 시각: 2026-10-08 16:30 — **제어 PC 가 TestPC(전용 Windows 노트북)로 옮겨진 뒤.** 그 뒤의 일은 TestPC 의 `data/run.log` 와 PR #1 커밋 로그가 정본이다.

## 1. 한 장 요약

- **무엇을:** 셀(EPTS, 24대) 배터리 내구 시험을 사람 없이 돌리는 1층 플랫폼. 셀을 켠 채 스마트 플러그로 Dock 충전을 켜고 끄며 방전 → 추출 → 충전 사이클을 반복하고, 사이클마다 작동시간·충전 Wh 를 남긴다.
- **어디까지:** 프로그램(`cell-bench/`)이 1사이클을 끝까지 돌렸고(방전·추출 성공), 밤새 멈춘 사고 두 가지를 고쳤으며, 결과판·원격 제어·알림이 붙어 있다. 10-08 16시 TestPC 에서 `--precharge --cycles 4` 로 시작했고, 19:06 세트 1 플러그가 실수로 켜져 사이클 1 을 중단하고 19:18 에 처음부터 다시 시작했으며, 19:43 에 클라우드 전송(Supabase cell-bench · 결과판 https://cell-bench-board.vercel.app)을 켜느라 한 번 더 다시 시작했다. 정식 사이클(100%→30%→만충)이 끝까지 돈 기록은 아직 없다.
- **다음:** ① 정식 사이클 결과 확인 → PR #1 검토대기 ② PC↔LiveHub 유선화 ③ Tailscale·PIN 설정 ④ 심박 시뮬레이터(보드 도착 후) ⑤ 2층(온습도·비교군·합격선·스웰링).

## 2. 장비와 망 — 그림 한 장

```
TestPC(Windows 노트북 · USB-A 1 · USB-C 1 · 유선 포트 없음 · 충전 별도 단자)
  ├─ USB-A ── Realtek USB 이더넷 "이더넷 3" (192.168.8.174, 메트릭 10) ──▶ ipTIME V504 ──▶ 사무실 공유기 ──▶ 인터넷
  └─ Wi-Fi "Wi-Fi" 고정 192.168.1.100 (게이트웨이 없음) ──▶ LiveHub FTG-3D93-5G
       └─ 내일: USB-C 허브(유니콘 CLAN-1000HC)의 이더넷으로 바꾸고 Wi-Fi 는 끈다
LiveHub FTG-3D93 (192.168.1.1 · WAN 비움 · 인터넷 없음)
                                               ├─ 2.4GHz ─ 셀 24대 (.114~.242, DHCP)
                                               └─ 2.4GHz ─ 스마트 플러그 Tapo P110M (.103, MAC 20:E1:5D:E6:9C:77)
멀티탭 ─ 스마트 플러그 ─ 100W 어댑터 ─USB-C─ Dock DKP2 ─ 셀 24대
```

- PC 는 반드시 **192.168.1.100** 이어야 한다. 셀은 이 주소로만 라이브(UDP 60222)를 보낸다. **이 주소는 한 번에 한 대만** LiveHub 에 붙는다.
- Wi-Fi 프로필: FTG-3D93-5G 자동 · FTG-3D93(2.4G) 자동(예비) · Fitogether 1 수동. 고정 주소는 Wi-Fi 어댑터 전체에 걸리므로 다른 Wi-Fi 에 붙으면 인터넷이 안 된다 — 사무실 Wi-Fi 를 쓰려면 먼저 DHCP 로 되돌린다.
- Dock 의 USB-C 는 하나뿐이라 어댑터 전용. PC 와 Dock 은 선으로 잇지 않는다.
- 플러그는 Tapo 앱 **Third-Party Compatibility 켜짐** 상태여야 python-kasa 가 말을 건다(KLAP). 시험망에 인터넷이 없어 Tapo 앱 원격 제어는 안 된다.
- LiveHub 관리자(192.168.1.1)는 사람이 로그인한다. 사무실 TP-Link 공유기(192.168.0.1)는 우리 것이 아니라 손대지 않는다.

## 3. 실측으로 확정한 것 (설정값의 근거)

| 항목 | 값 | 날짜 |
|---|---|---|
| 셀 라이브 | 1320B 0x09 초당 2회 + 9B 0x16 초당 1회. 배터리 @52, 심박 @51, 상태 @53, RSSI @50 | 10-06 |
| 깨우기·명령 | UDP 9999 로 0x10 → 셀이 PC TCP 60222 로 접속 → 0x11 상태 → 0x12 업로드(4104B 블록, unsigned 합) → 0x26 복귀(17~18초) | 10-06 |
| 재부팅 | **0x15 는 TCP 대기 모드로 켜져 측정 안 함. 재부팅은 항상 0x26** | 10-07 |
| 추출 속도 | 1대 0.50 MB/s · 6대 동시 5GHz 2.88 · 24대 동시 2.41 → 기본 6대씩 | 10-06 |
| 데이터 증가 | 3.45 MB/h (24대 3.44~3.54), 6h 사이클 셀당 20.7 MB | 10-07 |
| 방전 속도 | 19~21 %/h → 100→30% 환산 3.4~3.7h | 10-07 |
| 충전 전력 | 24대 68 W(~78%) · 85.7 W 로 한 번 뜀 · 만충 뒤 **바닥 31 W**(셀 작동분) · 82→100% 44분 | 10-07 |
| 전원 끊김 | 플러그 OFF 10초 ×3 에 셀 24대 측정 유지 | 10-07 |
| Dock 동작 | 만충 뒤 충전을 멈추고 **다시 시작하지 않는다** → 플러그를 껐다 켜야 재충전 | 10-07 |
| 심박 시뮬 | PC 블루투스를 표준 심박대(0x180D)로 광고 → 셀에 0x20 으로 이름 → 60~159 bpm 수신 성공 | 10-07 |
| 셀 기종 표기 | 셀은 "CLBY4B" 로 보고(사용자 말: CLBX6) — 미확인 | 10-06 |

## 4. 사고와 교훈 (같은 일을 반복하지 않기 위해)

| 날짜 | 일어난 일 | 원인 | 지금의 대책 |
|---|---|---|---|
| 10-06 | PC 네트워크를 바꾸자 Claude 연결이 끊김 | PC IP·SSID 를 세션이 바꿈 | **PC 네트워크는 명령을 보여 주고 "예"를 받은 뒤에만** |
| 10-07 14:35 | 블루투스 광고 시작 직후 Wi-Fi 끊김 | 인텔 Wi-Fi·BT 겸용 칩 간섭 추정 | 심박 시뮬은 PC 가 아닌 ESP32 보드로 · PC↔LiveHub 유선화 |
| 10-07 18:19 | 충전 중 시험 프로그램 종료 | 결과판이 now.json 을 읽는 순간 덮어쓰기 거부(PermissionError) | 재시도 + 화면용 파일 오류는 삼킴 + 감독 루프 |
| 10-07 22:43 | Wi-Fi 끊긴 뒤 자동 재연결 안 됨 → 셀 24대 밤새 방전·꺼짐 | 프로필 수동 연결 + 끊김 감시 없음 | 자동 연결 + 셀 전체 끊김 시 재연결·5분이면 사이클 중단·**플러그 ON 으로 대기** |
| 10-08 | 셀이 꺼지면 원격으로 켤 방법이 없음 | Dock 버튼만이 켜기 수단 | 멈출 때는 항상 충전 쪽 · 30분 지나면 "Dock 버튼 필요" 알림 |
| 10-08 14:25 | 옛 노트북 Wi-Fi 를 사무실 망으로 옮기자 인터넷 불가 · 2분 뒤 프로그램이 LiveHub 로 도로 연결 | 고정 주소가 Wi-Fi 어댑터 전체에 걸림 + 재연결 장치가 사람의 의도적 이동을 구분 못 함 | 옮길 때는 먼저 안전 정지 → DHCP 복원 → 자동 연결 끄기 순서 (아래 8절) |
| 10-08 | 만충 뒤 멈춘 Dock 은 플러그가 켜진 채로는 재충전 안 함 | Dock 동작 | 안전 상태 = `Plug.recharge()`(OFF→10초→ON) · 시작 시 `--precharge` |

## 4-1. 제품 사실(배터리 사양·BOM)을 읽을 때

Fitstack 의 라이브 DB 는 **자체 호스팅 Supabase(https://fitstack-api.fitogether.com)** 이고, 조회 통로는 Fitstack 저장소의 `sh selfhost/query.sh "<select>"` 뿐이다(AWS CLI 자격증명 필요). Supabase 클라우드 `hardware-team-system`(ikpzndop…)과 Google Drive `HTS/Items` 는 **은퇴한 사본**이라 읽어도 된다는 보장이 없다 — 2026-10-08 에 두 곳 모두를 정본인 줄 알고 읽었다가 정정했다. 이 저장소에서는 Fitstack CLAUDE.md 가 로드되지 않으므로, 제품 사실이 필요하면 **Fitstack 저장소 세션에서 조회하거나 Fitstack 웹(https://fitstack.fitogether.com)에서 확인**한다. 그 결과 셀 모델별 배터리(CLBX-6B→500 mAh, CLBY-4B→750 mAh)와 온도 한계(충전 10~45 ℃ 등)는 **재검증 전**이다.

## 5. 결정된 것 · 아직 결정 안 된 것

**결정됨(사용자):** 범위는 정적 시험·셀 배터리 내구 / 1층 자동화 먼저 / 셀을 끄지 않고 켠 채 사이클 / 플러그는 P110M / 기준선 30% / 삭제(0x13)는 승인 전까지 끔 → **10-10 변경: 셀 저장 상한 ≈160 MiB 실측(가득 차면 측정·라이브 송신이 멈추고 0x26 으로도 안 돌아옴)으로 사이클마다 추출 뒤 삭제를 켬**(끝 표지까지 받았고 오류 블록 0 이며 받은 바이트 ≥ 크기인 셀만. 코드 기본값과 TestPC `data/run_config.json` 이 같은 값) / 심박 시뮬은 ESP32-C3 보드 25개(검증 세트 5개 먼저) / PC↔LiveHub 유선화 / 원격 모니터링·제어까지 만든다 / 제어 PC 를 전용 노트북으로 옮긴다.

**미결:** Claude Code Remote Control(로컬 세션을 휴대폰에서 쓰기) — 회사 정책으로 막혀 승인 요청 중(10-08). 그 전까지 Claude 작업은 제어 PC 앞에서 / Tailscale 계정(개인·회사) / Slack 알림 채널 / 플러그 TP-Link 계정 소유 / 시험 기록 보관 위치 / 저장소를 팀 조직으로 옮길지 / 판정 기준(정상 ±5%, 주의 3사이클, 점검 2회)은 가안 / 2층 전부 / 셀 기종 표기.

## 6. 손에 쥔 것

| 무엇 | 어디 |
|---|---|
| 코드·문서 | GitHub `GeonwooKim-fitogether/QA-Automation-Test-Room` 브랜치 `feat/cell-bench-controller`, PR #1(Draft) |
| 시험 기록 | TestPC `cell-bench/data/` (git 제외, 10-08 19:43 재시작부터 사이클 1 · 그 전 16시·19:18 시작분은 같은 폴더에 섞여 있음). 옛 노트북 기록(10-07~08 사이클 1·2, 사고·중단 기록)은 옛 노트북 바탕화면 `cell-bench-data-20261008-1511.zip` — 넣더라도 `data_old_laptop/` 에만 |
| 추출한 셀 파일 | `cell-bench/data/ftg/<사이클>/` · 10-06~07 분은 옛 PC scratchpad |
| 설계 문서(아티팩트) | v2 도해 https://claude.ai/artifact/WmERr1iaUZRpBXqbWUAAb3 · CEO 브리프 https://claude.ai/artifact/3LUoc89nKgaDB7pEuTaXpy · 결과판 시안 https://claude.ai/artifact/QR8sxwZvygmFDmrUrjdevF · 24셀 배선도 https://claude.ai/artifact/8iD1baT48asThPwS5kC8mp |
| 펌웨어 원본 | Google Drive `G:\공유 드라이브\HTS\Items\SWFW0-0001`(Dock) · 셀 `cell-y4-ESP32-S3-firmware-master.zip` · iOS Live 앱 `fitogether-live-ios-main.zip` (사용자 Downloads) |
| 비밀값 | Windows 자격 증명 관리자: `cell-bench-tapo`(username/password, TestPC 에 저장됨) · `cell-bench-remote`(pin/slack_webhook, 아직 없음). **PC 마다 다시 넣는다** |

## 7. 새 PC 에서 시작하는 순서

1. Claude Code 데스크톱 설치 · 같은 계정 로그인. GitHub CLI `gh auth login`.
2. `git clone https://github.com/GeonwooKim-fitogether/QA-Automation-Test-Room.git` → 폴더를 Claude Code 로 연다. `.claude/` 규칙은 저장소에 있어 그대로 적용된다.
3. `git checkout feat/cell-bench-controller` (PR 머지 전이면).
4. `cell-bench/docs/control-pc-setup.md` 의 다섯 가지 + `pip install -r requirements.txt` → `python tools/check_env.py` 전부 ✓.
5. 원격이 필요하면 `docs/remote-access.md`.
6. 세션 환경은 **로컬(이 컴퓨터)** 로 연다. 클라우드 세션은 Anthropic 서버에서 돌아 LiveHub·셀·플러그에 닿지 못한다.
7. 새 Claude 세션의 첫 메시지:
   > `cell-bench/docs/handover.md` 를 읽고, 지금 `data/run.log` 상태를 확인한 뒤 이어서 하자.

## 8. 제어 PC 를 옮기는 순서 (10-08 에 실제로 쓴 것)

두 PC 모두 인터넷이 한순간도 끊기지 않게 하는 것이 핵심이다. 각 PC 의 Claude 세션은 인터넷이 끊기면 대화가 끊긴다.

| | 어디서 | 할 일 | 끝났다는 증거 |
|---|---|---|---|
| T1 | 새 PC 세션 | 절전 안 함 · 덮개 아무 것도 안 함 · 방화벽 python 허용 · 플러그 계정 입력 | 네 가지 모두 됨 |
| T2 | 새 PC 세션 | `git pull` · `python -m pytest -q` | 검사 전부 통과 |
| T3 | 새 PC 세션 | `check_env.py` | LiveHub 관련 4개만 ✗ |
| T4 | 옛 PC 세션 | 시험 안전 정지(`stop_safe`) | 플러그 ON · 충전 W |
| T5 | 옛 PC 세션 | 기록 zip (CSV·로그) | 바탕화면·Drive |
| T6 | 옛 PC 세션 | Wi-Fi DHCP 복원(관리자) · LiveHub 프로필 수동 · 사무실 Wi-Fi 연결 | Wi-Fi 만으로 인터넷 됨 |
| T7 | 사람 | 유선 어댑터+랜선을 새 PC 로 | 새 PC 에 유선 인식 |
| T8 | 새 PC 세션 | 유선 주소·게이트웨이·github 443 (읽기만) | 유선으로 인터넷 됨 |
| T9 | 새 PC 세션 | Wi-Fi 고정 192.168.1.100(게이트웨이 없음) · 이더넷 메트릭 10 | 기본 경로 = 유선 하나 |
| T10 | 사람 | Wi-Fi 메뉴에서 FTG-3D93-5G 연결(비밀번호 · 자동 연결) | 연결됨 · 세션 안 끊김 |
| T11 | 새 PC 세션 | 사무실 Wi-Fi 프로필 수동 | 프로필 모드 확인 |
| T12 | 새 PC 세션 | `check_env.py` 전부 ✓ · `battery_table.py` 24대 · `plug_cli.py status` | 셀·플러그 보임 |
| T13 | (선택) | 옛 기록은 `data_old_laptop/` 에만 | — |
| T14 | 새 PC 세션 | `serve_board.py` · `run_cycle.py --precharge --cycles 4` 를 별도 프로세스로 | run.log "예비 충전 시작" |
| T15 | 옛 PC 세션 | LiveHub Wi-Fi 프로필 삭제 · 남은 프로세스 없음 (자격 증명은 사용자 선택) | 옛 PC 개인용 |
| T16 | — | 이 문서 갱신 · 커밋 | PR 에 반영 |

T9 가 T10 보다 먼저인 이유: 고정 주소 없이 LiveHub 에 붙으면 LiveHub 가 게이트웨이(192.168.1.1)를 주고, Windows 가 인터넷 없는 그쪽을 기본 경로로 고를 수 있다(10-06 사고).

## 9. 무인 운전 안전망(PR #3, 10-10) 적용 순서

PR #3(`feat/bench-supervisor`)은 FMEA 묶음 P1~P4, 신호등, 세트 등록 화면을 더한다. 바뀌는 것은 코드뿐이고, TestPC 의 설정·작업 스케줄러·클라우드 DB 는 그대로다. 머지 뒤 TestPC 세션이 아래 순서로 적용한다. 어느 단계를 건너뛰어도 위험해지지 않는다. 옛 엔진은 새 감시자가 지켜보기만 하고, 클라우드 표가 없으면 결과판은 "신호등을 읽지 못함"만 띄운다.

| 순서 | 어디서 | 할 일 | 끝났다는 증거 |
|---|---|---|---|
| 1 | TestPC 원래 checkout | `git pull` 뒤 `python -m pytest -q` | 검사 전부 통과 |
| 2 | TestPC, 사이클 경계 | `powershell -ExecutionPolicy Bypass -File tools\restart_at_boundary.ps1 -OldPid <엔진 pid>`. 교체하는 동안 감시자 일시 중지 표지를 두고 `data/run_config.json` 을 이어 붙인다 | run.log 에 새 엔진 시작, `data/engine.json` 이 생기고 now.json 에 `beat`·`metrics` |
| 3 | TestPC, 관리자 PowerShell | `tools\install.ps1 -WhatIf` 로 바뀔 것을 본 뒤 `-WhatIf` 없이 적용(업데이트 재시작 금지, 절전, 블루투스, 어댑터 절전, ARSO, 방화벽, 감시자 등록). 감시자 등록이 옛 임시 작업 `cell-bench watchdog` 을 사용 안 함으로 바꾼다 | `python tools\check_env.py` 에 ✗ 없음, `data\supervisor.json` 이 1분마다 갱신 |
| 4 | TestPC | Slack 웹훅은 **입력하지 않아도 된다** — 새 감시자·엔진이 클라우드 Vault 의 `cell_bench_slack_webhook` 을 받아 쓴다(마이그레이션 `20261010061544_slack_webhook_rpc.sql` 적용 뒤, docs/remote-access.md 7절). 웹훅 입력은 선택(PC 만 다른 채널로 보낼 때). 결과판 원격 명령의 PIN 은 현장에서 `python tools\remote_setup.py` 로 넣는다 | 5분 안에 Slack 에 "감시자 시작 · 알림 시험", `data\supervisor.json` 의 `slack_from` 이 `cloud` |
| 5 | Supabase cell-bench | 마이그레이션 `20261009235950_heartbeat_watch.sql` 과 `20261010003538_bench_health.sql` — **10-10 사용자 승인 뒤 적용 완료**(pg_cron·pg_net 설치, 예약 작업 `cell-bench-heartbeat` 1분마다). Vault 의 웹훅(`cell_bench_slack_webhook`)도 10-10 에 넣었다(클라우드 시험 전송 ok). 남은 것은 `20261010061544_slack_webhook_rpc.sql`(PC 가 그 웹훅을 받아 가는 함수) — **실환경 적용 대기, 사용자 승인 필요** | `bench_watch` 에 hq-bench-1 행, 새 감시자가 돌면 `bench_health` 에도 |
| 6 | 결과판 | 로컬 http://127.0.0.1:8765 과 클라우드 https://cell-bench-board.vercel.app 의 맨 위 | 시험대 띠에 불과 이유, 차선 9개. 클라우드는 5분 묵으면 "미확인" |

아직 사람이 정해야 하는 것: ARSO 항상 모드(이 PC 는 BitLocker 가 꺼져 있어 기본 모드로는 업데이트 재시작 뒤 자동 로그온이 되지 않는다), 정전 뒤 자동 로그온, Slack 채널, UPS. 세트 2 는 등록 화면으로 등록할 수 있고, 다중 세트 운전 기능은 `feat/multi-set-bench`(PR #5)에 구현돼 있다 — 다만 관문(U1 · U2)이 닫혀 있어 **운전은 세트 1 만 한다**(10절).

### 9-1. 시험대가 놀지 않게 (PR #8, 10-11) — 시험 목표 500사이클

10-11 에 두 번 멈춰 섰다. 12:20 사이클 4 가 끝나자 엔진이 `exit=done` 으로 멈췄고(감시자는 다 돈 엔진을 되살리지 않는 설계) 16:42 에 사람이 새 시험을
띄울 때까지 4시간 21분 공회전했다. 16:42 `--precharge --cycles 5` 로 띄우자 셀 96~97% 에서 Dock 이 다시 충전하지 않아(27.7 W 평탄) '모든 셀 100%'
조건이 영영 오지 않았고, 그 2시간 39분 동안 신호등 9차선이 전부 '정상'이었다. 19:21 사람이 원격 '플러그 끄기'를 눌러 겨우 사이클 5 를 시작했다.

고친 것 셋(코드만 바뀌고 TestPC 설정 · 작업 스케줄러 · 클라우드 DB 는 그대로다):

| 무엇 | 어떻게 | 설정(`cellbench/config.py`, `bench.json` 으로 덮어쓴다) |
|---|---|---|
| 자동 이어 가기 | 시험 전체 목표는 **500사이클(약 6시간/사이클 → 약 4개월)**. 엔진은 `cycles.csv` 누적이 목표에 닿을 때까지 사이클을 이어 가고, 닿으면 그때만 `exit=done`(플러그 ON). `--cycles N` 은 '이번 실행 최소 N'. 멈추는 길은 '안전 정지' · Ctrl+C · 연속 실패뿐. 누적 100마다 Slack "목표 500 중 100사이클 완료 · 누적 방전 평균 Nh". 감시자는 남은 사이클 = 목표 − 누적으로 다시 띄우고, `done` 인데 누적 < 목표면(옛 코드 엔진) 되살린다 | `auto_continue` true · `target_cycles` 500 · `progress_notify_every` 100 |
| 예비 충전 조건 | 시작 때 모든 셀 ≥ 95% 면 건너뛴다. 전력이 바닥(≤ 35 W)에서 10분 평탄하면 모든 셀 ≥ 95% 는 만충으로 치고, 미만이면 `dock_no_recharge`(노랑)를 남기고 30초 끊었다 켠 뒤 그래도 같으면 사이클로. 정규 충전의 만충(모든 셀 100%)은 그대로 | `precharge_skip_pct` 95 · `floor_w_max` 35 |
| 진척 없음 경고 | '프로그램' 차선 — 단계 기대 시간(PRECHARGE 1h + (100 − 시작 최저%)/20 h · DISCHARGE 5h · PLUG_ON 0.1h · EXTRACT 0.5h · CHARGE 는 `charge_timeout_h` · RECOVER 0.5h)의 1.5배에 노랑 + Slack 1건, 3배(예비 충전 4배)에 빨강 | `HEALTH["dwell_expect_h"]` · `dwell_factor` 1.5 · `dwell_alarm_factor` 3 · `dwell_alarm_factor_by_phase` · `precharge_pct_per_h` 20 |

적용 순서는 9절과 같다 — TestPC 에서 `git pull` → `pytest` → 사이클 경계에서 `tools\restart_at_boundary.ps1` 로 엔진 교체(감시자 · 결과판 서버도 새 코드로).
**주의:** 지금 돌고 있는 옛 코드 엔진이 `--cycles` 를 다 돌고 `done` 으로 끝나면, 새 코드의 감시자가 그것을 "누적 < 목표" 로 보고 플러그 ON 뒤 남은 사이클로 다시 띄운다 — 이것이 의도다. 예전처럼 `--cycles` 만 돌고 멈추게 하려면 `bench.json` 에 `"auto_continue": false`.

보는 법 — **어디서:** 결과판 관제 탭 '프로그램' 차선 · `data/run.log` · `data/events.csv`. **무엇을 하면:** 한 사이클이 끝나는 것을 본다 / 셀이 95% 이상인 채 `--precharge` 로 띄운다 / 한 단계를 기대의 1.5배 넘게 둔다.
**무엇이 보이나:** 엔진이 끝나지 않고 다음 사이클을 시작하고 차선 값에 "누적 N / 목표 500" / `run.log` 에 "예비 충전 건너뜀" 또는 "만충으로 친다" / 차선이 노랑 "… 단계 체류가 기대(1시간)의 1.5배를 넘김 — 진척 없음" 과 Slack 1건.

## 10. 다중 세트 운전 (PR #5) — 배포 순서와 사람이 정할 것

설계 · 근거 · 회귀 검사는 [다중 세트 설계 명세서](design/multi-set-plan.md), 쓰는 법은 README 의 '다중 세트 운전' 절.

**한 줄 요지.** 새 코드를 넣어도 세트 1 만 등록된 지금은 지금과 같게 돈다(골든 기록 검사가 못 박는다). 고장일 때만 발동하는 안전 장치(방향 대조 ·
방전 7시간 한도)는 단일 모드에도 들어간다. 세트 2 는 아래 순서의 5번까지 끝나야 돈다.

| 순서 | 무엇 | 누가 | 확인 |
|---|---|---|---|
| 1 | PR #5 의 코드를 TestPC 에 들인다 — 엔진은 `tools\restart_at_boundary.ps1` 로 세트 1 사이클 경계에서, 감시자 · 결과판 서버도 새 코드로 | 사람 (TestPC) | `data\now.json` 에 `cloud_schema: 2`, 단일 모드 그대로 |
| 2 | 새 코드가 **단일 모드로 한 사이클을 끝까지** 돈다 | — | cycles.csv 한 줄 · 이상에 `pair_mismatch` · `discharge_timeout` 없음 |
| 3 | PR 머지 → 클라우드 결과판 배포(Vercel) | 사람 | 클라우드 결과판이 지금처럼 보인다 |
| 4 | **U1** — 마이그레이션 `20261010174625_bench_multi_set.sql` 적용 (SQL 전문을 보고 승인) | 사용자 | 적용 전 `bench_state` payload 의 `cloud_schema` = 2, 적용 뒤 `bench_state` 에 set_id = 1 한 줄 |
| 5 | **U2** — 세트 2 보정 실측(엔진 안 '기록 전용 보정', 약 2.5시간) → 결과로 짝 문턱을 정해 `PAIR["calibrated"] = True` 커밋 | 사용자 · Claude | `data\set2\calib_*.csv` |
| 6 | 세트 2 짝 검증(판정) → 통과 → 결과판에서 '운전 켜기'(PIN) | 사람 | `data\set2\pair.json` state=passed |
| 7 | 엔진이 세트 1 의 다음 만충 경계에서 스스로 다시 시작해 다중 모드로 돈다 | — | `engine.json` 의 `mode: multi` · 결과판에 세트 2 운전 카드 |

**자동 합류 (PR #10).** 결과판 세트 2 화면의 '자동 합류 시작'(PIN 한 번)이 보정 기록 → 문턱 자동 도출 → 짝 검증 → 운전 켜기 → 다음 경계 합류를 잇는다(README '자동 합류'). 위 5번(U2 보정 실측 후 `PAIR["calibrated"]` 커밋)은 필요 없다 — 문턱은 세트별로 `data\set2\pair_thresholds.json` 에 남는다. **다만 4번(U1 마이그레이션)은 여전히 사람 몫이다** — U1 이 닫혀 있으면 자동 합류는 짝 검증까지 통과한 뒤 '운전 관문 닫힘'으로 멈추고, U1 적용 뒤 다시 누르면 운전 켜기부터 이어 간다. 감시자는 엔진이 도는 동안 운전하지 않는 등록 세트의 플러그를 10분마다 지키고 셀이 15분 안 들리면 Slack 으로 알린다(`set_silent`). 배포는 엔진 · 감시자 · 결과판 서버를 함께 바꾼다.

**충전 중 교체 (PR #10).** `tools\restart_at_boundary.ps1 -OldPid <pid> -InCharge` — now.json 단계가 CHARGE 일 때만 즉시 교체(다른 단계는 거부). 새 엔진이 cycles.csv 에 복구 행(`note=restart_in_charge`)을 쓰고 사이클 번호를 +1 로 이어 세며 `--precharge` 로 충전을 이어 간다.

**사용자가 정할 것 (U1 ~ U6, 답이 없는 동안 세트 2 는 돌지 않는다):** U1 마이그레이션 적용 · U2 보정 실측 일정과 세트 2 장비 연결 ·
U3 챔버 온도 프로파일(충전 10 ~ 45 ℃ 밖이면 '플러그 ON = 안전' 전제가 바뀐다) · U4 품질 기준 종료(기본: 두지 않고 기록만) ·
U5 P110M 정전 복구 동작 · UPS · 점검 모드 · U6 라이브 state 바이트가 충전 중을 알리는지(펌웨어).
