param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$server = 'root@120.46.186.50'
$consoleUrl = 'http://127.0.0.1:18092/'
$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
$ready = $false
try {
    $health = Invoke-RestMethod -Uri ($consoleUrl + 'health') -TimeoutSec 2
    $ready = $health.status -eq 'ok' -and [bool]$health.wechat_version
} catch { }
if (-not $ready) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort 18092 -ErrorAction SilentlyContinue
    if ($listener) { throw '本机 18092 端口被其他程序占用，请先检查该程序。' }
    $logDir = Join-Path $PSScriptRoot 'artifacts'
    [IO.Directory]::CreateDirectory($logDir) | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $sshArgs = @('-N','-L','127.0.0.1:18092:127.0.0.1:8092','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',$server)
    $tunnel = Start-Process -FilePath $ssh -ArgumentList $sshArgs -WindowStyle Hidden -PassThru -RedirectStandardError (Join-Path $logDir "console-tunnel-$stamp.log") -RedirectStandardOutput (Join-Path $logDir "console-tunnel-$stamp.out.log")
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod -Uri ($consoleUrl + 'health') -TimeoutSec 2
            if ($health.status -eq 'ok' -and $health.wechat_version) { $ready = $true; break }
        } catch { }
        if ($tunnel.HasExited) { throw 'SSH 连接未建立，请检查网络和免密登录。诊断记录保存在 artifacts 目录。' }
    }
    if (-not $ready) { throw '连接尚未就绪，请检查服务器后重试。' }
}
if (-not $NoBrowser) {
    # Keep the existing access key in memory; never print it or write it into the shortcut.
    $accessKey = ((& $ssh -o BatchMode=yes -o ConnectTimeout=10 $server 'cat /var/lib/whalegirl-linux/core/admin.token') -join '').Trim()
    if ($LASTEXITCODE -ne 0 -or $accessKey.Length -lt 20) { throw '未能读取控制台授权，请检查 SSH 登录。' }
    Start-Process ($consoleUrl + '#access=' + [Uri]::EscapeDataString($accessKey))
    $accessKey = $null
}
Write-Host '微信机器人控制台已连接：http://127.0.0.1:18092/'
