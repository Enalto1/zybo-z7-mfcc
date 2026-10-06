# ZYBO Z7-20 실제 보드 검증·측정 결과

작성일: 2026-10-05 KST. `float_03`(09:36), `fixed_01`(09:38), `accel_03`(09:59)의 실제 보드 완료 결과와 독립 감사를 기록한다.

**실제 ZYBO Z7-20에서 Hello/전용 DDR 4 KiB 검사, 개발 음성 float32 C·full fixed C v2·fixed PS/PL의 수치 검증과 시간 측정을 완료했다.** 534프레임/6,942 MFCC의 float32 결과는 기존 Python 및 PC C 허용치를 통과했고, fixed C와 PS/PL은 동결 정수 모델 및 서로 비트 일치했다. 각 30회 클립 처리시간 중앙값은 float32 C **127.434356 ms**, fixed C **1,529.340455 ms**, fixed PS/PL **123.965773 ms**다. 측정 구간과 초기화 정책은 경로별로 다르며 아래에 명시한다. 결과는 지정한 개발 음성 1개에 한정하고, fixed의 float64 정확도 **NOT_ACCEPTED** 및 FP32 PL 보드 통합 대기 상태를 유지한다.

## 1. 실행 근거와 현재 범위

실행 루트는 `D:/2610_MFCC/build/board_validation/board_20261005_01`이다. 이후 표의 경로는 이 루트 기준이다. 과거 실패·복구 실행과 원시 파일은 덮어쓰지 않았다.

| 항목 | 확인된 상태 | 근거 |
|---|---|---|
| 실제 연결 | Digilent Zybo Z7 케이블 `210351B40030A`, FPGA `xc7z020`, CPU0/CPU1, UART `COM7` 확인 | `jtag_probe.log`, `windows_devices.json` |
| PS 초기화·Hello | 실제 실행 및 UART `ARM_HELLO_DDR_V1` 관측 | `float_03/hello/prepare.log`, UART 원시 로그 |
| 제한 DDR 검사 | ELF 심볼로 지정된 전용 4,096 bytes 통과 | `float_03/hello/validation.json` |
| ARM float32 C | 개발 음성 534프레임/6,942계수 및 집중 trace 4개 통과 | `float_03/speech_gate.json`, 개발 음성 `validation.json` |
| ARM float32 시간 | 3회 워밍업 + 30회 실측 완료 | `float_03/timing_summary.json`, 원시 `timing_records.bin` |
| ARM full fixed C v2 | 6,942 정수 출력 + 4,272 metadata 불일치 0; 3/30 실측 완료 | `fixed_01/run_manifest.json`, `validate/comparison.json`, `timing_summary.json` |
| 고정소수점 PS/PL | s06/a07 smoke 13개 + 전체 6,942개 값/ABI metadata 비트 일치; 초기 3회 제외 + 30회 실측 완료 | `accel_03/run_manifest.json`, `timing_summary.json`, `accel_analysis/diagnostics.json` |
| FP32 PS/PL | 보드 통합·ELF 준비 대기, 보드 실행/측정 없음 | RTL 음성 시뮬레이션 통과와 구분 |

대표 완료 근거는 [float_03 manifest](D:/2610_MFCC/build/board_validation/board_20261005_01/float_03/run_manifest.json)다. 상태는 `passed_development_speech_only`, `speech_numerical_passed=true`, `timing_measured=true`다. **전체 개발 gate는 false**, 평가 자격도 false이며 평가 음성은 실행하지 않았다. `numeric_acceptance_passed=null`은 전체 시험군의 수치 합격을 선언하지 않았음을 뜻한다.

이전 문서의 보드 실행 보류 문구는 당시 상태다. 이번에는 사용자가 실제 연결 확인 후 가역적인 JTAG 초기화·DDR ELF 실행을 지시했다. Flash/SD 기록이나 영구 부팅 설정 변경은 하지 않았다. 논문·발표 파일, 동결된 알고리즘/입력/계수/허용치는 수정하지 않았다.

## 2. 입력·명세·동결 식별자

| 항목 | 값 |
|---|---|
| 음성 | LibriSpeech `8463-294828-0037` |
| WAV | `D:/2610_MFCC/data/librispeech/wav_pcm16/8463-294828-0037.wav` |
| PCM | `D:/2610_MFCC/build/python_reference/reproduce_01/development/8463-294828-0037/input_s16le.pcm` |
| PCM SHA-256 | `026cae2934935a27911f88d7d6e0ddcc678ad433fb50fe764233a57e4bbcde32` |
| 형식·개수 | mono 16 kHz signed PCM16 LE, 85,920 samples, 5.37 s |
| framing | 512 samples, hop 160, 534 frames, 13 coefficients/frame; 기존 전처리·EOF 정책 유지 |
| MFCC 최종 허용치 | `abs(ARM-reference) <= 1e-3 + 1e-5*abs(reference)` |
| 허용치 파일 SHA-256 | `64ad3ab3a0ae050562b9ccfab7b3c8e7fcc90eb527eb6811cfcbbd4c07a9d2b6` |
| 명세 | `docs/MFCC_SPEC.md`; 작성 시 SHA-256 `6e5cf78bf202a923fa397b94b909711ab5d7a84808ec7ed3984942f2198b12a0` |
| fixed v2 계약 | `build/fixed_contract/v2_pcm16_mfcc40_20261004_r2` |
| fixed 계약 SHA-256 | `283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e` |

