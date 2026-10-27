# M2 개발 지시문 — 리니어 모터 입회 10 mm 왕복 (2026-10-22, 김건우 승인 후에만 실행)

**스킬:** `ponytail` 스킬 적용 — 동작하는 가장 단순한 해법. M2 에 필요 없는 것(패턴 · run 모드 · 결과판 · 다축)은 만들지 않는다.

## 할 일
M1 코드(`cell-bench/motionbench/`, `cell-bench/run_motion.py`, 기록 `dynamic-bench/records/2026-10-22-m1.md`) 위에 **M2 = 사용자 입회 하 10 mm 왕복**을 더하고, 사용자가 옆에 있는 지금 **한 번** 실제로 돌려 기록한다.
먼저 읽을 것: `dynamic-bench/records/2026-10-22-m1.md`(M1 결과 · SDO 표 · 시리얼 · 주기 통계), `dynamic-bench/plan/plan-v1.md` 2.4–2.6절 · 3.3절(PP 새 목표 핸드셰이크 — 조시우가 "없으면 치명적 알람"이라 한 부분) · M2 행, `dynamic-bench/plan/plan-v2.md` 4.2(입회 표지) · 5.1 M2 행, `dynamic-bench/plan/plan-v3.md` 8절 M2 행.

구현 (M2 에 필요한 것만):
- `run_motion.py move` 모드: **Profile Position(0x6060 = 1)**, 시작 시 실제 위치 `p0` 기준 **상대 10 mm 왕복**을 승인 파일의 `cycles` 회.
- `motionbench/safety.py`(순수 + pytest): 승인 파일 검증, 소프트 창(`p0 ± window_mm`) 검사, 목표가 창 밖이면 **전송 없이 거부**, 속도/가속 상한 검사, 정지 거리 보정, 자동 Quick stop 규칙.
- 입회 표지 검사(순수 + pytest), Quick stop 경로.

## 승인 파일 — 값은 코드에 박지 않는다
- `dynamic-bench/dev/m2-approval.json` (사용자 승인 내용으로 다른 쪽이 만든다. **너는 이 파일을 쓰거나 고치지 않는다**). 꼴:
  `{"approved_by": "김건우", "approved_at": "<ISO8601 +09:00>", "first_move_dir": 1 또는 -1 (펄스 카운트 부호), "distance_mm": 10, "window_mm": 15, "vmax_mm_s": 10, "amax_mm_s2": 50, "cycles": 3}`
- 코드 안 **절대 상한**(승인 파일이 넘으면 거부): distance ≤ 10 mm, window ≤ 15 mm, vmax ≤ 20 mm/s, amax ≤ 100 mm/s², cycles ≤ 5. 파일이 없거나 깨졌거나 필드가 빠지면 거부.
- 스케일 1 pulse = 1 µm 은 M1 기록·시운전 기록에서 확인. 0x6081/0x6083/0x6084 의 단위(전자 기어 0x608F/0x6091/0x6092 등)를 **읽어서** 확인하고, 확신이 없으면 움직이지 않고 기록 후 끝낸다.
- 과속도 레벨 Pr5.13(시운전 때 PANATERM 10 mm/s 에서 Err26 이 났다)을 SDO 로 **읽어** vmax 가 그 아래임을 확인. 확인 못 하면 움직이지 않는다.

## 입회 표지 — 없으면 서보 온 거부
- `cell-bench/data/motion_attended` (JSON `{"by": "...", "reason": "...", "created": <epoch>, "until": <epoch>}`) — 사용자가 승인할 때 다른 쪽이 만든다. **너는 이 파일을 만들거나 고치지 않는다.**
- 유효 조건: JSON 이 정상, `until` 이 미래이고 created 로부터 4시간 이내, `created` 가 60분 이내(신선함). 못 읽으면 **무효**(cell-bench pause_state 의 "못 읽어도 유효" 규칙을 쓰지 않는다).
- 서보 온 직전과 매 이동 직전에 다시 검사. 무효면 서보 온 거부 / Quick stop. "표지 없이 서보 온 시도가 거부됨"(v2 M2 ⑧)은 pytest 로 증명하고, 실제 실행 시작 로그에 표지 검사 결과를 남긴다.

