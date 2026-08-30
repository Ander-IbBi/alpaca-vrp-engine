# The panel next to the agent: does it still have a pulse?
#
#   .\scripts\watch-agent.ps1               # started for you by start-agent.cmd
#   .\scripts\watch-agent.ps1 -Every 15     # refresh faster
#
# It reads the decision journal through `agent-health` and never touches the broker,
# so it needs no keys of its own and cannot get in the agent's way.

param(
    [int]$Interval = 180,
    [int]$Every = 30
)

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$Host.UI.RawUI.WindowTitle = 'VRP Engine - watch'

$colours = @{ ok = 'Green'; late = 'Yellow'; stale = 'Red'; unknown = 'DarkGray' }
$previous = ''

function Write-Row([string]$label, [string]$value, [string]$colour = 'Gray') {
    Write-Host ('    {0,-12}' -f $label) -NoNewline -ForegroundColor DarkGray
    Write-Host $value -ForegroundColor $colour
}

while ($true) {
    # `uv run` can print build chatter before the payload, so take the last line that
    # actually looks like JSON rather than trusting the whole stream.
    $lines = @(& uv run agent-health --json --interval $Interval 2>$null)
    $payload = $lines | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1

    $state = $null
    if ($payload) {
        try { $state = $payload | ConvertFrom-Json } catch { $state = $null }
    }

    Clear-Host
    Write-Host ''
    if ($null -eq $state) {
        Write-Host '  VRP Engine - NO READING' -ForegroundColor Red
        Write-Host ''
        Write-Row 'problem' 'agent-health did not answer; is uv still installed?' 'Red'
    } else {
        $colour = $colours[$state.verdict]
        if (-not $colour) { $colour = 'Gray' }

        Write-Host "  VRP Engine - $($state.label)" -ForegroundColor $colour -NoNewline
        Write-Host "   $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor DarkGray
        Write-Host "  $($state.reason)" -ForegroundColor DarkGray
        Write-Host ''

        $session = if ($null -eq $state.market_open) { 'session unknown' }
                   elseif ($state.market_open) { 'market open' }
                   else { 'market closed' }
        Write-Row 'last cycle' "$($state.age) ago, $session" $colour

        $equity = if ($null -eq $state.equity) { 'unknown' } else { '${0:N0}' -f $state.equity }
        Write-Row 'decision' "$($state.action), equity $equity"

        if ($state.process_alive) {
            Write-Row 'process' "alive (pid $($state.pid))" 'Green'
        } elseif ($state.pid) {
            Write-Row 'process' "gone (pid $($state.pid))" 'Red'
        } else {
            Write-Row 'process' 'no pid file; the launcher is not running' 'Yellow'
        }

        Write-Row 'journal' "$($state.cycles) cycle(s)"
        if ($state.failed) { Write-Row 'warning' $state.failure 'Yellow' }

        # Beep only on the way in, so a long outage does not become background noise.
        if ($state.verdict -eq 'stale' -and $previous -ne 'stale') {
            [Console]::Beep(880, 400)
        }
        $previous = $state.verdict
    }

    Write-Host ''
    Write-Host "    refreshing every ${Every}s. Ctrl+C to close this panel;" -ForegroundColor DarkGray
    Write-Host '    the agent keeps running in the other window.' -ForegroundColor DarkGray
    Start-Sleep -Seconds $Every
}
