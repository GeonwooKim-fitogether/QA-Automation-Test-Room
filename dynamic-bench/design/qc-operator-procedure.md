# 레일 QC 담당자 절차서 — 셀 24대 한 세트를 A · C · B 로 시험하고 Pass/Fail 을 내린다

> 한 줄 요지: **준비 5분 → 파트 A 10분 → 파트 C · B 약 15분 → 받으면서 비우기 · 판정 10분 → 결과판에서 QC 판정.** 한 세트에 약 45분이다. 명령은 모두 제어 PC2 의 Windows PowerShell 에서 `C:\Dev\dynamic-bench` 폴더로 들어가 실행한다. 판정 기준은 **1차(2026-11-02) · 제안값**이라, 결과는 기록과 함께 QC 판정에 쓰되 바깥 대조로 확정되기 전까지 출하 근거로만 쓰지 않는다.

## 세 시험이 묻는 것

| 시험 | 담당자가 묻는 질문 | 모터 | 셀이 Fail 이면 뜻하는 것 |
|---|---|---|---|
| A 정지 | 이 셀은 다른 셀과 똑같이 GPS 신호를 받는가 | 멈춤 | 신호(C/N0)가 약하거나 위치를 놓치거나 엉뚱한 곳을 가리킨다 |
| C 출발 | 레일이 출발하면 셀이 제때 움직임을 알아채는가 | 가속도 50 mm/s² 왕복 | 움직임 판단이 늦다 |
| B 속도 | 레일과 같은 속도를 셀이 같은 속도로 읽는가 | 300 mm/s 왕복 + 같은 경로의 시뮬레이터 장면 | 속도를 다르게 읽거나 늦게 따라온다 |

모든 시험에서 공통으로 **수신**(라이브 패킷이 빠지지 않았는가)을 본다.

## 0. 시작 전 (약 5분)

1. **셀을 독에 꽂고 켠다.** 독 플러그(Tapo "Dynamic Bench")가 켜져 있는지 본다. 충전 유지는 PC 가 알아서 한다(작업 "Rail Charge Keep").
2. **배터리 · 저장 확인.**
   ```
   cd C:\Dev\dynamic-bench
   .venv\Scripts\python.exe cell-bench\tools\rail_cells.py status
   ```
   - 모든 셀 배터리 30 % 이상이어야 한다. 아니면 충전부터 한다.
   - "비워야 함"(120 MB 이상)이 보이면 `rail_cells.py extract` 로 먼저 비운다.
   - "안 보임" 셀은 꺼졌거나 망에서 빠진 것이다. 껐다 켠다.
3. **시뮬레이터 확인.** 정지 장면이 재생 중이어야 한다.
   ```
   .venv\Scripts\python.exe cell-bench\motionbench\labsat.py status
   ```
   "재생 중: S01-Static-GLE-8h" 가 아니면 `labsat.py play S01-Static-GLE-8h` 로 튼다. 장면을 새로 틀면 셀이 위치를 다시 잡는 데 몇 분 걸린다.
4. **펌웨어가 모두 같은지 본다.** 다른 셀은 판정하지 않고 "펌웨어 불일치"로 적는다(2026-11-02 현재 10677 만 0.99.68).

## 1. 파트 A — 정지 (10분, 모터 없음)

```
.venv\Scripts\python.exe cell-bench\motionbench\live_tap.py --minutes 10 --qc-now --part A
```
- 결과판 > 동적 벤치 시험 > 실시간 화면에서 셀 24대가 "고정"인지 본다. 10분이 지나면 저절로 끝난다.
- **시작 시각과 끝 시각을 적어 둔다**(판정에 쓴다).

## 2. 파트 C · B — 레일 (약 15분)

1. **실시간 기록을 켠다**(창 하나를 따로 연다, 모터 시험이 끝날 때까지 둔다).
   ```
   .venv\Scripts\python.exe cell-bench\motionbench\live_tap.py --minutes 40 --qc-now --part C
   ```
2. **현장 확인.** 레일 옆에서 확인 세 가지를 읽고 `현장확인` 을 입력한다.
   ```
   .venv\Scripts\python.exe cell-bench\tools\attend.py --by <이름>
   ```
3. **파트 C 3번.**
   ```
   .venv\Scripts\python.exe cell-bench\tools\run_rail.py C --n 3 --out cell-bench\data\qc_<날짜>_<세트>\C
   ```
