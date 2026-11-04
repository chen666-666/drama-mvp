$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& '.runtime/Scripts/python.exe' -m workers.episode_discovery
