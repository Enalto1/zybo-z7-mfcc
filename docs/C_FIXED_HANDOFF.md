# ARM fixed C 인계와 남은 검증

갱신: 2026-10-04 KST. **v2 계약의 PCM16→raw13 전체 정수 C 구현과 PC 비트 검증, ARM cross-build를 완료했다.** 이전 v1 문서의 “전처리·window·정수 BFP·log·DCT 계약 미발행” 상태는 v2 발행과 이번 구현으로 해소됐다. 현재 남은 일은 알고리즘 수치 수락 판단과 실제 보드 검증·동등한 범위의 성능 비교다. 보드 실행은 사용자 지시로 계속 보류한다.

## 적용한 고정 기준과 완료 범위

- 발행 계약: D:/2610_MFCC/build/fixed_contract/v2_pcm16_mfcc40_20261004_r2.
- 발행 contract SHA-256: 283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e.
- C 실행: D:/2610_MFCC/build/c_fixed_v2_20261004_02.
- C fixture SHA-256: 04b81c51b963cbc4147465fc05c62e6c6f110d2f569f4a3058dec9f2ffadc94e.
- ARM 빌드: 위 실행의 arm_full_02/binaries/c_fixed_full_validate.elf 및 c_fixed_full_timing.elf.
- 넓은 log 독립 시험: D:/2610_MFCC/build/c_fixed_v2_20261004_math_02.
- 발행 경계 보충 시험: C 실행의 published_boundaries_01.
- 최종 보존/ELF 감사: D:/2610_MFCC/build/c_fixed_v2_20261004_scopeaudit_01/final_audit.json.

연속 pre-emphasis, 512/160 완성 프레임, window, 정수 BFP 선택, 입력 requantization, 전체512-bin FFT, Power257, Mel26, floor/log, DCT raw13을 구현했다. previous PCM은 버려지는 꼬리에서도 갱신한다. 24개 case/107,102 PCM/616프레임을 세 컴파일러 설정과 다섯 chunk/reset 방식으로 비교했고, 방식당1,602,318개 정수·상태 값의 불일치는0이다. 0/511샘플 입력, finish 후 입력 거부, reset, 오류의 sticky 처리까지 검사했다.

정수 도구와 FFT/Power/Mel은 v1 파일을 그대로 사용한다. full API는 software/c_fixed/mfcc_fixed_full.h, 구현은 같은 이름의 .c다. caller가 context/state/workspace/output을 보유하고 cf_full_reset, cf_full_push, cf_full_finish를 호출한다. raw13은 int64_t 저장이지만 논리 S40/F24이고 복원 배율은2^−24다. clip 길이는 metadata 범위를 보존하기 위해 UINT32_MAX 이하로 제한한다.

PCM부터 raw13까지의 산술 규칙에 현재 미발행 항목은 없다. C에서 근사·폭·계수·반올림을 추가로 정하지 않았다. coefficient 생성과 float64 오차 계산은 호스트 도구에만 존재한다.

## 포팅에서 반드시 유지할 산술 조건

| 항목 | 유지할 조건 |
|---|---|
| 전처리/상태 | alpha=19/20의 정확한 정수 연산과 RNE, previous=0 reset, 모든 PCM에서 previous 갱신. 청크나 프레임 경계에서 다시 시작하지 않음 |
| framing/window | N512/H160, 미완성 EOF는 출력하지 않음. window U31/F30 정수표 그대로 사용하고 곱 뒤 한 번 RNE >>30 |
| BFP | peak의 안전한 절댓값, 목표31949의 반올림 전 비교, s∈[−2,24], 무음0, 최대지수/fallback clamp 플래그. 입력은 [−32767,32767] 대칭 clamp |
| FFT | S16→S20 승격, R2² butterfly·twiddle 곱·round/wrap 순서 및 natural512 순서 유지. float FFT 마지막 round나 DFT로 대체하지 않음 |
| Power/Mel | 저장 uint64와 논리 U40/U60 구분. Power 지수−27−2s, Mel 지수−43−2s. 원본 Mel 계수 및 순서 유지 |
| floor | Mel grid의 정확한 binary64 floor 판정. T×10^12 또는2^91을 uint64로 직접 만들지 않음 |
| log | 정규화된 U32 mantissa의 U64 제곱30회, S37 log2, 최종 S30/F24. S67 a×744261118을 int64로 직접 곱하지 않음 |
| 넓은 log 곱 | h=floor(a/64), l=a−64h의 정확한 분해 및 하위6 product bit를 포함한 RNE. 음수 C 나눗셈을 floor로 보정 |
| DCT | S30/F24×S31/F30, checked S64 누산, 전체26항 이후 한 번 RNE >>30, S40/F24 raw13 |
| 오류 | FFT wrap은 결과+sticky overflow, 그 외 지원 범위 이탈은 명시적 오류. C 저장 폭에서 우연히 통과한다고 논리 폭을 늘리지 않음 |

