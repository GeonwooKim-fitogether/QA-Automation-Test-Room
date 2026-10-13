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
# -InCharge : 충전 중에도 바로 바꾼다 — 우리가 보는 것은 방전(작동시간)이고 충전은 중간에 교체해도 이어서 하면 된다. data\now.json 의 단계가
#        CHARGE(추출을 마치고 충전하는 중)일 때만 허용하고, 추출 · 예비 충전 · 플러그 켜기 · 방전 · 복구 대기 · 그 밖의 단계는 거부한다(종료 코드 2).
#        새 엔진은 --precharge 로 시작해 만충 판정까지 충전한 뒤 다음 사이클로 간다 — 시작할 때 '방전은 끝났고 충전 중에 끊긴 사이클'을
#        cycles.csv 에 복구 행(note=restart_in_charge)으로 쓰고 새 사이클 번호는 그 다음으로 이어 센다(record.recover_cut_charge).
#        단계를 읽지 못해도 거부한다. -Now · -Force 의 동작은 바뀌지 않는다.
#
# -OldPid 는 지금 엔진(run_cycle.py)의 PID 여야 한다. 그 PID 가 없거나 명령줄에 run_cycle.py 가 없으면 거부한다 — 잘못 준 PID 로
#   경계를 기다리지도 않고 바로 새 엔진을 띄워, 살아 있는 진짜 엔진과 둘이 한 플러그·셀을 두고 다투지 않게(검토 F12).
#   지금 엔진의 PID 는 data\engine.json 의 pid 에 있다. -Force 면 그래도 진행하되, 엔진이 아닌 그 프로세스는 끝내지 않는다.
# 새 엔진이 설정 오류로 시작을 거부하면(data\engine.json exit=config_error) 사유를 크게 보이고 플러그를 켜 본다 — 거부된 엔진은
#   플러그를 만지지 않으므로, 경계(방전 시작 = 플러그 OFF)에서 바꾼 경우 셀이 그대로 방전된다(검토 F2).
# 짝 검증 중(data\engine.json 의 verifying)이면 -Force 여도 거부한다(종료 코드 4) — 강제로 끝내면 검증 플러그가 꺼진 채 남는다.
#
# 사용: powershell -File tools\restart_at_boundary.ps1 -OldPid 7436 -Cycles 4 [-Config data\run_config.json] [-Now [-Force]]
param([int]$OldPid, [int]$Cycles = 4, [int]$TimeoutMin = 300, [string]$Config = "", [switch]$Now, [switch]$Force, [switch]$InCharge)
$wd = Split-Path $PSScriptRoot -Parent
$log = Join-Path $wd "data\run.log"
$pause = Join-Path $wd "data\supervisor_pause"
$engineJson = Join-Path $wd "data\engine.json"
if (-not $Config -and (Test-Path (Join-Path $wd "data\run_config.json"))) { $Config = "data\run_config.json" }
$runArgs = @("run_cycle.py", "--cycles", "$Cycles")
if ($Config) { $runArgs += @("--config", $Config) }
if ($InCharge) { $runArgs += "--precharge" }   # 충전 중 교체 — 새 엔진은 방전부터가 아니라 충전을 이어 간다(셀 95% 이상이면 건너뜀)
# 이 PC(TestPC)에서 'python' 은 Microsoft Store 바로가기라 실행이 실패한다 — 사용자 설치본이 있으면 그것을 쓴다
$py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$env:PYTHONIOENCODING = "utf-8"

# -OldPid 가 정말 지금 엔진인가 (명령줄에 run_cycle.py)
$old = Get-CimInstance Win32_Process -Filter "ProcessId=$OldPid" -ErrorAction SilentlyContinue
$oldCmd = if ($old) { [string]$old.CommandLine } else { "" }
$isEngine = [bool]$old -and $oldCmd -like "*run_cycle.py*"
if (-not $isEngine) {
    $enginePid = $null
    try { $enginePid = (Get-Content $engineJson -Raw -Encoding utf8 | ConvertFrom-Json).pid } catch { }
    $what = if (-not $old) { "없음" } else { "엔진이 아님 ($($old.Name): $oldCmd)" }
    "$(Get-Date -Format HH:mm:ss) 그 PID($OldPid) 는 $what — 지금 엔진 PID 는 data\engine.json 의 pid ($enginePid)"
    if (-not $Force) {
        "  거부한다. 지금 엔진 PID 로 다시 실행한다. 엔진이 정말 없고 새로 띄우려는 것이면 -Force (그 PID 는 끝내지 않는다)."
        exit 3
    }
    "$(Get-Date -Format HH:mm:ss) -Force — 그대로 진행한다 (PID $OldPid 는 엔진이 아니므로 끝내지 않는다)"
}
$alive = $isEngine

# 짝 검증 중(engine.json 의 verifying)이면 교체하지 않는다 — 단일 모드의 교체는 옛 엔진을 강제로 끝내므로(Stop-Process)
# 끝내는 길(atexit)이 돌지 않아, 검증기가 끈 세트 k 플러그가 꺼진 채 남는다(코드 검토 3). -Force 로도 넘기지 않는다.
$verifying = $null
try { $verifying = (Get-Content $engineJson -Raw -Encoding utf8 | ConvertFrom-Json).verifying } catch { }
if ($alive -and $verifying) {
    $vs = ($verifying | ForEach-Object { "세트 $($_.id) ($($_.mac))" }) -join ", "
    "$(Get-Date -Format HH:mm:ss) 교체를 거부한다: 짝 검증 중 — $vs"
    "  검증이 끝나거나 결과판에서 '검증 멈춤'을 누른 뒤(engine.json 에서 verifying 이 사라진 뒤) 다시 실행한다."
    exit 4
}

