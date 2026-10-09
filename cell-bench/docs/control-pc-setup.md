# 제어 PC 준비 · 이식

> 한 줄 요지: **연결 방식은 어느 노트북이든 같다 — Wi-Fi 는 LiveHub, 인터넷은 유선, 프로그램은 GitHub 에서 클론.** 다만 아래 다섯 가지는 노트북 안에 저장되는 설정이라 새 노트북에서 한 번씩 다시 한다. 그중 운영체제 쪽(업데이트 재시작 · 절전 · 블루투스 · 자동 로그온 · 방화벽 · 감시자 작업)은 관리자 PowerShell 에서 `tools\install.ps1` 한 번으로 끝난다(1-1절). 다 했는지는 `python tools/check_env.py` 가 확인하고, 빠진 것마다 고치는 명령을 알려 준다.

## 1. 무엇이 따라오고 무엇을 다시 하나

| 구분 | 항목 | 새 노트북에서 |
|---|---|---|
| **따라온다** (GitHub) | 프로그램 · 설정 기본값 · 도구 · 이 문서 | `git clone` 한 번 |
| **다시 한다** (노트북 안에 저장) | ① Wi-Fi 고정 주소 `192.168.1.100` | 2절 |
| | ② 방화벽: 파이썬이 셀 신호를 받도록 허용 | 1-1절 스크립트 (손으로는 3절) |
| | ③ 플러그 계정 (Windows 자격 증명 관리자) | 4절 |
| | ④ 운영체제 고정: 업데이트 재시작 · 절전 · 덮개 · 블루투스 · 자동 로그온 · 감시자 작업 | 1-1절 스크립트 (손으로는 5절, 일부만) |
| | ⑤ 파이썬과 패키지 | 6절 |
| **옮긴다** (선택) | `cell-bench/data/` — 지난 사이클 기록 · 셀 파일 | 7절 |

**왜 "그냥 연결"만으로는 안 되나.** 셀은 정해진 주소 `192.168.1.100` 으로만 신호를 보낸다. 새 노트북이 LiveHub 에 자동으로 받은 다른 주소로 붙으면, 연결은 되어 보여도 셀 신호가 한 대도 오지 않는다(2026-10-06 에 실제로 겪었다). 방화벽도 같다 — 막혀 있으면 오류 없이 조용히 0대다.

## 1-1. 한 번에 설정 — 관리자 PowerShell 에서 `tools\install.ps1`

**운영체제 쪽 설정은 스크립트 한 번으로 끝난다. 먼저 `-WhatIf` 로 무엇이 바뀌는지 보고, 그다음 적용하고, `check_env.py` 로 다시 확인한다.** 이 설정들이 필요한 이유는 실제 사고에 있다. 10-08 21:15 에 Windows 업데이트가 "사용 시간(0~18시) 밖"이라며 PC 를 재시작했고, 시험 프로그램이 사라지면서 플러그가 꺼진 채로 남아 셀이 완전히 방전됐다. 10-07 에는 PC 블루투스 광고가 같은 칩을 쓰는 Wi-Fi 를 끊었다.

```powershell
cd <저장소 폴더>\cell-bench
powershell -ExecutionPolicy Bypass -File tools\install.ps1 -WhatIf    # 보기만 — 관리자가 아니어도 된다
powershell -ExecutionPolicy Bypass -File tools\install.ps1            # 적용 — 관리자 PowerShell 에서
python tools\check_env.py                                             # 다시 확인
```

- **관리자 PowerShell 여는 법:** 시작 → "PowerShell" 검색 → 오른쪽 클릭 → 관리자 권한으로 실행.
- **`-ExecutionPolicy Bypass -File` 을 붙이는 이유:** Windows 의 기본 실행 정책(Restricted — 스크립트 파일 실행을 막는 기본값)에서도 이 한 번만 실행되게 하려는 것이다. 실행 정책 설정 자체는 바꾸지 않는다.
- 항목마다 바꾸기 전 값과 바꾼 뒤 값을 보여 주고, 끝에 요약 표(항목 · 이전 · 지금 · 결과)를 낸다. 일부만 하려면 `-Only pause,power` 처럼 항목 이름을 쉼표로 준다.

