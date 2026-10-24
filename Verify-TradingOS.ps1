param([string]$ProjectRoot = "")
$ErrorActionPreference = 'Stop'
$resolvedRoot = if ($ProjectRoot) { $ProjectRoot } elseif ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$root = [System.IO.Path]::GetFullPath($resolvedRoot)
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'Run Setup-TradingOS.ps1 first.' }
New-Item -ItemType Directory -Force -Path (Join-Path $root 'update_reports') | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$report = Join-Path $root "update_reports\verification-$stamp.txt"
Push-Location $root
try {
  "TradingOS V2 verification $stamp" | Out-File -FilePath $report -Encoding utf8
  & $py -m compileall -q backend 2>&1 | Out-File -FilePath $report -Append -Encoding utf8
  if ($LASTEXITCODE -ne 0) { throw 'Python syntax check failed' }
  $env:PYTHONPATH = (Join-Path $root 'backend')
  $env:TRADINGOS_DISABLE_INNER_SCHEDULER = "1"
  $env:TRADINGOS_DISABLE_TELEGRAM = "1"
  $testOut = Join-Path $root 'update_reports\unittest-stdout.txt'
  $testErr = Join-Path $root 'update_reports\unittest-stderr.txt'
  $testProcess = Start-Process -FilePath $py -ArgumentList @('-m','unittest','discover','-s','backend','-p','test*.py','-v') -WorkingDirectory $root -Wait -PassThru -NoNewWindow -RedirectStandardOutput $testOut -RedirectStandardError $testErr
  Get-Content $testOut, $testErr | Out-File -FilePath $report -Append -Encoding utf8
  $global:LASTEXITCODE = $testProcess.ExitCode
  if ($LASTEXITCODE -ne 0) { throw 'Python tests failed' }
  Push-Location (Join-Path $root 'frontend')
  try {
    $npmOut = Join-Path $root 'update_reports\npm-build-stdout.txt'
    $npmErr = Join-Path $root 'update_reports\npm-build-stderr.txt'
    $npmProcess = Start-Process -FilePath "cmd.exe" -ArgumentList @('/c', 'npm run build') -WorkingDirectory (Join-Path $root 'frontend') -Wait -PassThru -NoNewWindow -RedirectStandardOutput $npmOut -RedirectStandardError $npmErr
    Get-Content $npmOut, $npmErr -ErrorAction SilentlyContinue | Out-File -FilePath $report -Append -Encoding utf8
    $global:LASTEXITCODE = $npmProcess.ExitCode
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
  } finally { Pop-Location }
  "STATUS: PASS (local syntax, all backend tests, npm build)" | Out-File $report -Append -Encoding utf8
  Write-Host "VERIFICATION PASS: $report" -ForegroundColor Green

} catch {
  "STATUS: FAIL - $($_.Exception.Message)" | Out-File $report -Append -Encoding utf8
  Write-Host "VERIFICATION FAILED: $report" -ForegroundColor Red
  throw
} finally { Pop-Location }
