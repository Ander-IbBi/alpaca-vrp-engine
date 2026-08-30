# Start the agent for the day: check the machine is ready, choose the mode out loud,
# then hand off to the restart-on-crash wrapper with a watcher window beside it.
#
#   double-click start-agent.cmd                    # the normal way in
#   double-click stop-agent.cmd                     # the way out
#   .\scripts\start-agent.ps1                       # same thing from a terminal
#   .\scripts\start-agent.ps1 -Execute              # announce EXECUTE instead of asking
#   .\scripts\start-agent.ps1 -Execute -Unattended  # never asks; for a scheduled start
#   .\scripts\start-agent.ps1 -NoWatcher            # no second window
#
# Trading is never silent. Without -Execute you have to type EXECUTE in capitals; with
# it, the mode is announced and a countdown lets any keypress back out into a dry run.
# Either way nobody trades without having been told, which is the point.

param(
    [switch]$Execute,
    [switch]$Unattended,
    [switch]$NoWatcher,
    [int]$Interval = 180,
    [int]$ConfirmSeconds = 20,
    [int]$ExecuteCountdown = 10,
    [int]$PreflightRetries = 10,
    [int]$RetryDelay = 20
)

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$Host.UI.RawUI.WindowTitle = 'VRP Engine - agent'

function Stop-WithAdvice([string]$message) {
    Write-Host ''
    Write-Host "  $message" -ForegroundColor Red
    Write-Host ''
    exit 1
}

function Get-FailureReason($output) {
    # A Python traceback is forty lines of which one says what went wrong. Show that one
    # here and keep the rest on disk, or the useful line scrolls past unread.
    $lines = ($output | Out-String) -split "`r?`n"
    $blamed = $lines | Where-Object {
        $_ -match '(ERROR:|Error:|APIError|Unauthorized|Forbidden|Max retries|timed out|refused|getaddrinfo)' `
            -and $_ -notmatch '^\s+File "'
    }
    if ($blamed) { return ($blamed | Select-Object -First 1).Trim() }
    return (($lines | Where-Object { $_ -match '\S' } | Select-Object -Last 1)).Trim()
}

function Read-Answer([int]$seconds) {
    # Read-Host cannot time out, and a launcher that blocks forever on a question is a
    # launcher you cannot leave alone. Poll the console instead, and fall back to a
    # plain prompt when stdin is not a real console (a scheduled task, a pipe).
    try {
        $null = [Console]::KeyAvailable
    } catch {
        return (Read-Host '  mode')
    }

    $deadline = (Get-Date).AddSeconds($seconds)
    $typed = ''
    while ((Get-Date) -lt $deadline) {
        if ([Console]::KeyAvailable) {
            $key = [Console]::ReadKey($true)
            if ($key.Key -eq 'Enter') { return $typed }
            if ($key.Key -eq 'Backspace') {
                if ($typed.Length -gt 0) {
                    $typed = $typed.Substring(0, $typed.Length - 1)
                    Write-Host -NoNewline "`b `b"
                }
                continue
            }
            $typed += $key.KeyChar
            Write-Host -NoNewline $key.KeyChar
        } else {
            Start-Sleep -Milliseconds 100
        }
    }
    return $typed
}

function Test-AbortRequested([int]$seconds, [string]$prompt) {
    # The mirror image of Read-Answer: the mode is already decided, and the countdown
    # is the window in which you can change your mind. No console, no countdown — an
    # unattended start is exactly the case that must not stall here.
    try {
        while ([Console]::KeyAvailable) { [void][Console]::ReadKey($true) }
    } catch {
        Start-Sleep -Seconds $seconds
        return $false
    }

    for ($left = $seconds; $left -gt 0; $left--) {
        Write-Host ("`r  $prompt $left s  ") -NoNewline -ForegroundColor Yellow
        for ($tick = 0; $tick -lt 10; $tick++) {
            if ([Console]::KeyAvailable) {
                [void][Console]::ReadKey($true)
                Write-Host ''
                return $true
            }
            Start-Sleep -Milliseconds 100
        }
    }
    Write-Host ''
    return $false
}

Write-Host ''
Write-Host '  VRP Engine launcher' -ForegroundColor Cyan
Write-Host "  $repo" -ForegroundColor DarkGray
Write-Host ''

# --- Is one already running? -------------------------------------------------
#
# Two loops against the same account would each propose against a book the other is
# changing underneath it. Refuse rather than let that happen quietly.

$pidFile = Join-Path $repo 'data\agent.pid'
$alreadyRunning = $null
if (Test-Path $pidFile) {
    $recorded = 0
    $raw = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ([int]::TryParse(("$raw").Trim(), [ref]$recorded) -and $recorded -gt 0) {
        if (Get-Process -Id $recorded -ErrorAction SilentlyContinue) {
            $alreadyRunning = $recorded
        }
    }
    if (-not $alreadyRunning) {
        # A hard kill or a power cut leaves the file behind. Clearing it here is what
        # stops the panel from reporting a process that died days ago.
        Remove-Item $pidFile -ErrorAction SilentlyContinue
    }
}

