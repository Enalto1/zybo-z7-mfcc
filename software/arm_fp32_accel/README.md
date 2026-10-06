# FP32 integrated accelerator host

This is a new transport/application derived from the frozen successful a07 fixed
transport. It performs no MFCC arithmetic on ARM. The PL computes continuous
PCM16 pre-emphasis/framing through raw C0..C12. Existing fixed files remain intact.

The independent FP32 identity is ID `0x4d464343`, ABI `0x00010001`, CORE_ID 2,
FORMAT `0x00030020`, TAG `0xc556a8e8`, base `0x43c00000`. RO `0x64` preserves
the first nonzero native 12-bit fault vector until START/CLEAR/ABORT; its backend
low byte is an error code, so vectors are not OR-accumulated. All s06 transport
offsets/count/stall rules remain.
Each 24-byte record is `(uint64 IEEE_bits, uint32 frame, uint32 index,
int32 bfp_zero, uint32 last_error_flags)`; high32 is zero and NaN/Inf is rejected.
The 128-byte status includes core_error_detail at byte124, formerly padding.

`build_arm_fp32_accel.py` snapshots these files, generates the first512 samples
and first13 expected IEEE patterns from frozen `continuous_development_01`,
runs the host protocol fault tests, and optionally builds a new standalone BSP
from an explicitly selected/pinned FP32 system XSA. BSP generation never connects
to hardware. The board runner defaults to offline preparation and requires the
observed cable/CPU/FPGA plus an explicit board acknowledgement to execute.

Execution gates: smoke exact bits, then all85920 samples/534frames/6942 records
exactly match frozen RTLgold and pass Python float64 and PC C tolerances
`1e-3 + 1e-5*abs(reference)`, then 3 discarded fresh starts +30 measured starts.
Every timing output is checked again. Known five FP32 synthetic failures remain
recorded; no evaluation speech is loaded. Input readback+CRC, raw status/output,
MMU/cache/FPSCR/clock snapshots, actual ELF layout and hashes are preserved.

Each job resets the PS system, halts CPU1, uses the same-XSA PS init, programs
the selected bitstream after reset, applies post_config, then downloads the ELF.
MMIO uses A9 Device/XN descriptor `0x43c00c16` (unsupported PXN bit0 clear).
The elapsed timer includes control, drain/feed polling, DDR result stores and
counter reads. Initial PCM CRC, setup, JTAG/UART and numeric comparison are
outside. Busy cycles include PS-induced stalls; ARM is occupied polling.
