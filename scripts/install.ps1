param([Parameter(Mandatory=$true)][string]$SkillsRoot)
$ErrorActionPreference='Stop'
$packageRoot=Split-Path $PSScriptRoot
$skillSources=Get-ChildItem -LiteralPath (Join-Path $packageRoot 'skills') -Directory
# Fail before copying anything if any target already exists. No global config changes.
foreach($source in $skillSources) {
  $target=Join-Path $SkillsRoot $source.Name
  if(Test-Path -LiteralPath $target){throw "Existing skill: $target. Compare/backup explicitly before installation."}
}
New-Item -ItemType Directory -Path $SkillsRoot -Force | Out-Null
foreach($source in $skillSources){Copy-Item -LiteralPath $source.FullName -Destination (Join-Path $SkillsRoot $source.Name) -Recurse}
Write-Output 'Installed four skills. Keep the full handover package for templates/runtime; enable Spreadsheets separately. Start a new agent task.'
