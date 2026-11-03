<#
레일 독 충전 유지 등록 — 작업 스케줄러에 "Rail Charge Keep" 을 만든다. 이 스크립트를 실행하기 전까지는 아무것도 바뀌지 않는다.

왜 (2026-11-02): 독은 한 번 완충되면 충전을 멈추고, 플러그가 켜진 채로는 다시 시작하지 않는다. 그래서 전력이 바닥에 붙으면
  플러그를 끊었다 켜 주는 충전 유지(motionbench/charge.py keep)가 있는데, 사람이 실행해야만 돌았다. 10-30 뒤 아무도 켜지 않아
  주말 동안 셀 24대가 방전됐다. 이 작업이 PC 로그온 때부터 늘 돌게 한다.

무엇을 하나
  - 트리거: 로그온할 때 + 그 뒤 5분마다 반복. 이미 돌고 있으면 새로 띄우지 않는다(IgnoreNew). charge.py 도 잠금으로 두 번째를 끝낸다.
  - 실행: <작업 트리>\.venv\Scripts\pythonw.exe motionbench\charge.py keep --hours 87600 --log data\rail_charge.log (창 없음)
  - 모터가 도는 동안(motion.lock)에는 플러그에 손대지 않는다. 틱마다 data\rail_charge.json 에 상태(ok · retry · lost)를 쓴다.
관리자 권한: 필요 없다(현재 사용자 작업).

되돌리기
  powershell -ExecutionPolicy Bypass -File cell-bench\tools\install_charge_keep.ps1 -Uninstall
사용
  powershell -ExecutionPolicy Bypass -File cell-bench\tools\install_charge_keep.ps1
#>
param([switch]$Uninstall)
$ErrorActionPreference = "Stop"
$TaskName = "Rail Charge Keep"
$wd = Split-Path $PSScriptRoot -Parent                       # cell-bench
$PythonW = Join-Path (Split-Path $wd -Parent) ".venv\Scripts\pythonw.exe"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
        Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -like "*charge.py*keep*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId; "충전 유지 프로세스 $($_.ProcessId) 를 끝냈다" }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        "작업 '$TaskName' 을 지웠다 — 플러그는 켜 둔 채로 둔다"
    } else { "작업 '$TaskName' 이 없다" }
    return
}

if (-not (Test-Path $PythonW)) { throw "$PythonW 가 없다 — 작업 트리의 .venv 를 확인한다" }
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $PythonW -Argument "motionbench\charge.py keep --hours 87600 --log data\rail_charge.log" -WorkingDirectory $wd
# 로그온 트리거의 반복은 다음 로그온 뒤에야 시작한다(2026-11-02 실측 — 등록 직후 5분마다 다시 뜨지 않았다).
# 그래서 '지금부터 5분마다' 트리거를 하나 더 둔다. 이미 돌고 있으면 IgnoreNew 와 잠금이 두 번째를 막는다.
$trigger = @((New-ScheduledTaskTrigger -AtLogOn -User $user),
             (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)))
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "레일 독 충전 유지 — 전력이 바닥이면 플러그를 끊었다 켜서 충전을 다시 시작한다 (cell-bench/motionbench/charge.py keep)" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep 8
"등록: '$TaskName' · 상태 $((Get-ScheduledTask -TaskName $TaskName).State)"
"확인: data\rail_charge.json 의 t 가 1분마다 바뀌고 state 가 ok 면 된다. lost 면 플러그를 PC 가 찾지 못하는 것이다"
