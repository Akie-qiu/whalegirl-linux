$ErrorActionPreference = 'Stop'
& ssh -o BatchMode=yes -o ConnectTimeout=10 root@120.46.186.50 /opt/whalegirl-linux/venv/bin/python /opt/whalegirl-linux-assets/spoken-learning-20261105-1793876138684272350/rollback_spoken_learning.py --report /opt/whalegirl-linux-assets/bilibili-spoken-learning-deployment.json
if ($LASTEXITCODE -ne 0) { throw 'Rollback did not complete; inspect the server response.' }
