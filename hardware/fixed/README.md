# Fixed integer MFCC RTL

The current candidate is PCM16/F15 -> preemphasis32/F30 -> window32/F30 ->
integer BFP -> FFT input16/F15 promoted20/F19 -> Power40 -> Mel60 -> log30/F24
-> raw MFCC13 signed40/F24. Coefficients are fixed integer tables. This is a
portable candidate, **not an algorithm accuracy acceptance**.

Normative immutable C/model packages:

- `D:/2610_MFCC/build/fixed_contract/v1_fft20_power40_mel60_20261004_r2`
- `D:/2610_MFCC/build/fixed_contract/v2_pcm16_mfcc40_20261004_r2`

Read `contract.json`, `verification.json`, `artifact_hashes.json`, and
`PUBLISHED.json`. The v1 bundle contains complex overflow and recovery inputs;
v2 r2 contains all616 PCM frames and integer stages. It corrects one nested
Mel coefficient path in the preserved first v2 without changing numerical files.
Published versions never change.

## Verified status on 2026-10-04

The complete PCM-to-MFCC RTL has passed integer transaction comparison and
100 MHz out-of-context placement/routing. Numerical accuracy remains
`NOT_ACCEPTED`, and board execution remains `NOT_RUN`. The immutable v2 r2
contract is unchanged; later RTL evidence is recorded in the runs below rather
than rewriting publication-time status files.

- Simulation: `D:/2610_MFCC/build/fixed_full_rtl/full_616_final_20261004_06`.
  All 24 cases/616 frames passed with zero mismatches: 315,392 frontend samples,
  315,392 complex FFT bins (all 512/frame), 158,312 Power bins, 16,016 Mel values,
  16,016 log values, and 8,008 MFCC values. The test exercised 15,457 output-stall
  cycles, input gaps, reset after 173 partial PCM samples, reset with FFT data
  in flight, and completion/draining for every clip; total 14,410,168 cycles.
- Reproducible implementation:
  `D:/2610_MFCC/build/fixed_full_rtl/full_616_repro_20261004_07`.
  This run authenticated the identical RTL, testbench and every vector against
  simulation 06, then reran synthesis, placement and routing. Its persistent
  `project/fixed_full.xpr` resolves coefficient files inside the source snapshot.
  Vivado 2024.2, xc7z020clg400-1, 100 MHz and 2 ns IO constraints gave routed
  WNS +0.506 ns and WHS +0.097 ns. This is OOC timing, not board timing.
- Routed resources: 5,455 LUT, 2,391 FF, 40 DSP, 9 RAMB36 and 7 RAMB18
  (12.5 BRAM tiles). All 894 LUTRAM and 65 SRL instances are retained FFT
  internals. New frontend/backend/tail storage has zero LUTRAM; the new frame
  buffers map to BRAM.
- Independent audit:
  `D:/2610_MFCC/build/fixed_full_rtl/full_616_repro_20261004_07_audit.json`
  is `PASS` with 3,065 checks, including 26 persistent XPR file references.

The preserved accuracy evidence has development RMSE 0.006260244715, synthetic
maximum absolute error 6.591594298125, and 60 floor regressions. Bit equivalence
and routed timing do not establish MFCC accuracy acceptance. See
`docs/reviews/FIXED_POINT_RTL_IMPLEMENTATION.md` for the implementation evidence.

## Top and transaction contract

`mfcc_fixed_top.sv` accepts signed16 PCM with `i_pcm_valid/o_pcm_ready`.
Pulse `i_clip_start` to reset previous PCM, local frame numbering, and all
pending downstream transactions. Hold the input sample and `i_pcm_last` until
accepted. `i_pcm_last` marks the final accepted sample. An empty clip is encoded
by `i_clip_start && i_pcm_last && !i_pcm_valid`. Frame length512, hop160, and
discarding incomplete tails match the common reference. `o_clip_done` pulses
only after the final output transaction is accepted (or after an empty/short
clip has been consumed). Synchronous active-low reset aborts all pending work.

