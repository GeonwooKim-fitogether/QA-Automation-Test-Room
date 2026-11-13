# 레일 QC 담당자 절차서 — 셀 한 세트를 시험 ID 여섯 개로 시험하고 Pass/Fail 을 내린다

> 한 줄 요지: **준비 → GNSS-1(독 1 · 3행, 혼자, 회차마다 1차 거르기) → 독을 채우고 센서 시험 GNSS-2 · IMU-1 · IMU-2 → 판정 → 펌웨어 시험 FIRMWARE-1(센서 불합격 셀은 판정에서 빠짐) → 판정 → 결과판에서 QC 판정.** FIRMWARE-2 는 보류라 지금은 돌리지 않는다. 시험 이름 · 조건 · 합격 기준의 정본은 [`qc-tests.md`](qc-tests.md)이고, 실행기 · 판정 스크립트의 정본은 [`cell-bench/tools/qc_tests/README.md`](../../cell-bench/tools/qc_tests/README.md)다. 명령은 모두 제어 PC2 의 Windows PowerShell 에서 `C:\Dev\dynamic-bench` 폴더로 들어가 실행한다. 판정 기준은 **1차(2026-11-02) · 제안값**이라, 결과는 기록과 함께 QC 판정에 쓰되 바깥 대조로 확정되기 전까지 출하 근거로만 쓰지 않는다.

2026-11-12 부터 옛 이름 '파트' 를 쓰지 않는다. 같은 날 결정으로 시험이 여섯이 됐다 — 그날까지 FIRMWARE-2 로 부르던 등속 장면 동기 운전은 GNSS-2 다. 그 전 기록을 읽을 때는 [`qc-tests.md`](qc-tests.md) 바뀐 이력과 [수집 일지](../records/qc-collection-log.md) 머리의 대응을 본다.

## 여섯 시험이 묻는 것

| 시험 | 담당자가 묻는 질문 | 모터 | 셀이 Fail 이면 뜻하는 것 |
|---|---|---|---|
| GNSS-1 · GNSS 수신 | 이 셀은 다른 셀과 똑같이 GPS 신호를 받는가 | 멈춤 | 안테나 · 수신 회로가 신호(C/N0)를 약하게 받는다 |
| GNSS-2 · GNSS 레일 | 레일과 같은 속도로 움직이는 장면에서 GPS 원값 속도가 레일 속도와 같은가 | 300 · 100 · 500 mm/s 등속 왕복 + 같은 속도의 시뮬레이터 장면 | GNSS 수신 모듈이 움직일 때 속도를 틀리게 낸다 |
| IMU-1 · IMU 정지 | 가만히 있을 때 IMU 가 0 을 가리키는가 | 멈춤 | 자이로스코프 영점 · 가속도계가 틀어졌다 |
| IMU-2 · IMU 레일 | 움직일 때 IMU 가 레일과 같은 속도 변화를 재는가 | 긴 거리 왕복 | 움직일 때 가속도를 틀리게 잰다 |
| FIRMWARE-1 · IMU 펌웨어 | 레일이 움직이고 멈출 때 IMU 움직임 판정이 제때 켜지고 꺼지는가 | 등속 없는 왕복, 가속도 0.5 → 8 → 0.5 m/s² 단계 | IMU 움직임 판정이 늦거나 잘못 켜진다 — **합격 기준 미정(프로토콜 설계 중)이라 지금은 기록만 한다** |
| FIRMWARE-2 · 속도 · 거리 | IMU 판정을 거친 속도로 더한 거리가 실제 거리와 같은가 | **보류** — 사람 같은 운전과 그에 맞춘 장면이 준비되면 | — |

## 0. 시작 전

