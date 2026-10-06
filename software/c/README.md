# C MFCC 기준 구현

2026-10-04 기준. `comparison_raw13`의 C11/binary32 구현과 PC 파일 입출력 검증 도구다. 실제 ARM 실행은 아직 검증하지 않았다. 상세 결과와 한계는 [C_REFERENCE_RESULTS.md](../../docs/C_REFERENCE_RESULTS.md), 수학 정의는 [MFCC_SPEC.md](../../docs/MFCC_SPEC.md)를 따른다.

## 현재 결과

| 검사 | 실제 결과 |
|---|---|
| 합성 입력 | 17개 중 15개 수치 통과; 구조 검사는 17개 모두 통과 |
| 개발 음성 | 534프레임 통과; MFCC 최대 절대 오차 `1.9288501e-5` |
| 평가 음성 | 20개, 9,501프레임 통과; MFCC 최대 절대 오차 `8.0170052e-5` |
| 알려진 수치 실패 | `fullscale_alternating`: MFCC 최대 절대 오차 `0.10488222`; `tone_bin32_1000hz`: `0.00239532` |
| 전체 재실행 | `completed_with_numerical_failures`, 종료 코드 **2** |

오차는 고정된 Python float64 기준과의 차이다. MFCC 허용치는 원소마다 `abs(C-Python) <= 1e-3 + 1e-5*abs(Python)`이며 다른 단계의 기준은 `verification/c/tolerances.json`에 있다. 두 합성 실패를 통과로 바꾸거나 허용치를 완화하지 않았다.

평가는 개발 음성·구조·core·host 검사가 통과하고, 알려진 두 실패의 정밀도 진단을 재현한 뒤 `known_development_limits.json`의 조건으로 수행했다. `development_gate.json`의 `passed=false`와 `evaluation_eligible=true`는 다른 의미다. 평가 전에 소스·계수·컴파일러·허용치·기준 자료를 고정했으며, 알려지지 않은 실패는 평가를 중단한다. 음성 20개 통과는 모든 입력에서의 수치 통과나 인식 정확도를 뜻하지 않는다.

## 재실행

