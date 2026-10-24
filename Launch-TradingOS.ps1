<#
.SYNOPSIS
    TradingOS V2 Supervisor - P1 (Startup & Recovery) + P2 (Operational Health Monitoring)
.DESCRIPTION
    One-click startup and supervision for:
      - FastAPI Backend (127.0.0.1:8765)
      - React/Vite Frontend (127.0.0.1:5200)
      - Exactly one logical scanner scheduler instance (15-minute cycles)
    Features:
      - Mutex-based single supervisor instance enforcement
      - Non-destructive port conflict detection (never kills foreign processes)
      - Accounts for Python parent/child process trees on Windows
      - Exclusive pipeline lock preservation
      - Bounded restart retries with exponential backoff
      - Rate-limit awareness (never restarts healthy scheduler on scan rate limit)
      - Atomic JSON health report updates in update_reports\health_report.json
      - Protected port 5173
      - Read-only execution (demo/real orders remain OFF)
#>

param(
    [switch]$NoBrowser,
    [switch]$Once,
    [int]$PollInterval = 10
)

$ErrorActionPreference = "Stop"
$root = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$py = Join-Path $root ".venv\Scripts\python.exe"
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$reportsDir = Join-Path $root "update_reports"
$supervisorLog = Join-Path $reportsDir "supervisor.log"
$healthReportPath = Join-Path $reportsDir "health_report.json"

New-Item -ItemType Directory -Force -Path $reportsDir | Out-Null

# ---------------------------------------------------------
# MUTEX: PREVENT MULTIPLE SUPERVISOR INSTANCES
# ---------------------------------------------------------
$mutexName = "Global\TradingOS_V2_Supervisor_Mutex"
$createdNew = $false
$mutex = $null
try {
    $mutex = New-Object System.Threading.Mutex($true, $mutexName, [ref]$createdNew)
} catch {
    $createdNew = $false
}

if (-not $createdNew) {
    Write-Warning "TradingOS V2 Supervisor is already running. Exiting duplicate instance."
    Write-Host "View current health in: $healthReportPath" -ForegroundColor Yellow
    if ($mutex) { $mutex.Dispose() }
    exit 0
}

# ---------------------------------------------------------
# LOGGING HELPER
# ---------------------------------------------------------
function Log-Msg([string]$message, [string]$level = "INFO") {
    $stamp = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $line = "[$stamp] [$level] $message"
    try {
        Add-Content -Path $supervisorLog -Value $line -Encoding utf8
    } catch {}

    $color = switch ($level) {
        "ERROR"   { "Red" }
        "WARN"    { "Yellow" }
        "SUCCESS" { "Green" }
        "RESTART" { "Magenta" }
        default   { "Cyan" }
    }
    Write-Host $line -ForegroundColor $color
}

# ---------------------------------------------------------
# ATOMIC JSON WRITE HELPER
# ---------------------------------------------------------
function Write-AtomicJson([string]$path, [string]$jsonContent) {
    $parent = Split-Path -Parent $path
    $tempFile = Join-Path $parent ("health-" + [System.Guid]::NewGuid().ToString("N") + ".tmp")
    try {
        [System.IO.File]::WriteAllText($tempFile, $jsonContent, [System.Text.Encoding]::UTF8)
        Move-Item -LiteralPath $tempFile -Destination $path -Force
    } catch {
        if (Test-Path $tempFile) {
            Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
        }
        throw
    }
}

# ---------------------------------------------------------
# SERVICE HEALTH CHECKERS & PORT CONFLICT DETECTION
# ---------------------------------------------------------
function Get-PortProcess([int]$port) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $conn) { return $null }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($conn.OwningProcess)" -ErrorAction SilentlyContinue
    return [PSCustomObject]@{
        Port = $port
        Pid = $conn.OwningProcess
        Name = if ($proc) { $proc.Name } else { "Unknown" }
        CommandLine = if ($proc) { $proc.CommandLine } else { "" }
    }
}

