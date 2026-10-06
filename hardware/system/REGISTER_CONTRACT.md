# MFCC PS/PL transport ABI v1

Implementation target: ZYBO Z7-20, Vivado 2024.2, xc7z020clg400-1.
New system sources do not modify the fixed arithmetic or existing PS-only platform.
Base address **0x43C00000**, aperture 64 KiB, little-endian AXI4-Lite, 32-bit data,
16-bit byte address. All accesses must be 4-byte aligned. Writes require WSTRB=0xf.
No IRQ/DMA in v1. PS M_AXI_GP0, interconnect and accelerator share FCLK_CLK0 at
100 MHz. A proc_sys_reset boundary synchronizes PS reset release; new user RTL
uses synchronous active-low reset. No fabric-derived/gated clock is used.

## Registers

| Offset | Name | Access/value |
|---|---|---|
|00|ID|RO 0x4d464343|
|04|ABI_VERSION|RO 0x00010000|
|08|CONTROL|WO:0=no-op,1=START,2=ABORT,4=CLEAR. Other combinations SLVERR|
|0c|STATUS|RO bits0 BUSY,1 DONE,2 ERROR,3 PCM_CAN_WRITE,4 OUTPUT_VALID; others0|
|10|SAMPLE_COUNT|RW idle with no pending output;0..262144|
|14|PCM_INPUT|WO low16 raw signed PCM16; high16 ignored|
|18|OUT_LO|RO output payload bits31:0, stable until POP|
|1c|OUT_HI|RO payload bits63:32, stable until POP|
|20|OUT_FRAME|RO zero-based clip-local frame ID|
|24|OUT_META|RO index[3:0],signed BFP[15:8],last[16],error[17],others0|
|28|OUT_POP|WO exactly1; remove one pending output record|
|2c|ERROR_FLAGS|RO sticky:0 bad bus access,1 command/config,2 input misuse,3 core error,4 output sequence,5 completion/count|
|30|INPUT_WRITTEN|RO accepted PCM MMIO writes|
|34|INPUT_CONSUMED|RO PCM transfers accepted by core|
|38|OUTPUT_CAPTURED|RO core results captured in transport slot|
|3c|OUTPUT_POPPED|RO output records removed by ARM|
|40|CORE_ID|RO 1=fixed;2 reserved for future FP32 adapter|
|44|FORMAT|RO 0x00011828 fixed: W[7:0]=40,F[15:8]=24,signed[16]=1,float[17]=0|
|48|FRAME_LENGTH|RO 512|
|4c|FRAME_HOP|RO 160|
|50|COEFFICIENTS|RO 13|
|54|MAX_SAMPLES|RO 262144|
|58|CYCLES_LO|RO low32 of busy-cycle counter|
|5c|CYCLES_HI|RO high32; read high-low-high while busy for coherent64-bit value|
|60|CONTRACT_TAG|RO 0x283fff8a, leading32 SHA bits of pinned fixed v2 r2 contract|

Illegal direction, undefined/unaligned address or partial write returns SLVERR;
illegal reads return zero. Reads of output words without OUTPUT_VALID return
zero/SLVERR. Misuse sets the appropriate sticky error even when idle. Unsupported
data/control writes do not change configuration, slots or counters.
SAMPLE_COUNT may always be read; the idle restriction applies to writes. A read
concurrent with a write observes the pre-write registered state. START/CLEAR/
ABORT clear prior errors, but a new illegal read in that same cycle remains
visible. AXI AW and W are independent; each may arrive first. B/R payloads hold
until their independent ready handshakes, including while the core is busy.

START requires idle and no output pending. It clears DONE/errors/counters and
starts exactly SAMPLE_COUNT samples, including an empty clip. Configuration is
retained across ABORT. CLEAR requires idle and clears DONE/errors only. ABORT is
always accepted: abort/reset core, flush both transport slots, clear BUSY/DONE/
errors/counters. It does not reset AXI response/pending-address channels. Board
reset also clears configuration and AXI channels. Software must single-own this
peripheral; concurrent threads/ISRs must not access transport registers.

