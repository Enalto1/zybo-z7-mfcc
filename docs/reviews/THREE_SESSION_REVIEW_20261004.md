# MFCC 세 세션 산출물 교차 점검

점검일: 2026-10-04 KST. 대상 저장소: `D:/2610_MFCC/project`.

세 세션의 완료 보고를 소스·고정 계약·실제 출력·시뮬레이션 및 배선 보고서와 대조했다. 점검한 지원 범위에서 새로 재현되는 중대 계산/RTL 결함이나 허위 완료 주장은 발견하지 못했다. **전체 시스템 완료는 아니다.** 고정소수점 C의 전체 경로 확장, 남은 수치 오차의 판단, 부동소수점 전체 클립 연속 검증, 실제 ARM/PL 통합·실행·측정이 남아 있다.

이번 점검은 소스 수정, 새 Vivado 합성·배치배선, 보드 실행을 하지 않았다. 아래 ‘재확인’은 저장 산출물 감사, 정수 모델 재계산, 일부 C 실행파일 재실행 범위를 뜻한다. 논문·발표 원고가 아닌 개발 점검 기록이다.

## 세션과 완료 범위

| 세션 이름 | 확인된 산출물 | 아직 완료하지 않은 것 |
|---|---|---|
| MFCC 자료 조사와 규격 초안 작성 | float32 IP의 PCM→raw13 RTL, BRAM 프레이머, 개발534프레임 분할 수치 검증, 합성 입력17개 검증, OOC 배치배선 | 합성5개 수치 실패, 단일 전체 클립 MFCC 연속 검증, PS/PL 통합 및 보드 실측 |
| 고정소수점 FFT 실험 이어가기 | 전체 Python 정수 모델, PCM→raw13 RTL616프레임 비트 검증, 전체 v2 계약, OOC 배치배선 | float64 대비 정확도 수락, 응용 성능, 보드 실행 및 post-route timing simulation |
| Implement ARM fixed-point MFCC C | v1 FFT→Power→Mel C, PC3빌드675프레임 비트 비교, 별도 ARM ELF2개 | v2 PCM→raw13 전체 경로, ARM 실제 수치 검증과 성능 측정 |

세 세션이 idle/completed인 것을 앱에서 확인했다. 완료한 작업 범위는 서로 다르다. 보고서에 제한 사항이 명시돼 있어 부분 구현을 전체 완료로 속인 근거는 없다.

## 우선순위가 높은 미완료 사항

### 1. C와 RTL의 수치 계약 및 처리 범위가 다르다

[C 결과](../C_FIXED_RESULTS.md)와 [C 인계](../C_FIXED_HANDOFF.md)는 `v1_fft20_power40_mel60_20261004_r2`를 사용한다. 입력은 이미 양자화된 복소512개와 BFP 지수이고, 출력은 FFT512/Power257/Mel26이다. PCM 전처리·window·BFP 선택·log·DCT는 C에 없다.

전체 RTL의 현재 인계본은 `D:/2610_MFCC/build/fixed_contract/v2_pcm16_mfcc40_20261004_r2`이며 PCM부터13개 MFCC까지 포함한다. contract SHA-256은 `283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e`다. [전체 포팅 안내](FIXED_POINT_PORTING_NOTES.md)가 제공돼 있으므로 이제 C 확장을 진행할 수 있다.

동일 개발 PCM의 v1/v2 FFT 입력을 직접 비교한 결과273,408개 정수 중216개가 달랐고95개 프레임에 걸쳐 있었다. v1 실수 전처리와 v2 정수 전처리의 차이이며 C 계산 오류가 아니다. 따라서 기존 C의 v1 통과를 v2 전체 RTL과의 비트 일치로 대신할 수 없다. 공통 FFT 코어는 재사용하되 v2 입력과 전후처리를 포함해 새 실행에서 검증해야 한다.

C 작업은 전체 v2 발행 전의 부분 계약에 따라 끝났으므로 과거 결과를 잘못된 것으로 폐기할 필요는 없다. v1 실행을 보존하고 새 버전으로 확장한다. ARM ELF의 현재 내장 검증 입력도 개발 첫 프레임1개뿐이다.

### 2. 수치 정확도와 비트 일치는 별개의 문제다

FP32는 합성17개 중12개가 모든 단계 허용치를 통과했고5개가 실패했다. `composite_500_2237hz`는 log-Mel만 실패하고, DC±·fullscale alternating·1kHz tone은 최종 MFCC도 실패했다. DC±와 composite는 기존 C float32에서는 통과했던 입력이므로 기존 C의 알려진2개 실패와 동일하게 취급하면 안 된다. [FP32 상세 결과](../FP32_HARDWARE_RESULTS.md)의 원인 분석은 앞단의 작은 Mel 에너지 차이가 로그에서 확대되는 것을 보여준다.

