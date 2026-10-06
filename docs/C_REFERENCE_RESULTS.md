# C float32 MFCC 구현·PC 검증 결과

작성: 2026-10-04 KST. 대상은 `comparison_raw13`만이다. 공통 수학 정의, PCM 입력, Python 정답과 로그 하한을 유지했다.

## 1. 완료 상태

**C11 전체 MFCC 경로와 PC 빌드·검증·재현 환경을 구현하고 실행했다. 개발 음성과 평가 음성 20개는 목표 오차를 통과했지만, 합성 입력 2개는 미달했다. 따라서 모든 입력에서 초기 정확도 목표를 만족한 골든 모델로 승인한 상태는 아니다.**

| 항목 | 직접 확인한 결과 |
|---|---|
| 구조·상태·FFT 계약 | 코어 시험 12/12, PC 입출력 시험 4/4 통과 |
| 합성 입력 17개 | 15개 수치 통과; fullscale alternating, 1 kHz bin32 tone 실패 유지 |
| 개발 음성 1개 | 534프레임, MFCC 최대 절대 오차 1.928850e-5, RMSE 3.061622e-6; 모든 단계 통과 |
| 평가 음성 20개 | 총 9,501프레임, 모든 단계 통과 |
| 평가 MFCC | 최대 절대 오차 8.017005e-5, 전체 원소 RMSE 2.672533e-6 |
| NaN/Inf·누락·프레임 순서 | 합성·개발·평가에서 구조 검사 실패 없음 |
| 전체 실행 상태 | `completed_with_numerical_failures`, Python 프로세스 종료 코드 **2** |
| 재현성 | 별도 재빌드·전체 재실행의 수치/계수 파일 505개 SHA-256 일치; 수치 실패도 재현 |
| ARM/보드 | ARM GCC 버전 확인만 수행. ARM 빌드·보드 실행·성능·전력·인식률 미검증 |

`host_manifest.json`의 `status=passed`는 C 처리와 입출력이 정상 종료했다는 뜻이다. Python 수치 허용치 통과는 별도의 `validation.json`에서 판정한다. 최상위 `run_manifest.json`은 두 합성 실패 때문에 `numerical_acceptance_passed=false`를 유지한다.

## 2. 실행 방법과 파일 위치

이미 준비된 Python 3.11.9 venv와 이 PC의 MSVC를 사용한다. 다음 명령 하나로 계수 생성 → C 컴파일 → 계약/합성/개발 검사 → 정밀도 진단 → 조건 동결 → 평가20 → 그림/manifest를 재생성한다. `--run-id`를 생략하면 실행별 새 폴더를 만들고 기존 결과를 덮어쓰지 않는다.

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B 'D:\2610_MFCC\project\scripts\run_c_reference.py' --phase all
$LASTEXITCODE
```

현재 고정 구현을 재실행하면 알려진 수치 미달을 포함해 **2**가 예상된다. 0은 모든 수치 기준 통과, 1은 빌드·구조·예상하지 못한 개발 실패 등 실행 오류다. 단순히 종료 코드를 0으로 바꾸거나 실패를 skip하지 않는다.

이번 실제 최종 실행은 다음과 같다.

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B 'D:\2610_MFCC\project\scripts\run_c_reference.py' --phase develop --run-id dev_frozen_02
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B 'D:\2610_MFCC\project\scripts\run_c_reference.py' --phase evaluate --run-id eval_frozen_01 --development-run 'D:\2610_MFCC\build\c_reference\dev_frozen_02'
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B 'D:\2610_MFCC\project\scripts\run_c_reference.py' --phase all --run-id reproduce_01
```

각 이름은 이미 존재한다. 그대로 반복하면 덮어쓰기 방지를 위해 거부하므로 새 이름을 쓰거나 생략한다. `evaluate`는 개발 때 컴파일한 동일 실행파일의 해시를 검사해 사용하며, 소스·현재 규격·계수·컴파일러/옵션·Python 도구 환경·허용치·Python 기준 식별자가 달라지면 중단한다.

