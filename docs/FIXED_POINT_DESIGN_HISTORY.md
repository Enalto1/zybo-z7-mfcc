# MFCC 고정소수점 설계 변경 이력

작성: 2026-10-04 KST. 이 문서는 고정소수점 형식과 구현 방식을 변경한 이유, 실험 근거, 남은 문제를 추적하는 개발 기록이다. 논문·발표 원고는 저장소 밖의 전용 폴더에서 작성하고, 이 기록과 원시 결과를 근거로 사용한다.

현재 상태는 **FFT 정밀도 실험 완료, 입력16/F15·내부 및 출력20/F19 후보 제안, 전체 정수 MFCC 계약 미확정**이다. 이 후보를 새 RTL에 적용하거나 보드에서 검증한 상태는 아니다. 전체 MFCC의 정확도 합격도 선언하지 않았다.

## 기록의 근거

- 상세 수식·후보별 통계·재현 방법: [고정소수점 FFT 정밀도 검토](reviews/FIXED_POINT_PRECISION_REVIEW.md).
- 선행 단계: [양자화 계약](reviews/FIXED_POINT_QUANTIZATION_PLAN.md), [pilot 결과](reviews/FIXED_POINT_PILOT.md), [FFT 재사용 검토](reviews/FFT_REUSE_REVIEW.md).
- 원시 산출물: `D:/2610_MFCC/build/fft_precision/prec_04_verified_full_20261004/`. `run_manifest.json`에 조건과 결과, `artifact_hashes.json`에 산출물 SHA-256, `source_snapshot/`에 실행 당시 소스를 보존한다.
- 검증 기록: `D:/2610_MFCC/build/fft_precision/verification_20261004_01/verification_summary.json` 및 `D:/2610_MFCC/build/fft_precision/prec_04_verified_full_20261004_audit.json`.

아래 수치는 위 실행에서 직접 측정한 본 프로젝트 결과다. 선행 논문에서 가져온 수치가 아니다. 표는 읽기 쉽게 반올림했으며 정밀한 값은 원시 산출물을 따른다. 로컬 실행 자료는 Git clone에 포함되지 않으므로 별도로 보존한다.

## 2026-10-04 변경과 판단

| 항목 | 이전 또는 비교 대상 | 변경·검토 내용 | 이유와 현재 판단 |
|---|---|---|---|
| FFT 입력 배율 | 고정 배율, Q15 변환 뒤 shift | 프레임별 BFP 지수를 먼저 정하고 FFT 입력을 한 번 양자화 | 작은 신호의 표현 범위 활용. 기존 경로와 구분하며 실제 정수 전처리 출력에 적용하는 규칙은 후속 확정 |
| BFP 목표 | peak 0.5 | peak 0.975도 비교 | 시험 입력에서 통합 오차가 감소해 후속 후보로 제안. 임의 입력의 무overflow 보장은 아님 |
| FFT 데이터 폭 | 입력·내부·출력16/F15 | 입력/내부/출력18·20비트 조합을 분리해 비교 | 내부만20·출력16으로는 출력 격자 손실이 남음. 내부·출력을 함께20/F19로 넓히는 후보 우선 |
| FFT 입력 폭 | 입력16/F15 | 입력18/F17·20/F19도 비교 | 입력20의 개선은 일관되지 않음. 현재는 입력16/F15 유지 후보를 우선 |
| Power/Mel 폭 | FFT 출력16에 대응하는 Power32·Mel52 | FFT 출력20 후보에는 Power40·Mel60 검토 | FFT 폭을 늘리면 후속 제곱·누산 폭과 실수 단위 복원도 함께 바뀜. 현재 Mel 계수 Fw16과 표의 범위에 대한 값 |
| 오차 분리 실험의 배율 오류 | B·C 경로의 잘못된 /512 처리 | 물리 FFT 축소와 공통 단위 복원 식 수정 | 종전 식은 Power를 1/262144로 만들 수 있었음. 오류 수정 전 결과로 정밀도를 판단하지 않음 |
| 출력 폭 변환 메타데이터 | 물리 FFT shift와 출력 재양자화 shift 혼용 위험 | 물리 S=9와 출력 R=F_internal−F_out을 별도 기록 | 20/F19→16/F15에서는 R=4. 정수 코드 shift 13을 물리 S로 재사용하면 배율을 중복 반영 |
| 포화 집계 | pilot D의 1,385를 샘플 수로 해석 | 923개 frame-sample / 1,385회 단계별 이벤트로 정정 | width saturation 923회와 symmetric clamp 462회의 중복을 분리. 중첩 프레임의 고유 PCM 위치 개수와도 다름 |

