# 레일 QC 시험 실행기와 판정 스크립트

이 폴더에는 [`dynamic-bench/design/qc-tests.md`](../../../dynamic-bench/design/qc-tests.md)에 정의한 시험 여섯 개(GNSS-1 · GNSS-2 · IMU-1 · IMU-2 · FIRMWARE-1 · FIRMWARE-2)를 **돌리는 실행기**와 **판정하는 스크립트**가 들어 있다. 시험마다 실행기가 하나씩 있고(보류 중인 FIRMWARE-2 는 없다), 여러 시험을 이어 돌릴 때는 **묶음 실행기**(`qc_bundle.py`)가 준비와 셀 기록 받기를 한 번만 한다. 판정 스크립트는 2026-11-09 ~ 11-10 에 Y 세트로 실제로 돌려 결과를 낸 그대로이고, 이름만 시험 ID 로 바꿨다(2026-11-12).

2026-11-12 사용자 결정으로 시험을 **수신 · 원값 · 펌웨어를 거친 값**으로 나눠 다섯에서 여섯이 됐다. 그날까지 FIRMWARE-2 로 부르던 등속 장면 동기 운전은 GPS 원값을 보는 **GNSS-2** 가 됐고(실행기 `gnss2_run.py`, 프리셋 `GNSS-2_v*` — 값은 그대로), FIRMWARE-1 은 가속도 단계 운전(`firmware1_step_run.py`)으로 다시 짜는 중이며, FIRMWARE-2 는 보류다.

## 먼저 알아 둘 것

- **실행 위치.** 스크립트는 파일 자리에서 `cell-bench` 폴더를 찾는다 — 제어 PC 에서는 `C:\Dev\dynamic-bench\cell-bench` 다. 셀 기록(FTG) 파서는 `C:\Dev\ohcoach-cell-tools\cores\fitogether_data_parser` 를 쓴다. `pv4_shift.py` 만 제어 PC 경로가 코드에 박혀 있다.
- **파이썬이 둘이다.** 모터를 움직이는 실행기(`*_run.py`, `qc_bundle.py`)는 `C:\Dev\dynamic-bench\.venv\Scripts\python.exe` 로 띄워야 한다. 시스템 파이썬에는 모터 통신 모듈(pysoem)이 없어 모터 명령이 바로 실패한다(2026-11-10 실측). 판정 스크립트는 pandas 가 있는 시스템 파이썬 3.10 으로 돌린다.
- **모터 실행기는 입회 표지가 있어야 레일을 움직인다.** 운전자가 레일 옆에서 `tools/attend.py` 로 만든다. 표지가 없으면 기다렸다가 만들면 바로 이어 간다.
- **결과는 `cell-bench/data/<시험 ID>_<날짜>[꼬리표]/` 에 쓴다.** 폴더마다 `summary.json`(조건 · 운전 · 받은 셀 기록 폴더 `ftg`), `run.log`(실행 기록), `tap.log`(실시간 기록). 2026-11-12 전 기록 폴더(`collectA_` · `imuStill_` · `railC_` · `railB_` · `railMap_` · `gapTest_` · `imuStep_`)와 2026-11-12 에 FIRMWARE-2 이름으로 만든 폴더는 이름을 바꾸지 않았고, 판정 스크립트가 그대로 읽는다.
- 독 배치 `Y-shift` · `Y-shift-b` · `Y-rot` 은 이 폴더의 `dock_layout_a.json` 에 있다(저장소의 `motionbench/dock_layout.json` 에는 없다). 실행기와 `pv4_shift.py` 가 이 배치를 함께 읽는다.

## 용어

| 용어 | 뜻 |
|---|---|
| IMU 움직임 판정 | 셀 펌웨어가 IMU 가속도 크기의 흔들림(표준편차 > 7.0)으로 셀이 움직이는지 판단하는 것. '움직임' 이면 IMU 를 0.01초 값(0x0E)으로, 아니면 0.1초 평균(0x0F)으로 기록한다 |
| IMU 판정을 거친 속도 | IMU 움직임 판정이 '움직임 아님' 이면 0 이 되는 GPS 속도(셀 기록 `filtered_sog`) |
| GPS 원값 속도 | 판정을 거치지 않은 GPS 수신 모듈의 속도(라이브 패킷 · 셀 기록 `sog`) |

