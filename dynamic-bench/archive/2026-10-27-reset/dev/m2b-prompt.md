# M2 재시도(2회차) 분석과 서보 온 대기 수정 — 오프라인 전용

KimLead /orchestrate 흐름으로 진행한다: kimdeveloper 가 조사·수정하고(ponytail 적용), kimqa 가 시험과 기록을 검증한다.

## 일어난 일 (2026-10-26)
- 18:24 1회차: 드라이브 알람 0xFF1A 로 서보 온 전 거부 (기존 기록 records/2026-10-22-m2.md).
- 사용자가 드라이브 표시 **Err26.0** 을 확인했다. 슬라이더를 왼쪽 Home 끝에 두고 전원 코드를 뽑았다 꽂아 알람을 풀었다. 읽기 전용 scan: statusword 0x0250, err 0, pos −9.
  > **[2026-10-27 대체됨]** 이 줄의 "왼쪽 Home 끝"은 확인되지 않은 표현이다(보는 방향 없음 — 시운전 기록 §2 를 옮긴 말). 방향 · Home 쪽의 정본은 `dynamic-bench/config/axis-motor1.json` (Home 쪽은 아직 미확인).
- 19:08:05 2회차 (Grr 가 같은 승인 값으로 새 승인 파일 · 입회 표지를 만들고 `run_motion.py move` 를 직접 1회 실행). 전체 출력: `C:\Dev\_grr\m2\run2.log`, 주기 기록: `cell-bench/data/motion_m2_20261026-190806.csv`, `_trace.csv`.
  - Pr5.13 이 전원 재투입 뒤 **7400** 으로 읽혔다 (1회차엔 50 — 시운전 임시값으로 추정).
  - OP 진입 정상, 알람 없음, WKC 오류 0, 주기 평균 2.04 ms.
  - 종료 이유: `Enable 0x0F → Operation enabled ? 1.0초 안에 안 됨 (statusword 0x0633)`.
  - 명령하지 않았는데 위치가 −9 → 최저 −1636 → 끝 −427 pulse 로 움직였다. **사용자가 "살짝 꿈적거렸다"고 직접 봤다.**
- 가설 (미확인): 인크리멘털 리니어 모터가 전원 투입 후 첫 서보 온 때 하는 **자극(磁極) 위치 추정** 동작이 1초보다 오래 걸린다.

## 할 일
1. 가설 검증: trace CSV 로 statusword · 위치의 시간 흐름을 표로 정리. PANATERM 파라미터 백업(records/20261021_원복백업.prm5)과 객체 덤프에서 자극 검출 방식 관련 파라미터(Pr9.xx 등)를 찾는다. 전임자 코드(`C:\Users\HW team\SOEM`, 읽기만)가 서보 온을 얼마나 기다렸는지, 설명서 · ESI 가 `C:\Users\HW team\Documents` 에 있으면 근거로 쓴다. 근거가 없으면 `[추정]` 으로 남긴다.
2. 수정(최소): 근거에 맞게 Enable 0x0F 대기 시간을 늘린다. 자극 추정 중의 무명령 움직임은 창(±15 mm) 검사와 자동 Quick stop 규칙이 계속 감시해야 한다(창 밖이면 멈춤). 추종 오차 규칙이 이 움직임을 어떻게 다루는지도 확인한다. 안전 규칙은 약하게 하지 않는다.
3. 장비 없는 시험 추가 · 전체 M1/M2 시험 통과.
4. records/2026-10-22-m2.md 에 "2회차 (19:08)" 절을 추가한다 (Err26 확인, 전원 재투입, Pr5.13 7400, 결과, 원인 판단, 수정).
5. `feature/dynamic-bench` 에 로컬 커밋만 한다.

## 금지
- **장비 실행 금지**: run_motion.py 의 scan · monitor · move 어느 것도 실행하지 않는다. 다음 실행은 사용자가 내일 새로 승인한다.
- push · PR · gh 금지. m2-approval.json · motion_attended 손대지 않기. Pr 값 쓰기 · Fault reset 추가 금지. 시우 폴더(SOEM 등)는 읽기만.

## 끝
`C:\Dev\_grr\m2\fix-summary.md` 에 한국어 요약(원인 판단과 근거, 바꾼 것, 시험 결과, 커밋 해시, 내일 사용자가 할 일)을 쓰고 마지막 줄에 `DONE results=C:\Dev\_grr\m2\fix-summary.md` 를 출력한다.