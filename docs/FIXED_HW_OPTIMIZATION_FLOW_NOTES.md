# Fixed hardware optimization: build and board flow

2026-10-05 KST. This note records an offline audit and candidate-gate changes. It is not an optimization performance result. Current user authorization supersedes the older division of work and board hold statements in AGENTS.md and C_FIXED_HANDOFF.md. No Vivado, XSCT, UART, or board operation was performed while preparing this note.

## Preserved baseline and measurement boundary

The comparison baseline is system `build/system_dma/i10`, ARM `build/arm_dma/i01`, board `build/board_validation/dma_board_20261005_01/fixed_03`. FP32 `f06/p01/fp32_01` remains unchanged.

| Evidence | Fixed baseline | FP32 comparison |
|---|---:|---:|
| Whole clip median, ms | 124.82371738011219 | 275.292867 |
| PL BUSY median, ms | 123.7802812378028 | 274.249463 |
| Post-route setup / hold slack, ns | +0.447 / +0.010 | +0.846 / +0.007 |
| LUT / FF / BRAM tiles / DSP | 9325 / 6843 / 15.5 / 40 | 8302 / 12266 / 11 / 26 |

Both measurements use 85,920 PCM16 samples, 534 frames, 6,942 output records, 171,840 input bytes and 166,608 output bytes. Record size is 24 bytes. The same ELF performs 3 warmups plus 30 measured clips; every output is retained and checked. The board runner has no `--warmups` or `--repeats` options because firmware fixes these values.

The measured interval begins after input preparation and identity probe, before per-clip reset/configuration, cache maintenance and DMA start. It ends after completion IRQ join, counter/status validation, cleanup and output invalidation. JTAG/UART, startup, host comparison and history copies are outside. PL BUSY includes stalls, serialization and reset/recovery. Neither BUSY nor WFI bracket is a pure compute time or CPU-utilization measure. Whole-clip/534 is an amortized average, not first-result latency or measured frame initiation interval.

| Fixed artifact | SHA-256 |
|---|---|
| bit | `9f8355908341f35d508f8b6932f35b2c52bfcedeecdcfd9fddaa128f25e1346e` |
| XSA | `8eaebc0c0351dcc1c7b1b7fe17c182cd3e6e960f956d7d1e3575e167601c8331` |
| ELF | `3c3eb77d4aa35598d80805092579ae03716ec53b8867f52b057a60cc9b17d379` |
| development result records | `888ee289cc44f03602934002e9c4e2793cd3c4bc6349547c247bbda934eba6a6` |
| PS init | `126a7277430f44f331d35eafcfa15fc74826a8dcf979ef20f0c30781ca002124` |

Fixed float64 acceptance remains **NOT_ACCEPTED**: development maximum absolute error 0.05517150245522062 and RMSE about 0.006260244715270819. Bit-exact structural optimization does not change that classification. Baseline board identity records nominal CPU/timer 666666687/333333343 Hz and FCLK reconstructed from configuration as 99999999 Hz; the physical oscillator was not measured.

## Candidate opt-in and immutable provenance

`scripts/run_dma_system.py` and `scripts/run_board_dma.py` now accept `--fixed-evidence PATH`, where PATH is the candidate full RTL run directory or its `run_manifest.json`. With no option, the original pinned fixed source gate remains. The option is refused for FP32.

The candidate must have completed the unchanged 24-case, 616-frame corpus with recorded Vivado exit code 0, matching model/contract/publication/case identities, exact oracle-vector hashes, PASS protocol, zero mismatches, every stage's full transaction count, partial PCM reset, in-flight FFT reset, and observed stalls. The complete fixed `.sv`/`.mem` source set and bytes must match its validated snapshot. Original dense Mel coefficients and FFT twiddles remain unchanged; the supported extra ROM is `hardware/fixed/power_mel/mel_sparse_fw16.mem`. Source additions or omissions cannot silently bypass validation. Fixed numeric acceptance must remain NOT_ACCEPTED.

