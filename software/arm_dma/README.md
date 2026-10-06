# Common ARM DMA/IRQ validation firmware

Both frozen numerical cores use these same sources, compiler options and buffer
policy. Select `--variant fixed` or `--variant fp32`; the core identity and
embedded full development reference are the only intended build differences.
The control/stream ABI is in `hardware/system_dma/REGISTER_CONTRACT.md`.

`mfcc_dma.c` is a portable transaction controller. The Xilinx adapter configures
Simple AXI DMA, the GIC and a one-shot SCU private timer, and uses actual WFI.
It checks the core identity before control writes. Each transfer prepares
dedicated cache-line-isolated buffers, arms receive first, starts the core and
transmit channel, then joins transmit IOC, receive IOC and core terminal DONE.
Empty channels are skipped. Errors preserve the first native fault, quiesce and
reset DMA with a bounded wait, then ABORT the core. Firmware never reuses output
storage after a failed job.

The Cortex-A9 TRM, section2.4.2, specifies IRQ wake from WFI regardless of CPSR.I.
The adapter masks IRQ, checks volatile completion flags, executes DSB/WFI, then
restores IRQ so pending handlers run. The private timer supplies a finite wake
deadline. Host tests validate transaction ordering and error handling; they do
not claim actual ISR or WFI execution. Those require board evidence.

`demo_main.c` publishes a48byte control,216byte status,152byte transport record,
168byte trial record and128byte layout. All addresses are taken from the actual
ELF. Input/result buffers are64byte aligned in the reserved DDR region below
0x02100000. The full reference is embedded as6942 records of24bytes.

One board session proceeds through READY/result breakpoints:

1. Mode0:512-sample/13-record smoke, embedded comparison of every record byte.
2. Mode1:85920-sample/534-frame/6942-record development clip; full embedded and
   host comparisons. The input is verified by JTAG readback and firmware CRC.
3. Mode2:3warmup plus30measured calls in the same ELF. All33 statistics and
   output arrays are saved. Numeric comparison and history copy follow each
   elapsed timestamp. A failure stops the sequence and prevents buffer reuse.

Timing starts after the read-only identity probe and before per-clip control,
cache maintenance and DMA setup. It ends after IRQ completion, status/count/
actual receive-length checks, interrupt cleanup and output invalidation.
Startup, CRC, JTAG/UART, comparison and history copy are excluded. WFI bracket
ticks include instruction/timestamp overhead and do not measure CPU utilization.
PL busy cycles include reset, serialization and stream backpressure.

Offline example:

```text
python scripts/build_arm_dma.py --variant fp32 --run-id host_new --host-tests
python verification/arm_dma/test_runner.py --output D:/2610_MFCC/build/board_validation/dma_board_20261005_01/runner_tests_new
python scripts/build_arm_dma.py --variant fp32 --run-id p_new --system-run f_new
python scripts/run_board_dma.py --variant fp32 --run-id prepared_new --arm-run p_new --system-run f_new --prepare-only
```

The system build must have successful configuration, actual DMA/PS simulation
and implementation gates before BSP/ELF preparation. The builder uses XSCT only
for offline BSP generation and never connects to hardware. Object-only syntax
checks using an earlier BSP are explicitly marked unverified platform checks.

The board runner defaults to preparation only. Execution additionally requires
`--execute`, explicit observed CPU0/FPGA filters, cable serial and
`--acknowledge-board ZYBO_Z7_20`. It resets/programs/downloads once per variant,
then resumes the same ELF. It never starts a server or writes flash. The shared
JTAG helpers remain unchanged. Fresh run directories prevent overwriting evidence.

Fixed results must match the published integer contract and recorded fixed C
outputs. The fixed float64 accuracy status remains `NOT_ACCEPTED`. FP32 must
match frozen continuous RTL bits and the existing Python/PC tolerances. The five
known FP32 synthetic failures remain unresolved. Only the named development
audio is exercised; no evaluation audio is read.
