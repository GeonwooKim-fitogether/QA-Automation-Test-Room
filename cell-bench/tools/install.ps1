<#
셀 시험대 제어 PC(무인 Windows 노트북)를 한 번에 설정한다 — 업데이트 재시작 · 절전 · 블루투스 간섭으로 시험이 멈추지 않게.

왜: 10-08 21:15 Windows 업데이트가 사용 시간(0~18시) 밖이라며 PC 를 재시작했고, 시험 프로그램이 사라져
    플러그가 꺼진 채로 남아 셀이 완전 방전됐다. 그 밖에 충전기가 빠지면 배터리 전원 설정(절전)이 적용되고,
    PC 블루투스 광고가 Wi-Fi 를 끊었고(10-07), 재시작 뒤 자동 로그온이 안 되면 감시자가 뜨지 않는다.

권한: 관리자 PowerShell 에서 실행한다. -WhatIf 로 보기만 할 때는 관리자가 아니어도 된다.
확인: 끝나면 python tools\check_env.py 를 돌린다. 그 점검이 여기서 바꾼 것을 매번 다시 읽는다.

사용 (cell-bench 폴더에서. 실행 정책이 기본값인 PC 라 -ExecutionPolicy Bypass -File 로 부른다)
  powershell -ExecutionPolicy Bypass -File tools\install.ps1 -WhatIf          # 무엇을 바꿀지 보기만
  powershell -ExecutionPolicy Bypass -File tools\install.ps1                  # 전부 적용
  powershell -ExecutionPolicy Bypass -File tools\install.ps1 -Only pause      # 일시 중지만 다시 5주 (check_env 가 '곧 만료'라고 할 때)
  powershell -ExecutionPolicy Bypass -File tools\install.ps1 -Only power,bluetooth
  powershell -ExecutionPolicy Bypass -File tools\install.ps1 -NoPause         # 일시 중지는 하지 않고 나머지만

