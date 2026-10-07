# 제어 PC 준비 · 이식

> 한 줄 요지: **연결 방식은 어느 노트북이든 같다 — Wi-Fi 는 LiveHub, 인터넷은 유선, 프로그램은 GitHub 에서 클론.** 다만 아래 다섯 가지는 노트북 안에 저장되는 설정이라 새 노트북에서 한 번씩 다시 한다. 다 했는지는 `python tools/check_env.py` 가 확인하고, 빠진 것마다 고치는 명령을 알려 준다.

## 1. 무엇이 따라오고 무엇을 다시 하나

| 구분 | 항목 | 새 노트북에서 |
|---|---|---|
| **따라온다** (GitHub) | 프로그램 · 설정 기본값 · 도구 · 이 문서 | `git clone` 한 번 |
| **다시 한다** (노트북 안에 저장) | ① Wi-Fi 고정 주소 `192.168.1.100` | 2절 |
| | ② 방화벽: 파이썬이 셀 신호를 받도록 허용 | 3절 |
| | ③ 플러그 계정 (Windows 자격 증명 관리자) | 4절 |
| | ④ 무인 운전 전원 설정 (절전 · 덮개) | 5절 |
| | ⑤ 파이썬과 패키지 | 6절 |
| **옮긴다** (선택) | `cell-bench/data/` — 지난 사이클 기록 · 셀 파일 | 7절 |

**왜 "그냥 연결"만으로는 안 되나.** 셀은 정해진 주소 `192.168.1.100` 으로만 신호를 보낸다. 새 노트북이 LiveHub 에 자동으로 받은 다른 주소로 붙으면, 연결은 되어 보여도 셀 신호가 한 대도 오지 않는다(2026-10-06 에 실제로 겪었다). 방화벽도 같다 — 막혀 있으면 오류 없이 조용히 0대다.

## 2. Wi-Fi 고정 주소 (관리자 PowerShell)

```powershell
netsh interface ipv4 set address name="Wi-Fi" static 192.168.1.100 255.255.255.0
netsh wlan set profileparameter name="FTG-3D93-5G" connectionmode=auto
```

- 게이트웨이는 **비운다.** 넣으면 인터넷을 시험망 쪽에서 찾다가 끊긴다. 인터넷은 유선(USB-이더넷)으로.
- 인터페이스 이름이 "Wi-Fi" 가 아니면 `Get-NetAdapter` 로 확인해 바꾼다.
- 그 노트북을 다른 곳에서 쓸 때 되돌리기: `netsh interface ipv4 set address name="Wi-Fi" dhcp`

## 3. 방화벽 (관리자 PowerShell)

처음 실행할 때 Windows 가 "액세스 허용" 창을 띄우면 **공용·개인 모두 허용**을 누른다. 창이 안 떴는데 셀이 0대면:

```powershell
New-NetFirewallRule -DisplayName "cell-bench python" -Direction Inbound -Program (Get-Command python).Source -Action Allow -Profile Any
```

## 4. 플러그 계정

```bash
python tools/plug_cli.py setup
```

입력 창에 TP-Link 계정을 넣는다. 값은 그 노트북의 자격 증명 관리자에만 저장되고 화면·기록에 찍히지 않는다. 플러그 쪽은 바꿀 것이 없다(Tapo 앱의 Third-Party Compatibility 는 플러그에 남아 있다).

## 5. 무인 운전 전원 설정 (관리자 PowerShell)

```powershell
powercfg /change standby-timeout-ac 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT
```

- 첫 줄: 전원 연결 시 절전 안 함. 둘째·셋째 줄: 전원 연결 시 **덮개를 닫아도 계속 동작.**
- 노트북 충전기는 항상 연결. 배터리로 밤을 새우면 꺼지고, 그때 플러그가 꺼진 상태로 남으면 셀이 방전돼 꺼진다.
- 잠금(Win+L)은 괜찮다. 절전·종료·재시작만 시험을 멈춘다.

## 6. 프로그램

```bash
git clone https://github.com/GeonwooKim-fitogether/QA-Automation-Test-Room.git
cd QA-Automation-Test-Room/cell-bench
pip install -r requirements.txt
python tools/check_env.py
```

`check_env.py` 가 모두 ✓ 면 준비 끝이다. 이어서 `python serve_board.py` 와 `python run_cycle.py --cycles N`.

## 7. 기록 옮기기 (선택)

이어서 같은 시험을 계속하려면 옛 노트북의 `cell-bench/data/` 를 통째로 복사한다. `cycles.csv` 의 줄 수로 다음 사이클 번호를 정하므로, 복사하면 번호가 이어지고 결과판 추이도 이어진다. 사이클마다 셀 파일이 약 0.5 GB 쌓이니 디스크 여유를 본다.

## 이 노트북이 개인 PC 라서 정해야 할 것

| 항목 | 지금 | 이식할 때 정할 것 |
|---|---|---|
| 플러그 TP-Link 계정 | 등록한 사람의 계정 | 팀 공용 계정으로 다시 등록할지 (다시 등록하면 Third-Party Compatibility 도 다시 켠다) |
| 시험 기록 · 셀 파일 | 노트북 디스크 `cell-bench/data/` | 회사 저장소(공유 드라이브 등)로 둘지 · 셀 원본 파일 보관 기간 |
| 저장소 접근 | 개인 GitHub 계정의 저장소 | 팀 조직으로 옮길지 |