넓은 log 분해는 signed37 전체 도메인의260,761개를 Python 무한 정밀도 직접 곱과 비교했고, int64를 넘는 원래 곱207,626개와 음수 tie를 포함한다. 별도 floor25,001개도 통과했다. 발행 log214·BFP75·전처리8·DCT3과 잘못된 도메인7개는 세 빌드에서 각각39,910개 assertion으로 검사했다. Python 기준을 C에 맞춰 고치지 않았다.

최종 감사는 실제 C 출력의16,016개 Mel cell도 정확한 유리수와 대조했다. floor825개 중 양수 Mel179개를 포함해 floor bit/log code 불일치는0이다. 이는 아래 “float64 대비 floor 회귀60개”와 다른 집계다.

## 미완료 사항과 수치 담당자에게 남길 항목

| 항목 | 현재 상태와 필요한 후속 |
|---|---|
| 알고리즘 정확도 수락 | NOT_ACCEPTED. 발행 기준도 허용치 미정이며 이 세션에서 변경하지 않음. 단계별/raw13 max·RMSE 및 floor 회귀에 대한 수락 기준·오차 예산은 수치 담당자가 동결해야 함 |
| 합성 floor 회귀 | 개발0, 전체 합성60. raw13 합성 max6.591594298124889 / RMSE0.8940570954424045. 포팅 불일치가 아니며 수치 설계 문제를 C 쪽 보정으로 숨기지 않음 |
| 개발 수치 오차 | max0.05517150245522062 / RMSE0.0062602447152708155. 실제 전체 정수 C output에서 측정했으며 발행 진단과 일치 |
| 평가20 | 이번 후보 개발·튜닝·검증에 사용하지 않음. 개발 조건·계약·수락 기준 동결 이후 별도 실행 필요 |
| ARM 보드 정확도 | ELF만 생성. Cortex-A9에서 명령을 실행하거나 UART/JTAG 값을 회수하지 않았으므로 ARM 비트정확 통과는 아직 없음 |
| ARM 성능 | timing ELF만 준비. 실제 ticks, cache/clock 확인, 분포/반복 측정은 미실시 |
| FPGA 및 3자 비교 | RTL은 다른 세션 소관. 이 C 세션은 RTL이나 FPGA 실행을 검증했다고 주장하지 않음. ARM float32/ARM fixed/FPGA fixed를 동일 PCM·출력·측정 경계로 연결하는 후속 작업 필요 |

공통 규격/설계 이력은 직접 수정하지 않았다. 문서 소유 세션에는 v2 contract ID와 hash로 전체 정수 경로를 참조하고, 과거 FFT16 규격·v1 부분 경로·v2 FFT20 전체 경로를 구분할 것을 제안한다. 후보가 바뀌면 새 버전을 발행하고 영향받는 단계·지수·상태·전체512 FFT·raw13 및 오차를 새 C 실행에서 검증해야 한다. 이미 보존된 v1/v2 실행의 기준을 소급 변경하지 않는다.

## ARM 준비 범위와 다음 실행의 경계

Vitis2024.2 GCC13.3으로 PC 통과 snapshot의 core/계수/개발 벡터를 빌드했다. 원래 PS-only XSA/BSP는 hash 확인 후 새 ARM 실행에 복사했다. 기존 software/arm 프로젝트와 ELF는 바꾸지 않았다.

