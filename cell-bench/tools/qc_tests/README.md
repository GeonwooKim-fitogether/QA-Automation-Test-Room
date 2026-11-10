# 레일 QC 시험 실행기와 판정 스크립트

이 폴더에는 [`dynamic-bench/design/qc-tests.md`](../../../dynamic-bench/design/qc-tests.md)에 정의한 시험(GNSS-1 · IMU-1 · IMU-2 · FIRMWARE-1 · FIRMWARE-2)을 **돌리는 실행기**와 **판정하는 스크립트**가 들어 있다. 2026-11-09 ~ 11-10 에 Y 세트로 실제로 돌려 결과를 낸 그대로 옮겨 왔다.

## 먼저 알아 둘 것

- **실행 위치가 정해져 있다.** 모든 스크립트는 제어 PC 의 `C:\Dev\dynamic-bench\cell-bench` 를 불러온다(경로가 코드에 박혀 있다). 셀 기록(FTG) 파서는 `C:\Dev\ohcoach-cell-tools\cores\fitogether_data_parser` 를 쓴다.
- **파이썬이 둘이다.** 모터를 움직이는 실행기(`*_run.py`, `a_round.py`)는 `C:\Dev\dynamic-bench\.venv\Scripts\python.exe` 로 띄워야 한다. 시스템 파이썬에는 모터 통신 모듈(pysoem)이 없어 모터 명령이 바로 실패한다(2026-11-10 실측). 판정 스크립트는 pandas 가 있는 시스템 파이썬 3.10 으로 돌린다.
- **모터 실행기는 입회 표지가 있어야 레일을 움직인다.** 운전자가 레일 옆에서 `tools/attend.py` 로 만든다.
- **결과는 `cell-bench/data/` 아래에 쓴다.** 폴더 이름은 실행기마다 아래 표에 적었다.
- 독 배치 `Y-shift` · `Y-shift-b` · `Y-rot` 은 이 폴더의 `dock_layout_a.json` 에 있다(저장소의 `motionbench/dock_layout.json` 에는 없다). `pv4_shift.py` 가 이 배치를 `protocol_v4.py` 에 알려 준다.

## 시험별 파일

| 시험 | 실행기(모터 · 장비를 움직임) | 판정 | 결과 폴더 |
|---|---|---|---|
| GNSS-1 · GNSS 수신 시험 | `a_round.py <바퀴> <배치> --attn 18` (모터 없음, 감쇠 18 dB · 30분) | `a_judge.py` | `collectA_<날짜>/r0k` |
| 감쇠 단계 실측(GNSS-1 조건 정하기) | `attn_sweep.py 0 6 12 18 24 30` | 같은 파일이 출력 | 스크립트 옆 `attn_sweep.json` |
| IMU-1 · IMU 정지 시험 | `imu_still_run.py --boots 6` (모터 없음, 재부팅 6번 · 5분 기록) | `imu_still_judge.py` | `imuStill_<날짜>` |
| IMU-1 (GNSS-1 기록에서) | — | `a_imu.py collectA_<날짜>` | 같은 폴더의 `imu_items.json` |
| IMU-2 · IMU 레일 시험 | `rail_c_run.py --n 10` (50 · 500 mm/s² · 긴 거리) | **`imu_rail_judge.py --lag -0.3`** | `railC_<날짜>` |
| FIRMWARE-1 · 움직임 판단 시험 | `rail_c_run.py` 와 같은 운전 | `rail_c_judge.py` 의 켜짐 · 꺼짐 시간 | `railC_<날짜>/judge_items.json` |
| FIRMWARE-2 · 속도 · 거리 시험 | `rail_b_run.py --speeds 100,300,500 --n 10` | `pv4_shift.py b ...` (`protocol_v4.py b` 를 그대로 부름) | `railB_<날짜>[꼬리표]/judged` |
| 칸 지도(FIRMWARE-1 · 2 의 칸 효과) | `rail_map_run.py --layout Y-rot` | `slot_map.py` | `railMap_<날짜>` |

## 쓰지 않는 것과 주의할 것

- `rail_c_judge.py` 의 **IMU 비율(출발 비 · 감속 비)은 쓰지 않는다.** 센서 축 하나를 상관으로 고르는 1판 방식이라 셀 몇 대에서 계산이 실패했다. IMU-2 는 `imu_rail_judge.py` 로 판정한다. 이 파일은 FIRMWARE-1 의 켜짐 · 꺼짐 시간에만 쓴다.
- **감속 구간 IMU 비율은 아직 믿지 않는다.** 셀은 멈추는 순간 IMU 기록 방식(움직임 0x0E → 멈춤 0x0F)을 바꾸는데, 그 자리에서 감속의 끝이 잘린다.
- **FIRMWARE-2 는 속도마다 첫 운전을 뺀다.** 첫 운전은 `run_rail.py` 가 모터 지연을 추정값(5.3초)으로 써서 레일이 장면보다 2.6 ~ 2.8초 먼저 출발한다. `slot_map.py` 는 이미 뺀다.
- **한 셀 기록에 정지 장면과 움직이는 장면이 섞이면 셀 시각이 장면마다 다르다.** `rail_c_judge.py` 는 셀 시계 차를 프리셋 폴더마다 따로 잰다.
- **실행기가 라이브 기록을 운전보다 길게 켜 둬야 한다.** 라이브 기록이 먼저 끝나면 배터리 값이 오래돼 모터 안전 장치가 시작을 거부한다(`rail_b_run.py` 는 200분으로 잡는다).

## 참고 파일

- `raw_layer.py` — 셀 기록(FTG)을 읽어 GNSS 원값과 IMU 두 흐름(0x0E · 0x0F)을 꺼내는 함수. 여러 판정 스크립트가 불러 쓴다.
- `ftg_start_imu.py` — 기록 폴더마다 셀의 시작 메시지(펌웨어 · IMU 보정값)와 IMU 메시지 수. 정지 중 움직임 기록 비율(0x0E ÷ 전체)을 볼 때 쓴다.
