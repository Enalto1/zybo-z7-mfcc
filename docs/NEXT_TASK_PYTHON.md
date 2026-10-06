# Python MFCC 기준 모델 구현

이번 작업의 목표는 준비된 PCM에서 단계별 중간값과 13차원 정적 MFCC를 계산하고, 실행 명령과 증거가 남는 Python 검증 경로를 완성하는 것이다. 초기 자료 조사는 이미 SOURCE_AUDIT.md와 MFCC_SPEC.md에 있으므로 이를 읽고 구현으로 이어간다. 전체 자료 조사나 FFT 재개발부터 반복하지 않는다.

## 입력과 먼저 읽을 자료

- D:/2610_MFCC/project/docs/MFCC_SPEC.md
- D:/2610_MFCC/project/docs/SOURCE_AUDIT.md
- D:/2610_MFCC/data/librispeech/DATASET_MANIFEST.json
- D:/2610_MFCC/references/README.md
- D:/2610_MFCC/references/CITATION_AND_WRITING_RULES.md

개발용 입력은 D:/2610_MFCC/data/librispeech/wav_pcm16/8463-294828-0037.wav이다. 16000 Hz, mono, PCM16, 85920 samples, 5.37초다. raw PCM의 SHA-256은 026cae2934935a27911f88d7d6e0ddcc678ad433fb50fe764233a57e4bbcde32이다. L512/H160/무패딩 작업 기준의 기대 출력은 534×13이며 아직 실행으로 확인한 결과가 아니다.

평가용 20개는 manifest에서 role=evaluation인 입력만 사용한다. 입력을 다시 자르거나 리샘플링·음량 정규화하지 않는다. 이미 검증한 manifest와 원본 데이터는 변경하지 않는다.

## 역할과 수정 범위

Codex는 Python 모델과 실행·검증 도구를 담당한다. Claude Code는 기존 FFT 재사용 검토를 수행 중이므로 기다리지 않고 독립적인 Python 작업을 진행한다.

- 작성 가능: software/python/, verification/python/, scripts/run_python_reference.py 및 필요한 전용 실행 스크립트, docs/PYTHON_REFERENCE_RESULTS.md.
- 필요한 설정 명확화만 docs/MFCC_SPEC.md에 근거와 함께 반영한다. 입력·알고리즘을 결과에 맞추어 조용히 바꾸지 않는다.
- 생성 결과와 로그: D:/2610_MFCC/build/python_reference/ 아래 실행별 폴더.
- Claude 담당 docs/reviews/FFT_REUSE_REVIEW.md 및 build/claude-review는 수정하지 않는다. hardware/, 기존 FFT, reference_code 원본, 논문·발표 파일도 이번 범위에서 수정하지 않는다.
- C/ARM, RTL, 비트정확 고정소수점 모델, Vivado IP 업그레이드는 후속 작업이다.
- 현재 변경과 미추적 파일을 확인하고 보존한다. 이번 작업에서 Git 커밋·푸시·브랜치 전환은 하지 않는다.

## 두 프로파일

1. 원본 설정 확인용: GitHub notebook의 실제 mfcc 호출 인수와 입력 크기 단위를 읽고 python_speech_features의 정적 13개를 확인한다. lifter=22와 에너지 대체 외에 프레임·윈도·프리엠퍼시스·꼬리 패딩·로그 0 처리도 기록한다. 원본의 근거 없는 기본값을 추정하지 않는다. 패키지와의 일치는 GitHub RTL 또는 최종 39차원 VAD 재현 완료가 아니다.
2. 본 비교용: MFCC_SPEC의 작업 기준대로 lifter·에너지 대체·delta 없는 C0…C12를 출력한다. NumPy/SciPy를 사용하되 mfcc 함수 호출에 숨기지 않고 단계별 값을 관찰할 수 있게 구성한다.

두 프로파일의 설정은 별도 파일로 저장한다. 본 비교용의 로그 하한과 꼬리 제외 정책은 패키지 기본값과 다르므로 명시적인 어댑터 또는 비교 범위를 사용한다. 패키지 원본 출력과 조정한 출력을 구분하며 설치된 패키지를 몰래 수정하지 않는다.

## 구현 순서

