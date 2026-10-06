# Cortex-A9 full fixed MFCC adapter

`v2_pcm16_mfcc40_20261004_r2`의 PCM16 → pre-emphasis → 512/160 framing → Hamming → BFP → FFT → Power/Mel → 정수 log → DCT/raw13 경로를 위한 별도 어댑터다. 기존 v1 partial 어댑터, float32 ARM 코드, XSA/BSP/ELF는 보존한다. 수치 정확도 합격과 ARM 실행은 빌드와 별도 상태다.

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B `
  'D:\2610_MFCC\project\scripts\build_c_fixed_full_arm.py' `
  --run-dir 'D:\2610_MFCC\build\c_fixed_v2_20261004_02' --arm-id arm_full_02
```

`--run-dir`에는 완료된 PC 비트 검증 실행을 지정한다. `--arm-id`는 새 이름이어야 하며 기존 산출물을 덮어쓰지 않는다. 빌더는 PC 검증을 통과한 소스 snapshot과 고정된 계수/벡터 hash를 검사한 뒤 새 폴더로 복사한다. 현재 수정 중인 Python/C 모델을 불러오지 않는다. 기존 PS-only XSA의 실제 HWH 모듈 목록과 BSP include/lib/startup/linker artifact hash도 검사한다. 새 XSA/BSP 생성이나 보드 연결·다운로드는 하지 않는다.

`c_fixed_full_validate.elf`에는 개발 PCM 85,920개와 기대 raw13 534×13개, 프레임 metadata 534×8개를 넣는다. PCM 샘플 순서로 한 번씩 처리하며 pre-emphasis 상태는 clip 전체에 연속 유지한다. 모든 PCM과 불완전 꼬리를 처리한 후 `cf_full_finish`를 호출하며 꼬리 frame을 출력하지 않는다. raw13, frame/start, BFP s, Power/Mel 지수, FFT overflow, 입력 clipping, BFP clamp를 정수로 비교한다.

UART의 `RAW13 frame coefficient bits`는 signed40/F24 값을 64-bit two's-complement **16자리 16진수**로 출력한다. `META frame` 뒤 8개 십진수의 순서는 `frame_id,start_sample,s,power_exp,mel_exp,fft_overflow,input_clips,bfp_clamped`다. signed32 중간값은 십진수, unsigned64 Power/Mel은 16자리 16진수다. version, 발행 계약 hash, 로컬 fixture hash, PCM hash도 먼저 기록한다.

정확도 경로는 전체 pre-emphasis 85,920개를 `arm_c_fixed_full_preemphasis`에 보존하고 선택한 프레임의 전체 중간 결과를 `arm_c_fixed_full_trace`에 복사한다. 기본 trace는 frame0이다. 향후 보드에서 `arm_cf_full_ready` breakpoint로 정지해 `arm_c_fixed_full_trace_frame`을 0…533 또는 `UINT32_MAX`(trace 없음)로 바꿀 수 있다. `arm_c_fixed_full_dump_all_pre=1`은 전체 pre-emphasis의 UART 출력을 추가한다. 기본 UART trace는 선택 프레임의 pre-emphasis/window/FFT 입력512개, FFT 전체512개 복소수, Power257개, Mel/log/floor26개다. UART 중간 trace는 추후 호스트 기준 파일과 대조해야 하며 ELF의 내장 기대값 비교는 모든 프레임 raw13/metadata에 한정한다.

`arm_cf_full_finished` breakpoint 전에 결과·상태·전체 pre-emphasis·선택 trace를 cache flush한다. debugger에서 내보낸 파일은 해당 ELF와 header의 필드/offset에 따라 해석한다. struct padding을 고정된 파일 ABI로 추정하지 않는다.

`c_fixed_full_timing.elf`는 같은 개발 clip 전체를 3회 warmup/검증하고 30회 반복한다. 측정은 `reset + 85,920 PCM push + 534 raw13/metadata 결과 저장 + finish/프레임 상태 검사`이며 두 XTime 호출 사이에 둔다. 초기 계수/context 검증, 기대값 비교/checksum, 입력 전송, UART, 별도 pre-emphasis/trace 저장, cache flush는 측정 밖이다. 모든 반복 결과는 타이머 정지 후 소비·검증한다. 같은 PCM·cache enabled 반복을 사용하는 warm-cache clip 시험이다. 호출 overhead를 별도로 기록하고 차감하지 않는다. 원시 tick은 `arm_c_fixed_full_ticks[30]`와 UART에 보존한다.

빌드는 Cortex-A9/VFPv3 hard-float BSP ABI에 맞춘 `-O2 -fno-tree-vectorize -fno-lto` scalar C11이며 정수 코어에는 부동소수점 계산과 `__int128`이 없다. 코어에 `-mgeneral-regs-only`를 추가해 정수 상수를 옮길 때도 VFP register를 사용하지 않는다. 첫 `arm_full_01` 시도에서 GCC가 DCT 정수 상수 이동에 `vpush/vldr/vstr/vpop` 4개를 선택했으며, 보수적인 VFP 명령 금지 검사 때문에 링크 전에 중단했다. 부동소수점 산술이나 FP helper 오류는 아니었고 실패 산출물을 보존했다. object의 VFP/NEON 명령·부동소수점 및 128-bit 산술 helper를 검사한다. BSP startup에 FP 상태 초기화 코드가 없다는 주장은 아니다. ELF/map/section·symbol 크기/stack `.su`/disassembly/hash를 실행 폴더에 보존한다.

**보드 실행·ARM 수치 비교·성능 측정은 아직 하지 않았다.** BSP CPU/timer 주파수는 nominal 값이며 실물 clock/cache/DDR/UART 확인과 회수 결과가 있어야 측정을 해석할 수 있다. 빌드 성공으로 float32보다 빠르다거나 SIMD 최적화 구현이라고 주장하지 않는다.
