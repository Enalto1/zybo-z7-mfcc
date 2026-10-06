# ZYBO Z7 MFCC

ZYBO Z7-20에서 MFCC 음성 특징 추출기를 구현하고 Python·ARM C·FPGA 결과를 비교하기 위한 개발 저장소입니다. 저장소 이름은 `zybo-z7-mfcc`입니다.

ARM C와 고정소수점 RTL의 기존 보드 실행 결과는 [보드 검증 기록](docs/BOARD_VALIDATION_RESULTS.md)에 있습니다. FP32·고정소수점 RTL은 DMA/인터럽트 통합 후 실제 보드에서 각각 예열3회·측정30회 및 모든 출력의 독립 감사를 완료했습니다. 결과와 정확도 한계, 측정 구간은 [DMA 통합 검증 기록](docs/DMA_SYSTEM_BOARD_RESULTS.md)에 있습니다. 개발 음성 결과를 전체 입력의 정확도나 성능 우위로 일반화하지 않습니다.

## 시작하기

1. [개발 인수인계](docs/DEVELOPMENT_HANDOFF.md)와 [참고 소스 현황](docs/REFERENCE_SOURCES.md)을 읽습니다.
2. [첫 개발 작업](docs/FIRST_TASK.md)에 따라 참고 MFCC와 기존 FFT를 점검합니다.
3. 공통 MFCC 규격을 확정하고 동일 입력으로 각 구현을 검증합니다.

전체 로컬 작업 공간 안내는 `D:\2610_MFCC\README.md`에 있습니다. 이 파일은 저장소 외부의 로컬 안내이며 다른 PC로 Git clone할 때 함께 내려오지 않습니다.

## 계획한 비교군

- Python 알고리즘 기준 모델.
- C 골든 모델과 ARM 실행 코드.
- 부동소수점 IP 기반 MFCC 하드웨어.
- 고정소수점 custom RTL MFCC 하드웨어.

기존 FFT는 재사용 후보입니다. FFT 규격, 스케일링, 출력 순서, 인터페이스와 검증 상태를 확인한 뒤 통합합니다. 세부 MFCC 규격과 각 구현의 완료 여부는 아직 확정되지 않았습니다.

## 디렉터리

| 경로 | 내용 |
|---|---|
| `docs/` | 개발 규격·설계 결정·검증 방법 |
| `software/` | Python·C 모델과 ARM 실행 코드 |
| `hardware/` | RTL·IP 설정·제약·프로젝트 생성 스크립트 |
| `verification/` | 테스트벤치와 작은 공통 입력 |
| `experiments/` | 실험 설정·측정 데이터 |
| `scripts/` | 검증·비교·빌드 보조 스크립트 |

## 로컬 배치와 버전 관리

- 이 저장소의 작업 위치: `D:\2610_MFCC\project`.
- 기존 코드 보관 위치: `D:\2610_MFCC\reference_code`.
- 학교 양식과 참고자료: `D:\2610_MFCC\references`.
- 논문·발표·제출물은 상위의 `thesis`, `presentation`, `submission`에서 관리하고 이 저장소에 넣지 않습니다.
- 생성되는 합성·구현·시뮬레이션 출력은 가능한 한 `D:\2610_MFCC\build`에 둡니다.

외부 참고 폴더는 Git clone에 포함되지 않습니다. 실제 구현이 의존하는 파일은 라이선스를 확인한 후 저장소에 포함하거나, 출처와 버전이 고정된 재현 절차를 제공합니다. 아직 확보하지 않은 파일을 있는 것으로 가정하지 않습니다.

Vivado IP 설정, 제약, 계수 및 메모리 초기화 파일은 개발 소스일 수 있습니다. 생성 폴더만 제외하고 `.xci`, `.xdc`, `.tcl`, `.coe`, `.mem`, `.hex`, `.xpr` 등을 확장자만으로 일괄 제외하지 않습니다.

## 참고 프로젝트

[Simple Voice Activity Detector using MFCC based on FPGA Kintex](https://github.com/AlexKly/Simple-Voice-Activity-Detector-using-MFCC-based-on-FPGA-Kintex)를 출발점으로 검토합니다. 원본의 출처·라이선스·기준 버전을 확인하고, 가져온 부분과 수정한 부분을 구분합니다.

## RTL 코딩과 고정소수점 개발

- [RTL 코딩 규약](docs/RTL_CODING_RULES.md): 신규 RTL 작성 시 필수 적용.
- [양자화·고정소수점 개발 절차](docs/FIXED_POINT_DEVELOPMENT.md): float32 C와 정수 비트모델의 역할 및 검증 단계.
- [고정소수점 설계 변경 이력](docs/FIXED_POINT_DESIGN_HISTORY.md): FFT 폭·배율 변경의 이유와 수치 근거, 남은 오차, 별도 fixed C 계획. 이후 양자화 변경도 이 문서에 기록.
- [Claude 다음 작업](docs/NEXT_TASK_CLAUDE_FIXED_POINT.md): 양자화 계약과 부분 정수 경로 실험.
- 도구별 시작 지침은 [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md)를 따릅니다.

## 협업과 검증

Codex와 Claude Code는 같은 규격을 사용하고 동시에 수정할 파일 범위를 나눕니다. 테스트 실행 여부, 비교 입력, 측정 범위와 코드 버전을 기록합니다. 논문과 발표에는 실제 확인한 결과만 사용합니다.
