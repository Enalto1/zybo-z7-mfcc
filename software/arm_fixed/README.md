# Cortex-A9 partial fixed C adapter

이 디렉터리는 고정된 정수 FFT 입력 → FFT → Power → Mel만 ARM에서 실행한다. PCM 전처리·window·BFP 선택·정수 log·DCT와 raw13 출력은 아직 없다. 기존 `software/arm` 및 float32 ELF와 별개다.

`build_c_fixed_arm.py`는 완료된 PC 실행의 고정 소스·계수·벡터와 기존 `reproduce_01_apps` standalone BSP를 검증한 뒤 새로운 `arm_*` 디렉터리에 복사하여 빌드한다. XSA/BSP를 생성하거나 수정하지 않고 보드에 연결하지 않는다. 같은 출력 디렉터리 이름의 재사용은 거절한다.

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B `
  'D:\2610_MFCC\project\scripts\build_c_fixed_arm.py' `
  --run-dir 'D:\2610_MFCC\build\c_fixed_20261004_03' --arm-id arm_02
```

`c_fixed_validate.elf`는 고정된 smoke 프레임을 처리하고 자연 순서 FFT 512개 복소수, Power 257개, Mel 26개, BFP 지수·복원 지수·overflow 플래그를 기대 정수와 비교한다. UART에는 계약 hash, `FRAME`의 입력 사례·프레임 번호·시작 PCM 위치·PCM hash, `FFT`, `POWER`, `MEL`, `META` 행과 승격/5개 butterfly 경계 `PROMOTED`, `GROUP`, `GROUP_OVF` 행을 출력한다. 정수 FFT/trace는 부호 있는 십진수, unsigned64 Power/Mel은 정확히 16자리 16진수다. `GROUP`의 내부 순서는 코어의 `cf_fft_trace` 계약을 따른다. 중간 trace는 덤프되지만 이 ELF의 내장 기대값 비교는 최종 FFT/Power/Mel 및 metadata에 한정한다.

UART capture는 정확도 검증 전용이며 시간이 오래 걸릴 수 있다. 완료 시 `arm_cf_finished`에 debugger breakpoint를 설정하고 `arm_c_fixed_result`, `arm_c_fixed_trace`, `arm_c_fixed_status`를 읽을 수도 있다. 모든 관련 영역은 breakpoint 전에 cache flush된다. C struct를 파일 형식으로 취급하지 말고 ELF debug 정보/필드와 해당 snapshot header에 따라 읽는다. 보드 UART capture와 memory dump의 호스트 비교 실행은 이번 작업에서 수행하지 않았다.

`c_fixed_timing.elf`는 같은 smoke 프레임으로 `cf_init` 및 3회 warmup/기대값 검사를 먼저 수행한 뒤 100회를 측정한다. 각 측정 구간은 두 `XTime_GetTime` 호출 사이의 `cf_process(..., trace=NULL)` 한 번이다. 결과 비교/checksum, UART, cache flush, 초기 table 검증과 타이머 설정은 구간 밖이다. 코어 본래의 결과 배열 저장은 포함한다. 각 반복은 결과 전체를 소비하고 기대값과 비교하며, 오류나 정지 타이머를 만나면 중단한다. 원시 tick은 `arm_c_fixed_ticks`와 UART `TICKS`에 남는다. 타이머 호출 overhead는 별도로 기록하고 차감하지 않는다.

CPU와 timer 주파수는 BSP nominal 값이다. 실제 측정 전에 기록된 SLCR clock registers, timer enable/prescaler, CPSR, SCTLR, L2 cache 상태와 플랫폼 초기화를 확인해야 한다. cache enabled, 반복 입력을 사용하는 warm-cache 단일 프레임 시험이며 전체 PCM MFCC 시간이나 ARM float32 대비 가속비가 아니다. 이 코드에는 NEON 최적화가 없고 빌드는 `-O2 -fno-tree-vectorize -fno-lto` scalar 기준이다.

빌드 결과 `build_manifest.json`, `artifact_manifest.json`, `integer_core_audit.json`, ELF/map/readelf/disassembly/symbol/size 보고서와 `.su` stack 자료를 보존한다. VFP hard-float ABI는 재사용 BSP의 ABI이며 정수 코어에 부동소수점 계산을 추가하지 않는다. 코어 object에서 VFP/NEON instruction 및 FP runtime helper가 없는지 검사하며, BSP startup이 부동소수점 상태를 초기화하는 코드까지 없다는 뜻은 아니다.

**보드 실행은 보류 상태다.** ELF 링크 성공은 ARM 비트 일치나 성능 측정 완료가 아니다. 실제 UART/DDR/수치/시간 결과는 향후 별도 실행으로 남겨야 한다.
