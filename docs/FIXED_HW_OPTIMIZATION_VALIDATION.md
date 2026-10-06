# Fixed optimization independent validation

Audit observation: **2026-10-05 14:29 KST**. Result: **PASS for source identity,
published-oracle identity, sparse reconstruction, and recorded RTL simulation
evidence**. This audit did not run Vivado, synthesis, placement/routing, or board
tools. It does not certify FPGA timing or physical board performance.

The executable audit and complete hashes are preserved separately:

- [audit.py](../../build/fixed_optimization/independent_validation01/audit.py)
- [audit.json](../../build/fixed_optimization/independent_validation01/audit.json)
- [pipeline design and frontend unit evidence](FIXED_HW_OPTIMIZATION_PIPELINE_REVIEW.md)

The audit process returned exit code **0**. It read source snapshots and
published binary vectors directly with Python's standard library. It did not
call the current sparse generator or numeric model to generate its expected
vector values. No root results report, live RTL, coefficient, original input,
existing run, thesis, or presentation file was modified by this audit.

## Source identity and actual change scope

The baseline is the optimization task's
`build/fixed_optimization/baseline_20261005_01` snapshot. All 24 fixed `.sv`/`.mem`
files match both the recorded and actual `build/system_dma/i10/source` files.
All listed source snapshot hashes were checked: baseline 35 files, sparse 31,
and final 32, including their recorded scripts and TB where present.

| Snapshot | Fixed `.sv`/`.mem` files | Independent identity result |
|---|---:|---|
| Baseline task snapshot / i10 | 24 | 0 mismatches |
| `fixed_full_rtl/opt_sparse_full01` | 26 | 0 snapshot-hash mismatches |
| `fixed_full_rtl/opt_pipe_full01` | 27 | 0 snapshot-hash or live-source mismatches |
| `system_dma/os01` source snapshot | 26 | Exactly the sparse core source set and hashes |
| `system_dma/op01` source snapshot | 27 | Exactly the final core source set and hashes |

Baseline to sparse changes are confined to:

- `fixed_spectral_tail.sv`: nonzero traversal with the original read/multiply/
  accumulate arithmetic.
- New `fixed_mel_descriptor.sv` and `mel_sparse_fw16.mem`.
- `fixed_spectral_top.sv` and `mfcc_fixed_top.sv`: only the default Mel ROM
  filename changes. Replacing that filename reconstructs the baseline wrapper
  text exactly; admission, error propagation, and inflight logic are unchanged.

Sparse to final changes are confined to:

- `fixed_spectral_tail.sv`: overlapping reads, full products, and accumulation.
- `mfcc_fixed_frontend.sv`: window scan and FFT drain pipelines.
- New `mfcc_fixed_log_pair.sv` and its use in `mfcc_fixed_log_dct.sv`.
  Replacing the instantiated pair module name with the scalar log module name
  reconstructs the original backend wrapper text exactly.

No file was removed from either fixed core set. The exact SHA-256 values are
recorded in `audit.json`; useful source identifiers are:

| File | Sparse SHA-256 prefix | Final SHA-256 prefix |
|---|---|---|
| Spectral tail | `690b3818fd9b6575` | `ce3e24e431574623` |
| Frontend | `421bad8c67cae2bb` | `c8196417ec7dbaad` |
| Log/DCT wrapper | `5e53686b94cc5c21` | `3a8c702dc1983beb` |
| Ordered log pair | absent | `f57b315239b72017` |
| Compact coefficient ROM | `c994327789403732` | identical |
| Descriptor | `216e1b1125cb7b69` | identical |

All FFT working-copy RTL and twiddle bytes match baseline, sparse, and final.
The 13 original FFT source/ROM entries referenced by `PROVENANCE.json` were
also read and matched their original pinned hashes. Scalar log, DCT, DCT
coefficients, floor thresholds, window coefficients, and the original dense
`mel_fw16.mem` are byte-identical across all three versions. The dense ROM
SHA-256 remains `a26829496cc73478100f01df692eba7ec5a3277ba4e41fe775604a032bda9e5c`.

## Published contract and independent oracle check

The published v2 contract remains:

```text
283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e
```

The publication manifest, verification manifest, and all **437** artifacts
listed by the published artifact inventory passed direct SHA-256 checks. The
source numeric run manifest matches the published pin and both new full-RTL
runs. Both runs select the same 24 development/synthetic cases and 616 frames.
No held-out evaluation audio is used by this audit.

The audit independently decoded the published PCM and stage binaries with
`struct`, applied only the documented storage masks, and combined real/imaginary
FFT words in the TB's 40-bit packing. All **936,854** input/oracle words across
eight files matched the preserved baseline full-616 run, sparse run, and final
run. The vector file bytes are also identical across all three runs.

