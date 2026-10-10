<#
감시자 등록 — 작업 스케줄러에 "CellBench Supervisor" 를 만든다. 이 스크립트를 실행하기 전까지는 아무것도 바뀌지 않는다.

무엇을 하나
  현재 사용자의 작업 "CellBench Supervisor" 를 등록하고 바로 한 번 시작한다(다음 로그온을 기다리지 않게).
  - 트리거: 로그온할 때 + 그 뒤 5분마다 반복. 이미 돌고 있으면 새로 띄우지 않는다(다중 인스턴스 IgnoreNew)
            — 감시자가 죽어도 5분 안에 다시 뜬다. supervise.py 도 잠금 파일로 두 번째 실행을 조용히 끝낸다.
  - 실행: pythonw.exe supervise.py (창 없음) · 작업 폴더 = cell-bench
  - 배터리일 때도 시작하고 멈추지 않음 · 실행 시간 제한 없음 · 실패하면 1분 뒤 다시(최대 999번) · 보통 우선순위
  감시자가 무엇을 하는지는 cellbench/supervisor.py 맨 위 설명을 본다.

옛 임시 감시자: 작업 "cell-bench watchdog"(tools/watchdog.ps1, PowerShell 무한 루프)이 있으면 등록 전에 그 작업을
  '사용 안 함'으로 바꾸고, watchdog.ps1 을 돌리는 PowerShell 프로세스를 끝낸다 — 둘이 동시에 run_cycle 을 띄우지 않게.
  옛 작업은 지우지 않는다. 그 작업을 '끝내기'(Stop-ScheduledTask)는 하지 않는다: 작업을 끝내면 옛 감시자가 띄운
  시험 프로그램까지 함께 끝날 수 있다. 옛 감시자 프로세스만 끝내면 작업은 저절로 끝난 상태가 된다.
  감시자(supervise.py)도 옛 감시자가 살아 있는 동안에는 점검만 하고 조치하지 않는다.

관리자 권한: 필요 없다 — 현재 사용자의 작업을 최소 권한으로 등록한다. 회사 정책이 작업 등록을 막으면 관리자 PowerShell 에서 다시 한다.
주의: 로그온해야 시작된다. Windows 업데이트·정전으로 재부팅된 뒤 아무도 로그온하지 않으면 감시자도 뜨지 않는다
      — 자동 로그온이 켜져 있는지 tools/check_env.py 로 확인한다.

되돌리기
  powershell -ExecutionPolicy Bypass -File tools\install_supervisor.ps1 -Uninstall
  작업을 끄고 감시자 프로세스만 끝낸 뒤 작업을 지운다. 감시자가 띄운 시험 프로그램·결과판은 그대로 둔다
  (작업 '끝내기'는 작업이 띄운 자식 프로세스까지 끝낼 수 있어 쓰지 않는다).
  옛 임시 감시자 작업은 되살리지 않는다 — 필요하면 안내대로 Enable-ScheduledTask 를 사람이 한다.

사용
  powershell -ExecutionPolicy Bypass -File tools\install_supervisor.ps1
  powershell -ExecutionPolicy Bypass -File tools\install_supervisor.ps1 -PythonW "C:\...\Python312\pythonw.exe"
#>
param([switch]$Uninstall, [string]$PythonW = "")
$ErrorActionPreference = "Stop"
$TaskName = "CellBench Supervisor"        # tools/check_env.py 가 이 이름으로 확인한다 — 바꾸지 않는다
$OldTask = "cell-bench watchdog"          # 옛 임시 감시자 (tools/watchdog.ps1)
$wd = Split-Path $PSScriptRoot -Parent    # cell-bench

function Get-OldWatchdogProcs {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe' or Name='pwsh.exe'" |
        Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -like "*watchdog.ps1*" }
}