1. **셀을 독에 꽂고 켠다.** 독 플러그(Tapo "Dynamic Bench")가 켜져 있는지 본다. 충전 유지는 PC 가 알아서 한다(작업 "Rail Charge Keep"). 실행기는 시험 동안 플러그를 끄고 배터리로 돌린다.
2. **배터리 · 저장 확인.**
   ```
   cd C:\Dev\dynamic-bench
   .venv\Scripts\python.exe cell-bench\tools\rail_cells.py status
   ```
   - 모든 셀 배터리 30 % 이상이어야 한다. 아니면 충전부터 한다.
   - "비워야 함"(120 MB 이상)이 보이면 `rail_cells.py extract` 로 먼저 비운다.
   - "안 보임" 셀은 꺼졌거나 망에서 빠진 것이다. 껐다 켠다.
3. **펌웨어가 모두 같은지 본다.** 다른 셀은 판정하지 않고 "펌웨어 불일치"로 적는다(2026-11-02 현재 10677 만 0.99.68).
4. **현장 확인.** 모터를 움직이는 시험(GNSS-2 · IMU-2 · FIRMWARE-1) 전에 레일 옆에서 확인 세 가지를 읽고 `현장확인` 을 입력한다. 표지가 없으면 실행기가 기다렸다가, 만들면 바로 이어 간다.
   ```
   .venv\Scripts\python.exe cell-bench\tools\attend.py --by <이름>
   ```

## 1. GNSS-1 — 독 1 · 3행만, 혼자 (모터 없음)

독의 1행 · 3행(배치 파일 `columns` 의 1 · 3번째 줄, 12칸)에만 셀을 꽂고 나머지는 비운다. 실행기가 플러그를 끄고, 정지 장면을 장면 313분부터 다시 틀고, 감쇠 18 dB 로 판정 구간(장면 321 ~ 346분)을 기록한 뒤 셀 기록까지 받는다.
```
.venv\Scripts\python.exe cell-bench\tools\qc_tests\gnss1_run.py <회차>
```
- 기록 폴더: `cell-bench\data\GNSS-1_<날짜>\r<회차>`.
- 다른 시험과 함께 돌리지 않는다 — 독 배치와 장면 구간이 다르다.
- **회차가 끝나면 바로 1차 거르기를 한다.** 화면 끝에 셀마다 '합격 · 재측정 · 불합격' 이 찍히고 `cell-bench\data\qc_gate_<세트>.json` 에 쌓인다. 위치를 못 잡았거나 칸 기준값보다 3 dB 넘게 낮으면 '재측정' — **다음 회차에 그 셀을 다른 칸에 꽂는다.** 두 번 걸리면 '불합격' 이고, 뒤 시험의 판정에서 빠진다.

## 2. 독을 채운 시험 — 센서 시험 묶음, 판정, 펌웨어 시험 묶음

셀을 독에 모두 꽂은 뒤 묶음 실행기로 돈다. 준비(플러그 끔 · 정지 장면 · 감쇠 0 · 위치 잡기 · 배터리 확인)와 셀 기록 받기는 묶음마다 한 번이다. 모터를 쓰므로 `.venv` 파이썬으로 띄운다. **센서 시험 결과로 펌웨어 시험을 거르기 위해 센서 묶음과 펌웨어 시험 사이에 판정(3절의 IMU-1 · IMU-2 · GNSS-2)을 한다.** 센서 묶음은 장면을 바꾸는 GNSS-2 를 맨 끝에 돈다.
```
.venv\Scripts\python.exe cell-bench\tools\qc_tests\qc_bundle.py --tests GNSS-2,IMU-1,IMU-2
```
판정(3절) 뒤에:
```
.venv\Scripts\python.exe cell-bench\tools\qc_tests\firmware1_step_run.py
```
- 실행기는 시작할 때 `qc_gate_<세트>.json` 을 읽어 앞 시험에서 불합격한 셀을 판정 대상에서 빼고, 뺀 셀을 화면과 `run.log` · `summary.json` 에 적는다. 셀은 독에 그대로 두면 된다. 거르기를 끄려면 `--include-failed`.
- 시험 하나만 돌릴 때는 그 실행기를 쓴다(`gnss2_run.py` · `imu1_run.py` · `imu2_run.py` · `firmware1_step_run.py`). 옵션은 같다(`--n` 운전 횟수, `--layout` 독 배치, `--tag` 폴더 꼬리). FIRMWARE-1 은 `--n` 대신 `--steps`(가속도 단계) · `--step-n`(단계마다 횟수)을 쓴다. FIRMWARE-2 는 보류라 실행기가 없다 — 묶음 실행기에 주면 장비를 건드리기 전에 멈춘다.
- 기록 폴더: 시험마다 `cell-bench\data\<시험 ID>_<날짜>`. 실행 기록(`run.log`)은 모든 폴더에 같은 줄로 남는다.
- 결과판 > 동적 벤치 시험 > 실시간 화면에서 셀이 "고정"인지, 모터가 도는지 본다.

