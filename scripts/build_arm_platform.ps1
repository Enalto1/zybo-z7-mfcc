[CmdletBinding()]
param(
    [string]$RunId = 'platform_01',
    [string]$Vivado = 'C:\Xilinx\Vivado\2024.2\bin\vivado.bat',
    [string]$BuildRoot = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $projectRoot
if (-not $BuildRoot) { $BuildRoot = Join-Path $workspaceRoot 'build\arm_platform' }
if ($RunId -notmatch '^[A-Za-z0-9_-]+$') { throw 'RunId must be a simple new directory name.' }
$sourceSpecPath = Join-Path $projectRoot 'hardware\platform\board_source.json'
$sourceSpec = Get-Content -LiteralPath $sourceSpecPath -Raw | ConvertFrom-Json
$sourceCache = Join-Path $BuildRoot ('officialsource\digilent-vivado-boards-' + $sourceSpec.commit)
$runDirectory = Join-Path $BuildRoot $RunId
if (Test-Path -LiteralPath $runDirectory) { throw "Refusing to overwrite an existing run: $runDirectory" }
if (-not (Test-Path -LiteralPath $Vivado -PathType Leaf)) { throw "Vivado not found: $Vivado" }
$records = @()
foreach ($entry in $sourceSpec.files) {
    $destination = Join-Path $sourceCache $entry.local
    $url = 'https://raw.githubusercontent.com/Digilent/vivado-boards/' + $sourceSpec.commit + '/' + $entry.upstream
    if (-not (Test-Path -LiteralPath $destination)) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
        Invoke-WebRequest -Uri $url -OutFile $destination
    }
    $actualHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $entry.sha256) { throw "Pinned official source hash mismatch: $destination" }
    $records += [ordered]@{ url=$url; path=$destination; sha256=$actualHash; bytes=(Get-Item -LiteralPath $destination).Length }
}
New-Item -ItemType Directory -Force -Path (Join-Path $runDirectory 'provenance') | Out-Null
$tcl = Join-Path $projectRoot 'hardware\platform\create_ps7_platform.tcl'
Copy-Item -LiteralPath $sourceSpecPath,$tcl,$PSCommandPath -Destination (Join-Path $runDirectory 'provenance')
Copy-Item -LiteralPath (Join-Path $sourceCache 'License.txt') -Destination (Join-Path $runDirectory 'provenance\Digilent_License.txt')
$manifest = [ordered]@{
    started_at_utc=[DateTime]::UtcNow.ToString('o'); status='running'; repository=$sourceSpec.repository
    commit=$sourceSpec.commit; board_part=$sourceSpec.board_part; target_part=$sourceSpec.target_part
    physical_board_revision='unverified'; physical_board_accessed=$false; bitstream_included=$false
    official_sources=$records; vivado=$Vivado; source_hashes=[ordered]@{}
}
foreach ($path in @($sourceSpecPath,$tcl,$PSCommandPath)) {
    $manifest.source_hashes[$path] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}
$manifestPath = Join-Path $runDirectory 'platform_manifest.json'
$manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $manifestPath -Encoding utf8
$exitCode = 1
try {
    Push-Location $runDirectory
    try {
        & $Vivado -mode batch -nojournal -log (Join-Path $runDirectory 'vivado.log') -source $tcl -tclargs $runDirectory (Join-Path $sourceCache 'board_files') 2>&1 | Tee-Object -FilePath (Join-Path $runDirectory 'console.log')
        $exitCode = $LASTEXITCODE
    } finally { Pop-Location }
    if ($exitCode -ne 0) { throw "Vivado failed with exit $exitCode; inspect $runDirectory" }
    $xsa = Join-Path $runDirectory 'design\zybo_z7_20_ps.xsa'
    if (-not (Test-Path -LiteralPath $xsa -PathType Leaf)) { throw 'Vivado returned without the required XSA.' }
    $manifest.status = 'xsa_exported'
    $manifest.xsa = $xsa
    $manifest.xsa_sha256 = (Get-FileHash -LiteralPath $xsa -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Output "ARM_PLATFORM_XSA=$xsa"
} catch {
    $manifest.status = 'failed'
    $manifest.error = $_.Exception.Message
    throw
} finally {
    $manifest.vivado_exit_code = $exitCode
    $manifest.completed_at_utc = [DateTime]::UtcNow.ToString('o')
    $manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $manifestPath -Encoding utf8
}
