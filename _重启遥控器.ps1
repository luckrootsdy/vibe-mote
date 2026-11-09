#requires -Version 5
# Reliable restart for VibeMote  (pure ASCII on purpose - encoding-safe)
#
# Design rules (learned the hard way):
#   1) Never depend on a scheduled task. The task "VibeMote" does NOT exist on
#      this machine; older versions called it as a fallback and ended up with
#      zero processes running.
#   2) Path is derived from this script's own location, so the whole folder can
#      be copied to another PC and still work.
#   3) Two stages: ask the running client to restart itself, then - if the API
#      is still down - launch pythonw.exe directly and retry twice.
#
$ErrorActionPreference = 'SilentlyContinue'

$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$log  = Join-Path $base 'client.log'
$pyw  = Join-Path $base '.venv\Scripts\pythonw.exe'
$app  = Join-Path $base 'app.py'
$stateUrl = 'http://127.0.0.1:8787/api/state'
$actUrl   = 'http://127.0.0.1:8787/api/action'

function Get-State {
    try {
        $r = Invoke-RestMethod -Uri $stateUrl -TimeoutSec 4 -ErrorAction Stop
        if ($r) { return $r }
    } catch { }
    return $null
}

Write-Host ''
Write-Host '  Restarting VibeMote ...' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path -LiteralPath $pyw)) {
    Write-Host "  FAILED - pythonw.exe not found: $pyw" -ForegroundColor Red
    Write-Host ''
    exit 1
}

# ---- Stage 1: let the running client restart itself ----------------------
$cur = Get-State
if ($cur) {
    try {
        $body = @{ action = 'restart-app' } | ConvertTo-Json -Compress
        Invoke-RestMethod -Uri $actUrl -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 8 | Out-Null
        Write-Host '  [1/2] asked the running client to restart itself' -ForegroundColor Gray
    } catch {
        Write-Host '  [1/2] client running but restart request failed' -ForegroundColor Yellow
    }
} else {
    Write-Host '  [1/2] client is not running - skip self-restart' -ForegroundColor Gray
}

$r = $null
for ($i = 0; $i -lt 12; $i++) {
    Start-Sleep -Seconds 2
    $r = Get-State
    if ($r) { break }
}

# ---- Stage 2: launch it ourselves if still down ---------------------------
if (-not $r) {
    Write-Host '  [2/2] still down - launching pythonw.exe directly' -ForegroundColor Yellow
    for ($try = 1; $try -le 2; $try++) {
        Write-Host "        attempt $try ..." -ForegroundColor Gray
        Start-Process -FilePath $pyw -ArgumentList 'app.py' -WorkingDirectory $base
        for ($i = 0; $i -lt 9; $i++) {
            Start-Sleep -Seconds 2
            $r = Get-State
            if ($r) { break }
        }
        if ($r) { break }
    }
} else {
    Write-Host '  [2/2] came back on its own - no direct launch needed' -ForegroundColor Gray
}

# ---- Result --------------------------------------------------------------
Write-Host ''
if ($r) {
    $v = "$($r.voice.ready)/$($r.voice.connected)"
    $k = "$($r.keys.ready)/$($r.keys.blocked_count)"
    Write-Host "  OK   voice=$v   keys=$k" -ForegroundColor Green
    Write-Host "  voice note: $($r.voice.note)" -ForegroundColor Gray
    Write-Host "  keys  note: $($r.keys.note)" -ForegroundColor Gray
    Write-Host '  opening the console page ...' -ForegroundColor Gray
    Start-Process 'http://127.0.0.1:8787/'
} else {
    Write-Host '  FAILED - client did not start. Opening client.log' -ForegroundColor Red
    if (Test-Path -LiteralPath $log) { Start-Process notepad.exe $log }
}
Write-Host ''
