# 고정소수점 C/RTL 인계

새 C 구현이 사용할 고정 버전은 `D:/2610_MFCC/build/fixed_contract/v2_pcm16_mfcc40_20261004_r2`다. PCM부터 raw13 MFCC까지 전체 정수 계약이며 `contract.json`이 기계 판독 기준이다. 선행 `v1_fft20_power40_mel60_20261004_r2`에는 FFT16 입력의 복소 overflow/복구와 추가 경계 벡터가 있다. 두 버전 모두 `PUBLISHED.json`과 SHA-256 검사를 통과했다. C 이식 가능, float64 수치 정확도 합격, RTL 검증 완료는 별도 상태다.

이전 `v2_pcm16_mfcc40_20261004`는 `front.mel.coefficients.file` 한 곳에 실제 존재하지 않는 이전 버전 파일명이 남아 있었다. 원본은 보존했고 r2에서 경로를 정정했다. 수치 정책 변경은 없으며 `numerical_identity.json`이 이전426개 계수·벡터·모델·수치증거의 byte 일치를 확인한다. r2는 재귀 descriptor415개와437개 artifact hash를 포함해 발행 후7,107검사를 통과했다. 기계 판독 W/F·부호·guard와 BFP clamp flag,64비트 log 분해 설명도 추가했다. r2 contract SHA-256은 `283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e`다.

## 실행과 저장 형식

`model_snapshot/software/fixed_model/full_integer.py`의 `run_pcm(pcm,coefficients,twiddle)`을 기준으로 한다. PCM은 signed16 little-endian이며 frame512/hop160, preemphasis previous PCM은 clip 안에서 연속 유지한다. 마지막 불완전 frame은 제외하되 PCM 벡터와 0프레임 case는 보존한다. 결과의 FFT real/imag는 각각512개이며 Power는257개, Mel/log는26개, MFCC는13개다.

각 case의 `stages`에 파일·dtype·shape·SHA가 있다. 저장 자료형과 논리 폭은 다르다. 예를 들어 FFT의 int32 파일은 signed20, Mel의 uint64 파일은 unsigned60, MFCC의 int64 파일은 signed40/F24를 담는다. 비교는 실수로 환산하지 않고 정수·s·frame/index/last·overflow를 직접 대조한다. s는 signed8 범위−2..24이고 Power 실수 배율은 `2^(−27−2s)`, Mel 배율은 `2^(−43−2s)`다.

명목 F와 물리 배율을 구분한다. 기존 후보의 사전0.5배가 BFP 정의에 포함되어 FFT 입력 q16은 원래 window 값에 대해 `q16*2^(−14−s)`로 해석한다. 전체FFT 출력 코드에서 비축소 DFT로 돌아가는 배율은 `2^(−9−s)`다. 따라서 F15라는 이유로 입력을 `2^(−15−s)`로 복원하면 한 비트 배율 오류가 된다. r2 manifest의 `input_physical_exp2`와 `output_physical_exp2`를 따른다.

계수 표의 정수 코드가 기준이다. C 런타임에서 window/Mel/DCT 계수나 ln2를 float로 다시 만들지 않는다. floor는 manifest에 기록된 reference binary64 `1e-12`의 정확한 유리수다. 가능한27개 Mel 지수 격자에서는 `1/10^12` 정수 비교와 결과가 같음을 경계 벡터로 검증했다. 음수 에너지는 floor 대상이 아니라 오류다.

## C에서 조심할 연산

Python의 `<<`는 수학적 정수 스케일을 뜻한다. 음수 signed C 값의 왼쪽 shift를 그대로 사용하면 안 된다. 예를 들어 전처리는 충분히 넓은 signed64 곱셈으로 `(20*x-19*previous)*32768`을 만든 뒤20으로 ties-even 나눈다. 음수 right shift나 signed overflow의 컴파일러 동작에도 의존하지 않는다. FFT wrap은 unsigned mask와 명시적 부호 복원으로 구현한다.

정수 log의 ln2 상수744261118은 unsigned30/F30(또는 양수 signed31/F30)다. 계약의 log2 signed37와 곱 signed67은 유효 입력에서 필요한 보수적인 논리 폭이며, RTL은 추가 guard를 둔 signed40×signed31→signed71 임시 값을 사용한다. 추가 guard는 절삭/반올림을 바꾸지 않는다. window도 수학적 곱 signed63에 대해 RTL 임시 signed64를 사용한다. **log 최종 곱을 단순 int64 곱으로 계산하면 overflow할 수 있다.** DCT signed64 누산이 안전하다는 사실과 구분한다.

int128이 없는 ARM C에서는 다중 워드 곱 또는 다음 정확한 분해를 사용할 수 있다. `a`는 log2 Q30, `L=744261118`, 모든 floor division의 remainder는 비음수다.

