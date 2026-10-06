# FP32 MFCC PS/PL 통합 및 실제 보드 검증

작성일: 2026-10-05 KST. **AXI-Lite polling 후보 검증 기록이며, 최신 지시에 따라
DMA/인터럽트 통합으로 전환했다. 이 후보의 bitstream/보드 실행은 하지 않았다.**
새 증거 루트는 `D:/2610_MFCC/build/board_validation/fp32_board_20261005_01`이다.

## 목적과 계산 위치

사용자의 “통합시스템 준비해서 한번 보드에 올려서 돌려봐” 지시에 따라
기존 FP32 RTL을 ZYBO Z7-20의 PS/PL 시스템에 연결한다.
ARM Cortex-A9 CPU0는 DDR의 PCM을 AXI4-Lite로 전송하고 출력 레코드를
회수한다. PCM 변환, pre-emphasis, framing/window, FFT, power/Mel,
log/DCT와 최종 MFCC 연산은 FPGA의 기존 FP32 RTL/vendor IP가 수행한다.
ARM에서 MFCC C를 계산한 값을 하드웨어 결과로 대신하지 않는다.

이전 FP32 작업은 실제 vendor IP 시뮬레이션과 코어 단독 OOC 배치배선까지였다.
PS 연결 어댑터, AXI 레지스터, 전체 시스템 bitstream/XSA 및 해당 XSA용
ARM 드라이버/ELF가 없었으므로, 당시 FP32 보드 실행을 완료했다고 볼 수 없다.
이번 작업에서 그 통합 계층을 새로 준비한다.

## 구현과 동결 범위

- `hardware/system_fp32`: FP32 native handshake 어댑터, AXI4-Lite 레지스터,
  PS7/FCLK/reset 연결, IP 패키징 및 전체 시스템 생성·구현 스크립트.
- `software/arm_fp32_accel`: 별도 identity를 검사하는 ARM 드라이버와 DDR 제어/결과 프로그램.
- `verification/system_fp32`, `verification/arm_fp32_accel`: 경계 프로토콜,
  실제 vendor IP 및 실행기 검사.

세부 ABI는 [REGISTER_CONTRACT.md](../hardware/system_fp32/REGISTER_CONTRACT.md)에 있다.
Base `0x43c00000`, ABI `0x00010001`, core ID `2`, format `0x00030020`이다.
24-byte 결과의 low32에는 변환하지 않은 IEEE754 binary32 비트가 들어가며
high32와 BFP는 0이다. 최초 native 오류 벡터는 RO `0x64`에 보존한다.

기존 산술 RTL 6개, vendor XCI 5개, ROM, PCM, Python/PC 기준 배열과
허용치는 고정한다. 기존 fixed 시스템과 이전 보드 보고서도 보존한다.
입력은 LibriSpeech `8463-294828-0037`의 mono16kHz PCM16,
85,920 samples, 534 frames, 6,942 MFCC다.
PCM SHA-256은 `026cae2934935a27911f88d7d6e0ddcc678ad433fb50fe764233a57e4bbcde32`다.
보드 raw 결과는 기존 `continuous_development_01`의 모든 MFCC 비트와
대조한 뒤 Python/PC 기준의 `1e-3 + 1e-5*abs(reference)`를 적용한다.

## 후보 검증 및 구조 전환

어댑터/MMIO 단위 검사와 ARM host 프로토콜 검사는 통과했다.
실제 vendor IP를 연결한 `f01` 시뮬레이션에서 개발 음성 prefix 두 클립의
52개 출력은 기존 FP32 RTL 기준과 비트 일치했다. 재시작 경계의 vendor
reset 경고로 전체 구현을 보류했다. `f02` 추적 실행에서도 52개 출력은
일치했으며, native reset LOW17/HIGH2/LOW17 경계에서 내부 FFT 경고가
발생함을 기록했다. 해당 HIGH2 동안 FFT clock enable은 모두 켜져 있었다.
이를 공개 명세의 외부 LOW2 요구 위반으로 단정하지 않는다.

어댑터 reset을 등록 출력으로 바꾸고 transport 명령 사이 HIGH4 회복 구간을
보장하도록 보강했다. `unit04`는 LOW16/HIGH4, 회복 중 START/ABORT 보존을
포함한 8개 어댑터 검사군 및 15개 MMIO 검사군을 통과했다. 수정 후 실제
vendor IP 검증은 새 DMA 통합 경로에서 수행한다. 기존 `f01/f02`의 경고와
이력은 보존하며, 단위 검사만으로 실제 IP 경고 해소나 보드 통과를 선언하지 않는다.

최신 사용자 지시는 FP32와 fixed 모두 AXI DMA Simple + HP 포트 + GIC
인터럽트로 전환하는 것이다. 새 공통 계약은
[DMA REGISTER_CONTRACT.md](../hardware/system_dma/REGISTER_CONTRACT.md)에 있다.
이하 polling 측정 경계는 취소한 후보의 설계 기록이며 DMA 측정 규격이 아니다.

JTAG의 Digilent `210351B40030A`/`xc7z020` 및 동일 FTDI 장치의 COM7 연결을
읽기 전용으로 확인했다. 이 후보로 실제 다운로드·수치 검증·시간 측정을 하지는 않았다.

## 결과 해석 범위

기존 FP32 합성 입력 실패 5개는 유지한다:
`composite_500_2237hz`, `dc_negative_8192`, `dc_positive_8192`,
`fullscale_alternating`, `tone_bin32_1000hz`.
이번 개발 음성 통과가 전체 합성/평가 입력의 정확도 합격을 뜻하지 않는다.
평가 음성 20개, 마이크 입력, DMA/interrupt, 전력 측정은 이번 대상이 아니다.

ARM 측정은 identity 검사 뒤 ABORT/SAMPLE_COUNT/START부터 MMIO 입력,
출력 polling, DDR 결과 저장, POP 및 카운터 조회까지다. FPGA programming,
PS 초기화, ELF startup, JTAG/UART, 초기 PCM CRC와 host 수치 비교는 제외한다.
HW busy cycles에는 PS의 전송 지연이 포함된다. 따라서 순수 연산 latency나
CPU가 다른 일을 할 수 있게 된 효과를 뜻하지 않는다.

시간을 반복 측정한다면 매 trial마다 시스템을 새로 초기화하고 동일 bit/ELF를
다시 올린다. 초기 3회 제외 후 30회 측정은 이전 trial의 cache를 이어받는
warmup 방식과 다르다. 각 trial의 사전 PCM CRC가 입력 cache를 채우므로
완전한 cold-cache 측정도 아니다.

이전 세 경로의 완료 결과는 [BOARD_VALIDATION_RESULTS.md](BOARD_VALIDATION_RESULTS.md)에
당시 상태 그대로 보존한다. 그 문서의 FP32 대기 상태는 이번 완료 결과가
확정되면 이 새 문서로 보완한다.