실행 전 Python 기준 1,321개, PC C 기준 732개 artifact의 동결 무결성을 확인했다. 38개 입력의 준비 검사는 보드에서 38개를 실행했다는 뜻이 아니다. 실제 보드 수치 대상은 위 개발 음성 1개다. float32 경로에서는 PCM을 JTAG 다운로드 후 전체 다시 읽어 SHA-256을 확인했고, ARM에서도 CRC32 `924516957`을 대조했다. fixed C 경로는 ELF에 내장된 PCM과 대상 DDR readback의 hash를 확인했다.

기존 `fullscale_alternating`, `tone_bin32_1000hz` 합성 입력의 PC 수치 실패는 그대로 유지하며 이번 보드 상태는 **not_retested**다. 합성 17개 및 평가 20개/9,501프레임을 이번에 통과한 것으로 표현하지 않는다. fixed의 float64 대비 정확도 상태 **NOT_ACCEPTED**도 그대로 유지한다. 이번 ARM fixed 정수 비트 일치 통과는 이 정확도 판정과 별개다.

## 3. ARM float32 실제 수치 결과

출력 레코드의 바이트 수·프레임 ID·시작 sample·순서·유한성·checksum을 먼저 확인했다. 모든 534프레임/6,942계수에 대한 비교 결과는 다음과 같다.

| 비교 | 최대 절대오차 | RMSE | 허용치 위반 |
|---|---:|---:|---:|
| ARM ↔ PC float32 C | `2.1457672119140625e-6` | `3.848014002643823e-8` | 0 / 6,942 |
| ARM ↔ Python float64 | `1.928850116073022e-5` | `3.0616948574242828e-6` | 0 / 6,942 |

ARM↔PC는 6,919계수가 비트 동일하고, 8프레임에 걸친 23계수만 비트 차이가 있다. 최대오차 위치는 PC 대비 frame 439/C2, Python 대비 frame 385/C1이다. 계수별 최대오차/RMSE 및 23개 차이의 원시 비트는 [출력 진단 JSON](D:/2610_MFCC/build/board_validation/board_20261005_01/float_analysis/output_diagnostics.json)에 보존했다. 허용치 통과와 전체 비트 동일성을 구분한다.

집중 trace는 **frame 0, 17, 385, 439**다. 전체 입력 pre-emphasis와 선택 프레임의 framing/window/FFT/power/Mel/log/DCT/energy/MFCC가 기존 단계별 허용치로 Python·PC 모두 통과했다. frame 0/385의 trace는 PC와 비트 동일하며, frame 17/439에서 PC와 최초로 차이가 나는 단계는 `log_mel`이다. 이를 모든 프레임 중간 단계의 전수 비교로 확대하지 않는다. 각 trace 실행에서도 전체 534프레임 출력을 회수했고 최초 validation 출력과 바이트 동일함을 확인했다.

`float_03` 전체 출력 `results.bin`은 32,040 bytes이며 SHA-256은 `8e97be82841c289b57930a8965e2872a8477924bd8162b0f540fdfd17509f06b`다. 이전 `float_02_trace_recovery/results.bin`과도 전체 레코드가 바이트 동일하다.

## 4. ARM float32 실측 조건과 시간

플랫폼은 PS-only `build/arm_platform/reproduce_01_platform`, 실행 파일은 `reproduce_01_apps`의 기존 ELF다. Cortex-A9 CPU0 standalone에서 실행하며, 각 ELF 전에는 같은 케이블의 CPU1을 정지·PC 조회로 확인한다. 실행 도중 CPU1을 작업에 사용하지 않는다.

| 조건 | 관측·설정 |
|---|---|
| 도구·컴파일러 | Vivado/Vitis/XSCT 2024.2, ARM GCC 13.3.0, Newlib 4.4.0 |
| CPU BSP nominal | 666,666,687 Hz |
| 클록 레지스터로 재구성 | 설정된 PS 입력 33,333,333 Hz × PLL 40 / divisor 2 = 약 666,666,660 Hz |
| global timer | BSP nominal 333,333,343 Hz, CPU/2; control `1`, prescaler 0 |
| cache·MMU | SCTLR `0x08c5187d`: MMU/I-cache/D-cache enabled; L2 control `1` |
| CPSR | `0xdf` 기록; IRQ/FIQ masked |
| FPSCR | before/active `0x00000000`, after `0x80000010`; rounding nearest, FZ/DN 비트 0 유지 |
| 측정 반복 | 동일 timing ELF 안에서 clip별 상태 초기화, warmups 3 + measured repeats 30 |
| 타이머 읽기 overhead | 25 ticks 별도 관측; 측정값에서 차감하지 않음 |

