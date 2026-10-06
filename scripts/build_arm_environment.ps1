[CmdletBinding()]
param(
    [string]$RunId = ('environment_' + (Get-Date -Format 'yyyyMMdd_HHmmss')),
    [string]$Python = 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe'
)
$ErrorActionPreference = 'Stop'
if ($RunId -notmatch '^[A-Za-z0-9_-]+$') { throw 'Use a simple new run name.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $projectRoot
$buildRoot = Join-Path $workspaceRoot 'build\arm_platform'
& (Join-Path $PSScriptRoot 'build_arm_platform.ps1') -RunId ($RunId + '_platform')
$platformRun = Join-Path $buildRoot ($RunId + '_platform')
& $Python -B (Join-Path $PSScriptRoot 'build_arm_apps.py') --platform-run $platformRun --run-id ($RunId + '_apps')
if ($LASTEXITCODE -ne 0) { throw 'ARM app build failed; inspect its preserved logs.' }
$manifest = Join-Path $buildRoot ($RunId + '_apps\build_manifest.json')
& $Python -B (Join-Path $PSScriptRoot 'run_arm_reference.py') --prepare-only --build-manifest $manifest --output-root $buildRoot --run-id ($RunId + '_prepare')
if ($LASTEXITCODE -ne 0) { throw 'ARM offline preparation failed; inspect its preserved logs.' }
Write-Output 'XSA/BSP/ARM ELFs and offline execution plan generated. No board execution has been performed.'
