# 내일 슬라이스 소프트웨어 개발 지시문 (2026-10-22, 김건우 승인) — 오프라인 · 모션 없음

**스킬:** `ponytail` — 동작하는 가장 단순한 해법. 내일(2026-10-23) 도크 1 + 셀 세트 1 을 모터에 싣고 "셀 vs 모터" 일관성을 처음 보는 데 필요한 만큼만.
먼저 읽을 것: `dynamic-bench/plan/plan-v3.md`(특히 3절 GPS · 3.5 셀 종류 · "내일 슬라이스" 절이 있으면 그것), `dynamic-bench/plan/plan-v2.md` 3절(시간 정렬 · 동기 펄스) · 6.2(`align.py`), `dynamic-bench/records/2026-10-22-m1.md`, M1 코드 `cell-bench/motionbench/`, `cell-bench/run_motion.py`, cell-bench 의 `cellbench/protocol.py` · 기록 형식(`record.py` 등).

## 절대 규칙 — 하드웨어 · 모션 없음
- **EtherCAT 어댑터를 열지 않는다.** `run_motion.py` 를 하드웨어 대상으로 실행하지 않는다(scan/monitor 포함). pysoem 을 실제로 쓰는 코드는 시험에서 가짜(fake)로 대신한다. Controlword 는 어떤 코드 경로에서도 0 이외 값을 쓰지 않게 둔다 — 이번 일에 서보 온 · 이동 코드는 없다.
- 셀 · LiveHub · 플러그 등 실제 장비에 연결하지 않는다(cell-bench 엔진/감시자 실행 금지). 전부 기록된 데이터나 합성 데이터로 시험.
- Supabase · GitHub · Slack 등 외부 쓰기 금지.

## 만들 것 (네 가지, 각각 pytest 로 오프라인 검증)
1. **GPS 필드 파서** — `cell-bench/cellbench/protocol.py` 에 `parse_gps` (기존 함수 동작은 그대로, 더하기만). 라이브 패킷 0x09, 1320 B, 2 Hz, UDP 60222. 펌웨어 사실(fitogether-org/cell-y4-ESP32-S3-firmware master 08fe785e, 양산 Y4 계열 v0.99.71 에서 v0.99.61 이후 변경 없음):
   byte 47 fix mode (ASCII N/A/D/F/R), byte 48 사용 위성 수 = 상위 5비트(`>> 3`), byte 49 평균 C/N0 dBHz u8, byte 55 gps_validity 비트(KINEXON=0 빌드), byte 41 hspeed(단위 의심 — 원값과 함께 `unit_unverified` 표시), bytes 57–1256 speed_history mm/s, 10 Hz × 60 s (600 × u16 로 보이나 엔디안 · 부호 · 순서(최신이 앞/뒤)는 `[확인 필요]` — 실제 기록 패킷이 있으면 그걸로 확인하고, 없으면 가정을 코드 주석과 기록에 명시). 바이트 오프셋 기준(패킷 헤더 포함 여부)은 protocol.py 의 기존 파싱과 맞춰라.
   iOS `Packet.swift` 는 위성 수를 하위 5비트로 읽는다(틀렸을 가능성) — 두 해석을 모두 돌려 볼 수 있는 작은 함수/주석만, iOS 코드는 건드리지 않는다.
   `cells.py` 는 고치지 않는다 — 원시 패킷 저장 통로(sink)는 다음 단계(지금은 파서 + 시험만).
2. **모터 2 ms 로그 기록기** — cell-bench `data/` 옆에 쓰는 형식(예: `data/motion/motion_<run>.csv`), 줄마다 PC 벽시계(ISO, KST) · monotonic · 위치 · 속도(위치 차분 또는 0x606C 가 PDO 에 없으면 차분) · statusword · 추종 오차. M1 의 `motion_record.py` 가 이미 하면 **재사용/정리만**. 셀 데이터와 같은 PC 시계 기준임을 문서화. 시험은 가짜 주기 데이터로.
3. **첫 일관성 비교 스크립트** — 예 `cell-bench/tools/compare_motion_cell.py` (+ 순수 함수는 `motionbench/align.py`):
   - 입력: 모터 2 ms 로그 + 셀 라이브 기록(cell-bench 가 쓰는 형식; 없으면 파서 1 의 출력 표).
   - 정렬: **동기 이동**(뚜렷한 짧은 왕복 펄스) 구간에서 모터 지령/실제 가속도 vs 셀 IMU 가속도 상호상관 → 시간 오프셋 · 드리프트(두 개 이상 펄스면) · 상관 피크 품질.
   - 비교: 정렬 뒤 모터 속도 vs 셀 속도(speed_history, hspeed) — 오차 평균 · RMS · p95 · 최대, 오프셋 · 드리프트를 표 + 짧은 글로 보고(CSV/MD 출력). 그래프는 선택(matplotlib 이 venv 에 있으면).
   - 셀 IMU 가 라이브 패킷에 실리는지, 실리면 어디인지는 cell-bench 코드로 확인하고, 없으면 `[확인 필요]` 로 두고 GPS 속도만으로 정렬하는 대안(속도 상호상관)을 같이 둔다.
   - 합성 데이터 시험: 알려진 오프셋 · 드리프트 · 잡음을 넣은 가짜 모터/셀 데이터에서 그 값을 되찾는지 pytest 로.
