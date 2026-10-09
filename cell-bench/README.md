# cell-bench — 셀 내구 시험대 무인 충방전 제어

> 한 줄 요지: **셀 24대를 켜 둔 채, 스마트 플러그 하나로 Dock 충전을 켜고 끄며 "방전 → 추출 → 충전" 사이클을 사람 없이 반복하고, 사이클마다 작동시간·충전 전력량을 CSV 로 남긴다.** 셀 펌웨어는 손대지 않는다.

```
방전(플러그 OFF) ─▶ 기준선 30% ─▶ 플러그 ON ─▶ 추출(충전 중, 6대씩) ─▶ 만충 판정 ─▶ 플러그 OFF ─▶ (반복)
```

## 구성

| 파일 | 역할 |
|---|---|
| `cellbench/config.py` | 설정 한 곳 (시리얼 24개 · 기준선 · 만충 조건 · 플러그 MAC · 감시자 문턱). 불러오는 순서 = 코드 기본값 ← `bench.json` ← `--config` |
| `bench.example.json` | 시험대 설정 파일 견본. 실제 `bench.json` 은 등록 화면이 운전 중에 고치므로 git 에 올리지 않는다 |
| `cellbench/protocol.py` | 셀 명령 프레임·라이브 메시지·업로드 블록 (iOS Live 앱과 같은 틀) |
| `cellbench/cells.py` | `LiveListener`(UDP 60222 수신기 하나) · `CellLink`(깨우기 → 상태 → 추출 → 0x26 복귀) |
| `cellbench/plug.py` | Tapo P110M 켜기·끄기·전력 (python-kasa · KLAP · 계정은 Windows 자격 증명 관리자) |
| `cellbench/cycle.py` | 사이클 상태기계 + 순수 판정 함수(`discharge_done` · `is_full` · `integrate_wh`) |
| `cellbench/record.py` | `data/` 아래 CSV 기록 |
| `run_cycle.py` | 실행 진입점. 시작·끝을 `data/engine.json` 에 남기고(감시자가 읽음), 다 돌면 플러그를 켜 둔 채 끝낸다 |
| `supervise.py` · `cellbench/supervisor.py` · `cellbench/proc.py` | 감시자 — 엔진 밖에서 죽음·멈춤을 잡아 플러그 ON 뒤 다시 띄운다 (아래 '감시자') |
| `serve_board.py` · `board/index.html` | 결과판 (운영·추이·구조). `data/` 를 10초마다 읽어 보여 줌. 셀·플러그에 명령하지 않음 |
| `cellbench/control.py` · `cellbench/alert.py` · `cellbench/cloud.py` | 원격 명령(파일 전달 · PIN) · Slack 알림 · Supabase 전송(상태·표본·사이클·이상·명령) |
| `tools/` | 수동 도구: `install_supervisor.ps1`(감시자를 작업 스케줄러에 등록) · `restart_at_boundary.ps1`(사이클 경계에서 새 코드로 교체) · `check_env.py`(제어 PC 점검) · `remote_setup.py`(PIN·웹훅) · `cloud_setup.py`(Supabase 키) · `build_cloud_board.py`(배포 폴더) · `battery_table.py` · `plug_cli.py` · `snapshot.py` |
| `tests/` | 장비 없이 도는 단위 검사 |

## 준비 (한 번)

> 새 노트북으로 옮길 때는 [제어 PC 준비 · 이식](docs/control-pc-setup.md) 을 따르고 `python tools/check_env.py` 로 확인한다.
> 휴대폰에서 보고 제어하려면 [원격 모니터링 · 제어](docs/remote-access.md).
> 다른 PC 나 새 Claude 세션에서 이어 받을 때는 [인수인계](docs/handover.md) 를 먼저 읽는다 — 코드에 없는 결정·실측·사고가 거기 있다.

1. PC Wi-Fi 를 LiveHub `FTG-3D93-5G` 에 고정 주소 `192.168.1.100` 으로 붙인다(셀은 이 주소로만 라이브를 보낸다). 인터넷은 이더넷으로.
2. `pip install -r requirements.txt`
3. 플러그는 Tapo 앱에서 LiveHub 2.4 GHz 에 등록하고 **나 → Tapo Lab → Third-Party Compatibility** 를 켠다.
4. `python tools/plug_cli.py setup` — 입력 창에 TP-Link 계정을 넣는다. 값은 자격 증명 관리자에만 저장된다.

## 실행

```bash
cd cell-bench
python -m pytest -q                      # 장비 없이 판정 로직 검사
python tools/battery_table.py            # 셀 24대 배터리 표 (10초, 셀 안 건드림)
python tools/plug_cli.py status          # 플러그 상태·전력
python run_cycle.py --dry-run            # 플러그·셀 안 건드리고 흐름만
python run_cycle.py                      # 1사이클 무인 실행
python serve_board.py                    # 결과판 → 브라우저로 http://127.0.0.1:8765
```