바꾸는 것 — 항목 이름(-Only 에 쓴다) · 무엇을 · 되돌리는 법
  policy       업데이트 정책: 로그온한 사용자가 있으면 업데이트 뒤 자동 재시작하지 않는다 (만료 없음).
               HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU 에 NoAutoUpdate=0, AUOptions=4,
               NoAutoRebootWithLoggedOnUsers=1. Microsoft 문서는 이 정책이 AUOptions=4(자동 다운로드·예약 설치)일 때만
               적용된다고 적는다. -AUOptions 3 을 주면 '다운로드 후 설치는 사람이 누름'으로 둔다. -NoPolicy 로 건너뛴다.
               되돌리기: 출력된 '이전' 값으로 되돌리거나, 정책을 모두 지우려면
                 Remove-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Name NoAutoUpdate,AUOptions,NoAutoRebootWithLoggedOnUsers
  pause        업데이트 일시 중지 N주 (-PauseWeeks, 기본·최대 5주 = Windows 한도 35일). -NoPause 로 건너뛴다.
               HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings 의 PauseUpdatesStartTime·PauseUpdatesExpiryTime 과
               PauseFeatureUpdates/PauseQualityUpdates 의 StartTime·EndTime (ISO 8601 UTC, 설정 앱이 쓰는 것과 같은 자리).
               되돌리기: 설정 → Windows 업데이트 → '업데이트 다시 시작'
  activehours  사용 시간을 최대 18시간으로 (기본 15시~다음 날 9시). 재시작이 허용되는 나머지 6시간을 사람이 있는
               9~15시에 둔다. 자동 조정(SmartActiveHoursState)은 끈다.
               되돌리기: 설정 → Windows 업데이트 → 고급 옵션 → 사용 시간
  power        지금 전원 구성표에서: 절전·최대 절전 안 함, 덮개 닫아도 아무 것도 안 함 (전원 연결 AC · 배터리 DC 모두),
               USB 선택적 절전 끔 (USB 이더넷·허브가 쉬다가 끊기지 않게). 디스플레이 끄기는 건드리지 않는다(화면은 꺼져도 된다).
               되돌리기: 설정 → 시스템 → 전원 및 배터리 (항목별로), 또는 powercfg -restoredefaultschemes (모든 구성표가 기본값이 됨)
  bluetooth    블루투스 무선 장치를 장치 관리자에서 '사용 안 함'. Wi-Fi 는 건드리지 않는다(인텔 겸용 칩이어도 장치가 따로다).
               되돌리기: 출력된 InstanceId 로 Enable-PnpDevice -InstanceId '<ID>' -Confirm:$false
                         또는 장치 관리자 → Bluetooth → 그 장치 오른쪽 클릭 → 디바이스 사용
  nicpower     물리 네트워크 어댑터(Wi-Fi·이더넷)의 '전원을 절약하기 위해 컴퓨터가 이 장치를 끌 수 있음' 해제.
               어댑터 레지스트리의 PnPCapabilities 에 0x18 을 더한다(Microsoft KB 2740020 의 값 24).
               시험 중 Wi-Fi 가 끊기지 않도록 어댑터를 다시 시작하지 않으므로 다음 재부팅부터 적용된다.
               되돌리기: 출력된 '이전' 값으로 PnPCapabilities 를 되돌리거나(없었으면 지움) 재부팅
  arso         업데이트 재시작 뒤 마지막 사용자로 자동 로그온하고 잠근다(ARSO). 감시자 작업이 로그온 때 뜨므로 필요하다.
               HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System 의 DisableAutomaticRestartSignOn=0.
               Windows 는 기본적으로 BitLocker 가 켜져 있을 때만 ARSO 를 한다. BitLocker 가 꺼진 PC 에서 실제로 동작하게
               하려면 -ArsoAlways (AutomaticRestartSignOnConfig=1) 가 필요한데, 디스크가 암호화되지 않은 채 자동 로그온하는
               보안 판단이라 사람이 정한다(docs/control-pc-setup.md '자동 로그온').
               한계: 정전·강제 종료 뒤에는 동작한다는 보장이 없다. 그것까지 덮으려면 자동 로그온(비밀번호 저장)이
               필요하고, 비밀번호를 다루므로 이 스크립트는 하지 않는다 — 문서에 방법만 있다.
               되돌리기: Remove-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name DisableAutomaticRestartSignOn,AutomaticRestartSignOnConfig
  firewall     python.exe·pythonw.exe 의 UDP·TCP 60222 받기 허용 (규칙 이름 'cell-bench <exe> <UDP|TCP> 60222', 이미 있으면 건너뜀).
               파이썬 경로는 -PythonPath (기본 사용자 설치 경로. Get-Command python 은 스토어 바로가기일 수 있어 쓰지 않는다).
               되돌리기: Remove-NetFirewallRule -DisplayName 'cell-bench *60222'
  supervisor   감시자 작업 'CellBench Supervisor' 등록 — 같은 폴더의 install_supervisor.ps1 을 부른다(없으면 경고하고 건너뜀).
               그 스크립트는 등록한 뒤 감시자를 바로 한 번 시작한다. -PythonPath 옆의 pythonw.exe 를 -PythonW 로 넘긴다.
               되돌리기: powershell -ExecutionPolicy Bypass -File tools\install_supervisor.ps1 -Uninstall
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string[]]$Only,                                   # 고를 항목. 쉼표로 여럿 (예: -Only pause,power)
    [ValidateRange(1, 5)][int]$PauseWeeks = 5,         # Windows 가 허용하는 최대가 35일(5주)
    [switch]$NoPause,
    [switch]$NoPolicy,
    [ValidateSet(3, 4)][int]$AUOptions = 4,
    [ValidateRange(0, 23)][int]$ActiveStart = 15,
    [ValidateRange(0, 23)][int]$ActiveEnd = 9,
    [switch]$ArsoAlways,
    [string]$PythonPath = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
)