4. **X/Y 세트 종류 자동 인식 순수 함수** — 예 `motionbench/celltype.py` + `cell_types.json`(종류별 설정: 페이로드 기본값 5 kg, 느린 속도/가속 상한 `[제안값]`, 기대 양산 펌웨어 — Y4 계열 Y4A/Y4B/Y3B/X6A/X6B = v0.99.71, X5/Y1 = 미확정, 파서 변형, 합격 기준 자리). X 세트 = X5/X6 (도크 DKPX-1C), Y 세트 = Y1/Y3B/Y4 (도크 DKPY-1C). 셀 모델/시리얼 목록 → 세트 종류. **세트는 절대 섞이지 않는다** — 혼재로 읽히면 이상으로 보고 **시작 차단**(결과에 이유). 모르면 '모름' → 차단. 모델/시리얼로 종류를 가르는 실제 규칙이 코드에 없으면 plan-v3 3.5 대로 등록부(시리얼 → 종류) 방식으로 하고 규칙은 `[확인 필요]`.

## 파일 규칙
- 기존 cell-bench 파일은 `protocol.py`(더하기만), `docs/system-map.md`(새 모듈 등록이 시험상 필요하면 한 줄씩)만 고친다. `run_cycle.py`, `cycle.py`, `cells.py`, `cloud.py`, `supabase/` 절대 금지. 조시우 폴더 · 메인 체크아웃 `C:\Dev\QA-Automation-Test-Room` 은 읽기만.
- Python 은 `.venv/Scripts/python.exe` 만(작업 트리 루트에서 경로 명시). 필요한 패키지(numpy 등)는 `.venv/Scripts/python.exe -m pip install ...` 로 venv 에만, 꼭 필요한 것만.
- 전체 시험(`cell-bench/tests`)이 통과해야 끝.

## 기록 · 커밋
- `dynamic-bench/records/2026-10-22-slice-sw.md`: 만든 것 네 가지, 각 시험 결과, 가정 · `[확인 필요]` 목록, 내일 현장에서 실제 데이터로 확인할 것(파서 엔디안/순서, IMU 위치, 동기 이동 모양 제안), 실행 방법 한 줄씩.
- 김건우(저장소 설정 그대로)로 `feature/dynamic-bench` 로컬 커밋, 자기 파일만 `git add <경로>`(절대 `-A`/`.` 금지), `index.lock` 이면 몇 초 뒤 한 번 재시도. **push · PR 금지.**
- 마지막 줄: `DONE results=dynamic-bench/records/2026-10-22-slice-sw.md`

## 추가 (plan-v3 1.3 '내일 슬라이스' 절 반영 — 먼저 그 절을 읽어라)
- plan-v3 1.3 에 따르면 셀 IMU 는 **라이브에 없고 추출(FTG) 파일에만** 있다. 그래서 비교 스크립트의 정렬은 (a) 추출 IMU 가속도 입력(FTG 파서가 작업 트리나 cell-bench 에 있으면 그것으로, 없으면 "시각, 가속도" 표 CSV 를 받는 꼴) 과 (b) 라이브 speed_history 와 모터 속도의 **속도 상호상관** 두 경로를 둔다. 실내에서 GPS fix 가 없으면 그 사실을 보고에 그대로 적는 경로도 둔다.
- 동시 기록용 원시 라이브 저장은 plan-v3 1.3 의 `live_tap.py`(cell-bench `LiveListener` 상속, cell-bench 파일 무수정) 를 이번에 같이 만든다 — 오프라인 시험만(실제 포트를 열어 셀에 붙지 않는다; 가짜 UDP 패킷을 localhost 로 보내는 시험은 된다).