The system copies the evidence manifest, protocol, artifact index, all recorded sources, and full-corpus oracle vectors into `provenance/fixed_evidence`. Each later launch/restart verifies that copy and the system source snapshot. The board runner checks the explicitly supplied evidence against this pinned evidence and the system's complete fixed source set. Candidate evidence can therefore be consumed after working-tree RTL moves to the next candidate. Board measurements never depend on whatever RTL happens to be current at measurement time.

System `tool_results` records the actual Vivado process exit code, success-sentinel count and console-log hash for each launched stage. The PS VIP compile already records its actual exit code separately. Extra ROM support validates unique fixed memory basenames plus exactly the six baseline FP32 ROM names, rather than accepting an arbitrary total memory count.

The DMA top's baseline parameter default is still `mel_fw16.mem`. Candidate setup therefore writes an immutable `fixed_mel_init_file.txt` and manifest selection and applies the corresponding `CONFIG.MEL_INIT_FILE` on the packaged accelerator BD instance. `create_dma_system.tcl` verifies the exact eight baseline or nine sparse memories in both package file groups. The configuration and board gates check the actual generated accelerator parameter, preventing the unchanged outer wrapper default from accidentally overriding the candidate's compact ROM. The DMA top bytes and baseline TOP_SHA remain intact. Offline `rom01` checks accept original i10/f06 and the candidate binding, reject a dense override and a changed selection file, and verify Tcl script completeness without launching Vivado.

Offline checks: `build/fixed_hw_opt_flow/guard02/results.json` passed 13 candidate-gate cases, including rejection of incomplete/smoke/failed-tool/changed-model evidence, forged vector/source indexes, original ROM changes and changed numeric acceptance. These are explicitly synthetic gate fixtures, not RTL execution evidence. `build/fixed_hw_opt_flow/runner01/results.json` passed the existing 437 runner checks without board access.

## Safe run order and commands

Use a short, fresh system ID: the absolute system run path must be at most 40 characters because nested Windows IP paths have a length limit. Failed runs are preserved and require a new ID. `--resume` advances only an intact successful prepared/BD/simulated run. Example IDs below are placeholders, not completed runs.

```powershell
$py = 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe'
$ev = 'D:\2610_MFCC\build\fixed_full_rtl\CANDIDATE_FULL\run_manifest.json'
Set-Location 'D:\2610_MFCC\project'
& $py -B scripts/run_dma_system.py --variant fixed --run-id os01 --fixed-evidence $ev --mode prepare
& $py -B scripts/run_dma_system.py --variant fixed --run-id os01 --fixed-evidence $ev --resume --mode sim
& $py -B scripts/run_dma_system.py --variant fixed --run-id os01 --fixed-evidence $ev --resume --mode bitstream
& $py -B scripts/build_arm_dma.py --variant fixed --run-id as01 --system-run os01
& $py -B scripts/run_board_dma.py --variant fixed --run-id sparse_pre01 --system-run os01 --arm-run as01 --fixed-evidence $ev --prepare-only --output-root 'D:\2610_MFCC\build\board_validation\fixed_opt_20261005_01'
```

Before actual board execution, confirm exclusive use and the currently observed cable/CPU/FPGA selection. Baseline selections below are historical verified values, not a new connection observation. The runner refuses ambiguous/wrong selection, never starts a hardware server, and performs one reset/program/download followed by same-ELF smoke, full development, and 3+30 timing stages.

```powershell
& $py -B scripts/run_board_dma.py --variant fixed --run-id sparse_board01 --system-run os01 --arm-run as01 --fixed-evidence $ev --execute --output-root 'D:\2610_MFCC\build\board_validation\fixed_opt_20261005_01' --acknowledge-board ZYBO_Z7_20 --cable-serial '210351B40030A' --target-filter 'name == "ARM Cortex-A9 MPCore #0" && jtag_cable_serial == "210351B40030A"' --fpga-target-filter 'name == "xc7z020" && jtag_cable_serial == "210351B40030A"' --uart-port COM7
```

