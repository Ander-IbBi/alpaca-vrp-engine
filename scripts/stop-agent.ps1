# Stop everything the launcher started, in one go.
#
#   double-click stop-agent.cmd          # the normal way out
#   .\scripts\stop-agent.ps1             # same thing from a terminal
#   .\scripts\stop-agent.ps1 -KeepPanel  # stop trading but leave the watcher open
#
# Closing the agent window by hand also works, but it tends to leave the panel running
# and the pid file behind, which then reports a process that died minutes ago. This
# tidies up all of it so the next start begins from a clean slate.

param(
    [switch]$KeepPanel
)

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$Host.UI.RawUI.WindowTitle = 'VRP Engine - stop'

function Stop-Tree([int]$target, [string]$label) {
    # /T because the wrapper's real work happens in a child `uv` process, and killing
    # only the parent would leave the loop running with nothing supervising it.
    & taskkill /PID $target /T /F 2>&1 | Out-Null
    Write-Host "  stopped  $label (pid $target)" -ForegroundColor Green
}

function Find-AgentProcesses([string[]]$needles) {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $command = $_.CommandLine
        if (-not $command -or $_.ProcessId -eq $PID) { return $false }
        foreach ($needle in $needles) {
            if ($command -like "*$needle*") { return $true }
        }
        return $false
    }
}

Write-Host ''
Write-Host '  VRP Engine - stopping' -ForegroundColor Cyan
Write-Host ''

$stopped = 0

# --- The registered wrapper, which is the one that restarts things -----------

$pidFile = Join-Path $repo 'data\agent.pid'
if (Test-Path $pidFile) {
    $recorded = 0
    $raw = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ([int]::TryParse(("$raw").Trim(), [ref]$recorded) -and $recorded -gt 0) {
        if (Get-Process -Id $recorded -ErrorAction SilentlyContinue) {
            Stop-Tree $recorded 'agent wrapper'
            $stopped++
        }
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}

# --- Anything left over: orphans from a hard kill, or a start without a pid --

$needles = @('run-forever.ps1', 'start-agent.ps1', 'run-agent')
if (-not $KeepPanel) { $needles += 'watch-agent.ps1' }

foreach ($process in Find-AgentProcesses $needles) {
    if (-not (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue)) { continue }
    Stop-Tree $process.ProcessId $process.Name
    $stopped++
}

Write-Host ''
if ($stopped -eq 0) {
    Write-Host '  Nothing was running. Already stopped.' -ForegroundColor Yellow
} else {
    Write-Host '  Stopped. No more orders will be sent.' -ForegroundColor Green
}

Write-Host ''
Write-Host '  Positions already open stay open on Alpaca - every one of them is' -ForegroundColor DarkGray
Write-Host '  defined risk. Nothing manages them until you start the agent again.' -ForegroundColor DarkGray
Write-Host ''
Write-Host '  Start again: double-click start-agent.cmd' -ForegroundColor DarkGray
Write-Host ''