## 시험별 파일

| 시험 | 실행기(장비를 움직임) | 판정 | 결과 폴더 |
|---|---|---|---|
| GNSS-1 · GNSS 수신 시험 | `gnss1_run.py <회차>` (모터 없음, 독 1 · 3행 · 장면 313분부터 · 판정 구간 장면 321 ~ 346분 · 감쇠 18 dB). 회차가 끝나면 바로 1차 거르기 | 1차 거르기는 실행기가, 셀 효과 분해는 `gnss1_judge.py GNSS-1_<날짜>` | `GNSS-1_<날짜>/r<회차>` |
| 감쇠 단계 실측(GNSS-1 조건 정하기) | `attn_sweep.py 0 6 12 18 24 30` | 같은 파일이 출력 | 스크립트 옆 `attn_sweep.json` |
| GNSS-2 · GNSS 레일 시험 | `gnss2_run.py --speeds 100,300,500 --n 10` (등속 왕복 + 같은 속도로 움직이는 장면) | `pv4_shift.py GNSS-2 ...` (`protocol_v4.py GNSS-2` 를 그대로 부름). 합불은 GPS 원값 속도 비 · 신호 세기, IMU 판정을 거친 속도 항목은 참고값. GPS 원값 위치 항목은 아직 없다 | `GNSS-2_<날짜>[꼬리표]/v<속도>` |
| IMU-1 · IMU 정지 시험 | `imu1_run.py --boots 6` (모터 없음, 재부팅 6번 · 5분 기록) | `imu1_judge.py IMU-1_<날짜>` | `IMU-1_<날짜>` |
| IMU-1 (GNSS-1 기록에서) | — | `imu1_gnss1_judge.py GNSS-1_<날짜>` | 같은 폴더의 `imu_items.json` |
| IMU-2 · IMU 레일 시험 | `imu2_run.py --n 10` (긴 거리 150 ↔ 1,550 mm · 300 mm/s · 500 mm/s²) | **`imu2_judge.py IMU-2_<날짜> --lag -0.3`** | `IMU-2_<날짜>/long` |
| FIRMWARE-1 · IMU 펌웨어 시험 (가설, 프로토콜 설계 중) | `firmware1_step_run.py` (등속 없는 왕복, 가속도 0.5 → 8 → 0.5 m/s² 단계, 단계마다 왕복 5번, 프리셋 `FIRMWARE-1_step-a<가속도>`). `--steps` · `--step-n` 으로 바꾼다 | `firmware1_step_judge.py FIRMWARE-1_<날짜>` — 살펴봄, 합격 기준 없음. **약점 두 가지는 아래** | `FIRMWARE-1_<날짜>/<차례>_a<가속도>` · `firmware1_step_judge.json` |
| FIRMWARE-1 옛 조건(이력) | `firmware1_run.py --n 10` (150 ↔ 750 mm · 100 mm/s 등속 · 50 과 500 mm/s²) | `firmware1_judge.py FIRMWARE-1_<날짜>` 의 켜짐 · 꺼짐 시간 | `FIRMWARE-1_<날짜>/{a50,a500}` · `judge_items.json` |
| FIRMWARE-2 · 속도 · 거리 시험 | **보류** — 실행기 없음. 실행기에 주면 장비를 건드리기 전에 멈춘다 | — | — |
| 여러 시험 이어서 | `qc_bundle.py --tests GNSS-2,IMU-1,IMU-2` → 판정 → `firmware1_step_run.py` | 시험마다 위 판정 | 시험마다 제 폴더 |
| 칸 지도(GNSS-2 의 칸 효과) | `qc_bundle.py --tests GNSS-2 --speeds 100 --layout Y-rot --tag _map` | `slot_map.py`(2026-11-10 기록 전용) | `GNSS-2_<날짜>_map` |
| 레일 상한 시험(QC 시험 아님) | `accel_step_run.py --steps 300:1000,...` (프리셋 `cap-…`) | 같은 파일이 출력 | `accelStep_<날짜>` |

### FIRMWARE-1 판정 스크립트의 알려진 약점

`firmware1_step_judge.py` 는 2026-11-12 첫 운전(`cell-bench/data/imuStep_20261112-1744`)을 본 첫 판이다. 고치는 것은 다음 작업이다.

