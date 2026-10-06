# Python MFCC 기준 모델 구현·검증 결과

작성 기준: 2026-10-04 KST. `NEXT_TASK_PYTHON.md`의 범위에서 Python 기준 경로를 구현하고 실제 실행했다. 이 문서는 개발 결과 기록이며 논문 완성 원고가 아니다.

## 1. 완료 상태와 구현

**완료:** 개발 음성 1개와 합성 입력을 검사한 후 코드·프로파일·허용치를 고정했고, 평가용 20개를 같은 절차로 검증했다. 전체 경로를 한 명령으로 재실행하여 수치 산출물의 SHA-256 일치를 확인했다. 대표 그림 2개를 실제로 열어 검토했다.

| 구분 | 이번에 직접 확인한 결과 |
|---|---|
| 본 비교용 `comparison_raw13` | 합성 17개, 개발 1개, 평가 20개 통과 |
| 설정 확인용 `github_static13` | 비어 있지 않은 합성 16개, 개발 1개, 평가 20개 및 native MFCC 대조 통과 |
| GitHub 빈 입력 | 수치 비교 미지원(`passed=null`); 별도 입력 계약 검사에서 의도한 `ValueError` 확인 |
| 개발 출력 | 본 비교 534×13, GitHub 535×13 |
| 평가 출력 합계 | 본 비교 9,501×13, GitHub 9,521×13 |
| 재현성 | 1,178개 수치/프로파일 파일의 SHA-256 일치, 불일치 0개 |
| 후속 검증 | C/ARM, RTL/FPGA, float32·고정소수점, 속도·전력·인식률은 이번에 수행하지 않음 |

실제 구현은 `software/python/mfcc_reference.py`, 프로파일은 `software/python/profiles/`, 검증은 `verification/python/checks.py`, 실행 진입점은 `scripts/run_python_reference.py`다. 본체는 NumPy/SciPy를 이용해 PCM 변환 → 연속 pre-emphasis → framing → Hamming → RFFT → power → Mel → ln → DCT의 중간값을 노출한다. `python_speech_features.mfcc()`는 검증에서만 사용한다. 외부 MFCC 소스를 복사·수정한 구현이 아니며, 규격의 기존 MFCC 수식을 구현한 것이다.

## 2. 재실행 명령

설치 스크립트는 Python 3.11을 확인하고 저장소 밖에 venv를 만든다. 기본 Python 경로는 이 PC에 맞춰져 있으며 `-Python` 인수로 바꿀 수 있다. 검증한 정확한 Python 버전은 3.11.9이다.

```powershell
& 'D:\2610_MFCC\project\scripts\setup_python_reference.ps1'
```

환경 설치 후 전체 절차는 다음 한 명령이다. 실행 시각 이름의 새 결과 폴더가 만들어진다.

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B 'D:\2610_MFCC\project\scripts\run_python_reference.py' --phase all
```

이번에 실제 실행한 명령은 다음과 같다. 아래 이름은 이미 존재하므로 다시 실행할 때 다른 `--run-id`를 쓰거나 생략한다.

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B 'D:\2610_MFCC\project\scripts\run_python_reference.py' --phase develop --run-id dev_frozen_01
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B 'D:\2610_MFCC\project\scripts\run_python_reference.py' --phase evaluate --run-id eval_frozen_01 --development-run 'D:\2610_MFCC\build\python_reference\dev_frozen_01'
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B 'D:\2610_MFCC\project\scripts\run_python_reference.py' --phase all --run-id reproduce_01
```

세 실행은 모두 종료 코드 0, manifest `status=passed`다. `develop`가 실패하면 freeze를 만들지 않고 평가를 열지 않는다. 분리된 `evaluate`는 통과한 개발 실행과 동일한 소스·설정·검증 코드·환경·계수·입력 manifest를 요구한다. 매 입력에서 WAV/PCM/FLAC 해시를 다시 검사하고, 변경이나 수치 실패 시 성공으로 기록하지 않는다. 사용법과 바이너리 해석은 [Python README](../software/python/README.md)에 정리했다.

## 3. 환경·입력·출처

