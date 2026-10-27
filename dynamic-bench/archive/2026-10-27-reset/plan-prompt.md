# 기획 지시문 — 리니어 모터 EtherCAT 새 제어 프로그램 (plan-v1)

너는 **kimpm** 에이전트로서 이 작업을 맡는다. 작업 폴더는 `C:\Dev\dynamic-bench` (git worktree, 브랜치 `feature/dynamic-bench`)이다.

## 0. 절대 규칙 (어기면 작업 실패)

1. **모터를 절대 움직이지 않는다. 서보 온도 하지 않는다.** `eram8s.exe`, `simple_test.exe`, `slaveinfo.exe` 를 포함해 **어떤 .exe 도 실행하지 않는다.** EtherCAT 통신, Controlword 송신, OP 상태 전환을 일으키는 어떤 프로그램·스크립트도 실행하지 않는다. 이 세션은 **파일을 읽고 문서를 쓰는 일만** 한다.
2. 조시우(퇴사한 전임자)의 폴더와 파일은 **읽기만** 한다. 수정·이동·삭제·새 파일 생성 금지:
   `C:\Users\HW team\SOEM`, `Project2504`, `jbshin`, `C:\Users\HW team\Fitogether\...`, `C:\eni`, `C:\Users\HW team\Documents` 의 pptx.
   조시우의 토큰·자격 증명은 어떤 것도 쓰지 않는다.
3. git 은 **로컬 커밋만** 한다. `git push` 금지, PR 생성 금지. `C:\Dev\QA-Automation-Test-Room` 원본 체크아웃은 건드리지 않는다.
4. 요구사항을 지어내지 않는다. 모르는 것은 "사용자에게 물을 질문"으로 적는다.

## 1. 배경

- 목적: QA-Automation-Test-Room 셀 세트(스포츠 웨어러블 센서 기기)를 리니어 스테이지에 올려 움직이며, 정적 시험을 넘어 **움직이는 상태의 셀 내구성과 데이터**를 확인하는 서보 기반 동적 QA 테스트 시스템.
- 운영 방식: 봇(Claude Code)이 시스템을 돌리고 결과를 보고한다. 사용자(김건우)는 감독만 한다 — 검토하고 결정한다.
- 사용자 결정: 조시우의 기존 코드를 기반으로 쓰지 않는다. **새 프로그램을 작성**하고, 조시우의 자료는 하드웨어 사실을 확인하는 읽기 전용 참고 자료로만 쓴다.

## 2. 먼저 읽을 것

1. `C:\Dev\dynamic-bench\dynamic-bench\records\2026-10-21-commissioning.md` — 오늘까지 검증된 사실(드라이브, 스케일, 방향, 범위, Err26, slaveinfo·PDO 표). **이 파일의 사실이 최우선 기준이다.**
2. `C:\Users\HW team\SOEM` — 조시우의 SOEM 기반 C 프로그램 소스(git 체크아웃). eram8s 관련 소스, `test\win32\slaveinfo`, `simple_test`. 기능: 초기화 "I", P 명령 "P,pos,vel,acc,dec"(mm 단위), 반복, `command_*.txt` 루틴, Enter = 비상정지, csv 로그(10~30 ms 불규칙 기록, Enter 확인 스레드 문제 있음).
3. `C:\Users\HW team\Documents\Linear motor 유저 매뉴얼.pptx`, `C:\Users\HW team\Documents\Linear motor and FTG reporter.pptx` (pptx 는 `unzip -p <파일> 'ppt/slides/*.xml'` 처럼 표준출력으로만 읽는다. 압축을 디스크에 풀지 않는다.)
4. `C:\Users\HW team\Fitogether\Research Collaboration - 문서\FIFA_FPP\Linear Motor\` — 벤더 매뉴얼, ESI xml.
5. 참고로 존재만 알아 둘 것: `C:\eni` (ENI 파일), `C:\Program Files\SoftServo\WMX3` (WMX3 3.6u1 SoftServo, 라이선스 상태 미확인).

## 3. 만들 산출물

`C:\Dev\dynamic-bench\dynamic-bench\plan\plan-v1.md` 하나. **한국어로만**, **결론 먼저**, **mermaid 다이어그램 중심**으로 쓴다. 다음을 모두 담는다.

1. **권장 아키텍처와 언어·라이브러리 선택** — 새 마스터를 무엇으로 만들지(예: SOEM(C) vs pysoem(Python) vs WMX3) 비교하고 이유를 댄다. Windows + Npcap 환경의 주기 타이밍 한계(지터, USB 어댑터, DC 동기화 영향)를 사실과 추정을 구분해 적는다. 구성도는 mermaid 로.
2. **안전 설계를 가장 먼저** — 실측 범위(MAX +687,339 / MIN -1,080,400 pulse) 안쪽에 여유를 둔 소프트 리미트, 최대 속도·가속도 상한, 비상정지, 워치독과 통신 두절 시 동작, 전원 투입 시 원점(인크리멘털이라 0 = 전원 투입 위치, 절차상 왼쪽 Home 끝) 처리, Err26 과속도 이력. 제안 수치는 "제안값"으로 표시하고 근거를 단다. 상태 전이는 mermaid stateDiagram 으로.
   > **[2026-10-27 대체됨]** 이 줄의 "절차상 왼쪽 Home 끝"은 확인되지 않은 표현이다(시운전 기록 §2 를 보는 방향 없이 옮긴 것). 방향 · Home 쪽의 정본은 `dynamic-bench/config/axis-motor1.json` (Home 쪽은 아직 미확인).
3. **단계별 마일스톤과 통과 기준** — 반드시 다음 순서로 시작한다.
   - M1: EtherCAT 으로 상태를 **읽기만** 하는 모니터링 (서보 온 없음)
   - M2: 사용자 입회 하에 서보 온 + 10 mm 이동, 속도 ≤ 20 mm/s
   - M3: 반복 패턴 + 고정 주기 로깅
   - M4: 셀 세트용 시험 루틴
   각 단계마다 측정 가능한 통과 기준, 사용자 입회 필요 여부, 다음 단계로 넘어가는 조건을 적는다.
4. **조시우 자료에서 사실로 재사용할 것 vs 버릴 것** — PDO 매핑, 초기화 순서, 함정(불규칙 로깅, Enter 확인 스레드 등)을 출처 파일 경로와 함께 정리한다. 코드는 복사하지 않는다.
5. **사용자만 답할 수 있는 열린 질문** — 짧은 목록으로. 시험 동작 패턴, 속도, 반복 횟수, 셀과 동기화할 데이터 등.

## 4. 마무리

1. `git add dynamic-bench/plan/plan-v1.md` 후 로컬 커밋한다 (메시지 예: `docs(dynamic-bench): 리니어 모터 EtherCAT 제어 기획 v1`). push 하지 않는다.
2. 마지막 줄을 정확히 이렇게 출력한다:

DONE results=dynamic-bench/plan/plan-v1.md