`pilot_01`과 집계를 수정한 `pilot_03_fixed_accounting`의 저장 파일 18개는 SHA-256이 동일했다. 집계 정정으로 계산 결과를 바꾼 것은 아니다. 기존 `fft_bitmodel.py` 및 이전·실패 실행 폴더를 보존했다. 최종 비교는 배율 오류를 수정한 `prec_04_verified_full_20261004`를 사용한다.

## 정밀도 선택의 수치 근거

조건은 개발 음성1개 534프레임, 기존 합성17개와 추가 합성6개의 총82프레임이다. 0프레임 경계 입력도 결과 목록에 남겼다. 평가 음성20개는 후보 선택에 사용하지 않았다. 모든 후보에서 twiddle16/F15, Mel Fw16, log floor `1e-12`를 유지했다.

아래는 BFP 목표0.975에서 **전체 프레임의 최종 MFCC 오차**다. 전처리·window·log·DCT가 float64인 혼합 경로의 결과이며 전체 fixed 결과가 아니다.

| FFT 입력/내부/출력 비트 | 개발 최대 절대 오차 | 개발 RMSE | 합성 최대 절대 오차 | 합성 RMSE | 개발/합성 floor 회귀 |
|---|---:|---:|---:|---:|---:|
| 16/16/16 | 3.379397 | 0.558624 | 24.387877 | 1.755017 | 24 / 129 |
| 16/20/16 | 3.325614 | 0.299994 | 22.467655 | 1.623424 | 7 / 123 |
| 16/18/18 | 0.226906 | 0.022073 | 20.819549 | 1.387043 | 0 / 102 |
| **16/20/20** | **0.055171** | **0.006258** | **6.591594** | **0.894057** | **0 / 60** |
| 18/18/18 | 0.210598 | 0.023168 | 22.248365 | 1.431753 | 0 / 104 |
| 20/20/20 | 0.054205 | 0.005619 | 8.642967 | 1.052201 | 0 / 68 |

각 데이터 폭 W의 소수부는 F=W−1이다. floor 회귀는 `reference > 1e-12 AND candidate < 1e-12`인 (frame, Mel band) 개수이며, 양쪽 모두 작은 정상 무음은 포함하지 않는다. 합성 RMSE는 82×13개 계수 전체의 제곱오차에서 계산했다.

16/20/20 후보의 개발 RMSE는 16/16/16 대비 약89.3배 작았다. 이는 수치 오차 비율이며 처리속도 향상이 아니다. 입력20은 개발 음성에서 조금 더 좋았지만 합성 통합 오차·최대 오차·floor 회귀가 더 컸으므로 현재 우선 후보로 정하지 않았다.

합성 제곱오차의 99.7668%와 floor 회귀60개는 `fullscale_alternating`, `tone_bin32_1000hz`, `silence_to_fullscale`에 집중됐다. 이 입력을 통계에서 제외하지 않았다. 로그는 아직 float64이며, 작은 에너지의 상대 오차나 소실이 로그에서 확대되는 현상이다. 정수 로그를 구현하는 것만으로 앞단에서 사라진 에너지가 복원되지는 않는다.

시험한 12조합×616프레임에서 FFT overflow, 입력 clipping, 지수 clamp는 관측되지 않았다. 회귀검사1,580개와 산출물 감사96,182개가 통과했다. 산출물 감사는 원시 FFT 정수에서 Power/Mel·복원 단위·통계를 재계산한 검사이며, 새 FFT RTL이나 독립 FFT 알고리즘을 검증했다는 뜻은 아니다.

## 후속 설계에 전달할 잠정 계약

| 경계 | 현재 후보 | 후속 확인 |
|---|---|---|
| FFT 입력 | signed16/F15, BFP 목표0.975, 지수 s∈[−2,24], 무음 s=0 | PCM16과 별개의 FFT 입력 형식. 정수 전처리·window 출력으로 지수를 선택하는 규칙 확정 |
| FFT 내부·출력 | signed20/F19, 입력 승격 4비트, 물리 축소 S=9 | 단계별 guard bits, ties-to-even, wrap 및 overflow 표시를 그대로 계약화 |
| Twiddle | signed16/F15 | 현재 고정 조건. 추가 확장의 효과는 아직 실험하지 않음 |
| Power | unsigned40, `psum = re² + im²` | 실수 단위 `P = psum × 2^(−27−2s)` |
| Mel | unsigned17 계수/Fw16, unsigned60 누산 후보 | 현재 계수 표에 대한 폭. `E = T × 2^(−43−2s)`로 복원하며 floor에도 s 반영 |
| 로그·DCT 및 최종 raw13 | 정수 형식 미확정 | 로그 근사/LUT, 계수, 곱·누산·출력 폭, 반올림과 최종 출력 환산 규칙 설계 |