## 안전 규칙 (어기면 즉시 Quick stop → 종료)
이 축에는 리미트 스위치 · 비상정지가 없다. 사용자는 전원 스위치 옆에 있다.
1. 서보 온은 승인 파일 + 유효 표지 + M1 상태 검사(슬레이브 1대, 시리얼 일치 = M1 기록값, 0x603F = 0, fault 없음) 모두 통과 후에만, CiA402 정상 전이(Shutdown 0x06 → Switch on 0x07 → Enable 0x0F)로. 시작 시 fault 가 있으면 **Fault reset 하지 말고** 기록 후 종료.
2. 서보 온 직전 Target position = 실제 위치(점프 금지). PP 새 목표는 v1 3.3 핸드셰이크대로(bit4 new set-point, bit5 change immediately 정책 명시, set-point acknowledge 확인).
3. 허용 SDO **쓰기**는 RAM 값인 0x6081 Profile velocity, 0x6083 Profile acceleration, 0x6084 Profile deceleration, 0x6085 Quick stop deceleration, 0x607D:01/02 Software position limit(= 창) 뿐. **0x1010 저장 · EEPROM · PANATERM 파라미터(Pr) 쓰기 금지.** 쓴 뒤 읽어서 확인.
4. 매 주기 자동 Quick stop(Controlword bit2 = 0): WKC 불일치 3주기 연속, 주기 지연 50 ms 초과, fault 비트, 0x603F ≠ 0, 추종 오차 0x60F4 가 임계(예: 2 mm `[제안값]`) 초과, 실제 위치가 창 밖, 표지 무효.
5. **Quick stop 경로 검증:** 왕복 중 한 번, 이동 도중 프로그램이 스스로 Quick stop 을 걸어 정지까지 위치 변화를 잰다(≤ 5 mm 기대). 그 뒤 정상 복귀 이동은 창 안에서만.
6. 종료는 항상(예외 · Ctrl+C 포함): Quick stop → Shutdown(0x06) → Controlword 0 → SAFE-OP → INIT → 어댑터 닫기. 끝난 뒤 서보가 꺼졌는지 statusword 로 확인.
7. 실제 하드웨어 실행은 **한 번만**. 실패하면 재시도하지 말고 기록 후 끝낸다(알람이면 코드 · 상태 기록). 사용자가 다시 결정한다.
8. 케이블을 뽑는 통신 두절 시험(v1 M2 ⑥)과 자로 이동량 재기(②)는 사람의 일 — 너는 하지 않고 기록에 "사용자 몫/미실시"로.

## 파일 규칙
- 기존 cell-bench 파일은 고치지 않는다(M1 이 만든 motionbench/run_motion.py 확장은 된다). `run_cycle.py`, `cycle.py`, `cells.py`, `cloud.py`, `supabase/` 절대 금지. 조시우 폴더 · 메인 체크아웃은 읽기만.
- Python 은 `.venv/Scripts/python.exe` 만, 작업 트리 루트에서 경로 명시.
- 원시 로그 · `data/` 는 커밋하지 않는다.

## 기록
`dynamic-bench/records/2026-10-22-m2.md`: 실행 시각(KST), 승인 파일 내용, 표지 검사 결과, 쓴 SDO 와 읽어 확인한 값, 각 이동의 목표 · 도달 위치 · |pos − target| · 최대 추종 오차 · 최고 속도, Quick stop 시험 정지 거리, 0x603F 전 구간, 주기 통계, v1 M2 ①–⑦ + v2 ⑧ 각각의 결과(사람 몫은 미실시로), 이상 · 중단.

## 커밋
김건우(저장소 설정 그대로)로 `feature/dynamic-bench` 로컬 커밋, 자기 파일만 `git add <경로>`(절대 `-A`/`.` 금지), `index.lock` 이면 몇 초 뒤 한 번 재시도. **push · PR 금지.**
마지막 줄: `DONE results=dynamic-bench/records/2026-10-22-m2.md`
