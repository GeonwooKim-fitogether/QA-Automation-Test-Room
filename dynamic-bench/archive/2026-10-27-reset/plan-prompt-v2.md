# 기획 지시문 v2 — cell-bench 위에 리니어 모터 층을 얹는 dynamic-bench (plan-v2)

너는 이전 턴에 이어 **kimpm** 으로서 이 작업을 맡는다. 작업 폴더 `C:\Dev\dynamic-bench` (브랜치 `feature/dynamic-bench`).

## 가장 큰 정정

plan-v1 은 같은 저장소의 기존 시스템 **`cell-bench/`** 를 무시했다. 사용자 결정: **dynamic-bench 는 cell-bench 위에 짓는다.**
cell-bench 의 셀 통신, supervisor, board(모니터링 화면), Slack 알림, Supabase 클라우드, 세트 등록, health 표시등을 **그대로 재사용**하고, **리니어 모터 제어만 새 층으로 더한다.** ponytail 방식 — 새로 쓰기 전에 재사용부터, 바꿀 것은 최소로.

## 0. 절대 규칙 (v1 과 같다, 어기면 실패)

1. 모터를 움직이지 않는다. 서보 온 금지. **어떤 .exe·프로그램·스크립트도 실행하지 않는다** (cell-bench 의 python 스크립트 포함). EtherCAT 통신·Controlword 송신·OP 전환 금지. 이 세션은 **파일을 읽고 문서를 쓰는 일만** 한다.
2. 조시우의 폴더(`C:\Users\HW team\SOEM`, `Project2504`, `jbshin`, `Fitogether\...`, `C:\eni`, Documents pptx)는 읽기만 한다. 조시우의 토큰·자격 증명 사용 금지.
3. git 은 로컬 커밋만. push·PR 금지. `C:\Dev\QA-Automation-Test-Room` 원본은 건드리지 않는다.
4. cell-bench 코드는 이번에 **고치지 않는다.** 바꿔야 할 것은 계획서에 "변경 목록"으로만 적는다.
5. 요구사항을 지어내지 않는다. 모르는 것은 질문으로, 매뉴얼에서 확인 못한 것은 `확인 필요` 로 표시한다.

## 1. 먼저 읽을 것 (워크트리 전체를 읽어도 된다)

- `cell-bench/README.md`, `cell-bench/docs/system-map.md`, `cell-bench/docs/handover.md`, `cell-bench/docs/control-pc-setup.md`, `cell-bench/docs/remote-access.md`, `cell-bench/docs/design/`
- `cell-bench/cellbench/*.py` (특히 supervisor, cells, protocol, record, health, alert, cloud, register, bench, config, net), `cell-bench/supervise.py`, `run_cycle.py`, `serve_board.py`, `bench.example.json`, `requirements.txt`
- `cell-bench/board/`, `cell-bench/supabase/`, `cell-bench/tests/`
- `docs/system-layer/`, `tools/integration-board/`
- 기존 자료: `dynamic-bench/records/2026-10-21-commissioning.md`, `dynamic-bench/plan/plan-v1.md` (v1 의 하드웨어 사실·안전 분석은 유효한 부분을 가져오되, 구조는 다시 짠다)

## 2. 사용자 결정 (2026-10-22) — 전부 반영할 것

1. **목표 세 가지, 이 순서로**
   1. 움직이는 동안 셀 데이터의 **정확도** — 모터 위치·속도를 정답(ground truth)으로 두고 셀 측정값과 비교
   2. 장시간 반복 운동에서의 **내구성**
   3. 출하·생산 QA **합격/불합격** — 기준은 나중에 1·2의 데이터에서 도출 (지금 기준을 지어내지 않는다)