단계별 반올림·wrap/saturation은 같은 동작이 아니다. 실험에 사용한 입력의 saturate/대칭 clamp와 FFT 내부 wrap을 구분한다. 내부만20·출력16인 다른 후보에 위 F19 복원식을 그대로 적용하지 않는다. 일반식은 상세 정밀도 보고서를 따른다.

전체 정수 모델과 오차 허용 기준을 확정한 뒤 독립 평가를 진행한다. 새20비트 RTL의 자원·최대 주파수·지연·전력과 AI 인식 정확도는 미측정이다. 기존16비트 FFT의 합성 결과를 새 후보나 전체 MFCC 결과로 사용하지 않는다.

## 별도 고정소수점 C 모델의 역할

**추가를 권장하지만 아직 구현에 착수하지 않았다.** 기존 C float32는 그대로 유지한다. Python 정수 모델만으로도 RTL 비트 검증 기준을 만들 수 있으므로, RTL 전에 같은 모델을 C로 반드시 중복 작성할 필요는 없다. 별도 fixed C의 주 목적은 ARM에서 같은 정수 알고리즘을 실행하는 보조 비교군을 마련하는 것이다.

| 비교 | 확인할 내용 | 해석 범위 |
|---|---|---|
| ARM float32 ↔ ARM fixed C | 같은 CPU에서 수치 표현과 구현 변경의 정확도·실행시간 영향 | 양자화만의 순수 효과라고 단정하지 않음. 알고리즘·라이브러리·최적화 차이 기록 |
| ARM fixed C ↔ PL fixed RTL | 같은 정수 계약을 서로 다른 실행 구조로 구현한 결과 | 단계별 비트 일치 확인. CPU/PL 클럭, 병렬성, 전송과 측정 범위를 함께 제시 |
| Python 정수 모델 ↔ fixed C ↔ fixed RTL | 정수 값·프레임 순서·BFP 지수·상태의 일치 | float64와의 오차 비교와 별개의 검사 |

네 주 비교군은 유지하고 ARM fixed C를 보조 실험으로 추가할 수 있다. 구현하지 못했다면 ARM float32 대 fixed HW의 차이를 수치 표현 변경과 하드웨어 구조 변경이 함께 포함된 결과로 설명한다. Python 정수 모델 실행시간을 ARM 성능으로 대신 사용하지 않는다.

권장 순서는 **전체 Python 정수 계약 완성 → 별도 fixed C 이식·PC 비트 비교 → ARM 실행 및 측정**이다. 확정된 단계의 C·RTL 이식은 병행할 수 있다. 현재 혼합 경로를 그대로 C로 옮긴 결과를 전체 fixed C라고 부르지 않는다.

C 구현 시 지킬 계약:

- 전체 경로를 일괄 INT8로 바꾸지 않는다. 각 단계의 W/F와 프레임 지수, 곱·누산 guard bits를 따른다.
- 20비트 값을 `int32_t`, 현재40/60비트 비음수 값을 적절한64비트 정수에 저장할 수 있지만, 저장 자료형이 논리 폭·반올림·overflow 동작을 자동으로 구현하지는 않는다. 계산 상한과 signed/unsigned 변환도 확인한다.
- 계수 정수값과 생성 버전을 공유하고, 하드웨어와 같은 경계에서 quantize·shift·wrap/saturation을 명시한다. signed overflow나 음수 shift의 언어·컴파일러 동작에 기대지 않는다.
- PCM 전처리 상태, 프레임 경계, FFT 순서, BFP 지수, Power/Mel, 정수 log/DCT, raw13 정수 출력과 상태를 Python 모델과 단계별 대조한다. 최종 MFCC 그림이 비슷한 것으로 대체하지 않는다.
- float64 대비 수치 오차와 정수 모델 대비 비트 일치를 각각 기록한다. 현재 C float32의 합성 입력 실패 기록도 보존한다.
- CPU 계산시간, PL 계산시간, 데이터 이동을 포함한 전체시간을 구분한다. 전체 MFCC와 FFT만의 시간을 직접 가속비로 비교하지 않는다. 같은 PCM·프레임·출력 범위를 사용하며 컴파일 옵션, SIMD 여부, clock, cache, 반복 횟수, 실행 환경을 보존한다.

