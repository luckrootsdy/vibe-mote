# Hard restart for VibeMote  (ASCII only, do not add Chinese here)
# ---------------------------------------------------------------------------
# MANUAL TOOL ONLY since 2026-11-09 (Xiao Ke).
# The client no longer calls this on its own: 34 measured process swaps proved
# that a fresh process does NOT bring ATVV back while the remote is deeply
# asleep, and the 30-60 s key-mapping blackout made things worse.  The voice
# role now stays alive and simply reconnects when a physical key wakes the
# remote.  Keep this script for "I want to restart it by hand" only.
#
# Fixes vs the old version:
#   * .venv\Scripts\pythonw.exe is a py.exe redirect stub; it spawns
#     "C:\Python312\pythonw.exe" app.py  -- a RELATIVE path, so matching only
#     the absolute path killed the stub and left the real child running (that
#     child kept the BLE link and made the remote expose only 4 services).
#     Now we also match bare "app.py" whose parent chain is inside $base.
#   * The PID written by the running instance (_app_pid.txt) is trusted first.
#   * ONE launch attempt (the old 3x loop could leave two instances fighting
#     over GATT) and a single 40 s wait for /api/state.
# ---------------------------------------------------------------------------

$ErrorActionPreference = 'SilentlyContinue'

$base   = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw    = Join-Path $base '.venv\Scripts\pythonw.exe'
$pidf   = Join-Path $base '_app_pid.txt'
$target = 'http://127.0.0.1:8787/api/state'

if (-not (Test-Path -LiteralPath $pyw)) { exit 1 }

# 1) collect every python process belonging to this folder
$procs = Get-CimInstance Win32_Process -Filter "Name like 'python%'"
$byPid = @{}
foreach ($p in $procs) { $byPid[[int]$p.ProcessId] = $p }

$kill = New-Object 'System.Collections.Generic.HashSet[int]'

# 1a) the PID the running instance reported
if (Test-Path -LiteralPath $pidf) {
    $raw = (Get-Content -LiteralPath $pidf -TotalCount 1)
    $v = 0
    if ([int]::TryParse($raw, [ref]$v)) {
        if ($byPid.ContainsKey($v)) { [void]$kill.Add($v) }
    }
}

# 1b) command line contains this folder (absolute path form)
foreach ($p in $procs) {
    $cl = [string]$p.CommandLine
    if ($cl -and $cl.IndexOf($base, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        [void]$kill.Add([int]$p.ProcessId)
    }
}

# 1c) bare "app.py" whose parent chain is inside this folder (stub child)
$changed = $true
while ($changed) {
    $changed = $false
    foreach ($p in $procs) {
        $id = [int]$p.ProcessId
        if ($kill.Contains($id)) { continue }
        $cl = [string]$p.CommandLine
        if (-not $cl) { continue }
        $isApp = $false
        foreach ($tok in ($cl -split '\s+')) {
            $t = $tok.Trim('"')
            if ($t -ieq 'app.py' -or $t -imatch '\\app\.py$') { $isApp = $true }
        }
        if (-not $isApp) { continue }
        $parent = [int]$p.ParentProcessId
        if ($kill.Contains($parent)) { [void]$kill.Add($id); $changed = $true; continue }
        $pp = $byPid[$parent]
        if ($pp -and (([string]$pp.CommandLine).IndexOf($base, [StringComparison]::OrdinalIgnoreCase) -ge 0)) {
            [void]$kill.Add($id); $changed = $true
        }
    }
}

foreach ($id in $kill) {
    Stop-Process -Id $id -Force
}
if (Test-Path -LiteralPath $pidf) { Remove-Item -LiteralPath $pidf -Force }

Start-Sleep -Seconds 4

# 2) launch exactly one fresh instance, then wait up to 40 s for /api/state
Start-Process -FilePath $pyw -ArgumentList 'app.py' -WorkingDirectory $base

for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-RestMethod -Uri $target -TimeoutSec 3 -ErrorAction Stop
        if ($r) { exit 0 }
    } catch { }
}

exit 1
