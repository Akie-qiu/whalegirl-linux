[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$folder=Join-Path $root 'artifacts/rollback-previous-20261103'
$plan=Get-Content -LiteralPath (Join-Path $folder 'local-retire-plan.json') -Raw | ConvertFrom-Json
if($root -cne $plan.root){throw 'Unexpected workspace'}
$targets=@()
foreach($name in $plan.delete_files){
    $target=[IO.Path]::GetFullPath((Join-Path $root $name))
    if(-not $target.StartsWith($root+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Path outside workspace'}
    $item=Get-Item -LiteralPath $target
    if($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Refuse directory or symlink deletion'}
    if((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -cne $plan.sha256.$name){throw "File changed: $name"}
    $targets+=$target
}
if(-not $Apply){[pscustomobject]@{Root=$root;DeleteFiles=$targets;RestoreFiles=$plan.restore_files.Count}|ConvertTo-Json -Depth 4;return}
foreach($target in $targets){Remove-Item -LiteralPath $target -Force}
foreach($name in $plan.restore_files){
    # Keep the independently verified native navigation regression checks.
    if($name -eq 'tests/test_native_delivery.py'){continue}
    $source=Join-Path (Join-Path $folder 'baseline') $name
    $target=Join-Path $root $name
    Copy-Item -LiteralPath $source -Destination $target -Force
}
[pscustomobject]@{RestoredPreviousSource=$true;DeletedFiles=$targets.Count;NativeNavigationFixPreserved=$true}|ConvertTo-Json