고정소수점 C가 float32보다 빠르다고 미리 가정하지 않는다. 넓은 정수 곱셈·누산과 비트정확 반올림 처리의 비용까지 실제 ARM에서 측정해야 한다.

## 앞으로 변경할 때 남길 기록

새 양자화 형식이나 연산 순서를 적용하는 작업은 이 문서에 날짜별 이력을 추가하고 상세 보고서·새 실행 폴더를 연결한다. 기존 결과·실패 기록은 덮어쓰지 않는다.

| 기록 항목 | 내용 |
|---|---|
| 상태와 범위 | 제안/실험/채택/보류/대체, 변경 단계, 담당 작업과 날짜 |
| 변경 전후 계약 | 입력·계수·곱·누산·출력 W/F, signed, BFP, shift, 반올림/tie, saturation/wrap |
| 문제와 가설 | 어떤 입력·어떤 중간 단계에서 왜 바꾸려는지, 다른 대안 |
| 재현 근거 | 규격·소스·입력·계수 hash, 도구 버전, 실행 ID, 명령, 원시 정수와 출력 위치 |
| 수치 결과 | 전체 프레임 max/RMSE, 계수별 오차, overflow/clipping/floor 회귀, 실패 입력 |
| 구현 결과 | 모델·C·RTL 각각의 검증 상태, 실제 수행한 합성·구현·보드 측정과 범위 |
| 결정과 한계 | 채택/보류 이유, 남은 오차, 미검증 사항, 다음 변경이 이전 결정을 대체하는지 |

논문 작성 시 각 주장에 위 근거를 연결하고, 선행 자료에서 가져온 설계·코드와 본 프로젝트의 변경·실험을 구분한다. 수치 실험만 끝난 내용을 RTL 구현 완료 또는 보드 성능 입증으로 서술하지 않는다.

## 2026-10-04 전체 정수 후보와 C 계약 발행

위의 “전체 정수 모델 미완성”, “새20비트 RTL 미검증”, fixed C 권고 문장은 정밀도 실험 종료 시점의 기록이다. 이번 작업은 사용자의 후속 지시에 따라 Python 정수 모델·수치 계약·fixed RTL을 담당하고, C/ARM 구현은 별도 작업자가 담당한다. C 작업자의 파일·문서는 수정하지 않는다. 아래 이력이 당시 상태를 대체하며 과거 측정은 그대로 보존한다.

### 변경 계약과 채택 범위

| 단계 | 이전 | 새 후보 | 이유·상태 |
|---|---|---|---|
| PCM/preemphasis | float64 | PCM signed16/F15; `RNE((20*x−19*prev)*2^15/20)` signed32/F30 | alpha19/20, clip 경계에서만 previous PCM 초기화. 전체 정수 모델 채택, 정확도 합격과 별개 |
| Hamming window | float64 | 계수 unsigned31/F30, 곱 signed63, RNE >>30, 출력 signed32/F30 | 기존 window 표에서 offline ties-even 생성. 실행 중 float 없음 |
| BFP | float peak 선택 | Q30 peak로 `peak*2^(s−16)<=31949`, s[-2,24] 최대 선택 | 반올림 전 선택 유지. silence s0, 출력 q16 대칭 clamp±32767 |
| FFT/Power/Mel | prec04 입력16/내부·출력20 후보 | 기존 후보 유지:16/F15→20/F19, S9, twiddle16/F15, Power40/Mel60 | 전체 폭 실험 재실행 없이 이미 선택한 계약을 포팅 대상으로 고정 |
| log | float64 ln | unsigned60 Mel+지수 → Q31 정규화,30회 정수 제곱 → ln2 정수 곱 → signed30/F24 | 공통 floor1e-12 유지. 음수 에너지는 error. 정수 제곱64비트·ln2 변환용 넓은 곱 필요 |
| DCT | float64 | signed31/F30 계수, signed61 곱, signed64 누산, 단일 RNE >>30, signed40/F24 출력13개 | 누산 절댓값 상한2,538,068,713,882,680,420. runtime float 없음 |

정수 coefficient 생성에 사용한 float64와 오차 측정용 float64는 런타임 정수 경로와 분리했다. 전체 후보 실행은 `D:/2610_MFCC/build/fixed_full_model/integer_02_20261004`이며, `run_manifest.json/source_sha256`, `coefficient_sha256`, 입력별 SHA와 source snapshot으로 재현한다. `full_integer.py` SHA-256은 `be94ae09ba183f72ada50bc06f48fa70e8817555790ec5935750fde85613e137`이다. `integer_01_20261004`도 보존했고,02는 문서의 누산 폭 정정·입력 지수 유효성 검사와 단위 검증 후 실행이다. 두 실행의 정수 출력은 같다.