$ErrorActionPreference = 'Stop'
$Port = 60222                                          # cellbench/config.py 의 port (셀 → PC 라이브 UDP, 셀 → PC 명령 TCP)
$TaskName = 'CellBench Supervisor'                     # tools/check_env.py 의 TASK_NAME 과 같아야 한다
$AuKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
$UxKey = 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings'
$SysKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
$NetClass = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}'
$UsbSub = '2a737441-1930-4402-8d77-b2bebba308a3'      # 전원 옵션 'USB 설정'
$UsbSel = '48e6b7a6-50f5-4782-a5d4-53bb8f07e226'      #   'USB 선택적 절전 모드 설정'

$Summary = New-Object System.Collections.Generic.List[object]
$script:Current = ''

# ---------------------------------------------------------------- 공용 도우미
function Show($v) { if ($null -eq $v -or "$v" -eq '') { '(없음)' } else { "$v" } }

function RegGet([string]$Path, [string]$Name) {
    $p = Get-ItemProperty -Path $Path -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $p) { return $null }
    return $p.$Name
}

function RegSet([string]$Path, [string]$Name, $Value, [string]$Type) {
    if (-not (Test-Path $Path)) { New-Item -Path $Path -Force | Out-Null }
    New-ItemProperty -Path $Path -Name $Name -Value $Value -PropertyType $Type -Force | Out-Null
}

# 바꾸는 일은 전부 이 함수를 거친다 — -WhatIf 면 무엇을 바꿀지 출력만 하고 실행하지 않는다.
function Change([string]$What, [scriptblock]$Do) {
    if ($WhatIfPreference) { Write-Host "    바꿀 것: $What" -ForegroundColor Yellow; return }
    Write-Host "    바꿈: $What"
    & $Do
}

function Before([string]$Text) { Write-Host "    이전: $Text" }
function After([string]$Text) { Write-Host "    지금: $Text" }

# 요약 표에 한 줄. $Ok: $true 성공 · $false 실패 · $null 건너뜀
function Done($Before, $After, $Ok) {
    $r = if ($WhatIfPreference) { '보기만' } elseif ($null -eq $Ok) { '건너뜀' } elseif ($Ok) { '성공' } else { '실패' }
    $Summary.Add([pscustomobject]@{ 항목 = $script:Current; 이전 = "$Before"; 지금 = "$After"; 결과 = $r })
}

function Pcfg {
    & powercfg @args | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "powercfg $args 실패 (코드 $LASTEXITCODE)" }
}

# powercfg /qh 의 전원 연결(AC)·배터리(DC) 현재 색인. 영문·한글 출력 둘 다 읽는다 (check_env.py 의 power_index 와 같은 규칙).
function Read-Power([string]$Sub, [string]$Setting) {
    $out = (& powercfg /qh SCHEME_CURRENT $Sub $Setting) -join "`n"
    $r = [ordered]@{}
    foreach ($c in 'AC', 'DC') {
        $r[$c] = if ($out -match "(?:Current $c Power Setting Index|$c 전원 설정 색인)\s*:\s*(0x[0-9a-fA-F]+)") { [Convert]::ToInt64($Matches[1], 16) } else { $null }
    }
    [pscustomobject]$r
}

function Get-BtRadio {
    # BTH·SWD 로 시작하는 것은 무선 장치 아래에 붙는 열거자라 빼고, 무선 장치(어댑터)만 고른다
    @(Get-PnpDevice -Class Bluetooth -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -notmatch '^(BTH|SWD)' })
}

function Get-Nic {
    $keys = @(Get-ChildItem $NetClass -ErrorAction SilentlyContinue)
    foreach ($a in @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.PnPDeviceID -notlike 'BTH*' })) {
        $k = $keys | Where-Object { (Get-ItemProperty $_.PSPath -Name NetCfgInstanceId -ErrorAction SilentlyContinue).NetCfgInstanceId -eq $a.InterfaceGuid } | Select-Object -First 1
        if ($k) { [pscustomobject]@{ Name = $a.Name; Path = $k.PSPath; Cap = (RegGet $k.PSPath 'PnPCapabilities') } }
        else { Write-Warning "$($a.Name): 어댑터 레지스트리 키를 찾지 못해 건너뜀" }
    }
}

