"""Explicit-port UART capture using Windows .NET SerialPort, outside timed code."""
from __future__ import annotations
import re
import subprocess
import time
from pathlib import Path


class Capture:
    def __init__(self, port: str, output: Path):
        if not re.fullmatch(r'COM[1-9][0-9]*',port.upper()):
            raise ValueError('Use an explicitly observed Windows COM port')
        self.path=output/'uart.log'
        self.stopfile=output/'uart.stop'
        self.process=None
        self.port=port
        self.script=output/'capture_uart.ps1'
        self.script.write_text('''param([string]$Port,[string]$Output,[string]$StopFile)
$ErrorActionPreference='Stop'
$serial=New-Object System.IO.Ports.SerialPort $Port,115200,None,8,one
$serial.ReadTimeout=100
$serial.DtrEnable=$false
$serial.RtsEnable=$false
$writer=New-Object System.IO.StreamWriter $Output,$false,([System.Text.Encoding]::ASCII)
$writer.AutoFlush=$true
try {
  $serial.Open()
  $writer.WriteLine('UART_CAPTURE_OPEN '+$Port)
  while (-not (Test-Path -LiteralPath $StopFile)) {
    $text=$serial.ReadExisting()
    if ($text.Length -gt 0) { $writer.Write($text) }
    Start-Sleep -Milliseconds 50
  }
} finally { if ($serial.IsOpen) { $serial.Close() }; $writer.Dispose() }
''',encoding='utf-8')

    def start(self):
        self.process=subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(self.script),
            '-Port',self.port,'-Output',str(self.path),'-StopFile',str(self.stopfile)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if self.process.poll() is not None:
                raise RuntimeError('UART capture could not open selected port: '+self.process.communicate()[1].decode(errors='replace'))
            if self.path.exists() and 'UART_CAPTURE_OPEN' in self.path.read_text(errors='replace'):
                return
            time.sleep(.05)
        raise RuntimeError('UART capture did not become ready')

    def marker(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    def require(self, token: str, offset: int):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if self.path.exists() and token.encode('ascii') in self.path.read_bytes()[offset:]:
                return
            time.sleep(.05)
        raise ValueError('Required startup UART marker not observed: '+token)

    def close(self):
        if self.process is not None:
            self.stopfile.write_text('stop',encoding='ascii')
            try:
                self.process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.communicate(timeout=3)
