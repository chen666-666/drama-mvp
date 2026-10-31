$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$workerPython = Join-Path $PSScriptRoot '.runtime/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $workerPython)) { $workerPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe' }
if (Test-Path -LiteralPath $workerPython) {
    $logDir = Join-Path $PSScriptRoot 'data/logs'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    Start-Process -FilePath $workerPython -ArgumentList '-m workers.download' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'download.out.log') -RedirectStandardError (Join-Path $logDir 'download.err.log') | Out-Null
}
if (Test-Path -LiteralPath '.runtime/Scripts/python.exe') {
    & '.runtime/Scripts/python.exe' main.py
} elseif (Test-Path -LiteralPath '.venv/Scripts/python.exe') {
    & '.venv/Scripts/python.exe' main.py
} else {
    & python main.py
}