| Vector | Words checked per run |
|---|---:|
| PCM | 107,102 |
| Frontend signed16 | 315,392 |
| Complex FFT packed40 | 315,392 |
| Power unsigned40 | 158,312 |
| Mel unsigned60 | 16,016 |
| Log signed30 | 16,016 |
| MFCC signed40 | 8,008 |
| BFP exponent | 616 |

The audit independently parsed all 26 emitted descriptor cases, reconstructed
the dense matrix from the compact ROM, and compared it with published integer
coefficients. It confirmed 6,682 active dense terms, **459 nonzero terms**,
**6,223 skipped exact zeros**, 1,510 dense padding zeros, and 53 compact padding
zeros. All descriptor addresses stay within the stated support and ROM bounds.
Independent integer dot products reproduced **16,016 Mel values**, with no
mismatch and no unsigned60 overflow on this corpus.

This proves the checked structural transformation against the frozen integer
contract. It does not change fixed-versus-float64 accuracy acceptance. The
existing development error, synthetic failures, and `NOT_ACCEPTED` status are
retained.

## Recorded simulation evidence and coverage

The new full-RTL manifests, protocol files, console logs, selected artifact
index entries, vector files, and listed source hashes were cross-checked. The
audit verified 45 selected artifact hashes for sparse and 46 for final. Both
manifests record Vivado exit **0**, `implementation=none`, and a completed PASS
simulation; their recorded protocol matches the standalone protocol file.

| Full integration run | Frames | TB clocks | Output-stall clocks | Bit mismatches |
|---|---:|---:|---:|---:|
| Preserved `full_616_repro_20261004_07` baseline | 616 | 14,410,168 | 15,457 | 0 |
| `opt_sparse_full01` | 616 | 3,660,687 | 15,546 | 0 |
| `opt_pipe_full01` | 616 | 1,828,330 | 15,599 | 0 |

These are testbench execution totals with input gaps, output stalls, setup,
abort checks, and multiple clips. They are not ZYBO clip times, pure compute
cycles, first-result latencies, or steady-state frame initiation intervals.
Sparse and final use the same full-pipeline TB source hash. The older baseline
TB lacks the new passive stage-event instrumentation.

Each new full run checks 315,392 frontend samples, 315,392 complex FFT bins,
158,312 power values, 16,016 Mel values, 16,016 log values, and 8,008 MFCC values.
Frame/index/exponent/last fields are checked at their relevant boundaries;
final MFCC error must be clear. The TB includes empty and incomplete clips,
single/multiple-frame drain, periodic PCM gaps, periodic final output stalls,
an initial 5,000-cycle long output stall, reset after 173 accepted PCM samples,
and reset while FFT work is in flight.

| Additional affected-unit run | Checked result | Explicit coverage |
|---|---|---|
| `fixed_power_mel/opt_sparse_full01` | 621 frames; 16,146 Mel, 159,597 power outputs; PASS | 26,660 stall checks; six reset/malformed/credit checks |
| `fixed_power_mel/opt_pipe_full01` | Same output counts; PASS; 285,039 Mel requests | Request II min=max=1 within bands; 31,309 stall checks; six reset/malformed/credit checks |
| `fixed_optimization/frontend_pipeline_full_20261005_01` | 616 frames; 315,392 window writes and frontend outputs; 0 mismatches | Every scan 516 clocks; 117,839 stalled clocks; two reset and two clip-abort checks; first/middle/final long stalls |
| `fixed_log_pair/log_pair_20261005_01` | 733 accepted/emitted values; PASS | 332 adjacent admissions; 3,336 ordered-wait clocks; 878 stalls; reset with pending/held outputs; odd-count drain |

The Mel unit's 621 frames comprise its 616-frame reference corpus plus five
directed FFT-boundary frames; it is a unit input boundary, not an additional
set of end-to-end PCM clips. Its six checks cover partial FFT reset, duplicate
reservation rejection, reset during Mel work, reset while output is held,
malformed bin order recovery, and early-last pipeline drain. The two log engines
use the unchanged scalar arithmetic; the pair test includes published floor
boundaries, long/floor result mixtures, invalid exponents, error metadata,
seeded values, and out-of-order lane completion.

Coverage limits remain explicit. This is finite simulation rather than formal
proof over every possible PCM sequence or ready pattern. The full integration
TB does not independently assert every internal ready/valid signal's stall
stability; dedicated frontend, Mel, and paired-log tests supply those affected
boundary checks. The unit frontend reset/abort tests populate every new stage,
but do not exhaust all combinations of stage occupancy. The final integration
does not introduce a new malformed-Mel/DCT metadata campaign; DCT is unchanged,
the pair test verifies its forwarded metadata, and integration checks correct
corpus order through final output. No physical clock-domain or analog timing
property is established by these simulations.

## Arithmetic, style, and integration review

