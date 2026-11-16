param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^/opt/whalegirl-linux-assets/[A-Za-z0-9_./-]+\.json$')]
    [string]$Report,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^/opt/whalegirl-linux-assets/[A-Za-z0-9_./-]+\.py$')]
    [string]$RemoteScript,
    [ValidatePattern('^[A-Za-z0-9_.@-]+$')]
    [string]$Server = 'root@120.46.186.50',
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
if ($Report.Contains('/../') -or $RemoteScript.Contains('/../')) {
    throw 'Parent directory traversal is not allowed.'
}
$RemoteArguments = @(
    '/opt/whalegirl-linux/venv/bin/python', $RemoteScript, '--report', $Report
)
if ($Execute) { $RemoteArguments += '--execute' }
& ssh -o BatchMode=yes -o ConnectTimeout=10 $Server @RemoteArguments
if ($LASTEXITCODE -ne 0) {
    throw 'Rollback did not complete. Inspect the reported reason and candidate recovery status.'
}
