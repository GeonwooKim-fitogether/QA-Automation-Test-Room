# 시험 프로그램을 '사이클 경계'에서만 새 코드로 다시 시작한다.
# 충전 중에 끊으면 플러그가 꺼져 예비 충전이 날아가므로, run.log 에 "사이클 N 끝"이 찍힌 직후(다음 방전 시작 = 플러그 OFF 라 잃는 것이 없다)에 바꾼다.
# 사용: powershell -File tools\restart_at_boundary.ps1 -OldPid 7436 -Cycles 4
param([int]$OldPid, [int]$Cycles = 4, [int]$TimeoutMin = 300)
$wd = Split-Path $PSScriptRoot -Parent
$log = Join-Path $wd "data\run.log"
$env:PYTHONIOENCODING = "utf-8"
$t0 = Get-Date
$before = (Select-String -Path $log -Pattern "=== 사이클 \d+ 끝" -Encoding utf8 | Measure-Object).Count
"$(Get-Date -Format HH:mm:ss) 경계 대기 시작 (지금까지 끝난 사이클 $before)"
while ($true) {
    if (-not (Get-Process -Id $OldPid -ErrorAction SilentlyContinue)) { "$(Get-Date -Format HH:mm:ss) 옛 프로세스가 이미 없음 — 바로 시작"; break }
    $now = (Select-String -Path $log -Pattern "=== 사이클 \d+ 끝" -Encoding utf8 | Measure-Object).Count
    if ($now -gt $before) { "$(Get-Date -Format HH:mm:ss) 사이클 끝 감지"; break }
    if (((Get-Date) - $t0).TotalMinutes -gt $TimeoutMin) { "$(Get-Date -Format HH:mm:ss) $TimeoutMin 분 안에 경계가 오지 않음 — 그대로 둠"; exit 1 }
    Start-Sleep 15
}
Stop-Process -Id $OldPid -ErrorAction SilentlyContinue
Start-Sleep 3
$p = Start-Process -FilePath python -ArgumentList "run_cycle.py", "--cycles", "$Cycles" -WorkingDirectory $wd -WindowStyle Minimized -RedirectStandardError (Join-Path $wd "data\stderr.txt") -PassThru
"$(Get-Date -Format HH:mm:ss) 새 프로세스 PID $($p.Id)"
Start-Sleep 25
Get-Content $log -Encoding utf8 -Tail 4 | Where-Object { $_ -notlike '*설정:*' }
