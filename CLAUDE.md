# Claude Code 시작 지침

먼저 저장소 루트의 `AGENTS.md`를 읽고 따른다. 아래 파일을 실제로 읽은 뒤 관련 작업을 시작한다.

1. `docs/MFCC_SPEC.md`: 공통 MFCC 수학·입출력 정의.
2. `docs/RTL_CODING_RULES.md`: 신규 RTL의 필수 문법·구조·명명·검증 규칙과 기존 코드 적용 범위.
3. `docs/FIXED_POINT_DEVELOPMENT.md`: float32 경로와 고정소수점 비트모델의 구분, 양자화 설계 절차.
4. `docs/NEXT_TASK_CLAUDE_FIXED_POINT.md`: 이번 고정소수점 선행 작업과 완료 조건.

RTL 담당이므로 양자화 모델·계수 생성기·검증 스크립트도 담당한다. Codex 담당 부동소수점 IP 하드웨어·BRAM 버퍼·통합 검증, C·ARM 작업 파일, 공통 float64 기준값, 기존 FFT 원본은 현재 작업 범위 밖이다.

코딩 규칙을 이 파일에 복제하지 않는다. 공통 원문은 `docs/RTL_CODING_RULES.md`다. 기존 FFT를 스타일만 맞추려고 전면 재작성하거나, 임의로 `for/function/initial` 금지를 완화하지 않는다.
