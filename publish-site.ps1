param(
    [Parameter(Mandatory=$true)][string]$ReleaseId,
    [Parameter(Mandatory=$true)][string]$Server,
    [Parameter(Mandatory=$true)][string]$RemoteProject,
    [int]$Port = 22,
    [string]$IdentityFile,
    [string]$RemotePython = "data/site-runtime/bin/python",
    [string]$Domain = "https://drama-mvp.cfd",
    [string]$RcloneRemote,
    [switch]$Apply,
    [switch]$InventoryConfirmed,
    [switch]$RightsConfirmed
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$lockDirectory = Join-Path $PSScriptRoot 'data/publish'
New-Item -ItemType Directory -Force -Path $lockDirectory > $null
try { $transferLock = [System.IO.File]::Open((Join-Path $lockDirectory '.transfer.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
catch { throw '已有上传或发布正在执行，请等待完成。' }
# Keep the handle open for the script lifetime, also protecting across web restarts.
if ($ReleaseId -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$' -or
    $Server -notmatch '^[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+$' -or
    $RemoteProject -notmatch '^/[A-Za-z0-9_./-]+$' -or $RemoteProject.Contains('..') -or
    $RemotePython -notmatch '^[A-Za-z0-9_./-]+$' -or $RemotePython.Contains('..') -or
    $Domain -notmatch '^https://[A-Za-z0-9.-]+(:[0-9]+)?/?$' -or $Port -lt 1 -or $Port -gt 65535) {
    throw "服务器、目录或版本参数无效。请使用服务器绝对项目路径，不要填写 ~。"
}
if ($Apply -and (-not $InventoryConfirmed -or -not $RightsConfirmed)) {
    throw "上线前必须确认服务器资料已盘点、视频有展示许可。当前默认只预检。"
}
$python = Join-Path $PSScriptRoot '.runtime/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "请先准备项目的 .runtime Python 环境。" }
$bundle = Join-Path $PSScriptRoot "data/publish/$ReleaseId"
if (-not (Test-Path -LiteralPath (Join-Path $bundle 'manifest.json'))) { throw "找不到发布包，请先在本机发布页面生成。" }
# Verify locally before any connection; never invoke an arbitrary package path.
& $python -c 'import sys; from services.publishing import verify_bundle; verify_bundle(sys.argv[1])' $bundle
if ($LASTEXITCODE -ne 0) { throw "本机发布包校验失败。" }
$sshArgs = @('-p', "$Port", '-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes')
$scpArgs = @('-P', "$Port", '-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes')
if ($IdentityFile) { $sshArgs += @('-i', $IdentityFile); $scpArgs += @('-i', $IdentityFile) }
$remoteIncoming = "$RemoteProject/data/site/incoming/$ReleaseId"
& ssh @sshArgs $Server "mkdir -p '$remoteIncoming'"
if ($LASTEXITCODE -ne 0) { throw "无法连接服务器。请先自行核对并保存主机指纹，配置 SSH 密钥登录；不要关闭主机校验。" }
if ($RcloneRemote) {
    $rclone = Join-Path $PSScriptRoot 'data/tools/rclone/rclone.exe'
    if (-not (Test-Path -LiteralPath $rclone)) { $rclone = (Get-Command rclone -ErrorAction SilentlyContinue).Source }
    if ($RcloneRemote -notmatch '^[A-Za-z0-9_-]+$' -or -not $rclone) { throw "请先运行 scripts/install_publish.py 安装 rclone，并配置已核对主机指纹的 SFTP 连接。" }
    # A named SFTP remote must independently configure known_hosts_file.
    $knownHosts = Join-Path $env:USERPROFILE '.ssh/known_hosts'
    if (-not (Test-Path -LiteralPath $knownHosts)) { throw '请先核对 SSH 指纹并保存到 known_hosts。' }
    $remoteParts = $Server.Split('@')
    $rcloneArgs = @('copy', $bundle, "${RcloneRemote}:$remoteIncoming", '--progress', '--transfers', '2', '--retries', '3',
        '--sftp-host', $remoteParts[1], '--sftp-user', $remoteParts[0], '--sftp-port', "$Port", '--sftp-known-hosts-file', $knownHosts)
    if ($IdentityFile) { $rcloneArgs += @('--sftp-key-file', $IdentityFile) }
    & $rclone @rcloneArgs
} else {
    & scp @scpArgs -r $bundle "${Server}:$RemoteProject/data/site/incoming/"
}
if ($LASTEXITCODE -ne 0) { throw "上传未完成，线上版本未切换。可重跑同一版本上传。" }
$arguments = "--root '$RemoteProject' --bundle '$remoteIncoming' --domain '$Domain'"
if ($Apply) { $arguments += ' --apply --inventory-confirmed --rights-confirmed' }
& ssh @sshArgs $Server "cd '$RemoteProject' && '$RemotePython' scripts/activate_site_release.py $arguments"
if ($LASTEXITCODE -ne 0) { throw "远端预检或激活失败，请查看错误；本机不标记为已发布。" }
if ($Apply) {
    Copy-Item -LiteralPath (Join-Path $bundle 'manifest.json') -Destination (Join-Path $PSScriptRoot 'data/publish/last-published.json')
    Write-Output "发布已通过服务器检查。还需用电脑和手机检查实际画面、声音及选集。"
} else {
    Write-Output "仅完成上传与预检，线上版本未切换。"
}
