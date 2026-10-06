# FFT20 E-FFT work copy

This directory is isolated from the preserved source in
`D:/2610_MFCC/reference_code/previous_fft/rtl`. `PROVENANCE.json` pins every
original file and its transformed copy. Only the files needed by the streaming
top are copied. The unsafe original AXI wrapper is not included.

`fft20_stream_top` retains the original streaming interface, except complex
input and output components are signed20. MFCC integration fixes `cfg_log2_n=9`.
The parent promotes signed16/F15 inputs exactly by concatenating four zero LSBs
and retains the frame BFP exponent separately. Exposed configuration sizes
other than512 retain inherited logic but are not covered by this new run.

| Boundary | Signed | W | F | Operation and overflow |
|---|---|---:|---:|---|
| FFT input / each group output | yes | 20 | 19 | Two's complement |
| Type-I sum/difference | yes | 21 | 19 | Full sum/difference |
| Type-II including minus-j rotation | yes | 22 | 19 | Wrap and flag |
| Twiddle | yes | 16 | 15 | Original1024-entry ROM, source/hash preserved |
| Real product | yes | 38 | 34 | Full22x16 product |
| Complex product sum/difference | yes | 39 | 34 | Full carry guard bit |
| Twiddle requantization | yes | 22 | 19 | Right15, nearest ties to even, wrap and flag |
| Full group output | yes | 20 | 19 | Right2, nearest ties to even, wrap and flag |
| Trailing Type-I output | yes | 20 | 19 | Right1, nearest ties to even, wrap and flag |

N512 has four complete groups and one trailing Type-I: physical scaling S=9.
Twiddle fractional-bit removal and exact input promotion are not additional
physical FFT scaling. The final output format is unchanged20/F19. Natural bins
0..511 are all emitted; only downstream MFCC Power/Mel discards bins257..511.
Overflow is accumulated per frame and repeated on each reordered output beat.

Reset is synchronous active-low. Configuration must be accepted after reset.
Input advances only on `in_valid && in_ready`. Mid-frame input gaps are allowed.
A gap after the last accepted input triggers512 dummy tokens and autonomous
draining; no extra input frame is required to release the last real frame.

There is no output-ready port anywhere on the core/reorder stream. It cannot be
stalled by downstream backpressure. MFCC integration must reserve a complete
frame destination before accepting its first input. The proposed parent admits
one in-flight frame and carries frame ID/BFP metadata until its output burst
has been captured. Standalone FFT regression additionally exercises continuous
frames independently of that conservative parent protocol.

The original reorder has two ping-pong banks, each1024 complex words. Its four
arrays with a multiplexed output register could not map to BRAM: Vivado8-6849
reported LUTRAM fallback in `fft20_directed_02`. The work copy now stores the
same81,920bits in one2048x40 simple dual-port array, with the bank bit in the
address and a separate unreset synchronous read register. Natural-order reading
starts only when a whole frame is present. The one-beat read latency is preserved
and valid state masks stale data. In `fft20_bram_03`, Vivado2024.2 synthesis
confirmed the reorder as twoRAMB36 plus oneRAMB18 and zeroLUTRAM. All41 directed
frames remained bit-identical, including exactly the same first/last output
cycle as the preceding implementation. The same run completed placement and
routing at 100 MHz with setup WNS +0.156 ns and hold WHS +0.121 ns. Routed
resources were 3,950 LUT, 1,378 FF, 20 DSP, five RAMB36 and one RAMB18 (5.5 BRAM
tiles). Explicit 1 ns OOC input/output delays covered the external ports; there
were no unclocked registers or unconstrained internal endpoints. These results
cover this FFT unit's OOC internal timing. Physical external port locations and
the parent clock buffer location remain unset, so external-port and board
timing need the final top-level implementation.
Small SDF feedback delay arrays retain the source's asynchronous-read structure under
E-FFT, so those are separately reported from frame-buffer BRAMs.

The E-FFT exception preserves original generate loops, helper functions, ROM
initialization and reset/control style only within these copied modules.
The testbench is nonsynthesizable and separately compiled.

Run with the project Python environment:

```powershell
python -B scripts/run_fixed_fft20.py --run-id fft20_NEW_ID --implement
```

The command refuses to overwrite an existing run under
`D:/2610_MFCC/build/fixed_fft`. It snapshots sources, creates full512 complex
input/expected vectors, tests transaction values/index/last/overflow, captures
all output integers, checks reset of a173-sample partial frame, input gaps,
continuous frames, overflow followed by recovery, and final frame drain.
`--vectors-json` appends records `{name,re:[512],im:[512]}` at the signed20 raw
input boundary. The implementation is OOC100MHz on `xc7z020clg400-1`; it does not
generate a board bitstream or prove full MFCC numerical accuracy.