function Test-BackendService {
    $portInfo = Get-PortProcess 8765
    if (-not $portInfo) {
        return [PSCustomObject]@{ Status = "OFFLINE"; Pid = $null; Detail = "Port 8765 not listening" }
    }
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -Method Get -TimeoutSec 3 -ErrorAction Stop
        if ($resp.service -eq "TradingOS V2 Backend" -and $resp.status -eq "online") {
            return [PSCustomObject]@{ Status = "ONLINE"; Pid = $portInfo.Pid; Detail = "Healthy TradingOS Backend" }
        }
    } catch {}

    # Port is listening but does NOT respond as TradingOS Backend -> Port Conflict! Never kill it.
    return [PSCustomObject]@{
        Status = "CONFLICT"
        Pid = $portInfo.Pid
        Name = $portInfo.Name
        Detail = "Port 8765 occupied by unrelated process '$($portInfo.Name)' (PID $($portInfo.Pid))"
    }
}

function Test-FrontendService {
    $portInfo = Get-PortProcess 5200
    if (-not $portInfo) {
        return [PSCustomObject]@{ Status = "OFFLINE"; Pid = $null; Detail = "Port 5200 not listening" }
    }
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:5200/" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($resp.StatusCode -eq 200 -and ($resp.Content -match "TradingOS" -or $resp.Content -match "market-terminal" -or $resp.Content -match "Trading Terminal")) {
            return [PSCustomObject]@{ Status = "ONLINE"; Pid = $portInfo.Pid; Detail = "Healthy TradingOS Frontend" }
        }
    } catch {}

    # Port is listening but does not serve TradingOS frontend -> Port Conflict! Never kill it.
    return [PSCustomObject]@{
        Status = "CONFLICT"
        Pid = $portInfo.Pid
        Name = $portInfo.Name
        Detail = "Port 5200 occupied by unrelated process '$($portInfo.Name)' (PID $($portInfo.Pid))"
    }
}

# ---------------------------------------------------------
# SCANNER LOGICAL INSTANCE DETECTOR (ACCOUNTS FOR PARENT/CHILD)
# ---------------------------------------------------------
function Get-ScannerStatus {
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match "^(python|pythonw)(\.exe)?$" -and
        $_.CommandLine -match "scanner_scheduler\.py" -and
        ($_.CommandLine -like "*$([System.IO.Path]::GetFileName($root))*" -or $_.CommandLine -like "*TradingOS*")
    })

    if ($all.Count -eq 0) {
        return [PSCustomObject]@{
            Status = "STOPPED"
            LogicalCount = 0
            Pids = @()
            RootPid = $null
            Detail = "No scanner scheduler running"
        }
    }

    $allPids = $all.ProcessId
    # Logical roots: processes whose parent is NOT in the set of scanner processes
    $rootProcs = @($all | Where-Object { $_.ParentProcessId -notin $allPids })
    $logicalCount = $rootProcs.Count

    if ($logicalCount -eq 1) {
        return [PSCustomObject]@{
            Status = "RUNNING"
            LogicalCount = 1
            Pids = $allPids
            RootPid = $rootProcs[0].ProcessId
            Detail = "Single logical scanner instance active (Root PID $($rootProcs[0].ProcessId))"
        }
    } elseif ($logicalCount -gt 1) {
        return [PSCustomObject]@{
            Status = "CONFLICT"
            LogicalCount = $logicalCount
            Pids = $allPids
            RootPid = $null
            Detail = "Multiple ($logicalCount) logical scanner instances detected"
        }
    } else {
        # Fallback if circular parentage
        return [PSCustomObject]@{
            Status = "RUNNING"
            LogicalCount = 1
            Pids = $allPids
            RootPid = $allPids[0]
            Detail = "Scanner scheduler active"
        }
    }
}

