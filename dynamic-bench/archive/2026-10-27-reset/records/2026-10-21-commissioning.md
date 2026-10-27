# 2026-10-21~22 리니어 모터 시운전 기록

> 결론: 드라이브는 EtherCAT으로 PC에 연결되어 SOEM slaveinfo에서 슬레이브 1대로 정상 인식된다(SAFE-OP).
> 스케일은 약 1 pulse = 1 µm, 실측 사용 가능 범위는 +687,339 ~ -1,080,400 pulse(약 1768 mm)이다.
> 이 기록 작성 시점까지 새 프로그램으로 모터를 움직인 적은 없다.

## 1. 하드웨어

| 항목 | 값 |
|---|---|
| 드라이브 | Panasonic MINAS-A6BL LINEAR, 형식 MBDLN25BL, 시리얼 25010217, EtherCAT 사양 |
| 설정용 연결 | USB (드라이브 X1) — PANATERM 설정 전용 |
| 제어용 연결 | EtherCAT — Realtek USB GbE 어댑터, 링크 100 Mbps |
| 패킷 드라이버 | Npcap 1.81 |
| Npcap 장치명 | `\Device\NPF_{6D0E574A-8D90-4065-9679-6A2DBF525343}` |
| 제어 PC | 3 QA 제어 PC 2 (DELLXPS15-PCW), Windows 11 |

## 2. 스케일과 원점

- 약 **1 pulse = 1 µm**.
- 인크리멘털 방식이다. **위치 0 = 전원 투입 시 슬라이더가 있던 자리**이다.
- 운용 절차: 전원 투입 **전에** 스테이지를 왼쪽 Home 끝에 놓는다.
  > **[2026-10-27 대체됨]** 이 줄의 "왼쪽 Home"은 확인되지 않은 표현이다 — 어느 쪽에서 본 왼쪽인지 적혀 있지 않다. 아래 3절과 같은 쪽(케이블 쪽)에서 봤다면 + = 왼쪽이므로 + 가 Home 쪽으로 간다는 뜻이 되어, 조시우 매뉴얼(Home 에서 양수로 움직임, plan-v1 2.2절)과 어긋난다. 방향 · Home 쪽의 정본은 `dynamic-bench/config/axis-motor1.json` (Home 쪽은 아직 미확인).

## 3. 방향

케이블 쪽이 사용자를 향하게 놓고 볼 때:

| 부호 | 방향 | 이름 |
|---|---|---|
| + | 왼쪽 | MAX |
| - | 오른쪽 | MIN |

## 4. 실측 사용 가능 범위

| 끝 | 위치 [pulse ≈ µm] |
|---|---|
| MAX | +687,339 |
| MIN | -1,080,400 |
| 폭 | 약 1,767,739 (≈ 1768 mm) |

전체 스트로크는 약 1770 mm. 소프트 리미트는 이 범위 안쪽으로 여유를 두고 정해야 한다(값은 미정).

## 5. Err26.0 (과속도) 이력과 조치

- 현상: PANATERM 시운전(trial run) 중 Err26.0 과속도 보호 발생.
- 원인: 시운전 모드의 과속도 레벨이 **10 mm/s로 자동 설정**된 상태에서 그보다 빠르게 움직였다.
- 확인: **50 mm/s 설정에서는 정상 동작**했다.
- 복구: Err26은 알람 해제만으로 안 풀렸고 **드라이브 전원을 껐다 켜야** 했다.

## 6. 파라미터 백업

- 원본: `C:\Users\HW team\Documents\Panasonic Industry\PANATERM\prm\20261021_원복백업.prm5` (UTF-16)
- 사본: 이 폴더의 `20261021_원복백업.prm5`

## 7. SOEM slaveinfo 결과 (2026-10-22 15:06 KST)

| 항목 | 값 |
|---|---|
| 슬레이브 수 | 1 |
| 이름 | MBDLN25BL |
| Manufacturer | 0x066F |
| Product ID | 0x60540006 |
| Revision | 0x00010000 |
| DC (분산 클럭) | 지원 |
| 상태 | 4 (SAFE-OP) |

### SM2 출력 PDO (마스터 → 드라이브), 9 byte

| Index | 이름 | 형식 |
|---|---|---|
| 0x6040 | Controlword | u16 |
| 0x6060 | Modes of operation | i8 |
| 0x607A | Target position | i32 |
| 0x60B8 | Touch probe function | u16 |

### SM3 입력 PDO (드라이브 → 마스터), 23 byte

| Index | 이름 | 형식 |
|---|---|---|
| 0x603F | Error code | u16 |
| 0x6041 | Statusword | u16 |
| 0x6061 | Modes of operation display | i8 |
| 0x6064 | Position actual value | i32 |
| 0x60B9 | Touch probe status | u16 |
| 0x60BA | Touch probe pos1 pos value | i32 |
| 0x60F4 | Following error actual value | i32 |
| 0x60FD | Digital inputs | u32 |

(0x603F·0x60B9 u16, 0x60FD u32 는 표준 CiA402 형식 기준. 합계 2+2+1+4+2+4+4+4 = 23 byte, 출력 2+1+4+2 = 9 byte로 slaveinfo 크기와 일치)

## 8. 참고 자료 (읽기 전용)

- 전임자 조시우의 SOEM 기반 프로그램: `C:\Users\HW team\SOEM` (eram8s.exe, test\win32\slaveinfo, simple_test). **새 프로그램의 기반으로 쓰지 않는다.**
- 매뉴얼: `C:\Users\HW team\Documents\Linear motor 유저 매뉴얼.pptx`, `Linear motor and FTG reporter.pptx`
- 벤더 매뉴얼·ESI xml: `C:\Users\HW team\Fitogether\Research Collaboration - 문서\FIFA_FPP\Linear Motor\`
- ENI 파일: `C:\eni`
- WMX3 3.6u1 SoftServo: `C:\Program Files\SoftServo\WMX3` (라이선스 상태 미확인)
