[CmdletBinding()]
param(
    [string]$RunId = ('probe_' + (Get-Date -Format 'yyyyMMdd_HHmmss')),
    [switch]$StartServer
)
$ErrorActionPreference = 'Stop'
if ($RunId -notmatch '^[A-Za-z0-9_-]+$') { throw 'Use a simple new run name.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $projectRoot
$out = Join-Path $workspaceRoot ('build\arm_platform\' + $RunId)
if (Test-Path -LiteralPath $out) { throw "Refusing existing probe: $out" }
New-Item -ItemType Directory -Path $out | Out-Null
$record = [ordered]@{captured_utc=[DateTime]::UtcNow.ToString('o');serial=@();usb_jtag_uart_matches=@();query_errors=@();board_actions='none'}
try { $record.serial = @(Get-CimInstance Win32_SerialPort | Select-Object DeviceID,Name,PNPDeviceID) }
catch { $record.query_errors += $_.Exception.Message }
try { $record.usb_jtag_uart_matches = @(Get-PnpDevice -PresentOnly | Where-Object { $_.FriendlyName -match 'Digilent|JTAG|FTDI|USB Serial|UART|Xilinx' } | Select-Object Status,Class,FriendlyName,InstanceId) }
catch { $record.query_errors += $_.Exception.Message }
if ($StartServer -and -not (Get-Process hw_server -ErrorAction SilentlyContinue)) {
    $process = Start-Process -FilePath 'C:\Xilinx\Vitis\2024.2\bin\hw_server.bat' -ArgumentList '-s','tcp:127.0.0.1:3121' -WindowStyle Hidden -RedirectStandardOutput (Join-Path $out 'hw_server_stdout.log') -RedirectStandardError (Join-Path $out 'hw_server_stderr.log') -PassThru
    $record.started_server_launcher_pid = $process.Id
    Start-Sleep -Seconds 2
}
$script = Join-Path $projectRoot 'verification\arm\probe_targets.tcl'
$record.probe_script_sha256 = (Get-FileHash -LiteralPath $script -Algorithm SHA256).Hash.ToLowerInvariant()
& 'C:\Xilinx\Vitis\2024.2\bin\xsdb.bat' $script *> (Join-Path $out 'jtag_probe.log')
$record.xsdb_exit_code = $LASTEXITCODE
$record | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 (Join-Path $out 'windows_devices.json')
Get-Content -Raw (Join-Path $out 'jtag_probe.log')
Write-Output $out
if ($record.query_errors.Count -gt 0) { Write-Warning 'Windows query failed; do not infer board absence from unavailable queries.' }
