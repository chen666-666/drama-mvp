param(
    [Parameter(Mandatory=$true)][string]$Server,
    [int]$Port = 22
)
$ErrorActionPreference = 'Stop'
if ($Server -notmatch '^[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+$' -or $Port -lt 1 -or $Port -gt 65535) {
    throw '服务器地址或端口无效。'
}
$project = Split-Path -Parent $PSScriptRoot
$privateDirectory = Join-Path $project 'data/private'
New-Item -ItemType Directory -Force -Path $privateDirectory > $null
$key = Join-Path $privateDirectory 'site-deploy-key'
$python = Join-Path $project '.runtime/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '找不到项目 Python 环境。' }
& $python (Join-Path $PSScriptRoot 'create_site_deploy_key.py') --key $key
if ($LASTEXITCODE -ne 0) { throw '未能准备本机专用部署密钥。' }
$account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls $key /inheritance:r /grant:r "${account}:(R,W)" > $null
if ($LASTEXITCODE -ne 0) { throw '未能限制部署密钥文件权限。' }
$publicParts = (Get-Content -LiteralPath ($key + '.pub') -Raw).Trim().Split(' ')
if ($publicParts[0] -ne 'ssh-ed25519' -or $publicParts[1] -notmatch '^[A-Za-z0-9+/=]+$') { throw '公钥格式无效。' }
$publicKey = $publicParts[0] + ' ' + $publicParts[1] + ' drama-site-deploy'
$sshArgs = @('-p', "$Port", '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15', '-o', 'IPQoS=none',
    '-o', 'KexAlgorithms=ecdh-sha2-nistp256', '-o', 'Ciphers=aes128-ctr', '-o', 'PreferredAuthentications=password',
    '-o', 'PubkeyAuthentication=no', '-o', 'NumberOfPasswordPrompts=1')
Write-Host '接下来请在你自己的终端输入服务器密码，输入时不会显示字符。不要把密码发到聊天中。'
Write-Host '本操作仅为当前电脑添加一把专用部署公钥，不会发布影片或修改网站。'
$remote = "set -eu; cd; umask 077; mkdir -p .ssh; touch .ssh/authorized_keys; grep -qxF '$publicKey' .ssh/authorized_keys || printf '%s\n' '$publicKey' >> .ssh/authorized_keys; chmod 700 .ssh; chmod 600 .ssh/authorized_keys"
& ssh @sshArgs $Server $remote
if ($LASTEXITCODE -ne 0) { throw '服务器登录或公钥设置未完成，网站未改动。' }
$verifyArgs = @('-p', "$Port", '-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
    '-o', 'IPQoS=none', '-o', 'KexAlgorithms=ecdh-sha2-nistp256', '-o', 'Ciphers=aes128-ctr', '-o', 'IdentitiesOnly=yes', '-i', $key)
& ssh @verifyArgs $Server 'printf DEPLOY_CONNECTION_READY'
if ($LASTEXITCODE -ne 0) { throw '公钥登录尚未通过，请保留终端错误信息。' }
Write-Host ''
Write-Host '部署连接已就绪。请回到聊天告诉我“连接完成”，我会继续备份和部署功能。'
