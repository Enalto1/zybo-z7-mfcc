# FP32 MFCC PS/PL transport ABI 1.1

Target: ZYBO Z7-20 / xc7z020clg400-1 / Vivado 2024.2.
This is a separate FP32 system. Existing fixed RTL, transport, bitstreams,
numeric contracts and historical results remain unchanged.

## Identity and register map

Base is **0x43C00000**, aperture 64 KiB, AXI4-Lite 32-bit little-endian.
Accesses are aligned full words; writes require WSTRB=0xf. No DMA or IRQ.
The register directions, status/error bits, independent AXI channel behavior,
single PCM/output holding slots and counters follow the existing
[transport ABI](../system/REGISTER_CONTRACT.md), with these FP32 overrides:

| Offset | Name | FP32 value/meaning |
|---|---|---|
| 0x00 | ID | 0x4d464343 |
| 0x04 | ABI_VERSION | **0x00010001** |
| 0x40 | CORE_ID | **2** |
| 0x44 | FORMAT | **0x00030020**: IEEE754 binary32, W32/F0, signed/float bits set |
| 0x60 | CONTRACT_TAG | **0xc556a8e8**, leading SHA-256 bits of the pinned FP32 backend source |
| 0x64 | CORE_ERROR_DETAIL | RO: first nonzero native FP32 error[11:0], upper20 bits zero |

The complete backend SHA-256 is
`c556a8e85f30b4914ef23c523017a719be2ffc752499be7e25a4ae549201ab6b`.
The tag is an identity check, not a hash of the entire numeric specification.
Each run separately binds all six FP32 RTL files, five vendor XCI customizations,
ROM files, specification, input, reference arrays and tolerances by SHA-256.

The unchanged register set includes CONTROL at0x08, STATUS0x0c,
SAMPLE_COUNT0x10, PCM_INPUT0x14, OUT_LO/HI/FRAME/META0x18..0x24,
OUT_POP0x28, ERROR_FLAGS0x2c, input/output counters0x30..0x3c,
frame length512/hop160/coefficients13/max samples262144 at0x48..0x54,
and the 64-bit busy-cycle counter0x58/0x5c. Undefined addresses return SLVERR.

## Input and result records

PCM is the frozen mono16kHz signed16 stream. Only the low16 write bits carry
PCM. Hardware marks the last accepted sample using SAMPLE_COUNT.

Every result carries the **unaltered IEEE754 binary32 bits in payload[31:0]**,
with payload[63:32]=0. BFP is always0. No numeric cast, fixed-point scaling,
rounding, or second BFP factor is applied. Frame/index/last ordering remains
frame0 coefficient0..12, frame1 coefficient0..12, etc. Last means index12.
The adapter additionally verifies native `start_sample == frame_id*160` with
a 64-bit shift/add expression; failure sets the transport sequence error bit4
and aggregate core error bit3. Native error detail retains its own12-bit meaning.

ARM DDR records remain24 bytes: uint64 raw IEEE bits, uint32 frame,
uint32 index, int32 BFP0, uint32 flags(last/error). The board runner rejects
nonzero high32, nonfinite binary32, bad order/count/last/BFP/error, and any
unexpected native error detail before numerical acceptance.

## Native FP32 handshake adapter

`fp32_core_adapter` bridges transport start/last pulses to the frozen
`fp32_mfcc` valid/ready interfaces. It holds START until accepted, transfers
PCM only on valid&&ready, and asserts END only after the final PCM acceptance.
END remains high while pre-emphasis drains and until the native core accepts it.
An empty clip uses start+empty then END without a PCM transaction. Incomplete
tails use the unchanged core's no-padding policy.

START and ABORT each impose at least16 synchronous reset clocks on the native
pipeline. A START received during a prior reset is retained and restarts this
reset interval. ABORT cancels a simultaneous START, pending completion and
pending data. Native reset is a registered signal. After each release, the
adapter holds it high for at least4 clocks before a subsequent transport reset.
START/ABORT arriving during this release guard are retained, with ABORT winning
a simultaneous request; stream handshakes stay blocked during recovery.
External system reset clears pending commands. The core can receive PCM only
after reset recovery and native START have completed. No clock gating is introduced.
The four-clock guard is this integration's conservative recovery contract,
verified against the installed vendor model; it is not a claimed vendor minimum.

Outputs pass directly through native valid/ready during active/end/drain states;
the transport holding register keeps AXI readback stable until OUT_POP.
Native DONE is acknowledged in drain state and becomes a registered transport
done pulse. The transport independently checks all configured PCM and expected
output counts before successful completion. The final output may still need POP
when DONE appears; ARM must drain it before accepting the run.

## Errors and recovery

CORE_ERROR_DETAIL captures the first nonzero native12-bit error vector exactly
while the transport is active, including errors without an output transaction.
Later errors do not overwrite or OR into that first vector. Native layout is the
frozen core's `o_error`: backend error CODE[7:0], framer flag8, pre-emphasis flag9,
window flag10, top flag11. In particular, OR-combining backend codes could invent
a code that the core never emitted, so accumulation is prohibited.
Aggregate error bit3 and sequence bit4 also diagnose adapter metadata faults.
The adapter does not replace native bits with its own synthetic error code.

START/CLEAR/ABORT clear the previous transport error/detail latch under the
existing command rules. New read misuse in the same cycle retains the existing
last-priority bus-error behavior. Native errors during reset are masked, so the
previous clip's native sticky flags cannot recontaminate a fresh START.
Software saves status/counters/detail before an error-recovery ABORT.

ARM uses bounded drain-first polling with finite deadline and poll count.
MMIO uses Cortex-A9 Device/XN section attributes0xC16 with barriers. DDR remains
ordinary cached data; there is no DMA cache ownership transfer. The terminal
status is accepted only after complete input/output counters and error checks.

## System and verification boundary

PS7 M_AXI_GP0, AXI interconnect, transport/FP32 core and reset controller share
FCLK0 at the100MHz target. The pinned official board preset preserves DDR/MIO/
UART/CPU configuration. `proc_sys_reset` synchronizes the active-low fabric
reset release. DDR/FIXED_IO are the only external interfaces.

Unit tests exercise adapter/reset/empty/tail/stall/restart/error handling without
substituting a mock for arithmetic correctness. Actual vendor-IP AXI smoke
checks raw output against the preserved continuous-development RTL result.
Physical board full-clip comparison then checks all534 frames/6942 coefficients
against those raw bits and the frozen Python/PC tolerances. Simulation,
implementation, board execution and numerical acceptance are separate results.
Existing synthetic numerical failures are retained.

Busy cycles span transport START to native completion and include reset,
handshake and PS-induced stalls. ARM timing additionally includes configuration,
MMIO feed/drain, DDR result stores, POPs and counter/cycle reads. It excludes
JTAG/UART/ELF startup/FPGA programming and initial PCM CRC. These counters do
not establish pure core compute latency or CPU utilization reduction.
