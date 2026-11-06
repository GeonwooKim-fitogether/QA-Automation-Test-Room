<#
수집 원본 자동 백업 등록 — 작업 스케줄러에 "Cell Bench Backup" 을 만든다. 이 스크립트를 실행하기 전까지는 아무것도 바뀌지 않는다.

왜 (2026-11-06): 셀 기록 · 바퀴 폴더 · 실행 기록(cell-bench/data, 약 22 GB)이 제어 PC 한 곳에만 있었다.
  구글 드라이브 데스크톱 앱이 올리는 폴더로 1시간마다 복사해, PC 가 망가져도 원본이 남게 한다.

무엇을 하나
  - -Dest 의 드라이브 폴더를 data\backup_dest.txt 에 적는다(tools/backup_drive.py 가 읽는다).
  - 트리거: 지금부터 1시간마다. 이미 돌고 있으면 새로 띄우지 않는다(IgnoreNew).
  - 실행: <작업 트리>\.venv\Scripts\pythonw.exe tools\backup_drive.py (창 없음)
    모터 · 시험 · 레일 실행기가 돌면 그 회차는 건너뛰고, 복사는 지우지 않고 더하기만 한다. 결과는 data\backup.log.
관리자 권한: 필요 없다(현재 사용자 작업).

되돌리기
  powershell -ExecutionPolicy Bypass -File cell-bench\tools\install_backup_task.ps1 -Uninstall
사용 (제어 PC2 — 레일 시험은 C:\Dev\dynamic-bench 에서 돈다)
  powershell -ExecutionPolicy Bypass -File cell-bench\tools\install_backup_task.ps1 -Tree C:\Dev\dynamic-bench -Dest "G:\내 드라이브\rail-backup"
#>
param([string]$Tree = "", [string]$Dest = "", [switch]$Uninstall)
$ErrorActionPreference = "Stop"
$TaskName = "Cell Bench Backup"
if (-not $Tree) { $Tree = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
$wd = Join-Path $Tree "cell-bench"
$PythonW = Join-Path $Tree ".venv\Scripts\pythonw.exe"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        "작업 '$TaskName' 을 지웠다 (이미 복사된 파일은 그대로 둔다)"
    } else { "작업 '$TaskName' 이 없다" }
    return
}

if (-not $Dest) { throw "-Dest 로 구글 드라이브 폴더를 준다 (예: G:\내 드라이브\rail-backup)" }
if (-not (Test-Path $Dest)) { New-Item -ItemType Directory -Path $Dest | Out-Null }
if (-not (Test-Path $PythonW)) { throw "$PythonW 가 없다 — 작업 트리의 .venv 를 확인한다" }
if (-not (Test-Path (Join-Path $wd "tools\backup_drive.py"))) { throw "$wd\tools\backup_drive.py 가 없다 — -Tree 를 확인한다" }
Set-Content -Path (Join-Path $wd "data\backup_dest.txt") -Value $Dest -Encoding utf8
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $PythonW -Argument "-X utf8 tools\backup_drive.py" -WorkingDirectory $wd
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "수집 원본(cell-bench/data)을 구글 드라이브 폴더로 1시간마다 복사 — 모터 · 시험 중에는 건너뛴다 (cell-bench/tools/backup_drive.py)" -Force | Out-Null
"등록: '$TaskName' · 1분 뒤 첫 실행 · 대상 $Dest"
"확인: cell-bench\data\backup.log 마지막 줄이 '복사 — 코드 0~7' 이면 된다. 첫 복사는 약 22 GB 라 오래 걸린다"
