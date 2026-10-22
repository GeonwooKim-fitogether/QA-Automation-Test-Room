# dynamic-bench — 리니어 모터 기반 동적 QA 테스트 시스템

QA-Automation-Test-Room 셀 세트(스포츠 웨어러블 센서 기기)를 리니어 스테이지 위에 올려 움직이면서,
정적 시험만으로는 볼 수 없는 **움직이는 상태에서의 셀 내구성과 데이터**를 확인하기 위한 시스템입니다.

- 구동부: Panasonic MINAS-A6BL LINEAR 드라이브(MBDLN25BL) + 리니어 모터, EtherCAT 연결
- 제어 PC: 3 QA 제어 PC 2 (DELLXPS15-PCW, Windows 11)
- 운영 방식: 봇(Claude Code)이 시스템을 돌리고 결과를 보고하며, 사람은 감독·검토·결정만 합니다.
- 제어 프로그램: 새로 작성합니다. 전임자(조시우)의 SOEM 기반 프로그램은 하드웨어 참고 자료로만 읽고, 기반 코드로 쓰지 않습니다.

## 폴더

| 경로 | 내용 |
|---|---|
| `records/` | 시운전·점검 기록, 드라이브 파라미터 백업(.prm5) |
| `plan/` | 기획 문서 (plan-v1.md 부터) |
| `plan-prompt.md` | 기획 세션(Claude Code, kimpm)에 준 지시문 |

## 안전 원칙

모터 구동(서보 온, OP 전환, Controlword 송신)은 **사용자가 현장에 있을 때만** 합니다.