function Get-BitLocker {
    # 1 = 켜짐. 그 밖의 값은 문서가 없어 '켜짐 아님'으로 본다 (check_env.py 의 arso_check 와 같은 규칙)
    try { (New-Object -ComObject Shell.Application).NameSpace($env:SystemDrive).Self.ExtendedProperty('System.Volume.BitLockerProtection') } catch { $null }
}

function Get-TaskState {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) { "$($t.State)" } else { '등록 안 됨' }
}

# ---------------------------------------------------------------- 항목
function Set-UpdatePolicy {
    $names = 'NoAutoUpdate', 'AUOptions', 'NoAutoRebootWithLoggedOnUsers'
    $want = @{ NoAutoUpdate = 0; AUOptions = $AUOptions; NoAutoRebootWithLoggedOnUsers = 1 }
    $b = ($names | ForEach-Object { "$_=$(Show (RegGet $AuKey $_))" }) -join ' '
    Before $b
    Change "$AuKey 에 NoAutoUpdate=0 AUOptions=$AUOptions NoAutoRebootWithLoggedOnUsers=1" {
        foreach ($n in $names) { RegSet $AuKey $n $want[$n] 'DWord' }
    }
    $a = ($names | ForEach-Object { "$_=$(Show (RegGet $AuKey $_))" }) -join ' '
    After $a
    Done $b $a (((RegGet $AuKey 'NoAutoRebootWithLoggedOnUsers') -eq 1) -and ((RegGet $AuKey 'AUOptions') -eq $AUOptions))
}

function Set-UpdatePause {
    $b = Show (RegGet $UxKey 'PauseUpdatesExpiryTime')
    Before "일시 중지 만료 $b (UTC)"
    $now = (Get-Date).ToUniversalTime()
    $fmt = "yyyy-MM-dd'T'HH:mm:ss'Z'"
    $inv = [Globalization.CultureInfo]::InvariantCulture
    $start = $now.ToString($fmt, $inv)
    $end = $now.AddDays(7 * $PauseWeeks).ToString($fmt, $inv)
    Change "업데이트 일시 중지 $start ~ $end (UTC, $PauseWeeks 주)" {
        foreach ($n in 'PauseUpdatesStartTime', 'PauseFeatureUpdatesStartTime', 'PauseQualityUpdatesStartTime') { RegSet $UxKey $n $start 'String' }
        foreach ($n in 'PauseUpdatesExpiryTime', 'PauseFeatureUpdatesEndTime', 'PauseQualityUpdatesEndTime') { RegSet $UxKey $n $end 'String' }
    }
    $a = Show (RegGet $UxKey 'PauseUpdatesExpiryTime')
    After "일시 중지 만료 $a (UTC) — 설정 → Windows 업데이트 화면에 '일시 중지됨'이 보이는지 확인"
    Done $b $a ($a -eq $end)
}

function Set-ActiveHours {
    $span = ($ActiveEnd - $ActiveStart + 24) % 24
    if ($span -eq 0 -or $span -gt 18) { throw "사용 시간은 1~18시간이어야 한다 (지금 $ActiveStart 시 → $ActiveEnd 시 = $span 시간)" }
    $read = { "$(Show (RegGet $UxKey 'ActiveHoursStart'))시~$(Show (RegGet $UxKey 'ActiveHoursEnd'))시 자동조정=$(Show (RegGet $UxKey 'SmartActiveHoursState'))" }
    $b = & $read
    Before $b
    Change "사용 시간 $ActiveStart 시 ~ $ActiveEnd 시 ($span 시간), 자동 조정 끔" {
        RegSet $UxKey 'ActiveHoursStart' $ActiveStart 'DWord'
        RegSet $UxKey 'ActiveHoursEnd' $ActiveEnd 'DWord'
        RegSet $UxKey 'SmartActiveHoursState' 0 'DWord'
    }
    $a = & $read
    After $a
    Done $b $a (((RegGet $UxKey 'ActiveHoursStart') -eq $ActiveStart) -and ((RegGet $UxKey 'ActiveHoursEnd') -eq $ActiveEnd))
}

