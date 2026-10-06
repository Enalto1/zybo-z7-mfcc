# ZYBO Z7-20 ARM bare-metal MFCC adapter

이 디렉터리는 기존 `software/c`의 float32 계산 코어에 Zynq Cortex-A9 CPU0의 DDR/JTAG 입출력과 타이머를 붙인다. 수학 정의, 계수, 로그 하한, PC 결과와 허용 오차는 변경하지 않는다. **소스와 ELF가 존재하는 것만으로 실제 보드 실행·수치 통과·성능 측정을 완료했다고 판단하지 않는다.** 실제 연결/빌드/실행 증거는 실행별 manifest와 상위 ARM 결과 문서를 따른다.

대상은 2024.2 도구로 생성한 ZYBO Z7-20 PS 플랫폼의 standalone BSP이다. 마이크, I2S, 코덱, Linux, PL MFCC, DMA는 포함하지 않는다. 다른 프로세서는 동작시키지 않는 CPU0 전용 실험이다.

## 소스와 빌드

| 파일 | 역할 |
|---|---|
| `arm_protocol.h` | 버전 1 제어/상태/출력/trace descriptor ABI와 상태·오류 코드 |
| `arm_support.c/.h` | startup, 정해진 DDR 영역 검사, 캐시 관리, CRC32, FPSCR·클록 레지스터 기록 |
| `hello_main.c` | `hello_ddr.elf`: UART marker와 DDR 검사만 실행 |
| `mfcc_main.c` | `mfcc_arm.elf`: 검증·한 프레임 trace·반복 시간 측정 |
| `create_bsp.tcl` | 플랫폼/BSP 생성 자동화; 상위 빌드 절차에서 실행 |

hello는 support와 hello main을 링크한다. MFCC는 support, MFCC main, 변경하지 않은 `software/c/mfcc.c`, `fft32.c` 및 동결된 `mfcc_tables.c`를 링크한다. BSP의 `libxil`, C runtime, `logf`를 제공하는 `libm`이 필요하다. 헤더 경로에는 이 디렉터리, `software/c`, 동결 계수 디렉터리와 BSP include를 넣는다.

의도한 계산 플래그는 `-std=c11 -O2 -mcpu=cortex-a9 -mfpu=vfpv3 -mfloat-abi=hard -fno-fast-math -ffp-contract=off -fexcess-precision=standard`이다. 빌드는 `-Wall -Wextra -Werror -Wno-error=enum-compare`를 사용한다. 마지막 옵션은 기존 코어의 두 enum FFT 크기를 비교하는 static assert에서 GCC가 내는 경고를 보존하되 오류로 승격하지 않아, 동결된 코어 소스를 수정하지 않도록 한다. 실제 컴파일러 버전·전체 argv·ELF/라이브러리 hash는 빌드 manifest가 근거다. core의 binary32 정적 검사를 그대로 적용하고, 실행 시 FPSCR의 rounding을 nearest로, FZ와 DN을 0으로 설정·기록한다. `logf`는 ARM C runtime 구현을 사용하므로 PC CRT와의 비트 동일성을 가정하지 않는다.

## 메모리와 안전한 DDR 확인

주소를 임의 상수로 정하지 않는다. 아래 객체는 ELF/linker가 할당하며 호스트는 `arm-none-eabi-nm`으로 주소와 크기를 확인한다. 데이터 버퍼는 64바이트 정렬이다.

| 심볼 | 저장 내용/최대 크기 |
|---|---|
| `arm_pcm` | int16 262,144개, 524,288 bytes |
| `arm_results` | 2,048개 × 60 bytes; `uint32 frame_id`, `uint32 start_sample`, float32 C0…C12 |
| `arm_preemphasis` | trace 모드에서만 유효한 float32 262,144개 |
| `arm_trace` | 선택한 한 `mfcc_frame`; descriptor로 각 배열 위치를 해석 |
| `arm_timing_records` | 100개 × 16 bytes; elapsed ticks low/high, frame count, checksum |
| `arm_control`, `arm_status`, `arm_layout` | 각각 64, 128, 128 bytes |
| `arm_clock_registers` | ARM/DDR/IO PLL control 및 ARM clock control의 4개 uint32 |
| `arm_ddr_test` | 별도 `.ddr_test` section의 4,096 bytes, 4KiB 정렬 |