## 감시자 (엔진 밖에서 되살리기)

시험 프로그램이 사라지거나(업데이트 재시작·정전·예외·창 닫힘) 멈추면(행) 아무도 모르던 것을 막는다. 작업 스케줄러 작업
**"CellBench Supervisor"** 가 로그온 때 `pythonw supervise.py` 를 창 없이 띄우고, 감시자는 1분마다 시험망 · 결과판 서버 · 엔진을 점검한다.
엔진이 이유 없이 사라졌거나 심박(`now.json` 의 `beat`)이 5분(추출 중 20분) 넘게 두 번 연속 멈춰 있으면, **플러그를 먼저 켜고**
남은 사이클 수로 `run_cycle.py --precharge` 를 처음과 같은 인자(`--config` 등)로 다시 띄운다. 1시간에 3번을 넘기면 멈추고 Slack 으로 사람을 부른다.

- **등록(한 번):** `powershell -ExecutionPolicy Bypass -File tools\install_supervisor.ps1` — 관리자 권한 필요 없음. 옛 임시 감시자(`cell-bench watchdog`)가 있으면 사용 안 함으로 바꾸고 그 프로세스를 끝낸다. 되돌리기는 `-Uninstall`.
- **보는 법:** `data/supervisor.json` 의 `t` 가 1분마다 바뀌고 `checks`(시험망·결과판·엔진·심박)가 보인다. 조치와 상태 변화는 `data/supervisor.log` 에 한 줄씩.
- **되살리지 않는 것:** 다 돈 엔진, 결과판의 '안전 정지', Ctrl+C, 설정 오류, 연속 실패로 스스로 멈춘 엔진.
- **일부러 멈출 때:** 감시자가 띄운 엔진은 창이 없다. 결과판의 '안전 정지'를 쓴다. 작업 관리자에서 끝내면 감시자는 비정상 종료로 보고 되살린다.
- **코드 교체·이관 동안:** `data/supervisor_pause` 파일을 두면 감시자는 점검만 하고 조치하지 않는다. 내용은 `{"reason": "이관", "until": <epoch 초>}` (until 이 지나면 무시, 없으면 지울 때까지). `tools/restart_at_boundary.ps1` 은 교체하는 동안 이 표지를 스스로 둔다.
- **주의:** 로그온해야 감시자가 뜬다. 재부팅 뒤 자동 로그온이 켜져 있어야 사람 없이 복귀한다(`tools/check_env.py` 로 확인).

## 보는 법

- **어디서:** `cell-bench/data/`
- **무엇을 하면:** `run_cycle.py` 를 돌린다
- **결과판:** `python serve_board.py` 후 http://127.0.0.1:8765 — 운영 탭에 지금 단계·다음 단계 예상 시각·셀 24칸·전력/배터리 그래프, 추이 탭에 셀별 추정 작동시간과 사이클 표
- **무엇이 보이나:** `cycles.csv` 에 사이클 1줄(방전 시간·충전 분·충전 Wh·추출 결과), `samples_0001.csv` 에 20초마다 전력·배터리, `events.csv` 에 이상, `ftg/0001/` 에 추출 파일

## 실측값 (2026-10-07, 설정 근거)

| 항목 | 값 |
|---|---|
| 셀 데이터 증가 | 3.45 MB/h (24대 3.44~3.54) → 6h 사이클 셀당 20.7 MB |
| 충전 전력 | 24대 77~79% 에서 68 W · 만충 뒤 바닥 약 31 W (셀 작동분) |
| 충전 시간 | 82% → 100% 44분 |
| 추출 | 6대 동시 5 GHz 2.88 MB/s · 24대 동시 2.41 MB/s · 복귀 17~18초 |
| 전원 끊김 | 플러그로 10초 × 3회 끊어도 24셀 측정 유지 |
| 재부팅 | `0x15` 는 TCP 대기 모드로 켜져 측정을 안 한다 → 재부팅은 항상 `0x26` |

## 아직 하지 않은 것

- 추출 뒤 삭제(`0x13`)는 `delete_after_extract=False`. 사람이 승인한 사이클부터 켠다.
- 심박 시뮬레이터(ESP32-C3 보드)는 보드 도착 후. 셀에 이름을 쓰는 `0x20` 은 `protocol.ble_id_frame` 에 준비돼 있다.
- 셀별 작동시간은 직접 잴 수 없어(가장 빠른 셀이 기준선에 닿으면 방전이 끝남) 방전 속도로 환산한다(`discharge_####.csv`).
