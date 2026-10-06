# ZYBO Z7-20 fixed MFCC system

This directory adds a new PS/PL system around the previously verified fixed
arithmetic. The existing `hardware/platform` PS-only project, fixed numeric
contract, ARM/C fixed software and FP32 RTL are not implementation inputs that
may be edited by this workflow. Every run snapshots the fixed source and checks
its hashes against `fixed_full_rtl/full_616_repro_20261004_07`.

## Architecture

```text
ARM CPU0 / DDR stored PCM
        | uncached Device MMIO, 32-bit little-endian
PS7 M_AXI_GP0 --> AXI Interconnect --> fixed_accel_top / mfcc_mmio
                                       | PCM16 / ready / last
                                       v
                                 mfcc_fixed_top
                                       | MFCC signed40/F24 + frame/index/BFP
                                       v
                              stable raw64 result slot
```

GP0, interconnect, reset controller and accelerator share FCLK0 at100MHz.
The PS7 active-low fabric reset passes through `proc_sys_reset` before reaching
the accelerator's synchronous active-low reset. No PL pins, microphone, clock
crossing or DMA are introduced. DDR/FIXED_IO are the only external interfaces.
The official pinned Digilent preset preserves DDR/MIO/UART; GP0/FCLK0/reset0 are
enabled in the new block design. The existing PS-only XSA is not reused as the
new application's hardware description.

Vivado 2024.2 does not accept a SystemVerilog top as a direct BD module
reference. The Tcl therefore packages the unchanged arithmetic and new SV
transport as `mfcc.local:user:fixed_accel:1.0` in a separate temporary project.
Persistent imported sources live under the run's `design/ip_repo`; every RTL
and memory copy is compared with its frozen source. Both coefficient memories
must be present in synthesis and simulation file groups. The final system
project references that IP without duplicate standalone RTL definitions.

Polling is the initial transport because the core already supports input/output
backpressure and the task is stored PCM verification. Its simple ownership and
explicit output records permit bit comparison without adding DDR bus mastering
or DMA cache ownership. No board transfer bandwidth or speedup is claimed.
`software/arm_accel` contains the finite-timeout feed/drain driver and standalone
ARM harness; it is separate from `software/arm_fixed` and `software/c_fixed`.

One pending PCM and one result are holding registers. This is not a frame FIFO;
large frame storage remains in the verified core's BRAM. Software drains output
while feeding PCM, so finite storage never assumes that the FFT can be stalled.
The original FFT still has no ready input; the unchanged core's reservation and
BRAM logic absorbs its output.

The normative transport details, register map, error flags, ordering and actual
FP32 adapter differences are in [REGISTER_CONTRACT.md](REGISTER_CONTRACT.md).
The numeric manifest is fixed at
`D:/2610_MFCC/build/fixed_contract/v2_pcm16_mfcc40_20261004_r2/contract.json`.
Interface correctness does not accept the outstanding MFCC numeric errors.

## Reproduce

Use `D:/2610_MFCC/build/python_reference/venv/Scripts/python.exe` and a fresh run
ID (at most eight characters here for Windows IP checkpoint path limits).
First wait for the other FP32 session's Vivado/XSim jobs to finish. The runner
also checks processes before each Vivado invocation and refuses an overlap.

```powershell
python -B scripts/run_fixed_system.py --run-id smokeNEW --smoke --mode sim
python -B scripts/run_fixed_system.py --run-id fullNEW --mode all
```

`--mode prepare` snapshots sources/board preset and creates vectors without
launching Vivado. `--mode bd` validates packaging and the block design without
claiming simulation, synthesis or implementation success. `sim` checks the generic MMIO controller with a fake core and
then the actual fixed core behind AXI using development and synthetic vectors.
`all` additionally creates the PS block design, validates addresses/reset/clock,
synthesizes, routes, checks timing, writes the bitstream, and exports an XSA with
that exact bitstream. Tool commands, sources and hashes are retained in each
`D:/2610_MFCC/build/system_fixed/<RUN>` folder. Failed runs are also retained.

Principal outputs:

- `design/vivado_project/zybo_z7_20_fixed.xpr`
- `design/zybo_z7_20_fixed.bit` and `design/zybo_z7_20_fixed.xsa`
- `reports/`: PS properties, address map, hierarchy, clocks, reset, timing, DRC
- `simulation.json`, `unit_simulation.json`, logs, `run_manifest.json`, hashes

Full AXI-boundary simulation is not execution of ARM machine code. PS bus-model
simulation, full board-target implementation, software compilation, prior OOC
results, and physical board results are reported separately in
`docs/SYSTEM_FIXED_INTEGRATION.md`. These scripts contain no hardware-manager,
JTAG programming, board reset, download or execution commands.