### 전후 수치 결과와 남은 실패

| 범위 | prec04 혼합 경로 MFCC max / RMSE | integer02 전체 정수 max / RMSE | 판정 |
|---|---|---|---|
| 개발 음성534프레임 |0.0551714891 /0.00625815973 |0.0551715024552 /0.00626024471527 | 개선된 후보 수준 유지, 허용 기준 미정 |
| 합성23입력82프레임 |6.5915942256 /0.894057107 |6.591594298125 /0.894057095442 | 큰 잔여 오차 보존, 정확도 합격 아님 |

0프레임 입력까지 총24개를 보존했고 평가 음성20개는 사용하지 않았다. 정상616프레임의 FFT overflow와 입력 clipping은0이다. log/DCT 정수화만 더하는 최대 오차는 약3.745e-7이며 log의 독립 ln oracle 최대 오차는 약3.209e-8이다.

`integer_02_20261004/residual_analysis.json`으로 잔여 오차를 추적했다. 모든 BFP 지수는 prec04와 같고, 개발 음성의 FFT 입력 정수216/273,408개만 Q30 전처리 도입으로 변했다. 큰 오차를 가진 세 합성 입력의 FFT 입력은 기존과 같았다. floor 회귀60건은 `fullscale_alternating`24, `tone_bin32_1000hz`16, `silence_to_fullscale`20이며 모두 정수 Mel T=0이다. 예를 들어 silence_to_fullscale frame5/6 Mel3은 기준 에너지 약2.698910129e-10이 FFT/Power/Mel 양자화 후0이 되어 log 오차 약−5.59801825를 만든다. 이 손실은 정수 log 이전에 발생한다. floor를 바꾸거나 실패 입력을 제외하지 않았으며 추가 폭 정책 변경은 채택하지 않았다.

재현 스크립트는 `scripts/analyze_fixed_integer_residuals.py`, 정식 상세 산출물은 같은 실행의 `residual_diagnosis.json`이다. FFT 한 출력코드의 공통 진폭 격자는 `2^(−9−s)`, Mel 가중 전 한 bin의 단위 제곱 Power는 `2^(−27−2s)`다. s0에서는 약7.45058e-9, s2에서는 약4.65661e-10으로 공통 floor1e-12보다 크다. 따라서 작은 여러 bin의 기준 Mel 합이 floor보다 커도 각 FFT bin이0으로 양자화되는 문제가 가능하다. 현재 큰 오차를 log 근사 비트 확대만으로 해결할 수 있다는 근거는 없다.

### 불변 C 인계 버전

| 버전 경로 (`D:/2610_MFCC/build/fixed_contract/` 아래) | 범위 | 발행 검증 | 별도 상태 |
|---|---|---|---|
| `v1_fft20_power40_mel60_20261004_r2` | FFT→Power→Mel,634프레임, 전체512 복소 bin, 음수tie·BFP·floor·complex overflow/복구 |7,136검사,324,608복소bin, 정상616+경계18 | C 이식 가능. 정확도 미합격. 발행 시점 RTL 미검증 |
| `v2_pcm16_mfcc40_20261004` | PCM→MFCC,24입력616프레임, 모든 정수 단계·계수·전체512FFT |2,293검사,1,586,734정수원소 재계산, log경계214/BFP경계75 | 전체 C 이식 가능. 정확도 미합격. 발행 시점 RTL 미검증 |

각 버전은 `contract.json`, 정수 계수, `model_snapshot`, little-endian 입력·정답, `artifact_hashes.json`, 별도 verifier PASS를 작성·검사한 뒤 `PUBLISHED.json`을 마지막으로 쓰고 staging을 원자적으로 rename했다. 발행 후 수정하지 않는다. v1 최초 staging은 실수부만의 랜덤 스트레스에서 overflow를 찾지 못해 발행하지 않았고 보존했다. r2는 복소 signed16 극값 스트레스에서 실제 overflow1프레임과 정상 복구를 확인했다. 숫자 출력 실패를 숨긴 것이 아니라 미충족된 경계 검증을 보강했다.

아직 미정인 것은 알고리즘 정확도 허용 기준·응용 합격, 보드 clock/IO/PS 연결·성능, 별도 C/ARM 실행 결과다. RTL 증거는 발행된 모델 계약을 수정하지 않고 별도 새 실행 및 [RTL 구현 검증 보고서](reviews/FIXED_POINT_RTL_IMPLEMENTATION.md)에 기록한다.

