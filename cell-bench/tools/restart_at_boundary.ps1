# 시험 프로그램을 '사이클 경계'에서만 새 코드로 다시 시작한다.
# 충전 중에 끊으면 플러그가 꺼져 예비 충전이 날아가므로, run.log 에 "사이클 N 끝"이 찍힌 직후(다음 방전 시작 = 플러그 OFF 라 잃는 것이 없다)에 바꾼다.
# 바꾸는 동안에는 감시자(supervise.py)가 옛 프로세스의 종료를 '비정상 종료'로 보고 따로 다시 띄우지 않도록
# data\supervisor_pause 표지를 둔다. 이 스크립트가 중간에 죽어도 감시자가 영원히 멈추지 않게 표지는 15분 뒤 만료된다.
# 실행 설정(--config)은 -Config 로 준다. 주지 않으면 data\run_config.json 이 있을 때 그것을 붙인다
# (cellbench/config.py 의 engine_config_file 과 같은 기본값 — 지금 엔진이 그 파일로 돌기 때문에 교체 때 빠뜨리지 않게).
#
# -Now : 경계를 기다리지 않고 바로 바꾼다. 단 data\now.json 의 단계가 예비 충전·플러그 켜기·추출·충전(PRECHARGE · PLUG_ON ·
#        EXTRACT · CHARGE)이면 거부한다 — 새 엔진은 방전부터 시작하므로 충전이 끊기고(셀이 덜 찬 채 방전), 추출 중이면 셀이
#        0x26 복귀를 못 받고 대기 모드에 남을 수 있다. 단계를 읽지 못했는데 옛 프로세스가 살아 있어도 거부한다(안전한지 모르므로).
# -Force : 위 거부를 무시하고 바꾼다 (사람이 그 손실을 알고 받아들일 때만).
#
# 사용: powershell -File tools\restart_at_boundary.ps1 -OldPid 7436 -Cycles 4 [-Config data\run_config.json] [-Now [-Force]]
param([int]$OldPid, [int]$Cycles = 4, [int]$TimeoutMin = 300, [string]$Config = "", [switch]$Now, [switch]$Force)
$wd = Split-Path $PSScriptRoot -Parent
$log = Join-Path $wd "data\run.log"
$pause = Join-Path $wd "data\supervisor_pause"
if (-not $Config -and (Test-Path (Join-Path $wd "data\run_config.json"))) { $Config = "data\run_config.json" }
$runArgs = @("run_cycle.py", "--cycles", "$Cycles")
if ($Config) { $runArgs += @("--config", $Config) }
# 이 PC(TestPC)에서 'python' 은 Microsoft Store 바로가기라 실행이 실패한다 — 사용자 설치본이 있으면 그것을 쓴다
$py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$env:PYTHONIOENCODING = "utf-8"
$alive = [bool](Get-Process -Id $OldPid -ErrorAction SilentlyContinue)

if ($Now) {
    $phase = $null
    try { $phase = (Get-Content (Join-Path $wd "data\now.json") -Raw -Encoding utf8 | ConvertFrom-Json).phase } catch { }
    $why = @{
        "PRECHARGE" = "예비 충전 중 — 새 엔진은 방전부터 시작해 셀이 덜 찬 채로 첫 사이클을 돈다";
        "PLUG_ON"   = "플러그를 막 켠 참 — 새 엔진이 방전부터 다시 시작해 플러그를 끄고, 셀은 기준선 아래로 내려간다";
        "EXTRACT"   = "추출 중 — 끊으면 셀이 0x26 복귀를 못 받아 측정을 멈춘 대기 모드에 남을 수 있다";
        "CHARGE"    = "충전 중 — 새 엔진이 방전부터 다시 시작해 셀이 덜 찬 채로 방전한다"
    }
    $refuse = $null
    if ($why.ContainsKey("$phase")) { $refuse = $why["$phase"] }
    elseif (-not $phase -and $alive) { $refuse = "now.json 의 단계를 읽지 못함 — 지금 바꿔도 안전한지 알 수 없다" }
    if ($refuse -and -not $Force) {
        "$(Get-Date -Format HH:mm:ss) 즉시 교체를 거부한다 (단계 $phase): $refuse"
        "  사이클 경계에서 바꾸려면 -Now 없이 다시 실행한다. 손실을 알고도 지금 바꾸려면 -Now -Force."
        exit 2
    }
    if ($refuse) { "$(Get-Date -Format HH:mm:ss) -Force — 거부 이유를 무시하고 지금 바꾼다 (단계 $phase): $refuse" }
    else { "$(Get-Date -Format HH:mm:ss) 즉시 교체 (단계 $phase)" }
} else {
    $t0 = Get-Date
    $before = (Select-String -Path $log -Pattern "=== 사이클 \d+ 끝" -Encoding utf8 | Measure-Object).Count
    "$(Get-Date -Format HH:mm:ss) 경계 대기 시작 (지금까지 끝난 사이클 $before)"
    while ($true) {
        if (-not (Get-Process -Id $OldPid -ErrorAction SilentlyContinue)) { "$(Get-Date -Format HH:mm:ss) 옛 프로세스가 이미 없음 — 바로 시작"; break }
        $done = (Select-String -Path $log -Pattern "=== 사이클 \d+ 끝" -Encoding utf8 | Measure-Object).Count
        if ($done -gt $before) { "$(Get-Date -Format HH:mm:ss) 사이클 끝 감지"; break }
        if (((Get-Date) - $t0).TotalMinutes -gt $TimeoutMin) { "$(Get-Date -Format HH:mm:ss) $TimeoutMin 분 안에 경계가 오지 않음 — 그대로 둠"; exit 1 }
        Start-Sleep 15
    }
}
$until = [DateTimeOffset]::Now.ToUnixTimeSeconds() + 900
$mark = @{ reason = "restart_at_boundary.ps1 — 새 코드로 교체 중"; until = $until; by = "restart_at_boundary.ps1" } | ConvertTo-Json -Compress
[IO.File]::WriteAllText($pause, $mark, (New-Object Text.UTF8Encoding $false))
"$(Get-Date -Format HH:mm:ss) 감시자 일시 중지 표지를 둠 (15분 뒤 만료)"
Stop-Process -Id $OldPid -ErrorAction SilentlyContinue
Start-Sleep 3
"$(Get-Date -Format HH:mm:ss) 새 명령: $py $($runArgs -join ' ')"
$p = Start-Process -FilePath $py -ArgumentList $runArgs -WorkingDirectory $wd -WindowStyle Minimized -RedirectStandardError (Join-Path $wd "data\stderr.txt") -PassThru
"$(Get-Date -Format HH:mm:ss) 새 프로세스 PID $($p.Id)"
Start-Sleep 25
Get-Content $log -Encoding utf8 -Tail 4 | Where-Object { $_ -notlike '*설정:*' }
Remove-Item $pause -ErrorAction SilentlyContinue
"$(Get-Date -Format HH:mm:ss) 감시자 일시 중지 표지를 치움 — 새 엔진은 data\engine.json 으로 감시자가 이어서 지켜본다"