현재 내장 ARM 입력은 개발 음성85,920 PCM 전체이며 기대값은534프레임 raw13와 지수/상태 metadata다. PC의 전체24 case가 ARM ELF에 모두 내장된 것은 아니다. 추후 보드 검증은 이 전체 개발 clip을 먼저 실행한 뒤 합성 case를 별도 fixture로 공급·회수하여 확대할 수 있다.

정확도 경로는 전체 pre-emphasis 배열, raw13/metadata 전부, 선택한 프레임의 window/FFT입력/FFT512/Power/Mel/log/floor를 UART 또는 debugger로 회수하도록 준비했다. 시간 경로는 reset+85,920 PCM 처리+raw13/metadata 저장을 측정하며 context 초기화, 비교/checksum, UART, cache flush는 구간 밖이다. warmup3회/반복30회와 timer overhead 기록은 준비 상태다. 실제 보드 접근·다운로드·실행 명령은 이번에 수행하지 않았다.

ARM scalar -O2, -fno-tree-vectorize, -fno-lto를 적용했고 세 정수 코어 파일에는 -mgeneral-regs-only를 더했다. 이전 첫 ARM 시도에서 정수 DCT 상수 이동에 VFP 레지스터를 사용한 것을 보수적 검사에서 검출했기 때문이다. 최종 코어에는 VFP/NEON 및 FP/int128 helper가 없다. hard-float는 재사용 BSP ABI의 이름이며 코어 부동소수점 계산을 뜻하지 않는다. 이 구현을 최적화된 ARM fixed의 대표값으로 보거나 float32보다 빠르다고 가정하지 않는다.

## 보존·검증 자료와 재실행

최종 감사는 PC732개, ARM416개, 경계25개, 독립 log22개 artifact hash를 확인했다. v1 PC257개·ARM402개와 시작 시 기록한 기존 파일29개 hash는 그대로다. 실제 두 ELF의 spectral/window/DCT 계수34,224 bytes, 개발 PCM171,840 bytes, 기대 raw13/metadata72,624 bytes가 원본 정수 byte와 일치한다.

전체616프레임 검증 후 미래 runner에 발행 경계 시험 자동 호출을 추가했다. 02의 snapshot·결과·index는 수정하지 않았고, 현재 runner hash는 published_boundaries_01/report.json의 build_helper_sha256으로 별도 연결했다. 기존 snapshot 대비 바뀐 파일은 scripts/run_c_fixed_full.py의 이 보충 검증 orchestration뿐이다. 코어·호스트·코어 단위 시험 C는02와 동일하다.

재실행 명령과 메모리 표는 [C_FIXED_RESULTS.md](C_FIXED_RESULTS.md)를 따른다. 보충 경계만 다시 검사하려면 새 보충 ID를 사용한다.

~~~powershell
Set-Location D:\2610_MFCC\project
$py = 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe'
& $py -B verification/c_fixed/full_published_boundaries.py D:\2610_MFCC\build\c_fixed_v2_20261004_02 --id published_boundaries_repro01
& $py -B verification/c_fixed/full_audit.py --run D:\2610_MFCC\build\c_fixed_v2_20261004_02 --arm-id arm_full_02 --math-run D:\2610_MFCC\build\c_fixed_v2_20261004_math_02 --before D:\2610_MFCC\build\c_fixed_v2_20261004_scopeaudit_01\before.json --v1-run D:\2610_MFCC\build\c_fixed_20261004_03 --report D:\2610_MFCC\build\c_fixed_v2_20261004_scopeaudit_01\audit_repro01.json
~~~

실패 실행01, math_01, arm_full_01과 이전 v1 실행을 지우지 않았다. 수정 범위는 새 c_fixed/arm_fixed 관련 코드·전용 스크립트와 두 C_FIXED 문서다. 공용 README/AGENTS, Python 모델, RTL, 발행 계약, 기존 float32 C/ARM은 이 작업에서 수정하지 않았다. 논문·발표 파일을 생성하지 않았으며 커밋·푸시도 하지 않았다.
