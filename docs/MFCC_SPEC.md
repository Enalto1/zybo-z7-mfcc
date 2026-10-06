# MFCC 공통 구현 규격 초안

작성: 2026-10-04, Asia/Seoul. 규격 ID 제안: `mfcc-raw13-v0.1-draft`.

이 문서는 네 비교군의 공통 수학 정의와 검증 상태를 관리한다. Python float64는 구현·검증을 완료했으며 [PYTHON_REFERENCE_RESULTS.md](PYTHON_REFERENCE_RESULTS.md)에 근거가 있다. 네 비교군 전체의 완료를 뜻하지 않는다. 실제 소스와 실행 점검은 [SOURCE_AUDIT.md](SOURCE_AUDIT.md)에 기록했다. 사용자 목표는 10월 6일 교수님 전달이며, 전달받은 일정은 **2026년 10월 7일 11:00 KST**이다. 둘을 같은 마감으로 취급하지 않는다.

사용자 추가 확정 조건: **특정 음성 파일을 공통 입력으로 사용하고, 목표 개발·검증 도구는 Vivado 2024.2로 한다.** 공통 음성은 LibriSpeech test-clean에서 확보한 21개이며, 개발용 1개와 별도 평가용 20개를 사용한다. 상세 출처와 샘플 해시는 아래 입력 명세를 따른다. 마이크/I2S/오디오 코덱 연동은 이번 구현 범위에서 제외한다. 저장 PCM을 사용하더라도 실제 ZYBO의 ARM 및 PL 실행·계수 회수가 목표이다. Vitis/ARM 도구 버전과 플랫폼 연동은 별도 검증 항목이다.

2024.2 직접 점검에서는 authored HDL 15개 구문 분석이 통과했고, Zynq 대상 raw13+입력변환 XCI9개 모두 locked/Upgrade IP 권고였다. 이는 IP 생성·MFCC simulation 통과가 아니다. 원본은 보존하고 후속 작업 복사본에서 업그레이드·재생성 및 회귀 검증한다.

## 1. 상태와 비교 경계

- **소스에서 확인**: 참고 파일의 연결·상수 또는 이번 정적 계산에서 확인했다. 전체 파이프라인의 기능 검증과 다르다.
- **설계 제안**: 이번 비교에 채택할 명시적인 선택이다. 구현 후 공통 벡터로 통과해야 확정할 수 있다.
- **미확인**: 실행·재현·결정 근거가 아직 없다. 수치나 성공 여부를 추정해 채우지 않는다.

**설계 제안 — 주 비교 출력은 정규직교 DCT-II의 13개 값 `C0, C1, …, C12`이다.** 참고 설계에서는 `coef_dct2` 출력 `dct2_feat/tvalid_dct2_feat`에 해당한다. `MFCC.v`의 외부 포트는 lifter, C0 에너지 대체, delta, delta-delta를 거친 39개 값이므로 그대로 주 비교 출력으로 사용하지 않는다.

```text
PCM int16 → 진폭 정규화 → 연속 pre-emphasis → 512/160 framing
→ symmetric Hamming → 512-point FFT → 257-bin power /512
→ 26 Mel energies → floor + ln → orthonormal DCT-II → C0…C12
```

위 경로의 Python float64를 알고리즘 기준으로 삼는다. C의 PC 실행은 이식 전 검증이고, **속도 비교의 SW 기준은 동일 ZYBO의 ARM C 실행**이다. 고정소수점 비트정확 모델은 검증 보조 수단이며 다섯 번째 성능 비교군이 아니다. 모든 HW가 같은 경계까지 수행한 경우에만 전체 MFCC 성능을 비교한다. ARM 전처리 + PL FFT 같은 부분 가속은 별도 측정으로 표시한다.

## 2. 항목별 채택안과 원본 차이

