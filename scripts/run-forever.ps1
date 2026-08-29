# Keep the agent loop alive for the whole contest week.
#
#   .\scripts\run-forever.ps1                 # dry run, safe to leave running
#   .\scripts\run-forever.ps1 -Execute        # sends tickets to the paper account
#
# The cycle already survives API faults on its own: a failed cycle is journalled and
# the next one runs. This wrapper covers the rarer case where the process itself dies,
# so a transient crash at 10:04 does not cost the rest of the day.
#
# Ctrl+C twice to stop for good: once to end the child, once to end the loop.

param(
    [switch]$Execute,
    [int]$Interval = 180
)

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$arguments = @('run', 'run-agent', '--loop', '--interval', $Interval)
if ($Execute) { $arguments += '--execute' }

Write-Host "VRP Engine wrapper: execute=$($Execute.IsPresent) interval=${Interval}s" -ForegroundColor Cyan
if (-not $Execute) {
    Write-Host "Dry run: no orders will be sent. Add -Execute when you mean it." -ForegroundColor Yellow
}

while ($true) {
    Write-Host "[$(Get-Date -Format u)] starting agent loop" -ForegroundColor Green
    & uv @arguments
    Write-Host "[$(Get-Date -Format u)] agent exited (code $LASTEXITCODE); restarting in 30s" -ForegroundColor Yellow
    Start-Sleep -Seconds 30
}