실행 환경은 Windows 10 build 19045, Python 3.11.9 64bit다. OpenBLAS/OMP/MKL thread 수는 1로 지정했다. NumPy 2.2.6, SciPy 1.15.3, python_speech_features 0.6, Matplotlib 3.10.3을 사용했다. 간접 의존성까지 `software/python/requirements.txt`에 고정했으며, 실행 중 버전 불일치는 거부한다. 데이터 준비용 Python 환경과 별도다.

| 출처 | 용도 / 기록된 라이선스 근거 |
|---|---|
| [python_speech_features](https://github.com/jameslyons/python_speech_features), 0.6 | 단계/API 대조 및 native `mfcc`; 설치 metadata의 MIT |
| [NumPy](https://github.com/numpy/numpy), 2.2.6 | 배열·수치 계산, 패키지 측 FFT; 설치 metadata의 BSD 및 번들 고지 |
| [SciPy](https://github.com/scipy/scipy), 1.15.3 | 기준 RFFT·대조 DCT; 설치 metadata의 BSD 및 번들 고지 |
| [Matplotlib](https://github.com/matplotlib/matplotlib), 3.10.3 | 이번 입력에서 새로 생성한 그림; 설치 metadata의 Matplotlib license agreement 및 고지 |
| [LibriSpeech / OpenSLR 12](https://www.openslr.org/12/) | 준비된 test-clean PCM; 데이터 manifest에 기록된 CC BY 4.0 |
| 로컬 GitHub notebook snapshot | `reference_code/github_mfcc/Python source/DNN modeling.ipynb`, commit `27aa09974049d11f49383c8e18f3ca9e34d08ed9`, 0기준 셀 3·10·13 |

실행별 `provenance/packages.json`과 `*_METADATA.txt`가 설치본 출처·라이선스 고지 및 설치된 notice 파일 해시를 보존한다. 설치 다운로드/배포 파일 해시는 `D:\2610_MFCC\build\python_reference\_environment\install_report.json`에 있다. notebook의 해당 셀과 파일 해시는 `provenance/github_notebook_settings.json`에 보존했다. 원래 notebook은 패키지와 resampy 버전을 고정하지 않았으므로 이번 0.6 대조는 과거 환경 전체 재현이 아니다.

입력은 기존 `D:\2610_MFCC\data\librispeech\DATASET_MANIFEST.json`의 개발 1개와 `role=evaluation` 20개다. 개발 화자와 평가 화자 집합이 겹치지 않는지 검사했다. 준비된 음성을 다시 자르거나 리샘플링·peak 정규화하지 않았다. `/32768`은 본 비교 규격의 PCM 수치 단위 변환이다. WAV decode 값과 raw PCM byte 일치, 채널·샘플링률·비트수·길이·영역·FLAC/WAV/PCM 해시를 매번 검사했다.

| 기록 항목 | SHA-256 / 값 |
|---|---|
| 개발 음성 | `8463-294828-0037`, 85,920 samples, 16 kHz mono PCM16, 5.37초 |
| 개발 PCM | `026cae2934935a27911f88d7d6e0ddcc678ad433fb50fe764233a57e4bbcde32` |
| 개발 WAV | `cb179478556602a7d0cd7ef28af4be87c4040a705b7d2d9f1a32583cf35e3f91` |
| 입력 manifest | `79181da24148a61645c149567bba90a6c7e8448d4c0777b08ded1881a0dc77bc` |
| 이번 실행의 MFCC_SPEC | `33a331032b3ffedd9213c4d589d0d5234c0a0643b55c3918df5a13894627ce08` |
| 데이터 manifest의 과거 spec 해시 | `19bab8658afb5f8865a861e01d2dd3baca44f21b63d414101a5c5eb6958fd412` — 보존, 변경하지 않음 |
| Git HEAD | `e679534de5b2f9f2222dc06d3f9ac0a4a7fe8db9` + 미커밋 상태 |

Git HEAD만으로 이번 구현을 재현할 수는 없다. 작업 시작부터 문서 변경이 있었고 이번 코드도 커밋하지 않았다. `run_manifest.json`에 당시 `git status`, 실행 명령·시각·환경·설정·소스 해시를 기록하고 `provenance/source_snapshot/`에 실행 소스를 복사했다. 입력별 해시는 `dataset_manifest_snapshot.json`과 각 `validation.json`에 있다.

## 4. 고정한 두 프로파일

공통: fs=16,000 Hz, L=512(32 ms), H=160(10 ms), pre-emphasis=0.95, 대칭 Hamming(`L-1` 분모), 512점 비정규화 순방향 FFT(음의 지수), bin 0…256의 power `(re²+im²)/512`, 편측 두 배 보정 없음. HTK Mel 0…8,000 Hz, 26개 삼각 필터, bin=`floor(513*f/16000)`, 최고 높이 1·면적 정규화 없음. 자연로그, orthonormal DCT-II, 13개 출력. 클립 첫 이전 입력은 0이고 pre-emphasis 상태는 프레임 경계에서 초기화하지 않는다.

| 항목 | 본 비교 `comparison_raw13` | 설정 확인 `github_static13` |
|---|---|---|
| 근거 | MFCC_SPEC의 작업 기준을 구현한 설계 선택 | notebook의 호출 인수/입력 단위 + 설치한 0.6 소스의 동작 |
| PCM 단위 | int16 / 32768 → float64 | int16 수치 크기 그대로 → float64 |
| 프레임 | 완전 프레임만, 꼬리 제외 | pre-emphasis 후 꼬리를 0으로 채움 |
| 0/작은 Mel energy | `max(E,1e-12)` | 정확히 0일 때만 `2^-52`; 양수는 그대로 |
| lifter | 0(없음) | 22 |
| C0 에너지 교체 | 없음 | lifter 후 `ln(sum(power))`로 교체, 0은 epsilon |
| 출력 순서 | C0…C12 | log frame energy, lifter C1…C12 |
| delta, delta-delta, CMVN | 모두 없음 | 모두 없음; 원본 39차원 후처리는 이번 범위 밖 |
| 빈 입력 | 0×13 | 명시적 거부 |
| 개발 프레임 수 | 534 | 535 |

`github_static13`의 8 kHz highfreq는 notebook `highfreq=None`을 패키지가 Nyquist로 해석하는 동작을 명시한 것이다. GitHub 입력은 notebook의 PCM 크기 단위를 확인한 것이며 원래 학습 음성/리샘플링을 재현한 것은 아니다. 두 설정을 혼합해 공정한 네 비교군 결과라고 해석하면 안 된다. 이후 C/하드웨어 본 비교 기준은 `comparison_raw13`다.

## 5. 개발 검증과 평가 전 고정

검사식은 원소마다 `abs(actual-reference) <= atol + rtol*abs(reference)`다. shape·dtype·유한값과 프레임 인덱스도 검사하며 프레임 인덱스는 정확히 같아야 한다.

| 프로파일 | atol | rtol | 범위 |
|---|---:|---:|---|
| comparison_raw13 | 1e-10 | 1e-10 | Python float64 단계/수식 대조 |
| github_static13 | 1e-7 | 1e-10 | PCM count의 큰 중간값 및 로그/lifter 민감도를 포함한 float64 대조 |

FFT backend, `abs(z)^2`와 `re²+im²`, DCT 합산 순서에 따른 반올림 차이를 허용한다. 큰 PCM count power에는 상대 오차 항이 적용된다. 따라서 모든 최대 절대 오차가 `atol`보다 작다는 뜻은 아니다. 이 기준을 float32 C/FPGA나 고정소수점에 그대로 적용하지 않는다.

초기 `dev_probe_01`은 양쪽 `atol=rtol=1e-10`에서 GitHub의 작은 진폭 교대 입력과 full-scale 교대 입력이 실패했다. 주 원인은 거의 상쇄된 FFT 값의 로그 민감도이며 lifter 후 합성 MFCC 최대 절대 차이는 약 `1.93e-8`이었다. 해당 실행은 `status=failed`로 보존했고 평가를 실행하거나 freeze를 만들지 않았다. 개발 중 GitHub 절대 허용치만 `1e-7`로 조정한 이유를 `tolerances.json`에 기록했다. 본 비교 허용치는 유지했다. **평가 20개 결과를 보고 조건을 바꾸지 않았다.**

최종 개발 실행은 15:29:46 KST 시작, 15:29:50.766905에 freeze 생성, 15:29:50.789906에 통과로 종료했다. 평가 수치 처리는 15:31:28.548 이후 시작했다. 로그의 시각은 절차 증거이며 성능 측정값이 아니다.

| 합성/입력 검사 | 실제 결과 |
|---|---|
| 길이 0/511/512/671/672/832 | 본 비교 프레임 0/0/1/1/2/3, 통과 |
| 832-sample 무음 | 모든 단계 유한; C0 예상 `sqrt(26)*ln(1e-12)=-140.89111585061394`; C0…C12 예상값 대비 최대 차이 `8.523238e-14`, 통과 |
| 임펄스 | n=0 및 n=511, 진폭 16,384, 통과 |
| DC | ±8,192, 통과 |
| 정현파 | 1,000 Hz(bin32), 1,037 Hz, 진폭 12,000, 통과 |
| 복합파 | 500 Hz×7,000 + 2,237 Hz×5,000, 통과 |
| 작은/큰 진폭 | ±1 교대, 32,767/−32,768 교대; 전처리 headroom·부호·유한값, 통과 |
| 난수 | 명시적 PCG64 seed=20261004, PCM16 전 범위 832 samples, 통과 |
| 잘못된 API 입력 | float, stereo, int32, Python list, 지원하지 않는 프로파일 변경 거부; 양쪽 합계 11개 계약 검사 통과 |

단계별 패키지 대조 외에 `math.fsum/sin/cos`의 직접 DFT(최대 3프레임×8 bin), 직접 DCT(같은 선택 프레임의 C0…C12), Parseval, 무음 예상값, Hamming 수식, 고정 Mel edge, 전처리 scalar sample을 검사했다. 직접 DFT/DCT에는 실제 windowed/log 배열을 입력하므로 변환 자체의 검증을 보완하며, 전체 파이프라인에 대한 완전히 독립된 두 번째 정답은 아니다. 합성+개발에는 단계 검사 420개·분석 검사 950개, 평가에는 단계 검사 480개·분석 검사 1,040개가 모두 통과했다.

## 6. 실제 오차와 패키지 정책 차이

아래는 기준 구현 대 명시적 정책 어댑터를 거친 패키지의 MFCC 오차다. global RMSE는 모든 평가 원소를 합친 값이고, 최악 클립 RMSE는 클립별 RMSE 중 최대다.

| 데이터 / 프로파일 | 프레임 | 최대 절대 오차 | global RMSE 또는 단일 클립 RMSE | 최악 클립 RMSE |
|---|---:|---:|---:|---:|
| 개발 / main | 534 | 7.227552e-14 | 1.265838e-14 | 동일 |
| 개발 / GitHub | 535 | 1.147527e-12 | 1.711711e-13 | 동일 |
| 평가 20 / main | 9,501 | 2.469136e-13 | 1.438940e-14 | 2.540015e-14 |
| 평가 20 / GitHub | 9,521 | 2.864819e-12 | 1.540125e-13 | 2.990427e-13 |

main 최대 오차 클립은 `121-121726-0008`, 최대 RMSE 클립은 `121-121726-0005`다. 두 통계는 같은 클립을 의미하지 않는다. 집계 원자료는 `stage_errors.csv`, 클립별 `validation.json`, `reproducibility_01.json`이다.

| main 평가 단계 | 20개 중 최악 최대 절대 오차 | 최악 클립 RMSE |
|---|---:|---:|
| 입력 / pre-emphasis / frames / 시작 인덱스 | 0 | 0 |
| windowed | 1.665335e-16 | 4.535607e-18 |
| FFT(복소 오차의 크기) | 3.972055e-15 | 1.799053e-16 |
| power | 2.220446e-16 | 2.415919e-18 |
| Mel energy | 3.330669e-16 | 8.173334e-18 |
| ln Mel | 5.364598e-13 | 9.561072e-15 |
| DCT / MFCC | 2.469136e-13 | 2.540015e-14 |

**수정하지 않은 패키지의 native 출력과 main은 그대로 같지 않다.** `native_mfcc.npy`와 `adapted_mfcc.npy`를 별도로 저장했다. main 어댑터는 연속 pre-emphasis를 보존한 채 완전 프레임만 선택하고, 원시 Mel energy에 `max(E,1e-12)`를 적용한다. 패키지 소스는 수정하지 않았다.

개발 음성에서는 로그 하한 적용 값이 0개이고 native가 패딩 프레임 1개를 추가했다. 평가에서는 20개 모두 native가 프레임 1개를 추가했으며, main 로그 하한에 걸린 Mel 값은 총 6,937개였다. 같은 프레임만 비교해도 native와 main의 MFCC 최대 절대 차이는 `42.89617612166913`이다. 이는 0만 epsilon으로 교체하는 패키지와 모든 작은 값을 하한으로 올리는 본 규격의 **예상 정책 차이**로 `native_package_policy`에 기록했다. 이 수치를 float64 반올림 오차나 구현 일치 결과로 표현하지 않는다. GitHub 프로파일은 패키지 원래 정책을 유지해 native 출력도 검사했다.

평가 클립별 본 비교 출력은 다음과 같다. 모든 행에서 입력 해시·유한값·프레임 순서·단계 검사 통과를 확인했다. GitHub 프레임 수는 각 행에 1을 더한 값이다.

| ID | main 프레임 | MFCC 최대 절대 오차 | MFCC RMSE |
|---|---:|---:|---:|
| 121-121726-0005 | 307 | 2.238210e-13 | 2.540015e-14 |
| 121-121726-0008 | 496 | 2.469136e-13 | 2.268084e-14 |
| 1221-135766-0013 | 362 | 4.263256e-14 | 1.149649e-14 |
| 1221-135767-0024 | 582 | 4.574119e-14 | 1.184146e-14 |
| 1320-122612-0005 | 589 | 6.042389e-14 | 1.246713e-14 |
| 1320-122617-0021 | 531 | 5.284662e-14 | 1.356384e-14 |
| 2300-131720-0006 | 409 | 4.951595e-14 | 1.368639e-14 |
| 2300-131720-0014 | 372 | 8.338330e-14 | 1.463914e-14 |
| 237-134493-0014 | 367 | 4.118927e-14 | 1.126360e-14 |
| 237-134500-0003 | 314 | 4.529710e-14 | 1.272774e-14 |
| 3575-170457-0032 | 300 | 4.496403e-14 | 1.142555e-14 |
| 3575-170457-0051 | 489 | 5.784262e-14 | 1.224566e-14 |
| 4992-23283-0000 | 662 | 5.551115e-14 | 1.365943e-14 |
| 4992-41797-0002 | 560 | 5.750955e-14 | 1.513535e-14 |
| 5105-28240-0005 | 737 | 4.362483e-14 | 1.222393e-14 |
| 5105-28241-0016 | 626 | 6.039613e-14 | 1.337603e-14 |
| 6930-75918-0000 | 348 | 1.207923e-13 | 1.452301e-14 |
| 6930-75918-0007 | 329 | 5.362377e-14 | 1.422287e-14 |
| 7127-75947-0003 | 595 | 5.517808e-14 | 1.378747e-14 |
| 7127-75947-0029 | 526 | 6.750156e-14 | 1.393041e-14 |

## 7. 재현성·그림·증거 위치

전체 실행 기준 폴더는 `D:\2610_MFCC\build\python_reference\reproduce_01`이다. 원래 분리 실행도 그대로 보존했다.

| 위치 (`D:\2610_MFCC\build\python_reference\` 기준) | 내용 |
|---|---|
| `dev_probe_01/` | 평가 전 최초 허용치에서 발생한 합성 실패 보존 |
| `dev_frozen_01/` | 최종 개발·합성 검증, freeze, 대표 그림 |
| `eval_frozen_01/` | 위 freeze를 검증하고 실행한 평가 20개 |
| `reproduce_01/` | `--phase all`로 개발·합성·평가 모두 재생성한 통과 실행 |
| `reproducibility_01.json` | 실제 파일 해시 재검사, 재실행 일치 및 global RMSE 증거 |
| `_environment/verify_reproduction.py` | 위 재현성 결과를 생성한 검사 코드; SHA-256과 실행 명령은 JSON에 기록 |

재현성 검사에서 개발 641개, 평가 720개, 전체 재실행 1,321개의 artifact manifest 기록을 실제 파일과 대조해 불일치 0개를 확인했다. 최초 결과와 전체 재실행 사이의 `.bin`, 입력 `.pcm`, 패키지 `.npy`, `mfcc.csv`, 프로파일 총 1,178개는 byte hash가 모두 일치했다. 실행 시각·경로가 다른 manifest나 PDF metadata까지 byte 동일하다고 주장하지 않는다. 이 확인은 현재 PC/고정 환경에서의 재실행 결과이며 다른 BLAS·OS의 bit 동일성을 보장하지 않는다.

개발용 그림은 다음 경로에 PNG(300 dpi)와 PDF로 저장했다.

- [기준·대조·차이 히트맵](D:/2610_MFCC/build/python_reference/dev_frozen_01/figures/8463-294828-0037_mfcc_comparison.png)
- [계수별 최대 절대 오차·RMSE](D:/2610_MFCC/build/python_reference/dev_frozen_01/figures/8463-294828-0037_mfcc_errors.png)

PNG 두 개를 실제 열어 프레임/초·계수 축, C0…C12 순서, 잘림 없는 레이블, 기준/대조의 동일 색 범위, 차이 그림의 별도 대칭 색 범위(`×10^-14`)를 확인했다. 기준과 대조의 C0 크기 때문에 다른 계수의 대비가 작아질 수 있으나 색 범위를 임의로 달리하지 않았다. 그림은 이번 입력/결과로 새로 만든 것이며 외부 논문의 그림을 복제하지 않았다.

## 8. C 구현으로 넘길 기준값과 남은 작업

C 작업에서는 먼저 `comparison_raw13`만 구현하고 아래 기준을 사용한다.

- 설정: `D:\2610_MFCC\build\python_reference\reproduce_01\profiles\comparison_raw13.json`
- 계수: `D:\2610_MFCC\build\python_reference\reproduce_01\coefficients\comparison_raw13\arrays.json` 및 같은 폴더 `.bin`
- 개발 음성: `D:\2610_MFCC\build\python_reference\reproduce_01\development\8463-294828-0037\input_s16le.pcm`
- 개발 단계/정답: `D:\2610_MFCC\build\python_reference\reproduce_01\development\8463-294828-0037\comparison_raw13\arrays.json`, 각 단계 `.bin`, `mfcc.csv`
- 합성/평가: `reproduce_01\synthetic\<id>\comparison_raw13\`와 `reproduce_01\evaluation\<id>\comparison_raw13\`의 같은 형식

바이너리는 헤더 없는 little-endian C row-major다. 실수는 float64, FFT는 real/imag float64 교대, frame index/Mel edge는 int64다. 각 `arrays.json`에 shape·축·단위·해시·바이트 수가 있다. 입력부터 최종값까지 저장돼 있으므로 C에서 처음 어긋나는 단계를 찾을 수 있다. `dct_matrix`에는 정규화 계수가 포함돼 있으며 `dct_scale`을 중복 적용하면 안 된다. 자세한 형식과 로드 예시는 Python README에 있다.

남은 작업은 PC C로 같은 PCM→C0…C12 경로를 구현하고 단계별 대조한 뒤 ARM으로 옮기는 것이다. C float32 변환·합산 순서 및 향후 고정소수점 오차 예산은 별도 개발 입력으로 정해야 한다. 현재 기준값은 float64이고, Python끼리의 작은 오차를 C/ARM/FPGA 통과나 속도·전력·인식 정확도 개선으로 확대하지 않는다. GitHub RTL의 실제 계수·스트림 의미 및 기존 FFT 재사용 판단도 이 Python 검증으로 해결된 것은 아니다.

이번 작업은 기존 `MFCC_SPEC.md`, 데이터 manifest, GitHub/FFT/하드웨어 원본, Claude 검토 파일을 수정하지 않았다. 시작 전에 있던 문서 변경은 유지했다. 논문·발표·학교 자료를 저장소에 추가하지 않았으며 Git 커밋·푸시도 하지 않았다.
