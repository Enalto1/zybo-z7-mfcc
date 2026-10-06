# Shared MFCC DMA/IRQ transport ABI 2.0

Target: ZYBO Z7-20 / xc7z020clg400-1 / Vivado/Vitis 2024.2.
The latest user instruction replaces the proposed FP32 AXI-Lite polling data
path with **DMA + interrupts for both fixed and FP32**. Earlier polling runs
and source snapshots remain historical evidence. This document specifies the
new implementation; verification/build/board completion are separate results.

## System and ownership

Both variants use one identical transport with a separately selected frozen
arithmetic core. One bitstream per variant is sufficient. CPU0 owns it;
CPU1 is halted during board validation.

- FCLK0 target100MHz drives custom control/streams, DMA and interconnects,
  M_AXI_GP0_ACLK and S_AXI_HP0_ACLK. Reset release uses proc_sys_reset.
- GP0 controls custom S_AXI at **0x43C00000/64KiB** and AXI DMA at
  **0x40400000/64KiB**.
- AXI DMA Simple mode: MM2S+S2MM, no SG, both memory masters64bit through
  an interconnect to PS HP0; stream widths32bit; burst16; length field23bits;
  DRE enabled on both channels. Both variants use the same settings.
- DMA MM2S IRQ, S2MM IRQ and custom terminal IRQ occupy concat bits0,1,2
  respectively, then IRQ_F2P[2:0]. Expected GIC IDs61,62,63 must be checked
  against generated HWH/BSP. IRQs are active-high levels with source ACK.
- DDR/HP is noncoherent. DMA does not grant CPU cache ownership automatically.
  No TUSER field carries required numerical or ordering metadata.

## PCM stream and DDR layout

Input is the unchanged mono16kHz signed PCM16 little-endian file. A32bit beat
packs sample2k in bits15:0 and sample2k+1 in bits31:16. Each half is interpreted
as signed16 without a numerical conversion. Earlier samples occupy lower DDR
addresses. Full beats have TKEEP=0xf. An odd final sample uses TKEEP=0x3;
the unused upper half is ignored. Every other TKEEP pattern is invalid.
TLAST must occur on exactly the final input beat computed from SAMPLE_COUNT.
An even final beat uses TKEEP=0xf; no earlier TLAST is permitted.

The unpacker holds an accepted beat until both valid samples are consumed.
It must not drop the upper sample when the final beat has two samples.
It does not accept excess input after the configured length. Accepted input
bytes and consumed PCM samples are counted independently. The whole clip is
one MM2S transfer of **2*SAMPLE_COUNT bytes**, not one transfer per frame.

SAMPLE_COUNT ranges0..262144. Frames are0 if N<512, otherwise
`1 + floor((N-512)/160)`. Partial tails are consumed by the unchanged core
and do not cause padding or an extra frame. Zero-length DMA channels are not
started. Empty and short clips terminate through the custom core IRQ;
nonzero short clips additionally require MM2S completion.

## Result stream and DDR records

Each result is a24byte little-endian record, serialized as **six32bit words**:

| Word | Byte offset | Meaning |
|---|---:|---|
|0|0|payload low32|
|1|4|payload high32|
|2|8|frame ID, unsigned32|
|3|12|coefficient index, unsigned32,0..12|
|4|16|BFP diagnostic exponent, signed32|
|5|20|flags: bit0 native C12/last; bit1 native error; other bits0|

For fixed, payload is the unchanged signed40 raw Q24 value, sign-extended to
signed64. The BFP signed8 metadata is sign-extended to32. The final value is
already raw/2^24; BFP must not be applied a second time. No float conversion
is permitted in the transport. Core ID1, FORMAT0x00011828,
CONTRACT_TAG0x283fff8a bind the existing fixed v2 contract.

For FP32, payload low32 is the unchanged IEEE754 binary32 bit pattern;
high32 and BFP are0. Core ID2, FORMAT0x00030020,
CONTRACT_TAG0xc556a8e8 bind the frozen FP32 backend identity. Full source,
IP, coefficient, reference and tolerance SHA-256 values are recorded separately.

