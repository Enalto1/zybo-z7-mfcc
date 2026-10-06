# ARM fixed C PCM→MFCC 전체 경로 구현·검증 결과

갱신: 2026-10-04 KST. **PCM16 → 연속 pre-emphasis → 512/160 framing → window → 정수 BFP → FFT → Power → Mel → 정수 log → DCT raw13 전체 경로를 구현했다.** 고정 기준은 v2_pcm16_mfcc40_20261004_r2다. PC의 전체 단계 비트 비교와 별도 ARM ELF 2개 cross-build를 완료했다. 보드 실행·ARM 정확도 측정·성능 측정은 하지 않았다.

현재 실행은 D:/2610_MFCC/build/c_fixed_v2_20261004_02, ARM은 그 아래 arm_full_02다. **비트정확 C 이식은 통과했지만 알고리즘 수치 정확도는 NOT_ACCEPTED다.** 기존 float32 C/ARM, v1 코어·실행, Python 정수 모델, RTL, 발행 계약을 보존했다.

## 기준과 재현 식별

요청받은 D:/2610_MFCC/build/fixed_contract/v2_pcm16_mfcc40_20261004_r2 및 docs/reviews/FIXED_POINT_PORTING_NOTES.md를 기준으로 확장했다. v2가 추가한 정수 전처리·window·BFP 선택·log·DCT만 새 파일에 구현하고, v1 FFT/Power/Mel 및 정수 도구는 수정 없이 호출한다. v2 r2는 앞선 v2의 Mel 경로 descriptor와 metadata를 보완한 발행이며 수치 파일 426개는 동일하다는 발행 기록을 보존했다. 이 기록과 이번 C 검증 결과는 구분한다.

PUBLISHED.json의 3개 digest와 artifact index의 실제 437개 파일, 계약의 415개 descriptor를 검증했다. 발행 verification.json에는 435개 hash 및 7,099 checks가 기록되어 있다. 실제 현재 index 수와 발행 당시 기록을 혼동하지 않는다. 실행에 계약 전체, 모델 snapshot, 정수 계수·PCM·각 단계 정답을 복사하고 hash를 기록했다. 개발 중인 software/fixed_model은 실행하지 않는다.

| 식별 | SHA-256 |
|---|---|
| 발행 contract.json | 283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e |
| 발행 artifact_hashes.json | 67403d3d0483b376ab578355eb623947bba931d68c1bde6dca03cbbc32d05b45 |
| 발행 verification.json | 41ea35e82435f462a8c0d59664293775e04b5afcbc429fbeda2ae9f16af68049 |
| 이번 C fixture contract.json | 04b81c51b963cbc4147465fc05c62e6c6f110d2f569f4a3058dec9f2ffadc94e |
| full C 코어 mfcc_fixed_full.c | d527bad8b9b724f8c2b35f8c3bbe1602c61c738a94ab8b33f86d7470ab6428d9 |
| full C API mfcc_fixed_full.h | e35f56cbf41c2a00c7cf8d2266ae2ca276a823a0c93251676d77500dbfa71c6d |
| window U31/F30 원본 | b5c6c404b9d860ed9ee5a1a6ad83a5c71bccdcc8df6081cf96cda2ffe8592057 |
| twiddle 원본 ROM | b36956acf6ab9589a397196111f8e9b34e48242260b4c5106b9f3c2a5199f2ca |
| Mel U17/F16 원본 | f6476ace9b81e9b5f19da0cec2e10293ed2cc3387827d683862685f93b1c3dfc |
| DCT S31/F30 원본 | 4dec3238e0f28849db242c78e689dd89574fa1a23d4216255aa2b5190907f6e2 |

C fixture hash는 발행 계약 hash와 다르다. 정수 정답을 C 파일 배치와 float64 진단 참조에 연결하는 로컬 manifest다. 각 PC 실행 및 ARM ELF의 출력에는 버전과 fixture hash를 넣고, ARM은 발행 contract hash도 출력한다. 계수는 발행 정수값을 그대로 literal로 내보내며 수식으로 다시 생성하지 않는다.

## 정수 구현과 범위