실행기가 멈추면 화면의 마지막 줄이 이유다 — 입회 표지 시간 부족이면 0절 4번을, 배터리 부족이면 충전을, "정상으로 끝나지 않아"면 모터 기록(`runN_motor.log`)을 엔지니어에게 넘긴다. 한 시험의 운전이 실패하면 뒤 시험은 돌지 않지만 셀 기록은 받는다.

"다른 모션 엔진이 이미 돈다 — 레일 라운드를(드라이브를) 다른 프로그램이 쥐고 있다(pid · 무엇 · 폴더 · 언제부터)" 로 멈췄으면, 이 PC 의 다른 프로그램(다른 Claude 세션 · 다른 작업 트리 포함)이 레일 드라이브를 쓰고 있다는 뜻이다. 레일과 드라이브는 그대로 두고, 괄호 안의 프로그램이 끝난 뒤 다시 띄운다. 레일 실행기는 라운드 내내(운전과 운전 사이 틈까지) PC 에 하나뿐인 잠금을 쥐어, 운전 중에 다른 프로그램이 드라이브에 붙지 못하게 한다(2026-11-12 16:43 다른 작업 트리의 시험이 운전 중인 드라이브에 붙어 Err88.2 로 멈춘 일 뒤, `cell-bench/motionbench/drive_lock.py`).

## 3. 판정

시험별 판정 스크립트는 셀 기록을 읽어야 해서 **Python 3.10** 으로 한다. 어느 스크립트를 쓰는지는 [`qc_tests/README.md`](../../cell-bench/tools/qc_tests/README.md) 표가 정본이다.
```
$py = "C:\Users\HW team\AppData\Local\Programs\Python\Python310\python.exe"
& $py cell-bench\tools\qc_tests\gnss1_judge.py GNSS-1_<날짜>
& $py cell-bench\tools\qc_tests\imu1_judge.py IMU-1_<날짜>              # 끝에 합격 · 불합격을 거르기 파일에 적는다
& $py cell-bench\tools\qc_tests\imu2_judge.py IMU-2_<날짜> --lag -0.3     # 같음
& $py cell-bench\tools\qc_tests\firmware1_step_judge.py FIRMWARE-1_<날짜>   # 살펴봄 — 합격 기준 없음. 켜짐 지연 칸은 믿을 수 없다(README)
```
결과판 목록에 줄을 올리는 판정 파일(`protocol_<시각>.json`)은 `protocol_v4.py` 가 쓴다 — 시험 ID 를 첫 낱말로 준다. `pv4_shift.py` 는 그것을 그대로 부르되 이 폴더의 독 배치(Y-shift 등)를 알게 한다. `<배치>` 는 실행할 때 준 `--layout` 이다. 받은 셀 기록 폴더(`cell-bench\data\ftg_y_<시각>`, 기록 폴더 `summary.json` 의 `ftg`)를 `<FTG>` 자리에 쓴다.
```
& $py cell-bench\tools\qc_tests\pv4_shift.py GNSS-2 --runs cell-bench\data\GNSS-2_<날짜>\v300\runs.json --truth cell-bench\data\partB_20261030\rail_B_120s_truth.csv --scene S03_RailB120 --ftg <FTG> --dock <배치>
```
판정 파일이 생기면 1분 안에 결과판에 줄이 생긴다. GNSS-2 는 속도마다 첫 운전을 판정에서 뺀다(레일이 장면보다 먼저 출발한다). GNSS-2 의 합불은 GPS 원값 속도와 신호 세기로 보고, IMU 판정을 거친 속도 항목은 참고값이다. FIRMWARE-1 의 지금 조건(가속도 단계 운전)은 결과판 판정 파일을 아직 쓰지 않는다.