4. **파트 B 3번.** 시뮬레이터 장면을 실행기가 알아서 바꾼다.
   ```
   .venv\Scripts\python.exe cell-bench\tools\run_rail.py B --n 3 --out cell-bench\data\qc_<날짜>_<세트>\B
   ```
   끝나면 정지 장면을 다시 튼다: `labsat.py play S01-Static-GLE-8h`.
5. 실시간 기록 창을 `Ctrl+C` 로 끝낸다.

실행기가 멈추면 화면의 마지막 줄이 이유다 — 입회 표지 시간 부족이면 2번을, 배터리 부족이면 충전을, "정상으로 끝나지 않아"면 모터 기록(`runN_motor.log`)을 엔지니어에게 넘긴다.

## 3. 받으면서 비우기 · 판정 (약 10분)

```
.venv\Scripts\python.exe cell-bench\tools\rail_cells.py extract
```
받은 폴더 이름(`cell-bench\data\ftg_y_<시각>`)을 아래 `<FTG>` 자리에 쓴다. 판정은 셀 기록을 읽어야 해서 **Python 3.10** 으로 한다.
```
$py = "C:\Users\HW team\AppData\Local\Programs\Python\Python310\python.exe"
& $py cell-bench\tools\protocol_v4.py a --from "<A 끝 5분 전>" --to "<A 끝>" --ftg <FTG> --scene S01-Static-GLE-8h
& $py cell-bench\tools\protocol_v4.py c --runs cell-bench\data\qc_<날짜>_<세트>\C\runs.json --ftg <FTG> --accel 50
& $py cell-bench\tools\protocol_v4.py b --runs cell-bench\data\qc_<날짜>_<세트>\B\runs.json --truth cell-bench\data\partB_20261030\rail_B_120s_truth.csv --scene S03_RailB120 --scene-utc0 2026-10-30T00:00:00
```
판정 파일이 생기면 1분 안에 결과판에 줄이 생긴다.

## 4. QC 판정 (결과판)

**어디서:** 결과판 > 동적 벤치 시험 > 오늘 줄을 연다 · **무엇을:** 맨 아래 QC 판정에서 셀마다 Pass/Fail 과 코멘트를 넣고 저장 · **무엇이 보이나:** 목록의 "QC 판정" 열이 채워진다.

판정 규칙(1차):
- 한 파트에서 실행의 절반 넘게 Fail 인 셀은 그 파트 Fail 이다(A 는 1번이므로 그 1번).
- **장비 이상**(목록 회색) 줄은 셀 판정에 쓰지 않고 그 파트를 다시 한다.
- Fail 셀은 **자리를 바꿔 그 파트를 한 번 다시** 한다. 다시 Fail 이면 **선별 제외**로 두고 엔지니어에게 넘긴다. 다시 Pass 면 칸(독 자리) 문제로 코멘트를 남긴다.
- 노란 "벗어남"(살펴봄 항목)은 Fail 이 아니다. 같은 셀이 여러 날 반복해 벗어나면 코멘트로 남긴다.

## 문제가 생기면

| 보이는 것 | 뜻 | 할 일 |
|---|---|---|
| 셀이 결과판에서 안 보인다 | 꺼졌거나 배터리 바닥 | 독에 꽂아 충전하고 켠다 |
| 셀이 빨간불로 깜빡인다 | 배터리가 낮다(2026-11-02: 9~16 %) | 충전한다. 충전해도 측정이 안 돌아오면 저장을 본다 |
| `rail_cells.py` 가 "포트를 다른 프로그램이 쓴다" | 실시간 기록이 아직 돈다 | 그 창을 끝내고 다시 |
| 받기 뒤 "안 지움" | 지우기 응답 없음(10677 펌웨어 0.99.68) | 기록은 PC 에 남았다. 펌웨어를 올려야 지워진다 — 엔지니어 |
| 결과판 실시간 화면에 "충전 제어 끊김" | PC 가 플러그를 못 찾는다 | Tapo 앱에서 플러그가 온라인인지 본다 |

## 관련

- 시험 설계: [`qc-protocol-v4.md`](qc-protocol-v4.md)
- 1차 기준 규칙과 값: `cell-bench/motionbench/qc_criteria.py` · `cell-bench/motionbench/criteria/rail_v1.json`
- 모터 값(승인 프리셋): `dynamic-bench/dev/presets/long-C.json` · `long-B.json`
