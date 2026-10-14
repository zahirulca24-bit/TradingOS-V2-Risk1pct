<#
.SYNOPSIS
    Stop-TradingOS.ps1 — Gracefully stop all TradingOS V2 components and supervisor
.DESCRIPTION
    Stops:
      - TradingOS Supervisor (releasing mutex)
      - Scanner scheduler process(es)
      - Backend (127.0.0.1:8765)
      - Frontend (127.0.0.1:5200)
    Protected:
      - Port 5173 is never modified or touched
      - Unrelated foreign processes are never touched
#>

$root = "E:\TradingOS-V2-Risk1pct"
$reportsDir = Join-Path $root "update_reports"
$supervisorLog = Join-Path $reportsDir "supervisor.log"
$py = Join-Path $root ".venv\Scripts\python.exe"

function Log-Msg([string]$message, [string]$level = "INFO") {
    $stamp = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $line = "[$stamp] [$level] $message"
    try { Add-Content -Path $supervisorLog -Value $line -Encoding utf8 } catch {}
    Write-Host $line -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Yellow
Write-Host "             STOPPING TRADINGOS V2 SYSTEM"                 -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Yellow

# 1. Stop Scanner Scheduler Processes
$scanners = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -match "^(python|pythonw)(\.exe)?$" -and
    $_.CommandLine -match "scanner_scheduler\.py" -and
    $_.CommandLine -like "*TradingOS-V2-Risk1pct*"
})
foreach ($p in $scanners) {
    Log-Msg "Stopping Scanner Scheduler PID $($p.ProcessId)..." "INFO"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}

# 2. Stop Backend (Port 8765) if it belongs to TradingOS
$bConn = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($bConn) {
    $bProc = Get-CimInstance Win32_Process -Filter "ProcessId = $($bConn.OwningProcess)" -ErrorAction SilentlyContinue
    if ($bProc -and ($bProc.CommandLine -like "*TradingOS-V2-Risk1pct*" -or $bProc.CommandLine -match "uvicorn\s+main:app")) {
        Log-Msg "Stopping Backend PID $($bProc.ProcessId)..." "INFO"
        Stop-Process -Id $bProc.ProcessId -Force -ErrorAction SilentlyContinue
    } else {
        Log-Msg "Port 8765 occupied by foreign process ($($bProc.Name)). Not terminating." "WARN"
    }
}

# 3. Stop Frontend (Port 5200) if it belongs to TradingOS
$fConn = Get-NetTCPConnection -LocalPort 5200 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($fConn) {
    $fProc = Get-CimInstance Win32_Process -Filter "ProcessId = $($fConn.OwningProcess)" -ErrorAction SilentlyContinue
    if ($fProc -and ($fProc.CommandLine -like "*TradingOS-V2-Risk1pct*" -or $fProc.CommandLine -match "vite")) {
        Log-Msg "Stopping Frontend PID $($fProc.ProcessId)..." "INFO"
        Stop-Process -Id $fProc.ProcessId -Force -ErrorAction SilentlyContinue
    } else {
        Log-Msg "Port 5200 occupied by foreign process ($($fProc.Name)). Not terminating." "WARN"
    }
}

# 4. Stop any Supervisor powershell processes running Launch-TradingOS.ps1
$supervisors = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.ProcessId -ne $PID -and
    $_.Name -match "^powershell(\.exe)?$" -and
    $_.CommandLine -match "Launch-TradingOS\.ps1"
})
foreach ($s in $supervisors) {
    Log-Msg "Stopping Supervisor PID $($s.ProcessId)..." "INFO"
    Stop-Process -Id $s.ProcessId -Force -ErrorAction SilentlyContinue
}

# 5. Update Health Report to STOPPED
try {
    $healthPy = Join-Path $root "backend\operational_health.py"
    & $py $healthPy --backend-status "STOPPED" --frontend-status "STOPPED" --logical-count 0 --supervisor-status "STOPPED" | Out-Null
} catch {}

Log-Msg "All TradingOS V2 components stopped. Port 5173 was untouched." "SUCCESS"
Write-Host ""