계산 state/frame도 정적 DDR 객체다. 큰 배열이나 프레임을 stack 또는 malloc으로 만들지 않는다. linker는 코드·데이터·BSS·stack을 검증된 DDR 범위 안에 배치하고, `.ddr_test`를 다른 section과 겹치지 않는 `NOLOAD` section으로 예약해야 한다. `KEEP(*(.ddr_test))`, 정확한 크기 4096, DDR 시작/끝 경계의 `ASSERT`가 필요하다. 상위 빌드가 map과 심볼 크기를 확인한다. DDR test는 이 배열만 덮어쓰며 input/output/프로그램/stack 영역을 검사 대상으로 사용하지 않는다.

DDR 검사는 0, 모든 bit 1, A5, 5A 패턴과 각 word 주소에 의존한 패턴을 순서대로 쓴 뒤 flush/invalidate 후 읽어 대조한다. 최초 실패 index/기대값/관측값을 status에 남긴다. 이 작은 영역의 검사는 전체 DDR의 exhaustive memory test 또는 장시간 안정성 시험이 아니다.

## 한 번 실행하는 JTAG 프로토콜

1. CPU0를 reset하고 올바른 PS/DDR 초기화를 수행한다. 각 job에서 ELF를 다시 다운로드하고 BSP startup을 실행한다. 이전 ELF의 cache/MMU 상태를 둔 채 PC만 `main`으로 바꾸지 않는다.
2. `arm_ready_breakpoint`와 `arm_result_breakpoint` 주소에 breakpoint를 설치한 후 실행한다. startup은 cache를 enable하고 FPSCR을 설정하며 DDR 검사를 한다. UART marker는 hello에서 `ARM_HELLO_DDR_V1`, MFCC에서 `ARM_MFCC_READY_V1`이다. marker 하나를 DDR 또는 수치 통과 증거로 사용하지 않는다.
3. ready에서 정지하면 status의 magic/version/state/error 및 DDR 검사 결과를 확인한다. MFCC는 이 지점 전에 **전체 arm_pcm 영역을 flush하고 invalidate**하여 BSS 초기화의 dirty cache line을 제거한다. 이 후 resume 전까지 CPU는 input에 접근하지 않는다.
4. MFCC job은 동일 PCM16 little-endian 바이트를 `arm_pcm`에 다운로드한다. 호스트가 전체를 다시 읽어 SHA-256을 대조한 뒤 control을 쓴다. command는 마지막에 쓴다. 모호한 target, 범위 초과, byte count/hash 불일치는 실행하지 않는다.
5. resume하면 CPU가 control/input cache를 invalidate하고 control의 CRC32와 실제 PCM CRC32를 대조한다. CRC는 reflected polynomial `0xEDB88320`, 초기/최종 XOR `0xFFFFFFFF`이며 raw PCM 바이트 순서를 사용한다. JTAG 왕복 SHA-256과 별개의 검사다.
6. 전체 계산이 끝나면 해당 출력과 status를 flush하고 DSB/ISB 후 result breakpoint에 도달한다. 호스트가 상태와 결과를 읽는다. MFCC 내부 또는 timed region에는 breakpoint가 없다.
7. result 이후 firmware는 idle에 머문다. control을 새로 써서 자동 반복하지 않는다. 다음 job은 새 reset/download로 시작한다.

이 방식은 실행 중 cached doorbell을 반복 읽는 polling protocol을 사용하지 않는다. 입력의 마지막 cache line이 부분적으로 다운로드되는 경우도 ready 전에 전체 input을 clean했으므로 post-download invalidate가 오래된 dirty BSS zero를 다시 DDR에 쓰는 위험을 피한다. 확인 없이 다른 프로그램이 CPU1이나 같은 DDR buffer를 사용하도록 하지 않는다.

## 제어와 출력 해석

control의 uint32 순서는 `magic, version, command, mode, sample_count, expected_crc32, trace_frame, warmups, repeats, validation_passed, reserved[6]`이다. magic은 `0x4D464343`, version과 RUN command는 1, reserved는 모두 0이다.

| mode | 의미와 필드 |
|---|---|
| 1 validation | 모든 60-byte 출력 레코드; warmups/repeats/validation_passed는 0 |
| 2 trace | 모든 최종 레코드 + 전체 preemphasis + 선택한 한 프레임 중간 단계; trace_frame은 유효 frame index 또는 아래 0프레임 sentinel, 나머지 반복 필드는 0 |
| 3 timing | warmups 0…20, repeats 1…100, 최소 한 프레임; host가 앞선 수치 검증을 확인한 뒤 validation_passed=1 |

