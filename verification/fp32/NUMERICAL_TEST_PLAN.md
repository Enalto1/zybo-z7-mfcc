# FP32 IP MFCC numerical and transfer checks

This is a verification plan, not a record of completed tests. The run's manifests and simulator reports determine what actually ran. No evaluation20 input is part of the initial work. The shared definition and unchanged initial tolerances come from `docs/MFCC_SPEC.md` and `verification/c/tolerances.json`.

## Sources and numerical scope

`generate_coefficients.py` reads only the frozen C run's coefficient/provenance files, verifies their artifact-index hashes, and writes exact binary32 bit patterns for window, Mel, DCT cosine and DCT scale. No coefficient formula is regenerated. Dense logical word counts are 512, 6682, 338 and 13; their audit `.mem` capacities remain 512, 8192, 512 and 16. The backend instead reads `mel_compact.mem` (459 logical words padded to512) and `mel_descriptor.mem` (26 integer metadata words padded to32). Dense `mel.mem` is retained for traceability and is not the active Mel ROM. Trailing words are zero, are recorded as padding, and must not be addressed as valid coefficients.

Each descriptor packs first bin in bits8:0, inclusive last bin in17:9, compact word offset in26:18 and reserved zeros in31:27. The generator requires 26 nonempty contiguous runs, `0<=first<=last<=256`, consecutive offsets and `offset+last-first+1<=459`. It reads back both generated files, scatters their decoded words into a zero-filled26x257 array and requires exact equality with all6682 original binary32 words. It also compares the full ordered `(row,bin,word)` MAC sequence. Thus all459 multiply/add pairs and their order are preserved; the6223 skipped source words are positive zero. The backend validates descriptor ranges at runtime before accessing coefficient/power RAM.

The backend receives 512 already-windowed float32 samples per complete frame. It uses one fresh Vivado FFT IP, then shared multiplication/addition/log IP transactions. It calculates `(re*re + im*im) * 2^-9`, serial ascending-bin Mel sums, natural `ln(max(E, binary32(1e-12)))`, ascending Mel-index cosine DCT sums and a separate normalization multiply. It does not replace C0 with frame energy or apply lifter/delta. Exactly zero Mel weights are skipped; adding positive zero to a finite nonnegative accumulator is an identity. All 459 nonzero coefficients retain their original ascending order.

The vendor FFT has a floating-point interface but may use an internal fixed/pseudo-floating implementation. `scaling_options=scaled` and truncation-related XCI fields are retained as vendor settings, not proof of a mathematical gain. The reviewed generated interface uses a 24-bit configuration word, bit0 forward/inverse and bits18:1 schedule. The backend requests `24'h000001`; its actual gain and error must be measured by the unit tests below. Do not silently normalize observed FFT results to make a test pass. Any deliberate gain correction would need a reviewed change and a new run.

Vendor FP operations use the documented rounding/denormal behavior of the generated IP, which is not assumed to equal the PC CRT or complete IEEE754 handling. In particular subnormal operands/results may flush to zero. The selected log floor is normal binary32. No double/real/shortreal mathematical model substitutes for an IP in synthesizable RTL.

## Required order of checks

1. Generate and inspect the actual 2024.2 IP ports/settings. Record IP names, XCI hashes, reset duration, latencies and files used by xsim/synthesis. Fresh generated wrappers are external vendor code; local wrappers follow `RTL_CODING_RULES.md`.
2. Exercise the independent ALU A/B/operation-channel handshakes, request backpressure, response backpressure and reset during work. Confirm add opcode00, subtract01, multiplication and natural log values against an independent reference. A transaction must never be duplicated when the A/B ready cycles differ. Reset aborts outstanding work; recovery begins after the vendor's reset requirement and configuration handshake.
3. Run the actual FFT IP on 512-sample impulse at n=0, impulse at n=1, DC, positive complex bin tone and real tone. Validate output count/order/TUSER/TLAST, negative forward exponent, unnormalized gain, bin location and real/imaginary values. Repeat supported output stalls and input gaps. Test near-zero and representative normalized amplitudes. A unit FFT failure is preserved separately from MFCC errors.
4. Run a backend-only complete frame with known windowed inputs and export each accepted FFT bin0…256, power257, raw Mel26, log26, DCT13 and final output. First classify metadata/count/nonfinite errors, then calculate maximum absolute error, RMSE and per-element tolerance violations against pinned Python and PC C intermediates. Numerical failures remain failures.
5. Integrate PCM conversion, continuous preemphasis, frame/hop/window and backend. Use the same pinned 17 synthetic cases and one development utterance. Record zero-frame and incomplete-tail behavior, two adjacent overlapping frames and continuous frame IDs/start indices. Do not open evaluation20 to tune the design.
6. Exercise reset, single-frame drain, input-valid gaps and output-ready stalls. Hold each output's valid/data/index/frame/start/last unchanged while stalled. The source frame bank is released only after C12 is accepted. Malformed sample index/metadata/TLAST or nonfinite numeric data sets sticky error and requires reset; it must not produce a successful frame.
7. Separate simulation, synthesis and physical timing evidence. Report latch/width warnings, resource counts, achieved timing constraints and measured simulator cycle counts only after those checks execute. Board power/performance/recognition accuracy remains unmeasured unless separately measured.

