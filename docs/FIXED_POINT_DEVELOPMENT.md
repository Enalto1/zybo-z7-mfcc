# MFCC 양자화·고정소수점 개발 절차

작성: 2026-10-04 KST. 상태: **개발 절차 확정, 전체 비트 폭과 수치 합격 기준은 미확정**.

## 1. 현재 모델의 역할

`software/c/mfcc.h`, `mfcc.c`, `fft32.h`를 확인했다. 데이터 경로는 C `float`, `logf`, float 상수이며 `fft32.h`가 binary32 크기·유효 비트·지수 범위를 검사한다. 따라서 현재 C는 **float32 MFCC**다. PCM 입력이 int16이라는 사실은 중간 계산 전체가 고정소수점이라는 뜻이 아니다.

| 경로 | 역할 | 유지/후속 작업 |
|---|---|---|
| Python float64 | 공통 수학 정의와 오차 비교 기준 | 기존 frozen 결과 보존 |
| C float32 → ARM | 같은 보드의 SW 비교군 | 그대로 유지. 전체 C를 정수로 바꾸지 않음 |
| float32 IP HW | 부동소수점 HW 비교군 | IP 연동과 실제 수치 검증 별도 |
| Python 정수 비트모델 → custom fixed RTL | 양자화 설계와 RTL 비트 일치 검증 | 단계별 작성 필요 |

Python 정수 비트모델은 RTL 검증 기준이며 그 자체가 ARM 성능 비교군은 아니다. RTL 전에 같은 정수 모델을 C로 반드시 중복 작성할 필요는 없다. 다만 논문의 보조 실험으로 **별도 ARM fixed C**를 추가하는 것을 권장한다. ARM float32와 fixed C를 비교하고, 같은 정수 계약의 fixed C와 fixed RTL을 비교하면 수치 표현 변경과 하드웨어 구현 변경의 효과를 구분해 평가하기 쉽다. 기존 C float32는 유지한다.

권장 순서는 **전체 Python 정수 계약 완성 → 별도 fixed C 이식·PC 비트 비교 → ARM 실행·측정**이다. 확정된 단계는 C와 RTL로 병행 이식할 수 있다. 현재 fixed C는 미구현이며 전처리·log·DCT가 실수인 혼합 경로를 전체 fixed C라고 부르지 않는다. 일괄 INT8 변환 대신 단계별 W/F, BFP 지수, 반올림·overflow를 따른다. 측정 범위와 이식 계약은 [고정소수점 설계 변경 이력](FIXED_POINT_DESIGN_HISTORY.md)에 기록한다.

기존 C의 합성 입력 2개 수치 실패는 [C_REFERENCE_RESULTS.md](C_REFERENCE_RESULTS.md)에 보존하며, 이 절차로 해결됐다고 주장하지 않는다.

## 2. 양자화를 생략할 수 없는 이유

고정소수점 RTL은 유한 폭의 정수와 약속된 소수점 위치로 수를 표현한다. 계수·연산 결과를 그 격자에 맞추고 비트 폭을 줄이는 선택이 양자화다. 전용 도구가 반드시 필요한 것은 아니지만 이 선택을 생략할 수는 없다. RTL에 바로 shift/bit slice를 쓰더라도 이미 양자화 방식을 선택한 것이다.

양자화 대상은 최종 MFCC만이 아니다. pre-emphasis 0.95, window, FFT twiddle, Mel/DCT 계수, 로그 LUT와 각 곱셈·누산·출력 경계가 대상이다. 중간값이 폭 안에 정확히 들어가면 매 연산마다 줄일 필요는 없다. 어떤 경계까지 full precision을 유지하는지도 설계 계약이다.

예: 소수부 15비트로 0.95를 최근접 반올림하면 정수 31130, 복원값은 `31130 / 32768 = 0.95001220703125`다. 이는 설명용이며 pre-emphasis 계수 형식을 F=15로 확정하지 않는다. PCM16의 `s/32768`은 같은 정수를 보존하면 정확히 표현할 수 있지만, 이후 곱셈·누산·스케일 조절에는 추가 설계가 필요하다.

signed W비트, 소수부 F비트의 기본 정의:

```text
실수 해석: x_hat = q * 2^-F
정수 범위: -2^(W-1) <= q <= 2^(W-1)-1
양자화 예: q = overflow_policy(round(x * 2^F))
```