이 주파수는 실제 읽은 PLL/분주 레지스터와 동일 XSA/BSP 설정의 일치를 확인한 값이다. **외부 계측기로 물리 발진 주파수를 측정한 값이 아니다.** float 경로에는 PL MFCC 계산이 없으므로 PL 계산시간을 제시하지 않는다.

컴파일 옵션은 `-mcpu=cortex-a9 -mfpu=vfpv3 -mfloat-abi=hard -std=c11 -O2 -g3 -fno-fast-math -ffp-contract=off -fexcess-precision=standard -fno-tree-vectorize -Wall -Wextra -Werror -Wno-error=enum-compare`다. 기존 코어와 계수는 유지했다. 연결된 Newlib `logf` 내부에 double 연산이 있다는 기존 ELF 감사 결과도 유지하므로 실행 파일 내부 전체를 순수 binary32 연산이라고 주장하지 않는다.

| 개발 음성 1클립 처리시간 | 실측 값 |
|---|---:|
| min | 127.403198 ms |
| median | **127.434356 ms** |
| p95 | 127.547921 ms |
| max | 127.550540 ms |
| 중앙값 / 534 frames | 0.238641 ms/frame |
| 중앙값 환산 처리량 | 4,190.393 frames/s |
| 5.37 s 음성 / 중앙값 | 42.139배 실시간 처리율 |

`ms/frame`은 클립 시간의 프레임 수 나눗값이며 개별 프레임 latency 실측이 아니다. 실시간 처리율은 다른 구현 대비 speedup이 아니다.

측정 구간은 **`mfcc_init` + 전체 입력 push + frame ID/count 검사 + 출력 계수 checksum 소비**다. ELF reset/startup, JTAG 다운로드/회수, UART, PCM CRC, 출력/trace/pre-emphasis 별도 복사, cache maintenance는 제외한다. 모든 반복의 frame count와 checksum을 사전 검증 결과에 대조했다. fixed C의 결과 저장 포함·checksum 제외 구간, PS/PL의 전송·polling 포함 구간과 비교할 때 이 차이를 명시해야 한다. UART/JTAG 시간을 계산시간으로 합치지 않는다.

주요 파일 SHA-256:

| 파일 | SHA-256 |
|---|---|
| `hello_ddr.elf` | `eda3d6b335cbce0e8b6a69251b4f376257a9c812e91ce3d5c809aae31b3b17a0` |
| `mfcc_arm.elf` | `dc3430018c58142f6a1489a78ff078a5af824a56381e96159835ad703565aa81` |
| PS-only XSA | `8a721765b6b460fb899a98014105836b92fd76f8081e588dc8637ee078ecc1e6` |
| `ps7_init.tcl` | `49af037f2b6bbe883c2499969da844119dc1cb85a26f404be7ca85d87c54284e` |
| float build manifest | `bdf79fbdb055c90bf5e21f7f68c91207513585126ef83f7b5dbce601ca8b2dbb` |
| `float_03` conditions | `306c3d8301214cf2c46c272b8bb8d9b8d3b0b81d0612f09bbc189546f2eaa5c4` |

컴파일러·libm·BSP·도구 hash와 전체 소스 hash는 `float_03/build_identity.json`, `conditions.json`에 있으며, 원시 출력/상태/UART/Tcl 로그/tick 및 각 파일 hash는 해당 run 아래에 보존했다.

## 5. 초기 실패·복구와 실행기 수정

실패 기록은 성공 run으로 대체하지 않고 각각 보존한다.

| 실행 | 관측 결과·처리 |
|---|---|
| `float_01` | Hello/DDR 통과 후 다음 ELF 준비의 `stop`이 `Already stopped`로 실패. XSCT launcher는 Tcl 오류에도 exit 0을 반환했다. 정확히 이 오류만 허용하도록 수정하고 Tcl catch·명시적 성공 sentinel을 추가했다. |
| `float_02` | 첫 MFCC 출력은 구조 검사를 통과했으나 PC/Python 모두 6,942계수 전부 허용치 실패. PC 대비 max abs 8.929977416992188, RMSE 4.469058675569825. timing 미실행. frame0 trace 준비 후 새 XSCT 연결의 CPU 조회가 0개여서 실행 중단. |
| `float_02_trace_recovery` | 이미 준비된 frame0 trace를 별도 run으로 복구. frame0 모든 중간 단계가 PC와 비트 동일하고, 전체 출력은 PC/Python 허용치 통과. timing 없음. |
| `float_03` | 모든 ELF 전 system reset + PS 초기화 적용 후 Hello, 전체 음성, 집중 trace, timing 모두 위 한정 범위로 통과. |

CPU 조회는 결과가 0개일 때만 100 ms 간격으로 최대 5초 재시도한다. 여러 대상이나 시리얼/CPU 불일치는 실패로 남긴다. READY 제어 쓰기 및 결과 회수 전에는 stale할 수 있는 target-property 표시 대신 실제 `rrd -nvlist pc`를 의도한 breakpoint 주소와 비교한다.