1. `h=floor(a/64)`, `l=a−64*h` (0≤l<64).
2. `p=l*L`, `b=h*L+floor(p/64)`; 유효 입력에서 각 값은 signed64 안에 든다.
3. `q=floor(b/2^30)`, `r=b−q*2^30`.
4. `r>2^29` 또는 `r==2^29 && ((p mod64)!=0 || q가 홀수)`이면 `q+1`, 아니면 `q`.

이는 원래 넓은 곱의 `RNE(a*L/2^36)`와 같다. 음수 floor division은 C truncation division에서 remainder가 음수일 때 quotient를1 줄이고 remainder에 divisor를 더해 얻는다. 이 대안은 별도 구현 제안이며 계약의 정수 결과는 그대로다. `verification/fixed/full_integer/test_full_integer.py`의 독립10,008개 분해 검사가 근거다.

## 최종 RTL 검증 상태 — 2026-10-04

C에 인계한 `v2_pcm16_mfcc40_20261004_r2`는 변경하지 않았다. 발행 당시의 상태 파일도 그대로 보존하며, 이후 확보한 RTL 결과는 아래 실행과 외부 감사 결과로 연결한다. 현재 상태는 **C 이식 가능 / 전체 RTL 정수 비트 검증 완료 / OOC 구현·타이밍 통과 / 수치 정확도 NOT_ACCEPTED / 보드 NOT_RUN**이다. 이 상태는 별도 C 구현의 완료나 정확성을 대신 증명하지 않는다.

`D:/2610_MFCC/build/fixed_full_rtl/full_616_final_20261004_06`에서 24개 case, 616프레임을 끝까지 검사했다. Frontend 315,392개, 전체 512-bin FFT 315,392복소 값, Power 158,312개, Mel과 log 각각 16,016개, MFCC 8,008개가 Python 정수 모델과 일치했고 mismatch는 0이다. 입력 gap, 출력 stall 15,457사이클, 173 PCM 샘플을 받은 뒤의 reset, FFT 처리 중 reset, 모든 clip의 마지막 출력 배출과 완료 신호를 검사했다. 전체 실행은 14,410,168사이클이다.

최종 재현 프로젝트는 `D:/2610_MFCC/build/fixed_full_rtl/full_616_repro_20261004_07/project/fixed_full.xpr`다. 이 실행은 06의 성공한 시뮬레이션에 대해 RTL·testbench·모든 벡터의 동일성을 인증한 뒤 합성·배치·배선을 다시 수행했다. 계수 파일도 해당 실행의 source snapshot을 참조하도록 고정했다. Vivado 2024.2, xc7z020clg400-1, 100 MHz와 입출력 지연 2 ns의 OOC 결과는 WNS +0.506 ns, WHS +0.097 ns다. 보드 pin/PS가 통합된 타이밍 결과는 아니다.

배선 후 자원은 LUT 5,455개, FF 2,391개, DSP 40개, RAMB36 9개와 RAMB18 7개(12.5 BRAM tile)다. LUTRAM 894개와 SRL 65개는 모두 보존한 FFT 내부에 속한다. 신규 frontend/backend/tail에는 LUTRAM이 없고 신규 frame buffer의 BRAM 매핑을 확인했다.

외부 감사 `D:/2610_MFCC/build/fixed_full_rtl/full_616_repro_20261004_07_audit.json`은 3,065검사 PASS이며, 저장된 XPR의 26개 파일 참조도 포함한다. 프로젝트 저장소 `D:/2610_MFCC/project`에서 다음 명령으로 감사 결과를 새 파일에 재생성할 수 있다.

```powershell
python verification/fixed/audit_full_rtl.py --run D:/2610_MFCC/build/fixed_full_rtl/full_616_repro_20261004_07 --out D:/2610_MFCC/build/fixed_full_rtl/NEW_AUDIT.json
```

## 남은 판단

개발 음성534프레임과 합성23입력82프레임을 모두 유지했다. 전체 정수 모델의 개발 RMSE0.006260244715, 합성 최대6.591594298125/RMSE0.894057095442와 floor 회귀60건은 합격 판정이 아니다. 허용 오차와 응용 정확도 기준은 아직 정하지 않았다. 평가 음성20개는 후보 튜닝에 사용하지 않았다.

보드 연결·다운로드·실행은 보류 상태다. C/ARM 검증은 해당 작업자의 범위이고 이 문서는 그 결과를 추정하지 않는다. RTL은 [구현 검증 보고서](FIXED_POINT_RTL_IMPLEMENTATION.md)의 실제 실행과 snapshot hash에 연결한다.

보존된 `fft_bitmodel_wide.py`의 실험단계 경고는 작성 당시 상태를 나타낸다. 이번 RTL 증거는 정확히512점·내부20/F19·twiddle16/F15·S9 설정에 해당하며, 해당 모듈이 지원하는 다른 폭까지 RTL 검증됐다는 뜻은 아니다. 발행된 모델 snapshot과그hash는이설명때문에수정하지않는다.
