"""Read-only diagnostic replay of a frozen simulator's compiled design.

Copies simulator products into a fresh directory, re-elaborates with driver
visibility, and inspects100ns of generated clocks. No force, board, or source
modification; no arithmetic/transport acceptance is claimed.
"""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_dma_system as common

source = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=False)
sim = source / 'design/vivado_project/zybo_dma.sim/sim_1/behav/xsim'
common.process_guard()
shutil.copytree(sim / 'xsim.dir', out / 'xsim.dir')
for name in ('xsim.ini', 'dct_cosine.mem', 'dct_scale.mem', 'mel.mem', 'mel_compact.mem',
             'mel_descriptor.mem', 'mel_fw16.mem', 'twiddle_1024_w16.mem', 'window.mem'):
    shutil.copy2(sim / name, out / name)
for name in ('pcm.mem', 'records.mem'):
    shutil.copy2(source / name, out / name)
line = next(line for line in (sim / 'elaborate.bat').read_text().splitlines() if line.startswith('call xelab '))
args = line[len('call xelab '):].split()
args[args.index('--debug') + 1] = 'all'
args[args.index('--snapshot') + 1] = 'clock_probe'
args[args.index('-log') + 1] = 'clock_elaborate.log'
command = ['C:/Xilinx/Vivado/2024.2/bin/xelab.bat', *args]
with (out / 'clock_elaborate_console.log').open('w') as log:
    result = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT)
if result.returncode:
    raise RuntimeError('Clock diagnostic elaboration failed')
script = '''run 100 ns
foreach path {
 /tb_dma_system/DUT/zybo_dma_i/processing_system7_0/inst/gen_clk/clk0
 /tb_dma_system/DUT/zybo_dma_i/processing_system7_0/inst/gen_clk/fclk_clk0
 /tb_dma_system/DUT/zybo_dma_i/processing_system7_0/inst/FCLK_CLK0
 /tb_dma_system/DUT/zybo_dma_i/processing_system7_0_FCLK_CLK0
 /tb_dma_system/DUT/zybo_dma_i/axi_dma_0/s_axi_lite_aclk
 /tb_dma_system/DUT/zybo_dma_i/axi_dma_0/m_axi_mm2s_aclk
 /tb_dma_system/DUT/zybo_dma_i/axi_dma_0/m_axi_s2mm_aclk
} {
 puts "CLOCK_PATH=$path"
 puts "CLOCK_VALUE=[get_value $path]"
 if {[catch {report_drivers [get_objects $path]} result]} {puts "DRIVER_ERROR=$result"}
}
puts "CLOCK_DIAGNOSTIC_COMPLETE"
quit
'''
(out / 'clock.tcl').write_text(script)
common.process_guard()
(out / 'clock_options.txt').write_text('-tclbatch clock.tcl\n-testplusarg "RUN='+out.as_posix()+
    '"\n-testplusarg "CORE_KIND=1"\n-log clock_simulate.log\n')
# A command file preserves equals signs through the Windows batch launcher.
command = ['C:/Xilinx/Vivado/2024.2/bin/xsim.bat', 'clock_probe', '-f', 'clock_options.txt']
with (out / 'clock_console.log').open('w') as log:
    result = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT)
common.dump(out / 'diagnostic_manifest.json', dict(source_run=str(source), source_manifest_sha256=common.sha(source/'run_manifest.json'),
            status='DIAGNOSTIC_ONLY', exit_code=result.returncode, physical_board_accessed=False,
            arithmetic_acceptance_claimed=False, generated_clock_forced=False))
print('Clock diagnostic exit', result.returncode)
