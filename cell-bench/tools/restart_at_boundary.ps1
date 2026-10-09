# 시험 프로그램을 '사이클 경계'에서만 새 코드로 다시 시작한다.
# 충전 중에 끊으면 플러그가 꺼져 예비 충전이 날아가므로, run.log 에 "사이클 N 끝"이 찍힌 직후(다음 방전 시작 = 플러그 OFF 라 잃는 것이 없다)에 바꾼다.
# 바꾸는 동안에는 감시자(supervise.py)가 옛 프로세스의 종료를 '비정상 종료'로 보고 따로 다시 띄우지 않도록
# data\supervisor_pause 표지를 둔다. 이 스크립트가 중간에 죽어도 감시자가 영원히 멈추지 않게 표지는 15분 뒤 만료된다.
# 실행 설정(--config)은 -Config 로 준다. 주지 않으면 data\run_config.json 이 있을 때 그것을 붙인다
# (cellbench/config.py 의 engine_config_file 과 같은 기본값 — 지금 엔진이 그 파일로 돌기 때문에 교체 때 빠뜨리지 않게).
# 사용: powershell -File tools\restart_at_boundary.ps1 -OldPid 7436 -Cycles 4 [-Config data\run_config.json]
param([int]$OldPid, [int]$Cycles = 4, [int]$TimeoutMin = 300, [string]$Config = "")
$wd = Split-Path $PSScriptRoot -Parent
$log = Join-Path $wd "data\run.log"
$pause = Join-Path $wd "data\supervisor_pause"
if (-not $Config -and (Test-Path (Join-Path $wd "data\run_config.json"))) { $Config = "data\run_config.json" }
$runArgs = @("run_cycle.py", "--cycles", "$Cycles")
if ($Config) { $runArgs += @("--config", $Config) }
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
$until = [DateTimeOffset]::Now.ToUnixTimeSeconds() + 900
$mark = @{ reason = "restart_at_boundary.ps1 — 새 코드로 교체 중"; until = $until; by = "restart_at_boundary.ps1" } | ConvertTo-Json -Compress
[IO.File]::WriteAllText($pause, $mark, (New-Object Text.UTF8Encoding $false))
"$(Get-Date -Format HH:mm:ss) 감시자 일시 중지 표지를 둠 (15분 뒤 만료)"
Stop-Process -Id $OldPid -ErrorAction SilentlyContinue
Start-Sleep 3
"$(Get-Date -Format HH:mm:ss) 새 명령: python $($runArgs -join ' ')"
$p = Start-Process -FilePath python -ArgumentList $runArgs -WorkingDirectory $wd -WindowStyle Minimized -RedirectStandardError (Join-Path $wd "data\stderr.txt") -PassThru
"$(Get-Date -Format HH:mm:ss) 새 프로세스 PID $($p.Id)"
Start-Sleep 25
Get-Content $log -Encoding utf8 -Tail 4 | Where-Object { $_ -notlike '*설정:*' }
Remove-Item $pause -ErrorAction SilentlyContinue
"$(Get-Date -Format HH:mm:ss) 감시자 일시 중지 표지를 치움 — 새 엔진은 data\engine.json 으로 감시자가 이어서 지켜본다"
