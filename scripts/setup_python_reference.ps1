param(
    [string]$Python = 'C:\Users\rlagk\AppData\Local\Programs\Python\Python311\python.exe'
)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$outputBase = Join-Path (Split-Path -Parent $projectDir) 'build\python_reference'
$envDir = Join-Path $outputBase 'venv'
$envLogDir = Join-Path $outputBase '_environment'
New-Item -ItemType Directory -Path $envLogDir -Force | Out-Null
& $Python -c "import sys; assert sys.version_info[:2] == (3,11), 'Python 3.11 required'"
if ($LASTEXITCODE -ne 0) { throw 'Python version check failed' }
if (!(Test-Path -LiteralPath (Join-Path $envDir 'Scripts\python.exe'))) {
    & $Python -m venv $envDir
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}
$envPython = Join-Path $envDir 'Scripts\python.exe'
& $envPython -m pip install --disable-pip-version-check -r (Join-Path $projectDir 'software\python\requirements.txt') --report (Join-Path $envLogDir 'install_report.json') 2>&1 | Tee-Object -FilePath (Join-Path $envLogDir 'install.log')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& $envPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed' }
Write-Output "MFCC environment ready: $envPython"