1. 프로젝트용 Python 환경을 만들거나 적절한 기존 환경을 선택하고 python_speech_features, NumPy, SciPy 및 필요한 시각화 패키지 버전을 고정한다. 데이터 준비용 임시 환경과 MFCC 실행 환경을 혼동하지 않는다.
2. PCM int16 읽기와 hash 검증 → /32768 → 연속 pre-emphasis → framing → Hamming → FFT → power → Mel → floor/ln → DCT를 구현한다.
3. pre-emphasis 샘플, 프레임 시작 위치, windowed samples, 복소 FFT, power 257개, Mel energy 26개, ln energy 26개, MFCC 13개를 저장할 수 있게 한다. 프레임·계수 축과 순서, dtype·진폭 단위를 함께 기록한다.
4. 먼저 합성 입력과 개발용 1개로 검증한다. Python float64끼리의 오차 허용치를 이유와 함께 정하고, 평가용 20개 실행 전에 고정한다. 향후 FP HW/fixed 허용치와 구분한다.
5. 같은 실행 경로를 평가용 20개에 일괄 적용하고, 클립·단계별 최대 절대 오차와 RMSE, 프레임 수·유한값 검사를 기록한다. 정책상 차이를 예상 오차로 구분하고, 설명되지 않은 불일치는 실패로 남긴다. 결과를 맞추려고 평가 파일·조건·허용치를 바꾸면 새 실행으로 기록한다.
6. 개발용 1개의 기준/대조/차이 히트맵을 만든다. 필요하면 오차가 큰 사례만 추가한다. 시간 또는 프레임과 계수 번호 축을 쓰고, 두 MFCC 그림의 색 범위는 동일하게 둔다. 차이 그림은 별도 오차 색 범위임을 표시한다.

## 필요한 검증

- 길이 0, 511, 512, 671, 672, 832에서 프레임 수 0, 0, 1, 1, 2, 3.
- 무음에서 NaN/Inf가 없고 규격의 로그 하한으로부터 계산한 C0 및 나머지 계수의 예상값 확인.
- 임펄스·정현파·작은 진폭·큰 진폭·고정 입력 난수 등으로 FFT 크기/스케일과 전처리 범위 확인.
- 개발용 실제 PCM에서 534×13 및 각 단계의 기대 shape 확인.
- 모든 실제 입력의 hash, 프레임 수, 데이터 순서·유한값 검사.

패키지와 자체 모델은 NumPy/SciPy의 연산을 공유하므로 완전히 독립된 두 정답으로 부르지 않는다. 소규모 직접 DFT/DCT 식 계산이나 합성 신호의 예상 성질을 이용해 보완한다. 검증 코드는 구현을 그대로 복사해 자기 자신을 정답으로 삼지 않는다.

## 결과와 출처

한 명령으로 입력 명세를 읽고 필요한 결과를 재생성할 수 있게 한다. 실행 manifest에 입력·설정·규격·소스·계수 hash, 실행 명령, Python/패키지 버전, Git commit과 미커밋 상태, 실행 시각, 결과 위치를 기록한다. 데이터 manifest에 보존된 과거 spec hash를 바꾸지 않고 현재 실행 hash를 별도로 남긴다.

사용한 패키지의 출처·고정 버전·라이선스를 기록한다. 외부 코드를 복사·수정했다면 원출처와 고지를 유지한다. Python 모델은 문헌과 규격을 구현한 기준 모델이며 MFCC 알고리즘 자체를 새로 발명한 것으로 설명하지 않는다. 실행 결과는 정확한 데이터와 로그를 근거로 새로 서술한다.

docs/PYTHON_REFERENCE_RESULTS.md에는 실행 방법, 프로파일 차이, 테스트별 통과/실패, 대표 오차, 결과 폴더, 아직 확인하지 못한 사항을 정리한다. 논문 완성 문장·원문 PDF·음성 파일을 코드 저장소에 넣지 않는다. MFCC Python 검증을 ARM/FPGA 검증이나 속도·전력·인식률 향상으로 확대해 표현하지 않는다.

## 완료 조건

- 개발용 1개와 합성 입력의 단계별 확인을 마친 뒤 평가용 20개를 실행했다.
- 같은 명령으로 결과와 manifest가 재생성되고, 입력 hash·프레임 수·유한값·수치 비교 결과가 남는다.
- 두 프로파일의 차이와 독립성 한계가 설명되어 있다.
- 대표 히트맵을 실제로 열어 축·색 범위·레이블을 확인했다.
- 확인되지 않은 결과를 통과로 표시하지 않았고, Claude의 파일과 하드웨어 원본을 변경하지 않았다.

모든 기준을 만족하지 못하면 부분 완료와 실패 원인을 보고한다. 계획만 제시하고 종료하지 말고 위 범위의 구현과 실행까지 진행한다.