ELF 전환 시 실행 상태/캐시 초기화 문제를 의심하여, 각 새 ELF 전에 `rst -system -stop` → 동일 케이블 CPU1 정지·PC 확인 → CPU0 재선택·PC 확인 → 해당 XSA의 `ps7_init`/`ps7_post_config` → ELF 다운로드를 적용했다. READY와 execute 사이에는 reset하지 않는다. `float_03`의 각 준비 로그에서 reset 직후 L2 control `0`, CPU0 PC `ffffff28`, CPU1 PC `ffffff34`를 관측했다.

설치된 BSP startup은 L1 활성화 뒤 L2 disable/invalidate를 수행한다. 이전 ELF의 L2 상태가 남는 전환의 영향은 가능한 설명이지만, **초기 수치 실패의 근본 원인이 캐시였다고 확정하지 않았다.** debugger의 기본 `dow` 역시 cache 동기화를 수행한다고 명시하므로 단순히 다운로드에 cache 관리가 전혀 없었다고 설명하면 안 된다. reset 후 L2 control 0 관측도 모든 cache tag 상태의 직접 검사를 뜻하지 않는다.

JTAG 회수는 주소·길이가 모두 4바이트 정렬된 경우 word access로 변경했다. 동일한 128-byte 상태 영역의 byte/word 회수는 길이·SHA-256·기존 원시 결과가 일치했다(`memory_read_check/report.json`). 비정렬/잔여 길이는 byte 방식, 0바이트는 빈 파일 처리다. 출력 형식과 계산 허용치는 바꾸지 않았다. 실행기 수정에 대한 오프라인 프로토콜 40/40 및 개별 Tcl/명령 생성 검사 근거는 `build/board_validation/preparation_*` 폴더에 보존했다.

## 6. 재실행 명령

아래는 완료된 `float_03`와 같은 옵션이다. `--run-id`는 반드시 새 이름을 쓴다. 관측된 동일 보드·케이블·UART가 연결되고 해당 세션이 JTAG/UART를 단독 소유하는 조건이다. runner는 기존 evidence 폴더를 덮어쓰지 않는다.

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B `
  'D:\2610_MFCC\project\scripts\run_arm_reference.py' `
  --execute --phase speech `
  --build-manifest 'D:\2610_MFCC\build\arm_platform\reproduce_01_apps\build_manifest.json' `
  --output-root 'D:\2610_MFCC\build\board_validation\board_20261005_01' `
  --run-id 'float_replay_01' `
  --target-filter 'name =~ "*Cortex-A9*#0" && jtag_cable_serial == "210351B40030A"' `
  --cable-serial '210351B40030A' --uart-port 'COM7' `
  --acknowledge-board ZYBO_Z7_20 --timeout 180 `
  --hardware-probe-log 'D:\2610_MFCC\build\board_validation\board_20261005_01\jtag_probe.log'
```

기존 probe 로그는 최초 발견 증거이므로 나중의 물리 연결 상태를 대신하지 않는다. `--execute`를 `--prepare-only`로 바꾸면 무결성·ABI 준비 검사만 수행하며 보드 통과를 주장하지 않는다. float 프로토콜 runner에 fixed/accelerator ELF 경로만 바꿔 넣지 않는다.

## 7. ARM full fixed C v2 실제 결과

[fixed_01 manifest](D:/2610_MFCC/build/board_validation/board_20261005_01/fixed_01/run_manifest.json)의 상태는 `passed`, 실제 validation 및 timing 완료다. 실행 파일은 `build/c_fixed_v2_20261004_02/arm_full_02/binaries/c_fixed_full_validate.elf`와 `c_fixed_full_timing.elf`다. v1 smoke 경로와 구분한다.

| 검증 항목 | 실제 결과 |
|---|---|
| 전체 raw13 | signed40 F24를 sign-extended int64로 회수; 6,942개 정수 불일치 0 |
| 전체 metadata | 534×8 = 4,272개 불일치 0; frame/start/BFP/power·Mel exponent/overflow/clip/clamp 포함 |
| pre-emphasis | 85,920개 Q30 전수 비트 일치 |
| 선택 중간 trace | frame0 window, FFT input/re/im, power, Mel, log/floor, MFCC 모두 비트 일치 |
| 완료 상태 | 85,920 samples, 534 frames, error/mismatches 0, finished=1, 최종 previous PCM 일치 |
| timing 반복 | firmware가 warmup 및 각 측정 후 전체 raw13/metadata를 검증; 완료 30, mismatch 0 |

실제 ELF DWARF/symbol 크기에서 상태·결과 구조를 확인했고, 내장 PCM·기대 raw13·metadata를 게시된 계약과 비교했다. validate 시 대상 DDR의 내장 자료도 다시 읽어 hash를 확인했다. validate/timing의 전체 `results.bin` SHA-256은 모두 `9c2bfef476f1e99095d5719a934b99b7aab5b1c54f37e321f1ed464b3158914c`다. frame0 이외의 모든 중간 단계를 보드에서 전수 비교한 것은 아니다.