All changed/new authored `.sv` files passed lexical checks for forbidden
`for`, generate-for, function, task, initial, loop substitutes, real arithmetic,
`casex`, and wildcard port connections. Stateful modules use one `always_ff`
and one `always_comb`; pure connection wrappers have neither, and the generated
descriptor has one combinational process. No negative-edge/asynchronous reset
block was added. This lexical scan supplements manual review and Vivado parsing;
it is not a SystemVerilog parser or a synthesis guarantee.

Manual review and executable expression checks found:

- Mel retains full unsigned40×unsigned17 products, unsigned57 product storage,
  unsigned60 accumulation, and the original 61st-bit overflow check. Registered
  reads align power/weight; the last product retires before output becomes
  valid. Terms remain in ascending bin order and output stalls hold the sum.
- Frontend retains all 19 checked arithmetic assignment expressions unchanged,
  including exact preemphasis, full signed64 window product, RNE, peak/BFP test,
  signed40 quantization, and rounding. Window write value/index are delayed
  together. One advance condition freezes BRAM reads and all drain stages under
  output stalls; final acceptance controls frame completion and ring refill.
- The pair adds no numeric arithmetic. Input and output selectors advance only
  on their respective handshakes, preserving order when a younger floor result
  finishes first. Both lane outputs hold their own frame/exponent/error fields.
- Original one-frame spectral reservation is retained. FFT output is never
  treated as stallable. At most DCT, pending log, and one reserved spectral
  frame coexist; the unchanged three-bit inflight counter has room. Clip-start
  resets frontend token validity and both backend selectors/lanes together.
- No bit width, coefficient, rounding point, floor threshold, frame/coeff order,
  FFT numeric core, or DMA record layout was changed to obtain the cycle gain.

The combined candidate still requires post-route 100 MHz timing evidence. The
baseline critical setup path was DCT BRAM to DSP; new continuous Mel stages and
the second log engine can alter placement and timing despite preserved widths.

## Route and board status at this audit

**Candidate route/timing: NOT_RUN. Candidate board execution: NOT_RUN.** The
audited full-RTL runs explicitly leave synthesis, implementation, timing, and
board fields at `NOT_RUN`.

During the audit, `system_dma/os01` had configuration PASS but overall status
`failed`, reporting `simulation did not complete: exit=1, success_sentinels=0`.
DMA simulation, board implementation, and board execution remained `NOT_RUN`.
`system_dma/op01` was `prepared`, with those validation steps `NOT_RUN`. The
snapshot identities above remain valid independently of these execution states.
This audit does not classify the separate system-run failure's cause or resolve
it; the board/system worker owns that follow-up. Later corrected runs and board
measurements must be cited by their new results rather than replacing this
observation-time record.

To reproduce the read-only audit without replacing its first result:

```powershell
& D:/2610_MFCC/build/python_reference/venv/Scripts/python.exe -B D:/2610_MFCC/build/fixed_optimization/independent_validation01/audit.py --output D:/2610_MFCC/build/fixed_optimization/independent_validation01/audit_recheck.json
```

The script refuses to overwrite an existing JSON output. Prepared-system
execution status can legitimately change during subsequent implementation;
source/oracle mismatches remain failures.

## Subsequent completed route and board evidence, 2026-10-05

The earlier NOT_RUN statements above record the original audit time. Subsequent independent runs completed without changing those snapshots: sparse `system_dma/os02` / `arm_dma/as02`, and combined pipeline `system_dma/op02` / `arm_dma/ap02`. Both passed actual DMA/PS simulation and 100 MHz implementation. Sparse setup/hold slack is +0.539/+0.020 ns; combined pipeline is +0.159/+0.017 ns.

Actual board runs `board_validation/fixed_opt_20261005_01/sparse_board01` and `pipeline_board01` both completed with `BOARD_DEVELOPMENT_BIT_EXACT_PASS`, three warmups and 30 measured repeats. Independent offline audit results and the auditing script are preserved in `build/fixed_optimization/independent_board_audit01`:

- All 33 × 6,942 = 229,086 complete 24-byte records per candidate match the pinned contract, including value, frame, coefficient index, BFP and last fields; zero mismatches.
- Raw trial bytes reproduce sparse median 31.878368075527327 ms and combined median 16.343057526051332 ms, and BUSY medians 30.8347703083477 / 15.2998401529984 ms.
- Six pre/post phase clock snapshots per run corroborate the unchanged configured PL frequency of approximately 99,999,999 Hz. ELF/XSA/bit/PS init, embedded ELF oracle/layout/symbols, artifact hashes and fixed-evidence source bindings pass.
- The current 27 fixed RTL/ROM files still exactly equal `opt_pipe_full01`'s verified snapshot.

Final collection `build/fixed_optimization/comparison01` has no evidence errors and retains the preserved same-day baseline for comparison. Results do not change the published numerical acceptance classification: NOT_ACCEPTED. BUSY includes stalls; clip/534 is an amortized average, distinct from latency and initiation interval. No held-out speech was evaluated.
