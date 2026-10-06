[CmdletBinding()]
param([string]$RunId = 'ip_01')
$ErrorActionPreference = 'Stop'
if ($RunId -notmatch '^ip_[A-Za-z0-9_-]+$') { throw 'RunId must start with ip_ and contain only simple characters.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $projectRoot
$runDirectory = Join-Path $workspaceRoot ('build\fp32_hw\' + $RunId)
if (Test-Path -LiteralPath $runDirectory) { throw "Existing run is preserved: $runDirectory" }
$vivado = 'C:\Xilinx\Vivado\2024.2\bin\vivado.bat'
$tcl = Join-Path $projectRoot 'hardware\fp32\ip\create_fp32_ips.tcl'
New-Item -ItemType Directory -Path (Join-Path $runDirectory 'provenance') -Force | Out-Null
Copy-Item -LiteralPath $tcl,$PSCommandPath -Destination (Join-Path $runDirectory 'provenance')
$manifest = [ordered]@{ started_at_utc=[DateTime]::UtcNow.ToString('o'); status='running'; vivado=$vivado;
    target_part='xc7z020clg400-1'; fresh_ip=$true; previous_dcp_used=$false; physical_board_accessed=$false;
    aclken_enabled=$true;
    source_hashes=[ordered]@{}; output_hashes=[ordered]@{} }
foreach ($path in @($tcl,$PSCommandPath)) {
    $manifest.source_hashes[$path]=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}
$exitCode=1
try {
    Push-Location $runDirectory
    try {
        & $vivado -mode batch -nojournal -log (Join-Path $runDirectory 'vivado.log') -source (Join-Path $runDirectory 'provenance\create_fp32_ips.tcl') -tclargs $runDirectory 2>&1 | Tee-Object -FilePath (Join-Path $runDirectory 'console.log')
        $exitCode=$LASTEXITCODE
    } finally { Pop-Location }
    if ($exitCode -ne 0) { throw "Vivado failed with exit $exitCode; see $runDirectory" }
    foreach ($path in Get-ChildItem -LiteralPath $runDirectory -Recurse -File | Where-Object { $_.Extension -in '.xci','.v','.vhd','.xdc','.tsv' -or $_.Name -eq 'ip_status.txt' }) {
        $relative=[IO.Path]::GetRelativePath($runDirectory,$path.FullName)
        $manifest.output_hashes[$relative]=(Get-FileHash -LiteralPath $path.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $manifest.status='generated_not_synthesized'
    $manifest.project=Join-Path $runDirectory 'project\fp32_ips.xpr'
} catch {
    $manifest.status='failed'; $manifest.error=$_.Exception.Message; throw
} finally {
    $manifest.vivado_exit_code=$exitCode
    $manifest.completed_at_utc=[DateTime]::UtcNow.ToString('o')
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $runDirectory 'ip_manifest.json') -Encoding utf8
}
Write-Output "FP32_IP_PROJECT=$($manifest.project)"