`--smoke-only` limits execution to one frame; `--skip-timing` performs smoke plus full development validation. Neither is the requested final 3+30 comparison. Vivado/Vitis 2024.2 launcher paths and the project Python environment were present during audit. `Get-Process` showed only the existing hw_server PID 19020 among relevant processes; that alone does not prove board exclusivity. CIM command-line inspection returned access denied. Root-session chat-state inspection and a fresh process check remain necessary before tool/board use.

## PS simulation library isolation

`build/system_dma/cache_incident01/incident.json` records an earlier shared compiled-cache modification, no installed HDL modification, and an automatic-review-blocked restoration. Do not modify or attempt to restore `C:/Xilinx`.

Reuse `verification/system_dma/ps_vip_model.py` and `hardware/system_dma/simulate_dma_system.tcl`: stage the reviewed vendor source into the run, change only the 22 four-port write-arbiter request assignments, and place the compiled library in `RUN/simulation_model/library`. Both explicit `xvlog --work logical=physical` and `--initfile RUN/simulation_model/local_xsim.ini` are required. An options file preserves the equals sign through Windows batch argument parsing. Vivado compile **and** elaboration also receive that local initfile. Compilation verifies the expected local `.sdb`, source/diff identities, and unchanged installed-cache hashes before and after. The copy is simulation-only and is excluded from synthesis/implementation. State explicitly that actual DMA/PS integration simulation uses this scheduling-corrected vendor model.

The sparse `os01` attempt exposed an additional library-isolation gap: the generated design compiler targeted `cmpy_v6_0_25` through the public installation cache, producing a denied-write compile error (actual simulation stage exit 1). `os01` remains failed and unchanged. No elevation or cache restoration was attempted. A root read-only audit found no public-cache file timestamp after that run started; this is narrower evidence than a complete pre/post hash comparison.

Fresh runs use `verification/system_dma/sim_library_isolation.py` and a scripts-only preparation phase before any design compiler runs. The exported Verilog/VHDL PRJs identify every compilation target. Each vendor target is cloned read-only into a fresh run-local physical directory; `xil_defaultlib` gets an empty local directory. A separate `all_libraries_xsim.ini` maps those targets and preserves the already-reviewed patched PS library exactly. The original frozen PS-only initfile/metadata remain unchanged. `xvlog`, `xvhdl`, and `xelab` all receive the all-library mapping. Tcl checks regenerated PRJ targets are all mapped beneath the current run before actual compilation. Python checks mapping identities, explicit compiler options, and before/after public-cache fingerprints. The sparse configuration currently has 13 target libraries, including 12 vendor cache clones totaling about 63.5 MB. A root full public-cache hash inventory supplements this per-target guard.

`--fixed-source-root` may select an external validated fixed source snapshot on a new `--fixed-evidence` run. Its complete `.sv`/`.mem` set and hashes must equal the full-corpus evidence. This allows a fresh sparse attempt after live RTL has advanced to the pipelined candidate without restoring or changing the live files. It is not accepted without full evidence, and cannot alter an existing resumed snapshot.

Further evidence remains in `docs/DMA_SYSTEM_BOARD_RESULTS.md`, `docs/BOARD_VALIDATION_RESULTS.md`, original system/ARM/board manifests, and immutable raw data. This note does not replace those reports or claim a new board result.

## Reproducible result tables

`scripts/summarize_fixed_optimization.py` reads the original baseline by default and accepts explicit `--sparse-{system,arm,board,full}` and `--pipelined-{system,arm,board,full}` paths. Omitted/missing runs remain NOT_RUN; no future run ID is assumed to have succeeded. An optional `--run-spec` JSON list with `stage`, `system`, `arm`, `board`, and `full` keys supports additional candidates. Output must be a new build directory.

