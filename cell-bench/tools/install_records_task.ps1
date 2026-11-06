<#
레일 수집 하루 요약 자동 올리기 등록 — 작업 스케줄러에 "Cell Bench Records" 를 만든다. 실행 전까지는 아무것도 바뀌지 않는다.

왜 (2026-11-06): 그날 무엇을 돌렸고 무엇이 걸렸나를 저장소에 자동으로 남긴다. PR 이 올라갈 때마다 자동 검사(각 1분 과금)가
  돌아, 사용자와 정한 대로 1주일에 한 번만 올린다(약 월 20분).

무엇을 하나
  - 트리거: 매주 금요일 17:00. PC 가 꺼져 있었으면 다음에 켜질 때(StartWhenAvailable).
  - 실행: <작업 트리>\.venv\Scripts\pythonw.exe tools\rail_records.py — 요약을 다시 만들어 바뀐 것이 있을 때만
    봇 브랜치 chore/rail-records 에 올리고 PR 을 연다. 결과는 data\rail_records.log.
관리자 권한: 필요 없다(현재 사용자 작업). git · gh 로그인은 현재 사용자의 것을 쓴다.

되돌리기
  powershell -ExecutionPolicy Bypass -File cell-bench\tools\install_records_task.ps1 -Uninstall
사용
  powershell -ExecutionPolicy Bypass -File cell-bench\tools\install_records_task.ps1 -Tree C:\Dev\dynamic-bench
#>
param([string]$Tree = "", [switch]$Uninstall)
$ErrorActionPreference = "Stop"
$TaskName = "Cell Bench Records"
if (-not $Tree) { $Tree = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
$wd = Join-Path $Tree "cell-bench"
$PythonW = Join-Path $Tree ".venv\Scripts\pythonw.exe"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        "작업 '$TaskName' 을 지웠다"
    } else { "작업 '$TaskName' 이 없다" }
    return
}

if (-not (Test-Path $PythonW)) { throw "$PythonW 가 없다 — 작업 트리의 .venv 를 확인한다" }
if (-not (Test-Path (Join-Path $wd "tools\rail_records.py"))) { throw "$wd\tools\rail_records.py 가 없다 — -Tree 를 확인한다" }
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $PythonW -Argument "-X utf8 tools\rail_records.py" -WorkingDirectory $wd
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At "17:00"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "레일 수집 하루 요약을 매주 금요일 봇 브랜치 chore/rail-records 에 올리고 PR 을 연다 (cell-bench/tools/rail_records.py)" -Force | Out-Null
"등록: '$TaskName' · 매주 금요일 17:00"
"확인: cell-bench\data\rail_records.log 마지막 줄이 '올림' 또는 '바뀐 것 없음' 이면 된다"