아래 채택안은 Python comparison_raw13에 구현·검증된 공통 정의이다. C/ARM/HW는 각각 별도 검증하며, 근거 열의 소스 확인은 RTL 전체의 검증 완료를 뜻하지 않는다. `G/`는 `D:\2610_MFCC\reference_code\github_mfcc`, `M/`는 그 아래 `FPGA source/Calculation MFCC features/`이다.

| 항목 | 공통 채택안 | 소스에서 확인 / 미확인 |
|---|---|---|
| 입력 | mono, 16,000 Hz, signed PCM16; `x[n]=s[n]/32768` | notebook `cells[3]` 16 kHz, top int16→float32. 원본은 명시적 /32768 없음. 정규화는 새 제안 |
| 입력 파일 | RIFF/WAV PCM16 또는 동일 샘플의 little-endian raw; stereo·다른 rate는 자동 변환하지 않고 사전 변환본을 별도 해시로 식별 | 코덱·마이크 실시간 경로는 이번 구현 범위에서 제외 |
| pre-emphasis | `y[0]=x[0]`, `y[n]=x[n]-0.95*x[n-1]` | notebook .95, `Pre_emphasis.v:33`의 음수 상수는 binary32 -.95. 실제 RTL 샘플 짝 정렬은 미확인 |
| 상태 | 클립 시작에서 이전 샘플 0으로 초기화; 겹친 프레임마다 다시 초기화하지 않음 | RTL 1클록 지연이 이전 유효 샘플 지연인지 별도 검증 필요 |
| 프레임 | 길이 L=512 (32 ms), hop H=160 (10 ms), 시작 인덱스 `t*160` | `get_frames.vhd:51–52`, notebook .032/.01 |
| 경계 | center=False; 완성된 프레임만 출력, 앞·뒤 패딩 없음 | 원본 streaming framing에 EOF 없음. 라이브러리의 꼬리 zero padding과 다르므로 래퍼 필요 |
| 윈도 | symmetric Hamming: 분모 L−1=511 | `windowing.v:43–554` 512개 상수와 수식의 binary32 변환이 일치 |
| FFT | N=512, 순방향 음의 지수, 무정규화 DFT; 자연 순서 k=0…511 | `fft_block.xci` 길이512, float32, natural_order. 설정 문자열은 `scaling_options=scaled`; 실제 floating-point 이득은 미확인. impulse 검증 후 보정 |
| 스펙트럼 | k=0…256만 사용, `P=(Re²+Im²)/512`; 내부 bin 2배 가중 없음 | `power_spectrum.v:136–166,186–203`; 원본은 sqrt 후 재제곱하므로 rounding 차이 가능 |
| Mel | HTK 식, 0…8000 Hz, 26개, 이산 bin 삼각형, 피크1; 면적 정규화 없음 | `filterbanks.v`의 6682개 계수가 아래 수식과 binary32 기준 일치 |
| 로그 | 자연로그 `ln(max(E,10^-12))` | 원본은 정확히 +0일 때만 `2^-52`로 바꿈(`MFCC.v:145–160`); floor 정책과 값은 새 제안 |
| DCT | type II, orthonormal, 26→13 | `dct_type2.v:131–468`, `coef_dct2.v:46–58`의 계수 일치 |
| 출력 | 13개, 오름차순 `C0…C12`, C0 유지 | 원본 `appendEnergy.v`는 C0를 ln energy로 교체함. 주 비교에서 우회 |
| 후처리 | lifter OFF, energy replacement OFF, delta OFF, delta-delta OFF, CMVN OFF | 원본 L22 lifter 및 N2 delta 두 단계 존재. 원본 VAD와 동일 특징이라고 주장하지 않음 |
| 수치 표현 | 기준 float64; C/부동소수점 HW binary32; 고정소수점은 아래 별도 규약 | 전체 fixed word length·log 근사·오차 예산 미확인 |
| 처리 목표 | 프레임 완료 후 지속 처리 간격 ≤10 ms, 100 frames/s | 처리 목표이며 실제 처리 시간·Fmax·전력 측정값 아님 |