1. **셀 시계 차를 운전 전체에서 값 하나로만 맞춘다.** 그래서 '켜짐 지연' 칸(초)은 믿을 수 없다. 꺼짐 지연은 아직 내지 않는다.
2. **셀 시계가 기록 도중 뛴 셀은 모든 단계가 0 % 로 나온다.** 첫 운전의 10680 · 10694 가 그랬다 — 불량이 아니라 판정 무효다. 화면에 0 % 셀이 보이면 그 셀의 시계를 먼저 본다.

첫 운전 기록을 다시 볼 때는 독 안 셀을 `--dock` 으로 준다(그 기록의 `summary.json` 에는 배치 칸이 없다).

## 단계별 거르기 — 센서 시험에서 떨어진 셀은 펌웨어 시험에 넣지 않는다

운영 순서는 GNSS-1 → GNSS-2 → IMU-1 → IMU-2 → FIRMWARE-1 → FIRMWARE-2 다(2026-11-12 사용자 결정, 규칙의 정본은 [`qc-tests.md`](../../../dynamic-bench/design/qc-tests.md) 2-1절). 센서 시험 넷(수신 · 원값)을 먼저, 펌웨어 시험 둘을 뒤에 한다.

- **거르기 파일**: 세트마다 `cell-bench/data/qc_gate_<세트>.json`. 셀마다 시험 ID 별 합격 · 재측정 · 불합격과 날짜 · 근거 폴더 · 이유. 다루는 코드는 `motionbench/qc_gate.py`, 숫자는 `motionbench/criteria/qc_tests.json`.
- **누가 적나**: GNSS-1 은 `gnss1_run.py` 가 회차마다 바로(실시간 패킷의 신호 세기로 — 위치 못 잡음 · 칸 기준값보다 3 dB 넘게 낮음이면 재측정, 두 번이면 불합격). IMU-1 · IMU-2 는 `imu1_judge.py` · `imu2_judge.py` 가 판정 끝에. GNSS-2 는 아직 거르기 파일에 적는 판정 스크립트가 없다.
- **누가 빼나**: 모든 실행기가 시작할 때 거르기 파일을 읽어 앞 시험에서 불합격한 셀을 `summary.json` 의 `excluded` 와 `run.log` 에 적고, 판정 스크립트(`imu1_judge` · `imu2_judge` · `firmware1_judge`)와 `protocol_v4.py`(`--runs` 의 기록 폴더)가 그 셀을 건너뛴다. 셀은 독에 있어도 된다. `--include-failed` 로 끈다.
- **재측정 셀**은 다음 GNSS-1 회차에 다른 칸에 꽂는다. 같은 칸에서 다시 재면 거르기 결과에 '같은 칸에서 다시 잼' 이 붙는다.
- **묶음 안에서는** 앞 시험의 판정이 아직 없다 — 센서 시험 결과로 펌웨어 시험을 거르려면 센서 시험 묶음 → 판정 → 펌웨어 시험 차례로 나눠 돈다.

## 실행기의 모양 — 하나씩도, 함께도

- **하나씩.** 각 `*_run.py` 는 준비 → 자기 운전 → 셀 기록 받기를 혼자 다 한다. 옵션은 같다: `--n`(운전 횟수), `--layout`(독 배치, 기본 Y-shift), `--tag`(폴더 꼬리), `--no-extract`(셀 기록을 받지 않음), `--include-failed`(단계별 거르기 끔). FIRMWARE-1 은 `--n` 대신 `--steps` · `--step-n` 을 쓴다.
- **함께.** `qc_bundle.py --tests IMU-2,FIRMWARE-1` 은 준비(플러그 끔 · 정지 장면 · 감쇠 0 · 위치 잡기 · 배터리 확인)를 한 번만 하고, 시험마다의 운전 부분(`drive`)만 차례로 부른 뒤 셀 기록을 한 번 받는다. 차례는 주는 순서와 상관없이 IMU-1 → IMU-2 → FIRMWARE-1 → GNSS-2 다 — 운영 순서를 따르되, 장면을 바꾸는 GNSS-2 는 다른 레일 시험이 정지 장면 아래에서 돌도록 맨 끝에 둔다(거르기 파일은 시작할 때 한 번만 읽으므로 거르기 결과는 같다). 한 시험의 운전이 실패하면 뒤 시험은 돌지 않지만 셀 기록은 받는다.
- **GNSS-1 은 혼자 돈다.** 독 1 · 3행 배치와 장면 구간이 다른 시험과 달라 묶음 실행기가 거부한다. 수집 실행기 잠금(`charge.LOOP_LOCK`)을 쥐어 충전 유지가 플러그를 다시 켜지 않게 한다.
- **FIRMWARE-1 의 단계 운전은 단계마다 멈추는 기준을 본다.** 추종 오차가 문턱의 90 % 를 넘거나 알람 · 비정상 종료면 다음 단계로 가지 않는다(2026-11-12 첫 운전의 기준).
- 엮는 뼈대는 `run_common.py`, 공통 준비 함수는 `cell-bench/tools/qc_round.py`(`rail_prep` · `rail_finish` · `extract`), 레일 운전은 `cell-bench/tools/run_rail.py` 다. 장비 없이 차례만 보는 시험: `cell-bench/tests/test_qc_bundle.py`.

