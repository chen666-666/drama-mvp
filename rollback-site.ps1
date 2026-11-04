param(
    [Parameter(Mandatory=$true)][string]$Server,
    [Parameter(Mandatory=$true)][string]$RemoteProject,
    [int]$Port = 22,
    [string]$IdentityFile,
    [string]$RemotePython = "data/site-runtime/bin/python",
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
if ($Server -notmatch '^[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+$' -or
    $RemoteProject -notmatch '^/[A-Za-z0-9_./-]+$' -or $RemoteProject.Contains('..') -or
    $RemotePython -notmatch '^[A-Za-z0-9_./-]+$' -or $RemotePython.Contains('..') -or $Port -lt 1 -or $Port -gt 65535) { throw '服务器或目录参数无效。' }
$sshArgs = @('-p', "$Port", '-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes')
if ($IdentityFile) { $sshArgs += @('-i', $IdentityFile) }
$arguments = "--root '$RemoteProject' --rollback"
if ($Apply) { $arguments += ' --apply' }
& ssh @sshArgs $Server "cd '$RemoteProject' && '$RemotePython' scripts/activate_site_release.py $arguments"
if ($LASTEXITCODE -ne 0) { throw '回退预检或执行失败，请查看服务器结果。' }
if ($Apply) {
    # The old last-published manifest must never be reused after a server-side rollback.
    $pointer = Join-Path $PSScriptRoot 'data/publish/last-published.json'
    if (Test-Path -LiteralPath $pointer) { Move-Item -LiteralPath $pointer -Destination ($pointer + '.before-rollback-' + (Get-Date -Format 'yyyyMMddHHmmss')) }
    Write-Output '已回退；下次发布前需从服务器 current.json 核对当前发布清单，并恢复本机 last-published.json。'
}
