# Fixed hardware pipeline review

Date: 2026-10-05 KST. Scope: source and preserved-report inspection, followed by
isolated frontend candidate simulation as recorded below. No new synthesis,
timing, or board result is claimed here.
Later execution completed in `os02/as02/sparse_board01` and
`op02/ap02/pipeline_board01`: both closed timing at 100 MHz and passed actual
board bit comparison. The combined board median is 16.343058 ms, versus the
preserved baseline 124.823717 ms. These subsequent results and their distinct
measurement boundaries are documented in `FIXED_HW_OPTIMIZATION_RESULTS.md`;
the review-time observations below remain preserved.
The active optimization request supersedes the old role and board-hold wording
in `AGENTS.md`. Published coefficients, integer contracts, FFT arithmetic,
FP32, ARM C, and thesis/presentation files are outside this review's edits.

## Current scheduling and exact arithmetic

The current design already overlaps frontend preparation, spectral work, log,
and DCT where their ready/valid boundaries permit. The FFT is an R2^2 SDF
pipeline with a natural-order ping-pong reorder buffer; the power stage has
registered FFT operands and registered squares. It is incorrect to describe
the entire design as one sequential FSM.

However, `fixed_spectral_top` reserves one spectral frame at its first FFT
input and releases that ownership only when Mel band 25 is accepted downstream.
The FFT has no output ready, so this reservation is essential with the current
single power buffer. Reducing arithmetic alone does not remove that dependency.

| Boundary | Arithmetic retained by an optimization |
|---|---|
| Preemphasis | signed16 PCM; exact alpha 19/20; signed33 intermediate; signed32/F30 stored result |
| Window | signed32/F30 by unsigned31/F30; signed64 full product; one RNE right shift 30; signed32/F30 stored value |
| BFP | peak of the same rounded window values; greatest legal s in [-2,24] satisfying the existing threshold; silence s=0 |
| FFT input | signed40 exact quotient and rounding intermediate; the same RNE and clamp to [-32767,32767]; signed16/F15 plus BFP metadata |
| FFT/power | existing FFT20 numeric core retained; signed20 squares produce 40-bit nonnegative results; 41-bit power sum and existing flag |
| Mel | unsigned40 by unsigned17/F16; full unsigned57 product; unsigned60 accumulator with 61st-bit overflow check |
| Log | unsigned60 Mel; threshold in common energy units; truncating normalization to unsigned32/F31; 30 unchanged full-width square iterations; signed71 LN2 product; one RNE right shift 36 to signed30/F24 |
| DCT | signed30/F24 by signed31/F30; full signed61 product; signed64 accumulation in increasing Mel index; one RNE right shift 30 to signed40/F24 |

Removing additions of exactly zero changes neither the Mel accumulator nor its
overflow flag. A pipeline must preserve every retained nonzero term in ascending
bin order and must not insert any additional truncation or rounding.

## Source-derived scheduling costs

These are cycle counts derived from the inspected FSMs, with an always-ready
consumer and no input gaps. They are not measured whole-frame performance.
Clocks spent waiting for a different block must be counted separately.

| Block/operation | Current source-derived work |
|---|---:|
| PCM preemphasis accept/store | II 4 clocks per accepted PCM |
| Window scan | 5 clocks per sample; 2,560 clocks for 512 samples |
| BFP selection | 1 to 27 clocks, depending on peak and selected s |
| Window drain to FFT | II 4 clocks per sample; 2,048 clocks for 512 samples |
| Frontend next-frame schedule | 160*4 + 512*5 + BFP + 512*4 = 5,248 + BFP clocks, before spectral stalls |
| Dense Mel read/multiply/accumulate | 6,682*3 = 20,046 clocks |
| Sparse Mel with unchanged term FSM | 459*3 = 1,377 clocks; per-band/output overhead excluded |
| Log nonfloor admission | II = 95 + abs(floor(log2(T))-31) clocks |
| Log floor or invalid-exponent admission | II 2 clocks |
| DCT after collection | 13*(26*3 + round + accepted output) = 1,040 clocks |

For log, the 30 repeated squares consume 90 clocks: partial products, combine,
and mantissa/fraction update each take one clock. The next iteration depends on
the updated mantissa. Simply adding more registers to the same recurrence
cannot make a single log transaction accept a new square every clock.