### 무엇을 바꾸나 · 되돌리는 법

| `-Only` 이름 | 무엇을 바꾸나 | 막는 고장 | 되돌리는 법 |
|---|---|---|---|
| `policy` | 업데이트 정책 `NoAutoRebootWithLoggedOnUsers=1` — 로그온한 사용자가 있으면 업데이트 뒤 자동 재시작하지 않는다. 만료가 없다. Microsoft 문서가 이 정책은 자동 업데이트 방식(`AUOptions`)이 4(자동 다운로드·예약 설치)일 때만 적용된다고 적기 때문에 `AUOptions=4` 도 함께 쓴다. `-NoPolicy` 로 건너뛴다 | 업데이트 자동 재시작 | 스크립트가 출력한 '이전' 값으로 되돌린다. 정책을 모두 지우려면 `Remove-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Name NoAutoUpdate,AUOptions,NoAutoRebootWithLoggedOnUsers` |
| `pause` | 업데이트 일시 중지 5주(`-PauseWeeks` 로 1~5). Windows 가 허용하는 최대가 35일이다. `-NoPause` 로 건너뛴다 | 같음 (멈춘 동안은 업데이트 자체가 없다) | 설정 → Windows 업데이트 → 업데이트 다시 시작 |
| `activehours` | 사용 시간(Windows 가 재시작하지 않는 시간대)을 최대인 18시간으로, 15시~다음 날 9시. 재시작이 허용되는 나머지 6시간을 사람이 있는 9~15시에 둔다. 자동 조정은 끈다 | 같음 (세 번째 겹) | 설정 → Windows 업데이트 → 고급 옵션 → 사용 시간 |
| `power` | 절전 안 함 · 최대 절전 안 함 · 덮개 닫아도 아무 것도 안 함 · USB 선택적 절전(쉬는 USB 장치의 전원을 끊는 기능) 끔. 전원 연결(AC)과 배터리(DC) 모두. 디스플레이 끄기는 건드리지 않는다 — 화면은 꺼져도 시험에 영향이 없다 | 충전기가 빠지면 배터리 설정으로 잠듦 · USB 이더넷 끊김 | 설정 → 시스템 → 전원 및 배터리 에서 항목별로 |
| `bluetooth` | 블루투스 무선 장치를 장치 관리자에서 '사용 안 함'. Wi-Fi 장치는 건드리지 않는다(같은 칩이어도 장치가 따로다) | 블루투스가 Wi-Fi 를 끊음 | 장치 관리자 → Bluetooth → 그 장치 오른쪽 클릭 → 디바이스 사용 (스크립트가 출력한 InstanceId 로 `Enable-PnpDevice` 도 된다) |
| `nicpower` | Wi-Fi 와 이더넷 어댑터의 "전원을 절약하기 위해 컴퓨터가 이 장치를 끌 수 있음"을 해제한다(어댑터 레지스트리 값 `PnPCapabilities` 에 0x18). 시험 중 Wi-Fi 가 끊기지 않도록 어댑터를 다시 시작하지 않으므로 **다음 재부팅부터 적용된다** | 어댑터가 쉬다가 끊김 | 출력된 '이전' 값으로 `PnPCapabilities` 를 되돌리고(없었으면 지우고) 재부팅 |
| `arso` | ARSO 정책 `DisableAutomaticRestartSignOn=0` (아래 '자동 로그온' 참고) | 재시작 뒤 감시자가 안 뜸 | `Remove-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name DisableAutomaticRestartSignOn,AutomaticRestartSignOnConfig` |
| `firewall` | `python.exe` · `pythonw.exe` 의 UDP·TCP 60222 받기 허용 규칙 4개(이름 `cell-bench <exe> <UDP·TCP> 60222`). 이미 있으면 건너뛴다. 파이썬 경로가 기본 사용자 설치 경로가 아니면 `-PythonPath` 로 준다 | 셀 신호 0대 · 보안 프로그램 차단 | `Remove-NetFirewallRule -DisplayName 'cell-bench *60222'` |
| `supervisor` | 감시자 작업 `CellBench Supervisor` 를 같은 폴더의 `install_supervisor.ps1` 로 등록한다. 그 스크립트는 등록한 뒤 감시자를 바로 한 번 시작한다. 파일이 없으면 경고하고 건너뛴다 | 프로그램이 죽은 채로 남음 | `powershell -ExecutionPolicy Bypass -File tools\install_supervisor.ps1 -Uninstall` |