## 쓰지 않는 것과 주의할 것

- **GNSS-2 의 IMU 판정을 거친 속도 항목(`ftg_move` · `ftg_on` · `ftg_dist`)은 합불에 쓰지 않는다.** 등속 구간에서는 IMU 가 볼 흔들림이 없어 IMU 움직임 판정이 '움직임 아님' 으로 가는 것이 설계대로라(2026-11-10: 300 · 500 mm/s 에서 '움직임' 61 ~ 64 %), 그 값으로 셀을 가르면 레일의 매끄러움을 재는 셈이다. 참고값으로만 남는다.
- `firmware1_judge.py`(옛 조건)의 **IMU 비율(출발 비 · 감속 비)은 쓰지 않는다.** 센서 축 하나를 상관으로 고르는 1판 방식이라 셀 몇 대에서 계산이 실패했다. IMU-2 는 `imu2_judge.py` 로 판정한다.
- **감속 구간 IMU 비율은 아직 믿지 않는다.** 셀은 멈추는 순간 IMU 기록 방식(움직임 0x0E → 멈춤 0x0F)을 바꾸는데, 그 자리에서 감속의 끝이 잘린다.
- **GNSS-2 는 속도마다 첫 운전을 뺀다.** 첫 운전은 `run_rail.py` 가 모터 지연을 추정값(5.3초)으로 써서 레일이 장면보다 2.6 ~ 2.8초 먼저 출발한다. `slot_map.py` 는 이미 뺀다.
- **한 셀 기록에 정지 장면과 움직이는 장면이 섞이면 셀 시각이 장면마다 다르다.** `firmware1_judge.py` 는 셀 시계 차를 운전 폴더마다 따로 잰다.
- **실행기가 실시간 기록을 운전보다 길게 켜 둬야 한다.** 실시간 기록이 먼저 끝나면 배터리 값이 오래돼 모터 안전 장치가 시작을 거부한다(GNSS-2 는 10번 · 세 속도에 200분으로 잡는다).
- **GNSS-1 판정은 꽂지 않은 칸의 셀도 읽는다.** 1 · 3행만 꽂아도 비운 칸의 셀이 켜져 있으면 들리고, `gnss1_judge.py` 는 배치 파일의 칸으로 셀을 센다. 결과를 볼 때 `summary.json` 의 `rows` 에 든 행만 본다.

## 참고 파일

- `raw_layer.py` — 셀 기록(FTG)을 읽어 GNSS 원값과 IMU 두 흐름(0x0E · 0x0F)을 꺼내는 함수. 여러 판정 스크립트가 불러 쓴다. 직접 돌리면 하루 수집 기록의 판정 창(GNSS-1 · FIRMWARE-1 옛 조건)마다 원값 지표를 낸다.
- `ftg_start_imu.py` — 기록 폴더마다 셀의 시작 메시지(펌웨어 · IMU 보정값)와 IMU 메시지 수. 정지 중 움직임 기록 비율(0x0E ÷ 전체)을 볼 때 쓴다.
- `slot_map.py` — 2026-11-10 칸 지도 기록(`railB_20261110*` · `railMap_20261110` · `railC_20261110`)을 읽는 일회성 분석. 새 기록에 쓰려면 폴더 이름을 고친다.