## 3. 공통 음성 파일과 모호함 없는 수학 정의

**입력 자료 확보 및 검증 완료:** [DATASET_MANIFEST.json](D:/2610_MFCC/data/librispeech/DATASET_MANIFEST.json)에 LibriSpeech test-clean 개발용 1개와 평가용 20개(10명 화자)를 기록했다. 개발 화자는 평가 화자와 겹치지 않는다. 모든 입력은 mono/16 kHz/PCM16이며 FLAC·WAV·raw PCM 샘플의 동일성과 공식 아카이브 체크섬을 검증했다. MFCC나 보드 실행의 완료를 뜻하지 않는다.

첫 개발 입력은 D:/2610_MFCC/data/librispeech/wav_pcm16/8463-294828-0037.wav이며 85,920 samples, 5.37초다. L512/H160/무패딩 규격의 Python 출력 534×13을 실제 실행으로 확인했다. 각 발화의 시작 샘플 0부터 전체를 사용하며, 리샘플링·자르기·음량 정규화·무음 제거·패딩·디더를 적용하지 않았다. [데이터 안내](D:/2610_MFCC/data/librispeech/README.md)와 [인용 원칙](D:/2610_MFCC/references/CITATION_AND_WRITING_RULES.md)을 따른다.

입력 manifest의 mfcc_spec_snapshot_sha256은 데이터 준비 당시 초안의 이력이다. 이를 덮어쓰지 않고 각 실행 manifest에 당시의 규격·소스·입력 hash를 새로 기록한다. 현재 입력은 목표 형식이므로 추가 변환 없이 사용한다. 아래 일반 변환 절차는 향후 다른 형식의 입력을 받을 때의 제안이며, 합성 입력은 검증 보조 자료로 구분한다.

새로운 형식의 입력이 필요한 경우에만 공통 변환을 PC에서 한 번 수행한다. PCM16 WAV를 우선 입력으로 하고, 디코딩한 각 채널을 /32768하여 float64로 변환한다. 여러 채널이면 산술평균 mono를 제안한다. 16 kHz가 아니면 버전을 고정한 `scipy.signal.resample_poly`의 유리수 up/down(원 rate와 16000의 최대공약수로 약분), `window=('kaiser',5.0)`, `padtype='constant'`, `cval=0` 사용을 제안한다. Python MFCC 환경의 SciPy 1.15.3은 구성·사용했으나 여기 제안한 다른 rate 입력 변환은 실행하지 않았다. 원본이 다른 인코딩이면 디코더·스케일을 먼저 명시하고 자동 추정하지 않는다.

peak normalization·AGC·dither는 끈다. 변환 후 `roundTiesToEven(32768*x)`를 [-32768,32767]로 clamp하여 공통 PCM16을 한 번 저장한다. 이미 mono/16k/PCM16인 입력은 샘플 바이트를 그대로 사용한다. 이후 각 비교군은 이 동일 PCM을 /32768한다. 시작 샘플은 변환된 PCM의 **0**을 제안하며, 사용자가 구간을 지정하면 그 절대 시작 인덱스와 길이를 고정한다. 클립 이전 신호를 끌어오지 않고 선택 구간의 첫 샘플에서 pre-emphasis 상태를 초기화한다.

manifest에는 원본 파일 이름·SHA-256·인코딩·채널·rate·샘플 수, 변환 도구/버전/인수·코드 hash, 변환 PCM SHA-256, 선택 시작 샘플·T, L/H/패딩 정책·F, 진폭 단위, spec/계수 hash를 저장한다. 출력 frame_id t의 원본 대비 시작 위치는 `선택 시작 샘플+160*t`이며 단위는 변환 후 16 kHz 샘플이다. 변환 결과 파일과 manifest를 네 비교군이 공유한다.

