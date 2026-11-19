param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$sourceDir = $PSScriptRoot
$expectedOld = 'D:\Program Files\VibeMote 遥控器'
$launcher = Join-Path $sourceDir '启动遥控器.vbs'
$logFile = Join-Path $sourceDir '切换到源码目录.log'
function Log([string]$message) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $message"
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
}
function State {
    Invoke-RestMethod -Uri 'http://127.0.0.1:8787/api/state' -Proxy $null -TimeoutSec 5
}
if (-not (Test-Path -LiteralPath $launcher)) { throw 'Source launcher not found' }
if (-not (Test-Path -LiteralPath (Join-Path $sourceDir '.venv\Scripts\pythonw.exe'))) { throw 'Source Python not found' }
$task = Get-ScheduledTask -TaskName 'VibeMote' -ErrorAction Stop
if (@($task.Actions).Count -ne 1) { throw 'Unexpected scheduled task actions' }
$oldAction = $task.Actions[0]
$before = State
if ($before.home -ne $expectedOld -or -not $before.running) { throw "Unexpected running app: $($before.home)" }
$owner = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 8787 -State Listen)
if ($owner.Count -ne 1) { throw 'Unexpected port owner' }
if ($CheckOnly) { Write-Output "READY source=$sourceDir old=$($before.home) pid=$($owner[0].OwningProcess) task=$($oldAction.Arguments)"; exit 0 }
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator rights required' }
$oldPid = $owner[0].OwningProcess
$proc = Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid"
if (-not $proc -or $proc.CommandLine -notlike "*$expectedOld*app.py*") { throw 'Port owner is not the expected installed VibeMote app' }
Log "Switch start; old PID $oldPid; source $sourceDir"
$changed = $false
try {
    $newAction = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"' + $launcher + '" --silent --boot')
    Set-ScheduledTask -TaskName 'VibeMote' -Action $newAction | Out-Null
    $changed = $true
    Log 'Autostart action changed; stopping old roles'
    $result = Invoke-RestMethod -Uri 'http://127.0.0.1:8787/api/action' -Method Post -ContentType 'application/json' -Body '{"action":"stop"}' -Proxy $null -TimeoutSec 20
    if (-not $result.ok) { throw "Old client stop failed: $($result.msg)" }
    Stop-Process -Id $oldPid -ErrorAction Stop
    for ($i = 0; $i -lt 40; $i++) {
        if (-not (Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
    if (Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue) { throw 'Port did not close after stopping old client' }
    Log 'Old process stopped; starting source task'
    Start-ScheduledTask -TaskName 'VibeMote'
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $after = State
            if ($after.home -eq $sourceDir -and $after.running) {
                Log "SUCCESS source=$($after.home) voice_connected=$($after.voice.connected) keys_ready=$($after.keys.ready)"
                exit 0
            }
        } catch { }
    }
    throw 'Source app did not start within 20 seconds'
} catch {
    Log "ERROR $($_.Exception.Message)"
    if ($changed) {
        try {
            Set-ScheduledTask -TaskName 'VibeMote' -Action $oldAction | Out-Null
            Log 'Restored old autostart action'
            if (-not (Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue)) {
                Start-ScheduledTask -TaskName 'VibeMote'
                Log 'Requested start of old app'
            }
        } catch { Log "Rollback failed: $($_.Exception.Message)" }
    }
    exit 1
}