| 단계 | 구현된 계약 |
|---|---|
| PCM/pre-emphasis | PCM16, clip 시작 previous=0. RNE((20*x−19*previous)*32768/20) → S32/F30. 모든 PCM에서 previous 갱신 |
| framing | 512샘플 최초 프레임, 이후160샘플마다 출력. 시작0,160,…; 중복 pre-emphasis 없음. 미완성 꼬리는 상태만 갱신하고 finish에서 패딩하지 않음 |
| window | 정확한 U31/F30 512계수, S32×U31의 정수 곱을 한 번 RNE >>30 → S32/F30 |
| BFP | window peak에서 반올림 전 목표31949와 비교. s=24…−2 순서의 첫 적합값, 무음 s=0. 최대지수 선택/fallback clamp 상태 포함 |
| FFT 입력 | RNE(window / 2^(16−s)), 음수 shift는 안전한 int64 곱셈. 대칭 [−32767,32767] clamp와 clip 수 |
| FFT 승격/구조 | S16/F15 ×16 → S20/F19. 기존 R2² block-array O(N log N), group 길이512/128/32/8 및 마지막 radix-2, 9비트 reversal로 전체512 자연순서 |
| FFT butterfly | Type-I21 → Type-II22 → twiddle16/F15 full complex 곱 → RNE >>15/wrap22 → RNE >>2/wrap20; 마지막 RNE >>1/wrap20. 물리 축소 S=9 |
| Power | 257-bin re²+im², U40. P=integer×2^(−27−2s) |
| Mel | 정확한 26×257 U17/F16 계수, U60 합. E=integer×2^(−43−2s) |
| floor/log | 정확한 floor 비교, 정수 bit-length/정규화 및 30회 반복 제곱의 log2 근사. ln2 정수744261118로 변환하여 S30/F24, floor 코드−463571610 |
| DCT/raw13 | 13×26 S31/F30 계수. 각 S30×S31 곱과 순차 S64 누산을 검사, 마지막 한 번 RNE >>30 → S40/F24, C0…C12 |

런타임에는 float/double, log/logf, 부동소수점 FFT, int128을 사용하지 않는다. 코어에서 파일 I/O·타이머·동적 할당을 분리했다. 상태, 작업 공간, 결과와 계수표는 호출자가 제공한다. cf_full_reset / cf_full_push / cf_full_finish로 사용하며 클립별 최대 샘플 수는 UINT32_MAX다. finish 뒤 push는 거부하고 reset으로 재시작한다. 범위 오류는 reset까지 유지한다. FFT wrap의 sticky overflow는 hard error와 별도다. 지원 범위는 이 v2 계약과 현재 폭에 한정된다.

전처리 분자 절댓값은 보수적으로1,277,952 이하이며 ×32768은 S37, window 곱은 S63 안에 든다. FFT 복소 곱의 절댓값은2^37 이하, Power는2^39 이하이다. Mel 행 합 최대1,540,096에 의해 누산은2^39×1540096 < 2^60이다. DCT는 저장 형식과 논리 폭을 별도 검사한다. 정확한 v2 계수와 log 출력 범위의 절댓값 누산 상한은2,538,068,713,882,680,420 < 2^62이며 매 덧셈에 signed64 overflow 검출도 수행한다.

### int64만 사용하는 넓은 log 변환

a=log2_q30은 논리 S37이며 a×744261118은 S67이므로 int64에 직접 곱하지 않는다. 인계 문서의 다음 분해를 구현했다.

~~~text
L = 744261118
h = floor(a/64), l = a−64*h                # 0 <= l < 64
p = l*L
b = h*L + floor(p/64)
q = floor(b/2^30), r = b−q*2^30
q += (r > 2^29) or
     (r == 2^29 and ((p mod 64) != 0 or q is odd))
~~~

h×L의 절댓값은2^60 미만, p<64×L이며 모두 int64 안에 든다. 음수 나눗셈은 floor quotient와 양의 remainder로 명시적으로 보정한다. r만 보아 tie를 판정하면 빠지는 하위6비트도 검사한다.

floor 비교에서도 T×10^12나2^91을 uint64에 직접 만들지 않는다. 10^12=244140625×2^12를 이용해 floor(2^(−e−12)/244140625)를 계산하고, 큰 지수는 base2^32로 분해한다. 나머지×2^32는2^60 미만이다. 이 threshold가 계약의 정확한 binary64 유리수와 같은 판정을 한다는 점을 독립 검증했다.

