# Actual FFT IP unit validation

`scripts/run_fp32_fft_unit.py` creates five deterministic binary32 inputs and
computes an independent explicit scalar float64 DFT of their exact stored words.
It imports only the generated `fp32_fft512` IP into a separate Vivado project,
uses the actual vendor xfft/floating_point simulation libraries, and rechecks
captured hexadecimal outputs in Python. No synthesized FFT behavior is replaced
by a behavioral arithmetic stub.

The current runner defaults to the CE-enabled `ip_04`. A new run name is
required; existing evidence is never overwritten. It snapshots the TB and Tcl
before execution and requires the source IP manifest/XCI to enable `aclken`.

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B `
  'D:\2610_MFCC\project\scripts\run_fp32_fft_unit.py' `
  --run-id fft_unit_ce_new --ip-root 'D:\2610_MFCC\build\fp32_hw\ip_04'
```

## Preserved original IP03 result

The original executed evidence is in `D:\2610_MFCC\build\fp32_hw\fft_unit_probe_01`.
The three original unit source files, matching that run's hashes, are preserved
separately under `D:\2610_MFCC\build\fp32_hw\fft_unit_ip03_source_01`.
Vivado 2024.2 / xfft9.1 revision13 passed all five512-bin vectors. The configuration
word is24'h000001: FWD_INV bit0=1, all18 scale schedule bits0. The result establishes
an unnormalized transform for this exact IP configuration: impulse_n0 returns
1+j0 in every bin; DC1 returns512+j0 at bin0. The sine bin32 has negative imaginary
sign at bin32 and positive imaginary sign at bin480.

| Input | Maximum complex absolute error | RMSE | Violations |
|---|---:|---:|---:|
| impulse n0 | 0 | 0 | 0 |
| DC1 | 3.392603e-11 | 1.950370e-12 | 0 |
| cos bin32 | 1.298810e-5 | 8.058439e-7 | 0 |
| sin bin32 | 1.298807e-5 | 8.609930e-7 | 0 |
| impulse n17 | 1.071084e-7 | 4.991528e-8 | 0 |

The criterion was fixed before execution:
`abs(complex(actual-reference)) <= 2e-5 + 2e-5*abs(complex(reference))`.
DC's tiny reported error comes from finite-precision scalar DFT trigonometry;
the analytical observation records512+j0 directly. The global RMSE is
5.27860748e-7 across2,560 complex bins.

Protocol checks passed for every bin: `XK_INDEX[8:0]` in natural0…511 order,
TUSER padding0, TLAST only at511, finite results, input gaps, back-to-back input,
and stable output data/index/last while stalled. The run exercised1,309 output
stall cycles including a97-cycle hold on each final bin. Nonrealtime input/output
halt event counts are retained as120/163; no missing/unexpected TLAST or status
halt event was accepted.

Evidence files: `run_manifest.json`, `comparison.json`, `vectors.json`,
`fft_ip_properties.rpt`, `console.log`, and
`vivado_project/fft_unit.sim/sim_1/behav/xsim/{simulate.log,fft_results.csv}`.
The source XCI hash is checked before/after the run; the pinned `ip_03` project
is not regenerated in place. This unit result does not certify arbitrary-speech
FFT error, full MFCC error, FPGA routing/timing closure or board execution.

## Clock-enable test

The CE variant retains the five input vectors, scalar DFT oracle and numerical
threshold above. [AMD PG109](https://docs.amd.com/r/en-US/pg109-xfft/aclken-Clock-Enable)
states that low `aclken` pauses core logic. Therefore this TB counts configuration,
input and output transfers only when `aclken && valid && ready`. It checks that
valid output data/index/last remain stable over a disabled clock, including when
the external receiver leaves ready high. Native clock enable does not create a
second clock or replace any vendor arithmetic.

`fft_unit_ce_01` completed with all2,560 bins passing and554 disabled clocks:
configuration19, input155, computation265 and held final output115. Its output
hexadecimal words exactly match the preserved IP03 result (zero mismatches).
The255 artifact hashes were independently rechecked with zero mismatches.

The stronger `fft_unit_ce_02` completed with all2,560 bins passing and zero
hexadecimal word mismatches versus IP03. Its255 artifact hashes also rechecked
without a mismatch. The test pauses37 clocks immediately after bin17
is accepted in every case. The observed coverage is739 disabled clocks:
configuration19, input155, computation265, held final output115 and immediately
after accepted output185. The computation pause begins after256 enabled clocks
following the last input acceptance. The final output also sees the ordinary
97-clock ready stall. Initial reset lasts8 clocks; in-flight reset recovery is a
separate integration test, not a claim of this FFT unit test.
The enabled-transfer counts were one configuration,2,560 input samples and2,560
output bins. It exercised1,366 ordinary output-ready stall clocks; input/output
halt events counted on enabled clocks were120/148. Numerical maximum error and
RMSE remained1.2988095634599429e-5 and5.2786074779549901e-7. Evidence is in
`D:\2610_MFCC\build\fp32_hw\fft_unit_ce_02`, including immutable source snapshots,
`run_manifest.json`, `comparison.json` and the actual vendor `simulate.log`.
