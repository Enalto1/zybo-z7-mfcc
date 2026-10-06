# Full integer model and frontend/backend verification

`test_full_integer.py` checks eight independent arithmetic properties: signed
ties, continuous exact-rational preemphasis, exhaustive reciprocal decomposition,
integer BFP boundaries, narrowed RTL quantization, log floor/oracle error, portable
signed64 log conversion, and exact DCT accumulation/rounding. Runtime model code
is `software/fixed_model/full_integer.py`; coefficient generation and float64
error analysis are separate scripts.

From the project directory, use the existing Python reference environment:

```powershell
& D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe -m unittest verification.fixed.full_integer.test_full_integer -v
```

`scripts/verify_fixed_integer_rtl.py --model-run <immutable-model-run> --run-id
<new-id> --units log,front,back --synthesis` generates vectors, snapshots every
frontend/backend source, and executes Vivado 2024.2. Existing run directories are
rejected. The generated disk project and Tcl remain in the new build folder.
`scripts/synth_fixed_integer_rtl.py --source-run <unit-run> --run-id <new-id>
--units front,back` synthesizes the exact source snapshot independently of unit
simulation. Both use xc7z020clg400-1, a 10 ns clock and 2 ns input/output delays.

The corpus is 534 development-speech frames plus 82 frames from the required
17 original and six added synthetic cases. No evaluation-20 data is used.
Frontend comparison covers all 315,392 quantized samples, frame/index/BFP/last,
input gaps, output stalls, reset, continuous preemphasis and overlapping frames.
Backend comparison covers 8,008 corpus MFCC values plus 26 values from an
intentionally malformed frame and its recovery frame. Scalar log comparison
covers 1,029 transactions, including all 27 BFP floor boundaries, random values,
unsigned60 maximum, invalid exponent metadata and recovery. The backpressure
tests compare transactions and assert stable payloads while stalled.

Final stage evidence as of 2026-10-04:

| Scope | Immutable build directory | Result |
| --- | --- | --- |
| Integer model, all 616 frames | `fixed_full_model/integer_02_20261004` | Complete; no input clipping/FFT overflow |
| Eight arithmetic tests, source snapshot and hashes | `fixed_full_model/model_unit_narrow_quant_20261004` | PASS |
| Latest frontend simulation | `fixed_integer_rtl/front_11_20261004` | 315,392 transactions PASS |
| Same frontend snapshot synthesis | `fixed_integer_rtl/front_synth_12_20261004` | 1,143 LUT, 364 FF, 5 DSP, 2 RAMB18, zero LUTRAM/latches |
| Latest log/DCT simulation and synthesis | `fixed_integer_rtl/back_07_20261004` | 1,029 log and 8,034 MFCC transactions PASS; 786 LUT, 437 FF, 11 DSP, 1 RAMB18 |

All paths above are relative to `D:\2610_MFCC\build`. Frontend synthesis has one
external `o_last` timing violation: WNS -0.217 ns with the unit IBUF/OBUF and IO
constraints. The worst reported internal path has +1.075 ns slack; the other
reported external outputs have +0.454 ns and +0.477 ns slack. This is not a unit
timing pass. Log/DCT synthesis WNS is +0.430 ns, TNS zero and
WHS +0.132 ns. These are synthesis estimates; full integration, placement and
routing are reported separately by the top-level verification flow.

The numerical acceptance status remains false. Development MFCC RMSE is
0.0062602447152708155, while all synthetic frames have RMSE
0.8940570954424044 and maximum absolute error 6.591594298124889. All 60 residual
floor regressions are preserved. `integer_02_20261004/residual_diagnosis.json`
traces them to zero quantized Mel energies in full-scale alternating, bin-32
tone and silence-to-full-scale cases; the isolated integer log/DCT approximation
is at most 3.744859213838936e-7. Bit equivalence does not waive those errors or
establish full MFCC accuracy acceptance.

Earlier failed harness and timing experiments remain intact. In particular,
`units_01` had an in-memory project harness failure; `units_02` accidentally ran
past completed simulations; `units_03`/`units_04` passed transaction checks but
failed at an unsupported report Tcl command. Their results are not silently
promoted to clean tool completion. Later clean runs and source hashes identify
the accepted simulation evidence.
