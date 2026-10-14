param([switch]$StartScheduler)
$root = $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'Run Setup-TradingOS.ps1 first.' }
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
Start-Process powershell -ArgumentList @('-NoExit','-Command',"Set-Location '$backend'; & '$py' -m uvicorn main:app --host 127.0.0.1 --port 8765")
Start-Process powershell -ArgumentList @('-NoExit','-Command',"Set-Location '$frontend'; npm run dev -- --host 127.0.0.1")
if ($StartScheduler) {
  Start-Process powershell -ArgumentList @('-NoExit','-Command',"Set-Location '$backend'; & '$py' scanner_scheduler.py")
}
Write-Host 'Started backend and frontend (plus scheduler when requested). Trading execution is disabled.'
Write-Host 'Open http://127.0.0.1:5173'
