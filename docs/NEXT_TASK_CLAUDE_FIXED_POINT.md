# Claude 다음 작업: 양자화 계약과 고정소수점 선행 실험

작성: 2026-10-04 KST. 이 지시는 이전의 FFT→Power/Mel 실험 프롬프트를 대체한다. **Q0(양자화 계약/기반 도구)와 Q1(부분 정수 경로 실험)을 수행**하고, 전체 RTL 구현은 시작하지 않는다.

## 전달할 작업 지시

너는 이 프로젝트의 고정소수점 설계·RTL 검증 담당이다. RTL을 위한 Python 정수 비트모델도 담당 범위다. 먼저 루트 `AGENTS.md`, `CLAUDE.md`, 다음 문서를 실제로 읽어라.

- `docs/RTL_CODING_RULES.md`
- `docs/FIXED_POINT_DEVELOPMENT.md`
- `docs/MFCC_SPEC.md`
- `docs/PYTHON_REFERENCE_RESULTS.md`, `docs/C_REFERENCE_RESULTS.md`
- `docs/reviews/FFT_REUSE_REVIEW.md`, `docs/reviews/FFT_SPEC_HANDOFF.md`

### 1. 공통 기준과 수정 범위

현재 C float32는 ARM SW 비교군으로 유지한다. 고정소수점 RTL의 비트정확 oracle로 사용하지 않는다. 기존 Python float64, 공통 PCM, 계수 원본, 이전 실행 snapshot, C·ARM·platform 코드, 기존 FFT 원본을 수정하지 않는다.

comparison_raw13의 연속 pre-emphasis, 512/160 full frames, window, power /512, Mel26, ln(max(E,1e-12)), C0…C12를 유지한다. lifter·에너지 대체·delta·CMVN은 추가하지 않는다. 주 비교에서 저음량 프레임을 제외하지 않는다.

### 2. 이전 인계 문서의 정정

앞선 검토를 반영해 네 FFT 검토/인계 문서의 잘못된 설계 제안을 정정한다. 로그와 과거 실행은 보존한다.

- 실제 연속 출력 기록에 4개 미완료 프레임이 겹치는 구간이 있으므로 max3/3N FIFO 충분성 주장을 철회한다. 깊이와 credit 방식은 후속 protocol 설계에서 검증한다.
- FFT RMS에서 유도한 오차 전력을 모든 신호의 실제 Mel 에너지 하한으로 부르지 않는다.
- 에너지0/저음량 프레임 제외 제안을 제거한다. 추가 층화 분석만 허용한다.
- BFP 지수까지 반영한 에너지에 공통 로그 floor를 적용한다. 양수 정수 에너지도 floor 아래일 수 있다.
- 기존 BFP 실험의 양자화 위치와 큰 진폭 fallback, α 배율을 실제 스크립트와 맞춘다. 양자화 후 shift와 양자화 전 배율 선택은 다르다.
- Mel 계수 양자화의 오차를 누락하지 않는다. 생성 스크립트 없는 표는 재현 가능하다고 표시하지 않는다.
- clamp가 없앤 것은 시험한 입력군에서 관측된 overflow다. 유일 원인에 대한 일반 증명처럼 표현하지 않는다.

### 3. Q0: 양자화 계약과 검증 도구

PCM부터 pre-emphasis/window/FFT/Power/Mel/log/DCT/출력까지 각 단계의 W/F/부호/배율, 곱·누산 폭, 반올림 위치·정책, overflow 정책과 범위를 표로 작성한다. 확인된 값·후보·미확정을 구분한다. log/DCT처럼 이번에 구현하지 않는 단계는 후보와 후속 검증 항목만 기록한다.

Python 정수 모델에 필요한 명시적 rounding/shift/wrap/saturation 도구를 만들거나 기존 검증된 도구를 재사용한다. 양·음 half-LSB tie, 경계값, overflow 검출을 독립적인 예상값으로 검사한다. Python 무제한 정수로 full precision 연산 후 명시적인 하드웨어 경계에서 폭을 적용한다. float 배열 마지막에 round만 하는 모델은 금지한다.

