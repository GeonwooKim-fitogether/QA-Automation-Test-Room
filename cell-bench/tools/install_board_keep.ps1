<#
결과판 서버 자동 시작 등록 — 작업 스케줄러에 "Cell Bench Board" 를 만든다. 이 스크립트를 실행하기 전까지는 아무것도 바뀌지 않는다.

왜 (2026-11-06): 레일 판정 파일(protocol_*.json)을 클라우드 표 rail_run 에 올리는 일은 결과판 서버(serve_board.py)가
  1분마다 한다(cellbench/rail_cloud.py). 그런데 이 서버는 사람이 손으로 켜야만 돌았다. 11-02 17:04 뒤로 꺼져 있어서
  11-05 X 세트 판정 272개가 하나도 올라가지 않았고, 클라우드 결과판에 세트 2가 생기지 않았다.
  이 작업이 PC 로그온 때부터 서버를 늘 띄워 두게 한다. 서버가 뜨면 밀린 판정 파일도 1분 안에 올라간다.

무엇을 하나
  - 트리거: 로그온할 때 + 그 뒤 5분마다 반복. 이미 돌고 있으면 새로 띄우지 않는다(IgnoreNew).
    손으로 띄운 서버가 이미 있으면 새 서버는 포트(8765)를 못 잡고 곧바로 끝나므로, 같은 파일을 두 번 올리지 않는다.
  - 실행: <작업 트리>\.venv\Scripts\pythonw.exe serve_board.py (창 없음, 결과판은 http://127.0.0.1:8765)
  - 판정 데이터는 <작업 트리>\cell-bench\data 를 읽는다. 레일 시험을 돌리는 작업 트리를 -Tree 로 준다.
관리자 권한: 필요 없다(현재 사용자 작업).

되돌리기
  powershell -ExecutionPolicy Bypass -File cell-bench\tools\install_board_keep.ps1 -Uninstall
사용 (제어 PC2 — 레일 시험은 C:\Dev\dynamic-bench 에서 돈다)
  powershell -ExecutionPolicy Bypass -File cell-bench\tools\install_board_keep.ps1 -Tree C:\Dev\dynamic-bench
#>
param([string]$Tree = "", [switch]$Uninstall)
$ErrorActionPreference = "Stop"
$TaskName = "Cell Bench Board"
if (-not $Tree) { $Tree = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }   # 이 스크립트가 든 작업 트리
$wd = Join-Path $Tree "cell-bench"
$PythonW = Join-Path $Tree ".venv\Scripts\pythonw.exe"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
        Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -like "*serve_board.py*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId; "결과판 서버 프로세스 $($_.ProcessId) 를 끝냈다" }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        "작업 '$TaskName' 을 지웠다"
    } else { "작업 '$TaskName' 이 없다" }
    return
}

if (-not (Test-Path $PythonW)) { throw "$PythonW 가 없다 — 작업 트리의 .venv 를 확인한다" }
if (-not (Test-Path (Join-Path $wd "serve_board.py"))) { throw "$wd\serve_board.py 가 없다 — -Tree 를 확인한다" }
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $PythonW -Argument "-X utf8 serve_board.py" -WorkingDirectory $wd
# 로그온 트리거의 반복은 다음 로그온 뒤에야 시작한다(install_charge_keep.ps1, 2026-11-02 실측).
# 그래서 '지금부터 5분마다' 트리거를 하나 더 둔다. 서버가 죽으면 늦어도 5분 뒤 다시 뜬다.
$trigger = @((New-ScheduledTaskTrigger -AtLogOn -User $user),
             (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)))
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "결과판 서버 — 로컬 결과판을 띄우고 레일 판정 파일을 클라우드 rail_run 에 1분마다 올린다 (cell-bench/serve_board.py)" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep 8
"등록: '$TaskName' · 상태 $((Get-ScheduledTask -TaskName $TaskName).State) · 작업 트리 $Tree"
"확인: http://127.0.0.1:8765/api/rail/cloud 의 state 가 ok 이고, 1~2분 뒤 클라우드 결과판 레일 목록에 새 판정이 보이면 된다"
