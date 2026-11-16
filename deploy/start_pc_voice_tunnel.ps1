param([string]$RuntimePath='D:/appData/qqbot/voise/runtime')
$ErrorActionPreference='Stop'
$ssh=(Get-Command ssh).Source
$key=Join-Path $RuntimePath 'tunnel_ed25519'
if (!(Test-Path -LiteralPath $key)) { throw 'Voice tunnel identity is not provisioned' }
$tunnelLog=Join-Path $RuntimePath 'tunnel-events.log'
while ($true) {
  "$(Get-Date -Format o) Connecting dedicated loopback voice tunnel" | Add-Content -LiteralPath $tunnelLog
  & $ssh -n -N -T -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=12 -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -i $key -R 127.0.0.1:19881:127.0.0.1:9882 whalegirl-voice@120.46.186.50
  "$(Get-Date -Format o) Tunnel disconnected (exit $LASTEXITCODE); reconnecting in 15 seconds" | Add-Content -LiteralPath $tunnelLog
  Start-Sleep -Seconds 15
}