기존 FFT 비트모델을 재사용하면 출처와 hash, 지원 N/설정, 기존 round/wrap 동작을 기록한다. 스타일 때문에 RTL을 다시 쓰지 않는다.

### 4. Q1: 개발 입력으로 후보 비교

기존 개발 음성1개(534프레임 전체)와 합성17개를 사용한다. 무음, 저진폭, 최대 진폭 교대, 1kHz tone, BFP 배율 전환 경계를 포함한다. 추가 합성 입력은 별도 목록·hash로 관리한다. 평가 음성20은 이번 작업에서 사용하지 않는다.

A. 기존 float64 기준
B. 고정 α=1/2 → Q15 입력 → 기존 FFT 비트모델 → 정수 Power/Mel
C. 양자화 전에 프레임 지수를 고르는 BFP 후보1개 → 같은 정수 경로

기존 Q15 후 shift 방식은 필요하면 보조 비교로 추가한다. 각 후보의 shift 범위, 무음, 큰 진폭, tie, clamp 정책을 명시한다. BFP는 이미 사라진 정보를 복원하는 방법이 아니다.

전처리/window와 후단 log/DCT는 우선 float64를 사용할 수 있다. 이를 **혼합 정밀도 실험**이라고 표시한다. fixed 전처리에서 BFP 지수를 고르는 방법과 전체 정수 log/DCT는 후속 Q2 항목으로 남긴다.

Power/Mel은 곱·누산의 정수 폭과 signedness를 명시적으로 처리한다. `FIXED_POINT_DEVELOPMENT.md`의 배율 식을 직접 유도·검사하며, Mel 가중치 소수부16은 우선 후보일 뿐 최종 확정값으로 쓰지 않는다. coefficient quantization과 FFT input/stage quantization의 오차를 가능한 범위에서 분리한다.

### 5. 평가와 산출물

- FFT/Power/Mel/log-Mel/MFCC의 전체 프레임 max/RMSE, C0·C1…C12 오차, input quantization loss, clipping/overflow, Mel zero/floor 횟수와 shift별 분포를 보고한다.
- 기준/후보/차이 히트맵은 같은 프레임·축과 비교 가능한 색 범위를 사용한다. 구간별 분석으로 전체 결과를 대체하지 않는다.
- 아직 fixed 허용치가 확정되지 않았으면 측정값과 합격 기준 제안을 구분한다. 평가 결과에 맞춰 floor/입력/정답을 바꾸지 않는다.
- 모델은 `software/fixed_model/`, 검사 코드는 `verification/fixed/`, 실행기는 `scripts/run_fixed_pilot.py`를 기본 위치로 한다. 기존 파일이 있으면 덮어쓰기 전에 현재 작업 상태를 확인한다.
- 결과·덤프는 `D:\2610_MFCC\build\fixed_pilot\<run-id>`에 저장한다.
- `docs/reviews/FIXED_POINT_QUANTIZATION_PLAN.md`에 단계별 수치 계약/미확정을, `docs/reviews/FIXED_POINT_PILOT.md`에 실제 실험 결과를 기록한다.
- 재실행 명령, 도구/환경, 규격·입력·계수·소스·모델 hash, seed, 실패 및 제한을 남긴다.

### 6. 완료 보고와 다음 단계

고정 배율/BFP 중 어떤 후보가 나은지 전체 MFCC 오차와 overflow를 근거로 제안한다. Q2에서 먼저 양자화할 단계, 아직 비트정확하지 않은 단계, 다음 RTL 모듈을 구체적으로 적는다. 혼합 모델을 전체 fixed 모델 완성으로 보고하지 않는다.

이번에는 공통 MFCC_SPEC을 직접 바꾸지 말고 인계 문서에 제안을 남긴다. 새로운 RTL, FIFO/DMA/AXI, 보드 실행, 전체 로그/DCT RTL을 범위에 추가하지 않는다. 커밋·푸시는 하지 않는다.

향후 RTL 작성 시 `RTL_CODING_RULES.md`의 금지/명명/2-process/리셋 규약을 따라야 하며, 기존 FFT 예외를 신규 코드까지 자동 확대하지 않는다.