## PC 비트 비교와 경계 시험

발행 snapshot Python을 다시 실행하여 24개 case의 1,586,734개 발행 정수 성분을 먼저 확인했다. C는 개발1개534프레임, 기존 합성17개40프레임, 추가 합성6개42프레임을 모두 검사한다. 총107,102 PCM, 616프레임이며 0/511샘플 등 프레임이 없는 case도 포함한다. 평가20은 사용하지 않았다.

세 빌드는 MSVC19.42.34435 C11 /O2, MSVC /Od /RTC1, Vitis 번들 Clang14 C11 /O2 및 UBSan이다. 각 빌드에서 다음5가지 입력 방식을 모두 검사했다: 4096개 청크, 단일 샘플, 불규칙 청크, 173샘플 주입 후 reset, 600샘플 주입 후 reset. 총15개 실행 모두 불일치0이다.

| 한 빌드·한 입력 방식의 비교 경계 | 정수 값 수 | 불일치 |
|---|---:|---:|
| 전체 PCM pre-emphasis | 107,102 | 0 |
| frame/case/start/s/복원 지수/overflow/clip/clamp metadata | 6,776 | 0 |
| window | 315,392 | 0 |
| FFT 입력 | 315,392 | 0 |
| FFT 실수 전체512 | 315,392 | 0 |
| FFT 허수 전체512 | 315,392 | 0 |
| Power257 | 158,312 | 0 |
| Mel26 | 16,016 | 0 |
| log26 | 16,016 | 0 |
| floor26 | 16,016 | 0 |
| raw13 | 8,008 | 0 |
| clip 끝 상태·ring512·previous PCM·countdown·finish | 12,504 | 0 |

정상616프레임의 FFT overflow, input clip, BFP clamp는 모두0이다. 이 정상 입력에서 플래그가0이라는 사실만으로 경계 동작을 검증했다고 주장하지 않는다. 세 빌드마다 별도 코어 경계·오류·상태 시험1,493개와 호스트 잘린 입력/잘못된 개수/기존 출력 보존 등10개 검사를 통과했다. 변경하지 않은 v1 정수 도구의9,280,281개 검사 결과는 이전 실행으로 보존한다.

위 표는 한 실행 방식당1,602,318개 비교이며 세 빌드×다섯 방식 총24,034,770개가 모두 일치했다. 발행 경계 벡터 log214·BFP75·전처리8·DCT3 및 잘못된 도메인7개도 세 빌드에서 각각39,910개 assertion을 통과했다. 이 보충 실행은 published_boundaries_01/report.json에 있다. 전체616프레임 검증 뒤 미래 runner에 경계 검사를 자동 연결했으며, 기존02 snapshot·결과는 보존하고 현재 runner hash를 보충 보고서에 별도 기록했다. 코어·호스트·단위 시험 C는02와 동일하다.

독립 log 산술 oracle은 D:/2610_MFCC/build/c_fixed_v2_20261004_math_02에 보존했다. Python 무한 정밀도 a×L/2^36의 직접 RNE와 C 분해를 비교하여 MSVC O2 및 Clang UBSan에서 각각260,761개가 일치했다. signed64 범위를 넘는 곱207,626개, 정확한 half tie4개, 하위6비트가 있는 tie 직후124개, tie 직전124개를 포함한다. 모든27개 exponent grid의 floor25,001개도 정확한 binary64 유리수와 일치했다. 코어 hash는 전체 PCM 검사와 동일하다.

## float64 대비 수치 오차

다음은 **전체 정수 C 출력**을 호스트에서 실수로 복원하여 측정한 값이다. 동일 PCM hash를 갖는 불변 prec_04_verified_full_20261004의 float64 기준 배열 및 계수 snapshot을 사용했다. 참조 파일별 hash는 이번 fixture에 기록했다. 이전 v1의 float64 log/DCT를 붙인 혼합 경로 결과와 구분한다.