The script produces `comparison.csv`, `source_hashes.csv`, `board_trials.csv`, `stage_cases.csv`, `frame_latencies.csv`, `stage_interval_statistics.csv`, `summary.json`, and a separate `report_fragment.md`; it never edits the root optimization report. It verifies indexed input artifacts, independently decodes all raw timing trials, compares all 33 output histories to the preserved baseline bytes, recomputes median/linear p95, and binds system/ARM/board/full source identities. Inconsistent evidence returns exit 1 and excludes that stage's trial/event rows from accepted outputs.

Stage events are accepted transactions. First-frame admission-to-accepted-MFCC latency includes TB stalls. Admission intervals are reported for all consecutive frames and, separately, after excluding the first four frame IDs by default (`--ii-skip-frames`). Those tail intervals are observations under the TB's stimulus/backpressure, not a claim of unstalled peak throughput. The older baseline lacks the new stage CSVs, so its instrumentation status is NOT_RUN unless a separate measured baseline full run is supplied.

Recollect the completed candidates into a fresh summary directory:

```powershell
& $py -B scripts/summarize_fixed_optimization.py --output 'D:\2610_MFCC\build\fixed_optimization\comparison_reproduce01' --sparse-system 'D:\2610_MFCC\build\system_dma\os02' --sparse-arm 'D:\2610_MFCC\build\arm_dma\as02' --sparse-board 'D:\2610_MFCC\build\board_validation\fixed_opt_20261005_01\sparse_board01' --sparse-full 'D:\2610_MFCC\build\fixed_full_rtl\opt_sparse_full01' --pipelined-system 'D:\2610_MFCC\build\system_dma\op02' --pipelined-arm 'D:\2610_MFCC\build\arm_dma\ap02' --pipelined-board 'D:\2610_MFCC\build\board_validation\fixed_opt_20261005_01\pipeline_board01' --pipelined-full 'D:\2610_MFCC\build\fixed_full_rtl\opt_pipe_full01'
```

`build/fixed_hw_opt_flow/summary_tests01/results.json` records seven offline checks, including rejection of reindexed corruption in a middle timing trial or history output. `summary_dev02` is an intermediate baseline-plus-sparse-simulation collection; it contains no new sparse board measurements.

## Build-local recovery of blocked Windows script dispatch

The `os02` implementation parent reached `launch_runs` but Windows CScript could not load its script engine (Access denied), so no synthesis worker initially ran. The parent console and generated dispatch files are preserved. `scripts/run_vivado_generated_worker.py` executes the exact single generated Vivado argv and Tcl from each queued worker directory directly, with the real tool exit code, copied helper/recipe bytes, hashes, console output, and begin/end/error markers in `worker_recovery`. It changes no installed script or registry setting and needs no elevation.

The helper refuses parent stop markers and previously started/completed workers. A narrowly scoped option accepts only an empty blocked-dispatch begin placeholder with no worker log. Top synthesis requires all eight OOC dependencies to have successful completion markers and nonempty checkpoints; implementation requires completed top synthesis. Exceptions after launch wait for the actual child before writing a terminal error. Offline fault/ordering checks are recorded in `build/fixed_hw_opt_flow/worker_guards01/result.json`.

`scripts/recover_dma_vivado_workers.py` requires fresh OOC workers, supports a maximum of two at a time, and stops submitting workers after a failure. Guarded top synthesis follows. It then waits for the parent's route recipe and, after routing, requires the parent's fresh bitstream recipe/phase marker and nonnegative timing results. The ordinary parent still checks the selected hierarchy, timing, actual bitstream/XSA and artifact provenance. Its original dispatch failure and the real direct-worker outcome are separate evidence.

