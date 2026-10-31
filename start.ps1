$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Test-Path -LiteralPath '.runtime/Scripts/python.exe') {
    & '.runtime/Scripts/python.exe' main.py
} elseif (Test-Path -LiteralPath '.venv/Scripts/python.exe') {
    & '.venv/Scripts/python.exe' main.py
} else {
    & python main.py
}