# 다중 모드 엔진(engine.json mode = multi)은 죽이지 않는다 — 다른 세트가 사이클 중간이다. data\end_request 표지만 놓으면
# 엔진이 다음 만충 경계에서 모든 플러그를 켠 뒤 exit=reconfigure 로 끝내고, 감시자가 새 코드로 곧바로 다시 띄운다(명세 2-8 · A-M3)
$engMode = $null
try { $engMode = (Get-Content $engineJson -Raw -Encoding utf8 | ConvertFrom-Json).mode } catch { }
if ($alive -and $engMode -eq "multi") {
    $endReq = Join-Path $wd "data\end_request"
    [IO.File]::WriteAllText($endReq, "restart_at_boundary.ps1 $(Get-Date -Format s)", (New-Object Text.UTF8Encoding $false))
    "$(Get-Date -Format HH:mm:ss) 다중 모드 — 경계 종료 요청 표지(data\end_request)를 놓았다. 엔진이 경계에서 끝내면 감시자가 새 코드로 다시 띄운다"
    $t0 = Get-Date
    while (Get-Process -Id $OldPid -ErrorAction SilentlyContinue) {
        if (((Get-Date) - $t0).TotalMinutes -gt $TimeoutMin) { "$(Get-Date -Format HH:mm:ss) $TimeoutMin 분 안에 끝나지 않음 — 표지는 남겨 둔다"; exit 1 }
        Start-Sleep 15
    }
    "$(Get-Date -Format HH:mm:ss) 엔진이 끝났다 — 이후는 감시자 몫 (data\supervisor.log)"
    exit 0
}

if ($InCharge) {
    $phase = $null
    try { $phase = (Get-Content (Join-Path $wd "data\now.json") -Raw -Encoding utf8 | ConvertFrom-Json).phase } catch { }
    if ("$phase" -ne "CHARGE") {
        "$(Get-Date -Format HH:mm:ss) 충전 중 교체를 거부한다 (단계 '$phase'): CHARGE(추출을 마치고 충전하는 중)일 때만 바꾼다 — 추출 · 예비 충전 · 플러그 켜기 · 방전 · 복구 대기 중에는 안 된다"
        "  사이클 경계에서 바꾸려면 -InCharge 없이 다시 실행한다."
        exit 2
    }
    "$(Get-Date -Format HH:mm:ss) 충전 중 교체 (단계 CHARGE) — 새 엔진이 복구 행을 쓰고 --precharge 로 충전을 이어 간다"
} elseif ($Now) {
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
        if (-not $alive -or -not (Get-Process -Id $OldPid -ErrorAction SilentlyContinue)) { "$(Get-Date -Format HH:mm:ss) 옛 프로세스가 이미 없음 — 바로 시작"; break }
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
if ($isEngine) { Stop-Process -Id $OldPid -ErrorAction SilentlyContinue }   # 엔진이 아닌 PID(-Force)는 끝내지 않는다
Start-Sleep 3
"$(Get-Date -Format HH:mm:ss) 새 명령: $py $($runArgs -join ' ')"
$stderrTxt = Join-Path $wd "data\stderr.txt"
$t_start = [DateTimeOffset]::Now.ToUnixTimeSeconds()
$p = Start-Process -FilePath $py -ArgumentList $runArgs -WorkingDirectory $wd -WindowStyle Minimized -RedirectStandardError $stderrTxt -PassThru
"$(Get-Date -Format HH:mm:ss) 새 프로세스 PID $($p.Id)"
Start-Sleep 25
Get-Content $log -Encoding utf8 -Tail 4 | Where-Object { $_ -notlike '*설정:*' }

# 새 엔진이 설정 오류로 시작을 거부했나 — 거부된 엔진은 플러그를 만지지 않는다. 사유를 크게 보이고 플러그를 켜 본다(충전 쪽이 안전)
$eng = $null
try { $eng = Get-Content $engineJson -Raw -Encoding utf8 | ConvertFrom-Json } catch { }
if ($eng -and $eng.exit -eq "config_error" -and [double]$eng.started -ge ($t_start - 5)) {
    ""
    "=" * 78
    "!!! 새 엔진이 설정 오류로 시작을 거부했다 — 시험이 돌지 않는다 !!!"
    "    사유: $($eng.error)"
    if (Test-Path $stderrTxt) { "    stderr:"; Get-Content $stderrTxt -Encoding utf8 -Tail 5 | ForEach-Object { "      $_" } }
    "=" * 78
    "$(Get-Date -Format HH:mm:ss) 플러그를 켠다: $py tools\plug_cli.py on"
    & $py (Join-Path $wd "tools\plug_cli.py") on
    if ($LASTEXITCODE -ne 0) { "!!! 플러그를 켜지 못했다 — Tapo 앱이나 python tools\plug_cli.py on 으로 손으로 켠다 (감시자도 다음 점검에서 켠다)" }
    "설정을 고친 뒤 엔진을 다시 띄운다 — 지금은 엔진이 없으므로 이 스크립트로 하려면 -Now -Force."
}
Remove-Item $pause -ErrorAction SilentlyContinue
"$(Get-Date -Format HH:mm:ss) 감시자 일시 중지 표지를 치움 — 새 엔진은 data\engine.json 으로 감시자가 이어서 지켜본다"
