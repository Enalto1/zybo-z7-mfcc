# Spectral integration verification

`run_fixed_spectral.py` reads a published C handoff bundle and verifies its
publication and artifact SHA-256 before creating a fresh run directory. Every
executed run snapshots the exact RTL, testbench, memory initialization data and
runner. The numerical oracle is the bundle's signed integer arrays, including
all512 complex FFT bins,257 unsigned40 power bins and26 unsigned60 Mel bands.
No FFT or Mel outputs are recalculated using floating-point arithmetic in this
RTL regression.

The synthesized boundary is `fixed_spectral_top`: signed16/F15 complex samples
plus a frame ID and signed8 BFP exponent. Input promotion is exact by fourLSBs;
output data is unsigned60 Mel with frame ID, band index, last, BFP exponent,
overflow and protocol-error signals. The debug FFT output is observational and
cannot stall. The main output uses ordinary valid/ready transactions.

One frame may be in flight. A new first sample is accepted only while the
Power/Mel tail can reserve its entire frame bank. The FFT consumes that frame
and autonomously drains; all512 FFT beats are accepted without output-ready.
The tail stores257 useful powers in a512x40 XPM block RAM. A complex-input
register stage and a full-square register stage carry matching valid/bin/last/
overflow metadata before the square-sum write; the Power debug transaction also
has its own register. The two pipeline entries are covered by the reserved
frame and never require FFT backpressure. It ignores the unused255 negative-frequency
bins only after checking their sequence and final last. Mel reads use one-cycle
synchronous RAM/ROM latency and a separate multiply then accumulate cycle.
The parent admits another frame only after the26th Mel output is consumed.
Consequently an indefinitely stalled Mel consumer cannot overrun a downstream
frame buffer: the next FFT frame has not been admitted. The FFT's internal
reorder separately retains two ping-pong banks and is tested with uninterrupted
frame input by the standalone FFT test.

The testbench covers:

- All values and natural-order indices for512 FFT bins,257 Power bins and26 Mel
  bands per frame. FFT and Mel integers are also captured as CSV and checked
  again in Python.
- Frame IDs deliberately remapped to `1000+7*contract_frame`; BFP metadata and
  numerical overflow must follow the same frame under backpressure.
- Input gaps and consecutive frame requests. The producer holds a sample until
  `valid && ready`, so full-frame admission backpressure is explicitly counted.
- Periodic output stalls plus1000-cycle stall windows, checking complete output
  data/metadata/valid stability on every stalled clock.
- Reset after173 accepted samples, during FFT output capture, and while a Mel
  output is stalled. A malformed input last is detected and reset recovery is
  tested before the complete corpus.
- Final frame drain without another input frame; no additional outputs after
  all expected transactions. The published complex overflow stress frame is
  immediately followed by its recovery frame.

```powershell
python -B scripts/run_fixed_spectral.py --run-id spectral_NEW_ID `
  --contract D:/2610_MFCC/build/fixed_contract/v1_fft20_power40_mel60_20261004_r2 `
  --implement
```

`--limit` is a smoke-test option; a limited run is explicitly recorded and does
not establish full corpus coverage. The default executes634 frames, comprising
all616 normal frames and18 directed frames in v1r2. Zero-frame cases remain in
the contract input manifest and emit no spectral frames.

`--implement` runs syntax checking, synthesis, optimization, placement and
routing on `xc7z020clg400-1`, with a10ns clock and explicit1ns OOC input/output
delays. It separately records synthesis and routed reports and verifies that
actual BRAM cells exist under both FFT reorder and Power RAM hierarchy. The
OOC reports do not establish board pin/clock timing, PS7 integration, or MFCC
algorithmic accuracy. Physical port locations and the top-level clock buffer
location remain unset (`HD.PARTPIN_LOCS`, `HD.CLK_SRC`); the corresponding
Vivado warnings limit these results to OOC internal timing. No board
bitstream/download is performed.

The first complete634-frame run, `spectral_full_03`, verified all integers but
exposed a synthesis path from the unregistered FFT BRAM output through square,
sum and overflow logic (synthesisWNS-2.378ns; final routedWNS-2.383ns at100MHz).
Thus its634-frame bit match was insufficient for timing acceptance. The two added arithmetic pipeline stages
address that integration timing path without changing any numerical contract;
subsequent runs separately verify their bits and actual routed timing. Earlier
results and their exact source snapshots remain preserved.

The subsequent `spectral_pipeline_full_04` run passed all 634 frames: 324,608
complex FFT bins, 162,938 Power values and 16,484 Mel values matched exactly.
It checked output stability on 189,142 stalled cycles and observed 13,559,763
input backpressure cycles. Reset, malformed-last recovery, input gaps, frame
metadata, the intentional overflow frame and its recovery, and final drain all
passed. Its constrained synthesis WNS was +1.171 ns. Actual routed timing at
100 MHz passed with WNS +0.634 ns and WHS +0.050 ns. Routed resources were
3,607 LUT, 1,588 FF, 24 DSP, nine RAMB36 and four RAMB18 (11 BRAM tiles).
The FFT reorder used two RAMB36 plus one RAMB18; the Power bank used two
RAMB18. Both frame-buffer hierarchies used zero LUTRAM. The 894 LUTRAMs
elsewhere belong to the inherited FFT SDF feedback arrays under E-FFT.

`D:/2610_MFCC/build/fixed_spectral/spectral_pipeline_full_04_audit.json`
records the independent hash/report audit: 23 source snapshots, 35 run
artifacts and all 13 preserved original FFT files verified. All 12 timing
constraint checks reported zero violations. The original negative timing
result remains under `spectral_full_03`; no numerical contract changed for the
pipeline repair. These are RTL bit-equivalence and OOC timing results, and do
not change the separately pending MFCC numerical-accuracy status.