| 단계 | 개발 max | 개발 RMSE | 전체 합성 max | 전체 합성 RMSE |
|---|---:|---:|---:|---:|
| pre-emphasis | 3.72529e-10 | 2.62925e-10 | 3.72529e-10 | 2.03378e-10 |
| window | 9.13656e-10 | 3.16278e-10 | 1.39683e-9 | 2.81919e-10 |
| FFT | 0.00174385535 | 0.000159349931 | 0.0667246246 | 0.000856498955 |
| Power | 0.000121793210 | 0.00000105795460 | 0.140277426 | 0.00165994065 |
| Mel | 0.000143614943 | 0.00000464967454 | 0.00131900965 | 0.0000912361841 |
| log | 0.142329020 | 0.00513037929 | 7.442586449 | 0.661441537 |
| raw13 | 0.05517150245522062 | 0.0062602447152708155 | 6.591594298124889 | 0.8940570954424045 |

수치 FFT 오차 표는 float64 참조가 있는257-bin 기준이며, 위의 비트 비교는 전체512-bin이다. 합성 floor 회귀는60개, 개발은0개다. floor 회귀는 float64 Mel이 floor보다 큰데 fixed Mel이 floor 아래가 된 cell로 집계한다. case별/계수별 raw13 max/RMSE와 단계별 값은 numerical_errors.json에 남겼다. 발행 numerical_evidence의 진단 값과도 일치한다. 호스트 진단 일관성 점검의1e-10은 알고리즘 허용치가 아니다. 수치 수락 기준은 이 세션에서 정하지 않았다.

## ARM cross-build와 메모리

기존 PS-only XSA/BSP를 hash 확인 후 별도 실행 폴더에 복사하여 재사용했다.

- XSA: arm_platform/reproduce_01_platform/design/zybo_z7_20_ps.xsa, SHA-256 8a721765b6b460fb899a98014105836b92fd76f8081e588dc8637ee078ecc1e6.
- BSP: arm_platform/reproduce_01_apps. 원본·기존 프로젝트·ELF에 쓰지 않았다.
- Vitis2024.2 arm-none-eabi-gcc13.3, Cortex-A9, 기존 hard-float BSP ABI, -O2 -fno-tree-vectorize -fno-lto 및 엄격 경고.
- 코어 세 번역 단위에는 -mgeneral-regs-only를 추가했다. 정수 코어의 VFP/NEON 명령과 부동소수점/int128 helper는0개다. BSP startup 전체가 이 조건을 만족한다고 주장하지 않는다.

| ELF | SHA-256 |
|---|---|
| arm_full_02/binaries/c_fixed_full_validate.elf | e7023f9ea3ce5e00fc36afe112d1712c6af074156b99379085112b89f053f72e |
| arm_full_02/binaries/c_fixed_full_timing.elf | 9b9b4a85d9eb7c39586cb16a2941eb819ed5d69bff625d1a79dd1b64c4b90e08 |

정확도 ELF에는 개발 PCM85,920개와 정답534프레임 raw13/metadata가 들어간다. 전체 PCM 전처리 dump, 선택한 프레임의 window/FFT512/Power/Mel/log/floor UART dump 및 debugger 배열 회수가 가능하다. 시간 ELF의 측정 구간은 reset+전체 PCM push+raw13/metadata 저장이다. context 초기화, 비교/checksum, UART 및 cache flush는 구간 밖이다. 3회 warmup과30회 반복, timer read overhead 기록을 준비했으며 실제 측정하지 않았다.

| 객체/범위 | bytes |
|---|---:|
| 불변 spectral 계수 | 30,824 |
| 불변 window/DCT 계수 | 3,400 |
| cf_full_state | 2,080 |
| cf_full_workspace | 9,216 |
| cf_full_output | 9,784 |
| cf_full_context | ARM16 / PC x64 32 |
| ARM 코어 가변 객체 합 | 21,096 |
| 내장 개발 PCM | 171,840 |
| 내장 raw13 정답 / metadata 정답 | 55,536 / 17,088 |
| ARM 결과534프레임 | 72,624 |
| 정확도용 전체 pre-emphasis / 선택 trace | 343,680 / 9,784 |
| 정확도 ELF load 범위 | 0x00100000…0x001d0e10, 855,568 |
| 시간 ELF load 범위 | 0x00100000…0x0017a9c0, 502,208 |

load 범위는 배열 외 BSP·코드·stack 등 예약도 포함하며 마지막 주소는 exclusive다. GCC .su 개별 함수 stack 최대는 기존 cf_process176 bytes, full DCT88, full push80, log40이다. 이를 전체 호출 깊이 high-water로 해석하지 않는다. linker main stack은65,536 bytes, 예외 stack 포함71,680 bytes를 예약했다.

