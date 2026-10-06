# Python MFCC 기준 모델

준비된 mono 16 kHz PCM16에서 단계별 float64 기준값을 계산한다. `mfcc_reference.py`는 `python_speech_features.mfcc()`를 호출하지 않으며, 검증 도구에서만 수정하지 않은 패키지를 대조한다. 실측 결과는 [PYTHON_REFERENCE_RESULTS.md](../../docs/PYTHON_REFERENCE_RESULTS.md)에 있다.

## 설치와 실행

PowerShell에서 환경 설치를 한 번 수행한다. 기본 실행기는 이 PC의 Python 3.11이며, 다른 설치 위치는 `-Python`으로 지정한다. 실제 검증 버전은 3.11.9이다.

```powershell
& 'D:\2610_MFCC\project\scripts\setup_python_reference.ps1'
```

다음 한 명령은 합성 입력 → 개발 음성 → 조건 고정 → 평가 20개 → 결과 기록을 수행한다. `--run-id` 생략 시 실행 시각으로 새 폴더를 만들며 기존 결과를 덮어쓰지 않는다.

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B 'D:\2610_MFCC\project\scripts\run_python_reference.py' --phase all
```

개발과 평가를 나누려면 다음과 같이 실행한다. 이미 존재하는 이름은 새 이름으로 바꾼다. 평가 전에 코드·규격·설정·계수·검증 코드·패키지 환경·데이터 manifest가 달라지면 중단하며 개발 검증부터 다시 해야 한다.

```powershell
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B 'D:\2610_MFCC\project\scripts\run_python_reference.py' --phase develop --run-id my_development
& 'D:\2610_MFCC\build\python_reference\venv\Scripts\python.exe' -B 'D:\2610_MFCC\project\scripts\run_python_reference.py' --phase evaluate --run-id my_evaluation --development-run 'D:\2610_MFCC\build\python_reference\my_development'
```

출력은 `D:\2610_MFCC\build\python_reference\<run-id>`에 생성된다. 종료 코드 0과 `run_manifest.json`의 `status=passed`를 함께 확인한다. 생성 파일을 지워서 실패 이력을 감추거나 평가 결과에 맞춰 설정을 바꾸지 않는다.

## 소스 구성과 프로파일

| 파일 | 역할 |
|---|---|
| `mfcc_reference.py` | PCM 변환부터 DCT/선택 후처리까지 명시적인 단계 계산, 계수 생성 |
| `profiles/comparison_raw13.json` | 본 비교용 C0…C12, `/32768`, 꼬리 제외, ln 하한 1e-12, lifter/에너지 대체/delta 없음 |
| `profiles/github_static13.json` | GitHub 정적 13차원 설정 확인, PCM count, 꼬리 패딩, 0만 epsilon으로 대체, lifter22/에너지 대체 |
| `../../verification/python/checks.py` | 패키지 대조·명시적 정책 어댑터·직접 DFT/DCT·합성/입력 계약 검사 |
| `../../verification/python/tolerances.json` | 평가 전 고정한 Python float64 오차 기준과 개발 중 변경 근거 |
| `plots.py` | 개발 음성 기준/대조/차이 히트맵 및 계수별 오차 PNG/PDF |
| `requirements.txt` | 직접·간접 의존성의 검증 버전 고정 |

프로파일은 실험 계약이다. 지원하지 않는 키나 값을 조용히 무시하지 않고 거부한다. MFCC 규격 변경 시 프로파일, 구현, 검증을 함께 검토하고 새 개발 실행을 남긴다.

## C로 넘기는 배열

`<run>/coefficients/comparison_raw13/`는 공통 계수이고, `<run>/<development|evaluation|synthetic>/<id>/comparison_raw13/`는 입력별 중간값이다. 각 폴더의 `arrays.json`을 배열 형식과 SHA-256의 기준으로 사용한다.

- 모든 `.bin`: 헤더 없는 little-endian, C row-major. 실수는 `<f8`(IEEE binary64), 정수는 `<i8`, FFT는 `<c16`(real float64, imag float64 교대).
- `input_s16le.pcm`: 프로파일 폴더의 한 단계 위에 있는 원래 PCM16 입력. 부호 있는 little-endian 16비트.
- `input_float`, `preemphasis`: `[sample]`. 각 클립 시작에서 이전 입력을 0으로 초기화하고, 프레임 경계에서는 초기화하지 않는다.
- `frame_starts`: `[frame]`, 0부터 160 간격의 입력 샘플 인덱스. `frames`, `windowed`: `[frame,512]`.
- `fft`, `power`: `[frame,257]`, bin 0…256 순서. FFT는 비정규화 순방향, power는 `(re²+im²)/512`, 편측 내부 bin을 두 배로 만들지 않는다.
- `mel_energies`, `log_mel`: `[frame,26]`, 낮은 주파수 필터부터. 전자는 로그 보호 전 값이다.
- `dct`, `mfcc`: `[frame,13]`, C0…C12. 본 비교에서는 둘이 같다. `frame_energy`는 `[frame]`의 편측 power 합이며 본 비교 출력 C0를 대체하지 않는다.
- `window[512]`, `mel_edges[28]`, `mel_filters[26,257]`, `dct_cosine[13,26]`, `dct_scale[13]`, `dct_matrix[13,26]`, `lifter[13]`. `dct_matrix`에는 정규화가 포함돼 있으므로 `dct_scale`을 다시 곱하지 않는다. Python 본체는 cosine 합산 후 scale을 곱한다.
- `mfcc.csv`는 헤더와 17자리 유효숫자를 갖춘 확인용 파일이다. GitHub 프로파일 첫 열은 `log_frame_energy`이고 뒤는 `lifter_C1`…`lifter_C12`다.

예를 들어 개발용 본 비교 MFCC의 Python 로드는 다음과 같다.

```python
from pathlib import Path
import json
import numpy as np

folder = Path(r'D:\2610_MFCC\build\python_reference\reproduce_01\development\8463-294828-0037\comparison_raw13')
schema = json.loads((folder / 'arrays.json').read_text(encoding='utf-8'))
info = schema['arrays']['mfcc']
mfcc = np.fromfile(folder / info['file'], dtype=info['dtype']).reshape(info['shape'])
assert mfcc.shape == (534, 13)
```

C에서 float64 파일을 `float*`로 해석하면 안 된다. 먼저 binary64 기준을 읽거나 명시적으로 변환하고 변환 오차를 분리한다. float32·고정소수점 허용치는 후속 개발에서 별도로 정한다.

## 검증 해석과 출처

`package_comparison/native_mfcc.npy`는 패키지 본래의 꼬리 패딩/0 처리 정책을 유지한다. `adapted_mfcc.npy`는 본 비교 규격의 완전 프레임과 로그 하한을 명시적으로 적용한 대조값이다. 설치된 패키지 파일은 수정하지 않는다. GitHub 프로파일은 native 출력도 수치 검사한다. GitHub의 빈 입력은 미지원으로 기록하고 별도 거부 시험을 수행한다.

각 실행의 `provenance/`에 패키지 설치 metadata/라이선스 정보, 원본 notebook 셀, 소스 snapshot을 남긴다. NumPy/SciPy 연산을 일부 공유하므로 두 모델을 완전히 독립된 정답이라고 부르지 않는다. 선택 프레임의 직접 DFT/DCT와 Parseval, 무음 예상값으로 보완한다. 이것은 Python 기준 검증이며 ARM/RTL/FPGA나 인식률 측정은 아니다.