function Set-Power {
    $settings = @(
        @{ Name = '절전'; Sub = 'SUB_SLEEP'; Id = 'STANDBYIDLE' },
        @{ Name = '최대 절전'; Sub = 'SUB_SLEEP'; Id = 'HIBERNATEIDLE' },
        @{ Name = '덮개 닫기'; Sub = 'SUB_BUTTONS'; Id = 'LIDACTION' },
        @{ Name = 'USB 선택적 절전'; Sub = $UsbSub; Id = $UsbSel }
    )
    $read = { ($settings | ForEach-Object { $p = Read-Power $_.Sub $_.Id; "$($_.Name) AC=$(Show $p.AC) DC=$(Show $p.DC)" }) -join ' · ' }
    $b = & $read
    Before $b
    $v = Read-Power 'SUB_VIDEO' 'VIDEOIDLE'
    Write-Host "    (그대로 둠) 디스플레이 끄기 AC=$(Show $v.AC)초 DC=$(Show $v.DC)초 — 화면은 꺼져도 시험에 영향 없음"
    Change '절전·최대 절전 안 함, 덮개 아무 것도 안 함, USB 선택적 절전 끔 (AC·DC 모두, 지금 전원 구성표)' {
        foreach ($s in $settings) {
            Pcfg /setacvalueindex SCHEME_CURRENT $s.Sub $s.Id 0
            Pcfg /setdcvalueindex SCHEME_CURRENT $s.Sub $s.Id 0
        }
        Pcfg /setactive SCHEME_CURRENT
    }
    $a = & $read
    After $a
    $ok = $true
    foreach ($s in $settings) { $p = Read-Power $s.Sub $s.Id; if ($p.AC -ne 0 -or $p.DC -ne 0) { $ok = $false } }
    Done $b $a $ok
}

function Disable-Bluetooth {
    $radios = @(Get-BtRadio)
    if ($radios.Count -eq 0) { Before '블루투스 무선 장치 없음'; Done '없음' '없음' $true; return }
    $b = ($radios | ForEach-Object { "$($_.FriendlyName)=$($_.Status)" }) -join ', '
    Before $b
    foreach ($r in @($radios | Where-Object { $_.Status -eq 'OK' -or $_.Status -eq 'Degraded' })) {
        Write-Host "    되돌릴 때 쓸 InstanceId: $($r.InstanceId)"
        Change "장치 사용 안 함: $($r.FriendlyName)" { Disable-PnpDevice -InstanceId $r.InstanceId -Confirm:$false }
    }
    $after = @(Get-BtRadio)
    $a = ($after | ForEach-Object { "$($_.FriendlyName)=$($_.Status)" }) -join ', '
    After $a
    Done $b $a (@($after | Where-Object { $_.Status -eq 'OK' -or $_.Status -eq 'Degraded' }).Count -eq 0)
}

function Set-NicPower {
    $nics = @(Get-Nic)
    if ($nics.Count -eq 0) { Before '물리 네트워크 어댑터를 찾지 못함'; Done '?' '?' $false; return }
    $b = ($nics | ForEach-Object { "$($_.Name) PnPCapabilities=$(Show $_.Cap)" }) -join ', '
    Before $b
    foreach ($n in $nics) {
        $new = ([int]$n.Cap) -bor 0x18
        if ($n.Cap -eq $new) { continue }
        Change "$($n.Name): PnPCapabilities $(Show $n.Cap) → $new (다음 재부팅부터 적용)" { RegSet $n.Path 'PnPCapabilities' $new 'DWord' }
    }
    $after = @(Get-Nic)
    $a = ($after | ForEach-Object { "$($_.Name) PnPCapabilities=$(Show $_.Cap)" }) -join ', '
    After "$a — 어댑터를 다시 시작하지 않았으므로 다음 재부팅부터 적용"
    Done $b $a (@($after | Where-Object { -not (([int]$_.Cap) -band 0x08) }).Count -eq 0)
}

