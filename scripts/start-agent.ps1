# Start the agent for the day: check the machine is ready, then spawn the
# restart-on-crash wrapper in the background with a watcher window beside it.
# Closing this launcher does not stop the agent; stop-agent.cmd does.
#
#   double-click start-agent.cmd            # the normal way in
#   double-click stop-agent.cmd             # the way out
#   .\scripts\start-agent.ps1               # same thing from a terminal
#   .\scripts\start-agent.ps1 -Unattended   # start even if Alpaca rejects the keys
#   .\scripts\start-agent.ps1 -DryRun       # rehearse only; for development
#   .\scripts\start-agent.ps1 -NoWatcher    # no second window
#
# Switching it on is the decision to trade. There is no mode question and no
# confirmation: from here the agent opens, manages and closes positions on its own
# judgement, and does nothing when it sees nothing worth doing. That call is the
# strategy's and the risk layer's, never the operator's.

param(
    [switch]$DryRun,
    [switch]$Unattended,
    [switch]$NoWatcher,
    # Executing is the default now. Accepted and ignored so an older desktop shortcut
    # or scheduled task does not fail on an unknown parameter.
    [switch]$Execute,
    [int]$Interval = 180,
    # Short on purpose: an unreachable account no longer blocks the start, so there is
    # nothing to gain by making you watch a three-minute countdown at the open.
    [int]$PreflightRetries = 3,
    [int]$RetryDelay = 10
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

function Test-CredentialFailure($output) {
    # The one distinction that matters at start-up: a broken line heals by itself and a
    # wrong key never does. Waiting out a rejected key wastes the session; refusing to
    # start over a flaky connection wastes it just as thoroughly.
    return (($output | Out-String) -match '(401|403|[Uu]nauthorized|[Ff]orbidden|invalid.*key|ALPACA_API_KEY)')
}

function Test-CommandRunning([string]$needle) {
    $self = $PID
    $hit = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessId -ne $self -and $_.CommandLine -like "*$needle*"
    }
    return [bool]$hit
}

function Start-WatcherWindow {
    if (Test-CommandRunning 'watch-agent.ps1') {
        Write-Host '  Heartbeat panel is already open.' -ForegroundColor DarkGray
        return
    }
    Start-Process powershell -ArgumentList @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-NoExit',
        '-File', (Join-Path $PSScriptRoot 'watch-agent.ps1'),
        '-Interval', "$Interval"
    ) | Out-Null
    Write-Host '  A second window is now watching the heartbeat.' -ForegroundColor DarkGray
}

function Wait-WrapperAlive([string]$pidPath, [int]$seconds = 15) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 400
        if (-not (Test-Path $pidPath)) { continue }
        $recorded = 0
        $raw = (Get-Content $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ([int]::TryParse(("$raw").Trim(), [ref]$recorded) -and $recorded -gt 0) {
            if (Get-Process -Id $recorded -ErrorAction SilentlyContinue) {
                return $recorded
            }
        }
    }
    return $null
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
    if (-not $NoWatcher) { Start-WatcherWindow }
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

    if ((Test-CredentialFailure $smoke) -and -not $Unattended) {
        Stop-WithAdvice 'Alpaca rejected those keys. Paste your paper keys into .env from https://app.alpaca.markets/paper/dashboard/overview, then open this launcher again.'
    }

    # Anything else reads as a connection problem, and giving up on one is the expensive
    # mistake: the loop survives API faults cycle by cycle and the wrapper restarts the
    # process if it ever dies, so the agent joins in by itself when the line comes back.
    Write-Host '  Cannot reach Alpaca right now. Starting anyway - the agent retries on' -ForegroundColor Yellow
    Write-Host '  its own and picks up as soon as the connection returns.' -ForegroundColor Yellow
}

# --- Mode: it trades, because that is what switching it on means -------------

$dry = $DryRun.IsPresent

Write-Host ''
if ($dry) {
    Write-Host '  DRY RUN: the engine will decide and journal, but send nothing.' -ForegroundColor Yellow
    Write-Host '  Development mode. Drop -DryRun to let the agent trade.' -ForegroundColor DarkGray
} else {
    Write-Host '  AUTONOMOUS: approved tickets go straight to the Alpaca paper account.' -ForegroundColor Green
    Write-Host '  Nothing will ask you to confirm a trade, now or later.' -ForegroundColor DarkGray
}
Write-Host "  Cadence: one cycle every ${Interval}s while the market is open." -ForegroundColor DarkGray
Write-Host '  To stop: double-click stop-agent.cmd.' -ForegroundColor DarkGray
Write-Host '  Closing this window (or the panel) leaves the agent running.' -ForegroundColor DarkGray
Write-Host ''

# --- Hand off to the wrapper in its own process ------------------------------
#
# The loop used to live in this window. Closing it (the natural thing once the
# panel is open) killed the agent and left a stale pid file, which is exactly
# what the panel then reported as "the launcher started something and it is gone".
# The wrapper now runs hidden; this window is only the launcher.

$foreverArgs = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', (Join-Path $PSScriptRoot 'run-forever.ps1'),
    '-Interval', "$Interval"
)
if ($dry) { $foreverArgs += '-DryRun' }

Start-Process -FilePath powershell -ArgumentList $foreverArgs -WindowStyle Hidden -WorkingDirectory $repo | Out-Null

$wrapperPid = Wait-WrapperAlive $pidFile
if (-not $wrapperPid) {
    $log = Join-Path $repo 'data\agent.log'
    Stop-WithAdvice "The wrapper did not stay up. Check $log"
}

Write-Host "  Agent running in the background (pid $wrapperPid)." -ForegroundColor Green
Write-Host "  Cycle log: $(Join-Path $repo 'data\agent.log')" -ForegroundColor DarkGray
Write-Host ''

if (-not $NoWatcher) {
    Start-WatcherWindow
    Write-Host ''
}
