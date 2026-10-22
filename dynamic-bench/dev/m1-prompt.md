# M1 개발 지시문 — 리니어 모터 읽기 전용 모니터 (2026-10-22, 김건우 승인)

**스킬:** 먼저 `ponytail` 스킬을 적용하라 — 동작하는 가장 단순한 해법. 과설계 금지, M1 에 필요 없는 모드(move/run/park 등)는 만들지 않는다.

## 할 일
`dynamic-bench/plan/plan-v2.md` 에 정의된 **마일스톤 M1** 을 그대로 구현하고, 실제 하드웨어로 10분 읽기 전용 시험을 한 번 돌려 기록한다.
먼저 읽을 것: plan-v2 의 2절(아키텍처, 2.2 층 구조, 2.3 시리얼 대조, 2.4 스레드), 5.1 M1 행, 6.2 `motionbench` 패키지 표, 8.3 체크리스트, 10.1. 그리고 `dynamic-bench/plan/plan-v1.md` 의 M1 점검 항목, `dynamic-bench/records/2026-10-21-commissioning.md` (PDO 매핑 · 어댑터 · 시운전 사실).

구현 범위 (M1 에 필요한 것만):
- `cell-bench/motionbench/` 패키지 골격 + 진입점 `cell-bench/run_motion.py` (plan-v2 6.2). M1 에는 `scan` · `monitor` 모드만.
  - `ecat.py`: pysoem 으로 어댑터 `\Device\NPF_{6D0E574A-8D90-4065-9679-6A2DBF525343}` (Realtek USB GbE) 열기, 슬레이브 탐색(1대, Panasonic MINAS-A6BL MBDLN25BL), PDO 매핑을 기록과 대조, 2 ms FreeRUN 주기 PDO 교환, **DC 끔**, WKC 검사, SDO **읽기만**.
  - `cia402.py`: Statusword 해석(순수 함수) 등 M1 에 쓰는 부분만.
  - `motion_record.py`: `data/motion.json` 상태(1초 심박, 원자적 쓰기 — cell-bench `control.write_json_atomic` 재사용) + 2 ms 로그(CSV, 줄마다 벽시계 시각과 monotonic 시각).
- PDO: 출력 0x6040 Controlword, 0x6060 Mode, 0x607A Target position, 0x60B8. 입력 0x603F Error code, 0x6041 Statusword, 0x6061, 0x6064 Position actual, 0x60B9, 0x60BA, 0x60F4 Following error, 0x60FD Digital inputs. 스케일 ~1 pulse = 1 µm.
- 읽는 값: statusword, 위치, 추종 오차, 디지털 입력, 에러 코드, 드라이브 시리얼(SDO 0x1018:04 읽기 — 읽히는지 자체가 M1 점검 ⑦).
- 주기 통계: 주기 시간 min/mean/p50/p99/p99.9/max, 늦은 주기 수, WKC 오류 수. 목표 **p99 ≤ 4 ms over 10 min**. Windows 타이머 해상도 문제는 정직하게 다루고 결과를 그대로 기록(못 맞추면 못 맞췄다고).
- 순수 함수 부분은 하드웨어 없이 도는 pytest 단위 시험을 둔다 (`cell-bench/tests/` 아래 새 파일).

## 절대 안전 규칙 (어기면 즉시 중단)
이 축에는 물리 안전장치가 없다 — 리미트 스위치 미배선, 비상정지 없음.
1. **서보 온 금지.** Operation Enabled 에 이르는 Controlword 비트를 절대 쓰지 않는다. 출력 PDO 는 항상 **Controlword = 0** (Fault reset 비트도 금지 — 오직 0), **Target position = 현재 Position actual**, **Mode of operation = 0/변경 없음**, 0x60B8 = 0.
2. EtherCAT 상태는 **SAFE-OP 까지만** (plan-v2 M1 정의). OP 요청 금지. SAFE-OP 에서는 드라이브가 출력 PDO 를 적용하지 않으므로 이중 안전이다.
3. **SDO 쓰기 금지** (읽기만). PANATERM 파라미터 변경 금지. EEPROM 쓰기 금지.
4. 모션 금지 — 어떤 종류의 움직임도 없다.
5. 매 주기 Statusword 를 검사: Operation Enabled (`sw & 0x6F == 0x27`) 이거나 Switched On 이상 등 **예상 밖 상태**, 예상 밖 상태 전이, 슬레이브 수 ≠ 1, PDO 매핑 불일치가 보이면 **즉시 중단**(INIT 로 내리고 어댑터 닫기)하고 기록한다. 판단이 애매하면 중단 쪽.
6. 하드웨어 실행 중 사람(김건우)이 축을 손으로 밀 수는 있다(부호 확인은 사용자 몫). 너는 그걸 요구하거나 기다리지 않는다.

## 파일 규칙
- 기존 cell-bench 파일은 고치지 않는다(M1 은 새 파일만으로 충분하다고 본다. 꼭 필요하면 plan-v2 6.1 이 M1 용으로 꼽은 최소 추가만, 이유를 기록). **`run_cycle.py`, `cycle.py`, `cells.py`, `cloud.py`, `supabase/migrations` 는 절대 건드리지 않는다.**
- 2 ms 원시 로그와 `data/` 산출물은 커밋하지 않는다(대용량). 요약 수치만 기록 문서에.
- `C:\Dev\QA-Automation-Test-Room`(메인 체크아웃), `C:\Users\HW team\SOEM`, `C:\eni` 등 조시우 폴더는 읽기만.

## 실행 환경
- Python: 작업 트리의 venv `.venv/Scripts/python.exe` (3.11.9, pysoem 1.1.13, pytest, cell-bench requirements 설치됨). 다른 python 은 쓰지 않는다. 필요하면 `.venv/Scripts/python.exe -m pip install ...` 만.
- 명령은 작업 트리 루트(`C:/Dev/dynamic-bench`)에서 경로를 명시해 실행한다 (예: `.venv/Scripts/python.exe cell-bench/run_motion.py monitor --minutes 10`, `.venv/Scripts/python.exe -m pytest cell-bench/tests/test_xxx.py`). `cd ... &&` 연결은 피한다.
- 10분 시험은 Bash 타임아웃(최대 25분으로 설정됨)을 충분히 주거나 백그라운드로 돌리고 끝까지 기다린다.
- Npcap 권한 문제 등으로 어댑터가 안 열리면 우회(관리자 권한 상승 등)하지 말고 그대로 기록하고 끝낸다.

## 기록
`dynamic-bench/records/2026-10-22-m1.md` 에: 실행 시각(KST), 실제 시험 길이, 주기 통계(p99 포함), WKC 오류, Statusword 관측값(전 구간 Operation Enabled 아님을 확인), 0x603F, 0x60FD 비트, 위치 범위, 0x1018:04 시리얼 결과, SDO 로 읽은 객체 표, 오류·중단 여부, plan-v2 M1 점검 8개 각각의 결과(사람이 할 항목 — 부호 s, cell-bench 공존 24 h — 은 "미실시/사용자"로).

## 커밋
- 김건우 이름으로(저장소 설정 그대로, author 변경 금지) `feature/dynamic-bench` 에 로컬 커밋. **push 금지, PR 금지.**
- 같은 브랜치에 다른 세션(기획 v3)이 동시에 커밋한다. 자기 파일만 명시적으로 `git add <경로들>` (절대 `git add -A` / `git add .` 금지). `index.lock` 이 있으면 몇 초 뒤 한 번 다시 시도.
- 마지막 줄: `DONE results=dynamic-bench/records/2026-10-22-m1.md`