**설계 제안.** 유효 샘플 수를 T라 하면 출력 프레임 수는 `F=0` (T<512), 그렇지 않으면 `F=1+floor((T-512)/160)`이다. 빈 입력은 0프레임이다. 선택한 T샘플 PCM 구간 전체에 pre-emphasis를 먼저 적용하고, `u_t[n]=y[160t+n]*w[n]`, n=0…511로 프레임을 만든다. 입력 순서는 오래된 샘플부터 최신 샘플까지이다.

```text
w[n] = 0.54 - 0.46*cos(2*pi*n/511)
X_t[k] = sum(n=0..511) u_t[n]*exp(-j*2*pi*k*n/512)
P_t[k] = (Re(X_t[k])^2 + Im(X_t[k])^2)/512, k=0..256
```

FFT 출력 순서나 스케일은 어댑터에서 이 정의로 환산한다. 음의 주파수를 더하지 않으며 DC·Nyquist도 다른 bin과 같은 /512를 적용한다. 윈도 에너지 또는 샘플링률로 추가 정규화하지 않는다. sqrt 후 제곱은 원본 재현 실험에만 필요하며, 공통 수학 정의는 위 직접 제곱합이다.

Mel 삼각형은 연속 주파수 보간과 혼용하지 않고 다음 **이산 bin 방식**을 쓴다.

```text
mel(f) = 2595*log10(1+f/700)
hz(m)  = 700*(10^(m/2595)-1)
m_i = mel(0) + i*(mel(8000)-mel(0))/27, i=0..27
b_i = floor(513*hz(m_i)/16000)

b = [0,2,4,7,10,13,16,20,24,29,34,40,46,53,
     60,68,77,87,97,109,122,136,152,169,188,209,231,256]

B_m[k] = (k-b_m)/(b_(m+1)-b_m),          b_m <= k < b_(m+1)
       = (b_(m+2)-k)/(b_(m+2)-b_(m+1)), b_(m+1) <= k < b_(m+2)
       = 0, otherwise; m=0..25, k=0..256
E_t[m] = sum(k=0..256) P_t[k]*B_m[k]
L_t[m] = ln(max(E_t[m], 1e-12))
```

현재 b에는 중복 인덱스가 없다. 다른 규격을 생성해 중복이 생기면 조용히 분모 0을 처리하지 말고 설정 오류로 보고한다. 음의 에너지, NaN, Inf는 정상 MFCC로 허용하지 않고 오류 플래그·실패 기록을 남긴다. epsilon은 **정규화된 P와 E의 단위**이다. FFT의 축소된 정수값에 같은 숫자를 그대로 적용하지 않는다.

```text
D[c,m] = cos(pi*c*(m+0.5)/26)
a[0] = sqrt(1/26); a[c>0] = sqrt(2/26)
C_t[c] = a[c]*sum(m=0..25) L_t[m]*D[c,m], c=0..12
```

에너지 대체·lifter가 없으므로 C0도 위 식의 결과이다. 예를 들어 무음은 모든 L=ln(1e-12)이므로 이상적인 C0=`sqrt(26)*ln(1e-12)`, C1…C12=0이다. Python 무음 시험에서 이 예상값과의 최대 차이 8.523238e-14를 확인했다.

## 3.1 검증 프로파일

원본 설정 확인용 프로파일과 본 비교용 프로파일을 분리한다. 전자는 notebook의 실제 입력 단위·인수와 lifter/에너지 대체를 확인하며, 패키지 일치를 GitHub RTL 또는 최종 39차원 VAD 재현 완료로 부르지 않는다. 후자는 위 식의 13차원 정적 MFCC(C0…C12)를 구현한다. 라이브러리 기본값과 다른 로그 하한·꼬리 정책을 명시한다. 패키지 버전과 계수·규격 hash를 고정하고, 공통 NumPy/SciPy 연산을 공유하는 검증의 한계도 기록한다. 상세 구현 범위와 완료 조건은 [NEXT_TASK_PYTHON.md](NEXT_TASK_PYTHON.md)를 따른다.