The output is thirteen signed40/F24 values with local frame ID, index0..12,
signed8 BFP exponent, last=index12, and per-frame error. All fields and valid
hold while `i_ready=0`. Diagnostic taps expose frontend, full512-bin FFT and
Mel transfers to the verification bench. They are verification interfaces, not
AXI/board interfaces. No board pin or PS integration has been performed.

## Storage and flow control

The original FFT has **no output ready and no global stall**. The spectral
wrapper reserves its tail at the first accepted FFT input and admits only one
spectral frame until the final Mel output is accepted. The frontend obeys real
input backpressure; an upstream source must retain PCM until ready. A pipeline
can have one frame in spectral processing, one buffered by log, and one in DCT.

- Frontend:512x32 continuous preemphasis ring and512x32 window RAM. Each uses
  registered synchronous read latency1; at most one read is in flight. No
  array reset or whole-array next copy. The only memory updates are individual
  write/read-port operations; the ring uses XPM and the window uses inference.
  The exact preemphasis /20 is factored into a narrow /5 calculation and
  registered steps; the window product, rounded value and magnitude are
  registered before the peak comparison. BFP quantization uses an exact
  signed40 quotient and18-bit remainder mask, followed by registered rounding.
- FFT reorder: four logical512x40 banks encoded in one2048x40 simple dual-port
  RAM; registered read latency1. The E-FFT provenance file identifies copied
  originals and work-copy hashes. Internal delay memories belong to E-FFT.
- Spectral tail:512x40 Power RAM (257 live entries),8192x17 Mel coefficient ROM
  (6682 live entries), XPM_MEMORY synchronous read latency1. At most one
  Power/coefficient pair is in flight during Mel accumulation. The ingress has
  two registered stages (complex input and squares) with aligned valid/bin/
  last/overflow, then exact Power sum/write. The capture FSM follows delayed
  metadata, including malformed early-last cases. One exact product/add per three cycles
  gives26*257 terms without losing unstalled FFT output.
- DCT:32x30 synchronous-read BRAM (26 live log values), one read in flight,
  used by the338-product sequential operation. Coefficient/product/final
  rounding phases are registered; log also registers its wide ln2 product.
  Log squares a normalized unsigned32 mantissa using registered16-bit hi/lo
  products, a registered exact64-bit sum, then the original normalization.
  No bits are discarded by these extra pipeline stages. Window/DCT/floor
  constants are generated explicit combinational case tables with defaults.

Memory-port inference and XPM primitives avoid the forbidden full-array reset
and new user initial/for/function/task constructs. The memory write/read-port
statements are the documented memory-specific exception to plain `_reg<=_next`
assignments; they do not add an initial block or reset stored data. Actual BRAM
mapping must be read from the synthesis evidence, not inferred from attributes.

## Reproduction

Use the repository Python virtual environment and Vivado2024.2:

```
python scripts/run_fixed_fft20.py --run-id NEW_FFT --implement
python scripts/run_fixed_spectral.py --help
python scripts/run_fixed_power_mel.py --run-id NEW_TAIL --implementation route
python scripts/verify_fixed_integer_rtl.py --help
python scripts/run_fixed_full_rtl.py --run-id NEW_FULL --implementation route
python verification/fixed/audit_full_rtl.py --run D:/2610_MFCC/build/fixed_full_rtl/full_616_repro_20261004_07 --out D:/2610_MFCC/build/fixed_full_rtl/NEW_AUDIT.json
```

The runners refuse to overwrite run folders and retain source snapshots,
integer vectors, Tcl projects, reports, and hashes under `D:/2610_MFCC/build`.
Full-pipeline implementation targets xc7z020clg400-1,100MHz, with explicit2ns
input/output delay budgets. Timing success is separate from simulation and
numerical accuracy. See the implementation report for actual completed runs.
