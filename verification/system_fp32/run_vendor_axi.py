"""Run only the isolated real-IP FP32 AXI smoke through the common freeze flow."""
from pathlib import Path
import subprocess
import sys

if __name__ == "__main__":
    script = Path(__file__).resolve().parents[2] / "scripts/run_fp32_system.py"
    raise SystemExit(subprocess.call([sys.executable, "-B", str(script), "--mode", "sim", *sys.argv[1:]]))
