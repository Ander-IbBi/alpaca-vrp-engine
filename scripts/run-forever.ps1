# Keep the agent loop alive for the whole contest week.
#
#   .\scripts\run-forever.ps1                 # autonomous: sends tickets to the paper account
#   .\scripts\run-forever.ps1 -DryRun         # rehearse only, for development
#
# The cycle already survives API faults on its own: a failed cycle is journalled and
# the next one runs. This wrapper covers the rarer case where the process itself dies,
# so a transient crash at 10:04 does not cost the rest of the day.
#
# Ctrl+C twice to stop for good: once to end the child, once to end the loop.

param(
    [switch]$DryRun,
    [int]$Interval = 180,
    [switch]$AllowSleep
)

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

# An agent that only trades while you are at the keyboard is not an agent. Ask Windows
# to keep the machine awake for as long as this window lives. The screen may still go
# dark, and the request dies with the process, so nothing is left changed behind us.
$ES_CONTINUOUS = [uint32]'0x80000000'
$ES_SYSTEM_REQUIRED = [uint32]'0x00000001'
$keepAwake = -not $AllowSleep
if ($keepAwake) {
    try {
        if (-not ('VrpEngine.Power' -as [type])) {
            Add-Type -Namespace 'VrpEngine' -Name 'Power' -MemberDefinition @'
[DllImport("kernel32.dll", SetLastError = true)]
public static extern uint SetThreadExecutionState(uint esFlags);
'@
        }
        [void][VrpEngine.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)
        Write-Host 'Holding the machine awake while the agent runs (-AllowSleep to opt out).' -ForegroundColor DarkGray
    } catch {
        Write-Host "Could not ask Windows to stay awake: $($_.Exception.Message)" -ForegroundColor Yellow
        $keepAwake = $false
    }
}

$arguments = @('run', 'run-agent', '--loop', '--interval', $Interval)
if ($DryRun) { $arguments += '--dry-run' }

# The watcher tracks this wrapper rather than the Python child: the child is expected
# to come and go across restarts, whereas if the wrapper dies nothing restarts anything.
$pidFile = Join-Path $repo 'data\agent.pid'
New-Item -ItemType Directory -Force -Path (Split-Path $pidFile) | Out-Null
Set-Content -Path $pidFile -Value $PID -Encoding ascii

Write-Host "VRP Engine wrapper: dryRun=$($DryRun.IsPresent) interval=${Interval}s pid=$PID" -ForegroundColor Cyan
if ($DryRun) {
    Write-Host "Dry run: no orders will be sent. Drop -DryRun to let the agent trade." -ForegroundColor Yellow
}

try {
    while ($true) {
        Write-Host "[$(Get-Date -Format u)] starting agent loop" -ForegroundColor Green
        & uv @arguments
        Write-Host "[$(Get-Date -Format u)] agent exited (code $LASTEXITCODE); restarting in 30s" -ForegroundColor Yellow
        Start-Sleep -Seconds 30
    }
}
finally {
    Remove-Item $pidFile -ErrorAction SilentlyContinue
    if ($keepAwake) {
        [void][VrpEngine.Power]::SetThreadExecutionState($ES_CONTINUOUS)
    }
}