function Set-Arso {
    $read = { "DisableAutomaticRestartSignOn=$(Show (RegGet $SysKey 'DisableAutomaticRestartSignOn')) AutomaticRestartSignOnConfig=$(Show (RegGet $SysKey 'AutomaticRestartSignOnConfig'))" }
    $b = & $read
    Before $b
    Change 'DisableAutomaticRestartSignOn=0 (업데이트 재시작 뒤 자동 로그온·잠금)' { RegSet $SysKey 'DisableAutomaticRestartSignOn' 0 'DWord' }
    if ($ArsoAlways) {
        Change 'AutomaticRestartSignOnConfig=1 (BitLocker 가 꺼져 있어도 자동 로그온 — 사람이 승인한 경우만)' { RegSet $SysKey 'AutomaticRestartSignOnConfig' 1 'DWord' }
    }
    $a = & $read
    After $a
    $bl = Get-BitLocker
    if (-not $ArsoAlways -and $bl -ne 1 -and (RegGet $SysKey 'AutomaticRestartSignOnConfig') -ne 1) {
        Write-Warning "BitLocker 가 켜져 있다고 확인되지 않아(값 $(Show $bl)) 이대로면 업데이트 재시작 뒤 자동 로그온하지 않는다. -ArsoAlways 는 보안 판단이라 사람이 정한다 (docs/control-pc-setup.md '자동 로그온')."
    }
    Write-Host '    한계: 정전·강제 종료 뒤에는 동작한다는 보장이 없다 — 자동 로그온(비밀번호 저장)은 사람이 정한다.'
    Done $b $a ((RegGet $SysKey 'DisableAutomaticRestartSignOn') -eq 0)
}

function Add-FirewallRules {
    $exes = @($PythonPath, (Join-Path (Split-Path $PythonPath -Parent) 'pythonw.exe'))
    $old = @(Get-NetFirewallRule -DisplayName "cell-bench *$Port" -ErrorAction SilentlyContinue)
    Before "cell-bench $Port 규칙 $($old.Count)개"
    $miss = @()
    foreach ($exe in $exes) {
        if (-not (Test-Path $exe)) { Write-Warning "없음: $exe — 건너뜀 (-PythonPath 로 실제 python.exe 경로를 준다)"; $miss += (Split-Path $exe -Leaf); continue }
        foreach ($proto in 'UDP', 'TCP') {
            $name = "cell-bench $(Split-Path $exe -Leaf) $proto $Port"
            if (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue) { Write-Host "    이미 있음: $name"; continue }
            Change "방화벽 받기 허용 규칙 추가: $name ($exe)" {
                New-NetFirewallRule -DisplayName $name -Direction Inbound -Program $exe -Protocol $proto -LocalPort $Port -Action Allow -Profile Any | Out-Null
            }
        }
    }
    $now = @(Get-NetFirewallRule -DisplayName "cell-bench *$Port" -ErrorAction SilentlyContinue)
    After "cell-bench $Port 규칙 $($now.Count)개"
    Done "규칙 $($old.Count)개" "규칙 $($now.Count)개" ($miss.Count -eq 0 -and $now.Count -ge 4)
}

function Install-Supervisor {
    $s = Join-Path $PSScriptRoot 'install_supervisor.ps1'
    $b = Get-TaskState
    Before "작업 '$TaskName': $b"
    if (-not (Test-Path $s)) {
        Write-Warning "install_supervisor.ps1 이 아직 없다 (감시자 P1 작업 중) — 건너뜀. 생기면 -Only supervisor 로 다시 실행"
        Done $b $b $null; return
    }
    # install_supervisor.ps1 은 -PythonW(창 없는 pythonw.exe 경로)를 받는다. 없으면 그 스크립트가 스스로 찾는다
    $params = @{}
    $pyw = Join-Path (Split-Path $PythonPath -Parent) 'pythonw.exe'
    if ((Get-Command $s).Parameters.ContainsKey('PythonW') -and (Test-Path $pyw)) { $params.PythonW = $pyw }
    Change "$s 실행 (등록 뒤 감시자를 바로 시작한다)" { & $s @params }
    $a = Get-TaskState
    After "작업 '$TaskName': $a"
    Done $b $a ($a -ne '등록 안 됨')
}