`revision_03/FINAL_CORRECTIONS.md` is the controlling interpretation of the
earlier sparse proposal. In particular, its roughly 2,679-clock aggregate
estimate is not actual segment time or a validated prediction: the old formula
paired the same band's Mel and log costs although the real overlap is previous
log versus next Mel. This review does not reuse that value as a performance
result or extrapolate a whole-clip speedup.

## Recommended bounded changes after sparse Mel

### 1. Pipeline the Mel term traversal

Issue one paired power/compact-coefficient ROM read per clock. Register the
aligned full product on the next clock and accumulate the preceding registered
product on the following clock. Separate issue bin/term counters from the
retirement counter; delay valid and last-term metadata with each product.
At the last issue, stop issuing and drain both occupied stages. Enter output
only after the last product has updated the accumulator. Hold the accumulator,
band, frame, exponent, flags, and valid throughout output stalls.

Keep a band boundary bubble initially. With a one-cycle memory read and one
registered multiply, L terms require L+2 arithmetic clocks, rather than 3L.
The existing single 60-bit accumulator can accept one exact integer product
per clock; its feedback is one add, independent of the multiplier pipeline.
There is no arithmetic need for multiple accumulators at this stage. Mapping
and 100 MHz timing still require synthesis and routing.

### 2. Pipeline the frontend window scan

This is the largest low-risk independent reduction while retaining the current
ring buffer and frame admission policy. Issue pre-RAM and window-coefficient
reads every clock. Delay index and valid through product, RNE, magnitude, and
peak/write. The coefficient register must be paired with the same issued index
as the pre-RAM read. Store the rounded sample alongside its magnitude until the
peak/write stage, because the sample at the preceding stage belongs to the next
index when II=1.

The first read through last peak/write takes 516 clocks for 512 samples. This
is a structural schedule, not a measured result. No additional memory port is
required: pre-RAM is read-only during the scan, and window RAM is write-only.
On retirement of index 511, update the peak and enter BFP; the following BFP
cycle observes the complete registered peak. Reset and clip-start clear all
pipeline-valid bits and disable RAM writes; RAM contents remain unreset.

### 3. Pipeline frontend drain with one global advance

Issue window reads, quantize, round/clamp, and emit concurrently. Use a bounded
valid pipeline and a shared `advance = !output_valid || i_ready` condition.
When the output is stalled, hold all pipeline registers, issue/read counters,
metadata, and the BRAM read register by disabling the read enable. Read latency
must be included explicitly in the valid/index pipeline. A registered BRAM
result must not be overwritten while downstream stages are frozen.

Only increment the output index on valid/ready. Do not infer completion from
the last RAM issue: remain in drain until the last valid output is accepted.
Then preserve the existing frame increment, 160-sample refill, EOF, and
incomplete-tail rules. This targets drain II1 without changing frame storage
or allowing PCM writes during the scan. It is a separate change from the
window pipeline and should receive its own small protocol check.

### 4. Pipeline DCT read/product/MAC

Keep all 26 log words in the current BRAM and process one output coefficient
at a time. Issue one synchronous log-memory read and registered coefficient
per cycle, register the aligned product, and add the preceding product each
cycle. Retire exactly 26 products before rounding. Retain the current signed64
sum and ascending input order. Empty the term pipeline before an output stall
or coefficient change.

The bounded arithmetic schedule becomes 13*(26+2+1+1) = 390 clocks under an
always-ready consumer. No extra multipliers or reordered sums are required.
The preserved i10 post-route critical setup path is DCT log-memory BRAM to
product DSP, with 7.904 ns data path and +0.447 ns setup slack. Thus operand and
product registers must remain separate; combining read and multiply control
into a longer combinational path would be counterproductive.

### 5. Address log or spectral frame ownership only after counters show need

A small parallel-log wrapper is safer than altering the log approximation.
Instantiate two or three unchanged log engines explicitly (no generate-for).
Round-robin dispatch advances only on the selected engine's input handshake;
round-robin retirement advances only on the selected engine's output handshake.
Only that retire engine sees downstream ready. Early floor results in later
engines wait in their existing output registers, maintaining band/frame order.
Both pointers reset together. Every engine retains exact threshold, normalizing
shifts, iterations, rounding, flags, and metadata. This changes throughput and
resources, not numeric results. A theoretical aggregate rate cannot substitute
for measurement because input spacing, floor paths, DCT stalls, and ordering
affect utilization.

Reducing normalization one bit at a time to fixed coarse shifts can preserve
exact integers, but it saves only the normalization component; the 90-cycle
recurrence remains. Interleaving three mantissa contexts through one square
pipeline can use all square stages, but requires context storage, scheduling,
normalization/product arbitration, and ordered retirement. It is a broader
control change than explicit parallel unchanged engines.