### RTL 구현 변경 이력

원본 previous_fft13개 파일 hash와 작업 복사본 hash는 `hardware/fixed/fft/PROVENANCE.json`에 기록했다. signed20 확대, guard21/22, 곱38/복소합39, 단계별 RNE, 물리S9와 자연순서를 구현했다. 원본 ready 없는 FFT를 정지 가능하다고 가정하지 않고, wrapper가 첫 입력 전에 프레임 저장 공간을 예약한다.

초기 reorder의 `ram_style=block`만으로는 Vivado8-6849 LUTRAM fallback이 발생했다. 실제 보고서를 근거로 네 논리bank를 단일2048x40 simple-dual-port 메모리에 주소화하고, reset 없는 동기 read로 수정했다. 변경 후41프레임20,992복소bin·overflow8개 비트 일치와 기존 첫/마지막 출력cycle 동일성을 확인했다. 이는 메모리 구현 변경이며 수치 계약 변경은 아니다. 초기 실패·후속 실행은 `build/fixed_fft/`에 모두 보존한다. 전체 RTL/BRAM/타이밍 완료 범위는 아래 연결 보고서의 실제 실행 결과를 따른다.

## 2026-10-04 통합 RTL 타이밍과 제어 보정

다음 수정은 정수 결과·W/F·계수·floor 정책을 바꾸지 않으며 v2 계약을 그대로 사용한다. 구형 실행도 비트 PASS와 timing FAIL을 분리해 보존한다.

| 실제 발견 | 수정과 이유 | 보존 실행 |
|---|---|---|
| FFT20 단독 버퍼 BRAM 매핑 |2048x40 reorder의 실제 RAMB36×2+RAMB18×1 확인. 단독route100MHz WNS+0.156ns/WHS+0.121ns | `fixed_fft/fft20_bram_03` |
| Power/Mel 단독100MHz는통과했지만 FFT통합 경로는실패 | FFT RAM출력→제곱→합산→overflow 경로를 입력register·제곱register로 분리하고 bin/valid/last/overflow도 함께2cycle지연 | `fixed_spectral/spectral_full_03`:634프레임비트PASS,routeWNS−2.383ns |
| frontend의 `/20` 경로 WNS−14.458ns,pre-ring LUTRAMfallback | `RNE((20*x−19*p)*32768/20)=(x−p)*32768+RNE(p*8192/5)`를 이용.16비트 /5와 remainder상수{0,1638,3277,4915,6554},단계별register로 동일정수값 계산. pre-ring은 XPM BRAM으로 강제,window곱도register | `fixed_integer_rtl/units_02_20261004` 계열 원시 보고서 |
| backend 계수ROM→곱→합→round 경로 WNS−6.342ns | DCT 계수·곱register 및최종round상태 분리,log ln2곱register 추가. 같은 누산순서와단일RNE 유지 | 초기단위backend8034값PASS/합성timingFAIL 기록보존 |
| 전체top의 clip완료 watchdog | frontend done도착·모든MFCC일치·in-flight0에도종료상태X 관측. 등록된bool만 읽는 중간 수정도 실패해 원인을 `_next` 재참조만으로 단정하지 않는다. 최종 명시적 ACTIVE→DRAIN FSM은 frontend EOF 이후 in-flight0을 확인하여 완료 pulse를 낸다 | 실패06/08 보존; `fixed_full_rtl/full_completion_fsm_20261004_09` 7입력10프레임 PASS |

전처리 항등식은 clip의연속 previous PCM을그대로쓰며, 식앞의 `(x−p)*32768`이짝수정수라 ties-even도보존된다. C 런타임은기존명세식이나이항등식모두가능하지만 음수signedshift 대신안전한곱/나눗셈을쓴다. Python모델과발행계약을 타이밍수정때문에재발행하거나수정하지않았다. 후속통합실행은각변경후새snapshot으로전체벡터를다시검사한다.

### C 계약 metadata 수정 발행

독립 recursive descriptor 감사에서 최초 v2의 `front.mel.coefficients.file`이 실제 `coefficients/mel_q16.bin` 대신 이전 v1의 파일명을 가리키는 오류를 찾았다. 수치·hash 자체는 같지만 C 기계 판독 경로로 부적합하여 이전 v2를 현재 인계본으로 사용하지 않는다. 발행본은 수정하지 않고 `D:/2610_MFCC/build/fixed_contract/v2_pcm16_mfcc40_20261004_r2`를 새로 발행했다. 수치 형식·정책·오차·채택 상태 변경은 없다.