2. 최종적으로 cell-bench 같은 **모니터링 시스템**과, cell-bench 처럼 **24시간 무인 운전**이 필요하다.
3. **단위 = 모듈.** 1 모듈 = 리니어 모터 1대 + 셀 24개 + 도크 1개 (조립된 세트를 모터 위에 올림). 목표 5 모듈 = 모터·드라이브 5대 (지금 1대, 4대 추가 조달 예정). **PC 한 대가 EtherCAT 한 줄에 데이지체인으로 연결된 드라이브 최대 5대를 제어**하도록 설계하고, cell-bench 의 **세트 개념을 모터까지 포함하도록 확장**한다.
4. **안전**: 물리 안전장치가 생기기 전에는 **사용자 입회 시에만 구동**. 사용자는 기계 개발자이며 하드웨어를 직접 만든다 — 드라이브에 배선하는 리미트 센서, 드라이브 STO 에 배선하는 비상정지, 가드·커버, 이후 모듈별 알루미늄 프로파일 랙. 계획서에 **사용자가 만들어야 할 하드웨어 요구 목록**(센서 종류, 드라이브의 배선 지점(커넥터·핀), 장착 위치)을 명확히 적고, 매뉴얼에서 확인 못한 세부는 `확인 필요` 로 표시한다.
5. **WMX3 는 쓰지 않는다. 라이선스 구매 없음.** 통신 엔진 = pysoem(무료). "pysoem 은 EtherCAT 통신만 하고, 그 위에 우리 프로그램이 앉는다"는 것을 쉽게 설명한다.
6. 노트북의 Python 은 **3.10** 이다 (3.11 아님). cell-bench 의 `requirements.txt`·코드가 3.10 과 호환되는지 확인한다 (파일을 읽어서; 실행 금지).
7. 네트워크: cell-bench 는 PC Wi-Fi 가 LiveHub `FTG-3D93-5G` 에 고정 IP `192.168.1.100` 으로 붙고, 인터넷은 유선 Ethernet 으로 받는 구조다. 이 노트북은 그 Ethernet(USB GbE 어댑터)을 지금 EtherCAT 에 쓴다. 인터넷·Supabase·Slack 을 어떻게 닿게 할지 계획하고, 불분명하면 질문으로 남긴다.

## 3. 산출물

`dynamic-bench/plan/plan-v2.md` 하나 (v1 은 그대로 둔다). **한국어로만, 결론 먼저, mermaid 중심.** 반드시 답할 것:

1. **아키텍처 다이어그램 (mermaid)** — cell-bench 에서 재사용하는 모듈과 새 모터 층을 색·묶음으로 구분. pysoem 위에 우리 프로그램이 있다는 층 구조도 포함. 5 드라이브 데이지체인 구성도.
2. **시간 정렬** — 모터 위치 로그(PDO 주기)와 셀 UDP 실시간 데이터를 어떻게 같은 시간축에 맞추는가 (cell-bench 의 기존 타임스탬프 방식을 근거로).
3. **supervisor / board / health 표시등의 확장** — 모터 상태(서보, 알람, 위치, 통신)를 어떻게 얹는가. 세트 등록에 모터를 넣는 방법.
4. **수정된 마일스톤** — M1 읽기 전용 유지, M2 입회 하 10 mm·≤ 20 mm/s 유지, M3·M4 를 cell-bench 기준으로 다시 정의, 그 뒤에 다중 모듈 단계와 24시간 무인 운전 단계(물리 안전장치 완비가 조건). 각 단계에 측정 가능한 통과 기준.
5. **cell-bench 코드 변경 목록 vs 새 코드 목록** — 파일 단위로, 최소 변경. 재사용이 먼저.
6. **사용자 하드웨어 요구 목록** (2절 4번).
7. **남은 열린 질문** (짧게) 과 **결정 필요** 항목.

## 4. 마무리

1. `git add dynamic-bench/plan/plan-v2.md dynamic-bench/plan-prompt-v2.md` (지시문이 이미 커밋돼 있으면 계획서만) 후 로컬 커밋. 메시지 예: `docs(dynamic-bench): cell-bench 위 리니어 모터 층 기획 v2`. push 금지.
2. 마지막 줄을 정확히:

DONE results=dynamic-bench/plan/plan-v2.md
