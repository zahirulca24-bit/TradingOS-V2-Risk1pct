param([string]$ProjectRoot = "")
$ErrorActionPreference = 'Stop'
$source = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$destination = if ($ProjectRoot) { [System.IO.Path]::GetFullPath($ProjectRoot) } else { [System.IO.Path]::GetFullPath($source) }
if ([System.IO.Path]::GetFullPath($source).TrimEnd('\') -eq $destination.TrimEnd('\')) {
  Write-Host 'Source folder is already the target; no copy needed.'
} else {
  if (Test-Path $destination) {
    $contents = @(Get-ChildItem -LiteralPath $destination -Force -ErrorAction SilentlyContinue)
    if ($contents.Count -gt 0) { throw "Safety stop: destination already has files: $destination" }
  }
  New-Item -ItemType Directory -Path $destination -Force | Out-Null
  Copy-Item -Path (Join-Path $source '*') -Destination $destination -Recurse -Force
}
Set-Location $destination
if (-not (Test-Path '.\.venv\Scripts\python.exe')) {
  py -3 -m venv .venv
  if ($LASTEXITCODE -ne 0) { throw 'Virtualenv creation failed. Install Python 3.11+.' }
}
$py = Join-Path $destination '.venv\Scripts\python.exe'
& $py -m pip install -r (Join-Path $destination 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python dependency setup failed' }
Set-Location (Join-Path $destination 'frontend')
npm install
if ($LASTEXITCODE -ne 0) { throw 'npm install failed; internet may be required' }
npm run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
Set-Location $destination
if (-not (Test-Path '.\backend\.env')) {
  Write-Host 'IMPORTANT: backend\.env is absent. Copy your own original .env locally; NEVER upload or print its contents.'
}
Write-Host 'SETUP OK. Run Verify-TradingOS.ps1 next.' -ForegroundColor Green