별도 [실제 출력 감사](D:/2610_MFCC/build/board_validation/board_20261005_01/fixed_analysis/diagnostics.json)에서도 전체 정수 출력/metadata, 최종 stream 상태와 512-sample pre-emphasis ring을 재확인했다. UART에 기록한 6,942 raw13 값, 534 metadata 행, 30 ticks도 바이너리와 일치했다. 보드 raw13을 `2^24`로 나눈 float64 기준 최대 절대오차는 **0.05517150245522062**(frame 383/C4), RMSE는 **0.0062602447152708155**다. 기존 모델의 수치 오차가 그대로 재현된 것이며 **NOT_ACCEPTED** 판정을 유지한다.

측정은 **상태 reset + 85,920 PCM pushes + raw13/metadata 결과 저장 + `cf_full_finish` + 최종 frame/stream 상태 검사**를 포함한다. 두 타이머 호출 사이의 실제 `arm_cf_full_process` 본문으로 범위를 확인했다. `identity.json`의 짧은 scope 문자열은 finish를 나열하지 않지만 실제 구간에는 들어간다. context/계수 초기화, 기대값 비교와 checksum, UART, JTAG, 별도 pre-emphasis/trace 저장, cache flush는 밖이다. float32 경로와 달리 결과 저장을 포함하고 checksum은 제외하므로 두 시간의 비율을 동일 구간의 알고리즘 speedup으로 해석하지 않는다.

| 개발 음성 1클립 처리시간 | 실측 값 |
|---|---:|
| min | 1,528.967926 ms |
| median | **1,529.340455 ms** |
| p95 | 1,529.634585 ms |
| max | 1,529.787595 ms |
| 중앙값 / 534 frames | 2.863933 ms/frame |
| 중앙값 환산 처리량 | 349.170 frames/s |
| 5.37 s 음성 / 중앙값 | 3.511배 실시간 처리율 |

동일 timing ELF 안에서 3회 warmup + 30회 반복했다. 원시 30 ticks는 `fixed_01/timing/ticks.bin` 및 `timing_summary.json`에 있다. timer는 333,333,343 Hz, CPU nominal 666,666,687 Hz이며 PS-only XSA 및 실측 레지스터 교차 확인은 float 경로와 같다. timer control=1, SCTLR `0x08c5187d`, L2=1, 시작 CPSR `0xdf`다. 완료 시 직접 읽은 CPSR는 `0x600000df`, FPSCR는 `0x00000000`이며 원시 로그에 보존했다. timer read pair는 24 ticks로 별도 기록하고 차감하지 않았다. 두 ELF 시작 전 system reset/동일 XSA 초기화를 적용했고, reset 직후 L2 control 0을 관측했다.

ARM GCC 13.3.0의 공통 옵션은 `-mcpu=cortex-a9 -mfpu=vfpv3 -mfloat-abi=hard -std=c11 -O2 -g3 -fno-tree-vectorize -fno-lto -Wall -Wextra -Werror -Wconversion -Wsign-conversion -fstack-usage`, 코어 추가 옵션은 `-mgeneral-regs-only`다. 실제 argv·도구·입력·계약·BSP 식별자는 `fixed_01/identity.json`과 해당 build manifest에 보존했다.

| 파일 | SHA-256 |
|---|---|
| validate ELF | `e7023f9ea3ce5e00fc36afe112d1712c6af074156b99379085112b89f053f72e` |
| timing ELF | `9b9b4a85d9eb7c39586cb16a2941eb819ed5d69bff625d1a79dd1b64c4b90e08` |
| fixed build manifest | `1648da71f1eaeda9ffe19b1acb436992e5fa108174a9161a8893c4e122544445` |
| PS-only XSA | `8a721765b6b460fb899a98014105836b92fd76f8081e588dc8637ee078ecc1e6` |

재실행할 때 `--output-dir`은 새 폴더를 지정한다. 이 명령의 성공은 개발 음성 정수 모델 재현이며 float64 정확도 상태 `NOT_ACCEPTED`를 변경하지 않는다.

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B `
  'D:\2610_MFCC\project\scripts\run_board_fixed.py' --execute `
  --build-manifest 'D:\2610_MFCC\build\c_fixed_v2_20261004_02\arm_full_02\build_manifest.json' `
  --contract 'D:\2610_MFCC\build\fixed_contract\v2_pcm16_mfcc40_20261004_r2' `
  --output-dir 'D:\2610_MFCC\build\board_validation\board_20261005_01\fixed_replay_01' `
  --target-filter 'name =~ "*Cortex-A9*#0" && jtag_cable_serial == "210351B40030A"' `
  --cable-serial '210351B40030A' --acknowledge-board ZYBO_Z7_20 `
  --uart-port 'COM7' --timeout 300
```

## 8. 고정소수점 PS/PL 실제 결과

시스템은 `build/system_fixed/s06`, bit/XSA는 `design/zybo_z7_20_fixed.*`다. 기존 `a06` ELF의 실제 보드 smoke에서 아래 문제를 확인해 보존했고, MMIO 속성만 수정한 새 `build/arm_accel/a07/binaries/mfcc_accel_demo.elf`를 동일 s06 XSA의 새 BSP로 빌드했다. 내장 mode0 smoke 1프레임과 외부 DDR mode1 개발 음성 534프레임을 구분한다. DONE/UART만으로 수치 통과를 선언하지 않으며 raw 결과·frame/index/BFP/last·입출력/POP 개수·오류 상태를 확인한다.