전체 고정소수점은 정수 모델과 RTL 값이 일치하지만, float64 대비 합성 최대 오차6.591594298·RMSE0.894057095와 floor 회귀60건이 남았다. 전체 수치 허용 기준이 아직 미정이라 `NOT_ACCEPTED` 상태다. 이를 명시된 허용치의 합격으로 표현하지 않으며, 해당 상태 자체를 RTL 구현 불일치라고 해석하지도 않는다.

실패 입력을 제외하거나 결과에 맞춰 허용치를 완화하지 않는다. 오차 원인·목표·후속 응용에 필요한 정확도를 정리하고, 평가20개를 사용하기 전에 수치 계약과 평가 기준을 고정한다. 평가 음성20개를 이미 fixed/FP32 HW로 검증했다고 주장할 근거는 없다.

### 3. 연속 동작과 실제 성능은 별도 검증이 필요하다

FP32의 개발534프레임은8개 구간의 실제 IP 시뮬레이터 결과를 검증·결합한 수치 증거다. 입력·준비 프레임·경계 중복의 처리는 재확인됐지만, `single_clip_protocol_passed=null`과 `single_clip_throughput_measured=false`는 유지해야 한다. 전체 길이 프레이머 단위 시험이 전체 MFCC의 연속 처리율 검증을 대신하지 않는다.

두 RTL 모두 OOC 결과다. FP32는 외부22입력/109출력 delay가 미제약인 내부 타이밍이고, fixed는100MHz·I/O2ns 조건이다. 둘 다 보드 핀·PS/PL·실제 시스템 clock 통합 완료를 뜻하지 않는다. 서로 다른 제약의 WNS로 Fmax 우열을 판단하지 않는다.

ARM float32와 fixed 모두 ELF 준비와 실제 실행을 구분한다. UART·DDR·수치 회수·계산시간·전송 포함 전체시간·전력의 보드 실측 결과는 아직 없다. 시뮬레이터 실행시간이나 인위적 stall을 포함한 검증 cycle을 보드 가속비로 바꾸지 않는다.

### 4. 공통 안내 문서의 현재 상태가 오래됐다

점검 시 [README](../../README.md)5·22행은 초기 준비 상태, [개발 절차](../FIXED_POINT_DEVELOPMENT.md)18행은 ‘현재 fixed C는 미구현’,106행은 ‘전체 정수 모델 미완성’으로 남아 있었다. [설계 이력](../FIXED_POINT_DESIGN_HISTORY.md)5행도 초기 상태인 반면 뒤쪽에는 전체 RTL 완료가 기록돼 있다. C 인계 문서의 ‘전체 계약이 없다’는 표현도 현재는 후속 v2 발행 사실을 추가할 대상이다.

현재 상태 요약과 역사적 기록을 구분해 갱신해야 한다. 실행 snapshot과 발행 계약의 과거 상태 필드는 증거이므로 소급 수정하지 않는다. 이번 점검에서는 기존 공통 문서를 수정하지 않았으며 본 보고서가 현재 교차 상태를 기록한다.

## 직접 재확인한 결과

| 점검 | 이번 확인 |
|---|---|
| v2 전체 계약 재계산 |7,107검사 PASS,24입력616프레임,1,586,734정수성분,437artifact hash,214log/75BFP 경계 |
| fixed RTL 산출물 감사 |3,065검사 PASS; full06의24입력616프레임 FULL_PIPELINE_PASS 로그, full07 동일성·실제 배선 보고서 확인 |
| 정수 모델·코딩 규약 |8개 unittest PASS(대량 산술 경계 루프 포함), RTL 규약 정적 검사 PASS |
| fixed C 실제 저장 출력 |MSVC/검사 MSVC/Clang3빌드 각각5,036,850정수 재비교, 불일치0 |
| fixed C 재실행 |기존 Clang/UBSan 실행파일로 정상·overflow 포함7프레임 재실행, exit0·진단 없음·불일치0 |
| C/ARM 산출물 |PC257/ARM402artifact hash 일치, 두 ELF의 실제 계수30,824bytes 각각 일치; 현재 소스와 검증 snapshot 일치 |
| FP32 산출물 |최종 합성 입력·분할 개발·배선의1,709artifact hash 일치, 현재6RTL과 snapshot 일치 |
| FP32 원시 데이터 재분석 |8worker trace를 재검증·결합하여10단계 저장 배열 bit 일치.534프레임/85,920샘플 소유권 각1회와7개 경계 일치 |
| FP32 수치 재계산 |10단계 HW−Python/HW−C 오차·위반 수 재계산, 개발 PASS·합성5실패 재확인 |

동일 개발 음성534프레임×13계수의 저장 배열에서 독립 재계산한 오차:

| 구현 | Python float64 대비 최대 절대 오차 | RMSE | 측정 범위 |
|---|---:|---:|---|
| PC C float32 |0.00001928850116|0.000003061621937|전체 MFCC, PC 결과 |
| FP32 HW |0.0002700975331|0.00002791054534|실제 IP RTL의 분할 재생 |
| 전체 fixed v2 |0.05517150246|0.006260244715|전체 정수 계약 출력; RTL 비트 일치 증거는 별도 감사 |

수치 기준 PCM SHA-256은 `026cae2934935a27911f88d7d6e0ddcc678ad433fb50fe764233a57e4bbcde32`로 v2와 공통 Python 입력이 일치한다. fixed C의 호스트 float log/DCT 진단은 전체 fixed C 출력이 아니므로 위 전체 MFCC 표의 별도 행으로 넣지 않았다.

## 현재 하드웨어 자원

| 배선 후 항목 | FP32 compact | Fixed 전체 |
|---|---:|---:|
| LUT |4,379|5,455|
| FF |7,801|2,391|
| DSP |26|40|
| BRAM36 환산 tile |8|12.5|
|100MHz WNS / WHS(ns)|+0.942 / +0.023|+0.506 / +0.097|

FP32의16RAMB18은8tile, fixed의9RAMB36+7RAMB18은12.5tile로 단위를 맞췄다. 현재 fixed 구현은 FF가 적지만 LUT·DSP·BRAM은 더 많다. 따라서 ‘고정소수점이 자원을 전반적으로 절감했다’는 결론은 현재 결과로 뒷받침되지 않는다. 연산 공유·병렬화·메모리 배치가 다른 두 설계의 결과이며 수치 표현만의 효과로 단정하지 않는다. 속도·전력의 우열도 아직 알 수 없다.

## 비차단 검증 보완 사항

- fixed 통합 TB의 진행 중 중단 시험은 `rst_n` reset이다. 계약이 약속한 busy/출력stall 중 `i_clip_start` abort를 별도 회귀로 보완할 수 있다. 현재 정상 경로를 깨는 결함을 재현한 것은 아니다.
- fixed의 frontend315,392개 비트 비교는 FFT 입력 q16·지수·metadata 경계다. 내부 pre-emphasis/window Q30 레지스터까지 직접 비교한 것으로 확대하지 않는다.
- FP32 ALU는 operand별 수락 상태를 구현하지만 실제 IP에서 A/B가 서로 다른 사이클에 수락되는 동적 coverage가0이었다. wrapper 단위 프로토콜 시험으로 보완한다.
- ARM 성능 측정 전 compiler/CPU ordering, 타이머 읽기 경계, 결과 저장 완료를 측정에 포함할지 명시하고 두 SW 비교군에 같은 방법을 적용한다. 현재 보드 측정값이 잘못됐다는 발견은 아니다.

## 다음 작업 순서

1. C 세션은 v2_r2 계약으로 전체 PCM→raw13 정수 경로를 추가하고 단계별 PC 비트 비교·ARM 재빌드를 한다. 기존 v1 core와 실행은 보존한다.
2. FP32 세션은 단일 전체 클립 통합 검증과5개 수치 실패의 후속 판단을 진행한다. fixed 세션은60개 floor 회귀 등 잔차와 정확도 기준을 정리하고 필요한 제어 coverage를 보완한다.
3. 공통 현재 상태 안내를 실제 결과와 동기화한다. 고정 계약/기준값/과거 실행을 소급 변경하지 않는다.
4. 보드 실행 작업을 재개할 때 같은 입력·처리 경계·측정 방법으로 ARM과 PL 결과를 회수한다. 커널 시간과 데이터 전송 포함 전체시간을 구분한다.

## 이번 감사 자료 위치

새 감사 자료는 `C:/Users/rlagk/Documents/Codex/2026-10-03/new-chat/audits/three_sessions_20261004/`에 저장했다.

- `full_contract_recheck.log`: 전체 v2 계약 재계산.
- `cross_session_numeric_audit.json`: 개발 MFCC 오차와 v1/v2 입력 차이의 독립 계산.
- `fixed/full_rtl_audit.json`: 고정 RTL·프로젝트·계약 산출물 감사.
- `fp32/saved_output_audit.json`: FP32 artifact·원시 trace·수치 재분석.
- `c_fixed/artifact_audit.json`, `c_fixed/independent_comparison.json`: C/ARM 감사와 C 재실행.

원래 결과는 [FP32 보고서](../FP32_HARDWARE_RESULTS.md), [fixed RTL 보고서](FIXED_POINT_RTL_IMPLEMENTATION.md), [fixed C 보고서](../C_FIXED_RESULTS.md)에 연결돼 있다. 새 감사는 소스 수정·새 하드웨어 검증·보드 실측을 대신하지 않는다.