All six output words have TKEEP=0xf. **DMA TLAST is asserted only on word5
of coefficient12 of the final frame of the clip.** The native frame-last flag
is retained in each record's flags and does not terminate intermediate DMA
packets. No required metadata is placed solely in TUSER. Each full output
clip is one S2MM packet of `frames*13*24` bytes.

A one-record holding register preserves the complete value/metadata while
serializing. TDATA/TKEEP/TLAST/TVALID remain stable under backpressure.
Native output is accepted only when storage is available. No ping-pong or
whole-clip BRAM copy is necessary for this stallable source.

Development input N85920 yields534frames/6942records, **171840 input bytes**
and **166608 output bytes** for both variants. Max input524288bytes and max
output510432bytes (21268records) both fit the23bit DMA length field.

## AXI4-Lite control and status

Aligned full32bit accesses only; writes require WSTRB0xf. Independent AW/W
channels and held R/B responses obey AXI4-Lite. Undefined/removed data-window
registers and invalid accesses return SLVERR and record the bus error.

| Offset | Name | Meaning |
|---|---|---|
|0x00|ID|RO0x4d464343|
|0x04|ABI_VERSION|RO0x00020000|
|0x08|CONTROL|WO START1, ABORT2, CLEAR4; one command per write|
|0x0c|STATUS|RO bit0busy, bit1done, bit2error; other bits0|
|0x10|SAMPLE_COUNT|RW while idle;0..262144|
|0x2c|ERROR_FLAGS|RO sticky errors described below|
|0x30|INPUT_RECEIVED|RO valid PCM samples accepted from input AXIS|
|0x34|INPUT_CONSUMED|RO samples accepted by arithmetic core|
|0x38|OUTPUT_CAPTURED|RO native records accepted into serializer|
|0x3c|OUTPUT_SENT|RO complete six-word records accepted by output AXIS|
|0x40|CORE_ID|RO1fixed/2FP32|
|0x44|FORMAT|RO format above|
|0x48|FRAME_LENGTH|RO512|
|0x4c|HOP|RO160|
|0x50|NCOEF|RO13|
|0x54|MAX_SAMPLES|RO262144|
|0x58/0x5c|BUSY_CYCLES_LO/HI|RO64bit counter|
|0x60|CONTRACT_TAG|RO core identity above|
|0x64|CORE_ERROR_DETAIL|RO first nonzero native error: FP32 exact12bits; fixed boolean1|
|0x68|INPUT_BYTES|RO accepted TKEEP-valid input bytes|
|0x6c|OUTPUT_BYTES|RO bytes accepted by output stream|
|0x70|IRQ_STATUS|RW1C bit0terminalDONE, bit1ERROR|
|0x74|IRQ_ENABLE|RW mask bits0..1; reset0|

Old PCM push/output read/POP addresses0x14..0x28 are absent. START requires
idle and a valid sample count; it clears previous run diagnostics/counters
and terminal IRQ status and retains IRQ_ENABLE. An internal preparation state
computes the expected record count using bounded subtract160/add13 steps for
N>=512 before issuing native START. BUSY is asserted throughout this
preparation; PCM and native-result acceptance wait until preparation completes.
Empty/short clips use a single preparation transition with zero records.
ABORT also cancels
preparation. Software must not assume zero-latency native START.
For N>=512, preparation takes one cycle per output frame, including its final
transition:534cycles for the development clip and at most1636cycles. At100MHz
these are5.34us and16.36us. This is setup latency within the measured transaction.
ABORT cancels pending input/output and resets the core; CLEAR clears idle
diagnostics/terminal IRQ. Neither command changes the configured sample count.
BUSY remains asserted until successful terminal completion or ABORT.
Error state stops new input and native-record acceptance and raises ERROR IRQ.
It also suppresses the internal PCM-valid presented to the failed native core;
an internally buffered sample is not drained after failure. No result from
that failed transaction is accepted as successful before ABORT recovery.
An already captured output record keeps its asserted valid/payload stable and
may finish serialization; it is cancelled only by explicit ABORT/reset.
Software captures diagnostics before bounded DMA reset and core ABORT recovery.