r2는 descriptor containment/존재/SHA/자료형·shape 검사를 추가하고 log/window/DCT W/F·부호·guard, twiddle descriptor, BFP clamp flag, int64 log 분해 설명을 명시했다. `numerical_identity.json`에서 이전426개 수치 파일의 byte 동일성을 확인했다. 발행 전7,099검사/발행 후7,107검사,437 artifact hash,415 descriptor,616프레임1,586,734정수원소가 PASS다. contract SHA는 `283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e`다. 이전 검증기가 경로 결함을 놓친 사실도 보존한다.

### 전체 비트 일치와 후속 타이밍 수정

`fixed_full_rtl/full_616_20261004_04`에서24입력616프레임 PCM→MFCC 통합의 모든 정수 단계·metadata·clip완료를 최초로 확인했다. frontend/FFT각315,392개(FFT복소),Power158,312개,Mel/log각16,016개,MFCC8,008개,불일치0이다. stall15,485cycle과 partial PCM/FFT진행중 reset을 포함한다. 타이밍 수정 전 증거이므로 후속source 검증으로 대체하지 않는다.

`fixed_integer_rtl/synth_05_20261004`는 frontend2 RAMB18/LUTRAM0,backend1 RAMB18/LUTRAM0을 확인했지만100MHz WNS는−3.817ns/−1.891ns다. frontend는 실제s[-2,24]에 맞춰양자화 quotient40/mask18로 제한하고 RNE·clamp를 다음cycle로 분리한다. log는 32비트mantissa의 unsigned16 hi/lo 부분곱3개를 register하고, 정확한64비트 제곱값을 결합·register한 다음 기존정규화·bit선택을 수행한다. 전후 수치W/F·반올림·계수·오차는 같고 log당60cycle만 추가한다. 최종 채택은 후속 비트/구현 보고서로 판단한다.

| 후속 실행 | 소스 SHA 앞12자리·변경 | 비트 검증·타이밍 결과 |
|---|---|---|
| `front_06_20261004` | `9637ee417e4c`, quant 임시64→40/마스크18, RNE분리 |315,392출력 PASS; 합성WNS−1.741ns,window RNE→abs→peak가 다음critical |
| `front_08_20261004` | `7f637bd44192`, window rounded32와magnitude32 등록 |315,392출력 PASS; 합성WNS−0.391ns,pre correction→BRAM쓰기 critical |
| `front_09_20261004` / `front_synth_10_20261004` | `33408df2469b`, pre correction signed33 등록 |315,392출력 PASS; 합성내부9경로+0.234ns, 단위외부o_last OBUF경로−0.217ns |
| `back_07_20261004` | log `627f1c3eebf4`, exact64 square를16bit부분곱/결합/정규화로 분리 |log1,029건 및MFCC8,034개 PASS; 합성WNS+0.430ns |
| `full_616_20261004_04` | 초기 전체FSM+파이프라인 snapshot |616프레임 PASS; routeWNS−2.991ns. 입력XDC오류도 있어최종IO제약합격아님 |
| `full_616_pipeline_20261004_05` | quant분리·log부분곱 적용, window peak경로 분리 전 |616프레임 PASS,14,374,321cycle; 올바른I/O2ns의routeWNS−0.343ns/WHS+0.070ns |

위 source의 전체 hash는 해당 실행 manifest/snapshot에 기록했다. 실패 source·실행은 보존한다. `front_11_20261004`는 RAM동기read와같은edge에window coefficient31을등록하여 다음product와index를맞춘다. 추가cycle 없이 ROM→DSP 경로를 분리했으며 최종full 실행에서 채택 여부를 확인한다. 모든수정은contract v2 r2의정수파일과동일한수치계약을유지한다.

## 2026-10-04 전체 RTL 완료·재현 프로젝트 확정

최종 `front_11_20261004`의315,392개 단위 출력이 일치했고, `front_synth_12_20261004`에서 내부경로 최소+1.075ns, BRAM18×2/LUTRAM0을 확인했다. 단위 외부 `o_last` OBUF 경로−0.217ns는 실패 이력으로 남긴다. 전체 설계의 내부 연결과 보드 외부 IO는 별도 범위다.

