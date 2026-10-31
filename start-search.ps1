$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$toolPython = Join-Path $PSScriptRoot 'data/tools/douk/.runtime/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $toolPython)) {
    throw 'DouK is not installed. Run .runtime/Scripts/python.exe scripts/install_douk.py first.'
}
$bridgeReady = $false
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:5555/health' -TimeoutSec 2
    $bridgeReady = $health.tool -eq 'DouK'
} catch {}
if (-not $bridgeReady) {
    $logDir = Join-Path $PSScriptRoot 'data/logs'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    Start-Process -FilePath $toolPython -ArgumentList ('"' + (Join-Path $PSScriptRoot 'scripts/run_douk.py') + '"') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'search.out.log') -RedirectStandardError (Join-Path $logDir 'search.err.log') | Out-Null
}
& (Join-Path $PSScriptRoot 'start.ps1')