## Debug and error contract

`fp32_backend` produces registered one-cycle `dbg_valid` events, with `dbg_index`, `dbg_data` and `dbg_aux`. Frame identity is `m_frame_id/m_start_sample`, which remains unchanged throughout a frame.

| stage | payload |
|---|---|
| 1 | FFT bin0…256, data=real, aux=imaginary |
| 2 | power bin0…256, aux=0 |
| 3 | raw Mel energy0…25, before floor |
| 4 | natural log Mel0…25 |
| 5 | normalized DCT C0…C12, identical boundary to MFCC output |

`o_error` codes: 1 input index/TLAST; 2 frame/start metadata; 3 nonfinite input; 4 FFT bin/TLAST order; 5 nonfinite FFT; 6 vendor TLAST event; 7 ALU wrapper fault; 8 negative power/energy; 9 nonfinite ALU response; 10 illegal backend state; 11 invalid compact Mel descriptor. Negative zero is accepted as zero and floored before log. Expected nonrealtime FFT input/output halt events are not numerical faults. Sticky errors clear only on reset.

## Cycle budget to measure

For one frame, the backend issues 1028 power operations, 918 nonzero Mel multiply/add operations, 26 log operations and 689 DCT/scale operations: **2661 FP transactions**, unchanged from the dense-scan revision. This is an operation count from the implemented schedule, not a simulated cycle result. Each transaction includes wrapper handshakes and vendor latency. ROM/RAM accesses use read latency2 with explicit wait states; only459 Mel positions are visited, with three descriptor-read states per filter. Relative to the earlier dense scan, removing6223 zero visits of four states each and adding26x3 descriptor states projects **24,814 fewer cycles per frame**. This schedule calculation is not a measured simulator speedup or board result. FFT configuration/input/output, negative-bin drain and output stalls add cycles. Report both compute cycles with output ready and observed end-to-end cycles with specified stalls. A100MHz constraint is a target until physical timing is verified.

The compact revision requires a fresh full-stage bit comparison against the preserved dense-CE smoke outputs, followed by the unchanged17 synthetic and534 development frames and new synthesis/routing. Existing dense runs and their source snapshots are preserved; their success cannot establish the new address schedule's correctness. Expected Mel storage reduction is from8192x32 to512x32 plus32x32; the actual BRAM mapping and timing must come from the new implementation report.

## Simulator performance investigation

The installed 2024.2 catalog at `C:/Xilinx/Vivado/2024.2/data/ip/xilinx/floating_point_v7_1/component.xml`, lines1642ff, lists the encrypted `floating_point_v7_1_rfs.vhd/.v` as its behavioral simulation files. The same package's `doc/floating_point_v7_1_changelog.txt`, line283, records replacement of the earlier behavioral VHDL model by encrypted RTL. Inspection found no generated/catalog `C_MODEL_TYPE` switch to a faster cycle-compatible arithmetic model. Supplied `cmodel/*bitacc_cmodel*.zip` libraries are separate bit-accurate C APIs, not ready/valid replacements for the current RTL testbench.

The actual 2024.2 `xelab -help` documents `--debug off`, default optimization `--O2`, and advanced optimization `--O3`. Its `--mt` controls parallel sub-compilation jobs, not parallel simulation of clock cycles. The inspected `xsim -help` exposes no runtime worker-thread setting. Disabling unused waveform/debug instrumentation and trying O3 preserves the actual vendor DUT; its speed and identical results still require a fresh run. Do not label a custom real/shortreal/software model as the actual IP to shorten simulation.

## Vendor clock-enable contract

The ACLKEN-enabled IP revision pauses inactive vendor datapaths using their real hardware enable ports; no fabric clock gate or simulation-only arithmetic is introduced. Each ALU IP is enabled during its selected ISSUE/WAIT states, and FFT is enabled during CONFIG/FEED/FFT_READ. The edge that accepts a result remains enabled. While FFT is paused for power/Mel/DCT processing, its held output has ready low; the resumed read state can accept the next bin. A register forces one enabled idle clock after reset release, with no new request/configuration handshake during that recovery clock.

[PG060 v7.1, printed page10](https://docs.amd.com/api/khub/documents/ym1A7qsltTGP_saZFTrikQ/content) specifies preservation of FP state/output with enable low, reset priority and a minimum two-clock reset. [PG109 clock enable](https://docs.amd.com/r/en-US/pg109-xfft/aclken-Clock-Enable) permits pausing all FFT logic, while [PG109 reset](https://docs.amd.com/r/en-US/pg109-xfft/aresetn-Synchronous-Clear) specifies reset priority and its two-clock minimum. These are design grounds, not evidence that the modified integration has passed. New ALU, FFT, integration and timing runs must use the matching regenerated IP revision. The ALU test checks actual vendor data/valid/ready stability on disabled edges, inactive wrapper handshake controls, long response holds and reset recovery. FFT pause/resume and numerical identity require their own runs. Clock enable can affect maximum frequency, so pre-enable physical timing does not validate the changed design.
