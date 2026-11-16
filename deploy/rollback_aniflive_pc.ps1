$ErrorActionPreference = 'Stop'
& 'D:/anconda/envs/GPTSoVits/python.exe' -X utf8 -I (Join-Path $PSScriptRoot 'activate_aniflive_pc.py') rollback
if ($LASTEXITCODE -ne 0) { throw 'Local rollback stopped; cloud settings were left untouched' }
ssh -o BatchMode=yes -o ConnectTimeout=15 root@120.46.186.50 '/opt/whalegirl-linux/venv/bin/python /opt/whalegirl-linux-assets/aniflive-20261112/stage/aniflive_cloud_acceptance.py rollback-config-label'
if ($LASTEXITCODE -ne 0) { throw 'Original engine restored; cloud model label needs review' }