## 4. 계수와 수치 재현 규칙

**소스에서 확인:** 원본은 window 512개, Mel 6682개, cosine 338개, DCT 정규화 13개, lifter 13개를 RTL 상수로 보관한다. 별도 MFCC `.coe/.mem` 누락을 전제로 작업하지 않는다. 이번 점검에서 이 다섯 집합은 각각 수식을 binary32로 반올림한 값과 0개 불일치였다. 이는 상수 검사이며 MFCC 출력 비교가 아니다.

**설계 제안:** 이후 하나의 계수 생성기로 float64 원본 수식값, binary32 비트패턴, fixed 정수 테이블을 내보낸다. 생성기 버전, `spec_id`, 배열 크기·순서, endian, 반올림법, 각 파일 SHA-256을 manifest에 남긴다. Python float64 계수와 해시는 build/python_reference/reproduce_01/coefficients/comparison_raw13에 생성·검증되어 있다. binary32 변환은 C 전용 생성 절차에서 명시적으로 수행하고 fixed 테이블은 별도 후속 과제다.

- Python 기준은 float64 수식 계수·연산을 사용한다. C/FP IP는 같은 수식에서 round-to-nearest-ties-to-even으로 변환한 binary32 계수를 사용한다. binary32 .95는 정확한 십진 .95와 다르므로 그 차이도 수치 오차에 포함한다.
- C는 FFT 길이와 연산 정의를 고정하고 scalar radix-2 등 실제 FFT를 사용한다. O(N²) DFT를 ARM 가속 비교의 대표 SW로 삼지 않는다. FFT 코드/라이브러리·라이선스·컴파일 옵션을 기록한다. 처음에는 `-ffast-math`를 끄고 FMA contraction 정책을 고정한다. 최적화 실험은 별도 빌드로 관리한다.
- Python 기본 MFCC 함수 호출만으로 규격을 충족했다고 판단하지 않는다. 라이브러리의 25 ms/alpha .97/rectangular window/energy replacement 등 기본값, 꼬리 패딩을 명시적으로 덮어써야 한다. 라이브러리 버전 고정 후 보조 교차검증으로 사용한다.
- binary32와 float64를 비트정확으로 요구하지 않는다. IP의 근사·denormal 처리·연산 순서·latency는 XCI와 버전 기록에 포함하고 수치 허용오차로 비교한다.

