param(
  [string]$RuntimePath='D:/appData/qqbot/voise/runtime',
  [string]$BridgePython='D:/anconda/envs/GPTSoVits/pythonw.exe'
)
$ErrorActionPreference='Stop'
# Compatibility entry point: explicit invocation opens the owner control panel.
# It no longer creates a separate, uncoordinated supervisor loop.
$voicePanel=Join-Path $PSScriptRoot 'pc_voice_control.py'
if (!(Test-Path -LiteralPath $BridgePython)) { throw 'Voice Python environment is unavailable' }
Start-Process -FilePath $BridgePython -ArgumentList @('-I',('"'+$voicePanel+'"'),'--runtime',('"'+$RuntimePath+'"'),'--action','start')