A one-frame Mel queue can release the power buffer as soon as the tail has
produced all bands even while log is busy. However, the queue must store error
metadata with each frame. The present top-level `frame_error_reg` is overwritten
on the next frontend admission; forwarding this live register to queued Mel
outputs would attach the new frame's error to an old frame. Put all frame
metadata in the queue at insertion, and size/review the top-level inflight
counter for the maximum admitted frames.

For simultaneous FFT capture and Mel consumption, two explicit power banks
and their ownership are required. Reserve a free bank before first FFT input,
track every admitted frame through the unstallable FFT, and release a bank
only after its final Mel read/product has retired. Buffered output may keep
metadata alive beyond bank release. Capturing the next FFT without an owned
complete destination is forbidden even if average throughput appears safe.
This is a subsequent optimization, not necessary to obtain Mel term II1 or
frontend/DCT pipeline improvements.

## Required evidence before accepting a candidate

The existing full pipeline TB compares frontend, FFT, power, Mel, log, and
MFCC transactions against all frozen integer vectors; it also checks final
output stalls, reset during partial PCM and FFT in flight, ordering, EOF, and
counts. The power/Mel TB additionally checks malformed FFT bursts and recovery.
The unit scripts include log floor boundaries at each legal s, invalid s,
metadata errors, and backend recovery. Preserve these tests and their original
failure distinctions.

Add cycle observations for first PCM, first/last frontend admission, FFT first/
last output, Mel first/last accepted band, first/last log acceptance, MFCC first/
last acceptance, and clip done. Count valid-without-ready separately at each
boundary. For new pipelines check issue and retire counts as well as output
value equality; exercise long stalls at first, middle, and final beats, reset
with each valid stage occupied, clip-start abort, silence and nonfloor mixtures,
and one-frame drain.

Small affected-unit/smoke checks precede full corpus, post-route 100 MHz timing,
DMA system checks, and temporary JTAG board execution. Whole-clip wall time,
PL BUSY, average whole-clip/534, first result latency, and steady-state frame
admission intervals are distinct quantities. Existing numerical accuracy remains
`NOT_ACCEPTED`; exact hardware/model matching does not change that status.

## Prepared frontend candidate

The first frontend candidate is isolated at
`build/fixed_optimization/frontend_candidate_20261005_01/mfcc_fixed_frontend.sv`.
The live RTL is intentionally untouched until the sparse-only snapshot is
acquired. It implements the window and drain pipelines described above, with
four window token stages and three drain token stages. The window write uses
a dedicated delayed signed32 sample plus its delayed index, so the magnitude
and stored sample always describe the same original word. The drain's single
advance controls its BRAM read enable, signed40 quotient/round bit, index, and
signed16 output. The ring buffer and four-cycle preemphasis path are unchanged.

`scripts/run_fixed_frontend_pipeline.py` and
`verification/fixed/frontend/tb_mfcc_fixed_frontend_pipeline.sv` snapshot the
candidate, generated coefficient module, TB, runner, and frozen input vectors.
They compare window writes as well as final frontend transactions, assert a
516-clock window scan, and check stall stability of the entire drain pipeline.
Reset and clip-start each abort one execution with all window stages occupied
and another with all drain stages occupied. They retain empty/incomplete clips,
160-sample ring refills, full corpus values, and input gaps from the existing
integer development corpus; no held-out audio is consumed.

Preparation-only smoke completed without Vivado on 2026-10-05. After the
primary task released the shared Vivado slot, the following new simulations
completed. Both the Python runner and Vivado returned exit code 0. The complete
console logs, source snapshots, input/oracle hashes, protocol result, and
per-frame output cycle records are preserved in the named run folders.

| Run under `build/fixed_optimization/` | Frames | FFT input words and window writes checked | Mismatches | TB clocks including gaps/stalls/abort setup | Window scan clocks |
|---|---:|---:|---:|---:|---:|
| `frontend_pipeline_smoke_20261005_01` | 10 | 5,120 each | 0 | 37,995 | 516 every frame |
| `frontend_pipeline_full_20261005_01` | 616 | 315,392 each | 0 | 1,203,028 | 516 every frame |