sample_count는 0…262144이다. 프레임 수는 `T<512 ? 0 : 1+(T-512)/160`이며 꼬리 flush는 없다. 최대 입력에서도 1,636개로 출력 capacity 이내다. 0프레임 입력의 trace에는 **trace_frame=UINT32_MAX (`0xFFFFFFFF`)만 허용**한다. 이 경우 전체 입력의 preemphasis만 유효하고 trace_valid=0을 유지하며 arm_trace를 읽지 않는다. 빈 입력은 preemphasis도 길이 0이다. 프레임이 있는 입력에서는 sentinel을 거부하고 유효 frame index를 요구한다. timing은 0프레임 입력을 거부한다. `validation_passed=1`은 호스트의 사전 검사 선언이며 firmware 자체가 Python 수치 비교를 수행했다는 뜻이 아니다.

상태는 BOOT=0, READY=1, RUNNING=2, DONE=3, ERROR=4이다. 정상 수치 회수에는 DONE/error=0, 입력 CRC, 프레임 수/ID/시작점 검증이 모두 필요하다. DONE은 계산·전송 종료 상태이며 Python 허용 오차 통과와 다르다. 상세 field 순서와 오류 코드는 header가 유일한 ABI 정의다.

`arm_results`는 static assert로 stride 60와 계수 offset 8을 고정한다. raw `mfcc_frame` 구조체의 packing을 가정하지 않는다. `arm_layout`의 크기와 각 `offsetof`를 먼저 읽어 trace의 uint64 frame ID/start, frames512, windowed512, complex FFT257의 interleaved514float, power257, Mel26, log26, DCT13, MFCC13, frame energy를 분리한다. 값은 little-endian binary32, trace ID는 little-endian uint64다. trace_valid가 1인 경우만 유효하다. timing 모드의 `arm_results`/trace/preemphasis는 결과로 사용하지 않는다.

## 타이밍과 환경 기록

CPU0 standalone 앱은 SCU global timer를 소유한다. startup의 `XTime_SetTime(0)`으로 timer를 enable/prescaler=0으로 설정한 다음 control 값을 기록한다. 각 반복에서 `XTime_GetTime` 두 번의 차이를 64-bit tick으로 저장한다. 별도 연속 두 번 읽기의 차이도 status에 남기지만 측정값에서 자동으로 빼지 않는다.

timed region은 클립별 `mfcc_init`, 입력마다 `mfcc_push`, frame ID/count 검사, 모든 출력 계수의 checksum 소비를 포함한다. UART, PCM CRC, JTAG 전송/회수, cache 관리, preemphasis/최종 레코드/trace 복사는 밖에 있다. 각 warmup/repeat는 상태를 새로 초기화한다. timing의 출력 소비를 제외한 가상의 FFT-only 시간으로 해석하지 않는다.

입력 CRC가 먼저 PCM을 읽고 cache를 enable한 상태에서 반복하므로 warmups=0도 cold-cache 실험이라고 부르지 않는다. 전체 클립 tick을 프레임 수로 나눈 값은 평균 처리 비용이며 개별 프레임의 도착 시점부터 완료까지 지연을 직접 측정한 값이 아니다.

checksum은 clip 시작 `0x6d666363`에서 각 frame_id/start_sample을 XOR하고, 각 계수의 float32 bit pattern을 XOR한 뒤 rotate-left 5와 `+0x9e3779b9`를 uint32 범위에서 수행한다. validation/trace/timing에 동일하게 적용한다. 호스트는 반복 결과의 frame count와 checksum을 앞선 검증 출력과 대조한다. 이것은 입력 CRC나 암호학적 hash가 아니며 계산 제거 방지와 반복 확인용이다.

status의 cpu_hz와 timer_hz는 **BSP 설정값**이며 물리 클록을 계측한 값이 아니다. SLCR ARM/DDR/IO PLL control, ARM clock control, SCU timer control, CP15 SCTLR, PL310 L2 cache control, CPSR와 FPSCR before/active/after를 함께 기록한다. 호스트는 PS 입력 클록 출처·분주 설정과 timer prescaler를 확인해야 초로 환산한다. cache enable/disable 및 IRQ 상태는 관측 register에 근거해 보고한다. 실측한 반복들만 min/median/p95/max, frames/s, audio-seconds/compute-seconds로 계산하며 아직 실행하지 않은 보드 성능은 빈 상태로 남긴다.

PC의 알려진 합성 입력 2개 수치 실패는 [C_REFERENCE_RESULTS.md](../../docs/C_REFERENCE_RESULTS.md)에 있다. ARM 결과를 얻어도 이 실패를 삭제하거나 허용 오차를 조용히 바꾸지 않는다. ARM runtime의 logf 차이와 target의 float 동작은 동일 PCM/계수와 stage trace로 별도로 비교한다.