### 8.1 실제 smoke 실패 진단과 최소 수정

| 실행 | 관측과 처리 |
|---|---|
| `accel_01` | FPGA program 뒤 system reset을 수행하자 PL이 미구성 상태가 됐다(`fpga_state_after_accel01.log`). 이후 순서를 system reset/PS init → s06 FPGA program → configured 상태 확인 → `ps7_post_config` → ELF로 수정했다. |
| `accel_02` | FPGA configured, reset/level-shifter 상태가 정상인데도 첫 MMIO read에서 data abort. CPU PC는 `0x00101d10`의 `Xil_DataAbortHandler`, 실제 fault instruction은 `0x00100e20`의 LDR다. 출력 0개이며 수치 검증·시간 측정으로 인정하지 않는다. |

중단 후 읽은 [fault 원시 로그](D:/2610_MFCC/build/board_validation/board_20261005_01/accel02_fault_probe.log)는 `DFSR=0x5`(section translation fault), `DFAR=0x43c00000`, `ID_MMFR0=0x00100103`, `TTBR0=0x0010805b`, MMIO descriptor `0x43c00c17`을 기록한다. BSP의 `DEVICE_MEMORY=0xC06`와 `EXECUTE_NEVER=0x11`을 OR하면 descriptor bit0도 켜진다. 관측한 Cortex-A9는 PXN을 지원하지 않으므로 이 descriptor의 low bits `11`이 translation fault를 발생시킨다. ARM short-descriptor 정의와 ID_MMFR0 해석은 [ARM DDI0406C.d, B3.5 p.B3-1324 및 p.B4-1616](https://documentation-service.arm.com/static/5f8daeb7f86e16515cdb8c4e)에 근거한다.

수정은 `software/arm_accel/mfcc_accel_xilinx.c`에서 section XN bit4(`0x10`)만 설정해 **Device/XN `0xC16`**으로 만드는 것이다. descriptor type에 대한 compile-time assertion도 추가했다. 새 a07 disassembly의 `Xil_SetTlbAttributes` 인수 `0xC16`을 확인했다. 컴파일된 driver/demo, fixed 산술·계수·RTL·s06 bit/XSA·계약은 유지했으며 a06 ELF를 덮어쓰지 않았다. 새 빌드의 host transport 검사 **968,295 checks PASS**는 실제 보드 수치 통과와 별도다. 상세 원인과 증거 hash는 [MMIO 진단 JSON](D:/2610_MFCC/build/board_validation/board_20261005_01/mmio_fault_diagnosis.json)에 보존했다.

[a06/a07 독립 비교](D:/2610_MFCC/build/board_validation/preparation_accel_20261005_01/a06_a07_identity_audit.json)에서 두 ELF의 전체 allocated code/data/table은 **주소 `0x00100e9c`의 한 바이트만** 달랐다(`MOVW r1,#0xC17` → `#0xC16`). 컴파일 입력 중 C 파일 차이도 MMIO adapter 한 개뿐이며 README 차이는 비컴파일 문서 snapshot 차이다. 새 BSP의 `libxil.a` 전체 hash는 debug 경로 차이로 달라졌지만, 124개 archive member의 allocated section은 모두 동일했다. 따라서 새 ELF 전체 hash가 같다는 주장은 하지 않으며, 실제 로드되는 코드/데이터의 최소 변경을 별도로 확인했다.

### 8.2 a07/s06 실제 개발 음성 수치 검증

`accel_03/smoke`의 mode0 512-sample/1-frame/13-record 검사와 `accel_03/development`의 mode1 전체 음성 검사가 통과했다. mode0는 firmware 내장 비교와 host 전체 레코드 비교를 모두 수행했다. mode1의 `numeric_checked=0`, `numeric_passed=0`은 firmware가 전체 음성 기대값을 내장하지 않았다는 뜻이며, host가 회수한 전체 raw 레코드를 동결 모델과 비교한다.

| 확인 항목 | 개발 음성 실제 결과 |
|---|---|
| PCM | 85,920 samples 전체 DDR readback 일치, firmware CRC32 `924516957` |
| MFCC | 534 frames × 13 = 6,942 signed40/F24 값 비트 일치 |
| ABI metadata | 6,942개 frame/index/signed BFP/last/error 레코드 비트 일치 |
| 입력 카운터 | `samples_sent=input_written=input_consumed=85920` |
| 출력 카운터 | `records_received=output_captured=output_popped=6942` |
| 완료·오류 | DONE-only status `2`, result/error flags `0`, 오류 복구 ABORT 없음(`abort_attempted=0`) |
| 마지막 레코드 | frame 533, index 12; `last_lo/hi/frame/meta`도 마지막 레코드와 대조 |
| 회수 파일 | `results.bin` 166,608 bytes; SHA-256 `888ee289cc44f03602934002e9c4e2793cd3c4bc6349547c247bbda934eba6a6` |

최종 MFCC는 signed40 F24를 `2^24`로 나누며 BFP 배율을 다시 곱하지 않는다. 전송 ABI에는 개별 Power/Mel exponent, 단계별 overflow/saturation 횟수나 `bfp_clamped` 필드가 없다. 따라서 이 결과를 전체 내부 metadata 전수 관측이나 오류 주입 시험으로 확대하지 않는다. 동결된 개발 음성은 overflow/input clip/BFP clamp가 모두 0인 정상 입력이다. 기존 float64 정확도 **NOT_ACCEPTED**를 유지한다.

### 8.3 PS/PL 측정 조건

각 job은 system reset → CPU1 halt → s06 `ps7_init`/`ps7_post_config` → 동일 s06 bit program → FPGA configured 확인 → `ps7_post_config` 재적용 → a07 ELF 순서로 시작한다. READY와 완료 때 실제 PC·MMU table·cache·timer·PLL/분주 레지스터를 읽는다. SCTLR `0x08c5187d`, CPSR `0x600000df`, FPSCR `0`, L2 control `1`, global timer control `1`, TTBR0 `0x0010805b`, TTBCR `0`, ID_MMFR0 `0x00100103`, MMIO descriptor **`0x43c00c16`**을 확인했다. CPU0가 실행하고 CPU1은 정지 상태다.

CPU/timer BSP nominal은 각각 666,666,687/333,333,343 Hz다. FCLK0는 s06 설정 100 MHz이며 실제 PLL/분주 레지스터와 설정된 33,333,333 Hz 입력으로 재구성하면 99,999,999 Hz다. HW busy 환산에는 이 재구성값을 사용한다. 물리 발진기를 외부 계측하지 않았다는 한계는 C 경로와 같다.

PS timer 구간은 identity probe 이후의 **ABORT + SAMPLE_COUNT + START → PCM MMIO feed/output polling → DDR 결과 저장 + 모든 POP + 카운터 검사 + HW cycle 조회**다. startup, reset/FPGA program, JTAG/UART, 초기 PCM CRC, cache flush, host 수치 비교는 제외한다. timer read overhead는 이 경로에서 별도 측정·차감하지 않았다. HW busy cycles는 PS stall을 포함하므로 순수 PL 계산시간으로 이름 붙이지 않는다. busy polling에서 CPU가 자유로워졌다는 주장은 하지 않는다.

매 trial마다 새로 초기화하므로 앞선 3회는 **제외한 초기 실행**이고 이후 30회에 cache 상태를 이어주는 warmup은 아니다. 각 trial의 PCM CRC 사전 읽기가 입력 cache를 채우므로 순수 cold-cache 시험이라고 부르지도 않는다. C의 동일 ELF 내 3회 warmup + 30회 반복과 구분한다. 모든 반복에서 전체 6,942개 출력/ABI metadata와 입력 readback을 확인하고, L2/timer/SCTLR/FPSCR 전후 일치 및 HW busy 시간이 ARM 측정 구간 안에 들어가는지도 확인했다.

| 개발 음성 1클립 처리시간 | 30회 실측 값 |
|---|---:|
| min | 123.965567 ms |
| median | **123.965773 ms** |
| p95 | 123.966007 ms |
| max | 123.966167 ms |
| 중앙값 / 534 frames | 0.232146 ms/frame |
| 중앙값 환산 처리량 | 4,307.641 frames/s |
| 5.37 s 음성 / 중앙값 | 43.318배 실시간 처리율 |
| HW busy 중앙값(PS stall 포함) | 123.961646 ms |

[accel_03 manifest](D:/2610_MFCC/build/board_validation/board_20261005_01/accel_03/run_manifest.json)는 `BOARD_DEVELOPMENT_BIT_EXACT_PASS`, `timing_measured=true`다. `timing/trial_000`~`trial_032`의 원시 상태·결과·레지스터·UART/Tcl 로그를 보존했다. 처음 3개를 제외한 30개에 선형 보간 p95를 적용했다. 전체 33회 출력 파일은 validation 결과와 바이트 동일하다.

[독립 감사](D:/2610_MFCC/build/board_validation/board_20261005_01/accel_analysis/diagnostics.json)는 runner 비교기를 재사용하지 않고 실제 ELF와 원시 레코드를 해석했다. smoke+validation+33 trials의 **35개 job/236,041개 레코드**, UART READY/RESULT 35쌍, 해시·PCM CRC/readback·모든 레코드와 마지막 payload·환경 상태 검사를 통과했다. 전체 음성의 PS/PL raw13은 `fixed_01`과 일치하고 float64 최대오차/RMSE도 각각 **0.05517150245522062 / 0.0062602447152708155**로 동일하다. 시간 측정 완료와 알고리즘 정확도 **NOT_ACCEPTED**를 별도 기록한다.

### 8.4 PS/PL 식별자와 재실행

| 파일 | SHA-256 |
|---|---|
| a06 ELF (실패 증거 보존) | `58ed18398fbdbc2fceb0d07ce2caefc473bc4845f6336bcd1a186f2ad2eb0e3a` |
| a07 ELF | `82971d027271f1fb5b0f3ae85ce0d9acc83ce35f4ef9c63867eceec24e5846e2` |
| s06 bit | `d364574bce6e191e898d8971061aac391aa9a858b2aab063b849cf7193b66f68` |
| s06 XSA | `2ca6f295ddf7547b890368967457c1533f09122239a886dbe1b5ada0f2b156dd` |
| s06 PS init | `126a7277430f44f331d35eafcfa15fc74826a8dcf979ef20f0c30781ca002124` |
| 최종 PS/PL runner | `7dc9ad1ad54289f62bb7f579cbe97b7b3470d46b73756a5d82470e8436d68bd7` |

ARM GCC 13.3.0 및 `-mcpu=cortex-a9 -mfpu=vfpv3 -mfloat-abi=hard -std=c11 -O2 -g3 -fno-tree-vectorize -fno-lto -Wall -Wextra -Werror -Wconversion -Wsign-conversion -fstack-usage`로 빌드했다. 소스·BSP·도구·ABI·클록 식별자는 `accel_03/identity.json`, a07 build manifest와 source snapshot에 있다. 실행 전 runner의 비교기 fixture 24개(정상 1개 + 오류 거부 23개) 및 Tcl 흐름/target/MMU/cache 조건 fixture 검사를 통과했다(`preparation_accel_20261005_01/check_revision08.json`).

새 run 이름과 현재 확인한 연결을 사용한다. `--arm-run a07`가 MMIO 수정 ELF를 선택하며, float/fixed 전용 runner로 이 ELF를 실행하지 않는다.

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B `
  'D:\2610_MFCC\project\scripts\run_board_accel.py' --execute --arm-run a07 `
  --run-id 'accel_replay_01' `
  --output-root 'D:\2610_MFCC\build\board_validation\board_20261005_01' `
  --acknowledge-board ZYBO_Z7_20 --cable-serial '210351B40030A' `
  --target-filter 'name =~ "*Cortex-A9*#0" && jtag_cable_serial == "210351B40030A"' `
  --initialize `
  --fpga-target-filter 'name == "xc7z020" && jtag_cable_serial == "210351B40030A"' `
  --uart-port COM7 --warmups 3 --repeats 30
```

`--prepare-only`는 보드에 접속하지 않는다. `--smoke-only`는 1프레임, `--skip-timing`은 smoke+전체 음성 수치 검증까지다. 기존 s06 post-route WNS +0.506 ns/WHS +0.030 ns 및 RTL/VIP 검증은 실제 보드 수치/측정과 별도 근거다.

FP32 하드웨어의 전체 음성 RTL 통과 기록은 유지하지만 PS/PL 시스템·보드 ELF 통합이 준비되지 않았으므로 이 문서에서 FP32 보드 성능이나 비교 speedup은 제시하지 않는다.

## 9. 세 경로 비교와 종료 상태

| 개발 음성 경로 | 보드 수치 결과 | 클립 중앙값(30회) | 주요 측정 구간·조건 |
|---|---|---:|---|
| ARM float32 C | PC/Python 허용치 통과 | 127.434356 ms | init+push+frame 검사+checksum; 동일 ELF warmup 3회 |
| ARM full fixed C v2 | 동결 정수 모델 비트 일치 | 1,529.340455 ms | reset+push+raw13/metadata 저장+finish 검사; 동일 ELF warmup 3회 |
| fixed PS/PL s06/a07 | 모델 및 ARM fixed C 비트 일치 | 123.965773 ms | config+MMIO feed/poll+결과 저장/POP+카운터 조회; 매회 재초기화, 초기 3회 제외 |

표의 시간은 각 명시한 구간의 관측값이다. 동일 구간·cache 정책으로 측정한 알고리즘 speedup을 산출하지 않았으며, PS/PL 측정에는 CPU polling과 전송 비용이 포함된다. 정수 모델 비트 일치가 float64 기준 수치 합격을 의미하지 않는다. 지정 개발 음성 외의 합성/평가 입력과 FP32 PL 보드 실행은 이 작업의 통과 범위에 포함하지 않는다.

최종 실행은 CPU0의 결과 breakpoint `0x00100f18`에서 멈췄고 CPU1은 정지 상태로 유지했다. UART capture 및 실행기 프로세스는 정상 종료했다. 기존 hw_server를 종료하지 않았으며 Flash/SD/영구 부팅 설정, 논문·발표 파일, 동결된 입력·규격·허용치·정수 계약·s06 bitstream을 변경하지 않았다. 커밋·푸시·다른 채팅으로의 메시지 전송도 수행하지 않았다.

실행별 manifest·artifact hash index와 원시 데이터를 실행 루트 아래 보존했다. [요약 JSON](D:/2610_MFCC/build/board_validation/board_20261005_01/summary.json)과 [재생성 스크립트](D:/2610_MFCC/build/board_validation/board_20261005_01/summarize_results.py)는 완료 상태·측정 통계·범위 제한·주요 증거 hash를 묶는다.
