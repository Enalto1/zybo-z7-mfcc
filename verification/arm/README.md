# ARM runner and evidence boundary

`scripts/run_arm_reference.py` defaults to offline preparation. It verifies all
pinned Python and PC artifacts, input PCM, shared core, tolerance and coefficient
identities, successful application build/BSP/compiler dependencies, PS7 init
against the XSA member, and ELF symbols. It runs offline ABI/negative tests and
writes a plan and review-only Tcl template. `prepared_unverified_on_arm` does not
pass the development gate, execute evaluation or establish timing results.

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B `
  'D:\2610_MFCC\project\scripts\run_arm_reference.py' --prepare-only `
  --build-manifest 'D:\2610_MFCC\build\arm_platform\FINAL_APPS\build_manifest.json' `
  --run-id prepare_01
```

Outputs must remain under `D:\2610_MFCC\build\arm_platform`; an existing run
directory is never replaced. The concrete final build path is supplied by the
build orchestrator. Preparation reads evaluation PCM only for byte/hash and
shape checks; it does not run MFCC inference on evaluation inputs.

## Future board execution

Supply `--execute --phase all --build-manifest ... --run-id ...` plus observed
`--target-filter`, exact `--cable-serial`, `--uart-port`, and
`--acknowledge-board ZYBO_Z7_20`. The filter must match exactly one Cortex-A9 CPU0
and that CPU's JTAG cable serial. No first-device fallback exists. Discovery is
separate and read-only. No board was available when this runner was created;
generated XSCT commands are checked against installed 2024.2 command help but
remain unverified on physical hardware.

Hello starts the matching XSA's generated PS7 initialization, checks the reserved
4 KiB DDR test and captures the UART marker at 115200 8N1. Every MFCC job downloads
the same ELF and halts at the exported ready breakpoint. Addresses come from
`arm-none-eabi-nm`, and the trace layout comes from the target's ABI descriptor.
The host downloads PCM, reads the **entire** PCM buffer back and verifies SHA256
before writing RUN last. The firmware independently checks CRC32. It flushes
results before the result breakpoint. Frame IDs, starts, record shapes, finite
values, output checksum, status, FPSCR and clock/cache snapshots are retained.
UART and JTAG transfers are outside the timed interval.

ARM versus Python and ARM versus PC use the unchanged stage tolerances from
`verification/c/tolerances.json`; PC bit identity is an additional diagnostic.
All MFCC records are compared. Synthetic vectors trace all full frames; short
and empty inputs use `trace_frame=UINT32_MAX` to inspect preemphasis without a
frame. Real speech traces the first frame plus the earliest/worst MFCC difference
frames, bounded to at most five frames. These diagnostic traces identify the
first differing/failing stage **within selected frames**; they do not certify
every intermediate stage of untraced speech frames.

The development gate requires synthetic17 and one speech clip (534 frames).
Existing PC failures remain failures. A controlled evaluation can proceed only
when ARM/PC comparisons pass and the two known synthetic cases have exactly the
same Python failure stages and element violation masks as pinned PC outputs.
New failures or changed masks block evaluation. This deliberately conservative
rule may require diagnosis for legitimate cross-architecture rounding changes.
It never excuses an evaluation failure.

Timing uses fixed 3 warmups and 30 repetitions. After actual development gate
acceptance, numerically accepted development speech is measured and the evidence
is frozen before evaluation. Evaluation requires exactly 20 clips / 9501 frames;
each clip is timed only after its own numerical checks pass. The measured scope
is `mfcc_init`, per-sample MFCC processing, frame checks and output checksum.
CRC, input download, output copies, UART and cache operations are excluded.
Raw 64-bit ticks, timer frequency, overhead observation, min/median/p95/max and
clock/cache state are retained; overhead is not subtracted. The frequency is the
BSP nominal value corroborated by register snapshots, not a measured clock.
`--skip-timing` makes a separate frozen diagnostic experiment.

`--phase evaluate --development-run ...` accepts only actual development evidence
with identical frozen sources, inputs, compiler/build/ELF, tolerance, tools,
board selection and timing settings. It cannot use an offline preparation as a
development gate. Overall numeric acceptance stays false and exit code is 2
when the known synthetic failures remain, even if all speech clips pass.