| 저장소 파일 | 역할 |
|---|---|
| `software/c/mfcc.h`, `mfcc.c` | 플랫폼과 독립된 상태·작업 버퍼·PCM 샘플 처리·MFCC 계산 |
| `software/c/fft32.h`, `fft32.c` | 자체 scalar radix-2 DIT FFT, 512점, O(N log N) |
| `software/c/host_main.c` | PC PCM 읽기, 명시적 LE 저장, 오류/메타데이터 기록 |
| `verification/c/generate_coefficients.py` | 기존 binary64 계수의 명시적 binary32 변환과 twiddle 생성 |
| `verification/c/compare.py`, `plots.py` | 단계별 구조/오차 검사, Python/C/차이 그림 |
| `verification/c/test_core.c`, `host_checks.py` | FFT·상태·reset 및 PC 입력/출력/청크 검사 |
| `verification/c/diagnose_precision.py` | 실패한 두 합성 입력의 정밀도 원인 분리; C 계산 결과와 구분 |
| `verification/c/tolerances.json`, `build_config.json` | 평가 전 고정한 단위별 허용치와 빌드 조건 |
| `verification/c/known_development_limits.json` | 개발 실패를 명시한 평가 진행 조건; 수치 통과로 바꾸지 않음 |
| `scripts/run_c_reference.py` | 한 명령 실행·동결·해시·실패 보존 |

