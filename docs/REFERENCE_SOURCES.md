# 참고 소스 현황

작성 기준: 2026년 10월 4일. 이 문서는 개발 시작을 위한 소스 보관 위치와 식별 정보를 기록합니다. 알고리즘이나 보드 동작을 새로 검증한 결과는 아닙니다.

## 기존 FFT

- 원본 위치: `D:\20260824_FFT`.
- 보관 위치: `D:\2610_MFCC\reference_code\previous_fft`.
- 개발 저장소 기준 상대 위치: `../reference_code/previous_fft`.
- 복사 범위: RTL, 테스트벤치, MATLAB, 보고서, Vivado/Vitis, 기존 제출본과 생성물을 포함한 전체 폴더.
- 원본은 기존 위치에 보존합니다. 복사본은 재사용 검토용으로 두고, MFCC용 변경은 검토 후 개발 저장소에서 관리합니다.

원본 README는 ZYBO Z7-20 대상의 SystemVerilog R2^2SDF FFT, 가변 길이, 자연 순서 재정렬, 시뮬레이션과 구현 결과를 설명합니다. 이 수치와 결과는 기존 문서의 기록이며 이번 준비 과정에서 재현하지 않았습니다.

같은 README는 AXI4-Stream 출력의 임의 backpressure를 지원하지 않으며, 실제 보드·DMA 동작을 입증하는 결과가 없다고 명시합니다. MFCC 통합 전에 `docs/open_issues.md`, `reports/phase8_integration_status.md`와 관련 RTL 및 테스트를 확인합니다.

## GitHub MFCC 원본

- 출처: https://github.com/AlexKly/Simple-Voice-Activity-Detector-using-MFCC-based-on-FPGA-Kintex
- 원본 ZIP: `Simple-Voice-Activity-Detector-using-MFCC-based-on-FPGA-Kintex-master.zip`.
- 원본 ZIP 위치: `C:\Users\rlagk\Downloads`.
- 보관 위치: `D:\2610_MFCC\reference_code\github_mfcc`.
- 개발 저장소 기준 상대 위치: `../reference_code/github_mfcc`.
- ZIP SHA-256: `3a00d8668a325a6efb62cf0b8ca505d9a2298ecc494523f12d7fe0bb5296039c`.
- 버전: 다운로드한 `master` ZIP의 주석에 기록된 커밋은 `27aa09974049d11f49383c8e18f3ca9e34d08ed9`입니다. 현재 원격의 최신 커밋을 대신 기록한 것이 아닙니다.

ZIP의 최상위 폴더 한 겹을 제외하고 전체 내용을 `github_mfcc`에 배치합니다. 이는 ZIP에서 가져온 참고 복사본이며, Git 이력이 있는 clone은 아닙니다.

별도 LICENSE/LICENCE/COPYING 이름의 파일은 ZIP 목록에서 찾지 못했습니다. 외부 코드의 수정·재배포 조건과 출처 표시는 개발에 반입하기 전에 확인합니다. 새 저장소에는 현재 이 참고 코드가 포함되어 있지 않습니다.

## 복사 검증과 후속 작업

FFT 12,167개 파일과 GitHub ZIP의 520개 파일에 대해 원본과 보관본의 파일 크기 및 SHA-256이 일치함을 확인했습니다. ZIP의 CRC 검사도 통과했습니다. 파일별 기록은 저장소 밖의 `D:\2610_MFCC\reference_code\SOURCE_MANIFEST.json`에 둡니다. 이 기록은 복사 무결성에 관한 것이며 설계 검증 결과가 아닙니다.

두 참고 폴더는 새 Git 저장소에 포함하지 않습니다. 다음에는 [첫 개발 작업](FIRST_TASK.md)에 따라 원본 MFCC의 경계와 FFT 재사용 조건을 점검하고 공통 규격을 작성합니다.
