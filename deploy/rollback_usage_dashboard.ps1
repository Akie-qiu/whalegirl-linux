param([string]$DeploymentDirectory)
$ErrorActionPreference='Stop'
if (-not $DeploymentDirectory) {
    $manifestFile=Join-Path $PSScriptRoot '../artifacts/usage-dashboard-deploy-20261111/manifest.json'
    $DeploymentDirectory=(Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json).backup
}
if ($DeploymentDirectory -notmatch '^/opt/whalegirl-linux-assets/usage-dashboard-20261111-\d{6}$') {
    throw 'Unexpected deployment directory'
}
ssh -o BatchMode=yes -o ConnectTimeout=10 root@120.46.186.50 "python3 $DeploymentDirectory/deploy_usage_dashboard.py rollback $DeploymentDirectory"
if($LASTEXITCODE){throw 'Rollback refused or failed; inspect the deployment report before retrying'}