One PCM holding register isolates AXI acceptance from core ready. PCM_CAN_WRITE
requires BUSY, no start/abort pulse, an empty input slot, and INPUT_WRITTEN less
than SAMPLE_COUNT. PCM_INPUT otherwise returns SLVERR/input misuse. The final
accepted MMIO sample is tagged last by the hardware count. The core accepts each
sample exactly once; PCM and last hold under core backpressure.

One output record slot stores raw64/frame32/meta32. The fixed adapter sign-extends
signed40/F24 to64 without any arithmetic or scaling. No read pops the slot.
OUT_POP requires value1 and OUTPUT_VALID; otherwise SLVERR/command error.
The core stalls while the output slot is occupied. Captured records are ordered
frame0 coefficient0..12, frame1 coefficient0..12, etc. last means coefficient12;
BFP is diagnostic frame metadata, not an extra scale on the final MFCC/F24 value.
The transport checks frame/index/last, same BFP within a frame, core error and
completion counts. ERROR records remain available for diagnosis.

DONE means core clip-done observed, all configured PCM consumed and all expected
outputs captured; BUSY clears. The final output may still occupy the slot.
Software must drain it and check OUTPUT_POPPED==13*frames before success.
frames=0 for T<512; otherwise1+(T-512)/160. Incomplete tails are discarded by the
unchanged core. Completion/count mismatch sets error bit5, still ends BUSY and
sets DONE so software does not silently wait forever. No automatic timeout in
hardware: ARM uses a finite timeout and ABORT for recovery.

ARM MUST interleave output draining and PCM feeding. Sending an entire long clip
before reading output can deadlock through the intentional backpressure chain.
The recommended loop drains one result first, then feeds at most one sample,
checks errors/DONE and deadline, and repeats. Polling MMIO uses uncached Device
memory with ARM barriers; stored PCM/output arrays remain ordinary DDR data.
No DMA cache ownership is introduced. Busy cycles include PS-induced stalls and
must not be presented as compute-only latency or measured board performance.

Single-word skid slots are registers, not frame arrays; existing fixed frame
storage remains BRAM. This bounded transport needs no new large FIFO. Future
bulk/DMA acceleration must receive its own buffer/cache/protocol validation.

## FP32 reuse boundary

The MMIO transport module is arithmetic-independent: PCM16 in, raw64/frame/meta
out, clip-start/last/clip-done, synchronous reset, ready/valid. A future FP32
adapter must set CORE_ID=2, FORMAT=0x00030020 (W32,F0,signed andfloat), zero-extend
the IEEE754 bits into payload[31:0] with high32=0, set BFP0, and preserve the same
frame/index/last order. It must map its actual error/clip/reset handshakes after
separate verification. No FP32 adapter is implemented or claimed verified here.

The actual existing `hardware/fp32/rtl/fp32_mfcc.sv` differs from the fixed core:

| Boundary | Fixed `mfcc_fixed_top` | Existing FP32 `fp32_mfcc` |
|---|---|---|
| Start | one-cycle `i_clip_start`, resets pending clip | `clip_start_valid/clip_start_ready` handshake |
| PCM | `i_pcm_valid/o_pcm_ready`, final sample `i_pcm_last` | `s_valid/s_ready/s_pcm`, no per-sample last |
| End/empty | final sample last; empty start with last and no valid | separate `clip_end_valid/clip_end_ready`, even for empty input |
| Completion | `o_clip_done` pulse | `clip_done_valid/clip_done_ready` handshake |
| Result | signed40/F24 plus frame/index/last/BFP/error | IEEE754 binary32 plus frame/coeff_index/start_sample/last |
| Error | per-result `o_error` | persistent12-bit `o_error`; needs capture before reset |

A future adapter therefore needs a start/end/done handshake FSM, must hold the
start request until accepted, generate end after the last PCM acceptance, and
acknowledge clip-done. Its error detail should be retained in a versioned register
extension; simply wiring fixed pulses onto FP32 valid signals is insufficient.
FP32 start_sample must equal frame*160 and can be checked without changing ABI
v1 result storage. This source inspection is not FP32 system integration proof.

Numeric contract remains immutable:
`D:/2610_MFCC/build/fixed_contract/v2_pcm16_mfcc40_20261004_r2`.
Transport ABI acceptance is separate from numeric accuracy, OOC timing, full
PS/PL implementation, and physical board execution.