# ---------------------------------------------------------------- 실행
$Items = [ordered]@{
    policy      = @{ Title = '업데이트 자동 재시작 막기 — 정책 (만료 없음)'; Run = { Set-UpdatePolicy } }
    pause       = @{ Title = "업데이트 일시 중지 $PauseWeeks 주"; Run = { Set-UpdatePause } }
    activehours = @{ Title = '사용 시간 18시간'; Run = { Set-ActiveHours } }
    power       = @{ Title = '절전 · 최대 절전 · 덮개 · USB 선택적 절전 (AC·DC)'; Run = { Set-Power } }
    bluetooth   = @{ Title = '블루투스 끄기 (Wi-Fi 간섭 방지)'; Run = { Disable-Bluetooth } }
    nicpower    = @{ Title = "네트워크 어댑터 '전원 절약을 위해 끌 수 있음' 해제"; Run = { Set-NicPower } }
    arso        = @{ Title = '업데이트 재시작 뒤 자동 로그온 (ARSO)'; Run = { Set-Arso } }
    firewall    = @{ Title = "방화벽: 파이썬 UDP·TCP $Port 받기 허용"; Run = { Add-FirewallRules } }
    supervisor  = @{ Title = "감시자 작업 '$TaskName' 등록"; Run = { Install-Supervisor } }
}

# -File 로 부르면 '-Only pause,power' 가 문자열 하나로 들어오므로 쉼표로 다시 나눈다
$sel = if ($Only) { @($Only | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ }) } else { @($Items.Keys) }
$unknown = @($sel | Where-Object { -not $Items.Contains($_) })
if ($unknown.Count) { Write-Host -ForegroundColor Red "모르는 항목: $($unknown -join ', ') (쓸 수 있는 것: $(@($Items.Keys) -join ', '))"; exit 2 }
if ($NoPause) { $sel = @($sel | Where-Object { $_ -ne 'pause' }) }
if ($NoPolicy) { $sel = @($sel | Where-Object { $_ -ne 'policy' }) }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($WhatIfPreference) {
    Write-Host '보기만(-WhatIf) — 아무 것도 바꾸지 않는다.' -ForegroundColor Yellow
} elseif (-not $admin) {
    Write-Host -ForegroundColor Red '관리자 PowerShell 에서 실행해야 한다 (시작 → PowerShell 오른쪽 클릭 → 관리자 권한으로 실행). 보기만 하려면 -WhatIf.'
    exit 1
}

foreach ($key in $Items.Keys) {
    if ($sel -notcontains $key) { continue }
    $script:Current = $key
    Write-Host ''
    Write-Host "[$key] $($Items[$key].Title)" -ForegroundColor Cyan
    try { & $Items[$key].Run }
    catch {
        Write-Host "    실패: $($_.Exception.Message)" -ForegroundColor Red
        if (-not ($Summary | Where-Object { $_.항목 -eq $key })) { Done '?' '?' $false }
    }
}

Write-Host ''
Write-Host '요약' -ForegroundColor Cyan
$Summary | Format-Table -AutoSize -Wrap | Out-String -Width 220 | Write-Host
if ($sel -contains 'nicpower' -and -not $WhatIfPreference) { Write-Host '네트워크 어댑터 절전 해제는 다음 재부팅부터 적용된다. 재부팅은 사이클 경계(충전 쪽)에서 사람이 한다.' }
Write-Host '다시 확인: python tools\check_env.py  (여기서 바꾼 것을 다시 읽어 ✓ · ✗ · ! 로 보여 준다)'
$failed = @($Summary | Where-Object { $_.결과 -eq '실패' }).Count
if ($failed) { exit 1 } else { exit 0 }