### 자동 로그온 — 사람이 정할 것 두 가지

**감시자 작업은 로그온할 때 시작되므로, PC 가 재시작된 뒤 누군가 로그온해야 감시자가 뜬다.** 그 빈자리를 메우는 장치가 둘 있고, 둘 다 보안 판단이 걸려 있어 스크립트가 기본으로 켜지 않는다.

1. **BitLocker 가 꺼진 PC 의 ARSO.** ARSO(자동 재시작 로그온, Automatic Restart Sign-On)는 업데이트로 재시작된 뒤 마지막 사용자로 자동 로그온하고 화면을 잠근 채 세션을 여는 Windows 기능이다. 그런데 Windows 는 기본적으로 **BitLocker(Windows 디스크 암호화)가 켜져 있을 때만** ARSO 를 한다. TestPC 는 BitLocker 가 켜져 있다는 표시가 없어(10-10 확인. 정확히 보려면 관리자 PowerShell 에서 `manage-bde -status C:`), `arso` 항목만으로는 실제로 자동 로그온이 일어나지 않는다. `check_env.py` 도 이 상태를 `!` 로 보여 준다. `-ArsoAlways` 를 주면 `AutomaticRestartSignOnConfig=1` 이 되어 BitLocker 와 무관하게 동작하지만, Microsoft 는 이 설정을 "장치가 물리적으로 안전한 곳에 있을 때만" 쓰라고 한다. 시험실 출입이 통제된다면 켜도 되는 조건이다. 정하면 이렇게 적용한다.

   ```powershell
   powershell -ExecutionPolicy Bypass -File tools\install.ps1 -Only arso -ArsoAlways
   ```

2. **정전·강제 종료 뒤 자동 로그온.** ARSO 는 Windows 가 스스로 재시작할 때 쓰는 장치라, 전원이 갑자기 끊긴 뒤에는 동작한다는 보장이 없다. 노트북은 배터리가 있어 짧은 정전에는 꺼지지 않지만(배터리 절전을 끈 이유가 이것이다), 배터리까지 다 닳아 꺼지면 다음 부팅에서 누군가 로그온해야 한다. 이것까지 덮으려면 Windows 자동 로그온(부팅할 때마다 정해진 계정으로 로그온)을 켜야 하는데, 그 계정의 비밀번호를 PC 에 저장해야 하므로 스크립트는 하지 않는다. 켜기로 정하면 Microsoft Sysinternals 의 **Autologon** 도구(비밀번호를 암호화된 LSA 비밀로 저장한다)를 관리자 권한으로 실행해 계정과 비밀번호를 넣는 방법을 권한다.

### 유지 — 일시 중지는 5주마다 갱신한다