rounding과 overflow 정책은 서로 다른 선택이다. 최근접 반올림, tie 처리, 음수 절삭/shift의 의미와 saturation/wrap을 각각 정한다. `float` 배열에 마지막 한 번만 round를 적용한 결과를 전체 비트정확 모델이라고 부르지 않는다.

## 3. 두 가지 검증 경계

```text
같은 PCM ─→ Python float64 ────────────────┐
         ├→ C float32 → ARM              │ 알고리즘 오차 비교
         └→ 정수 비트모델 ─→ fixed RTL ────┘
                     └──── 비트 일치 ────┘
```

- **정수 모델 vs float64:** 공통 프레임 전체에 대한 단계별 최대 절대 오차/RMSE, MFCC 계수별 오차와 후보별 오차 예산을 평가한다. 일반적으로 비트 일치를 기대하지 않는다.
- **RTL vs 정수 모델:** 같은 quantization profile의 정수 값·순서·overflow·frame metadata를 일치시킨다. latency가 달라도 올바른 transaction으로 정렬한다.
- 데이터 정확성과 stream protocol 검증은 별도로 필요하다. C float32 통과가 fixed RTL 정확성을 보장하지 않는다.

## 4. 순서와 단계별 종료 조건

### Q0. 수치 계약표와 양자화 연산 도구

현재 공통 수학 정의를 읽고 각 단계의 입력/계수/곱/누산/출력에 대해 다음 표를 만든다. 기존 FFT에서 확인된 사실, 새 후보, 미확정을 구분한다.

`W, F, signed, scale/exponent, 이론적 범위, 개발 입력 관측 범위, full product width, accumulator guard bits, quantization 위치/모드, overflow 정책, 계수 생성 방식, 상태·지연`.

관측 최대값만으로 모든 입력의 안전성을 주장하지 않는다. 가능한 범위를 분석하고 적대 입력도 사용한다. 모든 단계를 일률적으로 16비트로 정하지 않는다.

정수 모델에는 최근접/tie 처리, 산술 shift, 부호 확장, wrap/saturation, overflow 검출을 명시적으로 구현한다. Python의 무제한 정수로 full product를 계산한 후 하드웨어 경계마다 정확한 폭을 적용한다. NumPy 고정 폭 정수의 암묵적 overflow를 이용하지 않는다.

### Q1. 기존 FFT와 Power/Mel의 오차를 분리하는 실험

float64 전처리/window → FFT 입력 양자화 → 검증된 FFT 비트모델 → 정수 Power/Mel → float64 log/DCT 경로를 먼저 비교한다. 이는 **혼합 정밀도 수치 실험**이며 완성된 fixed MFCC가 아니다.

고정 α=1/2와 BFP 후보를 비교한다. BFP는 프레임 지수를 선택하여 좁은 표현 범위를 활용하는 방법이며 양자화를 없애지 않는다. 이전 Q15 변환 후 shift한 실험과, 더 넓은 값에서 shift를 선택하고 한 번 양자화하는 후보를 구분한다. 후속 full-fixed에서는 실제 정수 전처리 출력의 폭과 지수 선택 방법으로 대체해야 한다.

### Q2. 전체 정수 모델 완성

pre-emphasis/window 계수와 중간 폭, log의 정수 정규화·LUT/근사, DCT 계수/누산/출력까지 양자화를 추가한다. 상수 생성에 float64를 쓰거나 결과를 그림으로 복원하는 것은 가능하지만, **full-fixed 모델의 런타임 데이터 경로에는 float64 log/DCT를 남기지 않는다**.

단계별 연산 순서와 overflow를 기록하고 각 변경의 오차 증가를 분리한다. 전체 PCM→raw13 정수 모델, 공통 단위로의 출력 환산, 폭/반올림/계수 manifest가 갖춰져야 전체 양자화 설계가 완성됐다고 표현한다. 검증된 단계는 전체 설계 완료 전에 RTL로 옮길 수 있지만 전체 완료 주장은 Q2 이후 검증 근거가 있어야 한다.

### Q3. RTL과 교차 검증·평가