알고리즘 교차 확인에 사용한 1차 소스는 [python_speech_features base.py](https://raw.githubusercontent.com/jameslyons/python_speech_features/master/python_speech_features/base.py), [sigproc.py](https://raw.githubusercontent.com/jameslyons/python_speech_features/master/python_speech_features/sigproc.py)이다(조회 2026-10-04). 이 링크의 현재 master는 원본 notebook이 사용한 패키지 버전을 증명하지 않으며, 향후 재현 환경의 버전 고정을 대신하지 않는다.

## 5. 고정소수점 경로의 잠정 계약

**소스에서 확인:** 기존 `fft_stream_top`은 signed16 복소수 입력·출력, 자연 순서 출력, `cfg_log2_n=3…10`을 노출한다. `fixed_round_shift.sv`는 ties-to-even 및 overflow 검출을 사용하며 **포화가 아니라 wrap**한다. 원본 README의 Q1.15 및 성능 기록은 재실행 결과가 아니다. 이번 선택인 N=512의 비트정확·연속 프레임 시험과 누적 shift S는 별도 FFT 검토에서 확정해야 한다.

**설계 제안:** pre-emphasis로 |y|가 1보다 커지므로 PCM 정규화만 한 값을 Q1.15 FFT에 직접 넣지 않는다. 우선 `z=u/2`를 Q1.15로 반올림해 입력하고 `in_im=0`으로 한다. 입력 여유의 수학적 근거는 |y|≤1.95, |w|≤1이므로 |z|≤0.975라는 점이다. 모든 fixed stage의 overflow를 기록하고 무시하지 않는다.

FFT가 입력 z에 대해 총 S비트 축소했다면, 출력 정수를 Q1.15 실수로 해석한 F에 대해 다음을 사용한다.

```text
F ≈ FFT(u)/(2*2^S)
P = (4*2^(2S)/512) * (Re(F)^2 + Im(F)^2)
N=512, S=9 확인 시: P = 2048*(Re(F)^2 + Im(F)^2)
```

이 보정은 정수 양자화 오차를 없애지 않는다. fixed 에너지의 지수를 추적해 **정규화 E와 동등한 위치에서** 1e-12 floor를 적용한다. ‘0이면 정수 1’ 같은 구현은 공통 규격과 다른 로그 하한이므로 금지한다. 내부 스케일의 로그 보정을 사용할 때도 floor 전의 E를 기준으로 적용한다.

| 단계 | 초안 | 상태 / 확정에 필요한 것 |
|---|---|---|
| PCM | signed16 /32768 | 설계 제안 |
| pre-emphasis·윈도 | .95 및 Hamming을 표현할 충분한 중간 폭, FFT 전 /2 | 정확한 폭·소수부·반올림 위치 미확인; 범위·오차 분석 후 확정 |
| FFT 입출력 | signed16 Q1.15, twiddle Q1.15, 기존 stage scaling 보존 | 설계 제안; N512 shift/순서/overflow/단독 drain 검증 필요 |
| power·Mel | 제곱합 확대 폭 및 누적 guard bits 유지, 스케일 메타데이터 보존 | 폭·가중치 소수부 미확인 |
| log | 양수 정규화+LUT/근사 후보, ln 출력 | 구조·구간·오차·범위 미확인 |
| DCT·출력 | signed 고정소수점; PC에서 명시한 2^-F로 복원 | 누적 폭·출력 F 미확인; 결과 비교 전에 고정 |
| overflow 정책 | 기존 FFT wrap+sticky를 비트모델에 그대로 반영; 통과 벡터는 overflow=0 요구 | 신규 stage 포화/wrap 선택은 미확인; 임의 교체 금지 |

Claude Code의 정확한 검토 경로는 `D:\2610_MFCC\project\docs\reviews\FFT_REUSE_REVIEW.md`이다. 감사 시점에 없었으며 파일을 기다리거나 생성·수정하지 않았다. 검토가 들어오면 위 FFT 계약과 충돌을 확인한 뒤 규격을 갱신한다.

## 6. 프레임 전달·보드 인터페이스 제안

첫 전체 경로는 **저장된 PCM → Python 기준 → 동일 C 함수의 host 검증 → ZYBO ARM bare-metal 실행 → 13계수 회수 및 대조**이다. C 연산 함수와 보드 타이머·UART 코드를 분리한다. 보드 플랫폼이 아직 없으면 Python↔host C까지만 완료로 표시하고 ARM 결과를 비워 둔다.

후속 HW는 PCM 수락부터 framing까지 포함한다. 최초 bring-up은 한 클립 또는 한 프레임 묶음을 입력 BRAM에 먼저 적재하고, 계산 완료 후 출력 BRAM에서 읽는 AXI-Lite 제어 경로를 제안한다. 그 후 DMA·interrupt로 확장한다. 이렇게 전체 MFCC 숫자 검증과 연속 스트리밍 검증을 분리한다.

- 출력 레코드: `spec_id, input_hash, frame_id, start_sample, coeff_index, value`; 프레임 오름차순, 각 프레임 내 index 0…12. binary32는 little-endian IEEE754 32비트, fixed는 signed int32 및 fractional_bits/scale 메타데이터를 별도 명시한다.
- AXI4-Stream으로 확장하면 성공한 전송은 valid&&ready에서만 센다. 출력 TLAST는 c=12에서만 발생하며, stalled 데이터·index·TLAST는 유지한다. 입력은 클립 샘플 수를 제어 레지스터로 전달하고 원본의 valid gap을 프레임 경계로 사용하지 않는다.
- PL 100 MHz 단일 연산 도메인과 active-low 동기 해제 reset을 제안한다. 리셋 시 이전 샘플, 프레임 카운터, 누산기, valid, 출력 개수를 초기화한다. 기존의 rst=0 연결을 성공적인 reset으로 해석하지 않는다.
- 기존 FFT에는 output-ready가 없으므로 downstream이 멈출 수 있으면 프레임 전체를 담을 버퍼와 overflow 검출을 둔다. DMA S2MM을 먼저 시작하는 것만으로 임의 backpressure 지원이 증명되지 않는다.
- 마이크/I2S·코덱은 이번 구현 범위에서 제외한다. 저장 PCM 경로의 DMA/cache/interrupt 및 연속 프레임 큐는 후속 단계로 다룬다. 실제 PL 실행·결과 회수는 목표에 유지한다.

## 7. 재현 검증과 측정 계획

**Python은 아래 합성 17개·개발 1개·평가 20개를 실행·검증했다. C/ARM/HW는 구현별 증거를 별도로 기록한다.** 아래 작은 PCM 파일을 한 번 생성하고 샘플 바이트 및 SHA-256을 네 비교군에 동일하게 사용한다. 난수 seed만 기록하는 것으로 바이트 동일성을 대신하지 않는다.

| 입력/시험 | 확인할 항목 |
|---|---|
| 길이 0, 511, 512, 671, 672, 832 | 출력 프레임 수 0,0,1,1,2,3; 꼬리 처리·중복·유실 |
| 832샘플 무음 | 로그 유한성, 3프레임 출력, 이론 C0 및 나머지 0 근사 |
| DC ±8192, impulse 16384 at n=0 및 n=511 | pre-emphasis 최초/프레임 경계 상태와 polarity; stage별 기준 비교 |
| 1 kHz 정현파(k=32), 비정수 bin 정현파, 복합음 | FFT 부호·bin 순서·스케일·윈도·Mel 경계; 샘플 생성 반올림 고정 |
| ±최대 PCM 교대, 매우 작은 ±1, seeded noise | headroom, overflow, fixed 로그 하한·양자화 손실 |
| 허가된 16 kHz 음성 클립 | 실제 분포에서 단계별/계수별 오차. 분류 정확도 시험과 구분 |
| reset→첫 프레임, 단독/연속 프레임, 입력 gap·출력 stall | 전송 계약·drain·TLAST·재시작; 지원하지 않는 stall은 실패로 식별 |

단계별로 pre-emphasis, windowed samples, complex FFT, P[257], E[26], lnE[26], C[13]을 저장한다. frame/index/count 오류를 수치 오차보다 먼저 검사한다. 출력 누락·NaN·Inf·overflow는 별도 실패로 센다.

float32 C/FP HW의 초기 통과 기준 제안은 계수별 `abs(test-ref) <= 1e-3 + 1e-5*abs(ref)`이다. 이는 C 개발부터 적용할 초기 목표다. Python float64끼리의 허용치와 혼동하지 않으며, C 실측은 C_REFERENCE_RESULTS.md에 기록한다. fixed RTL↔비트모델은 **정수 0불일치**를 요구한다. fixed↔float64의 허용 최대오차/RMSE는 저진폭·음성 pilot을 보고 독립 평가 입력을 실행하기 전에 확정한다. 미확정 상태에서는 오차 측정표만 보고하고 정확도 통과를 선언하지 않는다. 작은 기준값에 대한 상대오차 하나로 평가하지 않는다.

성능 기록은 다음을 포함한다.

- 보드/소자, Vivado/Vitis·컴파일러 버전, git 코드 버전, spec·입력·계수 hash, PS/PL clock, compiler flags, cache 상태, timer 주파수.
- ARM C의 동일 연산 범위, 준비/워밍업/반복 횟수 및 min/median/p95/max. PC Python 시간은 개발 참고값으로만 제시한다.
- HW 첫 입력 수락→마지막 계수 생성 지연, 프레임 간 처리 간격, 총 throughput. 512샘플 획득 시간(32 ms)을 계산 지연과 구분한다.
- 준비 시간(파일 읽기·공통 변환·초기 PS 메모리 적재), ARM 연산 시간, HW 코어 시간, **PS 메모리 입력→전송/cache/제어→PS 메모리 결과 회수** 시간을 각각 기록한다. 마지막 범위에는 해당 경로의 framing도 포함한다. 시각화·UART 텍스트 출력·파일 기록은 별도 항목이다. 준비 시간을 뺀 결과를 파일 읽기부터의 전체 시간이라고 부르지 않는다.
- 같은 Zynq part·tool·constraint에서 합성 및 post-route LUT/FF/BRAM/DSP와 WNS를 분리한다. 음의 WNS인 구현에 해당 클록 성능을 주장하지 않는다.
- polling이면 CPU 사용량 감소를 주장하지 않는다. 측정하지 않은 전력·AI 인식 정확도·속도 향상은 결과란에 넣지 않는다.

## 8. 다음 구현 순서와 파일 소유 범위

1. `software/python/`, `scripts/`, `verification/`: 위 수식의 기준 모델, 계수 생성기, 작은 공통 PCM, hash manifest, 단계 dump. float64 기준과 source table 교차검증. **가장 먼저 하나의 입력에서 13계수까지 끝낸다.**
2. `software/c/`, `verification/`: C kernel 및 host harness, 동일 입력 대조. `software/arm/`: 신뢰할 수 있는 PS7 플랫폼이 확보된 뒤 bare-metal 실행·타이머·계수 회수. 10월 6일 전달의 우선 증거는 이 경로의 실제 도달 단계다.
3. `hardware/ip/`, `hardware/rtl/`, `hardware/scripts/`, `verification/`: **Vivado 2024.2**에서 source MFCC raw13 경계 분리, 작업 복사본 XCI retarget/regeneration, reset·handshake·pre-emphasis 정렬·framing 수정, XSIM 단계/전체 검증, 이후 ZYBO 입출력. 라이선스와 출처 확인 후 필요한 소스만 반입한다. 2020.2 기존 기록은 참고 증거이며 2024.2 재현으로 간주하지 않는다.
4. FFT 별도 검토 후 `software/fixed_model/`, `hardware/rtl/fixed/`: N512 FFT 계약 시험→power→Mel→log→DCT의 비트모델/RTL 동시 검증. 미완성 fixed 단계를 부동소수점으로 대체했으면 혼합 구현이라고 기록한다.
5. 두 HW의 전체 경로가 검증되면 공통 음성 파일과 측정 harness로 비교. DMA·후처리·분류기 확장은 별도 실험으로 구분한다. 마이크 통합은 이번 구현 계획에 포함하지 않는다.

위 목록은 구현 순서다. 초기 감사에서는 문서만 작성했으며, 후속 Python 작업에서 기준 모델·계수·단계 dump·합성 및 음성 검증을 완료했다. 현재 Python 근거는 PYTHON_REFERENCE_RESULTS.md와 보존된 reproduce_01 실행이다. 미확정 항목은 FFT N512 검증·fixed 정밀도/로그 오차·FP IP 재생성 및 timing·PS7 preset과 실제 보드 연결·외부 소스 사용 조건이다. 공통 음성과 기존 Python 실행 snapshot/hash는 유지한다. C PC 작업의 상태·제한은 C_REFERENCE_RESULTS.md를 따르며, ARM/보드 결과는 실제 실행 전까지 미검증이다.