# ---------------------------------------------------------
# COMPONENT STARTERS
# ---------------------------------------------------------
function Start-BackendComponent {
    $chk = Test-BackendService
    if ($chk.Status -eq "ONLINE") {
        Log-Msg "Backend is already online (PID $($chk.Pid)). Reusing." "SUCCESS"
        return $true
    }
    if ($chk.Status -eq "CONFLICT") {
        Log-Msg "CANNOT START BACKEND: $($chk.Detail). Never terminating foreign process." "ERROR"
        return $false
    }

    Log-Msg "Starting Backend on 127.0.0.1:8765..." "INFO"
    $outLog = Join-Path $reportsDir "backend-out.log"
    $errLog = Join-Path $reportsDir "backend-error.log"

    Start-Process -FilePath $py `
        -ArgumentList @("-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8765") `
        -WorkingDirectory $backendDir `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -WindowStyle Hidden

    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        $chk = Test-BackendService
        if ($chk.Status -eq "ONLINE") {
            Log-Msg "Backend online (PID $($chk.Pid))." "SUCCESS"
            return $true
        }
    }
    Log-Msg "Backend failed to respond on port 8765 after 15s." "ERROR"
    return $false
}

function Start-FrontendComponent {
    $chk = Test-FrontendService
    if ($chk.Status -eq "ONLINE") {
        Log-Msg "Frontend is already online (PID $($chk.Pid)). Reusing." "SUCCESS"
        return $true
    }
    if ($chk.Status -eq "CONFLICT") {
        Log-Msg "CANNOT START FRONTEND: $($chk.Detail). Never terminating foreign process." "ERROR"
        return $false
    }

    Log-Msg "Starting Frontend on 127.0.0.1:5200..." "INFO"
    $outLog = Join-Path $reportsDir "frontend-out.log"
    $errLog = Join-Path $reportsDir "frontend-error.log"

    Start-Process -FilePath "cmd.exe" `
        -ArgumentList @("/c", "npm run dev") `
        -WorkingDirectory $frontendDir `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -WindowStyle Hidden

    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        $chk = Test-FrontendService
        if ($chk.Status -eq "ONLINE") {
            Log-Msg "Frontend online (PID $($chk.Pid))." "SUCCESS"
            return $true
        }
    }
    Log-Msg "Frontend failed to respond on port 5200 after 15s." "ERROR"
    return $false
}