최종 full_audit.py 감사는 PC732개·ARM416개·발행 경계25개·독립 log22개 artifact hash와 원본/복사 발행 계약 각각437개를 확인했다. 실제 두 ELF의 계수34,224 bytes, PCM171,840 bytes, 기대 raw13/metadata72,624 bytes도 원본 byte와 일치했다. 정상 C 출력의 Mel16,016개를 정확한 유리수로 다시 검사하여 floor825개(양수 Mel179개)의 bit/log code 불일치0을 확인했다. v1 PC257개·ARM402개, 기존 소스29개의 hash도 그대로다. 보고서는 D:/2610_MFCC/build/c_fixed_v2_20261004_scopeaudit_01/final_audit.json이다.

FFT는 비트정확 이식을 위한 scalar block-array 방식이다. 최적화된 ARM DSP/NEON 구현을 대표하지 않는다. fixed가 float32보다 빠르다는 주장은 하지 않는다. 보드 실행과 같은 범위의 실제 timing 없이 성능을 비교할 수 없다.

## 보존한 실패와 이전 실행

- c_fixed_v2_20261004_01: full core 단위 시험 뒤 호스트 CLI 인자 개수 오류로 중단. 인자 처리를 수정하여 새02에서 전체 경로 재검증.
- c_fixed_v2_20261004_math_01: MSVC 환경의 PATH/Path 중복 선택 오류로 컴파일러 탐색 실패. 새 math_02에서 수정 후 두 컴파일러 통과.
- 현재02/arm_full_01: 컴파일은 끝났으나 GCC가 정수 DCT 상수 보관에 사용한 VFP load/store/save 명령4개를 보수적 검사에서 거부하여 링크 전 중단. 부동소수점 산술 또는 수치 실패가 아니다. 일반 레지스터 제한을 명시하여 새 arm_full_02에서 통과.
- v1 최종 c_fixed_20261004_03 및 arm_02는 그대로 보존했다. v1은675프레임 FFT→Power→Mel 이식이며 전체 정수 PCM→MFCC 결과로 소급 해석하지 않는다.
- 갱신 전 v1 결과·인계 문서는 c_fixed_v2_20261004_scopeaudit_01에 보관했다. 공용 README/AGENTS, 다른 세션의 파일을 수정하지 않았다. 커밋·푸시는 하지 않았다.

## 파일과 재실행

추가 코어는 software/c_fixed/mfcc_fixed_full.c/.h, PC 경계는 verification/c_fixed/full_host_main.c 및 test_full_core.c, 기준 검사는 full_reference.py다. full_math_oracle.py/full_math_probe.c는 독립 log 검증, full_audit.py는 계약·snapshot·ELF 실제 byte·기존 파일 보존 감사다. ARM 상세 사용법은 software/arm_fixed/README_full.md를 따른다.

아래 ID는 예시이며 기존 폴더가 있으면 반드시 새 ID를 쓴다. 실행은 이미 존재하는 폴더를 덮어쓰지 않는다.

~~~powershell
Set-Location D:\2610_MFCC\project
$py = 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe'
& $py scripts/run_c_fixed_full.py --run-id c_fixed_v2_20261004_repro01 --published-contract D:\2610_MFCC\build\fixed_contract\v2_pcm16_mfcc40_20261004_r2
& $py scripts/build_c_fixed_full_arm.py --run-dir D:\2610_MFCC\build\c_fixed_v2_20261004_repro01 --arm-id arm_full_01
& $py verification/c_fixed/full_math_oracle.py --out D:\2610_MFCC\build\c_fixed_v2_20261004_math_repro01 --contract D:\2610_MFCC\build\fixed_contract\v2_pcm16_mfcc40_20261004_r2
~~~

run_c_fixed_full.py는 새 실행에만 준비/검증하며 --prepare-only 뒤 --verify로 분리할 수도 있다. --verify는 아직 검증하지 않은 prepared 실행에 한 번 사용한다. compiler command·source snapshot·hash·case 순서·단계 dump·수치 오차를 각 실행에 저장한다. 보드 다운로드 명령은 포함하지 않는다. 남은 수락/보드 작업은 [C_FIXED_HANDOFF.md](C_FIXED_HANDOFF.md)에 기록했다.