`D:/2610_MFCC/build/fixed_full_rtl/full_616_final_20261004_06`은 최종 RTL로 개발534프레임+기존17합성40프레임+추가6합성42프레임을 검증했다.0프레임 case를 포함한24입력을 유지했다. frontend315,392개,FFT315,392복소값(매프레임512개),Power158,312개,Mel/log각16,016개,MFCC8,008개가 정수 모델과 모두 일치했다. 입력gap·연속프레임·마지막배출·모든clip-done,173PCM 입력 도중 reset,FFT진행중 reset,출력stall15,457cycle(연속5,000cycle 포함)을 검사했다. 총14,410,168cycle,불일치0이다. overflow는 별도634프레임 spectral 및41프레임 FFT 경계 검증에서 검출·정상복구까지 확인했다.

최종 frontend SHA-256은 `421bad8c67cae2bb3ebb01f9d1b1f24519787fbf4db5b8e674db029da43943f9`, log는 `627f1c3eebf4429d6288a225b786ba9a36bf7d22c311438ab86b4cb7ccf8abd6`, top은 `b50f4524a3084245f7fe4688baf0e782c3f6ab9097e4eca364abbf01acda134c`다. 다른 RTL·TB·계수는 각 실행의 전체 source hash 목록에 연결한다. 정수 모델·W/F·연산순서·반올림·floor 정책·계수·수치오차는 이전 v2 r2와 동일하다. 파이프라인과 제어·저장 구현만 채택했다.

06은 Vivado2024.2/xc7z020clg400-1에서100MHz,입출력delay2ns OOC 합성·배치·배선을 통과했지만, 완료된 `.xpr`가 가리키는 root 계수 두 파일이 사라져 재오픈 재현성이 부족했다. 원본source/XSim/합성cache의 계수 SHA는 같고 실제 합성read 성공도 확인했다. 삭제 원인은 입증하지 않았다. 이전 실행과 최초 감사 실패를 보존했다.

새 `D:/2610_MFCC/build/fixed_full_rtl/full_616_repro_20261004_07`에서는 Tcl 계수참조를 보존source snapshot으로 바꿨다. RTL/TB/계수 및 모든 입력·정답 벡터가06과 byte 동일함을 검사한 뒤06의 완료된 simulation을 명시적으로 재사용했다.07에서 합성·배치·배선을 다시 실행했으며 다음 결과를 최종 채택한다.

| 항목 | 최종 결과 |
|---|---|
| 합성 자원 | LUT5,775 / FF2,388 / DSP40 / RAMB36×9+RAMB18×7(12.5tile) |
| 배치·배선 자원 | LUT5,455 / FF2,391 / DSP40 / BRAM12.5tile / latch0 |
| 정적 타이밍 | WNS+0.506ns / WHS+0.097ns / TNS·THS0 / timing제약12종 위반0 |
| 메모리 실제매핑 | pre-ring/window/FFT reorder/Power/DCT log 모두 BRAM. 기존FFT 내부 LUTRAM894/SRL65는 유지 |
| DRC | error0,경고51개(DPIP-1×11,DPOP-1×9,DPOP-2×30,ZPS7-1×1); OOC의 외부핀·PS·clock배치 미검증 |
| 독립 감사 | `full_616_repro_20261004_07_audit.json`:3,065검사 PASS.26개 프로젝트파일참조·원본FFT13개·source/벡터/계약SHA·재사용simulation증거·실제BRAM/타이밍 확인 |

07 run manifest SHA-256은 `6b29205e6d077682c5e5484c3021ef1366ace357fa07208e952c70e3640a1325`, artifact manifest는 `f9c5177e300dc466afa653c4dbbc4f13e09f750a4125dba70d4f1cb759908eb3`, 외부감사 JSON은 `3ede40f4c0957000d0dfee3329f5a226d819b26cd49d4100adaedb2e9ec17d0d`다. 감사는07의60개와 원simulation06의125개 artifact를 검사한다. 처음 감사기가 필수라고 가정했던 임시계수/cache 경로 대신, 실제 보존 프로젝트참조·합성read로그·XSim 사본을 검증하도록 보완했다.

채택 상태는 **C 이식 계약 발행 완료 / 전체 behavioral RTL 비트검증 완료 / 합성·배치·배선·100MHz OOC 정적타이밍 완료 / 수치 정확도 미합격**이다. 개발 RMSE0.00626024471527,합성 최대6.591594298125·RMSE0.894057095442,floor 회귀60건을 그대로 남긴다. 정확도 허용 기준과 응용판정,보드 clock/IO/PS연결·실행·성능,post-route timing simulation,C/ARM 구현결과는 아직 확정하지 않았다. 평가20개는 사용하지 않았고,보드·커밋·푸시도 수행하지 않았다. 고정된 C 인계 버전은 계속 `fixed_contract/v2_pcm16_mfcc40_20261004_r2`이며 발행 시점 RTL상태 필드는 수정하지 않는다.