Observed Vivado continuation retains old generic `.vivado.begin/.end` and queue timestamps; native `ISEWrap.js` does not clear them. The narrow `--previous-attempt` path authenticates the prior successful route receipt, recipe, console and worker log, confirms the old PID has exited (accounting for Windows PID reuse), preserves old markers/logs, and accepts only a newly generated `write_bitstream` phase with its fresh begin marker. Actual exit zero plus `.write_bitstream.end` without its error marker is required. It does not delete native retained generic end markers merely to fit the initial assumption. Safe preflight rejections and the inspected native behavior are recorded in the run's recovery incident.

The generated accelerator synthesis recipe explicitly sets `ip_output_repo` to `RUN/design/vivado_project/zybo_dma.cache/ip`; XPR `IPOutputRepo` is `$PCACHEDIR/ip`. Its “Added synthesis output to IP cache” message therefore describes the run-local synthesis cache, separate from the installed XSim compiled cache guarded above. Final build results and measured board outcomes belong in the root optimization report.

## Actual optimization build IDs and reproduction

The earlier `os01` command examples above describe the original attempted run, **not a completed system**. `os01` failed during simulation compilation because of the public-cache target mapping; preserve it. `op01` is a preserved prepared-only snapshot made before the full library-isolation fix. Neither is an accepted system/ARM/board result.

The replacement sparse system `os02` and matching ARM build `as02` completed successfully. `os02` used `build/fixed_full_rtl/opt_sparse_full01/run_manifest.json` and its `source` directory, independent of later live RTL. Its parent and all recovered workers exited zero; all 69 recovery files were present in the final artifact index and verified unchanged. Post-route resources are 9,378 LUTs, 6,840 FFs, 11.5 BRAM tiles and 40 DSPs, with setup/hold slack +0.539/+0.020 ns at 100 MHz. `as02` is an offline executable build, with no board execution implied.

The final pipelined system `op02` and matching ARM build `ap02` completed successfully. `op02` uses `build/fixed_full_rtl/opt_pipe_full01/run_manifest.json` and its immutable `source` directory. Its actual DMA/PS simulation passed 6 clips / 78 records with zero mismatches in 40,519 cycles and its public compilation-library cache guard passed. The original parent and all eleven recovered workers exited zero, with an observed maximum of two direct workers at once; all 77 indexed recovery files were verified unchanged. Post-route resources are 9,768 LUTs, 7,193 FFs, 11.5 BRAM tiles and 47 DSPs, with setup/hold slack +0.159/+0.017 ns at 100 MHz. The pipeline therefore spends additional LUT/FF/DSP resources compared with the sparse-only step while retaining the four-BRAM reduction; board timing must be measured separately.

| Build | Bitstream SHA-256 | XSA SHA-256 | ARM ELF SHA-256 |
| --- | --- | --- | --- |
| `os02` / `as02` | `5bfdff227b21160394ae7ff95cf767fff7d08ff58c463c33e71e293afdf4f622` | `5eba393b07d4a0928c39a64f5e3f034f8b72fc5d82af5ca902f6b82ced8653a9` | `76f535e3417e19b39d02170658972e1957e32bb43faa54df7fc145dc0ec28c16` |
| `op02` / `ap02` | `d9fcde024f0b4ae5d279abb1b45d031d06511d876dc8ec4105866f51516cc079` | `7ef9a2f05cb033055cf43eb4152aa8d9f70ea028fdb911879cafeca6eb9bbca8` | `6bf608c92ab8e3b8b2273b6d424c3dfe163219c42608de65bf239f7ba186de18` |

The parent task's final read-only installed-XSim cache comparison passed: all 4,311 files / 1,113,682,991 bytes retained identical file names and SHA-256 values, with no additions, removals or changes relative to the pre-`os02` inventory. Evidence: `build/fixed_optimization/cache_guard01/public_cache_comparison.json`. Both ARM builds are `BUILT_NOT_BOARD_RUN`; this build subtask accessed no physical board. The Vivado/Vitis slot was explicitly released after `ap02` completion and a read-only process check found no remaining Vivado, XSCT, XSDB or ARM compiler process. Numerical acceptance remains `NOT_ACCEPTED`; bit-exact optimization validation does not change that status.