if ($alreadyRunning) {
    Write-Host "  An agent is already running (pid $alreadyRunning)." -ForegroundColor Yellow
    Write-Host '  Double-click stop-agent.cmd first if you want to restart it.' -ForegroundColor DarkGray
    Write-Host ''
    exit 0
}

# --- Preflight: fail here, with advice, rather than inside a stack trace ------

Write-Host '  [1/3] uv on PATH        ' -NoNewline
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'missing' -ForegroundColor Red
    Stop-WithAdvice 'uv is not installed or not on PATH. Install it from https://docs.astral.sh/uv/, then open this launcher again.'
}
Write-Host 'ok' -ForegroundColor Green

Write-Host '  [2/3] .env file         ' -NoNewline
if (-not (Test-Path (Join-Path $repo '.env'))) {
    Write-Host 'missing' -ForegroundColor Red
    Stop-WithAdvice 'No .env file. Copy .env.example to .env and paste your Alpaca paper keys into it.'
}
Write-Host 'ok' -ForegroundColor Green

# At login the network is usually still coming up, so a single attempt here would fail
# for the most boring reason there is and leave the machine with no agent all day.
Write-Host '  [3/3] paper account     ' -NoNewline
$reached = $false
$smoke = ''
for ($attempt = 1; $attempt -le [Math]::Max($PreflightRetries, 1); $attempt++) {
    $smoke = & uv run smoke-paper 2>&1
    if ($LASTEXITCODE -eq 0) { $reached = $true; break }
    if ($attempt -eq 1) { Write-Host 'no answer yet' -ForegroundColor Yellow }
    Write-Host "        retrying in ${RetryDelay}s ($attempt/$PreflightRetries)" -ForegroundColor DarkGray
    Start-Sleep -Seconds $RetryDelay
}

if ($reached) {
    Write-Host 'ok' -ForegroundColor Green
} else {
    $log = Join-Path $repo 'data\preflight-error.log'
    New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
    ($smoke | Out-String) | Set-Content -Path $log -Encoding utf8
    Write-Host ''
    Write-Host "  $(Get-FailureReason $smoke)" -ForegroundColor DarkGray
    Write-Host "  Full output: $log" -ForegroundColor DarkGray
    if (-not $Unattended) {
        Stop-WithAdvice 'The paper account never answered. Check your connection and the keys in .env, then try again.'
    }
    # Unattended, carrying on is strictly better than giving up: the wrapper retries the
    # loop every 30 s forever, so the agent joins in by itself the moment the line is back.
    Write-Host '  The account is unreachable, but starting anyway: the wrapper keeps' -ForegroundColor Yellow
    Write-Host '  retrying, so the agent picks up as soon as the connection returns.' -ForegroundColor Yellow
}

# --- Mode: dry run unless you say otherwise, in capitals ---------------------

$live = $Execute.IsPresent
if ($live) {
    Write-Host ''
    if (Test-AbortRequested $ExecuteCountdown 'EXECUTE requested. Any key switches to a dry run -') {
        $live = $false
        Write-Host '  Backed out. Dry run it is.' -ForegroundColor Yellow
    }
} else {
    Write-Host ''
    Write-Host '  What should it do?' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '    [Enter]   Dry run - decide and journal, send nothing' -NoNewline
    Write-Host '   (safe default)' -ForegroundColor DarkGray
    Write-Host '    EXECUTE   Send real tickets to the Alpaca paper account' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  > ' -NoNewline
    $answer = Read-Answer $ConfirmSeconds
    Write-Host ''
    # Read-Host hands back $null when there is no console to read from, and a launcher
    # must never fall over on the question that keeps it from trading by accident.
    $live = (("$answer").Trim() -ceq 'EXECUTE')
}

Write-Host ''
if ($live) {
    Write-Host '  EXECUTE: tickets will be sent to the Alpaca paper account.' -ForegroundColor Green
} else {
    Write-Host '  DRY RUN: the engine will decide and journal, but send nothing.' -ForegroundColor Yellow
}
Write-Host "  Cadence: one cycle every ${Interval}s while the market is open." -ForegroundColor DarkGray
Write-Host '  To stop: double-click stop-agent.cmd (or just close this window).' -ForegroundColor DarkGray
Write-Host ''

# --- The watcher window ------------------------------------------------------

if (-not $NoWatcher) {
    Start-Process powershell -ArgumentList @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-NoExit',
        '-File', (Join-Path $PSScriptRoot 'watch-agent.ps1'),
        '-Interval', $Interval
    ) | Out-Null
    Write-Host '  A second window is now watching the heartbeat.' -ForegroundColor DarkGray
    Write-Host ''
}

# --- Hand off to the wrapper, which already knows how to restart -------------

& (Join-Path $PSScriptRoot 'run-forever.ps1') -Execute:$live -Interval $Interval
