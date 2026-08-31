# Keep the agent loop alive for the whole contest week.
#
#   .\scripts\run-forever.ps1                 # autonomous: sends tickets to the paper account
#   .\scripts\run-forever.ps1 -DryRun         # rehearse only, for development
#
# The cycle already survives API faults on its own: a failed cycle is journalled and
# the next one runs. This wrapper covers the rarer case where the process itself dies,
# so a transient crash at 10:04 does not cost the rest of the day.
#
# The launcher starts this hidden. Closing other windows does not stop it; stop-agent.cmd
# does. Ctrl+C (if you run this in a terminal) exits 0 and is treated as a real stop,
# not as a crash worth restarting.

param(
    [switch]$DryRun,
    [int]$Interval = 180,
    [switch]$AllowSleep
)

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

# An agent that only trades while you are at the keyboard is not an agent. Ask Windows
# to keep the machine awake for as long as this process lives. The screen may still go
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
$logFile = Join-Path $repo 'data\agent.log'
New-Item -ItemType Directory -Force -Path (Split-Path $pidFile) | Out-Null
Set-Content -Path $pidFile -Value $PID -Encoding ascii

function Write-Agent([string]$message, [string]$color = 'White') {
    Write-Host $message -ForegroundColor $color
    Add-Content -Path $logFile -Value $message -Encoding utf8
}

Write-Agent "VRP Engine wrapper: dryRun=$($DryRun.IsPresent) interval=${Interval}s pid=$PID" 'Cyan'
if ($DryRun) {
    Write-Agent "Dry run: no orders will be sent. Drop -DryRun to let the agent trade." 'Yellow'
}

# A crash is worth retrying; a loop that cannot even start is not. Counting only the
# failures that happen within seconds separates "the network blinked" from "the keys are
# wrong", so a permanent fault stops with an explanation instead of respawning all night.
$FastFailSeconds = 60
$MaxFastFailures = 5
$fastFailures = 0

try {
    while ($true) {
        # Refresh on every spawn: other apps can clear the stay-awake request, and a
        # hidden wrapper that has been up for days still has to hold the machine.
        if ($keepAwake) {
            try {
                [void][VrpEngine.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)
            } catch { }
        }
        Write-Agent "[$(Get-Date -Format u)] starting agent loop" 'Green'
        $startedAt = Get-Date
        # Hidden window: stdout would vanish, so the cycle JSON lands in the log.
        & uv @arguments >> $logFile 2>&1
        $code = $LASTEXITCODE
        $ranFor = (Get-Date) - $startedAt

        # The loop returns 0 only on Ctrl+C, which is a person asking it to stop.
        if ($code -eq 0) {
            Write-Agent "[$(Get-Date -Format u)] agent stopped cleanly; not restarting." 'Cyan'
            break
        }

        if ($ranFor.TotalSeconds -lt $FastFailSeconds) {
            $fastFailures++
        } else {
            $fastFailures = 0
        }

        if ($fastFailures -ge $MaxFastFailures) {
            Write-Agent ''
            Write-Agent "  The agent failed to start $MaxFastFailures times in a row." 'Red'
            Write-Agent '  This is a setup problem, not a blip. Check the errors above, then:' 'Yellow'
            Write-Agent '    uv run smoke-paper'
            Write-Agent '  Giving up so it does not respawn all night.'
            Write-Agent ''
            break
        }

        Write-Agent "[$(Get-Date -Format u)] agent exited (code $code); restarting in 30s" 'Yellow'
        Start-Sleep -Seconds 30
    }
}
finally {
    Remove-Item $pidFile -ErrorAction SilentlyContinue
    if ($keepAwake) {
        try {
            [void][VrpEngine.Power]::SetThreadExecutionState($ES_CONTINUOUS)
        } catch { }
    }
}