확정된 단계의 정수 모델과 벡터를 기준으로 [RTL_CODING_RULES.md](RTL_CODING_RULES.md)에 맞춰 RTL을 작성한다. 비트 비교, protocol 검사, 합성·구현을 단계별 수행한다. full-fixed 조건과 허용치를 고정한 뒤 평가20을 1회 평가에 사용한다. 실패도 보존한다. 수정 후 같은 평가군을 다시 사용했다면 독립적인 최초 평가라고 부르지 않는다.

## 5. FFT·로그 스케일 주의 사항

N=512, 기존 FFT 총 축소 S=9, FFT 입력 `u * 2^(s-1)`를 signed16/F15로 양자화한 후보에서:

```text
psum = re_int^2 + im_int^2
P_hat = psum * 2^-(19+2s)
w_int = round(B_m[k] * 2^Fw)
T_m = sum(psum[k] * w_int[m,k])
E_hat = T_m * 2^-(19+2s+Fw)
L_hat = ln(max(E_hat, 1e-12))
```

고정 배율은 s=0, 현재 Mel 후보 Fw=16이면 E의 배율은 `2^-(35+2s)`다. 이 식은 FFT 양자화 오차와 Mel 계수 오차를 제거하지 않는다. BFP 지수를 포함해서 floor를 비교해야 하며, `T_m==0`만 확인하는 shortcut은 일반적인 BFP 경로에 적용할 수 없다.

무음이나 어떤 Mel band의 에너지 0은 유효 입력일 수 있다. 저음량·0 에너지 프레임을 주 비교에서 제외하지 않는다. FFT 오차 RMS로 추정한 오차 전력을 모든 입력의 실제 에너지 하한으로 간주하지 않는다.

## 6. 실험 관리

- 개발용 음성 1개(534프레임)와 기존 합성 입력17을 먼저 사용한다. 필요하면 배율 전환 경계·저진폭 검사 입력을 별도로 생성·기록한다.
- fixed 후보를 튜닝할 때 평가용 음성20은 열지 않는다. 이전 float32 평가와 fixed 후보 개발을 혼동하지 않는다.
- 후보 설정, 계수, round/clamp 정책, 입력·소스·모델·규격 hash를 실행별로 보존한다.
- 전체 프레임의 max/RMSE, 계수별 오차, clipping/overflow, Mel zero/floor 횟수를 보고한다. 구간별 분석은 전체 통계를 대체하지 않는다.
- 수치 오차와 자원/속도 결과를 구분한다. 비트 수 감소만으로 LUT/DSP/전력 감소를 확정하지 않는다.

**2026-10-04 진행 상태:** Q0 도구·부분 계약과 Q1 혼합 경로 실험, FFT 폭 확장 비교가 완료됐다. 전체 단계의 폭·반올림·정수 log/DCT 계약은 아직 미확정이다. 현재 우선 후보는 FFT 입력16/F15·내부 및 출력20/F19, BFP 목표0.975이며 정확도 합격 또는 RTL 채택 확정이 아니다. 다음 수치 설계 단계는 잔여 오차를 추적하면서 **Q2 전체 정수 모델**을 완성하는 것이다. 상세 근거는 [정밀도 검토](reviews/FIXED_POINT_PRECISION_REVIEW.md)를 따른다.

이후 고정소수점 변경은 [설계 변경 이력](FIXED_POINT_DESIGN_HISTORY.md)에 변경 전후 계약, 이유, 실행 ID/hash, 전체 수치 결과, 실패와 채택·보류 상태를 반드시 추가한다. 이전 실행 자료는 보존한다. [초기 작업 지시](NEXT_TASK_FIXED_POINT.md)는 당시 Q0/Q1 범위를 설명하는 기록이며, 현재 완료 상태와 이후 사용자 지시를 우선한다.

## 7. 참고

현재 C 자료형의 근거: `software/c/mfcc.h`, `software/c/mfcc.c`, `software/c/fft32.h`.

고정소수점의 폭·소수점 위치·반올림·overflow를 별도 지정한다는 기술적 근거: [AMD Fixed-Point Identifier Summary, UG1399 2024.1](https://docs.amd.com/r/2024.1-English/ug1399-vitis-hls/Fixed-Point-Identifier-Summary), [고정소수점 표현](https://docs.amd.com/r/2024.1-English/ug1399-vitis-hls/ap_-u-fixed-Representation). 조회: 2026-10-04. 개념 참고이며 HLS 사용이나 C를 통한 RTL 자동 생성을 프로젝트에 도입한다는 뜻은 아니다.