function Start-ScannerComponent {
    $scanner = Get-ScannerStatus
    if ($scanner.Status -eq "RUNNING") {
        Log-Msg "Scanner scheduler is already running (Logical Count: 1, PIDs: $($scanner.Pids -join ', ')). Reusing." "SUCCESS"
        return $true
    }
    if ($scanner.Status -eq "CONFLICT") {
        Log-Msg "WARNING: Multiple scanner instances detected ($($scanner.LogicalCount)). Not starting another." "WARN"
        return $false
    }

    Log-Msg "Starting Scanner Scheduler (read-only 15m cycles)..." "INFO"
    $outLog = Join-Path $reportsDir "scanner-out.log"
    $errLog = Join-Path $reportsDir "scanner-error.log"

    Start-Process -FilePath $py `
        -ArgumentList @("-u", "scanner_scheduler.py") `
        -WorkingDirectory $backendDir `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -WindowStyle Hidden

    for ($i = 0; $i -lt 5; $i++) {
        Start-Sleep -Seconds 1
        $scanner = Get-ScannerStatus
        if ($scanner.Status -eq "RUNNING") {
            Log-Msg "Scanner Scheduler started (Root PID $($scanner.RootPid))." "SUCCESS"
            return $true
        }
    }
    Log-Msg "Scanner Scheduler started but process verification pending." "WARN"
    return $true
}

# ---------------------------------------------------------
# HEALTH REPORT GENERATOR (P2)
# ---------------------------------------------------------
function Update-OperationalHealthReport(
    [string]$backendStatus,
    $backendPid,
    [string]$frontendStatus,
    $frontendPid,
    [string]$scannerStatus,
    [int]$scannerLogicalCount,
    $scannerPids,
    $restarts
) {
    try {
        $pidsArg = ($scannerPids -join ",")
        $uptimeVal = [int]([DateTime]::UtcNow - $script:startTime).TotalSeconds
        $healthPy = Join-Path $backendDir "operational_health.py"

        $cliArgs = @(
            $healthPy,
            "--backend-status", $backendStatus,
            "--frontend-status", $frontendStatus,
            "--logical-count", $scannerLogicalCount,
            "--supervisor-status", "RUNNING",
            "--supervisor-pid", $PID,
            "--uptime", $uptimeVal,
            "--restarts-backend", $restarts.backend,
            "--restarts-frontend", $restarts.frontend,
            "--restarts-scanner", $restarts.scanner
        )
        if ($backendPid) { $cliArgs += @("--backend-pid", $backendPid) }
        if ($frontendPid) { $cliArgs += @("--frontend-pid", $frontendPid) }
        if ($scannerStatus -eq "RUNNING") { $cliArgs += "--scheduler-running" }
        if ($pidsArg) { $cliArgs += @("--pids", $pidsArg) }

        & $py $cliArgs | Out-Null
    } catch {
        Log-Msg "Health report generation notice: $($_.Exception.Message)" "WARN"
    }
}

# ---------------------------------------------------------
# MAIN SUPERVISOR STARTUP & MONITORING LOOP
# ---------------------------------------------------------
$script:startTime = [DateTime]::UtcNow

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "           TRADINGOS V2 - P1/P2 SUPERVISOR SYSTEM"          -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Project Root: $root"
Write-Host "Supervisor PID: $PID"
Write-Host "Backend URL:    http://127.0.0.1:8765"
Write-Host "Frontend URL:   http://127.0.0.1:5200"
Write-Host "Protected Port: 5173 (untouched)"
Write-Host "Trading Orders: DISABLED (Read-Only 1% Risk Policy)"
Write-Host ""

Log-Msg "TradingOS V2 Supervisor started (PID $PID)" "INFO"

# Track restart attempts and sustained health
$restartStats = @{
    backend  = 0
    frontend = 0
    scanner  = 0
}
$backoffUntil = @{
    backend  = [DateTime]::MinValue
    frontend = [DateTime]::MinValue
    scanner  = [DateTime]::MinValue
}
$lastHealthy = @{
    backend  = [DateTime]::UtcNow
    frontend = [DateTime]::UtcNow
    scanner  = [DateTime]::UtcNow
}
$maxRetries = 5

try {
    # 1. INITIAL STARTUP
    $bOk = Start-BackendComponent
    $fOk = Start-FrontendComponent
    $sOk = Start-ScannerComponent

    # 2. OPEN BROWSER (Only after confirming both frontend and backend are healthy)
    if (-not $NoBrowser) {
        $bHealth = Test-BackendService
        $fHealth = Test-FrontendService
        if ($bHealth.Status -eq "ONLINE" -and $fHealth.Status -eq "ONLINE") {
            Log-Msg "Both Frontend and Backend confirmed healthy. Launching browser..." "SUCCESS"
            Start-Process "http://127.0.0.1:5200"
        } else {
            Log-Msg "Browser not opened because one or more services are not healthy." "WARN"
        }
    }

    # Initial health report
    $bCur = Test-BackendService
    $fCur = Test-FrontendService
    $sCur = Get-ScannerStatus
    Update-OperationalHealthReport $bCur.Status $bCur.Pid $fCur.Status $fCur.Pid $sCur.Status $sCur.LogicalCount $sCur.Pids $restartStats

    if ($Once) {
        Log-Msg "Single-run mode (-Once) complete. Exiting." "INFO"
        return
    }

    Write-Host ""
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host "  ACTIVE SUPERVISOR RUNNING (Ctrl+C to stop supervision)"   -ForegroundColor Green
    Write-Host "  To stop all TradingOS processes, run: .\Stop-TradingOS.ps1" -ForegroundColor Yellow
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host ""

    # 3. SUPERVISION LOOP
    $loopCount = 0
    while ($true) {
        Start-Sleep -Seconds $PollInterval
        $loopCount++
        $now = [DateTime]::UtcNow

        # A. Check Backend
        $bCheck = Test-BackendService
        if ($bCheck.Status -eq "ONLINE") {
            if (($now - $lastHealthy.backend).TotalSeconds -ge 60) {
                $restartStats.backend = 0  # reset on sustained health
            }
        } elseif ($bCheck.Status -eq "OFFLINE") {
            if ($now -ge $backoffUntil.backend) {
                if ($restartStats.backend -lt $maxRetries) {
                    $restartStats.backend++
                    $waitSec = [Math]::Min(60, 3 * [Math]::Pow(2, $restartStats.backend - 1))
                    $backoffUntil.backend = $now.AddSeconds($waitSec)
                    Log-Msg "BACKEND UNEXPECTEDLY STOPPED. Recovery restart attempt $($restartStats.backend)/$maxRetries (Backoff: ${waitSec}s)..." "RESTART"
                    Start-BackendComponent | Out-Null
                    $lastHealthy.backend = [DateTime]::UtcNow
                } else {
                    Log-Msg "Backend exceeded max restart retries ($maxRetries). Manual intervention required." "ERROR"
                }
            }
        } elseif ($bCheck.Status -eq "CONFLICT") {
            Log-Msg "PORT CONFLICT on 8765: $($bCheck.Detail)" "ERROR"
        }

        # B. Check Frontend
        $fCheck = Test-FrontendService
        if ($fCheck.Status -eq "ONLINE") {
            if (($now - $lastHealthy.frontend).TotalSeconds -ge 60) {
                $restartStats.frontend = 0  # reset on sustained health
            }
        } elseif ($fCheck.Status -eq "OFFLINE") {
            if ($now -ge $backoffUntil.frontend) {
                if ($restartStats.frontend -lt $maxRetries) {
                    $restartStats.frontend++
                    $waitSec = [Math]::Min(60, 3 * [Math]::Pow(2, $restartStats.frontend - 1))
                    $backoffUntil.frontend = $now.AddSeconds($waitSec)
                    Log-Msg "FRONTEND UNEXPECTEDLY STOPPED. Recovery restart attempt $($restartStats.frontend)/$maxRetries (Backoff: ${waitSec}s)..." "RESTART"
                    Start-FrontendComponent | Out-Null
                    $lastHealthy.frontend = [DateTime]::UtcNow
                } else {
                    Log-Msg "Frontend exceeded max restart retries ($maxRetries). Manual intervention required." "ERROR"
                }
            }
        } elseif ($fCheck.Status -eq "CONFLICT") {
            Log-Msg "PORT CONFLICT on 5200: $($fCheck.Detail)" "ERROR"
        }

        # C. Check Scanner Scheduler
        # Note: Do not restart a running scheduler just because a scan attempt failed due to rate limits or timeouts!
        # Only restart if the scheduler process itself has terminated (Status == STOPPED).
        $sCheck = Get-ScannerStatus
        if ($sCheck.Status -eq "RUNNING") {
            if (($now - $lastHealthy.scanner).TotalSeconds -ge 60) {
                $restartStats.scanner = 0  # reset on sustained health
            }
        } elseif ($sCheck.Status -eq "STOPPED") {
            if ($now -ge $backoffUntil.scanner) {
                if ($restartStats.scanner -lt $maxRetries) {
                    $restartStats.scanner++
                    $waitSec = [Math]::Min(60, 5 * [Math]::Pow(2, $restartStats.scanner - 1))
                    $backoffUntil.scanner = $now.AddSeconds($waitSec)
                    Log-Msg "SCANNER SCHEDULER STOPPED. Recovery restart attempt $($restartStats.scanner)/$maxRetries (Backoff: ${waitSec}s)..." "RESTART"
                    Start-ScannerComponent | Out-Null
                    $lastHealthy.scanner = [DateTime]::UtcNow
                } else {
                    Log-Msg "Scanner Scheduler exceeded max restart retries ($maxRetries). Manual intervention required." "ERROR"
                }
            }
        } elseif ($sCheck.Status -eq "CONFLICT") {
            Log-Msg "MULTIPLE SCANNER INSTANCES: $($sCheck.Detail)" "WARN"
        }

        # D. Update Operational Health Report (P2)
        Update-OperationalHealthReport $bCheck.Status $bCheck.Pid $fCheck.Status $fCheck.Pid $sCheck.Status $sCheck.LogicalCount $sCheck.Pids $restartStats

        # E. Periodic Console Pulse (every 6 intervals / ~60s)
        if ($loopCount % 6 -eq 0) {
            $uptime = ($now - $script:startTime).ToString("hh\:mm\:ss")
            Write-Host "[$([DateTime]::UtcNow.ToString('HH:mm:ss'))] Heartbeat: Backend $($bCheck.Status) | Frontend $($fCheck.Status) | Scanner $($sCheck.Status) (Uptime: $uptime)" -ForegroundColor DarkGray
        }
    }
} finally {
    Log-Msg "TradingOS V2 Supervisor shutting down. Releasing mutex." "INFO"
    if ($mutex) {
        try { $mutex.ReleaseMutex() } catch {}
        $mutex.Dispose()
    }
}