if ($Uninstall) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) { Disable-ScheduledTask -TaskName $TaskName | Out-Null; "작업 '$TaskName' 을 껐다 (새로 뜨지 않게)" }
    $state = Join-Path $wd "data\supervisor.json"
    if (Test-Path $state) {
        $sv = Get-Content $state -Raw -Encoding UTF8 | ConvertFrom-Json
        $p = if ($sv.pid) { Get-Process -Id $sv.pid -ErrorAction SilentlyContinue } else { $null }
        if ($p -and $p.ProcessName -like "python*") { Stop-Process -Id $sv.pid; "감시자 프로세스 $($sv.pid) 를 끝냈다" }
        else { "돌고 있는 감시자 프로세스가 없다" }
    }
    if ($task) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false; "작업 '$TaskName' 을 지웠다" }
    else { "작업 '$TaskName' 이 없다" }
    if (Get-ScheduledTask -TaskName $OldTask -ErrorAction SilentlyContinue) {
        "안내: 옛 임시 감시자 작업 '$OldTask' 은 되살리지 않았다. 다시 쓰려면: Enable-ScheduledTask -TaskName '$OldTask'; Start-ScheduledTask -TaskName '$OldTask'"
    }
    return
}

function Find-PythonW {
    # Microsoft Store 의 python 바로가기(WindowsApps)는 쓰지 않는다 — 실제 설치본의 pythonw.exe 를 찾는다
    $roots = @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles")
    foreach ($r in $roots) {
        $hit = Get-ChildItem -Path (Join-Path $r "Python3*\pythonw.exe") -ErrorAction SilentlyContinue |
            Sort-Object { [int]($_.Directory.Name -replace '\D', '') } -Descending | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike "*WindowsApps*" } | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    return $null
}

if (-not $PythonW) { $PythonW = Find-PythonW }
if (-not $PythonW -or -not (Test-Path $PythonW)) { throw "pythonw.exe 를 찾지 못했다 — -PythonW 로 경로를 준다" }
$py = Join-Path (Split-Path $PythonW -Parent) "python.exe"
& $py -c "import keyring, kasa" 2>$null
if ($LASTEXITCODE -ne 0) { throw "$py 에 keyring·python-kasa 가 없다 — cell-bench 에서 pip install -r requirements.txt 를 먼저 한다" }
if (-not (Test-Path (Join-Path $wd "supervise.py"))) { throw "supervise.py 가 $wd 에 없다" }

# 옛 임시 감시자를 먼저 멈춘다 — 둘이 동시에 run_cycle 을 띄우지 않게
if (Get-ScheduledTask -TaskName $OldTask -ErrorAction SilentlyContinue) {
    Disable-ScheduledTask -TaskName $OldTask | Out-Null
    "옛 작업 '$OldTask' 을 사용 안 함으로 바꿨다 (지우지 않음 · 작업 끝내기는 하지 않음 — 그 작업이 띄운 시험 프로그램을 지키려고)"
}
foreach ($p in @(Get-OldWatchdogProcs)) {
    Stop-Process -Id $p.ProcessId -Force
    "옛 감시자 프로세스 $($p.ProcessId) 를 끝냈다: $($p.CommandLine)"
}
if (@(Get-OldWatchdogProcs).Count -gt 0) { throw "옛 감시자(watchdog.ps1) 프로세스가 아직 남아 있다 — 둘이 겹치지 않게 등록을 멈춘다" }

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $PythonW -Argument "supervise.py" -WorkingDirectory $wd
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)).Repetition
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -Priority 4
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "셀 시험대 감시자 — 시험 프로그램이 죽거나 멈추면 플러그를 켜고 다시 띄운다 (cell-bench/supervise.py)" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep 5
$info = Get-ScheduledTaskInfo -TaskName $TaskName
"등록: '$TaskName' · $PythonW supervise.py · 폴더 $wd"
"상태: $((Get-ScheduledTask -TaskName $TaskName).State) · 마지막 실행 $($info.LastRunTime) · 결과 $($info.LastTaskResult)"
"확인: data\supervisor.json 의 t 가 1분마다 바뀌고, data\supervisor.log 에 '감시자 시작' 이 찍혀 있으면 된다"