- **일시 중지는 최대 35일이라 만료된다.** `check_env.py` 가 만료 7일 전부터 `!`(곧 만료)로, 지나면 `✗` 로 알린다. 그때 `install.ps1 -Only pause` 를 다시 돌린다. 만료되면 업데이트가 설치되기 시작한다. 정책(`policy`)이 로그온 중 자동 재시작은 계속 막지만, 설치 자체가 Wi-Fi 드라이버를 바꾸는 식으로 시험을 흔들 수 있으니 만료 전에 갱신한다.
- **업데이트가 재시작을 기다리고 있으면** `check_env.py` 가 `!`(업데이트 재시작 예약됨)로 알린다. 정책이 막고 있는 동안, 사이클 경계(충전 쪽)에서 사람이 재시작한다. `nicpower` 를 처음 적용했을 때도 같은 방식으로 한 번 재시작해야 적용된다.
- **보안 프로그램.** `check_env.py` 는 nProtect · AhnLab · INISAFE 류가 실행 중이면 이름을 보여 준다(`!`, 판정은 아니다). 셀 신호가 0대인데 방화벽 규칙이 있다면, 이 프로그램들의 예외(허용) 목록에 파이썬이 있는지 본다.

## 2. Wi-Fi 고정 주소 (관리자 PowerShell)

```powershell
netsh interface ipv4 set address name="Wi-Fi" static 192.168.1.100 255.255.255.0
netsh wlan set profileparameter name="FTG-3D93-5G" connectionmode=auto
```

- 게이트웨이는 **비운다.** 넣으면 인터넷을 시험망 쪽에서 찾다가 끊긴다. 인터넷은 유선(USB-이더넷)으로.
- 인터페이스 이름이 "Wi-Fi" 가 아니면 `Get-NetAdapter` 로 확인해 바꾼다.
- 그 노트북을 다른 곳에서 쓸 때 되돌리기: `netsh interface ipv4 set address name="Wi-Fi" dhcp`

## 3. 방화벽 (관리자 PowerShell)

> 1-1절 스크립트의 `firewall` 항목이 이것을 한다. 아래는 스크립트를 못 쓸 때의 손 절차다.

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

> 1-1절 스크립트의 `power` 항목이 이것을 한다. 아래는 스크립트를 못 쓸 때의 손 절차이고, 업데이트 재시작·블루투스·자동 로그온은 손 절차에 없다 — 그것들은 스크립트로 한다.

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT
```

- 앞의 네 줄: 전원 연결 시와 배터리일 때 모두 절전·최대 절전 안 함. 충전기가 빠지면 배터리 설정이 적용되므로 둘 다 바꾼다. 나머지 줄: 전원 연결·배터리 모두 **덮개를 닫아도 계속 동작.**
- 노트북 충전기는 항상 연결. 배터리로 밤을 새우면 꺼지고, 그때 플러그가 꺼진 상태로 남으면 셀이 방전돼 꺼진다.
- 잠금(Win+L)은 괜찮다. 절전·종료·재시작만 시험을 멈춘다.

## 6. 프로그램

```bash
git clone https://github.com/GeonwooKim-fitogether/QA-Automation-Test-Room.git
cd QA-Automation-Test-Room/cell-bench
pip install -r requirements.txt
python tools/check_env.py
```

`check_env.py` 에 ✗ 가 하나도 없으면 준비 끝이다(`!` 는 참고). 이어서 `python serve_board.py` 와 `python run_cycle.py --cycles N`.

## 7. 기록 옮기기 (선택)

이어서 같은 시험을 계속하려면 옛 노트북의 `cell-bench/data/` 를 통째로 복사한다. `cycles.csv` 의 줄 수로 다음 사이클 번호를 정하므로, 복사하면 번호가 이어지고 결과판 추이도 이어진다. 사이클마다 셀 파일이 약 0.5 GB 쌓이니 디스크 여유를 본다.

## 이 노트북이 개인 PC 라서 정해야 할 것

| 항목 | 지금 | 이식할 때 정할 것 |
|---|---|---|
| 플러그 TP-Link 계정 | 등록한 사람의 계정 | 팀 공용 계정으로 다시 등록할지 (다시 등록하면 Third-Party Compatibility 도 다시 켠다) |
| 시험 기록 · 셀 파일 | 노트북 디스크 `cell-bench/data/` | 회사 저장소(공유 드라이브 등)로 둘지 · 셀 원본 파일 보관 기간 |
| 저장소 접근 | 개인 GitHub 계정의 저장소 | 팀 조직으로 옮길지 |
