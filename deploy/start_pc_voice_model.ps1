param(
  [string]$TtsRoot = 'D:/yachiyo/voice',
  [string]$Python = 'D:/anconda/envs/GPTSoVits/python.exe',
  [string]$RuntimePath = 'D:/appData/qqbot/voise/runtime'
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$env:OMP_NUM_THREADS = '2'
$env:OPENBLAS_NUM_THREADS = '1'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:CUDA_VISIBLE_DEVICES = '0'
$env:no_proxy = '127.0.0.1,localhost'
$env:GPT_SOVITS_API_BATCH_SIZE = '1'
$env:GPT_SOVITS_PARALLEL_AUDIO_SYNTHESIS = '0'
$env:PATH = (Join-Path $RuntimePath 'tools') + ';' + (Split-Path $Python) + ';' + (Join-Path (Split-Path $Python) 'Library/bin') + ';' + $env:PATH
Set-Location -LiteralPath $TtsRoot
& $Python -I (Join-Path $PSScriptRoot 'pc_voice_engine.py') --runtime $RuntimePath
exit $LASTEXITCODE