To reproduce either stage, use fresh run IDs: the runners intentionally refuse to overwrite completed or failed evidence. The commands below are the actual argument pattern, with new example output IDs. Run one stage at a time under an exclusive Vivado/Vitis slot.

```powershell
$py = 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe'
$system = 'ns01' # Choose an unused short ID; the complete system path must be <=40 characters.
$arm = 'na01'
$evidence = 'D:\2610_MFCC\build\fixed_full_rtl\opt_sparse_full01'
# For the final pipeline, use new IDs and opt_pipe_full01 instead.
& $py -B scripts/run_dma_system.py --variant fixed --run-id $system --fixed-evidence "$evidence\run_manifest.json" --fixed-source-root "$evidence\source" --mode all
```

If the observed CScript engine failure blocks the generated implementation workers, keep the original parent running. In a second terminal, only after all eight OOC `rundef.js`/queue files exist and the implementation console records the original dispatch failure, run:

```powershell
& $py -B scripts/recover_dma_vivado_workers.py --system-run $system --attempt-prefix recovery01
```

This automatic path requires fresh OOC workers; a partially recovered run needs individual reviewed helper commands and cannot silently reuse completed workers. `os02` used individually recorded worker invocations while the launcher was being developed; `op02` uses the reviewed automatic two-worker dispatcher. Every invoked helper is copied into its receipt. Continue to ARM only after the original system runner exits zero and `run_manifest.json` says `complete` with simulation, implementation and configuration PASS:

```powershell
& $py -B scripts/build_arm_dma.py --variant fixed --run-id $arm --system-run $system
```

The board runner's `--prepare-only` performs offline identity validation and does not program or access hardware. Physical board execution is a separate parent-task step after the complete tool slot is released. Use `--fixed-evidence` with the matching full-corpus candidate in either board preparation or execution; the original dense baseline gate remains unchanged.

## Completed parent-owned physical board runs

After tool-slot release, both offline preparations and actual board runs completed with exit 0. The parent confirmed the other board chat was idle, only the existing hardware server remained, and COM7 was present; the runner then verified the exact cable/CPU/FPGA targets before programming. Runs are under `build/board_validation/fixed_opt_20261005_01`:

| Run | Bound system / ARM | Actual whole-clip median, ms | PL BUSY median, ms | Result |
|---|---|---:|---:|---|
| `sparse_pre01` | `os02` / `as02` | — | — | PREPARED_NOT_BOARD_RUN |
| `pipeline_pre01` | `op02` / `ap02` | — | — | PREPARED_NOT_BOARD_RUN |
| `sparse_board01` | `os02` / `as02` | 31.878368 | 30.834770 | BOARD_DEVELOPMENT_BIT_EXACT_PASS |
| `pipeline_board01` | `op02` / `ap02` | 16.343058 | 15.299840 | BOARD_DEVELOPMENT_BIT_EXACT_PASS |

Each actual run used the same 85,920 PCM samples / 534 frames / 6,942 records, nominal PL 100 MHz and three warmups plus 30 measured repeats. Every full timing output history matched exactly; smoke/full-development phases and pre/post clock readbacks also passed. The exact executed argv, target inventory, clock snapshots, device status, UART, raw trials and outputs remain in each indexed run. Temporary JTAG programming/download was used without flash or SD writes.

`build/fixed_optimization/comparison01` is the finalized three-stage collection (exit 0, no evidence errors); `plots01` contains the actual figures and exact CSV data. Independent raw-trial/record/source review is in `independent_board_audit01`. The main interpretation and timing-boundary caveats are in `docs/FIXED_HW_OPTIMIZATION_RESULTS.md`; fixed numerical acceptance remains NOT_ACCEPTED.