The full run observed 117,839 output-stall clocks and confirmed that the entire
drain pipeline, including its RAM read data and index, stayed unchanged during
each stall. Both runs performed two synchronous reset checks (window/drain),
two clip-start abort checks (window/drain), and deliberate long stalls at first,
middle, and final outputs. Every affected window and drain stage was occupied
at reset and abort. These cycle totals are TB execution totals, not whole-clip
latency or board throughput. The 516-clock scan count is directly asserted in
simulation, with no output stalls during that internal phase. The same full
corpus retains the existing numerical-accuracy status `NOT_ACCEPTED`.

The candidate file is unchanged from smoke to full simulation. Integration
with sparse/pipelined Mel, synthesis, post-route timing, and board validation
remain owned by the primary optimization task. The shared Vivado slot was
released after the full simulation completed.

### Static review with pending Mel and paired-log candidates

The candidates in `mel_pipeline_candidate01` and `log_pair_candidate01` were
read with the frontend candidate and unchanged top/spectral admission logic.
No functional integration blocker was identified by this static review.

- Spectral credit remains reserved from first FFT input through accepted Mel
  band 25. The faster frontend can issue a contiguous 512-word burst, but cannot
  admit an additional FFT frame without the original complete destination.
  If FFT input ready pauses, the entire frontend drain pipeline freezes.
- The last Mel product is added on the same edge that changes `S_DRAIN` to
  `S_OUTPUT`; the newly visible output therefore includes the complete sum.
  The data/metadata and accumulator remain unchanged under log backpressure.
  Unsigned57 products, unsigned60 accumulation, 61st-bit overflow, and the
  aligned one-clock RAM/ROM read remain identical to the sparse-only arithmetic.
- The log pair captures frame/BFP/error metadata in each unchanged log engine
  when its Mel input is accepted. A new frontend frame may update the live
  top-level `frame_error_reg` only after the old frame's last Mel input has been
  accepted, so the old logs already retain their own metadata. No Mel FIFO is
  introduced by these candidates; the queue metadata warning above is not an
  issue for this specific combination.
- The independent round-robin input/output selectors preserve order when old
  band 25 and new-frame band 0 occupy different lanes. In the worst blocked
  case there can be one DCT frame, one frame represented by pending logs, and
  one reserved spectral frame; the top's unsigned3 inflight counter has room.
  A completed final coefficient is still counted only on valid/ready, so final
  output stalls delay clip completion as before.
- Clip-start drives the same `pipeline_rst_n` to spectral and backend. The
  pair's two selectors and both log engines reset together; the frontend clears
  its own window/drain tokens and memory enables on the same edge. An old
  output cannot become a transfer during clip-start because spectral ready and
  paired-log valid are gated by the reset boundary.

Integration simulation must still verify these conclusions. The physical
timing risk is the placement of the continuously active Mel product/add stages
and the second log engine's DSPs together with the existing DCT BRAM-to-DSP
critical path. No same-frequency throughput claim is accepted until the actual
combined candidate meets 100 MHz post-route timing. No arithmetic narrowing,
accumulation reordering, FFT rewrite, or extra frame buffer was proposed to
address timing in this review.

```powershell
& D:/2610_MFCC/build/python_reference/venv/Scripts/python.exe -B scripts/run_fixed_frontend_pipeline.py --run-id frontend_pipeline_smoke_20261005_01 --frontend D:/2610_MFCC/build/fixed_optimization/frontend_candidate_20261005_01/mfcc_fixed_frontend.sv --smoke
```

## Inspected sources

- `hardware/fixed/frontend/mfcc_fixed_frontend.sv`
- `hardware/fixed/fixed_spectral_top.sv`
- `hardware/fixed/power_mel/fixed_spectral_tail.sv`
- `hardware/fixed/log_dct/mfcc_fixed_log.sv`
- `hardware/fixed/log_dct/mfcc_fixed_dct.sv`
- `hardware/fixed/log_dct/mfcc_fixed_log_dct.sv`
- `hardware/fixed/mfcc_fixed_top.sv`
- `hardware/fixed/fft/fft20_stream_top.sv` and stage READMEs
- `verification/fixed/full_pipeline/tb_mfcc_fixed_top.sv`
- `scripts/run_fixed_full_rtl.py`, `fixed_full_pipeline.tcl`, and
  `verify_fixed_integer_rtl.py`
- `build/system_dma/i10/reports/timing_route.rpt` (preserved baseline)
- `<감사 폴더 20261004>/revision_03/CORRECTIONS_R3.md` and
  `FINAL_CORRECTIONS.md` (preserved correction record)