Python 기준 실행 `D:/2610_MFCC/build/python_reference/reproduce_01`과 기존 Python venv, 아래 MSVC 설치가 필요하다. 입력·기준 자료를 재작성하지 않고 해시를 확인해 사용한다.

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B 'D:\2610_MFCC\project\scripts\run_c_reference.py' --phase all
```

새 실행 폴더가 `D:/2610_MFCC/build/c_reference/` 아래 만들어진다. 현재 구현의 알려진 실패까지 재현하면 종료 코드는 **2**다. 0은 전체 수치 통과, 1은 실행·계약·예상하지 못한 검증 실패를 나타낸다. `--run-id`로 새 이름, `--reference-root`로 고정된 Python 실행 경로를 지정할 수 있다.

실제 증거 폴더는 `dev_frozen_02`, `eval_frozen_01`, `reproduce_01`이다. 실행별 `run_manifest.json`, `development_gate.json`, `freeze.json`, `stage_errors.csv`, `diagnostics/diagnosis.json`, 입력별 `validation.json`을 함께 확인한다. 실행 시각·전체 경로가 들어간 로그와 manifest는 수치 바이너리의 재현성 판단과 구분한다.

## Core API와 처리

```c
#include "mfcc.h"
mfcc_state state;
mfcc_frame frame;
float filtered_sample;
int result = mfcc_init(&state); /* 0: 초기화, -1: 오류 */
/* 각 입력 샘플에 정확히 한 번 호출한다. */
result = mfcc_push(&state, sample, &frame, &filtered_sample);
/* 1: 완성 프레임, 0: 다음 샘플 대기, -1: 오류 */
```

`sample`은 `int16_t`다. `out`은 매 호출 필수이며 `preemphasis_out`은 NULL일 수 있다. 인수 메모리는 겹치면 안 된다. 상태 하나를 여러 실행 흐름에서 동시에 사용하지 않는다. 독립 상태는 분리해 사용할 수 있다. 새 클립은 반드시 `mfcc_init()`으로 재설정한다. 오류 후 상태 재사용에는 재초기화가 필요하다.

Core에는 파일 I/O·타이머·동적 할당·변경 가능한 전역 상태가 없다. `mfcc_state`의 ring buffer와 FFT 작업 공간을 호출자가 제공한다. PC MSVC x64의 실제 `sizeof`는 state **6,184바이트**, frame **7,512바이트**다. 계수 상수·호출 스택·호스트 입력 버퍼를 포함한 총 메모리 값이 아니며 ARM ABI의 크기로 간주하지 않는다.

처리 순서는 다음과 같다.

1. PCM16을 `32768.0f`로 나누고, 클립 전체에서 연속적으로 `x[n] - alpha*x[n-1]`을 계산한다. 첫 이전 입력은 0, alpha는 `.95`의 binary32 값이다.
2. ring buffer에 필터링한 샘플을 한 번씩 넣는다. 512샘플 뒤 첫 프레임, 이후 160샘플마다 프레임을 출력한다. 겹침 구간을 다시 pre-emphasis하지 않으며 꼬리 패딩은 없다.
3. 오래된 샘플부터 Hamming 계수를 곱하고 512점 complex FFT를 실행한다. 실수 입력의 허수부는 0이다.
4. `fft32.c`는 입력 bit reversal과 9단계 radix-2 DIT butterfly를 사용한다. 순방향 음의 지수, 무정규화, 자연 순서 출력이다. 외부 FFT/MFCC 라이브러리를 호출하지 않는다.
5. bin 0…256의 `(re*re + im*im)/512`를 계산한다. 편측 bin의 2배 보정은 없다.
6. 26개 Mel 필터를 bin 순서로 누적하고 `logf(max(energy, float32(1e-12)))`를 계산한다.
7. 필터 순서의 float32 누산과 별도 DCT 정규화 곱으로 C0…C12를 출력한다. lifter·에너지 대체·delta·CMVN은 없다. 진단용 `frame_energy`는 257개 power의 합이며 C0를 바꾸지 않는다.

## 계수와 빌드

`verification/c/generate_coefficients.py`가 고정된 Python 기준 계수에서 binary32 상수와 manifest를 생성한다. 현재 결과는 `D:/2610_MFCC/build/c_reference/reproduce_01/coefficients/`의 `mfcc_tables.h`, `mfcc_tables.c`, 개별 `.bin`, `arrays.json`, `coefficient_manifest.json`이다. 생성물은 저장소 밖에 두며 빌드 시 해당 디렉터리를 include path에 추가한다.

확인한 컴파일러는 MSVC **19.42.34435 x64**, 도구 디렉터리는 `14.42.34433`이다. 환경은 `C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Auxiliary/Build/vcvars64.bat`로 초기화한다. 실제 고정 플래그:

```text
/nologo /TC /std:c11 /O2 /fp:strict /W4 /WX /MD
```

빌드 대상은 `fft32.c`, `mfcc.c`, 생성한 `mfcc_tables.c`와 `host_main.c`다. 별도 core 검사 실행 파일은 `host_main.c` 대신 `verification/c/test_core.c`를 링크한다. 표준 C runtime과 `logf`를 사용하며 외부 C 수치 패키지는 없다. `/MD` runtime의 버전·해시는 실행별 `build/compiler.json`, 실제 명령은 `build/binaries.json`에 남긴다.

fast-math를 사용하지 않으며 `/fp:strict`로 FMA contraction을 금지한다. [Microsoft의 floating-point 옵션 설명](https://learn.microsoft.com/en-us/cpp/build/reference/fp-specify-floating-point-behavior?view=msvc-170)을 따른다. binary32 저장·연산 특성과 `FLT_EVAL_METHOD=0`을 검사한다. Host는 입력 전 `FE_TONEAREST`를 요구하고 manifest에 기록한다. ARM 이식 시에도 같은 연산 정책과 libm/ABI를 따로 검증해야 한다.

## Host 입력·출력

```text
mfcc_host.exe --input PCM_S16LE --output EXISTING_DIRECTORY [--chunk-size N]
```

입력은 헤더 없는 mono/16 kHz PCM16 little-endian이며 WAV 헤더를 받지 않는다. 디렉터리는 호출자가 먼저 만든다. 기본 청크는 4,096샘플이며 각 샘플을 한 번씩 core로 전달한다. 출력 파일을 여는 모드는 새 내용으로 기록하므로 실행마다 별도 폴더를 사용한다.

`host_manifest.json`의 `arrays`가 각 파일의 dtype·shape·bytes를 정의한다. 파일은 헤더 없는 C row-major 배열이고 byte order는 명시적으로 little-endian이다. F는 프레임 수, T는 입력 샘플 수다.

| 파일 stem | dtype / shape |
|---|---|
| `input_float`, `preemphasis` | `<f4`, `[T]` |
| `frame_starts`, `frame_ids` | `<u8`, `[F]` |
| `frames`, `windowed` | `<f4`, `[F,512]` |
| `fft` | `<c8`, `[F,257]`; float32 real/imag 교대 |
| `power` | `<f4`, `[F,257]` |
| `mel_energies`, `log_mel` | `<f4`, `[F,26]` |
| `dct`, `mfcc` | `<f4`, `[F,13]` |
| `frame_energy` | `<f4`, `[F]` |

Host의 `status=passed`는 파일 처리·유한값·개수·순서 검사 성공이다. Python 기준과의 수치 허용치 통과는 별도 `validation.json`으로 판단한다. 홀수 바이트·읽기/쓰기/flush 실패·비유한값은 비정상 종료한다. 출력 경로 자체가 잘못돼 manifest를 쓸 수 없으면 stderr와 비정상 종료 코드를 남긴다. 빈 입력은 0프레임과 빈 파일들로 정상 처리한다.

`verification/c/host_checks.py`의 실제 검사에서 개발 입력의 청크 1/4096 결과 13개 바이너리가 모두 동일했고, 홀수 바이트 거부·잘못된 출력 경로 거부·빈 입력 처리가 통과했다. Core 검사는 FFT impulse/DC/tone과 framing·reset·독립 상태를 확인한다. 증거는 실행별 `verification/host_checks/results.json`과 `core_contract_tests.log`에 있다.

## ARM과 측정 범위

Vitis 2024.2의 `C:/Xilinx/Vitis/2024.2/gnu/aarch32/nt/gcc-arm-none-eabi/bin/arm-none-eabi-gcc.exe --version`에서 **GCC 13.3.0**을 확인했다. 로그는 `D:/2610_MFCC/build/c_reference/newprobe/arm_gcc_version.txt`다. ARM용 컴파일·BSP·링크·실제 보드 실행은 하지 않았다. C11 core의 이식 가능한 구조와 실제 ARM 검증 완료를 구분한다.

현재 타이머 측정은 없으며 host manifest에 `timing.measured=false`를 기록한다. I/O와 모든 단계 dump를 수행하는 검증 실행을 성능 benchmark로 사용하지 않는다. ARM 대비 가속·전력·인식률 또는 FPGA 성공 결과는 이 코드와 PC 검사로 주장하지 않는다.