ERROR_FLAGS: bit0invalid AXI access, bit1command/configuration misuse,
bit2input TKEEP/TLAST/length error, bit3native core error,
bit4output frame/index/last/metadata error, bit5completion count mismatch.
No error code is fabricated by ORing native numerical codes. FP32 native
detail preserves the first nonzero12bit vector exactly; fixed native error
is boolean and detail1 does not identify an arithmetic submodule.

Terminal DONE is asserted only after native done has been observed, the
configured input has been consumed, all expected records have been captured
and serialized, and output holding storage is empty. It is not MM2S IOC.
Registered native done masks further PCM/native-record acceptance and enters
a finish phase while BUSY remains high. An already captured record continues
to serialize; completion checks use committed counters. Early native done or
missing input/records raises ERROR, and late native/MMIO errors take priority
over success. Finish/check clocks are included in BUSY_CYCLES.
The custom IRQ level is `|(IRQ_STATUS & IRQ_ENABLE)`. W1C acknowledgement
clears pending terminal IRQ bits, without clearing diagnostic status/counters.
New event setting takes priority over a simultaneous acknowledgement.

BUSY_CYCLES counts from accepted START through terminal completion and includes
record-count preparation, core reset/recovery, all handshakes, DMA backpressure
and serialization/finish checks. Both variants use the same preparation and
completion logic.
It is not an isolated arithmetic-latency or CPU-occupancy measurement.
Read high/low/high when the counter is live; after DONE it is stable.

## Software transaction, interrupts and timing

Use dedicated DDR buffers aligned to at least64bytes, with allocated size
rounded up to cache-line boundaries and no adjacent shared cache lines.
Before DMA, flush input and prepare output ownership by flushing/invalidation
of its dedicated range. CPU does not access owned ranges while DMA is active.
After receive completion, invalidate the output range before CPU inspection.
Capture the final S2MM_LENGTH actual receivedbytes and compare exact expectation.

Per clip: clear stale DMA/core IRQ and software flags, configure count/IRQ,
arm nonzero S2MM first, START core, then start nonzero MM2S. ISR work is limited
to source status/ACK, diagnostic capture and volatile completion/error flags.
Use an IRQ-safe WFI wait, not a busy loop reading completion status. A one-shot
SCU private timer (GIC PPI29) provides a finite wake deadline; global timer
provides elapsed ticks. Handle the completion-before-WFI race by masking IRQ,
rechecking conditions, issuing DSB/WFI, then restoring IRQ. The Cortex-A9 WFI
wake rule must be validated against its TRM and actual interrupt execution.

Success requires all applicable MM2S/S2MM IOC flags, custom terminal DONE,
zero DMA/core error, exact input/output byte counts, exact sample/record
counters and post-DMA result verification. Input completion alone is never
accepted as MFCC completion. Timeout/error recovery disables/ACKs sources,
resets DMA with a bounded wait and ABORTs the core before buffers are reused.
No unbounded reset wait is permitted.

Both variants perform the same3warmup+30measured clips within one running
ELF with equivalent buffer and cache policy. Start end-to-end timing when
input is already in DDR, before clip configuration and cache maintenance;
stop after DMA/core completion, final counter/status checks and required output
invalidation. Include normal output writes to DDR. Exclude JTAG, UART, ELF/
FPGA startup and host numerical comparison. Full numerical comparison remains
outside the timed interval. Record actual IRQ counts, wake counts, DMA lengths,
elapsed ticks and PL cycles; no unmeasured CPU-occupancy claim is permitted.

## Required evidence and limitations

Preserve the frozen cores and numerical contracts. Verify stream stalls,
odd/even/short lengths, end-of-clip TLAST, malformed inputs, reset/abort and
repeat clips. Actual AXI DMA + PS bus/DDR model tests are distinct from native
unit tests and do not prove ARM GIC ISR execution. Synthesis, routed timing,
bit/XSA generation and actual board ISR/numerical/timing checks are separate.

Fixed raw/metadata must match the existing integer model and fixed ARM C.
FP32 raw bits must match the frozen FP32 RTL reference; Python/PC tolerance is
a separate test. Preserve fixed float64 NOT_ACCEPTED and the known FP32
synthetic failures. Only the named development audio is within board scope.