## 4. QC 판정 (결과판)

**어디서:** 결과판 > 동적 벤치 시험 > 오늘 줄을 연다 · **무엇을:** 맨 아래 QC 판정에서 셀마다 Pass/Fail 과 코멘트를 넣고 저장 · **무엇이 보이나:** 목록의 "QC 판정" 열이 채워진다.

판정 규칙(1차):
- 한 시험에서 실행의 절반 넘게 Fail 인 셀은 그 시험 Fail 이다.
- **장비 이상**(목록 회색) 줄은 셀 판정에 쓰지 않고 그 시험을 다시 한다.
- Fail 셀은 **자리를 바꿔 그 시험을 한 번 다시** 한다. 다시 Fail 이면 **선별 제외**로 두고 엔지니어에게 넘긴다. 다시 Pass 면 칸(독 자리) 문제로 코멘트를 남긴다.
- 노란 "벗어남"(살펴봄 항목)은 Fail 이 아니다. 같은 셀이 여러 날 반복해 벗어나면 코멘트로 남긴다.

결과판 화면은 2026-11-12 현재 아직 옛 이름표(A · B · C 글자)로 보일 수 있다 — 판정 파일에 옛 칸을 함께 적어 화면이 깨지지 않게 했고, 화면의 이름표는 결과판 쪽에서 바꾼다.

## 문제가 생기면

| 보이는 것 | 뜻 | 할 일 |
|---|---|---|
| 셀이 결과판에서 안 보인다 | 꺼졌거나 배터리 바닥 | 독에 꽂아 충전하고 켠다 |
| 셀이 빨간불로 깜빡인다 | 배터리가 낮다(2026-11-02: 9~16 %) | 충전한다. 충전해도 측정이 안 돌아오면 저장을 본다 |
| `rail_cells.py` 가 "포트를 다른 프로그램이 쓴다" | 실시간 기록이 아직 돈다 | 그 창을 끝내고 다시 |
| 받기 뒤 "안 지움" | 지우기 응답 없음(10677 펌웨어 0.99.68) | 기록은 PC 에 남았다. 펌웨어를 올려야 지워진다 — 엔지니어 |
| 결과판 실시간 화면에 "충전 제어 끊김" | PC 가 플러그를 못 찾는다 | Tapo 앱에서 플러그가 온라인인지 본다 |
| GNSS-1 이 "수집 실행기 잠금을 다른 프로그램이 쥐고 있다" | 하루 수집 실행기 등 다른 시험이 돈다 | 그 시험이 끝난 뒤 다시 |

## 관련

- 시험 목록 · 합격 기준(정본): [`qc-tests.md`](qc-tests.md)
- 실행기 · 판정 스크립트: [`cell-bench/tools/qc_tests/README.md`](../../cell-bench/tools/qc_tests/README.md)
- 1차 기준 규칙과 값: `cell-bench/motionbench/qc_criteria.py` · `cell-bench/motionbench/criteria/rail_v1.json`(시험 ID 마다 한 칸)
- 모터 값(승인 프리셋): `dynamic-bench/dev/presets/` — `GNSS-2_v300` · `_v100` · `_v500`, `IMU-2_long`, `FIRMWARE-1_step-a500` · `-a1000` · `-a2000` · `-a4000` · `-a8000`(옛 조건 `FIRMWARE-1_a50` · `FIRMWARE-1_a500` 은 이력)