상세 API와 배열 해석은 [C README](../software/c/README.md)를 따른다. 생성 계수·EXE·object·PCM 복사·그림·수치 덤프는 모두 `D:\2610_MFCC\build\c_reference\`에 있고 Git에 추가하지 않았다.

## 3. 계산·상태·정밀도 계약

코어에 파일, UART, 타이머, 동적 할당 호출은 없다. 호출자가 `mfcc_state`와 `mfcc_frame`을 소유한다. `mfcc_init()`으로 클립 시작에만 이전 입력·ring·카운터를 초기화하고, `mfcc_push()`에 PCM16 샘플 하나를 한 번씩 전달한다. 512샘플째 첫 프레임, 이후 160샘플마다 다음 프레임을 출력한다. 중첩 샘플은 ring의 이미 처리한 값을 재사용하며 pre-emphasis를 재적용하지 않는다. EOF flush나 꼬리 패딩은 없다. 서로 다른 인스턴스는 가변 전역 상태를 공유하지 않는다.

PC에서 관측한 작업 메모리는 state **6,184 bytes**, 단계 출력 frame **7,512 bytes**다. 별도의 기본 PC 입력 청크는 8,192 bytes다. 코어 버퍼는 고정 크기이고, PC 어댑터의 청크/파일 경로 할당은 프레임 루프 밖에서 수행한다. ARM에서는 정적 배치 또는 충분한 stack/linker 예산을 확보하고 실제 `sizeof`를 다시 확인한다. 직렬화 시 struct padding을 저장하지 않고 개별 배열을 쓴다.

| 단계 | 실제 연산과 순서 |
|---|---|
| 입력 | `(float)PCM / 32768.0f`; PCM16 정수와 2의 거듭제곱 나눗셈은 이 범위에서 정확 |
| pre-emphasis | binary32 alpha×직전 입력을 먼저 반올림한 뒤 현재 입력에서 뺌; 결과도 binary32 |
| window | pre-emphasis float × 변환된 Hamming float; 시간 순서 유지 |
| FFT | 입력 bit reversal 후 길이 2,4,…512의 9단 DIT butterfly; float 복소 곱을 네 곱셈과 덧셈/뺄셈으로 분리 |
| FFT 계약 | 음의 지수, 자연 순서 0…511, 무정규화; MFCC에는 bin 0…256을 사용 |
| power | float `re*re`, `im*im`, 두 값 합산, `/512.0f`; 내부 bin 두 배 가중이나 fixed FFT의 shift 보정 없음 |
| Mel | 각 필터에 대해 bin 0…256 오름차순으로 float 곱과 float 누산 |
| log | binary32로 변환한 1e-12와 max 후 `logf`; 음수/비유한 에너지는 실패 |
| DCT | **cosine+scale 방식**. Mel 0…25 순서로 float 곱·합산한 뒤 float scale을 한 번 곱함 |
| 출력 | C0…C12. lifter·C0 에너지 대체·delta·BFP·정규화 포함 DCT matrix 사용 없음 |

진단용 `frame_energy`는 편측 power 합을 기록하지만 C0에 대입하지 않는다. 계산 코어의 변수·상수·중간 저장과 누산은 float이며 double 누산으로 실패를 숨기지 않았다. static assert와 실행 기록에서 `sizeof(float)=4`, radix=2, mantissa=24, exponent 범위, `FLT_EVAL_METHOD=0`을 확인했다. 실제 PCM 처리 전에 `fegetround()==FE_TONEAREST`도 검사한다. `logf`의 입력/출력은 float이며 CRT 내부 근사의 모든 명령 정밀도까지 분석한 것은 아니다.

## 4. 계수 변환·빌드·출처

기준은 기존 `D:\2610_MFCC\build\python_reference\reproduce_01`이다. 그 실행이 완료됐는지와 프로파일·계수·입력/단계의 SHA-256을 검사한다. 생성기는 main 프로파일의 수학/정책 필드 전체를 검사하며 GitHub 설정을 조용히 받아들이지 않는다.

`window`, `mel_filters`, `dct_cosine`, `dct_scale`의 기존 `<f8` 파일을 읽고 **값 변환 `astype('<f4')`**을 수행한다. float 포인터 재해석은 하지 않는다. 정수 Mel edge는 int64로 보존한다. 변환한 float값을 정확한 C99 hex literal(`f` suffix)로 출력한 `mfcc_tables.c/.h`를 컴파일한다. 미사용 lifter/정규화 포함 matrix는 내보내지 않는다. FFT twiddle은 512점 음의 지수의 cos/sin을 float64로 생성 후 binary32로 변환하며 사분면의 정확한 0/±1도 고정한다. 생성기와 수치 라이브러리 버전, 원본/변환/생성 파일 해시·최대 변환 오차는 `coefficients/coefficient_manifest.json`에 있다.

명시된 alpha의 실제 binary32 값은 `0.949999988079071`, 로그 하한은 `9.999999960041972e-13`이다. 이는 원래 수학 상수의 float 표현이며 하한을 다른 알고리즘 값으로 변경한 것이 아니다. 생성기 검토에서 C literal 8,059개의 비트 패턴과 변환 binary 파일의 일치를 확인했다.

실제 컴파일러는 **MSVC cl 19.42.34435 x64**, MSVC tools 14.42.34433, Windows SDK 10.0.22621.0이다. 빌드 플래그:

```text
/nologo /TC /std:c11 /O2 /fp:strict /W4 /WX /MD
```

`/fp:strict`로 source 연산 순서를 보존하고 FMA contraction을 금지했다. `/fp:fast`, `/fp:contract`는 사용하지 않았다. 관련 근거는 [Microsoft /fp 문서](https://learn.microsoft.com/en-us/cpp/build/reference/fp-specify-floating-point-behavior?view=msvc-170)다. `CL`, `_CL_`, `LINK` 환경변수로 옵션이 주입되지 않게 제거한다. 실행별 `build/compiler.json`에 compiler/환경 스크립트와 CRT 파일 해시를 기록하고, `*_compile.log`에 실제 argv와 진단, `binaries.json`에 EXE 해시를 남긴다.

추가 외부 C MFCC/FFT 코드를 복사하거나 링크하지 않았다. radix-2는 기존 FFT 수학 알고리즘의 새 C 구현이며 새로운 알고리즘 발명을 주장하지 않는다. `logf`와 표준 입출력은 설치된 Microsoft CRT를 사용한다. Python 검증은 기존 venv의 NumPy 2.2.6, SciPy 1.15.3, Matplotlib 3.10.3을 사용했다. 출처·설치 라이선스 고지는 기존 Python 실행의 `provenance/packages.json`/metadata와 [Python 결과 문서](PYTHON_REFERENCE_RESULTS.md)에 있다. LibriSpeech CC BY 4.0 입력의 출처·해시도 기존 manifest를 보존했다.

## 5. 개발에서 정한 수치 기준과 실패 분석

모든 수치 비교는 원소마다 `abs(C-Python) <= atol + rtol*abs(Python)`이다. complex FFT는 복소 차이의 크기를 사용한다. shape/dtype/프레임 ID/시작 인덱스/byte 수/NaN/Inf/음수 에너지는 수치 허용치와 별도로 검사한다.

| 단계 | atol | rtol | 설정 근거 |
|---|---:|---:|---|
| 입력 변환 | 0 | 0 | PCM16/32768의 정확 표현 |
| pre-emphasis, frames, windowed | 2e-7 | 2e-6 | 정규화 진폭, 상수 양자화와 소수의 반올림, 최대 headroom 1.95 |
| FFT | 2e-5 | 2e-5 | 비정규화512 합산·9단 butterfly의 증가한 스케일 |
| power, Mel, frame energy | 1e-8 | 5e-5 | 정규화 진폭 제곱 단위와 양수 누산; 작은 에너지의 정확성은 후속 log 검사로 추가 확인 |
| log Mel | 1e-3 | 1e-5 | 자연로그 단위에서 작은 에너지 오차 증폭 감시 |
| DCT/MFCC | **1e-3** | **1e-5** | 사용자와 MFCC_SPEC의 초기 목표 유지 |

허용치는 최초 개발 수치 실행부터 유지했으며 **평가 결과를 보고 조정하지 않았다.** Python끼리 사용한 1e-10을 C에 적용하지 않았다. 중간 power/Mel의 최대 절대 오차가 atol보다 커도 상대항까지 포함해 판정한다. 작은 Mel 값이 에너지 허용치 안에 있어도 로그/MFCC가 실패할 수 있다.

| 개발 합성 입력 | 최초 허용치 위반 | log 최대 절대 오차 | MFCC 최대 절대 오차 | 최종 위반 원소 |
|---|---|---:|---:|---:|
| fullscale alternating | FFT | 0.1663935927 | 0.1048822224 | 26 / 39 |
| 1 kHz bin32 tone | log Mel | 0.0015472722 | 0.0023953216 | 6 / 39 |

나머지 15개(길이 경계6개, 복합파, ±DC, 임펄스2개, 난수, 무음, 작은 ±1, 비정수 bin tone)는 단계별 수치 검사를 통과했다. 두 실패에서도 프레임/유한값/순서는 정상이다. fullscale의 최초 FFT 위반 원소는 frame0/bin128로 복소 오차 `2.29268e-5`, 허용치 `2.15193e-5`다. 전체 FFT 최대 오차 위치와 최초 허용치 위반 위치는 같지 않다.

fullscale의 frame1/Mel13에서 Python energy는 약 `2.93561494e-12`, C는 `3.4670706e-12`다. 아주 작은 절대 차이가 상대적으로 약 18.1%가 되어 로그 오차 0.16639로 증폭된다. 1 kHz tone의 약 `5.72e-12` 밴드에서도 약 0.1545% 에너지 차이가 생긴다.

원인 분리를 위해 **진단에서만** C 중간값 이후를 float64로 계산했다. fullscale의 C pre-emphasis는 이미 Python pre-emphasis를 정확히 binary32로 반올림한 값과 같다. 그런데 이 값 뒤의 window/FFT/Mel/DCT를 모두 float64로 처리해도 MFCC 최대 오차 **0.03573054**, 위반 26개가 남는다. 교대 두 샘플의 작은 합이 Python `-1.5258789061e-6`에서 C `-1.4305114746e-6`로 바뀌어 작은 DC 성분에 6.25% 차이가 생긴다. 완전한 float64 windowed 값을 한 번 float32로 반올림한 뒤 이상적인 후속 연산을 해도 최대 오차 약 0.004791이 남는다.

이는 현재처럼 단계 경계에 하나의 float값을 저장하는 구현의 정밀도 민감도를 보인 것이다. 모든 가능한 float32 알고리즘의 불가능성을 증명한 것은 아니다. FFT 교체나 DCT 누산 변경만으로 해당 fullscale 사례가 해결된다고 보지 않으며, 이번에 혼합 정밀도나 수식·로그 하한 변경을 넣지 않았다. `diagnostics/diagnosis.json`에 두 사례의 원자료 해시·8개 진단 변형·실패 위치를 남겼고 `verification/c/diagnose_precision.py`로 다시 생성한다. 진단의 float64 연산을 C 성능 경로로 사용하지 않는다.

## 6. 조건 고정과 평가 결과

개발 미달을 숨겨 평가 통과의 전제로 만들지 않았다. `development_gate.json`은 **`passed=false`, `evaluation_eligible=true`**를 구분한다. 후자는 구조/코어/입출력 시험이 정상이고, 개발 음성이 통과했으며, 두 실패가 개발 중 분석·명시된 단계와 일치할 때만 같은 구현의 평가 관찰을 허용한다. 새롭거나 설명되지 않은 개발 실패는 평가를 막는다. 이 정책은 평가 전에 `known_development_limits.json`으로 고정했으며 평가 입력에는 실패 허용 목록을 적용하지 않는다.

최종 개발 freeze는 **16:08:34.550520 KST**, 평가 시작은 **16:08:50.222867 KST**다. `freeze.json`에 실제 소스·현재 규격·계수·허용치·컴파일러/옵션·EXE·참조 실행·개발 진단과 gate 해시를 묶었다. 평가 이후 이 조건들을 바꾸지 않았다.

평가 20개는 1,528,960 samples → 9,501프레임 → 123,513개 MFCC 값이다. 모든 클립의 전 단계가 수치·구조 기준을 통과했다.

| 평가 단계 | 최대 절대 오차 | 전체 원소 RMSE |
|---|---:|---:|
| pre-emphasis | 3.576279e-8 | 1.845109e-9 |
| windowed | 5.371367e-8 | 1.273698e-9 |
| complex FFT | 2.707476e-6 | 4.922638e-8 |
| power | 1.620362e-7 | 3.783082e-10 |
| Mel energy | 4.230325e-7 | 2.161474e-9 |
| log Mel | 1.211663e-4 | 1.444358e-6 |
| DCT / MFCC | **8.017005e-5** | **2.672533e-6** |

최대 MFCC 오차 클립은 `121-121726-0008`이다. 클립별 RMSE 중 최대는 `121-121726-0005`의 `5.913599e-6`이며 전체 원소 RMSE와 다르다. 모든 클립·단계의 최대 오차/RMSE/위반 수는 `stage_errors.csv`, 각 클립 `validation.json`에는 실패 위치와 계수별 지표도 있다.

## 7. 검증·재현·그림 근거

코어 검사 12개는 FFT의 n=1 임펄스(전체 bin 부호/순서/스케일), DC의 정확한512 peak, 양의 복소 tone의 자연순서 bin19, 잘못된 인자/비유한 입력 거부, literal 경계 길이, 전역 샘플 기준 pre-emphasis와 중첩, reset 재실행, 두 인스턴스 교차 실행, counter overflow 거부를 포함한다. 독립 FFT 예상값의 double trig는 시험 코드에만 있다.

호스트 시험에서는 개발 음성을 chunk=1과 4096으로 처리해 13개 단계 파일의 byte hash 동일을 확인했고, 홀수 바이트 PCM·잘못된 출력 경로 거부와 빈 입력의 0프레임을 확인했다. 코어/호스트 시험의 통과를 수치 목표 통과와 혼동하지 않는다.

| `D:\2610_MFCC\build\c_reference\` 아래 | 내용 |
|---|---|
| `dev_probe_01/`, `dev_probe_02/` | 초기 빌드 실패 및 최초 수치 실패 보존 |
| `dev_frozen_01/` | 개발 검사·진단 통합 실행; 이후 진단 증거 검사 보강 전 기록 |
| `dev_frozen_02/` | 최종 개발·합성·계약·정밀도 진단·freeze |
| `eval_frozen_01/` | 위 동결 실행파일로 평가20 수행 |
| `reproduce_01/` | 한 명령 재빌드·전체 재실행 결과, 다음 작업의 대표 기준 폴더 |
| `reproducibility_01.json`, `verify_reproduction.py` | 실제 파일 integrity, 재실행 수치·지표·실패 일치 검사 |
| `newprobe/arm_gcc_version.txt` | 설치된 ARM 컴파일러 버전 실행 기록 |

재현 검사에서 C 개발 412개·평가359개·전체 재실행732개 artifact hash가 모두 일치했다. 최초 분리 실행과 전체 재실행의 단계 배열 494개 및 계수/생성 C 파일11개, 합계 **505개**가 byte 동일했고 모든 사례의 수치 지표·구조 결과·실패도 같았다. 실행 시각/폴더/PE linker timestamp를 포함한 모든 파일의 byte 동일성을 주장하지 않는다. 현재 PC의 고정 환경 재현 결과이며 ARM과 PC의 bit 동일성은 아직 검증하지 않았다.

기존 Python 실행의 기록된 artifact **1,321개**도 실제 파일과 다시 대조해 변경 0개를 확인했다. 데이터 manifest의 과거 spec hash와 Python snapshot은 덮어쓰지 않았다. 이번 현재 규격 hash는 `6e5cf78bf202a923fa397b94b909711ab5d7a84808ec7ed3984942f2198b12a0`이며 수학 정의를 유지한 상태 갱신이다. Git HEAD와 미커밋 상태, C 소스 snapshot/hash, 입력별 PCM hash는 각 실행에 보존했다.

개발 음성의 아래 그림을 실제로 열어 동일 축/색 범위, 별도의 대칭 오차 색 범위, 잘림 없는 레이블을 확인했다. PNG와 PDF가 모두 있다.

- [Python / C / 차이 히트맵](D:/2610_MFCC/build/c_reference/dev_frozen_02/figures/8463-294828-0037_python_c_mfcc.png)
- [계수별 최대 절대 오차 / RMSE](D:/2610_MFCC/build/c_reference/dev_frozen_02/figures/8463-294828-0037_coefficient_errors.png)

## 8. 다음 ARM 이식 준비와 미검증 사항

이식할 파일은 `mfcc.c/.h`, `fft32.c/.h`와 고정 실행의 `coefficients/mfcc_tables.c/.h`다. `host_main.c`는 PC 어댑터이며 ARM에서는 저장 PCM의 주소/길이 공급과 결과 수거 어댑터를 작성한다. 입력 PCM은 `python_reference/reproduce_01/<role>/<id>/input_s16le.pcm`, C 기준 출력은 `c_reference/reproduce_01/<role>/<id>/`에 있다. 출력은 헤더 없는 LE `<f4`, FFT는 `<c8` real/imag 교대, frame ID/시작 위치는 `<u8`다. 정확한 shape와 순서는 `host_manifest.json`을 따른다.

설치된 도구 `C:\Xilinx\Vitis\2024.2\gnu\aarch32\nt\gcc-arm-none-eabi\bin\arm-none-eabi-gcc.exe --version`은 GCC **13.3.0**으로 확인됐다. 이것은 ARM build나 실제 보드 동작 증거가 아니다.

다음 실행 전에 다음 사항이 필요하다.

1. 실제 ZYBO Z7-20에 맞는 PS7 DDR/MIO/clock 설정, XSA, standalone BSP, startup/linker script와 JTAG 연결을 확보·확인한다. board preset이나 PS 초기화 성공을 현재 가정하지 않는다.
2. Cortex-A9용 타깃·VFP/float ABI와 BSP/libm을 맞추고, fast-math/FMA 정책·반올림/denormal 처리·GCC/libm 버전을 기록한다. ARM의 `logf`와 최적화가 PC CRT와 같다고 가정하지 말고 동일 합성/개발 벡터부터 다시 검사한다.
3. 저장 PCM·계수·작업 상태·출력 버퍼의 메모리 배치를 정한다. JTAG/SD 등으로 입력을 적재해 같은 PCM hash를 확인하고, frame ID/시작 위치/C0…C12를 회수한다. 마이크·I2S·코덱은 추가하지 않는다.
4. 타이머는 코어 밖의 ARM 어댑터에 둔다. 실제 PS clock과 timer 주파수, cache 상태, 준비/워밍업/반복 횟수를 기록한다. PCM 적재·출력/UART·stage dump와 계산 시간을 분리하고 계산 및 전체 경로 통계를 따로 측정한다. 이번 PC 실행은 `timing.measured=false`이며 ARM 성능으로 환산할 값이 없다.
5. 두 합성 사례의 미달을 후속 보고에서도 유지한다. 모든 입력에서 초기 목표를 요구한다면 별도 정밀도 설계·보상 방식의 타당성을 개발 입력으로 검토해야 한다. 현재 데이터를 보고 입력·공통 수식·하한·Python 정답이나 평가 허용치를 바꾸는 방식으로 해결하지 않는다.

`MFCC_SPEC.md`와 `DEVELOPMENT_HANDOFF.md`의 Python 미구현 상태를 실제 완료 결과로 갱신했다. 이 C 작업에서는 Claude 검토 문서·검증 폴더와 FFT/RTL/하드웨어 원본을 수정하지 않았다. 마지막 확인에서는 검토 문서의 병행 갱신을 관측했다(수정 시각 16:10:54 KST, 당시 SHA-256 `d62a5260579b5934dd0dc28505d59431f6a0298fdb3eb0f2b4bfe5639c239e3a`). 이를 되돌리거나 C 검증 의존성으로 편입하지 않았다. 미확정 하드웨어 사항은 그대로 남겼다. Git commit/push 및 논문·발표·음성·PDF의 Git 추가는 하지 않았다.
